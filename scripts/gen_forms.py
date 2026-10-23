#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 courses/ 的真实目录生成 GitHub Issue Forms 模板。

模板粒度是「学院」：每个学院一个独立模板，进去后「开课学院」已预填；
「所属专业」是本学院的专业下拉，「课程名称」是本学院全部课程的下拉，
每项写成「专业 · 课程名」，学生按「学院 → 专业 → 课程」的顺序逐层选。
公共课单独一个模板，课程下拉就是 courses/公共课/ 下的全部课程。
通用兜底模板 上传资料.yml 是手写的，本脚本只校验、不覆盖。

用法：
    python3 scripts/gen_forms.py            # 生成 / 更新模板（幂等）
    python3 scripts/gen_forms.py --check    # 只校验，不一致时退出码 1

只使用 Python 3 标准库，零第三方依赖。

说明：
  * 文件名格式为 上传资料-<学院>.yml；文件名里不出现「·」和空格，
    目录名中的「·」、空白、斜杠会被替换为「-」。
  * 学院模板的「所属专业」是 dropdown，选项严格等于该学院下的专业目录；
    「课程名称」也是 dropdown，选项是本学院「专业 · 课程名」的全组合
    （先按专业、再按课程名排序），不添加「其他/手填」项。
  * 生成后立即做结构自检：模板数量、字段顺序、id 集合、选项集合与格式、
    控件类型、required、文件名规则等任何一项不符都会报错退出。
  * 每个模板都保留「课程类别」字段，因为 import_issue.py 按 label 解析时
    要求该字段存在；学院模板里固定为「专业课」，公共课模板固定为「公共课」。
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_DIR = REPO_ROOT / "courses"
PUBLIC_DIR = COURSES_DIR / "公共课"
MAJOR_DIR = COURSES_DIR / "专业课"
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"

AUTO_HEADER = "# 由 scripts/gen_forms.py 自动生成，请勿手工编辑"
PUBLIC_TEMPLATE_NAME = "上传资料-公共课.yml"
GENERIC_TEMPLATE_NAME = "上传资料.yml"
GENERATED_PREFIX = "上传资料-"

OTHER_OPTION = "其他（在下面写）"
YEARS = ["2024秋", "2025春", "2025秋", "2026春", "2026秋", "其他"]
# 「年份学期」在表单里是可手填的输入框，这两个常量同时用于生成与自检。
YEAR_DESCRIPTION = (
    "填成 20xx + 春/秋，例如 2023春、2025秋、2026秋。填你实际上课的那个学期。"
)
YEAR_PLACEHOLDER = "2025秋"
MATERIAL_TYPES = ["笔记", "课件", "试卷", "作业", "实验报告", "复习资料"]
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

COLLEGE_TEMPLATE_DESCRIPTION = "学院已填好，先选专业，再选课程。"
COLLEGE_COURSE_DESCRIPTION = (
    "先在上一步选专业，然后在这里挑课程；每项前面是它所属的专业。"
    "只列出本学院的课程。"
)
PUBLIC_TEMPLATE_DESCRIPTION = "课程从下拉里选；列表里没有就开个 Issue 说一声。"
PUBLIC_COURSE_DESCRIPTION = (
    "只列出仓库里已经建好目录的公共课；列表里没有这门课，说明还没建目录"
    "——开个 Issue 说一声。"
)
COURSE_OPTION_SEPARATOR = " · "


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


def _courses(college, major):
    return _subdirs(MAJOR_DIR / college / major)


def _major_entries():
    """返回全部 (学院, 专业, 课程列表)，顺序与目录排序一致。"""
    return [
        {
            "college": college,
            "major": major,
            "courses": _courses(college, major),
        }
        for college in _colleges()
        for major in _majors(college)
    ]


def _safe_filename_part(value):
    """把目录名压成可安全放进文件名的片段：不用「·」、空白、路径分隔符。"""
    text = re.sub(r"[·\s/\\]+", "-", str(value))
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        raise ValueError("目录名 {!r} 清洗后为空，无法生成模板文件名".format(value))
    return text


