#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描课程目录并生成 INDEX.md。

用法：
    python3 scripts/build_index.py          # 重新生成 INDEX.md
    python3 scripts/build_index.py --check  # 只校验，不写文件（供 CI 使用）

只使用 Python 3 标准库，零第三方依赖。
"""

import argparse
import difflib
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 脚本位于仓库的 scripts/ 目录下，向上一级就是仓库根目录。
REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_DIR = REPO_ROOT / "courses"
PUBLIC_DIR = COURSES_DIR / "公共课"
MAJOR_DIR = COURSES_DIR / "专业课"
INDEX_PATH = REPO_ROOT / "INDEX.md"

SUBDIRS = ("笔记", "课件", "试卷", "作业")
EMPTY = "—"

STATUS_PENDING = "⬜ 待填"
STATUS_FEW = "🚧 少量"
STATUS_READY = "✅ 可用"

# 自动生成提示行；--check 会复用旧文件里的时间，避免每次校验都因时间不同而失败。
STAMP_RE = re.compile(
    r"^> 🤖 自动生成时间：(.+?) ｜ 总课程数：\d+\s*$", re.MULTILINE
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="build_index.py",
        description="扫描 courses/ 下的真实课程目录，生成或校验 INDEX.md。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只在内存生成并与现有 INDEX.md 比对；不一致时 exit 1",
    )
    return parser.parse_args(argv)


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _clean_field(value):
    """清理 README 字段值；空值和“待补充”视为未填写。"""
    value = " ".join((value or "").split())
    if not value or value in (EMPTY, "-", "--", "待补充"):
        return EMPTY
    return value


def _parse_table_field(text, field):
    """解析课程 README 基本信息表格中的一行。"""
    pattern = re.compile(
        r"^\|\s*" + re.escape(field) + r"\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return EMPTY
    return _clean_field(match.group(1).replace("\\|", "|"))


def _parse_difficulty(text):
    """解析“难度打分”：兼容表格行与列表行两种写法。"""
    table_match = re.search(
        r"^\|\s*难度打分\s*\|\s*(.*?)\s*\|\s*$", text, re.MULTILINE
    )
    if table_match:
        return _clean_field(table_match.group(1).replace("\\|", "|"))

    bullet_match = re.search(
        r"^\s*[-*]\s*难度打分\s*[：:]\s*(.*?)\s*$", text, re.MULTILINE
    )
    if bullet_match:
        return _clean_field(bullet_match.group(1))
    return EMPTY


def _count_materials(course_dir):
    """统计四个资料目录中除 .gitkeep 外的文件总数（含子目录）。"""
    total = 0
    for subdir in SUBDIRS:
        directory = course_dir / subdir
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.name != ".gitkeep":
                total += 1
    return total


def _status(count):
    if count == 0:
        return STATUS_PENDING
    if count <= 2:
        return STATUS_FEW
    return STATUS_READY


def _cell(value):
    """转义 Markdown 表格中的竖线与换行。"""
    return " ".join(str(value).split()).replace("|", "\\|")


def _load_course(course_dir):
    """读取一门课程的展示信息。"""
    readme = _read_text(course_dir / "README.md")
    count = _count_materials(course_dir)
    link = course_dir.relative_to(REPO_ROOT).as_posix().rstrip("/") + "/"
    return {
        "name": course_dir.name,
        "nature": _parse_table_field(readme, "课程性质"),
        "difficulty": _parse_difficulty(readme),
        "count": count,
        "status": _status(count),
        "link": link,
    }


def _sort_key(name):
    """中文名称按 Unicode 码位排序，跨环境稳定可复现。"""
    return name


def _sort_courses(courses):
    return sorted(courses, key=lambda item: _sort_key(item["name"]))


def _table(courses):
    lines = [
        "| 课程 | 性质 | 难度 | 资料数 | 状态 |",
        "|---|---|---|---|---|",
    ]
    for course in _sort_courses(courses):
        lines.append(
            "| [{}]({}) | {} | {} | {} | {} |".format(
                _cell(course["name"]),
                course["link"],
                _cell(course["nature"]),
                _cell(course["difficulty"]),
                course["count"],
                course["status"],
            )
        )
    return lines


def _public_courses():
    if not PUBLIC_DIR.is_dir():
        return []
    return [
        path
        for path in PUBLIC_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]


def _major_courses():
    """返回 [(学院, 专业, [课程目录...]), ...]，已按学院、专业排序。"""
    if not MAJOR_DIR.is_dir():
        return []

    result = []
    colleges = sorted(
        (
            path
            for path in MAJOR_DIR.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ),
        key=lambda path: _sort_key(path.name),
    )
    for college_dir in colleges:
        majors = sorted(
            (
                path
                for path in college_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ),
            key=lambda path: _sort_key(path.name),
        )
        for major_dir in majors:
            courses = [
                path
                for path in major_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ]
            if courses:
                result.append((college_dir.name, major_dir.name, courses))
    return result


def build_index(generated_at):
    """构建 INDEX.md 文本，并返回 (文本, 总课程数, 有资料课程数)。"""
    public = [_load_course(path) for path in _public_courses()]
    majors = [
        (college, major, [_load_course(path) for path in courses])
        for college, major, courses in _major_courses()
    ]

    total = len(public) + sum(len(courses) for _, _, courses in majors)
    with_materials = sum(1 for course in public if course["count"] > 0)
    with_materials += sum(
        1
        for _, _, courses in majors
        for course in courses
        if course["count"] > 0
    )

    lines = [
        "# 课程索引 · Index",
        "",
        "课程资料库全部课程导航；本文件由 `scripts/build_index.py` 自动生成，请勿手工编辑。",
        "",
        "> 🤖 自动生成时间：{} ｜ 总课程数：{}".format(generated_at, total),
        "",
        "## 公共课",
        "",
    ]
    lines.extend(_table(public))

    lines.extend(["", "## 专业课", ""])
    for college, major, courses in majors:
        lines.append("### {} · {}".format(college, major))
        lines.append("")
        lines.extend(_table(courses))
        lines.append("")

    lines.extend(
        [
            "## 图例",
            "",
            "- {}：资料数为 0，目录已建好，等待认领。".format(STATUS_PENDING),
            "- {}：资料数为 1–2，已有少量资料。".format(STATUS_FEW),
            "- {}：资料数为 3 及以上，可满足基本复习。".format(STATUS_READY),
        ]
    )

    return "\n".join(lines).rstrip("\n") + "\n", total, with_materials


def _extract_stamp(text):
    match = STAMP_RE.search(text)
    return match.group(1).strip() if match else None


def _atomic_write(path, text):
    fd, tmp_name = tempfile.mkstemp(
        prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _print_diff(existing, generated):
    """打印差异概要，避免 CI 日志被完整 diff 淹没。"""
    old_lines = existing.splitlines()
    new_lines = generated.splitlines()
    removed = added = changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines, autojunk=False
    ).get_opcodes():
        if tag == "delete":
            removed += i2 - i1
        elif tag == "insert":
            added += j2 - j1
        elif tag == "replace":
            changed += max(i2 - i1, j2 - j1)

    print("差异概要：现有 INDEX.md 与自动生成结果不一致。", file=sys.stderr)
    print(
        "  现有 {} 行，自动生成 {} 行；删除 {} 行，新增 {} 行，修改 {} 行。".format(
            len(old_lines), len(new_lines), removed, added, changed
        ),
        file=sys.stderr,
    )
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="INDEX.md",
            tofile="自动生成 INDEX.md",
            lineterm="",
            n=1,
        )
    )
    limit = 80
    for line in diff[:limit]:
        print(line, file=sys.stderr)
    if len(diff) > limit:
        print("...（仅显示前 {} 行差异）".format(limit), file=sys.stderr)


def main(argv=None):
    args = parse_args(argv)
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    if args.check:
        if not INDEX_PATH.is_file():
            print(
                "错误：INDEX.md 不存在，请先运行 python3 scripts/build_index.py",
                file=sys.stderr,
            )
            return 1
        existing = _read_text(INDEX_PATH)
        # 复用旧文件中的生成时间，使 --check 只关注内容变化。
        stamp = _extract_stamp(existing) or now
        generated, _total, _with_materials = build_index(stamp)
        if existing == generated:
            print("[OK] INDEX.md 与自动生成结果一致。")
            return 0
        _print_diff(existing, generated)
        return 1

    generated, total, with_materials = build_index(now)
    try:
        _atomic_write(INDEX_PATH, generated)
    except OSError as exc:
        print("错误：写入 INDEX.md 失败：{}".format(exc), file=sys.stderr)
        return 1

    print(
        "已生成 INDEX.md：共 {} 门课程，其中 {} 门有资料。".format(
            total, with_materials
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
