#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交互式创建课程目录与课程卡片。

用法：
    python3 scripts/new_course.py
    python3 scripts/new_course.py --force

课程卡片的字段结构对齐 _template/README.md；本脚本只使用 Python 标准库。
"""

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 脚本位于仓库的 scripts/ 目录下，向上一级就是仓库根目录。
REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_DIR = REPO_ROOT / "courses"

# 课程类别与每门课固定创建的四个资料子目录。
CATEGORIES = ("公共课", "专业课")
SUBDIRS = ("笔记", "课件", "试卷", "作业")

# 公共课的“所属专业”统一写为全校通用。
PUBLIC_MAJOR = "全校通用"

# Windows 不允许作为文件/目录名的保留名（大小写不敏感）。
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
} | {"COM{}".format(i) for i in range(1, 10)} | {
    "LPT{}".format(i) for i in range(1, 10)
}

# 课程卡片中除课程名、类别、学院、专业以外，需要向用户询问的字段顺序。
FIELD_PROMPTS = (
    ("课程性质", "课程性质（例：公共必修课 / 专业核心课 / 专业选修课）："),
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
            "  公共课目录为 courses/公共课/<课程>/；\n"
            "  专业课目录为 courses/专业课/<学院>/<专业>/<课程>/。\n"
            "  课程卡片已存在时，必须加 --force 才会覆盖；\n"
            "  若原卡片已由人工填写，还需输入大写 YES 二次确认，\n"
            "  覆盖前会自动备份为 README.md.bak-<时间戳>。\n"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的 README.md（不会删除 笔记/课件/试卷/作业 中的资料）",
    )
    return parser.parse_args(argv)


def _configure_stdio():
    """避免 GBK 等窄编码环境下因个别字符打印失败而崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _clean_text(value):
    """把输入整理成适合写入 Markdown 的单行文本。"""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(value.split("\n")).strip()


def _cell(value):
    """转义表格单元格里的竖线，避免破坏表格的列结构。"""
    return _clean_text(value).replace("|", "\\|")


def _field(value):
    """表格字段：空值写成“待补充”，方便后来者识别未填写内容。"""
    text = _cell(value)
    return text if text else "待补充"


def _todo(value):
    """正文中的空值同样写成“待补充”。"""
    text = _clean_text(value)
    return text if text else "待补充"


def _ask(prompt, required=False, strip=True):
    """读取一项输入；必填项为空时重新询问，非必填项返回空字符串。

    strip=False 时保留输入末尾的空格，交给目录名校验明确拒绝；
    仅用于课程名、专业名这类会变成目录名的字段。
    """
    while True:
        try:
            raw = input(prompt)
        except EOFError:
            print("\n错误：输入意外结束，已取消创建。", file=sys.stderr)
            raise SystemExit(1)
        except KeyboardInterrupt:
            print("\n已取消创建。", file=sys.stderr)
            raise SystemExit(130)

        value = raw.strip() if strip else raw
        if value.strip():
            return value
        if not required:
            return ""
        print("该项不能为空，请重新输入。")


def _validate_path_name(name, label):
    """检查名称能否安全地作为目录名使用。"""
    if not name:
        return "{}不能为空".format(label)
    if name in (".", ".."):
        return "{}不能是 . 或 ..".format(label)
    if any(ch in name for ch in ("/", "\\", "\x00")):
        return "{}不能包含路径分隔符".format(label)
    if any(ord(ch) < 32 for ch in name):
        return "{}不能包含控制字符".format(label)
    if name.endswith(".") or name.endswith(" "):
        return "{}不能以点或空格结尾".format(label)
    if len(name.encode("utf-8")) > 200:
        return "{}长度不能超过 200 字节（UTF-8）".format(label)

    # Windows 把 CON、CON.txt、COM1 等视为保留名；这里按第一个点前的部分判断。
    reserved_stem = name.split(".", 1)[0].upper()
    if reserved_stem in WINDOWS_RESERVED_NAMES:
        return "{}不能使用 Windows 保留名（如 CON、PRN、AUX、NUL、COM1、LPT1）".format(
            label
        )
    return None


def _ask_name(prompt, label, required=True):
    """询问一个会作为目录名使用的字段，并在输入后立即校验。"""
    while True:
        name = _ask(prompt, required=required, strip=False).lstrip()
        if not name and not required:
            return ""
        error = _validate_path_name(name, label)
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


def _find_course_dirs(course_name):
    """递归查找 courses/ 下所有同名课程目录，返回实际路径列表。"""
    found = []
    if not COURSES_DIR.is_dir():
        return found
    for root, dirnames, _filenames in os.walk(str(COURSES_DIR)):
        for dirname in dirnames:
            if dirname == course_name:
                found.append(Path(root) / dirname)
    return sorted(found)


