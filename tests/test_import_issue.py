#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/import_issue.py 的标准库单元测试。

运行：
    python3 -m unittest discover -s tests -v

测试全部在临时目录里构造课程树，不会写入真实仓库。
"""

import io
import os
import re
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import import_issue as imp  # noqa: E402  (先调整 sys.path 再导入)


FAKE_ATTACHMENT_URL = (
    "https://github.com/user-attachments/assets/"
    "11111111-2222-3333-4444-555555555555"
)

CONFIRM_CHECKED = "- [X] 文件里不含本人或他人的姓名、学号、手机号、身份证号"
CONFIRM_UNCHECKED = "- [ ] 文件里不含本人或他人的姓名、学号、手机号、身份证号"


def make_body(
    category="专业课",
    college="医学院",
    college_manual="",
    major="医学院·康复治疗学",
    major_manual="",
    course="康复医学概论",
    course_manual="_No response_",
    year="2025秋",
    material_type="笔记",
    attachments=None,
    confirm=CONFIRM_CHECKED,
):
    """按 GitHub Issue Forms 的真实渲染格式生成正文。"""
    if attachments is None:
        attachments = "[我的笔记.pdf]({})".format(FAKE_ATTACHMENT_URL)
    return (
        "### 课程类别\n\n{category}\n\n"
        "### 开课学院\n\n{college}\n\n"
        "### 学院名称\n\n{college_manual}\n\n"
        "### 所属专业\n\n{major}\n\n"
        "### 专业名称\n\n{major_manual}\n\n"
        "### 课程名称\n\n{course}\n\n"
        "### 课程名\n\n{course_manual}\n\n"
        "### 年份学期\n\n{year}\n\n"
        "### 资料类型\n\n{material_type}\n\n"
        "### 附件\n\n{attachments}\n\n"
        "### 确认\n\n{confirm}\n"
    ).format(
        category=category,
        college=college,
        college_manual=college_manual,
        major=major,
        major_manual=major_manual,
        course=course,
        course_manual=course_manual,
        year=year,
        material_type=material_type,
        attachments=attachments,
        confirm=confirm,
    )


def make_major_template_body(
    college="医学院",
    major="康复治疗学",
    course="康复医学概论",
    year="2025秋",
    material_type="笔记",
    attachments=None,
    confirm=CONFIRM_CHECKED,
):
    """按新的「学院 + 专业」模板渲染正文：学院/专业/课程三处都是纯名。"""
    if attachments is None:
        attachments = "[我的笔记.pdf]({})".format(FAKE_ATTACHMENT_URL)
    return (
        "### 课程类别\n\n专业课\n\n"
        "### 开课学院\n\n{college}\n\n"
        "### 所属专业\n\n{major}\n\n"
        "### 课程名称\n\n{course}\n\n"
        "### 年份学期\n\n{year}\n\n"
        "### 资料类型\n\n{material_type}\n\n"
        "### 附件\n\n{attachments}\n\n"
        "### 确认\n\n{confirm}\n"
    ).format(
        college=college,
        major=major,
        course=course,
        year=year,
        material_type=material_type,
        attachments=attachments,
        confirm=confirm,
    )


class ImportIssueTest(unittest.TestCase):
    def setUp(self):
        self._old_repo_root = imp.REPO_ROOT
        self._old_courses_dir = imp.COURSES_DIR

        self._tmp = tempfile.TemporaryDirectory(prefix="course-vault-unit-")
        self.root = Path(self._tmp.name)
        self.courses = self.root / "courses"
        self.course = (
            self.courses / "专业课" / "医学院" / "康复治疗学" / "康复医学概论"
        )
        for subdir in ("笔记", "课件", "试卷", "作业"):
            (self.course / subdir).mkdir(parents=True, exist_ok=True)
        (self.course / "README.md").write_text("# 康复医学概论\n", encoding="utf-8")

        # 让脚本把临时目录当成仓库根。
        imp.REPO_ROOT = self.root
        imp.COURSES_DIR = self.courses

        self.body_path = self.root / "issue.md"
        self.write_body(make_body())

    def tearDown(self):
        imp.REPO_ROOT = self._old_repo_root
        imp.COURSES_DIR = self._old_courses_dir
        self._tmp.cleanup()

    def write_body(self, body):
        self.body_path.write_text(body, encoding="utf-8")

    def run_main(self, *extra):
        env = {
            "IMPORT_ISSUE_BODY_FILE": str(self.body_path),
            "IMPORT_ISSUE_TITLE": "[上传] 测试",
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = imp.main(["--issue-number", "1", *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    def make_source(self, name, content):
        source = self.root / "sources" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        return source

    def add_course(self, college, major, course):
        """在临时课程树里补一门专业课，返回课程目录。"""
        path = self.courses / "专业课" / college / major / course
        for subdir in ("笔记", "课件", "试卷", "作业"):
            (path / subdir).mkdir(parents=True, exist_ok=True)
        (path / "README.md").write_text(
            "# {}\n".format(course), encoding="utf-8"
        )
        return path

    def add_public_course(self, course):
        """在临时课程树里补一门公共课，返回课程目录。"""
        path = self.courses / "公共课" / course
        for subdir in ("笔记", "课件", "试卷", "作业"):
            (path / subdir).mkdir(parents=True, exist_ok=True)
        (path / "README.md").write_text(
            "# {}\n".format(course), encoding="utf-8"
        )
        return path

    # ---------- 解析 ----------

    def test_parse_chinese_and_major_with_separator(self):
        form = imp._parse_form("[上传] 测试", make_body())
        self.assertEqual(form["category"], "专业课")
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["course"], "康复医学概论")
        self.assertEqual(form["year"], "2025秋")
        self.assertEqual(form["type"], "笔记")
        self.assertEqual(len(form["attachments"]), 1)
        self.assertEqual(form["attachments"][0]["url"], FAKE_ATTACHMENT_URL)

    def test_parse_public_course_ignores_empty_college_and_major(self):
        body = make_body(
            category="公共课",
            college="_No response_",
            major="_No response_",
            course="高等数学",
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["category"], "公共课")
        self.assertEqual(form["college"], "")
        self.assertEqual(form["major"], "")
        self.assertEqual(form["course"], "高等数学")

    def test_checkbox_unchecked_is_rejected(self):
        self.write_body(make_body(confirm=CONFIRM_UNCHECKED))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("请先勾选确认项", out)

    def test_checkbox_lowercase_x_is_accepted(self):
        body = make_body(confirm=CONFIRM_CHECKED.replace("[X]", "[x]"))
        form = imp._parse_form("", body)
        self.assertEqual(form["course"], "康复医学概论")

    def test_checkbox_with_wrong_text_is_rejected(self):
        body = make_body(confirm="- [X] 随便勾一个无关的确认项")
        with self.assertRaises(imp.ImportFailure):
            imp._parse_form("", body)

    def test_unknown_heading_inside_attachment_is_not_a_section(self):
        body = make_body(
            attachments=(
                "说明文字\n\n### 随便写的标题\n\n"
                "[我的笔记.pdf]({})".format(FAKE_ATTACHMENT_URL)
            )
        )
        form = imp._parse_form("", body)
        self.assertEqual(len(form["attachments"]), 1)

    def test_form_labels_match_parser(self):
        form_text = (
            REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "上传资料.yml"
        ).read_text(encoding="utf-8")
        expected_labels = (
            "课程类别",
            "开课学院",
            "所属专业",
            "课程名称",
            "年份学期",
            "资料类型",
            "附件",
            "确认",
        )
        for label in expected_labels:
            self.assertRegex(
                form_text,
                r"(?m)^\s*label:\s*{}\s*$".format(re.escape(label)),
                "Issue 表单缺少字段标签：{}".format(label),
            )
            self.assertIn(label, imp._parse_sections(make_body()))
        self.assertIn(imp.CONFIRM_REQUIREMENT, form_text)

    def test_generated_form_labels_are_parser_known(self):
        """生成的按学院模板里，每个顶层 label 都必须是解析器已知的分节标签。

        未知 label（例如「课程名称（补充）」）不会成为分节边界，会被并进
        上一段，导致该段出现多行内容而解析失败。
        """
        templates = sorted(
            (REPO_ROOT / ".github" / "ISSUE_TEMPLATE").glob("上传资料-*.yml")
        )
        self.assertTrue(templates, "没有找到 scripts/gen_forms.py 生成的模板")
        for path in templates:
            text = path.read_text(encoding="utf-8")
            labels = re.findall(r"(?m)^ {6}label:\s*(.+?)\s*$", text)
            self.assertTrue(labels, "{} 没有任何字段 label".format(path.name))
            for label in labels:
                self.assertIn(
                    label,
                    imp.KNOWN_SECTION_LABELS,
                    "{} 的字段 label「{}」不是 import_issue.py 的已知分节标签".format(
                        path.name, label
                    ),
                )

    def test_form_dropdown_option_counts(self):
        form_text = (
            REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "上传资料.yml"
        ).read_text(encoding="utf-8")

        def options_of(field_id):
            block = re.search(
                r"(?ms)^\s*- type: dropdown\s*\n\s*id: {}\b(.*?)"
                r"(?=^\s*- type: )".format(re.escape(field_id)),
                form_text,
            )
            self.assertIsNotNone(block, "找不到 dropdown：{}".format(field_id))
            return re.findall(r"(?m)^\s{8}- (.+?)\s*$", block.group(1))

        colleges = options_of("college")
        majors = options_of("major")
        self.assertEqual(len(colleges), 24, colleges)
        self.assertEqual(len(majors), 84, majors)
        self.assertEqual(len(set(colleges)), len(colleges))
        self.assertEqual(len(set(majors)), len(majors))

    def test_new_major_template_pure_prefill_locates_course(self):
        """新专业模板：学院/专业预填纯名、课程来自下拉，仍能定位到四层目录。"""
        body = make_major_template_body()
        form = imp._parse_form("[上传] 测试", body)
        self.assertEqual(form["category"], "专业课")
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["course"], "康复医学概论")
        self.assertEqual(form["college_source"], "dropdown")
        self.assertEqual(form["major_source"], "dropdown")

        self.write_body(body)
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn(
            "courses/专业课/医学院/康复治疗学/康复医学概论/笔记", out
        )

    def test_new_major_template_accepts_parenthetical_major_note(self):
        body = make_major_template_body(major="康复治疗学（中外合作）")
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["course"], "康复医学概论")

    def test_new_major_template_course_typo_suggests_scoped_course(self):
        self.add_course("医学院", "护理学", "人体解剖学")
        self.write_body(
            make_major_template_body(
                major="护理学", course="人体解剖学（旧）"
            )
        )
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("课程目录不存在", out)
        self.assertIn("人体解剖学", out)

    # ---------- 失败路径 ----------

    def test_course_not_found_exits_2(self):
        self.write_body(make_body(course="不存在的课程"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("课程目录不存在", out)

    def test_unknown_college_suggests_closest(self):
        self.write_body(make_body(college="医学完"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("找不到学院", out)
        self.assertIn("医学院", out)

    def test_unknown_course_suggests_closest(self):
        self.write_body(make_body(course="康复医学概伦"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("康复医学概论", out)

    def test_unknown_major_suggests_candidates_with_college(self):
        self.write_body(make_body(major="闹鬼学"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("找不到专业", out)
        self.assertIn("医学院·康复治疗学", out)

    def test_ambiguous_bare_major_without_college_is_rejected(self):
        for college in ("商学院", "国际教育学院"):
            (self.courses / "专业课" / college / "经济学").mkdir(
                parents=True, exist_ok=True
            )
        # 没有「开课学院」时才需要全局判断；学院模板会先用学院锁定范围。
        body = make_body(college="_No response_", major="经济学")
        with self.assertRaises(imp.ImportFailure) as ctx:
            imp._parse_form("", body)
        self.assertIn("商学院·经济学", str(ctx.exception))
        self.assertIn("国际教育学院·经济学", str(ctx.exception))

    def test_bare_major_is_scoped_by_college_even_if_duplicated_elsewhere(self):
        for college in ("商学院", "国际教育学院"):
            (self.courses / "专业课" / college / "经济学").mkdir(
                parents=True, exist_ok=True
            )
        body = make_body(college="商学院", major="经济学")
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "商学院")
        self.assertEqual(form["major"], "经济学")

    def test_bare_major_wrong_college_fails_with_scoped_suggestion(self):
        (self.courses / "专业课" / "文学院" / "汉语言文学").mkdir(
            parents=True, exist_ok=True
        )
        self.write_body(make_body(college="文学院", major="康复治疗学"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("在学院「文学院」下找不到专业「康复治疗学」", out)
        self.assertIn("文学院·", out)

    def test_prefixed_major_conflicting_with_college_fails(self):
        (self.courses / "专业课" / "文学院" / "汉语言文学").mkdir(
            parents=True, exist_ok=True
        )
        self.write_body(make_body(college="文学院", major="医学院·康复治疗学"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("开课学院与所属专业不一致", out)

    def test_illegal_course_components_are_rejected(self):
        for name in ("a/b", "..", "a\x00b", "a\\b", " a", "a ", ".", "a:b"):
            with self.subTest(name=repr(name)):
                with self.assertRaises(imp.ImportFailure):
                    imp._validate_component(name, "课程名称")

    def test_course_name_with_slash_fails_whole_parse(self):
        body = make_body(course="康复/医学概论")
        with self.assertRaises(imp.ImportFailure):
            imp._parse_form("", body)

    def test_course_name_with_surrounding_spaces_is_trimmed(self):
        self.write_body(make_body(course="  康复医学概论\u3000"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("课程名称：康复医学概论", out)
        self.assertIn(
            "courses/专业课/医学院/康复治疗学/康复医学概论/笔记", out
        )

    def test_bare_major_resolves_unique(self):
        body = make_body(major="康复治疗学")
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["major"], "康复治疗学")

    def test_blank_college_is_derived_from_unique_major(self):
        body = make_body(college="_No response_", major="康复治疗学")
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["college_source"], "derived")
        self.assertEqual(form["major"], "康复治疗学")

    def test_blank_college_with_duplicate_major_fails_with_both_colleges(self):
        for college in ("商学院", "国际教育学院"):
            self.add_course(college, "经济学", "宏观经济学")
        body = make_body(
            college="_No response_", major="经济学", course="宏观经济学"
        )
        with self.assertRaises(imp.ImportFailure) as ctx:
            imp._parse_form("", body)
        message = str(ctx.exception)
        self.assertIn("专业「经济学」在多个学院都有", message)
        self.assertIn("商学院·经济学", message)
        self.assertIn("国际教育学院·经济学", message)

    def test_pure_major_name_locates_course_in_its_college(self):
        self.add_course("医学院", "护理学", "人体解剖学")
        body = make_body(college="医学院", major="护理学", course="人体解剖学")
        form = imp._parse_form("", body)
        self.assertEqual((form["college"], form["major"]), ("医学院", "护理学"))

        self.write_body(body)
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn(
            "courses/专业课/医学院/护理学/人体解剖学/笔记", out
        )

    def test_duplicate_major_is_scoped_to_each_requested_college(self):
        for college in ("商学院", "国际教育学院"):
            self.add_course(college, "经济学", "宏观经济学")
        for college in ("商学院", "国际教育学院"):
            with self.subTest(college=college):
                body = make_body(
                    college=college, major="经济学", course="宏观经济学"
                )
                form = imp._parse_form("", body)
                self.assertEqual(form["college"], college)
                self.assertEqual(form["major"], "经济学")
                self.write_body(body)
                code, out, _ = self.run_main("--dry-run")
                self.assertEqual(code, 0, out)
                self.assertIn(
                    "courses/专业课/{}/经济学/宏观经济学/笔记".format(college),
                    out,
                )

    def test_wrong_college_reports_the_actual_college(self):
        self.add_course("文学院", "汉语言文学", "现代汉语")
        self.write_body(
            make_body(
                college="文学院", major="康复治疗学", course="康复医学概论"
            )
        )
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("在学院「文学院」下找不到专业「康复治疗学」", out)
        self.assertIn("专业「康复治疗学」实际属于：医学院·康复治疗学", out)
        self.assertIn("请把「开课学院」改成「医学院」", out)

    def test_public_other_option_uses_course_manual_field(self):
        self.add_public_course("高等数学")
        body = make_body(
            category="公共课",
            college="_No response_",
            major="_No response_",
            course=imp.OTHER_OPTION,
            course_manual="高等数学",
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["category"], "公共课")
        self.assertEqual(form["course"], "高等数学")

        self.write_body(body)
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("courses/公共课/高等数学/笔记", out)

    def test_public_other_option_without_manual_course_fails_clearly(self):
        body = make_body(
            category="公共课",
            college="_No response_",
            major="_No response_",
            course=imp.OTHER_OPTION,
            course_manual="_No response_",
        )
        with self.assertRaises(imp.ImportFailure) as ctx:
            imp._parse_form("", body)
        message = str(ctx.exception)
        self.assertIn("课程名称选择了「其他（在下面写）」", message)
        self.assertIn("「课程名」为空", message)

    def test_public_concrete_dropdown_wins_over_stale_manual_text(self):
        body = make_body(
            category="公共课",
            college="_No response_",
            major="_No response_",
            course="高等数学",
            course_manual="马克思主义基本原理",
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["course"], "高等数学")

    def test_course_alias_falls_back_when_primary_field_is_blank(self):
        body = make_body()
        body = body.replace(
            "### 课程名称\n\n康复医学概论", "### 课程名称\n\n_No response_", 1
        )
        body = body.replace(
            "### 课程名\n\n_No response_", "### 课程名\n\n康复医学概论", 1
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["course"], "康复医学概论")

    def test_title_prefix_and_shell_text_do_not_affect_parsing(self):
        self.add_course("医学院", "护理学", "人体解剖学")
        body = make_body(college="医学院", major="护理学", course="人体解剖学")
        marker = self.root / "pwn-marker"
        title = "[上传] $(touch {}) `id` ' \"".format(marker)
        form = imp._parse_form(title, body)
        self.assertEqual(form["course"], "人体解剖学")
        self.assertFalse(
            marker.exists(),
            "解析 Issue 标题时不应执行其中的 shell 文本",
        )

    def test_dropdown_only_college_and_major(self):
        body = make_body()
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["college_source"], "dropdown")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["major_source"], "dropdown")

    def test_manual_college_and_major_with_other_option(self):
        body = make_body(
            college=imp.OTHER_OPTION,
            college_manual="医学院",
            major=imp.OTHER_OPTION,
            major_manual="康复治疗学",
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["college_source"], "manual")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["major_source"], "manual")

    def test_manual_major_accepts_full_college_separator(self):
        body = make_body(
            college=imp.OTHER_OPTION,
            college_manual="医学院",
            major=imp.OTHER_OPTION,
            major_manual="医学院 / 康复治疗学",
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["major_source"], "manual")

    def test_manual_values_keep_normalization_and_parenthetical(self):
        body = make_body(
            college=imp.OTHER_OPTION,
            college_manual="　医学院（附属医院）　",
            major=imp.OTHER_OPTION,
            major_manual="　医学院 ／ 康复治疗学　",
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["college_source"], "manual")

    def test_manual_same_as_dropdown_is_not_conflict(self):
        body = make_body(
            college="医学院",
            college_manual="医学院",
            major="医学院·康复治疗学",
            major_manual="医学院·康复治疗学",
        )
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院")
        self.assertEqual(form["major"], "康复治疗学")
        self.assertEqual(form["college_source"], "manual")

    def test_manual_overrides_different_dropdown(self):
        body = make_body(college="文学院", college_manual="医学院")
        form = imp._parse_form("", body)
        self.assertEqual(form["college"], "医学院", "补充框应优先于下拉框")
        self.assertEqual(form["college_source"], "manual")
        self.assertEqual(form["major"], "康复治疗学")

    def test_other_option_without_manual_college_fails(self):
        self.write_body(make_body(college=imp.OTHER_OPTION, college_manual=""))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("请在下方的「学院名称」里填写学院，或从下拉里选一个", out)

    def test_other_option_without_manual_major_fails(self):
        self.write_body(make_body(major=imp.OTHER_OPTION, major_manual=""))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("请在下方的「专业名称」里填写专业，或从下拉里选一个", out)

    def test_manual_college_typo_suggests_closest(self):
        self.write_body(
            make_body(college=imp.OTHER_OPTION, college_manual="医学完")
        )
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("找不到学院", out)
        self.assertIn("医学院", out)

    def test_meta_reports_manual_source_when_override(self):
        self.write_body(make_body(college="文学院", college_manual="医学院"))
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("IMPORT_META college=医学院", out)
        self.assertIn("IMPORT_META college_source=manual", out)
        self.assertIn("IMPORT_META major_source=dropdown", out)

    def test_major_accepts_slash_and_spaced_separator(self):
        for major in ("医学院/康复治疗学", "医学院 · 康复治疗学"):
            with self.subTest(major=major):
                form = imp._parse_form("", make_body(major=major))
                self.assertEqual(form["college"], "医学院")
                self.assertEqual(form["major"], "康复治疗学")

    def test_college_with_parenthetical_is_normalized(self):
        course = (
            self.courses
            / "专业课"
            / "计算机与信息技术学院"
            / "软件工程"
            / "软件工程导论"
        )
        for subdir in ("笔记", "课件", "试卷", "作业"):
            (course / subdir).mkdir(parents=True, exist_ok=True)
        (course / "README.md").write_text("# 软件工程导论\n", encoding="utf-8")

        self.write_body(
            make_body(
                college="计算机与信息技术学院（软件学院）",
                major="软件工程",
                course="软件工程导论",
            )
        )
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("开课学院：计算机与信息技术学院", out)
        self.assertIn(
            "courses/专业课/计算机与信息技术学院/软件工程/软件工程导论/笔记",
            out,
        )

    def test_illegal_major_component_is_rejected(self):
        for major in ("医学院·../逃逸", "医学院/../逃逸", "../康复治疗学"):
            with self.subTest(major=major):
                body = make_body(major=major)
                with self.assertRaises(imp.ImportFailure):
                    imp._parse_form("", body)

    # ---------- 类型映射 ----------

    def test_type_mapping(self):
        expected = {
            "笔记": "笔记",
            "课件": "课件",
            "试卷": "试卷",
            "作业": "作业",
            "实验报告": "作业",
            "复习资料": "试卷",
            "其他": "笔记",
        }
        for form_type, directory in expected.items():
            with self.subTest(form_type=form_type):
                self.assertEqual(imp.TYPE_TO_DIR[form_type], directory)
                self.assertEqual(
                    imp._parse_form("", make_body(material_type=form_type))["type"],
                    form_type,
                )
        self.assertEqual(imp.TYPE_TO_FILENAME["实验报告"], "实验报告")
        self.assertEqual(imp.TYPE_TO_FILENAME["复习资料"], "复习")
        self.assertEqual(imp.TYPE_TO_FILENAME["试卷"], "期末")

    # ---------- 重名 ----------

    def test_duplicate_filename_starts_at_2_then_3(self):
        target = self.course / "笔记"
        base = "2025秋-笔记-康复医学概论"
        (target / "{}.pdf".format(base)).write_bytes(b"old")
        reserved = set()

        first = imp._allocate_filename(target, base, "pdf", reserved)
        self.assertEqual(first, "{}-2.pdf".format(base))
        (target / first).write_bytes(b"second")

        second = imp._allocate_filename(target, base, "pdf", reserved)
        self.assertEqual(second, "{}-3.pdf".format(base))

    # ---------- 成功路径（本地附件冒充） ----------

    def test_success_path_with_local_attachment_and_no_overwrite(self):
        target = self.course / "笔记"
        first_source = self.make_source("我的笔记.pdf", b"%PDF-1.4\nfirst\n")
        second_source = self.make_source("另一个名字.pdf", b"%PDF-1.4\nsecond\n")

        code, out, err = self.run_main("--attachment-file", str(first_source))
        self.assertEqual(code, 0, out + err)
        self.assertIn("IMPORT_OK", out)
        self.assertIn("仅测试用", err)
        first = target / "2025秋-笔记-康复医学概论.pdf"
        self.assertTrue(first.is_file())
        self.assertEqual(first.read_bytes(), b"%PDF-1.4\nfirst\n")
        self.assertTrue(first_source.is_file(), "测试模式不能删除调用者的源文件")

        code, out, err = self.run_main("--attachment-file", str(second_source))
        self.assertEqual(code, 0, out + err)
        second = target / "2025秋-笔记-康复医学概论-2.pdf"
        self.assertTrue(second.is_file())
        self.assertEqual(second.read_bytes(), b"%PDF-1.4\nsecond\n")
        self.assertEqual(first.read_bytes(), b"%PDF-1.4\nfirst\n", "旧文件被覆盖了")

    def test_without_flag_still_uses_network_download_path(self):
        # 不传 --attachment-file 时必须走 _download_one，而不是本地冒充。
        with mock.patch.object(
            imp,
            "_download_one",
            side_effect=imp.ImportFailure("stub: 生产下载路径"),
        ) as download:
            code, out, _ = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("stub: 生产下载路径", out)
        download.assert_called_once()

    def test_attachment_file_count_must_match(self):
        source = self.make_source("我的笔记.pdf", b"%PDF-1.4\ntest\n")
        code, out, _ = self.run_main(
            "--attachment-file",
            str(source),
            "--attachment-file",
            str(source),
        )
        self.assertEqual(code, 2)
        self.assertIn("一一对应", out)

    def test_attachment_file_extension_whitelist_applies(self):
        source = self.make_source("恶意.exe", b"MZ")
        code, out, _ = self.run_main("--attachment-file", str(source))
        self.assertEqual(code, 2)
        self.assertIn("不支持的文件类型", out)

    def test_total_attachment_size_is_limited(self):
        first = self.make_source("a.pdf", b"1234")
        second = self.make_source("b.pdf", b"5678")
        self.write_body(
            make_body(
                attachments=(
                    "[a.pdf](https://github.com/user-attachments/assets/aaaa)\n\n"
                    "[b.pdf](https://github.com/user-attachments/assets/bbbb)"
                )
            )
        )
        with mock.patch.object(imp, "MAX_TOTAL_BYTES", 5):
            code, out, _ = self.run_main(
                "--attachment-file",
                str(first),
                "--attachment-file",
                str(second),
            )
        self.assertEqual(code, 2)
        self.assertIn("超过单个 PR", out)
        self.assertEqual(list((self.course / "笔记").iterdir()), [])

    # ---------- 下载安全 ----------

    def test_html_error_page_is_rejected(self):
        self.assertTrue(
            imp._looks_like_error_document(
                b"<!DOCTYPE html><html><body>not a pdf</body></html>",
                "application/octet-stream",
            )
        )
        self.assertTrue(imp._looks_like_error_document(b"", "text/html"))
        self.assertFalse(
            imp._looks_like_error_document(b"%PDF-1.4\nreal pdf\n", "application/pdf")
        )

    def test_download_404_and_html_are_friendly_failures(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/404"):
                    self.send_error(404)
                    return
                body = b"<!DOCTYPE html><html><body>error</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            temp_dir = Path(tempfile.mkdtemp(prefix="course-vault-download-"))
            try:
                with mock.patch.object(
                    imp, "_is_official_attachment", return_value=True
                ):
                    with self.assertRaises(imp.ImportFailure) as ctx:
                        imp._download_one(
                            {
                                "url": "http://127.0.0.1:{}/x.pdf".format(port),
                                "label": "x.pdf",
                            },
                            1,
                            temp_dir,
                        )
                    self.assertIn("HTML", str(ctx.exception))

                    with self.assertRaises(imp.ImportFailure) as ctx:
                        imp._download_one(
                            {
                                "url": "http://127.0.0.1:{}/404/x.pdf".format(port),
                                "label": "x.pdf",
                            },
                            2,
                            temp_dir,
                        )
                    self.assertIn("HTTP 404", str(ctx.exception))
                self.assertEqual(list(temp_dir.iterdir()), [])
            finally:
                import shutil

                shutil.rmtree(str(temp_dir), ignore_errors=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_attachment_urls_must_be_official_https(self):
        self.assertTrue(
            imp._is_official_attachment(
                "https://github.com/user-attachments/assets/abc"
            )
        )
        self.assertTrue(
            imp._is_official_attachment(
                "https://objects.githubusercontent.com/github-production-user-asset"
            )
        )
        self.assertFalse(imp._is_official_attachment("https://evil.example/x.pdf"))
        self.assertFalse(
            imp._is_official_attachment(
                "http://github.com/user-attachments/assets/abc"
            )
        )
        self.assertFalse(imp._is_official_attachment("https://github.com/login"))
        self.assertFalse(
            imp._is_official_attachment(
                "https://user:pass@github.com/user-attachments/assets/abc"
            )
        )

    def test_s3_asset_redirect_hosts(self):
        # GitHub 附件 302 后的真实落点（用户上传图片常见）。
        self.assertTrue(
            imp._is_official_attachment(
                "https://github-production-user-asset-6210df.s3.amazonaws.com/"
                "329902248/682241090-cc6a53af.jpg"
            )
        )
        self.assertTrue(
            imp._is_official_attachment(
                "https://github-production-release-asset-2e65be.s3.amazonaws.com/x/y.pdf"
            )
        )
        # 其他 S3 域名 / 伪装域名照旧拒绝。
        self.assertFalse(
            imp._is_official_attachment("https://s3.amazonaws.com/evil/x.pdf")
        )
        self.assertFalse(
            imp._is_official_attachment(
                "https://github-production-user-asset-x.s3.amazonaws.com.evil.com/x.jpg"
            )
        )
        self.assertFalse(
            imp._is_official_attachment(
                "http://github-production-user-asset-1.s3.amazonaws.com/x.jpg"
            )
        )

    def test_log_text_strips_control_characters(self):
        self.assertEqual(
            imp._safe_log_text("正常\n标题\r\x1b[31m"),
            "正常 标题  [31m",
        )

    def test_redirect_to_external_host_is_rejected(self):
        req = imp.request.Request(
            "https://github.com/user-attachments/assets/abc"
        )
        with self.assertRaises(imp.ImportFailure):
            imp._OfficialRedirectHandler().redirect_request(
                req, None, 302, "Found", {}, "https://evil.example/x.pdf"
            )

    # ---------- CLI ----------

    def test_help_marks_attachment_file_as_test_only(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as ctx:
                imp.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("仅测试用", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
