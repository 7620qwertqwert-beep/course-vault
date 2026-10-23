#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Gitee 仓库里打了 approved 标签的 Issue 归档进本地 GitHub 工作副本。

Gitee 不支持 GitHub 的 Issue Forms（YAML 表单），所以 .gitee/ISSUE_TEMPLATE/*.md
生成的是「### 小节 + 自由文本」的 Markdown，这里按小节解析。
校验、年份归一、类型→目录、文件名规则全部复用 scripts/import_issue.py。

安全/稳定性约束：
* 单实例运行（fcntl.flock），state 原子写入；
* 附件只允许 Gitee 官方 HTTPS 域名，手动处理重定向并校验解析后的 IP；
* 先下载到 staging，全部校验通过后才落盘；PR 失败会回滚已落盘文件；
* 失败最多自动重试 3 次，且只在首次失败和到达上限时评论。
"""
import errno
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import import_issue as imp  # noqa: E402

GITEE_API = "https://gitee.com/api/v5"
OWNER_REPO = "youliboshi/course-vault"
TOKEN = open("/root/.gitee_token", encoding="utf-8").read().strip()
STATE_FILE = "/root/.gitee_import_state.json"
COURSES = os.path.join(REPO_ROOT, "courses")
PUSH_SCRIPT = "/root/cv_push3.py"
DRY = "--dry-run" in sys.argv

LOCK_FILE = "/var/lock/gitee_import.lock"
MAX_RUN_SECONDS = 30 * 60
MAX_ISSUE_PAGES = 20
PER_PAGE = 100
MAX_ATTEMPTS = 3
STAGE_ROOT = "/tmp"

# 资料类型只认这六个值，且必须整行完全相等。
MATERIAL_TYPES = ("笔记", "课件", "试卷", "作业", "实验报告", "复习资料")

# 附件只允许这些域名及其子域（后缀匹配，不用 `in`）。
ALLOWED_ATTACHMENT_HOSTS = (
    "gitee.com",
    "images.gitee.com",
    "giteeusercontent.com",
)
REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5

FILE_EXT_RE = re.compile(
    r"\.(pdf|docx?|pptx?|xlsx?|md|txt|jpe?g|png|zip)(?:$|[?#])", re.I
)

# 运行时状态（由 main 初始化）。
_RUN_START = time.monotonic()
_LOCK_HANDLE = None


class RunTimeout(Exception):
    """整轮运行超过总时限。"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _check_deadline():
    if time.monotonic() - _RUN_START > MAX_RUN_SECONDS:
        raise RunTimeout("运行超过 {} 分钟".format(MAX_RUN_SECONDS // 60))


def _alarm_handler(signum, frame):  # noqa: ARG001
    raise RunTimeout("运行超过 {} 分钟".format(MAX_RUN_SECONDS // 60))


# --------------------------------------------------------------------------
# 单实例锁 / state
# --------------------------------------------------------------------------

def _acquire_lock():
    global _LOCK_HANDLE
    lock_path = LOCK_FILE
    try:
        handle = open(lock_path, "a+")
    except OSError as exc:
        # 正常服务器上 /var/lock 可写；受限沙箱/容器里可能只读。
        # 此时退回系统临时目录，仍然用 flock 保证单实例，不静默放行。
        if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT):
            print("无法打开锁文件 {}：{}".format(lock_path, exc))
            return False
        lock_path = os.path.join(tempfile.gettempdir(), "gitee_import.lock")
        print("提示：{} 不可写，改用 {}".format(LOCK_FILE, lock_path))
        try:
            handle = open(lock_path, "a+")
        except OSError as fallback_exc:
            print("无法打开锁文件 {}：{}".format(lock_path, fallback_exc))
            return False
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        handle.close()
        print("已有实例在跑")
        return False
    _LOCK_HANDLE = handle
    return True


def _release_lock():
    global _LOCK_HANDLE
    if _LOCK_HANDLE is None:
        return
    try:
        import fcntl

        fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    try:
        _LOCK_HANDLE.close()
    except OSError:
        pass
    _LOCK_HANDLE = None


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state):
    """原子写 state：临时文件 + fsync + os.replace。"""
    directory = os.path.dirname(STATE_FILE) or "."
    fd, tmp_name = tempfile.mkstemp(
        prefix=".gitee_import_state.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, STATE_FILE)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def update_state(number, **fields):
    """写前重新加载 state，避免用启动时的旧对象覆盖其他写入。"""
    state = load_state()
    key = str(number)
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    entry.update(fields)
    if "last_at" not in fields:
        entry["last_at"] = _now()
    state[key] = entry
    save_state(state)
    return entry


# --------------------------------------------------------------------------
# Gitee API
# --------------------------------------------------------------------------

def api(path, params=None, method="GET", data=None):
    _check_deadline()
    q = {"access_token": TOKEN}
    q.update(params or {})
    url = "{}{}?{}".format(GITEE_API, path, urllib.parse.urlencode(q))
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "xiaoxia-cv",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read().decode()
    return json.loads(text) if text.strip() else {}


def list_issues():
    """按 page=1..N 拉取 open issue，直到某一页不满 per_page。"""
    issues = []
    for page in range(1, MAX_ISSUE_PAGES + 1):
        _check_deadline()
        batch = api(
            "/repos/{}/issues".format(OWNER_REPO),
            {
                "state": "open",
                "per_page": PER_PAGE,
                "page": page,
                "sort": "created",
                "direction": "asc",
            },
        )
        if not isinstance(batch, list):
            imp._fail("Gitee Issue 列表返回格式异常")
        for issue in batch:
            if isinstance(issue, dict) and "number" in issue:
                issues.append(issue)
        if len(batch) < PER_PAGE:
            break
    return issues


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

# 精确标题；「课程」是旧模板别名，匹配时会优先尝试更长的「课程名称」。
HEADING_ALIASES = (
    ("课程名称", "course"),
    ("开课学院", "college"),
    ("所属专业", "major"),
    ("年份学期", "year"),
    ("资料类型", "type"),
    ("附件", "attachments"),
    ("确认", "confirm"),
    ("课程", "course"),
)
HEADING_ALIASES = tuple(
    sorted(HEADING_ALIASES, key=lambda item: len(item[0]), reverse=True)
)

COMBINED_HEADING_RE = re.compile(
    r"^(?:开课学院|学院)\s*[/／、]\s*(?:所属专业|专业)"
    r"\s*(?:[（(][^）)]*[）)])?\s*(.*)$"
)


def _clean(text):
    """去掉值前面的括号占位说明和反引号。"""
    text = (text or "").strip()
    text = re.sub(r"^[（(][^）)]*[）)]\s*", "", text)
    return text.strip().strip("`").strip()


def _match_heading(line):
    """把一行开头识别成精确小节标题，返回 (key, 同行剩余内容)。"""
    text = (line or "").strip().lstrip("-*").strip()
    match = COMBINED_HEADING_RE.match(text)
    if match:
        return "college_major", match.group(1).strip()
    for alias, key in HEADING_ALIASES:
        if not text.startswith(alias):
            continue
        rest = text[len(alias):]
        # 标题后面必须是结尾、空白、冒号或括号，避免「课程名称」被当成「课程」。
        if rest and rest[0] not in " \t:：（(":
            continue
        rest = re.sub(r"^\s*[（(][^）)]*[）)]\s*", "", rest)
        rest = rest.lstrip(":： \t").strip()
        return key, rest
    return None


def sections(body):
    """按 `###` 切分并精确匹配标题。

    兼容标题和内容挤在同一行、以及标题自己带「（必填）」说明的模板。
    未识别的小节（例如「备注」）直接忽略。
    """
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"#{2,4}\s*", text)
    out = {}
    for chunk in chunks[1:]:
        first, _, rest = chunk.partition("\n")
        matched = _match_heading(first)
        if not matched:
            continue
        key, inline = matched
        content = inline
        if rest:
            content = (content + "\n" + rest) if content else rest
        out[key] = out.get(key, "") + content.strip("\n") + "\n"
    return out


def first_line(text):
    """取小节里的第一个有效值，跳过模板说明文字。"""
    for line in (text or "").split("\n"):
        line = _clean(line.lstrip("-*").strip())
        if not line:
            continue
        if line.startswith(">") or "例如" in line or "照着" in line:
            continue
        if line.startswith("把文件") or line.startswith("（必填"):
            continue
        return line
    return ""


def _labeled_values(block):
    """解析「开课学院：xxx / 所属专业：yyy」，支持两个字段挤在同一行。"""
    values = {}
    matches = list(
        re.finditer(r"(开课学院|所属专业)\s*[:：]\s*", block or "")
    )
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
        raw = block[match.end():end]
        value = first_line(raw)
        value = re.sub(r"\s*[-*]\s*$", "", value).strip()
        if value and match.group(1) not in values:
            values[match.group(1)] = value
    return values


def _plain_value(block):
    for line in (block or "").split("\n"):
        value = _clean(line.lstrip("-*").strip())
        if not value:
            continue
        if re.match(r"^(开课学院|所属专业)\s*[:：]", value):
            continue
        if "例如" in value or "照着" in value or value.startswith("把文件"):
            continue
        return value
    return ""


def _is_file_link(url):
    if "/issues/attach_files/" in url or "/uploads/" in url:
        return True
    return bool(FILE_EXT_RE.search(url))


def parse_body(body):
    s = sections(body)

    course_raw = first_line(s.get("course", ""))
    year_raw = first_line(s.get("year", ""))
    type_block = s.get("type", "")

    combined = s.get("college_major", "")
    combined_values = _labeled_values(combined)

    college_block = s.get("college", "")
    major_block = s.get("major", "")
    college = _labeled_values(college_block).get("开课学院", "")
    major = _labeled_values(major_block).get("所属专业", "")
    if not college:
        college = combined_values.get("开课学院", "")
    if not major:
        major = combined_values.get("所属专业", "")
    if not college:
        college = _plain_value(college_block) or _plain_value(combined)
    if not major:
        major = _plain_value(major_block) or _plain_value(combined)

    # 附件链接从整篇里找，比只在小节里找更稳（用户常把文件拖到别处）。
    links = []
    seen = set()
    for entry in imp._parse_attachments(body):
        url = entry["url"]
        if not _is_file_link(url) or url in seen:
            continue
        seen.add(url)
        links.append(url)

    confirm_block = s.get("confirm", "")
    confirmed = False
    confirm_re = re.compile(
        r"^\s*[-*]\s*\[[xX]\]\s*"
        + re.escape(imp.CONFIRM_REQUIREMENT)
        + r"\s*$"
    )
    for line in confirm_block.split("\n"):
        if confirm_re.match(line):
            confirmed = True
            break

    return {
        "course_raw": course_raw,
        "college": college,
        "major": major,
        "year_raw": year_raw,
        "type_raw": type_block.strip(),
        "links": links,
        "confirmed": confirmed,
    }


def pick_type(type_block):
    """资料类型必须整行完全等于六个允许值之一。"""
    lines = []
    for raw in (type_block or "").split("\n"):
        value = _clean(raw.lstrip("-*").strip())
        if value:
            lines.append(value)

    if not lines:
        imp._fail("「资料类型」为空，请只保留一个类型（笔记/课件/试卷/作业/实验报告/复习资料）")

    # 模板说明里那行「笔记 / 课件 / ...」会同时命中多个值。
    # 如果用户已经在别的行明确写了唯一类型，以明确值为准；只有找不到
    # 唯一整行值时，才把「多个类型」当成错误，绝不默认成笔记。
    exact = [line for line in lines if line in MATERIAL_TYPES]
    if exact:
        unique = list(dict.fromkeys(exact))
        if len(unique) == 1:
            return unique[0]
        imp._fail("「资料类型」填写了多个类型，请只保留一个类型。")

    for line in lines:
        hits = [item for item in MATERIAL_TYPES if item in line]
        if len(hits) > 1:
            imp._fail(
                "「资料类型」里出现了多个类型（{}），请只保留一个类型。".format(
                    "、".join(hits)
                )
            )

    imp._fail(
        "资料类型「{}」不在允许范围内（笔记/课件/试卷/作业/实验报告/复习资料）".format(
            lines[0]
        )
    )


# --------------------------------------------------------------------------
# 课程定位
# --------------------------------------------------------------------------

def resolve_course(college, major, course_raw):
    """返回 (college, major, course, target_dir)。"""
    if not college and not major:
        name = imp._validate_component(course_raw, "课程名称")
        target = os.path.join(COURSES, "公共课", name)
        if not os.path.isdir(target):
            imp._fail("公共课里没有「{}」这个课程目录".format(name))
        return "公共课", "", name, target

    college = imp._validate_component(college, "开课学院")
    major = imp._validate_component(major, "所属专业")
    base = os.path.join(COURSES, "专业课", college, major)
    if not os.path.isdir(base):
        imp._fail("找不到目录：专业课/{}/{}".format(college, major))
    course = imp._resolve_course_value(course_raw, college, major)
    target = os.path.join(base, course)
    if not os.path.isdir(target):
        imp._fail("{} 专业下没有「{}」这门课的目录".format(major, course))
    return college, major, course, target


# --------------------------------------------------------------------------
# 附件下载（SSRF 防护 + 体积限制）
# --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """让 urllib 不自动跟随 3xx，改由 _open_checked 逐跳校验。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())


def _host_allowed(host):
    host = (host or "").lower().rstrip(".")
    return any(
        host == allowed or host.endswith("." + allowed)
        for allowed in ALLOWED_ATTACHMENT_HOSTS
    )


def _check_public_ip(host):
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        imp._fail("无法解析附件域名 {}：{}".format(host, exc))
    addresses = set()
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            imp._fail("附件域名 {} 解析出非法地址，已拒绝".format(host))
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global
        ):
            imp._fail(
                "附件域名 {} 解析到内网/保留地址（{}），已拒绝".format(host, ip)
            )
        addresses.add(str(ip))
    if not addresses:
        imp._fail("附件域名 {} 没有解析到可用地址，已拒绝".format(host))


def _validate_url(url):
    """只允许 Gitee 官方 HTTPS 域名，且解析后的 IP 必须是公网地址。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        imp._fail("附件链接格式非法，已拒绝")
    if parsed.scheme.lower() != "https":
        imp._fail("附件链接必须是 HTTPS（拒绝 http）")
    if parsed.username is not None or parsed.password is not None:
        imp._fail("附件链接不允许携带用户名/密码")
    try:
        port = parsed.port
    except ValueError:
        imp._fail("附件链接端口非法，已拒绝")
    if port not in (None, 443):
        imp._fail("附件链接只允许 443 端口")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or not _host_allowed(host):
        imp._fail(
            "附件域名「{}」不在允许范围内（只允许 gitee.com / "
            "images.gitee.com / giteeusercontent.com 及其子域）".format(host or "?")
        )
    _check_public_ip(host)
    return parsed


def _open_checked(url, timeout):
    """手动跟随重定向，每一跳都重新做 host/HTTPS/IP 校验。"""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _check_deadline()
        _validate_url(current)
        req = urllib.request.Request(
            current,
            headers={
                "User-Agent": "xiaoxia-cv",
                "Accept": "application/octet-stream",
            },
        )
        try:
            response = _NO_REDIRECT_OPENER.open(req, timeout=timeout)
            final_url = response.geturl()
            _validate_url(final_url)
            return response
        except urllib.error.HTTPError as exc:
            if exc.code in REDIRECT_CODES:
                location = exc.headers.get("Location")
                exc.close()
                if not location:
                    imp._fail("附件重定向缺少 Location，已拒绝")
                next_url = urllib.parse.urljoin(current, location)
                _validate_url(next_url)
                current = next_url
                continue
            exc.close()
            imp._fail("下载附件失败（HTTP {}）".format(exc.code))
        except urllib.error.URLError as exc:
            imp._fail("下载附件失败（网络错误）：{}".format(exc.reason))
        except TimeoutError:
            imp._fail("下载附件超时")
    imp._fail("附件重定向次数超过 {} 次，已拒绝".format(MAX_REDIRECTS))


def download(url, dest, limit=None, remaining=None):
    """下载到 dest；返回 (字节数, sha256)。失败时删除半成品。

    limit/remaining 省略时使用 import_issue.py 的默认上限，方便单独调用。
    """
    if limit is None:
        limit = imp.MAX_ATTACHMENT_BYTES
    if remaining is None:
        remaining = imp.MAX_TOTAL_BYTES
    try:
        digest = hashlib.sha256()
        total = 0
        response = _open_checked(url, timeout=120)
        try:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    announced = int(content_length)
                except (TypeError, ValueError):
                    announced = None
                if announced is not None and announced > limit:
                    imp._fail(
                        "附件超过单文件上限（{} > {}）".format(
                            imp._human_size(announced), imp._human_size(limit)
                        )
                    )
                if announced is not None and announced > remaining:
                    imp._fail(
                        "附件总大小超过上限（{} > {}）".format(
                            imp._human_size(announced), imp._human_size(remaining)
                        )
                    )
            with open(dest, "wb") as handle:
                while True:
                    _check_deadline()
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        imp._fail(
                            "附件超过单文件上限（{}）".format(
                                imp._human_size(limit)
                            )
                        )
                    if total > remaining:
                        imp._fail(
                            "附件总大小超过上限（{}）".format(
                                imp._human_size(imp.MAX_TOTAL_BYTES)
                            )
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        finally:
            response.close()
        if total == 0:
            imp._fail("附件是空文件，已拒绝入库")
        return total, digest.hexdigest()
    except BaseException:
        try:
            if os.path.exists(dest):
                os.unlink(dest)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# 归档
# --------------------------------------------------------------------------

def _rollback(paths):
    for path in paths:
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass


def _pr_url(stdout):
    matches = re.findall(r"(?m)^\s*PR\s+(\d+)\s+(\S+)\s*$", stdout)
    if matches:
        return matches[-1][1]
    match = re.search(r"\bPR\s+(\d+)\s+(https?://\S+)", stdout)
    return match.group(2) if match else ""


def process(issue, state):  # noqa: ARG001
    number = issue["number"]
    title = issue.get("title", "")
    body = issue.get("body") or ""
    form = parse_body(body)

    # dry-run：只打印解析结果，不下载、不落盘、不写 state、不发评论。
    if DRY:
        print(
            "DRY Issue #{} 解析结果：{}".format(
                number, json.dumps(form, ensure_ascii=False)
            )
        )
        return {"saved": [], "skipped": [], "pr_url": ""}

    if not form["confirmed"]:
        imp._fail("「确认」那一项没有勾选指定承诺，请勾选后再打 approved")
    if not form["links"]:
        imp._fail("没有在附件里找到文件链接（把文件拖进 Issue 正文后会自动生成）")

    material_type = pick_type(form["type_raw"])
    year = imp._year_term(form["year_raw"], body)
    college, major, course, target = resolve_course(
        form["college"], form["major"], form["course_raw"]
    )

    links = form["links"]
    if len(links) > imp.MAX_ATTACHMENTS:
        imp._fail(
            "附件数量 {} 超过上限 {} 个".format(len(links), imp.MAX_ATTACHMENTS)
        )

    type_dir = imp.TYPE_TO_DIR[material_type]
    type_word = imp.TYPE_TO_FILENAME[material_type]
    target_dir = os.path.join(target, type_dir)

    stage = os.path.join(STAGE_ROOT, "gi_stage_{}".format(number))
    downloads = []
    total_bytes = 0
    try:
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage, mode=0o700)

        # 先全部下载到 staging，任何一个失败都不会污染课程目录。
        for index, url in enumerate(links, 1):
            _check_deadline()
            ext = imp._ext_from_url(url)
            if not ext:
                imp._fail("附件 #{} 无法从链接确定扩展名".format(index))
            if ext not in imp.ALLOWED_EXTENSIONS:
                imp._fail(
                    "附件扩展名 .{} 不允许（支持 {}）".format(
                        ext, "/".join(sorted(imp.ALLOWED_EXTENSIONS))
                    )
                )
            limit = imp.MAX_ZIP_BYTES if ext == "zip" else imp.MAX_ATTACHMENT_BYTES
            remaining = imp.MAX_TOTAL_BYTES - total_bytes
            if remaining <= 0:
                imp._fail(
                    "附件总大小超过上限（{}）".format(
                        imp._human_size(imp.MAX_TOTAL_BYTES)
                    )
                )
            temp_path = os.path.join(stage, "download-{}.{}".format(index, ext))
            size, digest = download(url, temp_path, limit, remaining)
            total_bytes += size
            downloads.append(
                {
                    "path": temp_path,
                    "ext": ext,
                    "size": size,
                    "digest": digest,
                    "index": index,
                }
            )

        os.makedirs(target_dir, exist_ok=True)
        by_size = imp._target_files_by_size(Path(target_dir))
        digest_cache = {}
        planned = {}
        reserved = set()
        saved = []
        skipped = []
        moved = []

        try:
            for item in downloads:
                _check_deadline()
                duplicate = imp._find_duplicate(
                    by_size, item["size"], item["digest"], planned, digest_cache
                )
                if duplicate:
                    print(
                        "SKIP_DUP Issue #{}: 附件 #{} 与 {} 内容相同".format(
                            number, item["index"], duplicate
                        )
                    )
                    skipped.append(duplicate)
                    continue

                base = "{}-{}-{}".format(year, type_word, course)
                if len(downloads) > 1:
                    base += "-{}".format(item["index"])
                name = imp._allocate_filename(
                    Path(target_dir), base, item["ext"], reserved
                )
                final = os.path.join(target_dir, name)
                imp._move_exclusive(Path(item["path"]), Path(final))
                moved.append(final)
                planned[item["digest"]] = name
                saved.append(os.path.relpath(final, REPO_ROOT))
        except BaseException:
            # 落盘中途失败也回滚，避免留下半套资料。
            _rollback(moved)
            raise

        if not saved:
            print("全部附件已存在，跳过 Issue #{}".format(number))
            return {"saved": [], "skipped": skipped, "pr_url": ""}

        message = "add: {} {} {}（来自 Gitee Issue #{}）".format(
            course, year, material_type, number
        )
        body_text = (
            "来自 Gitee Issue #{}「{}」\n\n"
            "- 课程：{}\n- 学院/专业：{}/{}\n- 年份学期：{}\n"
            "- 资料类型：{}\n"
        ).format(
            number,
            title,
            course,
            college or "公共课",
            major or "-",
            year,
            material_type,
        )

        try:
            proc = subprocess.run(
                [sys.executable, PUSH_SCRIPT, message, body_text],
                input="\n".join(saved) + "\n",
                text=True,
                capture_output=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            detail = ((exc.stdout or "") + (exc.stderr or ""))[-500:]
            _rollback(moved)
            imp._fail(
                "创建 PR 超时（600 秒）{}，已回滚本次落盘文件".format(
                    "：" + detail if detail else ""
                )
            )
        except Exception as exc:
            _rollback(moved)
            imp._fail("创建 PR 失败，已回滚本次落盘文件：{}".format(exc))

        if proc.returncode != 0:
            detail = (proc.stdout + proc.stderr)[-500:]
            _rollback(moved)
            imp._fail("创建 PR 失败：" + detail)

        pr_url = _pr_url(proc.stdout)
        print(pr_url or (proc.stdout.strip().splitlines() or [""])[-1])
        return {"saved": saved, "skipped": skipped, "pr_url": pr_url}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def comment(number, text):
    api(
        "/repos/{}/issues/{}/comments".format(OWNER_REPO, number),
        method="POST",
        data={"body": text},
    )


def _safe_comment(number, text):
    try:
        comment(number, text)
    except Exception as exc:  # 评论失败不能影响主流程
        print("评论 Issue #{} 失败：{}".format(number, exc))


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    global _RUN_START
    _RUN_START = time.monotonic()

    if not _acquire_lock():
        return 0

    previous_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(MAX_RUN_SECONDS)
    except (ValueError, OSError):
        previous_handler = None

    try:
        issues = list_issues()
        for issue in issues:
            _check_deadline()
            number = issue["number"]
            labels = [label["name"] for label in issue.get("labels", []) if isinstance(label, dict)]
            if "approved" not in labels:
                continue

            key = str(number)
            entry = load_state().get(key, {})
            if not isinstance(entry, dict):
                entry = {}
            if entry.get("status") == "done":
                continue
            attempts = int(entry.get("attempts", 0) or 0)
            if entry.get("status") == "failed" and attempts >= MAX_ATTEMPTS:
                print(
                    "跳过 Issue #{}（已失败 {} 次，等待人工处理）".format(
                        number, attempts
                    )
                )
                continue

            print("处理 Issue #{}".format(number))

            if DRY:
                process(issue, entry)
                continue

            try:
                result = process(issue, entry)
                update_state(
                    number,
                    status="done",
                    files=result["saved"],
                    pr_url=result.get("pr_url", ""),
                    last_error="",
                )
                if result["saved"]:
                    text = "已归档并生成 PR"
                    if result.get("pr_url"):
                        text += "：{}".format(result["pr_url"])
                    text += "\n" + "\n".join(
                        "- `{}`".format(path) for path in result["saved"]
                    )
                    if result["skipped"]:
                        text += "\n\n内容相同已跳过：{}".format(
                            "、".join(result["skipped"])
                        )
                else:
                    text = "附件已在目标目录中存在，无需重复归档：{}".format(
                        "、".join(result["skipped"]) or "-"
                    )
                _safe_comment(number, text)
            except RunTimeout:
                raise
            except Exception as exc:
                current = load_state().get(key, {})
                if not isinstance(current, dict):
                    current = {}
                attempts = int(current.get("attempts", 0) or 0) + 1
                update_state(
                    number,
                    status="failed",
                    attempts=attempts,
                    last_error=str(exc)[:1000],
                )
                if attempts == 1:
                    text = (
                        "入库失败：{}\n\n"
                        "改好后请维护者移除 approved 再加回来重新触发。"
                    ).format(exc)
                    _safe_comment(number, text)
                elif attempts >= MAX_ATTEMPTS:
                    text = (
                        "入库失败（第 {} 次）：{}\n\n"
                        "已达到自动重试上限（{} 次），不再自动重试。"
                        "请修改后移除 approved，再重新加上触发。"
                    ).format(attempts, exc, MAX_ATTEMPTS)
                    _safe_comment(number, text)
                print("失败：", exc)
        return 0
    except RunTimeout as exc:
        print("运行超时，退出：{}".format(exc))
        return 0
    except Exception as exc:
        print("运行失败：{}".format(exc))
        return 1
    finally:
        try:
            signal.alarm(0)
            if previous_handler is not None:
                signal.signal(signal.SIGALRM, previous_handler)
        except (ValueError, OSError):
            pass
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())