def _college_course_options(college):
    """本学院全部课程，写成「专业 · 课程名」，先按专业再按课程名排序。"""
    return [
        "{}{}{}".format(major, COURSE_OPTION_SEPARATOR, course)
        for major in _majors(college)
        for course in _courses(college, major)
    ]


def _college_template_filename(college):
    return "{}{}.yml".format(GENERATED_PREFIX, _safe_filename_part(college))


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
    if options:
        lines.append("      options:")
        lines.extend("        - {}".format(_q(option)) for option in options)
    else:
        # 空专业暂时没有课程目录：写成 YAML 空数组，而不是裸 options:
        lines.append("      options: []")
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
    return _input(
        "year",
        "年份学期",
        YEAR_DESCRIPTION,
        placeholder=YEAR_PLACEHOLDER,
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
            PUBLIC_COURSE_DESCRIPTION,
            courses,
        ),
        _year_block(),
        _type_block(),
        _attachment_block(),
        _confirm_block(),
    ]
    return _document(
        "上传资料 · 公共课",
        PUBLIC_TEMPLATE_DESCRIPTION,
        blocks,
    )


def _college_course_description():
    return COLLEGE_COURSE_DESCRIPTION


def _college_template(college, majors, course_options):
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
            "先选你所在的专业，下一步的课程下拉会按这个专业核对。",
            majors,
        ),
        _dropdown(
            "course",
            "课程名称",
            _college_course_description(),
            course_options,
        ),
        _year_block(),
        _type_block(),
        _attachment_block(),
        _confirm_block(),
    ]
    return _document(
        "上传资料 · {}".format(college),
        COLLEGE_TEMPLATE_DESCRIPTION,
        blocks,
    )


def _expected_templates():
    """返回 {绝对路径: 文件内容}，顺序固定。"""
    public_courses = _public_courses()
    public_path = TEMPLATE_DIR / PUBLIC_TEMPLATE_NAME
    templates = {public_path: _public_template(public_courses)}
    seen = {public_path: "公共课"}

    for college in _colleges():
        path = TEMPLATE_DIR / _college_template_filename(college)
        if path in seen:
            raise ValueError(
                "模板文件名冲突：{} 与 {} 都会生成 {}；"
                "请调整目录名".format(seen[path], college, path.name)
            )
        seen[path] = college
        templates[path] = _college_template(
            college, _majors(college), _college_course_options(college)
        )
    return templates


COLLEGE_FIELD_ORDER = [
    "category",
    "college",
    "major",
    "course",
    "year",
    "material_type",
    "attachments",
    "confirm",
]
# 旧名字保留，避免外部脚本/测试引用时突然断掉。
MAJOR_FIELD_ORDER = COLLEGE_FIELD_ORDER
PUBLIC_FIELD_ORDER = [
    "category",
    "course",
    "year",
    "material_type",
    "attachments",
    "confirm",
]
FIELD_LABELS = {
    "category": "课程类别",
    "college": "开课学院",
    "major": "所属专业",
    "course": "课程名称",
    "course_manual": "课程名",
    "year": "年份学期",
    "material_type": "资料类型",
    "attachments": "附件",
    "confirm": "确认",
}


