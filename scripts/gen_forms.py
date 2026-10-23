#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 courses/ 的真实目录生成 GitHub Issue Forms 模板。

每个学院一个独立模板：进模板后「开课学院」已预填，「所属专业」下拉里
只有该学院的专业。公共课单独一个模板。

用法：
    python3 scripts/gen_forms.py            # 生成 / 更新模板（幂等）
    python3 scripts/gen_forms.py --check    # 只校验，不一致时退出码 1

只使用 Python 3 标准库，零第三方依赖。

说明：
  * 通用兜底模板 .github/ISSUE_TEMPLATE/上传资料.yml 不由本脚本生成或覆盖。
  * 专业选项写成「学院·专业」。这是 import_issue.py 支持的写法；仓库里
    「土木工程」「经济学」「生物技术」「新能源科学与工程」等专业在多个
    学院重复出现，只写专业名会让脚本无法判定学院。
  * 每个模板都保留「课程类别」字段，因为 import_issue.py 按 label 解析时
    要求该字段存在；学院模板里固定为「专业课」。
  * 公共课模板的补充框 label 用「课程名」而不是「课程名称（补充）」：
    import_issue.py 只把已知 label 当分节边界，未知的「课程名称（补充）」
    会被并进上一个「课程名称」段，导致每次公共课提交都报“应为单行内容”。
    「课程名」是 import_issue.py 已有的别名，能让正常下拉提交保持可用；
    但机器人仍以下拉值为准，选「其他（在下面写）」时补充框不会被解析。
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_DIR = REPO_ROOT / "courses"
PUBLIC_DIR = COURSES_DIR / "公共课"
MAJOR_DIR = COURSES_DIR / "专业课"
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"

AUTO_HEADER = "# 由 scripts/gen_forms.py 自动生成，请勿手工编辑"
PUBLIC_TEMPLATE_NAME = "上传资料-公共课.yml"
GENERATED_PREFIX = "上传资料-"

OTHER_OPTION = "其他（在下面写）"
YEARS = ["2024秋", "2025春", "2025秋", "2026春", "2026秋", "其他"]
MATERIAL_TYPES = ["笔记", "课件", "试卷", "作业", "实验报告", "复习资料", "其他"]
CONFIRM_ITEM = "文件里不含本人或他人的姓名、学号、手机号、身份证号"

ATTACHMENT_DESCRIPTION = (
    "把文件拖进来：最多 5 个、每个不超过 50MB（zip 不超过 20MB，"
    "合计不超过 100MB）。手机如无法添加附件，请直接发给维护者。"
)
ATTACHMENT_PLACEHOLDER = (
    "把文件拖进来后，GitHub 会自动生成类似下面的链接，请不要删掉：\n"
    "[我的笔记.pdf](https://github.com/user-attachments/assets/...)\n"
    "选「其他」年份学期时，也请在这里写清 20xx春 / 20xx秋。"
)


def _q(value):
    """输出 YAML 双引号标量；JSON 字符串语法与 YAML 双引号兼容。"""
    return json.dumps(str(value), ensure_ascii=False)


def _subdirs(base):
    """按名称排序列出真实子目录，忽略隐藏目录。"""
    try:
        return sorted(
            child.name
            for child in base.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )
    except OSError:
        return []


def _colleges():
    return _subdirs(MAJOR_DIR)


def _public_courses():
    return _subdirs(PUBLIC_DIR)


def _majors(college):
    return _subdirs(MAJOR_DIR / college)


def _dropdown(field_id, label, description, options, required=True, default=None):
    lines = [
        "  - type: dropdown",
        "    id: {}".format(field_id),
        "    attributes:",
        "      label: {}".format(label),
        "      description: {}".format(_q(description)),
    ]
    if default is not None:
        lines.append("      default: {}".format(default))
    lines.append("      options:")
    lines.extend("        - {}".format(_q(option)) for option in options)
    lines.extend(
        [
            "    validations:",
            "      required: {}".format("true" if required else "false"),
        ]
    )
    return "\n".join(lines)


