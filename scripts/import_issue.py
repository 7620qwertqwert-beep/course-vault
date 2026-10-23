#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「上传资料」Issue 表单里的附件归档到 courses/。

用法：
    python3 scripts/import_issue.py --issue-number <N> [--dry-run]

只使用 Python 3 标准库，零第三方依赖。

安全约定（任意一条不满足都会整体失败，退出码 2，不写入任何课程文件）：
  * 课程目录必须已经存在，课程名必须与目录名完全一致；
  * 所有目标路径都必须落在 courses/ 内，拒绝 ..、斜杠等路径逃逸；
  * 附件数量 1~5 个，单个不超过 50MB（zip 不超过 20MB），合计不超过 100MB；
  * 扩展名只允许 pdf/doc/docx/ppt/pptx/xls/xlsx/md/txt/jpg/jpeg/png/zip。

学院、专业既可以下拉选择，也可以在「学院名称」「专业名称」补充框里手写；
手写值优先于下拉值，支持末尾括号说明。学院模板的专业选项只写纯专业名：
脚本先用「开课学院」定位学院，再在该学院下找专业；通用模板仍支持
「学院·专业」「学院/专业」。找不到时用 difflib 给出最接近的 1~3 个候选，
并标明候选属于哪个学院。公共课模板的「课程名」是下拉兜底项的补充框：
下拉选「其他（在下面写）」或为空时使用它，下拉已是具体课程时以缓存的下拉
值为准，避免切换选项后残留的补充文本覆盖正确课程。容错只影响“找哪个真实
目录”，不会放宽上面的安全检查。

本地调试：
    IMPORT_ISSUE_BODY_FILE=/tmp/fake_issue.md \
    IMPORT_ISSUE_TITLE='[上传] 测试' \
    python3 scripts/import_issue.py --issue-number 1 --dry-run
设置 IMPORT_ISSUE_BODY_FILE 后不再访问 GitHub API。

仅测试用（生产不要传）：
    --attachment-file <本地路径>   可重复；按顺序冒充 Issue 里的附件内容，
                                   跳过网络下载，但保留扩展名/大小/空文件校验。
    该参数不传时，下载与归档行为与之前完全一致。
"""

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib import error, parse, request

# 脚本位于 scripts/ 下，向上一级是仓库根目录。
REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_DIR = REPO_ROOT / "courses"

MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_ZIP_BYTES = 20 * 1024 * 1024
# scripts/check.yml 对单个 PR 的“新增/修改文件总大小”上限是 100 MiB；
# 脚本先拦一次，避免下载完才发现 PR 必然过不了 CI。
MAX_TOTAL_BYTES = 100 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60
API_TIMEOUT = 60

ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
    "md", "txt", "jpg", "jpeg", "png", "zip",
}

# 表单里的「资料类型」→ 仓库里的资料子目录。
TYPE_TO_DIR = {
    "笔记": "笔记",
    "课件": "课件",
    "试卷": "试卷",
    "作业": "作业",
    "实验报告": "作业",
    "复习资料": "试卷",
    "其他": "笔记",
}

# 文件名里的类型使用 check.sh 允许的词：
# check.sh 允许「期末/期中/笔记/作业/实验报告/实验/课件/复习/答案」，
# 不允许「试卷/复习资料/其他」，所以这里做一次兼容映射；归档目录仍按需求映射。
TYPE_TO_FILENAME = {
    "笔记": "笔记",
    "课件": "课件",
    "试卷": "期末",
    "作业": "作业",
    "实验报告": "实验报告",
    "复习资料": "复习",
    "其他": "笔记",
}

# 下拉框里代表“列表没有，去下面的补充框手写”的选项。
OTHER_OPTION = "其他（在下面写）"

FIELD_ALIASES = {
    "category": ("课程类别", "类别"),
    "college": ("开课学院", "学院"),
    "college_manual": ("学院名称", "学院名称（补充）", "补充学院"),
    "major": ("所属专业", "专业"),
    "major_manual": ("专业名称", "专业名称（补充）", "补充专业"),
    "course": ("课程名称", "课程名", "课程"),
    # 公共课模板的下拉兜底项是「其他（在下面写）」，紧跟着的补充框 label
    # 目前是「课程名」。它同时是 course 的别名，所以公共课分支单独取用。
    "course_manual": ("课程名", "课程名称（补充）", "课程名（补充）", "补充课程名"),
    "year": ("年份学期", "学期", "年份/学期"),
    "type": ("资料类型", "类型"),
    "attachments": ("附件", "附件区", "附件或链接", "上传附件", "文件"),
    "confirm": ("确认", "确认事项", "自查确认"),
}

# Issue Forms 只会生成上面这些字段标题。只把已知标题当作分节边界，
# 避免附件说明里恰好写了 `### 附件` 之类的文本把正文截断。
KNOWN_SECTION_LABELS = {
    alias for aliases in FIELD_ALIASES.values() for alias in aliases
}

OFFICIAL_ATTACHMENT_HOSTS = {
    "github.com",
    "www.github.com",
    "user-images.githubusercontent.com",
    "private-user-images.githubusercontent.com",
    "objects.githubusercontent.com",
}
# GitHub 官方附件在 302 之后会落到 github-production-*-asset-*.s3[.*].amazonaws.com，
# 这是 GitHub 自己托管的桶，不限定会直接把用户上传的图片挡在门外。
# 只放行 github-production-* 这一族桶名，其他 S3 域名照旧拒绝。
OFFICIAL_ATTACHMENT_HOST_PATTERNS = (
    re.compile(
        r"^github-production-[a-z0-9-]+\.s3(?:[.-][a-z0-9-]+)*\.amazonaws\.com$"
    ),
)

USER_AGENT = "course-vault-import-bot/1.0 (+https://github.com/)"

# 必须勾选的那一项的原文；只匹配这个文本，避免用户自己写一个无关的 - [X] 绕过。
CONFIRM_REQUIREMENT = "文件里不含本人或他人的姓名、学号、手机号、身份证号"


class ImportFailure(Exception):
    """可以直接展示给同学的中文失败原因。"""


class FriendlyArgumentParser(argparse.ArgumentParser):
    """参数错误时也输出 IMPORT_FAIL，而不是英文 usage。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        print("IMPORT_FAIL 参数错误：{}".format(message))
        raise SystemExit(2)