def _unquote(value):
    text = value.strip()
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def _parse_template_fields(content):
    """解析本脚本生成的 YAML 子集，返回 (fields, problems)。

    不依赖 PyYAML：CI 里只有标准库。只解析 body 下的 type/id/attributes/
    options/validations 这些固定结构，足以做字段错位自检。
    """
    problems = []
    lines = content.splitlines()
    try:
        body_index = lines.index("body:")
    except ValueError:
        return [], ["缺少 body: 段"]
    if lines and lines[0] != AUTO_HEADER:
        problems.append("缺少自动生成头，可能不是本脚本管理的模板")

    fields = []
    current = None
    in_options = False
    for line in lines[body_index + 1 :]:
        if not line.strip():
            continue
        match = re.match(r"^  - type: (\S+)$", line)
        if match:
            current = {
                "type": match.group(1),
                "id": "",
                "label": "",
                "description": "",
                "value": None,
                "default": None,
                "options": [],
                "option_labels": [],
                "required": None,
            }
            fields.append(current)
            in_options = False
            continue
        if current is None:
            continue
        match = re.match(r"^    id: (\S+)$", line)
        if match:
            current["id"] = match.group(1)
            in_options = False
            continue
        match = re.match(r"^      label: (.+?)\s*$", line)
        if match:
            current["label"] = _unquote(match.group(1))
            continue
        match = re.match(r"^      description: (.+?)\s*$", line)
        if match:
            current["description"] = _unquote(match.group(1))
            continue
        match = re.match(r"^      value: (.+?)\s*$", line)
        if match:
            current["value"] = _unquote(match.group(1))
            continue
        match = re.match(r"^      default: (.+?)\s*$", line)
        if match:
            current["default"] = _unquote(match.group(1))
            continue
        if re.match(r"^      options:\s*$", line):
            in_options = True
            continue
        match = re.match(r"^        - (.+?)\s*$", line)
        if match and in_options:
            option = match.group(1)
            if option.startswith("label: "):
                current["option_labels"].append(option[len("label: ") :])
            else:
                current["options"].append(_unquote(option))
            continue
        if re.match(r"^    validations:\s*$", line):
            in_options = False
            continue
        match = re.match(r"^      required: (true|false)\s*$", line)
        if match:
            current["required"] = match.group(1) == "true"
            continue

    for field in fields:
        if not field["id"]:
            problems.append("存在没有 id 的字段（type={}）".format(field["type"]))
    return fields, problems


def _course_option_problems(name, options, majors):
    """校验课程选项文本必须是「<专业> · <课程名>」，且专业属于本学院。"""
    problems = []
    major_set = set(majors)
    for option in options:
        if COURSE_OPTION_SEPARATOR not in option:
            problems.append(
                "{}: 课程选项「{}」不是「<专业> · <课程名>」格式".format(
                    name, option
                )
            )
            continue
        major, course = option.split(COURSE_OPTION_SEPARATOR, 1)
        if major not in major_set:
            problems.append(
                "{}: 课程选项「{}」的专业前缀「{}」不在本学院专业列表里".format(
                    name, option, major
                )
            )
        if not course.strip():
            problems.append(
                "{}: 课程选项「{}」缺少课程名".format(name, option)
            )
    return problems


