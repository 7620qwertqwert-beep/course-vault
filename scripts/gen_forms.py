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
  * 通用兜底模板 .github/ISSUE_TEMPLATE/上传资料.yml 不由本脚本生成或覆盖，
    那里仍用「学院·专业」；学院模板的「开课学院」已固定，专业选项只写
    纯专业名，import_issue.py 会用「开课学院 + 专业名」定位。--check 会
    只读校验通用模板的学院/专业下拉是否与目录同步，过期时退出码 1。
  * 生成后立即做结构自检：字段顺序、id 集合、选项数量、控件类型、
    required 等任何一项不符都会报错退出，避免模板字段错位。
  * 每个模板都保留「课程类别」字段，因为 import_issue.py 按 label 解析时
    要求该字段存在；学院模板里固定为「专业课」。
  * 公共课模板的补充框 label 用「课程名」而不是「课程名称（补充）」：
    import_issue.py 只把已知 label 当分节边界，未知的「课程名称（补充）」
    会被并进上一个「课程名称」段，导致每次公共课提交都报“应为单行内容”。
    import_issue.py 已把「课程名」登记为 course_manual 的别名：选
    「其他（在下面写）」时会优先使用补充框，正常下拉提交不受影响。
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
    # 学院已由本模板固定，专业下拉只写纯专业名，避免「学院·专业」重复前缀。
    options = list(majors)
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
            "只列出「{}」下的专业；直接选专业名即可，不用带学院前缀。".format(
                college
            ),
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
PUBLIC_FIELD_ORDER = [
    "category",
    "course",
    "course_manual",
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


def _validate_template(name, content, college=None, majors=(), public=False):
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
        if field["required"] is not True and not (
            public and field_id == "course_manual"
        ):
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
        if year["type"] != "dropdown" or year["options"] != YEARS:
            problems.append(
                "{}: 年份学期必须为 dropdown，且选项为 {}（实际 type={}，options={}）".format(
                    name, YEARS, year["type"], year["options"]
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

    if public:
        if not majors:
            problems.append("{}: 公共课目录为空，至少需要一门课程".format(name))
        course = by_id.get("course")
        if course is not None:
            expected_courses = list(majors) + [OTHER_OPTION]
            if course["type"] != "dropdown" or course["options"] != expected_courses:
                problems.append(
                    "{}: 课程名称下拉必须为 {} 门公共课 + 兜底项".format(
                        name, len(majors)
                    )
                )
        manual = by_id.get("course_manual")
        if manual is not None and manual["type"] != "input":
            problems.append("{}: 课程名补充必须是 input".format(name))
    else:
        if not majors:
            problems.append(
                "{}: 学院下没有专业目录，不能生成空的专业下拉".format(name)
            )
        college_field = by_id.get("college")
        if college_field is None or college_field["type"] != "input":
            problems.append("{}: 开课学院必须是 input".format(name))
        elif college_field["value"] != college:
            problems.append(
                "{}: 开课学院 value 为「{}」，应为「{}」".format(
                    name, college_field["value"], college
                )
            )

        course = by_id.get("course")
        if course is None or course["type"] != "input":
            problems.append("{}: 课程名称必须是 input".format(name))

        major = by_id.get("major")
        if major is None:
            problems.append("{}: 缺少所属专业字段".format(name))
        else:
            options = major["options"]
            if major["type"] != "dropdown":
                problems.append("{}: 所属专业必须是 dropdown".format(name))
            if len(options) != len(majors):
                problems.append(
                    "{}: 所属专业选项 {} 项，应为 {} 项".format(
                        name, len(options), len(majors)
                    )
                )
            if options != list(majors):
                problems.append("{}: 所属专业选项与真实专业目录不一致".format(name))
            for option in options:
                if option != option.strip():
                    problems.append(
                        "{}: 所属专业选项首尾有空白，解析时会被去掉：{!r}".format(
                            name, option
                        )
                    )
                if not option or option.startswith(".") or ".." in option:
                    problems.append(
                        "{}: 所属专业选项不能以点开头或包含「..」：{}".format(
                            name, option
                        )
                    )
                if any(
                    ord(ch) < 32 or 127 <= ord(ch) <= 0x9F for ch in option
                ):
                    problems.append(
                        "{}: 所属专业选项包含控制字符：{!r}".format(name, option)
                    )
                if any(ch in option for ch in "·/／\\"):
                    problems.append(
                        "{}: 所属专业选项不应带学院前缀或路径分隔符：{}".format(
                            name, option
                        )
                    )

    return problems


def _validate_expected(expected):
    problems = []
    for path, content in expected.items():
        if path.name == PUBLIC_TEMPLATE_NAME:
            problems.extend(
                _validate_template(
                    path.name,
                    content,
                    college=None,
                    majors=_public_courses(),
                    public=True,
                )
            )
        else:
            college = path.stem[len(GENERATED_PREFIX) :]
            problems.extend(
                _validate_template(
                    path.name, content, college=college, majors=_majors(college)
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
        return []
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
        "CHECK OK {} 个模板结构自检通过（字段顺序 / id / 选项数 / 控件类型 / required）".format(
            len(expected)
        )
    )
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

    colleges = _colleges()
    majors = sum(len(_majors(college)) for college in colleges)
    generic_count = 1 if (TEMPLATE_DIR / GENERIC_TEMPLATE_NAME).is_file() else 0
    print(
        "OK 表单模板与 courses/ 一致（自动生成 {} 个：公共课 1、学院 {}；"
        "通用兜底 {}；公共课 {} 门、专业 {} 个）".format(
            len(expected), len(colleges), generic_count, len(_public_courses()), majors
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