def _fail(reason):
    raise ImportFailure(reason)


def _normalize_spaces(value):
    """把全角/半角空白统一成半角空格，并去掉首尾空白。"""
    text = "" if value is None else str(value)
    text = "".join(" " if ch.isspace() else ch for ch in text)
    text = re.sub(r" +", " ", text)
    return text.strip()


def _single_line(value, label):
    raw = "" if value is None else str(value)
    if any(ch in raw for ch in "\n\r\u2028\u2029"):
        _fail("表单字段「{}」应为单行内容，请重新填写。".format(label))
    text = _normalize_spaces(raw)
    if not text:
        _fail("表单必填项「{}」为空。".format(label))
    return text


def _is_blank_response(value):
    compact = (value or "").strip()
    return compact in ("", "_No response_", "_No response_。", "_无响应_", "-", "—")


def _parse_sections(body):
    """把 Issue Forms 的 `### 字段名` + 下一段内容解析成字典。

    只识别表单已知字段，且拒绝重复字段；字段值保留行内前后空格，
    交给 _single_line 统一去掉首尾空白后再做校验。
    """
    heading_re = re.compile(r"^###[ \t]*(.+?)[ \t]*$", re.MULTILINE)
    matches = [
        match
        for match in heading_re.finditer(body)
        if match.group(1).strip() in KNOWN_SECTION_LABELS
    ]
    sections = {}
    for index, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value = body[start:end]
        # GitHub 不会把模板注释渲染进正文，这里仍做一次清理，兼容手工粘贴。
        value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
        # 去掉标题后、下一个标题前的空行；不 strip 字段值自身的水平空白。
        value = value.strip("\n")
        value = re.sub(r"\A(?:[ \t]*\n)+", "", value)
        value = re.sub(r"(?:\n[ \t]*)+\Z", "", value)
        if label in sections:
            _fail("Issue 正文中存在重复字段「{}」，无法确定以哪一段为准。".format(label))
        sections[label] = value
    return sections


def _section(sections, key):
    for alias in FIELD_ALIASES[key]:
        if alias in sections:
            value = sections[alias]
            # 某个别名是空的（GitHub 会渲染成 _No response_）时继续看后面的
            # 别名，兼容旧模板同时存在「课程名称」和「课程名」的情况。
            if not _is_blank_response(value):
                return value
    return ""


def _required_section(sections, key, label):
    value = _section(sections, key)
    if not value:
        _fail(
            "Issue 正文缺少必填项「{}」，请确认使用的是「上传资料」表单。".format(label)
        )
    return value


def _clean_url(raw):
    url = (raw or "").strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1].strip()
    # 去掉 Markdown/中文标点里常见的尾随符号。
    url = url.rstrip(".,;:!?，。；：！？、")
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def _parse_attachments(text):
    """抓取 Markdown 链接、裸链接和 HTML href，按出现顺序去重。"""
    entries = []
    seen = set()

    def add(url, label=""):
        url = _clean_url(url)
        if not url.startswith(("http://", "https://")):
            return
        if url in seen:
            return
        seen.add(url)
        entries.append({"url": url, "label": (label or "").strip()})

    # Markdown 图片/链接：[名字](https://...) 或 ![名字](<https://...>)
    md_link_re = re.compile(
        r"!?\[([^\]\n]*)\]\(\s*(<[^>]+>|https?://[^)\s]+)\s*\)"
    )
    for match in md_link_re.finditer(text):
        add(match.group(2), match.group(1))

    # HTML 链接：<a href="https://...">...</a>
    for match in re.finditer(
        r"""href\s*=\s*["'](https?://[^"']+)["']""", text, re.IGNORECASE
    ):
        add(match.group(1))

    # 裸链接（GitHub 拖拽纯文件时有时只生成 URL）。
    for match in re.finditer(r"https?://[^\s<>\"'）】]+", text):
        add(match.group(0))

    return entries


def _is_official_attachment(url):
    try:
        parsed = parse.urlsplit(url)
    except ValueError:
        return False
    # 只接受 HTTPS，避免明文传输和把 token 发到 http 端点。
    if parsed.scheme != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    if host not in OFFICIAL_ATTACHMENT_HOSTS:
        # GitHub 的附件会 302 到 github-production-*-asset-*.s3.amazonaws.com。
        if any(p.match(host) for p in OFFICIAL_ATTACHMENT_HOST_PATTERNS):
            return True
        return False
    if host in ("github.com", "www.github.com"):
        path = parsed.path
        return (
            path.startswith("/user-attachments/assets/")
            or path.startswith("/user-attachments/files/")
            # 兼容题干里给出的 github.com/user-images.githubusercontent.com/... 写法。
            or path.startswith("/user-images.githubusercontent.com/")
        )
    # user-images / private-user-images / objects 都是 GitHub 官方附件域。
    return True


def _safe_url(url):
    """错误信息里只展示 scheme/host/path，避免把签名 query 写进日志。"""
    try:
        parsed = parse.urlsplit(str(url))
    except ValueError:
        return "<无效链接>"
    return "{}://{}{}".format(
        parsed.scheme or "?", parsed.hostname or "?", (parsed.path or "")[:160]
    )