def _validate_template(
    name, content, college=None, majors=(), courses=(), public=False
):
    """校验单个模板的字段结构；返回问题列表（空列表表示通过）。"""
    fields, problems = _parse_template_fields(content)
    problems = ["{}: {}".format(name, item) for item in problems]

    by_id = {}
    for field in fields:
        field_id = field["id"]
        if field_id in by_id:
            problems.append("{}: 字段 id 重复：{}".format(name, field_id))
        else:
            by_id[field_id] = field

    expected_order = PUBLIC_FIELD_ORDER if public else COLLEGE_FIELD_ORDER
    actual_order = [field["id"] for field in fields]
    if actual_order != expected_order:
        problems.append(
            "{}: body 字段顺序/数量不正确：{}（应为 {}）".format(
                name, actual_order, expected_order
            )
        )
    if set(actual_order) != set(expected_order):
        problems.append(
            "{}: 字段 id 集合不正确：{}（应为 {}）".format(
                name, sorted(actual_order), sorted(expected_order)
            )
        )

    for field_id, label in FIELD_LABELS.items():
        field = by_id.get(field_id)
        if field is None:
            continue
        if field["label"] != label:
            problems.append(
                "{}: 字段 {} 的 label 为「{}」，应为「{}」".format(
                    name, field_id, field["label"], label
                )
            )

    for field_id, field in by_id.items():
        if field["required"] is not True:
            problems.append("{}: 字段 {} 必须 required: true".format(name, field_id))

    category = by_id.get("category")
    if category is not None:
        expected = ["公共课"] if public else ["专业课"]
        if category["type"] != "dropdown" or category["options"] != expected:
            problems.append(
                "{}: 课程类别必须是值为 {} 的 dropdown（实际 type={}，options={}）".format(
                    name, expected, category["type"], category["options"]
                )
            )
        if category["default"] != "0":
            problems.append(
                "{}: 课程类别必须 default: 0 预选唯一选项（实际 {}）".format(
                    name, category["default"]
                )
            )

    year = by_id.get("year")
    if year is not None:
        if year["type"] != "input":
            problems.append(
                "{}: 年份学期必须是 input（实际 {}）".format(name, year["type"])
            )
        if year.get("description") != YEAR_DESCRIPTION:
            problems.append(
                "{}: 年份学期 description 不正确（实际「{}」）".format(
                    name, year.get("description")
                )
            )

    material_type = by_id.get("material_type")
    if material_type is not None:
        if material_type["type"] != "dropdown" or material_type["options"] != MATERIAL_TYPES:
            problems.append(
                "{}: 资料类型必须为 dropdown，且选项为 {}（实际 type={}，options={}）".format(
                    name, MATERIAL_TYPES, material_type["type"], material_type["options"]
                )
            )

    attachments = by_id.get("attachments")
    if attachments is not None and attachments["type"] != "textarea":
        problems.append(
            "{}: 附件必须是 textarea（实际 {}）".format(name, attachments["type"])
        )

    confirm = by_id.get("confirm")
    if confirm is not None:
        if confirm["type"] != "checkboxes":
            problems.append(
                "{}: 确认必须是 checkboxes（实际 {}）".format(name, confirm["type"])
            )
        if confirm["option_labels"] != [CONFIRM_ITEM]:
            problems.append(
                "{}: 确认勾选项不正确：{}".format(name, confirm["option_labels"])
            )

    course = by_id.get("course")
    expected_courses = list(courses)
    if public and not expected_courses:
        problems.append("{}: 公共课目录为空，至少需要一门课程".format(name))
    if course is not None:
        if course["type"] != "dropdown":
            problems.append(
                "{}: 课程名称必须是 dropdown（实际 {}）".format(
                    name, course["type"]
                )
            )
        if course["options"] != expected_courses:
            problems.append(
                "{}: 课程名称下拉与真实课程目录不一致（实际 {} 项，应为 {} 项）".format(
                    name, len(course["options"]), len(expected_courses)
                )
            )
        if OTHER_OPTION in course["options"]:
            problems.append(
                "{}: 课程名称下拉不应包含「{}」（表单做不到联动）".format(
                    name, OTHER_OPTION
                )
            )
        expected_description = (
            PUBLIC_COURSE_DESCRIPTION if public else COLLEGE_COURSE_DESCRIPTION
        )
        if course["description"] != expected_description:
            problems.append(
                "{}: 课程名称 description 不正确（实际「{}」）".format(
                    name, course["description"]
                )
            )
        if not public:
            problems.extend(
                _course_option_problems(name, course["options"], majors)
            )

    if public:
        if "college" in by_id or "major" in by_id:
            problems.append("{}: 公共课模板不应出现学院/专业字段".format(name))
        if "course_manual" in by_id:
            problems.append("{}: 公共课模板不应再保留手填课程补充框".format(name))
    else:
        college_field = by_id.get("college")
        if college_field is None or college_field["type"] != "input":
            problems.append("{}: 开课学院必须是 input".format(name))
        elif college_field["value"] != college:
            problems.append(
                "{}: 开课学院 value 为「{}」，应为「{}」".format(
                    name, college_field["value"], college
                )
            )

        major_field = by_id.get("major")
        if major_field is None or major_field["type"] != "dropdown":
            problems.append("{}: 所属专业必须是 dropdown".format(name))
        else:
            if major_field["options"] != list(majors):
                problems.append(
                    "{}: 所属专业下拉与真实专业目录不一致（实际 {} 项，应为 {} 项）".format(
                        name, len(major_field["options"]), len(majors)
                    )
                )
            if OTHER_OPTION in major_field["options"]:
                problems.append(
                    "{}: 所属专业下拉不应包含「{}」".format(
                        name, OTHER_OPTION
                    )
                )

    return problems


