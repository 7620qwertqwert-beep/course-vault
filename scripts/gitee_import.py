#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Gitee 仓库里打了 approved 标签的 Issue 归档进本地 GitHub 工作副本。

Gitee 不支持 GitHub 的 Issue Forms（YAML 表单），所以 .gitee/ISSUE_TEMPLATE/*.md
生成的是「### 小节 + 自由文本」的 Markdown，这里按小节解析。
校验、年份归一、类型→目录、文件名规则全部复用 scripts/import_issue.py。
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

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


def api(path, params=None, method="GET", data=None):
    q = {"access_token": TOKEN}
    q.update(params or {})
    url = "{}{}?{}".format(GITEE_API, path, urllib.parse.urlencode(q))
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "xiaoxia-cv"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode()
    return json.loads(text) if text.strip() else {}


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


KEYS = ("课程", "开课学院", "所属专业", "年份学期", "资料类型", "附件", "确认", "备注")


def sections(body):
    """把模板的 '### 小节' 拆成 {关键词: 内容}。

    两种脏数据都要吃得下：小节标题和内容挤在同一行、用户没删说明文字。
    不依赖 '#' 出现在行首，也不依赖换行完整。
    """
    text = re.sub(r"#{2,4}\s*", "\n", body)
    out = {}
    cur = None
    for line in text.split("\n"):
        t = line.strip().lstrip("-*").strip()
        matched = None
        for k in KEYS:
            if t.startswith(k):
                matched = k
                break
        if matched:
            cur = matched
            out[cur] = t[len(matched):].lstrip(":：").strip() + "\n"
            continue
        if cur is not None:
            out[cur] += line + "\n"
    return out


def sec(sects, *keys):
    for k in keys:
        if k in sects:
            return sects[k]
    return ""


def clean(text):
    """去掉「（必填）」「（必填，六选一）」这类占位前缀与反引号。"""
    text = re.sub(r"^[（(][^）)]*[）)]\s*", "", text.strip())
    return text.strip().strip("`").strip()


def first_line(text):
    for line in text.split("\n"):
        line = clean(line.lstrip("-*").strip())
        if not line:
            continue
        if line.startswith(">") or "例如" in line or "照着" in line or line.startswith("把文件"):
            continue
        return line
    return ""


def grab(text, label):
    m = re.search(r"%s\s*[:：]\s*([^\n\-*]+)" % re.escape(label), text)
    return clean(m.group(1)) if m else ""


def parse_body(body):
    s = sections(body)

    course_raw = first_line(sec(s, "课程"))
    year_raw = first_line(sec(s, "年份学期"))
    type_raw = first_line(sec(s, "资料类型"))

    college_block = sec(s, "开课学院")
    major_block = sec(s, "所属专业")
    college = grab(college_block, "开课学院") or grab(major_block, "开课学院")
    major = grab(major_block, "所属专业") or grab(college_block, "所属专业")
    if not college:
        college = first_line(college_block)
    if not major:
        major = first_line(major_block)

    # 附件链接从整篇里找，比只在小节里找更稳（用户常把文件拖到别处）。
    links = re.findall(r"\((https?://[^)\s]+)\)", body)
    links += re.findall(r"(?<![(\w])(https?://\S+?/attach_files/\S+)", body)
    seen, uniq = set(), []
    for u in links:
        if u not in seen:
            seen.add(u)
            uniq.append(u)

    confirm_block = sec(s, "确认")
    return {
        "course_raw": course_raw,
        "college": college,
        "major": major,
        "year_raw": year_raw,
        "type_raw": type_raw,
        "links": uniq,
        "confirmed": bool(re.search(r"-\s*\[[xX]\]", confirm_block)) or bool(re.search(r"-\s*\[[xX]\]", body)),
    }


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


def pick_type(type_raw):
    raw = type_raw.strip()
    for key in imp.TYPE_TO_DIR:
        if key in raw:
            return key
    imp._fail("资料类型「{}」不在允许范围内（笔记/课件/试卷/作业/实验报告/复习资料）".format(raw))


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "xiaoxia-cv"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:
        total = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > imp.MAX_FILE_BYTES:
                imp._fail("附件超过单文件 50MB 上限")
            fh.write(chunk)
    return total


def process(issue, state):
    number = issue["number"]
    title = issue.get("title", "")
    body = issue.get("body") or ""
    form = parse_body(body)

    if not form["confirmed"]:
        imp._fail("「确认」那一项没有勾选，请勾选后再打 approved")
    if not form["links"]:
        imp._fail("没有在「附件」小节里找到文件链接（把文件拖进 Issue 正文后会自动生成）")

    year = imp._year_term(form["year_raw"], body)
    material_type = pick_type(form["type_raw"])
    college, major, course, target = resolve_course(form["college"], form["major"], form["course_raw"])

    type_dir = imp.TYPE_TO_DIR[material_type]
    type_word = imp.TYPE_TO_FILENAME[material_type]
    target_dir = os.path.join(target, type_dir)
    os.makedirs(target_dir, exist_ok=True)

    reserved = set()
    saved = []
    links = [u for u in form["links"] if "/issues/attach_files/" in u or "/uploads/" in u or re.search(r"\.(pdf|docx?|pptx?|xlsx?|md|txt|jpe?g|png|zip)$", u, re.I)]
    if not links:
        imp._fail("附件链接里没有一个像文件地址的链接，请确认文件是拖进正文上传的")

    for i, url in enumerate(links, 1):
        ext = re.sub(r"^.*\.", "", url.split("?")[0]).lower()
        if ext not in imp.ALLOWED_EXTENSIONS:
            imp._fail("附件扩展名 .{} 不允许（支持 {}）".format(ext, "/".join(sorted(imp.ALLOWED_EXTENSIONS))))
        base = "{}-{}-{}".format(year, type_word, course)
        if len(links) > 1:
            base += "-{}".format(i)
        name = imp._allocate_filename(__import__("pathlib").Path(target_dir), base, ext, reserved)
        tmp = os.path.join("/tmp", "gitee_dl_" + str(number) + "_" + name)
        download(url, tmp)
        final = os.path.join(target_dir, name)
        os.replace(tmp, final)
        saved.append(os.path.relpath(final, REPO_ROOT))

    msg = "add: {} {} {}（来自 Gitee Issue #{}）".format(course, year, material_type, number)
    body_text = ("来自 Gitee Issue #{}「{}」\n\n"
                 "- 课程：{}\n- 学院/专业：{}/{}\n- 年份学期：{}\n- 资料类型：{}\n").format(
        number, title, course, college or "公共课", major or "-", year, material_type)

    if DRY:
        print("DRY", number, saved)
        return saved

    proc = subprocess.run([sys.executable, PUSH_SCRIPT, msg, body_text],
                          input="\n".join(saved) + "\n", text=True,
                          capture_output=True)
    if proc.returncode != 0:
        imp._fail("创建 PR 失败：" + (proc.stdout + proc.stderr)[-500:])
    print(proc.stdout.strip().splitlines()[-1])
    return saved


def comment(number, text):
    if DRY:
        print("DRY comment", number, text[:60])
        return
    api("/repos/{}/issues/{}/comments".format(OWNER_REPO, number), method="POST", data={"body": text})


def main():
    state = load_state()
    issues = api("/repos/{}/issues".format(OWNER_REPO), {"state": "open", "per_page": 50})
    for issue in issues:
        number = issue["number"]
        labels = [l["name"] for l in issue.get("labels", [])]
        key = str(number)
        if state.get(key, {}).get("status") == "done":
            continue
        if "approved" not in labels:
            continue
        print("处理 Issue #{}".format(number))
        try:
            saved = process(issue, state)
            state[key] = {"status": "done", "files": saved}
            save_state(state)
            comment(number, "已归档并生成 PR：\n" + "\n".join("- `{}`".format(x) for x in saved))
        except imp.ImportFailure as exc:
            state[key] = {"status": "failed", "reason": str(exc)}
            save_state(state)
            comment(number, "入库失败：{}\n\n改好后请维护者移除 approved 再加回来重新触发。".format(exc))
            print("失败：", exc)
        except Exception as exc:  # 网络等偶发错误，不写 failed，下次重试
            print("异常（下次重试）：", exc)


if __name__ == "__main__":
    main()