def _ext_from_name(name):
    """从文件名里取扩展名；遇到不支持的扩展名直接失败。"""
    if not name:
        return None
    name = parse.unquote(name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = name.split("?", 1)[0].split("#", 1)[0]
    match = re.search(r"\.([A-Za-z0-9]{1,8})$", name)
    if not match:
        return None
    ext = match.group(1).lower()
    if ext not in ALLOWED_EXTENSIONS:
        _fail(
            "不支持的文件类型 .{}；只允许：{}。".format(
                ext, " ".join(sorted(ALLOWED_EXTENSIONS))
            )
        )
    return ext


def _ext_from_url(url):
    parsed = parse.urlsplit(url)
    ext = _ext_from_name(parsed.path)
    if ext:
        return ext
    query = parse.parse_qs(parsed.query)
    for key in ("filename", "file", "name"):
        for value in query.get(key, []):
            ext = _ext_from_name(value)
            if ext:
                return ext
    return None


def _filename_from_content_disposition(value):
    if not value:
        return None
    match = re.search(r"filename\*\s*=\s*([^;]+)", value, re.IGNORECASE)
    if match:
        filename = match.group(1).strip().strip('"')
        if "''" in filename:
            filename = filename.split("''", 1)[1]
        return parse.unquote(filename)
    match = re.search(r'filename\s*=\s*"([^"]*)"', value, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"filename\s*=\s*([^;]+)", value, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"')
    return None


def _ext_from_content_type(value):
    if not value:
        return None
    mime = value.split(";", 1)[0].strip().lower()
    mapping = {
        "application/pdf": "pdf",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.ms-powerpoint": "ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "text/markdown": "md",
        "image/jpeg": "jpg",
        "image/png": "png",
        "application/zip": "zip",
        "application/x-zip-compressed": "zip",
    }
    # text/plain 可能是 .md/.txt，交给链接文字判断，避免把 .md 改成 .txt。
    return mapping.get(mime)


def _choose_extension(label_ext, header_filename, content_type, url_ext=None):
    """优先使用服务器返回的真实文件名/类型，缺失时回退到链接文字或 URL。"""
    header_ext = _ext_from_name(header_filename) if header_filename else None
    type_ext = _ext_from_content_type(content_type)
    return header_ext or type_ext or label_ext or url_ext


def _validate_component(value, label):
    """校验单个路径分量，禁止 ..、路径分隔符和文件名禁用字符。"""
    raw = "" if value is None else str(value)
    if not raw.strip():
        _fail("{}不能为空。".format(label))
    if raw != raw.strip():
        _fail("{}不能以空格或制表符开头/结尾，已拒绝。".format(label))
    value = raw
    if value in (".", "..") or ".." in value:
        _fail("{}包含非法路径片段「..」，已拒绝路径逃逸。".format(label))
    if any(ch in value for ch in '/\\<>:"|?*[]'):
        _fail("{}包含路径分隔符或非法字符，已拒绝。".format(label))
    if any(ord(ch) < 32 or 127 <= ord(ch) <= 0x9F for ch in value):
        _fail("{}包含不可见控制字符，已拒绝。".format(label))
    if any(ch in value for ch in "\u2028\u2029"):
        _fail("{}包含 Unicode 行分隔符，已拒绝。".format(label))
    if value.startswith("."):
        _fail("{}不能以点开头。".format(label))
    if value.endswith((".", " ")):
        _fail("{}不能以点或空格结尾。".format(label))
    if len(value) > 120:
        _fail("{}过长（超过 120 个字符）。".format(label))
    return value


def _iter_subdirs(base):
    """列出目录下的真实子目录名；目录不存在时返回空列表。"""
    try:
        return sorted(
            child.name
            for child in base.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )
    except OSError:
        return []


def _college_names():
    return _iter_subdirs(COURSES_DIR / "专业课")


def _major_names(college):
    return _iter_subdirs(COURSES_DIR / "专业课" / college)


def _all_major_pairs():
    return [
        (college, major)
        for college in _college_names()
        for major in _major_names(college)
    ]


def _closest(query, candidates, limit=3):
    """用 difflib 找最接近的 1~3 个候选；找不到时逐步放低门槛。"""
    unique = sorted({item for item in candidates if item})
    if not unique:
        return []
    for cutoff in (0.6, 0.3, 0.0):
        matches = difflib.get_close_matches(query, unique, n=limit, cutoff=cutoff)
        if matches:
            return matches
    return []


def _suggestion_text(items):
    return "、".join(items)


def _is_other_option(value):
    """下拉框是否选择了「其他（在下面写）」。"""
    return value in (OTHER_OPTION, "其他")


def _choose_field(sections, key, manual_key, label, manual_label, noun):
    """按“补充框 > 下拉框”的优先级取值，返回 (原值, 来源)。

    来源用于 IMPORT_META 追踪：manual / dropdown / missing。
    下拉选了「其他」但补充框为空时直接失败，不继续猜。
    """
    manual_value = _section(sections, manual_key)
    dropdown_value = _section(sections, key)

    if manual_value:
        return manual_value, "manual"
    if _is_other_option(dropdown_value):
        _fail(
            "{label}选择了「{other}」，但「{manual}」为空。"
            "请在下方的「{manual}」里填写{noun}，或从下拉里选一个。".format(
                label=label,
                other=OTHER_OPTION,
                manual=manual_label,
                noun=noun,
            )
        )
    if dropdown_value:
        return dropdown_value, "dropdown"
    return "", "missing"


def _strip_trailing_parenthetical(text):
    """去掉末尾的括号说明，例如「计算机与信息技术学院（软件学院）」。"""
    previous = None
    while text and text != previous:
        previous = text
        text = re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", text)
        text = _normalize_spaces(text)
    return text


def _resolve_college(value, label="开课学院"):
    """把用户填写的学院归一成 courses/专业课 下的真实目录名。"""
    text = _strip_trailing_parenthetical(_single_line(value, label))
    if not text:
        _fail("{}去掉括号说明后为空。".format(label))
    text = _validate_component(text, label)

    colleges = _college_names()
    if text in colleges:
        return text

    suggestions = _closest(text, colleges)
    if suggestions:
        _fail(
            "找不到学院「{}」。你是不是想写：{} ？"
            "可用的学院共 {} 个，见 README 的「按学院直达」。".format(
                text, _suggestion_text(suggestions), len(colleges)
            )
        )
    _fail("找不到学院「{}」。仓库里暂时没有可用的学院目录，请联系维护者。".format(text))


def _resolve_major_in_college(college, major):
    """在指定学院下校验专业；失败时给出本院候选和跨学院归属。

    学院模板只写纯专业名，因此「学院字段写错 + 专业名正确」是很常见的
    错误。只在写错的学院里找近似专业会给出误导性的候选（例如文学院里
    的汉语言文学），所以这里同时查全校同名专业，明确指出它属于哪个学院。
    """
    majors = _major_names(college)
    if major in majors:
        return

    local_suggestions = _closest(major, majors)
    local_text = _suggestion_text(
        ["{}·{}".format(college, item) for item in local_suggestions]
    )

    # 第一优先级：专业名本身是存在的，只是学院写错了。
    exact_pairs = [
        (other_college, name)
        for other_college, name in _all_major_pairs()
        if name == major
    ]
    if exact_pairs:
        correct_text = _suggestion_text(
            ["{}·{}".format(other_college, name) for other_college, name in exact_pairs]
        )
        message = (
            "在学院「{}」下找不到专业「{}」。专业「{}」实际属于：{}。"
            "请把「开课学院」改成「{}」，或改用对应学院的模板。".format(
                college, major, major, correct_text, exact_pairs[0][0]
            )
        )
        if local_text:
            message += "（本院可选专业：{}）".format(local_text)
        _fail(message)

    if local_text:
        _fail(
            "在学院「{}」下找不到专业「{}」。你是不是想写：{} ？"
            "（候选已带上是哪个学院下的）".format(college, major, local_text)
        )

    # 学院下没有专业目录（或专业名也差得太远）时，再给全校近似候选。
    all_pairs = _all_major_pairs()
    global_suggestions = _closest(major, [name for _college, name in all_pairs])
    if global_suggestions:
        colleges_by_major = {}
        for other_college, name in all_pairs:
            colleges_by_major.setdefault(name, []).append(other_college)
        suggestion_items = []
        for name in global_suggestions:
            for other_college in colleges_by_major.get(name, []):
                suggestion_items.append("{}·{}".format(other_college, name))
                if len(suggestion_items) >= 3:
                    break
            if len(suggestion_items) >= 3:
                break
        if suggestion_items:
            _fail(
                "在学院「{}」下找不到专业「{}」。你是不是想写：{} ？"
                "（候选都带上了所属学院）".format(
                    college, major, _suggestion_text(suggestion_items)
                )
            )

    _fail(
        "在学院「{}」下找不到专业「{}」。该学院下暂时没有专业目录，"
        "请联系维护者。".format(college, major)
    )


def _resolve_major(value, college=None):
    """解析专业，返回 (学院, 专业)。

    * 值里带「·」或「/」：按「学院·专业」拆分并校验；
    * 值里只有专业名且给了 college：直接用「college + 专业名」定位；
    * 值里只有专业名且没给 college：沿用旧的全局唯一匹配逻辑。
    """
    text = _single_line(value, "所属专业")

    # 统一分隔符，并容忍分隔符两侧的空格。
    text = re.sub(r"\s*[·/／]\s*", "·", text)

    if "·" in text:
        parts = text.split("·")
        if len(parts) != 2 or not all(parts):
            _fail(
                "所属专业格式不正确：{}；请写成「学院·专业」「学院/专业」，"
                "或只写专业名（学院模板会自动使用「开课学院」）。".format(text)
            )
        parsed_college = _resolve_college(parts[0], "所属专业中的学院名")
        major = _validate_component(parts[1], "所属专业中的专业名")
        _resolve_major_in_college(parsed_college, major)
        return parsed_college, major

    major = _validate_component(text, "所属专业中的专业名")
    if college:
        _resolve_major_in_college(college, major)
        return college, major

    pairs = [(college, name) for college, name in _all_major_pairs() if name == major]
    if len(pairs) == 1:
        return pairs[0]
    if len(pairs) > 1:
        _fail(
            "专业「{}」在多个学院都有，无法自动判断：{}。"
            "请在「所属专业」里写成「学院·专业」，或使用学院模板"
            "（学院已固定，专业只写专业名）。".format(
                major,
                _suggestion_text(
                    ["{}·{}".format(college, name) for college, name in pairs]
                ),
            )
        )

    all_pairs = _all_major_pairs()
    suggested_majors = _closest(major, [name for _college, name in all_pairs])
    colleges_by_major = {}
    for college, name in all_pairs:
        colleges_by_major.setdefault(name, []).append(college)
    suggestion_items = []
    for name in suggested_majors:
        for college in colleges_by_major.get(name, []):
            suggestion_items.append("{}·{}".format(college, name))
            if len(suggestion_items) >= 3:
                break
        if len(suggestion_items) >= 3:
            break

    if suggestion_items:
        _fail(
            "找不到专业「{}」。你是不是想写：{} ？"
            "候选都带上了所属学院；请按「学院·专业」填写，"
            "或改用学院模板（专业只写专业名）。".format(
                major, _suggestion_text(suggestion_items)
            )
        )
    _fail("找不到专业「{}」。仓库里暂时没有可用的专业目录，请联系维护者。".format(major))


def _relative(path):
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _resolve_inside_courses(parts):
    """拼出路径并做真实路径校验，确保最终位置在 courses/ 之下。"""
    raw = COURSES_DIR.joinpath(*parts)
    try:
        resolved = raw.resolve(strict=False)
        courses_root = COURSES_DIR.resolve()
    except OSError as exc:
        _fail("解析目标路径失败：{}".format(exc))
    try:
        resolved.relative_to(courses_root)
    except ValueError:
        _fail("目标路径越出 courses/ 目录，已拒绝。")
    return resolved


def _year_term(raw, body):
    value = _single_line(raw, "年份学期")
    if value.startswith("其他"):
        match = re.search(r"(20\d{2})\s*年?\s*(春|秋)", value)
        if not match:
            match = re.search(r"(20\d{2})\s*年?\s*(春|秋)", body)
        if not match:
            _fail(
                "年份学期选择了「其他」，但正文中没有写明具体的 20xx春 / 20xx秋；"
                "请在附件框的说明文字里补上。"
            )
        return match.group(1) + match.group(2)
    if re.fullmatch(r"20\d{2}[春秋]", value):
        return value
    _fail("年份学期格式不正确：{}；应为 2025秋 这类格式。".format(value))


def _parse_form(title, body):
    """解析表单，返回归档所需的全部字段。"""
    del title  # 标题不参与路径计算，避免标题写错导致目录跑偏。
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip():
        _fail("Issue 正文为空，无法解析「上传资料」表单。")

    sections = _parse_sections(body)
    category = _single_line(
        _required_section(sections, "category", "课程类别"), "课程类别"
    )
    if category not in ("公共课", "专业课"):
        _fail("课程类别只能是「公共课」或「专业课」。")

    if category == "公共课":
        # 公共课模板的课程名有两处：「课程名称」下拉 + 「课程名」补充框。
        # 补充框只在选了「其他（在下面写）」或下拉为空时使用；如果下拉
        # 已经是一个具体课程，保留下拉值，避免用户切换选项后残留的旧补充
        # 文本把课程覆盖掉。
        dropdown_course = _section(sections, "course")
        manual_course = _section(sections, "course_manual")
        if _is_other_option(dropdown_course):
            if not manual_course:
                _fail(
                    "课程名称选择了「{}」，但「课程名」为空。"
                    "请在下方的「课程名」里填写具体课程名，或从下拉里选一个。".format(
                        OTHER_OPTION
                    )
                )
            course_value = manual_course
        elif dropdown_course:
            course_value = dropdown_course
        elif manual_course:
            course_value = manual_course
        else:
            course_value = ""
        if not course_value:
            _fail(
                "Issue 正文缺少必填项「课程名称」：请从下拉里选一门课，"
                "或在「课程名」里填写仓库里已有的课程名。"
            )
        if _is_other_option(course_value):
            _fail(
                "「课程名称」不能是「{}」本身；请从下拉里选具体课程，"
                "或在「课程名」里填写完整课程名。".format(OTHER_OPTION)
            )
    else:
        course_value = _required_section(sections, "course", "课程名称")
    course = _validate_component(
        _single_line(course_value, "课程名称"), "课程名称"
    )

    year = _year_term(_required_section(sections, "year", "年份学期"), body)
    material_type = _single_line(
        _required_section(sections, "type", "资料类型"), "资料类型"
    )
    if material_type not in TYPE_TO_DIR:
        _fail(
            "资料类型「{}」不在允许范围内；请从表单下拉框选择。".format(material_type)
        )

    confirm = _section(sections, "confirm")
    if not confirm:
        _fail("Issue 正文缺少必填项「确认」；请重新使用「上传资料」表单。")
    checked_lines = re.findall(
        r"^\s*-\s*\[[xX]\]\s*(.+?)\s*$", confirm, re.MULTILINE
    )
    if not any(CONFIRM_REQUIREMENT in line for line in checked_lines):
        _fail(
            "请先勾选确认项：{}。".format(CONFIRM_REQUIREMENT)
        )

    college = ""
    major = ""
    college_source = ""
    major_source = ""
    if category == "专业课":
        # 先用「开课学院」定位学院；学院模板已预填，专业只写纯专业名。
        college_value, college_source = _choose_field(
            sections,
            "college",
            "college_manual",
            "开课学院",
            "学院名称",
            "学院",
        )
        if college_value:
            college = _resolve_college(college_value, "开课学院")

        major_value, major_source = _choose_field(
            sections,
            "major",
            "major_manual",
            "所属专业",
            "专业名称",
            "专业",
        )
        if not major_value:
            _fail(
                "Issue 正文缺少必填项「所属专业」，请确认使用的是「上传资料」表单。"
            )
        major_college, major = _resolve_major(major_value, college or None)

        if college and college != major_college:
            _fail(
                "开课学院与所属专业不一致：开课学院写的是「{}」，"
                "专业「{}」实际属于「{}」。请把开课学院改成「{}」，"
                "或在所属专业里只写专业名「{}」（不要带学院前缀）。".format(
                    college,
                    major,
                    major_college,
                    major_college,
                    major,
                )
            )
        if not college:
            # 学院为空时（通用模板只写专业名）从专业解析结果里带出学院。
            college = major_college
            college_source = "derived"

    attachments_text = _required_section(sections, "attachments", "附件")
    all_links = _parse_attachments(attachments_text)
    official = [item for item in all_links if _is_official_attachment(item["url"])]
    ignored = [item for item in all_links if item not in official]

    if not official:
        _fail(
            "附件区没有找到 GitHub 官方附件链接。请把文件直接拖进「附件」框，"
            "不要只贴网盘或其他网站链接。"
        )
    if len(official) > MAX_ATTACHMENTS:
        _fail(
            "附件数量为 {} 个，超过上限 {} 个；请分多次提交。".format(
                len(official), MAX_ATTACHMENTS
            )
        )
    if ignored:
        print(
            "提示：已忽略 {} 个非 GitHub 官方附件链接（只处理 github.com / "
            "githubusercontent.com 的附件）。".format(len(ignored))
        )

    return {
        "category": category,
        "college": college,
        "college_source": college_source,
        "major": major,
        "major_source": major_source,
        "course": course,
        "year": year,
        "type": material_type,
        "attachments": official,
    }


def _target_directory(form):
    """计算并校验目标目录；返回 (目标目录, 课程目录)。"""
    type_dir = TYPE_TO_DIR[form["type"]]

    if form["category"] == "公共课":
        course_parts = ["公共课", form["course"]]
    else:
        course_parts = [
            "专业课",
            form["college"],
            form["major"],
            form["course"],
        ]

    course_dir = _resolve_inside_courses(course_parts)
    if not course_dir.is_dir():
        if form["category"] == "公共课":
            available = _iter_subdirs(COURSES_DIR / "公共课")
            scope = "公共课"
        else:
            available = _iter_subdirs(
                COURSES_DIR / "专业课" / form["college"] / form["major"]
            )
            scope = "{}·{}".format(form["college"], form["major"])
        suggestions = _closest(form["course"], available)
        if suggestions:
            _fail(
                "课程目录不存在：{}。你是不是想写：{} ？"
                "请确认「课程名称」与仓库里的课程文件夹名完全一致"
                "（注意不要多字、少字或写错别字）。".format(
                    _relative(course_dir), _suggestion_text(suggestions)
                )
            )
        _fail(
            "课程目录不存在：{}。{}下暂时没有课程目录，"
            "请先把课程信息发给维护者建目录。".format(
                _relative(course_dir), scope
            )
        )

    target_dir = _resolve_inside_courses(course_parts + [type_dir])
    if not target_dir.is_dir():
        _fail(
            "资料目录不存在：{}。该课程目录结构不完整，请联系维护者补建"
            "笔记/课件/试卷/作业 四个子目录。".format(_relative(target_dir))
        )

    # 再兜底检查一次：任何情况下都不能跑到 courses/ 外面。
    try:
        target_dir.relative_to(COURSES_DIR.resolve())
    except ValueError:
        _fail("目标路径越出 courses/ 目录，已拒绝。")

    return target_dir, course_dir


def _allocate_filename(target_dir, base, ext, reserved):
    """在目标目录里选一个不冲突的文件名；重名时从 -2 开始追加序号。

    第一次导入是 `...pdf`，第二次是 `...-2.pdf`，第三次是 `...-3.pdf`。
    """
    number = 0
    while True:
        suffix = "" if number == 0 else "-{}".format(number)
        name = "{}{}.{}".format(base, suffix, ext)
        if len(name.encode("utf-8")) > 240:
            _fail("生成的文件名过长（{} 字节），请缩短课程名。".format(len(name.encode("utf-8"))))
        if name not in reserved and not os.path.lexists(str(target_dir / name)):
            reserved.add(name)
            return name
        number = 2 if number == 0 else number + 1


def _human_size(num):
    if num >= 1024 * 1024:
        return "{:.1f}MB".format(num / (1024 * 1024))
    if num >= 1024:
        return "{:.1f}KB".format(num / 1024)
    return "{}B".format(num)


def _http_error_detail(exc):
    try:
        raw = exc.read(64 * 1024).decode("utf-8", "replace")
        data = json.loads(raw)
        if isinstance(data, dict):
            detail = " ".join(str(data.get("message") or "").split())
            return detail[:300]
    except Exception:
        pass
    return ""


def _looks_like_error_document(sample, content_type):
    """识别 HTML/JSON/XML 错误页，避免把 200 的错误页当成资料存进来。"""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime in (
        "text/html",
        "application/xhtml+xml",
        "application/json",
        "application/problem+json",
        "application/xml",
        "text/xml",
    ):
        return True

    head = sample[:2048].decode("utf-8", "ignore").lstrip("\ufeff \t\r\n").lower()
    if head.startswith(("<!doctype html", "<html", "<head", "<body")):
        return True
    # 有些错误页前面有少量文本，再兜底看前 2KB 里是否出现 html 根标签。
    return "<html" in head or "<title" in head


class _OfficialRedirectHandler(request.HTTPRedirectHandler):
    """只允许重定向到 GitHub 官方附件域，并在跨域时移除 Authorization。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_official_attachment(newurl):
            _fail(
                "附件下载被重定向到非 GitHub 官方附件地址（{}），已拒绝。".format(
                    _safe_url(newurl)
                )
            )
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is not None:
            try:
                old_host = (parse.urlsplit(req.full_url).hostname or "").lower()
                new_host = (parse.urlsplit(newurl).hostname or "").lower()
                if old_host != new_host:
                    new_request.remove_header("Authorization")
            except Exception:
                pass
        return new_request


_DOWNLOAD_OPENER = request.build_opener(_OfficialRedirectHandler())


def _download_one(item, index, temp_dir):
    """下载一个附件到临时目录，返回临时文件、扩展名和大小。"""
    url = item["url"]
    if not _is_official_attachment(url):
        _fail("附件 #{} 不是 GitHub 官方 HTTPS 附件地址，已拒绝。".format(index))
    label_ext = _ext_from_name(item["label"]) if item["label"] else None
    url_ext = _ext_from_url(url)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/octet-stream",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    try:
        attachment_host = (parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        attachment_host = ""
    # 只对 github.com 附件端点带 token；githubusercontent 使用签名 URL，
    # 不需要也不应该收到仓库 token。
    if token and attachment_host in ("github.com", "www.github.com"):
        headers["Authorization"] = "Bearer {}".format(token)

    try:
        req = request.Request(url, headers=headers)
        with _DOWNLOAD_OPENER.open(req, timeout=DOWNLOAD_TIMEOUT) as response:
            final_url = response.geturl()
            if not _is_official_attachment(final_url):
                _fail(
                    "附件 #{} 下载地址被重定向到非 GitHub 官方附件地址（{}），已拒绝。".format(
                        index, _safe_url(final_url)
                    )
                )

            content_disposition = response.headers.get("Content-Disposition")
            content_type = response.headers.get("Content-Type")
            if _looks_like_error_document(b"", content_type):
                _fail(
                    "附件 #{} 返回的是 HTML/JSON/XML 错误页，"
                    "不是资料文件，已拒绝入库。".format(index)
                )
            header_filename = _filename_from_content_disposition(content_disposition)
            ext = _choose_extension(label_ext, header_filename, content_type, url_ext)
            if not ext:
                _fail(
                    "无法确定附件 #{} 的扩展名：链接里没有带文件名，"
                    "服务器也没有返回可识别的文件类型。请改用带文件名的附件链接。".format(
                        index
                    )
                )

            header_ext = _ext_from_name(header_filename) if header_filename else None
            type_ext = _ext_from_content_type(content_type)
            # 只要任何线索显示是 zip，就按 20MB 收紧限制。
            zip_hint = "zip" in {
                ext,
                label_ext or "",
                url_ext or "",
                header_ext or "",
                type_ext or "",
            }
            limit = MAX_ZIP_BYTES if zip_hint else MAX_ATTACHMENT_BYTES

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    announced = int(content_length)
                except (TypeError, ValueError):
                    announced = None
                if announced is not None and announced > limit:
                    _fail(
                        "附件 #{} 超过大小限制：{}，上限为 {}。".format(
                            index,
                            _human_size(announced),
                            "20MB（zip）" if zip_hint else "50MB",
                        )
                    )

            # 先读一小段做内容识别，确认不是 HTML 错误页后再落盘。
            sample = response.read(64 * 1024)
            if not sample:
                _fail("附件 #{} 是空文件，已拒绝入库。".format(index))
            if _looks_like_error_document(sample, content_type):
                _fail(
                    "附件 #{} 返回的是 HTML/JSON/XML 错误页，"
                    "不是资料文件，已拒绝入库。".format(index)
                )

            fd, tmp_name = tempfile.mkstemp(
                prefix="attachment-{}-".format(index), dir=str(temp_dir)
            )
            written = len(sample)
            if written > limit:
                os.close(fd)
                _fail(
                    "附件 #{} 超过大小限制：{}，上限为 {}。".format(
                        index,
                        _human_size(written),
                        "20MB（zip）" if zip_hint else "50MB",
                    )
                )
            with os.fdopen(fd, "wb") as handle:
                handle.write(sample)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        _fail(
                            "附件 #{} 超过大小限制：{}，上限为 {}。".format(
                                index,
                                _human_size(written),
                                "20MB（zip）" if zip_hint else "50MB",
                            )
                        )
                    handle.write(chunk)
            return {
                "tmp": Path(tmp_name),
                "ext": ext,
                "size": written,
                "url": url,
                "index": index,
            }
    except error.HTTPError as exc:
        detail = _http_error_detail(exc)
        _fail(
            "下载附件 #{} 失败（HTTP {}）{}".format(
                index, exc.code, "：{}".format(detail) if detail else ""
            )
        )
    except error.URLError as exc:
        _fail("下载附件 #{} 失败（网络错误）：{}".format(index, exc.reason))
    except TimeoutError:
        _fail("下载附件 #{} 超时（{} 秒）。".format(index, DOWNLOAD_TIMEOUT))
    except ImportFailure:
        raise
    except OSError as exc:
        _fail("下载附件 #{} 失败：{}".format(index, exc))
    except Exception as exc:  # 兜底，保证不打印裸 traceback
        _fail("下载附件 #{} 时发生未预期错误：{}".format(index, exc))


def _test_file_ext(path, index):
    """仅测试用：校验 --attachment-file 指向的本地文件并返回扩展名。"""
    source = Path(path).expanduser()
    if not source.is_file():
        _fail("测试附件 #{} 不存在或不是普通文件：{}".format(index, path))
    ext = _ext_from_name(source.name)
    if not ext:
        _fail("测试附件 #{} 的文件名没有可识别扩展名：{}".format(index, path))
    return ext


def _copy_test_file(path, index, temp_dir):
    """仅测试用：把本地文件复制进临时目录，冒充下载结果。"""
    source = Path(path).expanduser()
    ext = _test_file_ext(path, index)
    limit = MAX_ZIP_BYTES if ext == "zip" else MAX_ATTACHMENT_BYTES
    try:
        size = source.stat().st_size
    except OSError as exc:
        _fail("读取测试附件 #{} 失败：{}".format(index, exc))
    if size > limit:
        _fail(
            "测试附件 #{} 超过大小限制：{}，上限为 {}。".format(
                index,
                _human_size(size),
                "20MB（zip）" if ext == "zip" else "50MB",
            )
        )
    if size == 0:
        _fail("测试附件 #{} 是空文件，已拒绝入库。".format(index))

    fd, tmp_name = tempfile.mkstemp(
        prefix="attachment-{}-".format(index), dir=str(temp_dir)
    )
    written = 0
    try:
        with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    _fail(
                        "测试附件 #{} 超过大小限制：{}，上限为 {}。".format(
                            index,
                            _human_size(written),
                            "20MB（zip）" if ext == "zip" else "50MB",
                        )
                    )
                dst.write(chunk)
        if written == 0:
            _fail("测试附件 #{} 是空文件，已拒绝入库。".format(index))
    except BaseException:
        try:
            Path(tmp_name).unlink()
        except OSError:
            pass
        raise

    return {
        "tmp": Path(tmp_name),
        "ext": ext,
        "size": written,
        "url": "test-file:{}".format(source.name),
        "index": index,
    }


def _api_get_issue(issue_number):
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token:
        _fail(
            "缺少 GITHUB_TOKEN 环境变量，无法读取 Issue；"
            "本地调试可设置 IMPORT_ISSUE_BODY_FILE 指向假正文。"
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        _fail("缺少或错误的 GITHUB_REPOSITORY 环境变量，格式应为 owner/repo。")

    url = "https://api.github.com/repos/{}/issues/{}".format(
        parse.quote(repo, safe="/"), issue_number
    )
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer {}".format(token),
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with request.urlopen(req, timeout=API_TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
    except error.HTTPError as exc:
        detail = _http_error_detail(exc)
        if exc.code == 404:
            _fail("找不到 Issue #{}，请检查编号是否正确。".format(issue_number))
        if exc.code in (401, 403):
            _fail(
                "读取 Issue #{} 被拒绝（HTTP {}）{}；请检查 GITHUB_TOKEN 权限。".format(
                    issue_number, exc.code, "：{}".format(detail) if detail else ""
                )
            )
        _fail(
            "读取 Issue #{} 失败（HTTP {}）{}".format(
                issue_number, exc.code, "：{}".format(detail) if detail else ""
            )
        )
    except error.URLError as exc:
        _fail("连接 GitHub API 失败：{}".format(exc.reason))
    except TimeoutError:
        _fail("读取 Issue #{} 超时（{} 秒）。".format(issue_number, API_TIMEOUT))
    except OSError as exc:
        _fail("读取 Issue #{} 失败：{}".format(issue_number, exc))

    try:
        data = json.loads(raw)
    except ValueError:
        _fail("GitHub API 返回的不是合法 JSON，无法读取 Issue #{}。".format(issue_number))
    if not isinstance(data, dict):
        _fail("GitHub API 返回的数据格式异常，无法读取 Issue #{}。".format(issue_number))
    return str(data.get("title") or ""), str(data.get("body") or "")


def _load_issue(issue_number):
    body_file = os.environ.get("IMPORT_ISSUE_BODY_FILE", "").strip()
    if body_file:
        try:
            body = Path(body_file).read_text(encoding="utf-8")
        except OSError as exc:
            _fail("读取本地 Issue 正文失败：{}".format(exc))
        title = os.environ.get("IMPORT_ISSUE_TITLE", "").strip()
        return title, body
    return _api_get_issue(issue_number)


def _safe_log_text(value):
    """把标题等外部文本压成单行，避免控制字符污染 Actions 日志/PR 正文。"""
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", str(value or ""))


def _print_meta(form):
    print("IMPORT_META course={}".format(form["course"]))
    print("IMPORT_META year={}".format(form["year"]))
    print("IMPORT_META type={}".format(form["type"]))
    if form["category"] == "专业课":
        print("IMPORT_META college={}".format(form["college"]))
        print("IMPORT_META major={}".format(form["major"]))
        print("IMPORT_META college_source={}".format(form["college_source"]))
        print("IMPORT_META major_source={}".format(form["major_source"]))


def _move_exclusive(source, destination):
    """以 O_EXCL 方式写入目标文件，绝不覆盖已有文件。"""
    created = False
    try:
        with open(str(source), "rb") as src:
            with open(str(destination), "xb") as dst:
                created = True
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
    except BaseException:
        # 只清理本次新建的半成品；FileExistsError 时目标不是我们创建的，不能动。
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
    try:
        source.unlink()
    except OSError:
        pass


def _rollback_moved(moved):
    """失败时删掉本次已经移动的文件，保证不留下半套资料。"""
    for path in moved:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def _run(args):
    if args.issue_number <= 0:
        _fail("Issue 编号必须是正整数。")
    if not COURSES_DIR.is_dir():
        _fail("仓库根目录下找不到 courses/，请确认脚本位于仓库的 scripts/ 目录。")

    title, body = _load_issue(args.issue_number)
    if title:
        print("Issue #{}：{}".format(args.issue_number, _safe_log_text(title)))

    form = _parse_form(title, body)
    target_dir, _course_dir = _target_directory(form)
    target_rel = _relative(target_dir)

    test_files = list(getattr(args, "attachment_file", None) or [])
    if test_files:
        if len(test_files) != len(form["attachments"]):
            _fail(
                "--attachment-file（仅测试用）数量为 {}，但 Issue 里有 {} 个附件；"
                "两者必须一一对应。".format(len(test_files), len(form["attachments"]))
            )
        print(
            "警告：已启用 --attachment-file（仅测试用），将跳过网络下载，"
            "直接使用本地文件冒充附件。",
            file=sys.stderr,
        )

    print("课程类别：{}".format(form["category"]))
    if form["category"] == "专业课":
        print("开课学院：{}".format(form["college"]))
        print("所属专业：{}".format(form["major"]))
    print("课程名称：{}".format(form["course"]))
    print("年份学期：{}".format(form["year"]))
    print("资料类型：{} → 目录 {}".format(form["type"], TYPE_TO_DIR[form["type"]]))
    print("附件数量：{}".format(len(form["attachments"])))
    print("目标目录：{}".format(target_rel))

    file_type = TYPE_TO_FILENAME[form["type"]]
    base = "{}-{}-{}".format(form["year"], file_type, form["course"])

    if args.dry_run:
        reserved = set()
        for index, item in enumerate(form["attachments"], 1):
            if test_files:
                ext = _test_file_ext(test_files[index - 1], index)
            else:
                label_ext = _ext_from_name(item["label"]) if item["label"] else None
                url_ext = _ext_from_url(item["url"])
                ext = label_ext or url_ext
            if ext:
                name = _allocate_filename(target_dir, base, ext, reserved)
            else:
                name = "{}.<下载时确认扩展名>".format(base)
                # 预占一个名字，避免多个未知扩展名互相冲突；序号与正式
                # 归档保持一致，从 -2 开始（dry-run 无法预知真实扩展名）。
                number = 2
                candidate = base
                while candidate in reserved:
                    candidate = "{}-{}".format(base, number)
                    number += 1
                reserved.add(candidate)
            print(
                "附件 {}：{} → {}".format(
                    index, _safe_log_text(item["url"]), name
                )
            )

        _print_meta(form)
        print("DRY_RUN 已通过解析与安全检查；未下载、未写入任何文件。")
        print("IMPORT_OK {} {}".format(target_rel, len(form["attachments"])))
        return 0

    temp_dir = Path(tempfile.mkdtemp(prefix="course-vault-import-"))
    downloaded = []
    moved = []
    try:
        for index, item in enumerate(form["attachments"], 1):
            if test_files:
                print(
                    "正在读取测试附件 {}/{}（仅测试用，不访问网络）...".format(
                        index, len(form["attachments"])
                    )
                )
                downloaded.append(
                    _copy_test_file(test_files[index - 1], index, temp_dir)
                )
            else:
                print("正在下载附件 {}/{} ...".format(index, len(form["attachments"])))
                downloaded.append(_download_one(item, index, temp_dir))

        total_size = sum(item["size"] for item in downloaded)
        if total_size > MAX_TOTAL_BYTES:
            _fail(
                "本次附件总大小为 {}，超过单个 PR 的 100MB 上限；"
                "请分多次提交或改用网盘链接。".format(_human_size(total_size))
            )

        reserved = set()
        moves = []
        for item in downloaded:
            name = _allocate_filename(target_dir, base, item["ext"], reserved)
            moves.append((item, target_dir / name))

        # 全部下载并校验通过后才动 courses/；中途失败会回滚已移动的文件。
        for item, destination in moves:
            # O_EXCL 仍然可能和另一个进程撞名；撞名时重新分配 -2/-3，绝不覆盖。
            while True:
                try:
                    _move_exclusive(item["tmp"], destination)
                    break
                except FileExistsError:
                    name = _allocate_filename(
                        target_dir, base, item["ext"], reserved
                    )
                    destination = target_dir / name
            moved.append(destination)
            print(
                "已保存：{}（{}）".format(
                    _relative(destination), _human_size(item["size"])
                )
            )
    except ImportFailure:
        _rollback_moved(moved)
        raise
    except OSError as exc:
        _rollback_moved(moved)
        _fail("写入目标目录失败：{}".format(exc))
    except Exception as exc:
        _rollback_moved(moved)
        _fail("归档过程中发生未预期错误：{}".format(exc))
    except BaseException:
        # Ctrl+C / 被取消时也回滚，避免留下半套资料。
        _rollback_moved(moved)
        raise
    finally:
        shutil.rmtree(str(temp_dir), ignore_errors=True)

    _print_meta(form)
    print("IMPORT_OK {} {}".format(target_rel, len(moved)))
    return 0


def main(argv=None):
    parser = FriendlyArgumentParser(
        prog="import_issue.py",
        description="读取 GitHub Issue 表单，把附件归档到 courses/。",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        required=True,
        metavar="N",
        help="要处理的 Issue 编号",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析和校验，不下载、不写文件",
    )
    parser.add_argument(
        "--attachment-file",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "（仅测试用）用本地文件冒充 Issue 附件，跳过网络下载；"
            "可重复，数量必须与 Issue 附件数一致。生产环境不要使用。"
        ),
    )
    args = parser.parse_args(argv)

    try:
        return _run(args)
    except ImportFailure as exc:
        print("IMPORT_FAIL {}".format(exc))
        return 2
    except KeyboardInterrupt:
        print("IMPORT_FAIL 操作已取消。")
        return 2
    except Exception as exc:  # 最后一道兜底：绝不打印裸 traceback
        print("IMPORT_FAIL 未预期的错误：{}".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
