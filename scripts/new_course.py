#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互式创建课程目录与课程卡片。

用法：
    python3 scripts/new_course.py
    python3 scripts/new_course.py --force

课程卡片的字段结构对齐 _template/README.md；本脚本只使用 Python 标准库。
"""

import argparse
import sys
from pathlib import Path

# 脚本位于仓库的 scripts/ 目录下，向上一级就是仓库根目录。
REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_DIR = REPO_ROOT / "courses"

# 课程类别与每门课固定创建的四个资料子目录。
CATEGORIES = ("公共课", "专业课")
SUBDIRS = ("notes", "slides", "exams", "homework")

# 课程卡片中需要向用户询问的字段顺序。
FIELD_PROMPTS = (
    ("课程名", "课程名："),
    ("开课学院", "开课学院："),
    ("学分学时", "学分学时（例：5 学分 / 80 学时）："),
    ("授课教师", "授课教师（只写姓 + 老师即可）："),
    ("开课学期", "开课学期（例：2025 秋）："),
    ("教材", "教材（书名 · 作者 · 版次 · ISBN）："),
    ("考核方式", "考核方式（例：平时 30% + 期末 70%，闭卷）："),
    ("难度星级", "难度星级（例：★★★★☆）："),
    ("避坑点", "这门课最容易踩的坑："),
)


def parse_args(argv=None):
    """解析命令行参数；--help 由 argparse 自动处理。"""
    parser = argparse.ArgumentParser(
        prog="new_course.py",
        description="交互式新建课程目录，并生成课程卡片 README.md（零第三方依赖）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 scripts/new_course.py\n"
            "  python3 scripts/new_course.py --force\n\n"
            "说明：\n"
            "  运行后按提示依次填写课程信息，非必填项可直接回车跳过。\n"
            "  课程目录已存在时，必须加 --force 才会覆盖课程卡片。\n"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="课程目录已存在时覆盖 README.md（不会删除 notes/slides/exams/homework 中的资料）",
    )
    return parser.parse_args(argv)


def _clean_text(value):
    """把输入整理成适合写入 Markdown 的单行文本。"""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(value.split("\n")).strip()


def _cell(value):
    """转义表格单元格里的竖线，避免破坏表格的列结构。"""
    return _clean_text(value).replace("|", "\\|")


def _ask(prompt, required=False):
    """读取一项输入；必填项为空时重新询问，非必填项返回空字符串。"""
    while True:
        try:
            raw = input(prompt)
        except EOFError:
            print("\n错误：输入意外结束，已取消创建。", file=sys.stderr)
            raise SystemExit(1)
        except KeyboardInterrupt:
            print("\n已取消创建。", file=sys.stderr)
            raise SystemExit(130)

        value = raw.strip()
        if value:
            return value
        if not required:
            return ""
        print("该项不能为空，请重新输入。")


def _validate_course_name(name):
    """检查课程名是否为空、是否包含路径分隔符或控制字符。"""
    if not name:
        return "课程名不能为空"
    if name in (".", ".."):
        return "课程名不能是 . 或 .."
    if any(ch in name for ch in ("/", "\\", "\x00")):
        return "课程名不能包含路径分隔符"
    if any(ord(ch) < 32 for ch in name):
        return "课程名不能包含控制字符"
    return None


def _ask_course_name():
    """询问课程名，并校验它能否安全地作为目录名。"""
    while True:
        name = _ask("课程名：", required=True)
        error = _validate_course_name(name)
        if error is None:
            return name
        print(error + "，请重新输入。")


def _ask_category():
    """询问课程类别，只接受“公共课”或“专业课”。"""
    while True:
        value = _ask("类别（公共课/专业课）：", required=True)
        if value in CATEGORIES:
            return value
        # 顺手支持输入序号，减少手机端输入成本。
        if value == "1":
            return "公共课"
        if value == "2":
            return "专业课"
        print("类别只能是“公共课”或“专业课”，请重新输入。")


def _render_readme(data):
    """按 _template/README.md 的字段结构生成课程卡片内容。"""
    course_name = _clean_text(data["课程名"])

    # 未填写的值保持为空，方便后来者继续补充。
    lines = [
        "# " + course_name,
        "",
        "## 基本信息",
        "",
        "| 项 | 内容 |",
        "|---|---|",
        "| 课程名称 | {} |".format(_cell(course_name)),
        "| 开课学院 | {} |".format(_cell(data["开课学院"])),
        "| 学分 / 学时 | {} |".format(_cell(data["学分学时"])),
        "| 授课教师 | {} |".format(_cell(data["授课教师"])),
        "| 开课学期 | {} |".format(_cell(data["开课学期"])),
        "| 教材 | {} |".format(_cell(data["教材"])),
        "| 考核方式 | {} |".format(_cell(data["考核方式"])),
        "",
        "## 难度与避坑（最重要的一栏）",
        "",
        "- 难度打分：{}".format(_clean_text(data["难度星级"])),
        "- 挂科率体感：",
        "- 点名/签到频率：",
        "- 作业量：",
        "- 这门课最容易踩的坑：{}".format(_clean_text(data["避坑点"])),
        "",
        "## 复习建议",
        "",
        "- 期末重点章节：",
        "- 老师出题习惯（爱考计算/爱考概念/爱出原题）：",
        "- 建议复习时长：",
        "- 最有用的一份资料是：",
        "",
        "## 已归档资料",
        "",
        "| 类型 | 年份学期 | 文件名 | 备注 |",
        "|---|---|---|---|",
        "| 期末 | | | |",
        "| 笔记 | | | |",
        "| 课件 | | | |",
        "",
        "## 上传者留言",
        "",
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _collect_course_data(course_name):
    """依次询问课程卡片剩余字段，返回字段到内容的映射。"""
    print("\n请继续填写课程卡片，非必填项可直接回车跳过：")
    data = {"课程名": course_name}
    for key, prompt in FIELD_PROMPTS[1:]:
        data[key] = _ask(prompt)
    return data


def main(argv=None):
    """程序入口：询问信息、创建目录、写入 README.md。"""
    args = parse_args(argv)

    print("== 新建课程 ==")
    print("提示：除课程名和类别外，其余各项都可以直接回车跳过。\n")

    # 先问课程名和类别，这样才能确定目标目录。
    course_name = _ask_course_name()
    category = _ask_category()
    course_dir = COURSES_DIR / category / course_name

    # 已存在的课程目录默认不覆盖，避免误伤别人已经上传的资料。
    if course_dir.exists() and not course_dir.is_dir():
        print(
            "错误：目标路径已存在且不是目录：{}".format(course_dir),
            file=sys.stderr,
        )
        return 1
    if course_dir.exists() and not args.force:
        print(
            "错误：课程目录已存在：{}".format(course_dir),
            file=sys.stderr,
        )
        print(
            "如需覆盖课程卡片，请加 --force 参数后重试"
            "（已有资料文件不会被删除）。",
            file=sys.stderr,
        )
        return 1
    if course_dir.exists():
        print("提示：课程目录已存在，将覆盖 README.md：{}".format(course_dir))

    data = _collect_course_data(course_name)

    try:
        # 四个资料子目录：已存在时直接复用，不删除任何已有文件。
        for subdir in SUBDIRS:
            (course_dir / subdir).mkdir(parents=True, exist_ok=True)

        readme_path = course_dir / "README.md"
        readme_path.write_text(_render_readme(data), encoding="utf-8")
    except OSError as exc:
        print("错误：创建课程目录或写入课程卡片失败：{}".format(exc), file=sys.stderr)
        return 1

    print(
        "\n✅ 课程「{}」创建成功，课程卡片：{}".format(course_name, readme_path)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