def _render_readme(data):
    """按 _template/README.md 的字段结构生成课程卡片内容。"""
    course_name = _clean_text(data["课程名"])

    lines = [
        "# " + course_name,
        "",
        "## 基本信息",
        "",
        "| 项 | 内容 |",
        "|---|---|",
        "| 课程名称 | {} |".format(_field(course_name)),
        "| 所属专业 | {} |".format(_field(data["所属专业"])),
        "| 开课学院 | {} |".format(_field(data["开课学院"])),
        "| 课程性质 | {} |".format(_field(data["课程性质"])),
        "| 学分 / 学时 | {} |".format(_field(data["学分学时"])),
        "| 授课教师 | {} |".format(_field(data["授课教师"])),
        "| 开课学期 | {} |".format(_field(data["开课学期"])),
        "| 教材 | {} |".format(_field(data["教材"])),
        "| 考核方式 | {} |".format(_field(data["考核方式"])),
        "",
        "## 难度与避坑（最重要的一栏）",
        "",
        "- 难度打分：{}".format(_todo(data["难度星级"])),
        "- 挂科率体感：待补充",
        "- 点名/签到频率：待补充",
        "- 作业量：待补充",
        "- 这门课最容易踩的坑：{}".format(_todo(data["避坑点"])),
        "",
        "## 复习建议",
        "",
        "- 期末重点章节：待补充",
        "- 老师出题习惯（爱考计算/爱考概念/爱出原题）：待补充",
        "- 建议复习时长：待补充",
        "- 最有用的一份资料是：待补充",
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


def _collect_course_data(course_name, category, college, profession):
    """依次询问课程卡片其余字段，返回字段到内容的映射。"""
    print("\n请继续填写课程卡片，非必填项可直接回车跳过：")
    data = {
        "课程名": course_name,
        "所属专业": profession,
        "开课学院": college,
        "课程性质": "",
    }
    for key, prompt in FIELD_PROMPTS:
        data[key] = _ask(prompt)
    return data


def _confirm_overwrite(readme_path):
    """覆盖人工填写过的卡片前，要求输入大写 YES。"""
    try:
        content = readme_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(
            "错误：无法读取现有课程卡片，已取消：{}".format(exc),
            file=sys.stderr,
        )
        return False

    if "待补充" in content:
        return True

    print("\n警告：现有 README.md 中没有“待补充”字样，可能已被真人填写。")
    answer = _ask("确认覆盖请输入大写 YES，输入其他内容将放弃：")
    if answer == "YES":
        return True
    print("已放弃覆盖，未修改任何文件。")
    return False


def _backup_path(readme_path):
    """生成不冲突的 README.md.bak-<时间戳> 路径。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = readme_path.with_name("README.md.bak-" + stamp)
    index = 1
    while candidate.exists():
        candidate = readme_path.with_name(
            "README.md.bak-{}-{}".format(stamp, index)
        )
        index += 1
    return candidate


def _atomic_write_text(path, text):
    """先写同目录临时文件，再用 os.replace 原子替换目标文件。"""
    fd, tmp_name = tempfile.mkstemp(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
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


def main(argv=None):
    """程序入口：询问信息、创建目录、写入 README.md。"""
    _configure_stdio()
    args = parse_args(argv)

    print("== 新建课程 ==")
    print("提示：公共课只需课程名；专业课还需开课学院和所属专业。其余各项都可以直接回车跳过。\n")

    # 先问类别；专业课按“开课学院 → 所属专业 → 课程名”的顺序提问。
    category = _ask_category()
    if category == "专业课":
        college = _ask_name(
            "开课学院（专业课必填）：",
            "开课学院",
            required=True,
        )
        profession = _ask_name(
            "所属专业（专业课必填）：",
            "所属专业",
            required=True,
        )
        course_name = _ask_name("课程名：", "课程名")
        course_dir = COURSES_DIR / category / college / profession / course_name
    else:
        course_name = _ask_name("课程名：", "课程名")
        college = _ask("开课学院（可留空）：")
        profession = PUBLIC_MAJOR
        course_dir = COURSES_DIR / category / course_name

    # 递归查找同名课程目录，避免在错误的层级里再建一份重复目录。
    existing_dirs = _find_course_dirs(course_name)
    if existing_dirs:
        print("提示：已存在同名课程目录：")
        for existing in existing_dirs:
            print("  {}".format(existing))

    if course_dir.exists() and not course_dir.is_dir():
        print(
            "错误：目标路径已存在且不是目录：{}".format(course_dir),
            file=sys.stderr,
        )
        return 1

    if course_dir not in existing_dirs and existing_dirs:
        print(
            "错误：同名课程已存在于其他路径，请直接使用上面的实际路径，"
            "或换一个课程名；本次未创建任何文件。",
            file=sys.stderr,
        )
        return 1

    readme_path = course_dir / "README.md"
    overwrite_readme = False

    if readme_path.exists():
        if not args.force:
            print(
                "错误：课程卡片已存在：{}".format(readme_path),
                file=sys.stderr,
            )
            print(
                "如需覆盖课程卡片，请加 --force 参数后重试"
                "（已有资料文件不会被删除）。",
                file=sys.stderr,
            )
            return 1
        if not _confirm_overwrite(readme_path):
            return 1
        overwrite_readme = True
    elif course_dir.exists():
        print("提示：课程目录已存在，将补充缺失的 README.md 和资料子目录。")

    data = _collect_course_data(course_name, category, college, profession)

    try:
        # 四个资料子目录：已存在时直接复用，不删除任何已有文件。
        # 每个子目录放入空 .gitkeep，保证空目录也能被 git 跟踪。
        for subdir in SUBDIRS:
            subdir_path = course_dir / subdir
            subdir_path.mkdir(parents=True, exist_ok=True)
            gitkeep_path = subdir_path / ".gitkeep"
            if not gitkeep_path.exists():
                _atomic_write_text(gitkeep_path, "")

        if overwrite_readme and readme_path.exists():
            backup_path = _backup_path(readme_path)
            shutil.copy2(str(readme_path), str(backup_path))
            print("已备份原课程卡片：{}".format(backup_path))

        _atomic_write_text(readme_path, _render_readme(data))
    except OSError as exc:
        print("错误：创建课程目录或写入课程卡片失败：{}".format(exc), file=sys.stderr)
        return 1

    print("\n[OK] 课程「{}」创建成功，课程卡片：{}".format(course_name, readme_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