def _input(field_id, label, description, placeholder=None, value=None, required=True):
    lines = [
        "  - type: input",
        "    id: {}".format(field_id),
        "    attributes:",
        "      label: {}".format(label),
        "      description: {}".format(_q(description)),
    ]
    if value is not None:
        lines.append("      value: {}".format(_q(value)))
    if placeholder is not None:
        lines.append("      placeholder: {}".format(_q(placeholder)))
    lines.extend(
        [
            "    validations:",
            "      required: {}".format("true" if required else "false"),
        ]
    )
    return "\n".join(lines)


def _year_block():
    return _dropdown(
        "year",
        "年份学期",
        "这份资料是哪一学年、哪个学期用的；选「其他」时请在附件框写清 20xx春 / 20xx秋。",
        YEARS,
    )


def _type_block():
    return _dropdown(
        "material_type",
        "资料类型",
        "这份资料是什么，决定放进课程下的哪个文件夹。",
        MATERIAL_TYPES,
    )


def _attachment_block():
    return "\n".join(
        [
            "  - type: textarea",
            "    id: attachments",
            "    attributes:",
            "      label: 附件",
            "      description: {}".format(_q(ATTACHMENT_DESCRIPTION)),
            "      placeholder: |",
        ]
        + ["        {}".format(line) for line in ATTACHMENT_PLACEHOLDER.splitlines()]
        + [
            "    validations:",
            "      required: true",
        ]
    )


def _confirm_block():
    return "\n".join(
        [
            "  - type: checkboxes",
            "    id: confirm",
            "    attributes:",
            "      label: 确认",
            "      description: {}".format(_q("上传前请自己检查一遍文件内容和文档属性。")),
            "      options:",
            "        - label: {}".format(CONFIRM_ITEM),
            "          required: true",
            "    validations:",
            "      required: true",
        ]
    )


def _document(name, description, blocks):
    head = [
        AUTO_HEADER,
        "name: {}".format(_q(name)),
        "description: {}".format(_q(description)),
        'title: "[上传] "',
        'labels: ["待入库"]',
        "body:",
    ]
    return "\n".join(head + ["\n\n".join(blocks)]) + "\n"


def _public_template(courses):
    blocks = [
        _dropdown(
            "category",
            "课程类别",
            "本模板固定为公共课，无需选择。",
            ["公共课"],
            default=0,
        ),
        _dropdown(
            "course",
            "课程名称",
            "从下拉里选你的课程；列表里没有就选「其他（在下面写）」，再到下一项补充。",
            courses + [OTHER_OPTION],
        ),
        # label 必须是 import_issue.py 的已知别名，否则会污染上一段。
        _input(
            "course_manual",
            "课程名",
            "课程名称（补充）：仅当上面选了「其他（在下面写）」时填写；"
            "必须与仓库课程文件夹名完全一致。",
            placeholder="例如：高等数学",
            required=False,
        ),
        _year_block(),
        _type_block(),
        _attachment_block(),
        _confirm_block(),
    ]
    return _document(
        "上传资料 · 公共课",
        "公共课专用：课程从下拉里选；列表里没有就选「其他（在下面写）」，在下一项补充课程名。",
        blocks,
    )


def _college_template(college, majors):
    options = ["{}·{}".format(college, major) for major in majors]
    blocks = [
        _dropdown(
            "category",
            "课程类别",
            "本模板固定为专业课，无需选择。",
            ["专业课"],
            default=0,
        ),
        _input(
            "college",
            "开课学院",
            "已预填为本模板的学院；一般不用改。",
            value=college,
        ),
        _dropdown(
            "major",
            "所属专业",
            "只列出「{}」下的专业；请选择「{}·专业」。".format(college, college),
            options,
        ),
        _input(
            "course",
            "课程名称",
            "与该专业下课程文件夹名完全一致；写错会提示最接近的。",
            placeholder="例如：康复医学概论",
        ),
        _year_block(),
        _type_block(),
        _attachment_block(),
        _confirm_block(),
    ]
    return _document(
        "上传资料 · {}".format(college),
        "{}专用：学院已预填，专业下拉只含本院专业。".format(college),
        blocks,
    )