def _filename_problems(path):
    """文件名规则：不带「·」、空白、路径分隔符，且由本脚本管理。"""
    name = path.name
    problems = []
    if "·" in name:
        problems.append("文件名不能包含「·」：{}".format(name))
    if any(ch.isspace() for ch in name):
        problems.append("文件名不能包含空白：{}".format(name))
    if "/" in name or "\\" in name:
        problems.append("文件名不能包含路径分隔符：{}".format(name))
    if not name.startswith(GENERATED_PREFIX) or not name.endswith(".yml"):
        problems.append(
            "生成模板文件名应形如 {}<学院>.yml：{}".format(
                GENERATED_PREFIX, name
            )
        )
    return problems


def _validate_expected(expected):
    """校验整批预期模板：数量、路径、文件名、每个模板结构。"""
    problems = []
    public_courses = _public_courses()
    colleges = _colleges()
    expected_paths = {TEMPLATE_DIR / PUBLIC_TEMPLATE_NAME: None}
    for college in colleges:
        try:
            path = TEMPLATE_DIR / _college_template_filename(college)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        expected_paths[path] = college

    if len(expected_paths) != len(colleges) + 1:
        problems.append(
            "模板数量不正确：{}（应为 学院 {} + 公共课 1）".format(
                len(expected_paths), len(colleges)
            )
        )

    for path in expected_paths:
        if path not in expected:
            problems.append("缺少预期模板：{}".format(path.name))
    for path in expected:
        if path not in expected_paths:
            problems.append("多出未知模板：{}".format(path.name))

    for path, content in expected.items():
        for item in _filename_problems(path):
            problems.append("{}: {}".format(path.name, item))
        if path == TEMPLATE_DIR / PUBLIC_TEMPLATE_NAME:
            problems.extend(
                _validate_template(
                    path.name,
                    content,
                    courses=public_courses,
                    public=True,
                )
            )
        elif path in expected_paths and expected_paths[path] is not None:
            college = expected_paths[path]
            problems.extend(
                _validate_template(
                    path.name,
                    content,
                    college=college,
                    majors=_majors(college),
                    courses=_college_course_options(college),
                )
            )
    return problems


def _generic_problems():
    """校验手写的通用兜底模板是否仍与 courses/ 同步。

    通用模板不由本脚本生成（它刻意保留「学院·专业」形式），但学院/专业
    目录变化后同样会过期。这里只做只读校验，避免它悄悄漏掉新专业。
    """
    path = TEMPLATE_DIR / GENERIC_TEMPLATE_NAME
    if not path.is_file():
        return ["{}: 通用兜底模板不存在".format(GENERIC_TEMPLATE_NAME)]
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ["{}: 读取失败（{}）".format(GENERIC_TEMPLATE_NAME, exc)]

    fields, _header_problems = _parse_template_fields(content)
    by_id = {}
    for field in fields:
        by_id.setdefault(field["id"], field)

    problems = []
    colleges = _colleges()
    category = by_id.get("category")
    if (
        category is None
        or category["type"] != "dropdown"
        or category["options"] != ["公共课", "专业课"]
    ):
        problems.append(
            "{}: 课程类别下拉应为「公共课 / 专业课」".format(GENERIC_TEMPLATE_NAME)
        )

    year = by_id.get("year")
    if (
        year is None
        or year["type"] != "input"
        or year.get("description") != YEAR_DESCRIPTION
    ):
        problems.append(
            "{}: 年份学期必须是 input，且 description 与学院模板一致".format(
                GENERIC_TEMPLATE_NAME
            )
        )

    college = by_id.get("college")
    if (
        college is None
        or college["type"] != "dropdown"
        or college["options"] != colleges
        or college["required"] is not False
    ):
        problems.append(
            "{}: 开课学院下拉与 courses/ 不一致，或未设为 required: false".format(
                GENERIC_TEMPLATE_NAME
            )
        )

    expected_majors = [
        "{}·{}".format(college, major)
        for college in colleges
        for major in _majors(college)
    ]
    major = by_id.get("major")
    if (
        major is None
        or major["type"] != "dropdown"
        or major["options"] != expected_majors
        or major["required"] is not False
    ):
        problems.append(
            "{}: 所属专业下拉与 courses/ 不一致，或未设为 required: false".format(
                GENERIC_TEMPLATE_NAME
            )
        )
    return problems


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


