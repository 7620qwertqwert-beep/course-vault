#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「上传资料」Issue 表单里的附件归档到 courses/。

用法：
    python3 scripts/import_issue.py --issue-number <N> [--dry-run]

只使用 Python 3 标准库，零第三方依赖。

安全约定（任意一条不满足都会整体失败，退出码 2，不写入任何课程文件）：
  * 课程目录必须已经存在，课程名必须与目录名完全一致；
  * 所有目标路径都必须落在 courses/ 内，拒绝 ..、斜杠等路径逃逸；
  * 附件数量 1~5 个，单个不超过 50MB（zip 不超过 20MB）；
  * 扩展名只允许 pdf/doc/docx/ppt/pptx/xls/xlsx/md/txt/jpg/jpeg/png/zip。

本地调试：
    IMPORT_ISSUE_BODY_FILE=/tmp/fake_issue.md \
    IMPORT_ISSUE_TITLE='[上传] 测试' \
    python3 scripts/import_issue.py --issue-number 1 --dry-run
设置 IMPORT_ISSUE_BODY_FILE 后不再访问 GitHub API。
"""

import argparse
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

FIELD_ALIASES = {
    "category": ("课程类别", "类别"),
    "college": ("开课学院", "学院"),
    "major": ("所属专业", "专业"),
    "course": ("课程名称", "课程名", "课程"),
    "year": ("年份学期", "学期", "年份/学期"),
    "type": ("资料类型", "类型"),
    "attachments": ("附件", "附件区", "附件或链接", "上传附件", "文件"),
    "confirm": ("确认", "确认事项", "自查确认"),
}

OFFICIAL_ATTACHMENT_HOSTS = {
    "github.com",
    "www.github.com",
    "user-images.githubusercontent.com",
    "private-user-images.githubusercontent.com",
    "objects.githubusercontent.com",
}

USER_AGENT = "course-vault-import-bot/1.0 (+https://github.com/)"


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


def _single_line(value, label):
    value = (value or "").strip()
    if not value:
        _fail("表单必填项「{}」为空。".format(label))
    if "\n" in value or "\r" in value:
        _fail("表单字段「{}」应为单行内容，请重新填写。".format(label))
    return value


def _is_blank_response(value):
    compact = (value or "").strip()
    return compact in ("", "_No response_", "_No response_。", "_无响应_", "-", "—")


def _parse_sections(body):
    """把 Issue Forms 的 `### 字段名` + 下一段内容解析成字典。"""
    heading_re = re.compile(r"^###[ \t]*(.+?)[ \t]*$", re.MULTILINE)
    matches = list(heading_re.finditer(body))
    sections = {}
    for index, match in enumerate(matches):
        label = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value = body[start:end]
        # GitHub 不会把模板注释渲染进正文，这里仍做一次清理，兼容手工粘贴。
        value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
        sections[label] = value.strip()
    return sections


def _section(sections, key):
    for alias in FIELD_ALIASES[key]:
        if alias in sections:
            value = sections[alias]
            if _is_blank_response(value):
                return ""
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
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host not in OFFICIAL_ATTACHMENT_HOSTS:
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
    value = (value or "").strip()
    if not value:
        _fail("{}不能为空。".format(label))
    if value in (".", "..") or ".." in value:
        _fail("{}包含非法路径片段「..」，已拒绝路径逃逸。".format(label))
    if any(ch in value for ch in '/\\<>:"|?*[]'):
        _fail("{}包含路径分隔符或非法字符，已拒绝。".format(label))
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        _fail("{}包含不可见控制字符，已拒绝。".format(label))
    if value != value.strip() or value.startswith("."):
        _fail("{}不能以点开头或以空白开头/结尾。".format(label))
    if value.endswith((".", " ")):
        _fail("{}不能以点或空格结尾。".format(label))
    if len(value) > 120:
        _fail("{}过长（超过 120 个字符）。".format(label))
    return value


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

    course = _validate_component(
        _single_line(_required_section(sections, "course", "课程名称"), "课程名称"),
        "课程名称",
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
    if not re.search(r"^\s*-\s*\[[xX]\]", confirm, re.MULTILINE):
        _fail("请先勾选确认项：文件里不含本人或他人的姓名、学号、手机号、身份证号。")

    college = ""
    major = ""
    if category == "专业课":
        college = _validate_component(
            _single_line(_required_section(sections, "college", "开课学院"), "开课学院"),
            "开课学院",
        )
        major_value = _single_line(
            _required_section(sections, "major", "所属专业"), "所属专业"
        )
        if "·" not in major_value:
            _fail(
                "所属专业格式不正确：{}；必须从下拉框选择「学院名·专业名」。".format(
                    major_value
                )
            )
        college_part, major_name = major_value.split("·", 1)
        college_part = _validate_component(college_part, "所属专业中的学院名")
        major_name = _validate_component(major_name, "所属专业中的专业名")
        if college != college_part:
            _fail(
                "开课学院与所属专业不一致：学院选的是「{}」，专业是「{}」。".format(
                    college, major_value
                )
            )
        major = major_name

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
        "major": major,
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
        _fail(
            "课程目录不存在：{}。请确认「课程名称」与仓库里的课程文件夹名完全一致"
            "（注意不要多字、少字或写错别字）。".format(_relative(course_dir))
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
    """在目标目录里选一个不冲突的文件名；冲突时从 -1 开始追加序号。"""
    number = 0
    while True:
        suffix = "" if number == 0 else "-{}".format(number)
        name = "{}{}.{}".format(base, suffix, ext)
        if len(name.encode("utf-8")) > 240:
            _fail("生成的文件名过长（{} 字节），请缩短课程名。".format(len(name.encode("utf-8"))))
        if name not in reserved and not os.path.lexists(str(target_dir / name)):
            reserved.add(name)
            return name
        number += 1


def _human_size(num):
    if num >= 1024 * 1024:
        return "{:.1f}MB".format(num / (1024 * 1024))
    if num >= 1024:
        return "{:.1f}KB".format(num / 1024)
    return "{}B".format(num)


def _http_error_detail(exc):
    try:
        raw = exc.read().decode("utf-8", "replace")
        data = json.loads(raw)
        if isinstance(data, dict):
            return str(data.get("message") or "").strip()
    except Exception:
        pass
    return ""


def _download_one(item, index, temp_dir):
    """下载一个附件到临时目录，返回临时文件、扩展名和大小。"""
    url = item["url"]
    label_ext = _ext_from_name(item["label"]) if item["label"] else None
    url_ext = _ext_from_url(url)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/octet-stream",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer {}".format(token)

    try:
        req = request.Request(url, headers=headers)
        with request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as response:
            content_disposition = response.headers.get("Content-Disposition")
            content_type = response.headers.get("Content-Type")
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

            fd, tmp_name = tempfile.mkstemp(
                prefix="attachment-{}-".format(index), dir=str(temp_dir)
            )
            written = 0
            with os.fdopen(fd, "wb") as handle:
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
            if written == 0:
                _fail("附件 #{} 是空文件，已拒绝入库。".format(index))
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


def _print_meta(form):
    print("IMPORT_META course={}".format(form["course"]))
    print("IMPORT_META year={}".format(form["year"]))
    print("IMPORT_META type={}".format(form["type"]))


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
        print("Issue #{}：{}".format(args.issue_number, title))

    form = _parse_form(title, body)
    target_dir, _course_dir = _target_directory(form)
    target_rel = _relative(target_dir)

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
            label_ext = _ext_from_name(item["label"]) if item["label"] else None
            url_ext = _ext_from_url(item["url"])
            ext = label_ext or url_ext
            if ext:
                name = _allocate_filename(target_dir, base, ext, reserved)
            else:
                name = "{}.<下载时确认扩展名>".format(base)
                # 预占一个名字，避免多个未知扩展名互相冲突。
                number = 0
                candidate = base
                while candidate in reserved:
                    number += 1
                    candidate = "{}-{}".format(base, number)
                reserved.add(candidate)
            print("附件 {}：{} → {}".format(index, item["url"], name))

        _print_meta(form)
        print("DRY_RUN 已通过解析与安全检查；未下载、未写入任何文件。")
        print("IMPORT_OK {} {}".format(target_rel, len(form["attachments"])))
        return 0

    temp_dir = Path(tempfile.mkdtemp(prefix="course-vault-import-"))
    downloaded = []
    moved = []
    try:
        for index, item in enumerate(form["attachments"], 1):
            print("正在下载附件 {}/{} ...".format(index, len(form["attachments"])))
            downloaded.append(_download_one(item, index, temp_dir))

        reserved = set()
        moves = []
        for item in downloaded:
            name = _allocate_filename(target_dir, base, item["ext"], reserved)
            moves.append((item, target_dir / name))

        # 全部下载并校验通过后才动 courses/；中途失败会回滚已移动的文件。
        for item, destination in moves:
            if os.path.lexists(str(destination)):
                _fail("目标文件已存在，已停止以避免覆盖：{}".format(_relative(destination)))
            _move_exclusive(item["tmp"], destination)
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