def _expected_templates():
    """返回 {绝对路径: 文件内容}，顺序固定。"""
    public_courses = _public_courses()
    colleges = _colleges()

    templates = {
        TEMPLATE_DIR / PUBLIC_TEMPLATE_NAME: _public_template(public_courses)
    }
    for college in colleges:
        templates[TEMPLATE_DIR / "上传资料-{}.yml".format(college)] = _college_template(
            college, _majors(college)
        )
    return templates


def _existing_generated():
    """列出本脚本管理的模板；只把带自动生成头的文件当自己的。"""
    found = {}
    for path in sorted(TEMPLATE_DIR.glob(GENERATED_PREFIX + "*.yml")):
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            first_line = ""
        if first_line == AUTO_HEADER:
            found[path] = first_line
    return found


def _write_if_changed(path, content):
    try:
        old = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        old = None
    except OSError as exc:
        print("ERROR 读取 {} 失败：{}".format(path.name, exc))
        return False

    if old == content:
        print("unchanged {}".format(path.name))
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print("{} {}".format("updated" if old is not None else "created", path.name))
    return True


def _generate():
    expected = _expected_templates()

    public_courses = _public_courses()
    colleges = _colleges()
    print("公共课：{} 门课程".format(len(public_courses)))
    for college in colleges:
        print("{}：{} 个专业".format(college, len(_majors(college))))
    print(
        "共 {} 个模板（公共课 1 + 学院 {}）".format(len(expected), len(colleges))
    )

    for path, content in expected.items():
        _write_if_changed(path, content)

    # 清掉学院/公共课目录变更后遗留的旧模板，避免选项过期。
    for path in sorted(_existing_generated()):
        if path not in expected:
            try:
                path.unlink()
            except OSError as exc:
                print("ERROR 删除过期模板 {} 失败：{}".format(path.name, exc))
                return 1
            print("removed {}".format(path.name))

    print("DONE 表单模板已与 courses/ 同步")
    return 0


def _check():
    expected = _expected_templates()
    actual = _existing_generated()
    problems = []

    for path in sorted(expected):
        if path not in actual:
            problems.append("MISSING {}".format(path.name))
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append("UNREADABLE {}（{}）".format(path.name, exc))
            continue
        if content != expected[path]:
            problems.append("DIFF {}".format(path.name))

    for path in sorted(actual):
        if path not in expected:
            problems.append("EXTRA {}".format(path.name))

    if problems:
        for problem in problems:
            print(problem)
        print(
            "FAIL 表单模板与 courses/ 不一致；请运行 python3 scripts/gen_forms.py 重新生成"
        )
        return 1

    colleges = _colleges()
    majors = sum(len(_majors(college)) for college in colleges)
    print(
        "OK 表单模板与 courses/ 一致（{} 个模板：公共课 1、学院 {}；"
        "公共课 {} 门、专业 {} 个）".format(
            len(expected), len(colleges), len(_public_courses()), majors
        )
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gen_forms.py",
        description="按 courses/ 真实目录生成按学院拆分的 Issue Forms 模板。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验现有模板与 courses/ 是否一致；不一致时退出码 1。",
    )
    args = parser.parse_args(argv)

    if not COURSES_DIR.is_dir():
        print("ERROR 找不到 courses/，请在仓库根目录运行。", file=sys.stderr)
        return 2

    if args.check:
        return _check()
    return _generate()


if __name__ == "__main__":
    sys.exit(main())