def _counts(expected):
    public_count = len(_public_courses())
    college_course_count = sum(
        len(_college_course_options(college)) for college in _colleges()
    )
    return {
        "colleges": max(len(expected) - 1, 0),
        "public": 1,
        "generic": 1 if (TEMPLATE_DIR / GENERIC_TEMPLATE_NAME).is_file() else 0,
        "courses": college_course_count + public_count,
    }


def _print_counts(counts):
    print(
        "学院模板 {} 个 / 公共课 {} 个 / 兜底 {} 个；课程选项总数 {}".format(
            counts["colleges"],
            counts["public"],
            counts["generic"],
            counts["courses"],
        )
    )


def _generate():
    try:
        expected = _expected_templates()
    except ValueError as exc:
        print("ERROR {}".format(exc))
        print("FAIL 模板生成失败")
        return 1

    counts = _counts(expected)
    _print_counts(counts)

    # 写盘前先校验生成内容，避免把错位模板落盘。
    problems = _validate_expected(expected)
    if problems:
        for problem in problems:
            print("ERROR {}".format(problem))
        print("FAIL 模板结构自检未通过，已中止写入")
        return 1

    # 通用兜底模板是手写的，本脚本不覆盖；这里只提醒它是否已过期。
    generic_problems = _generic_problems()
    for problem in generic_problems:
        print("WARN {}".format(problem))
    if generic_problems:
        print(
            "WARN {} 需要手动同步；同步前 python3 scripts/gen_forms.py --check 会失败".format(
                GENERIC_TEMPLATE_NAME
            )
        )

    for path, content in expected.items():
        if not _write_if_changed(path, content):
            return 1

    # 清掉旧粒度（按学院）或其他目录变更后遗留的模板，避免选项过期。
    for path in sorted(_existing_generated()):
        if path not in expected:
            try:
                path.unlink()
            except OSError as exc:
                print("ERROR 删除过期模板 {} 失败：{}".format(path.name, exc))
                return 1
            print("removed {}".format(path.name))

    # 再读回磁盘校验一次，确认写入结果与自检一致。
    written = {}
    for path in expected:
        try:
            written[path] = path.read_text(encoding="utf-8")
        except OSError as exc:
            print("ERROR 读取已生成模板 {} 失败：{}".format(path.name, exc))
            return 1
    problems = _validate_expected(written)
    if problems:
        for problem in problems:
            print("ERROR {}".format(problem))
        print("FAIL 写盘后的模板结构自检未通过")
        return 1

    print(
        "CHECK OK {} 个模板结构自检通过（数量 / 字段顺序 / id / 选项集合 / 控件类型 / required / 文件名）".format(
            len(expected)
        )
    )
    print("DONE 表单模板已与 courses/ 同步")
    return 0


def _check():
    try:
        expected = _expected_templates()
    except ValueError as exc:
        print("ERROR {}".format(exc))
        print("FAIL 表单模板生成规则校验失败")
        return 1
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

    for problem in _validate_expected(expected):
        problems.append("STRUCT {}".format(problem))

    for problem in _generic_problems():
        problems.append("GENERIC {}".format(problem))

    if problems:
        for problem in problems:
            print(problem)
        print(
            "FAIL 表单模板与 courses/ 不一致；请运行 python3 scripts/gen_forms.py 重新生成"
            "（通用兜底模板若被点名，需要手动同步）"
        )
        return 1

    counts = _counts(expected)
    _print_counts(counts)
    print(
        "OK 表单模板与 courses/ 一致（自动生成 {} 个：学院 {} + 公共课 1；"
        "通用兜底 {}；课程选项 {} 个）".format(
            len(expected),
            counts["colleges"],
            counts["generic"],
            counts["courses"],
        )
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="gen_forms.py",
        description="按 courses/ 真实目录生成「学院」粒度的 Issue Forms 模板。",
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
