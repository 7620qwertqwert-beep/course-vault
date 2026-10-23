#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gen_forms.py 的结构自检测试（标准库）。

运行：
    python3 -m unittest discover -s tests -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import gen_forms  # noqa: E402  (先调整 sys.path 再导入)


def write_generic(root):
    """在临时仓库里写一个与当前目录同步的最小通用兜底模板。"""
    colleges = sorted(
        child.name
        for child in (root / "courses" / "专业课").iterdir()
        if child.is_dir()
    )
    lines = [
        'name: "上传资料（通用：不知道选哪个学院就用这个）"',
        "body:",
        "  - type: dropdown",
        "    id: category",
        "    attributes:",
        "      label: 课程类别",
        "      options:",
        '        - "公共课"',
        '        - "专业课"',
        "    validations:",
        "      required: true",
        "",
        "  - type: dropdown",
        "    id: college",
        "    attributes:",
        "      label: 开课学院",
        "      options:",
    ]
    lines.extend('        - {}'.format(gen_forms._q(college)) for college in colleges)
    lines.extend(
        [
            "    validations:",
            "      required: false",
            "",
            "  - type: dropdown",
            "    id: major",
            "    attributes:",
            "      label: 所属专业",
            "      options:",
        ]
    )
    for college in colleges:
        for major in gen_forms._subdirs(
            root / "courses" / "专业课" / college
        ):
            lines.append(
                "        - {}".format(
                    gen_forms._q("{}·{}".format(college, major))
                )
            )
    lines.extend(
        [
            "    validations:",
            "      required: false",
            "",
        ]
    )
    template = root / ".github" / "ISSUE_TEMPLATE" / "上传资料.yml"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("\n".join(lines), encoding="utf-8")


class GenFormsTest(unittest.TestCase):
    def setUp(self):
        self.colleges = gen_forms._colleges()
        self.templates = gen_forms._expected_templates()
        self.public_path = gen_forms.TEMPLATE_DIR / gen_forms.PUBLIC_TEMPLATE_NAME

    def fields_of(self, content):
        fields, problems = gen_forms._parse_template_fields(content)
        self.assertEqual(problems, [])
        return {field["id"]: field for field in fields}

    def path_for(self, college):
        return gen_forms.TEMPLATE_DIR / gen_forms._college_template_filename(college)

    def test_all_generated_templates_pass_structure_check(self):
        self.assertEqual(len(self.colleges), 24)
        self.assertEqual(len(self.templates), 25, "应为 24 个学院模板 + 1 个公共课")
        self.assertEqual(gen_forms._validate_expected(self.templates), [])
        self.assertEqual(gen_forms._generic_problems(), [])
        self.assertEqual(
            len(list(gen_forms.TEMPLATE_DIR.glob("*.yml"))),
            26,
            "应为 24 个学院模板 + 1 个公共课 + 1 个通用兜底",
        )

    def test_college_templates_prefill_and_options(self):
        for college in self.colleges:
            with self.subTest(college=college):
                path = self.path_for(college)
                self.assertIn(path, self.templates)
                self.assertEqual(gen_forms._filename_problems(path), [])
                content = self.templates[path]
                fields = self.fields_of(content)
                self.assertEqual(
                    [
                        field["id"]
                        for field in gen_forms._parse_template_fields(content)[0]
                    ],
                    gen_forms.COLLEGE_FIELD_ORDER,
                )
                self.assertEqual(
                    'name: "上传资料 · {}"'.format(college), content.splitlines()[1]
                )
                self.assertEqual(fields["category"]["options"], ["专业课"])
                self.assertEqual(fields["category"]["default"], "0")
                self.assertEqual(fields["college"]["type"], "input")
                self.assertEqual(fields["college"]["value"], college)
                self.assertEqual(fields["major"]["type"], "dropdown")
                self.assertEqual(
                    fields["major"]["options"], gen_forms._majors(college)
                )
                self.assertEqual(fields["course"]["type"], "dropdown")
                self.assertEqual(
                    fields["course"]["options"],
                    gen_forms._college_course_options(college),
                )
                self.assertEqual(
                    fields["course"]["description"],
                    gen_forms.COLLEGE_COURSE_DESCRIPTION,
                )
                self.assertNotIn(
                    gen_forms.OTHER_OPTION, fields["course"]["options"]
                )
                self.assertNotIn(
                    gen_forms.OTHER_OPTION, fields["major"]["options"]
                )
                for field in fields.values():
                    self.assertIs(field["required"], True, field["id"])

    def test_college_course_options_sorted_and_formatted(self):
        for college in self.colleges:
            with self.subTest(college=college):
                majors = gen_forms._majors(college)
                expected = [
                    "{} · {}".format(major, course)
                    for major in majors
                    for course in gen_forms._courses(college, major)
                ]
                self.assertEqual(
                    gen_forms._college_course_options(college), expected
                )
                for option in expected:
                    major, separator, course = option.partition(" · ")
                    self.assertEqual(separator, " · ", option)
                    self.assertIn(major, majors)
                    self.assertTrue(course.strip(), option)

    def test_every_college_has_majors_but_empty_majors_add_no_course_options(self):
        for college in self.colleges:
            majors = gen_forms._majors(college)
            self.assertTrue(majors, college)
            options = gen_forms._college_course_options(college)
            real_courses = sum(
                len(gen_forms._courses(college, major)) for major in majors
            )
            self.assertEqual(len(options), real_courses)

    def test_public_template_course_dropdown(self):
        fields = self.fields_of(self.templates[self.public_path])
        self.assertNotIn("college", fields)
        self.assertNotIn("major", fields)
        self.assertNotIn("course_manual", fields)
        self.assertEqual(
            [
                field["id"]
                for field in gen_forms._parse_template_fields(
                    self.templates[self.public_path]
                )[0]
            ],
            gen_forms.PUBLIC_FIELD_ORDER,
        )
        self.assertEqual(fields["category"]["options"], ["公共课"])
        self.assertEqual(fields["course"]["type"], "dropdown")
        self.assertEqual(fields["course"]["options"], gen_forms._public_courses())
        self.assertEqual(len(fields["course"]["options"]), 20)
        self.assertNotIn(gen_forms.OTHER_OPTION, fields["course"]["options"])

    def test_structure_check_catches_misordered_fields(self):
        college = self.colleges[0]
        path = self.path_for(college)
        content = self.templates[path]
        broken = content.replace("    id: college", "    id: wrong_id", 1)
        problems = gen_forms._validate_template(
            path.name,
            broken,
            college=college,
            majors=gen_forms._majors(college),
            courses=gen_forms._college_course_options(college),
        )
        self.assertTrue(problems)
        self.assertTrue(any("wrong_id" in item for item in problems))

    def test_structure_check_catches_duplicate_ids(self):
        college = self.colleges[0]
        path = self.path_for(college)
        content = self.templates[path]
        broken = content.replace("    id: college", "    id: category", 1)
        problems = gen_forms._validate_template(
            path.name,
            broken,
            college=college,
            majors=gen_forms._majors(college),
            courses=gen_forms._college_course_options(college),
        )
        self.assertTrue(any("字段 id 重复" in item for item in problems), problems)

    def test_structure_check_catches_prefill_and_option_drift(self):
        college = next(
            item for item in self.colleges if gen_forms._majors(item)
        )
        path = self.path_for(college)
        content = self.templates[path]
        majors = gen_forms._majors(college)
        courses = gen_forms._college_course_options(college)
        kwargs = {"college": college, "majors": majors, "courses": courses}

        wrong_college = content.replace(
            '      value: "{}"'.format(college),
            '      value: "不存在的学院"',
            1,
        )
        problems = gen_forms._validate_template(path.name, wrong_college, **kwargs)
        self.assertTrue(any("开课学院 value" in item for item in problems), problems)

        missing_major = content.replace(
            '        - "{}"\n'.format(majors[0]), "", 1
        )
        problems = gen_forms._validate_template(path.name, missing_major, **kwargs)
        self.assertTrue(
            any("所属专业下拉与真实专业目录不一致" in item for item in problems),
            problems,
        )

        if courses:
            missing_option = content.replace(
                '        - "{}"\n'.format(courses[0]), "", 1
            )
            problems = gen_forms._validate_template(
                path.name, missing_option, **kwargs
            )
            self.assertTrue(
                any("课程名称下拉与真实课程目录不一致" in item for item in problems),
                problems,
            )

            foreign_option = content.replace(
                '        - "{}"\n'.format(courses[0]),
                '        - "{}"\n        - "别的专业的课"\n'.format(courses[0]),
                1,
            )
            problems = gen_forms._validate_template(
                path.name, foreign_option, **kwargs
            )
            self.assertTrue(
                any(
                    "课程名称下拉与真实课程目录不一致" in item
                    for item in problems
                ),
                problems,
            )

            other_option = content.replace(
                '        - "{}"\n'.format(courses[0]),
                '        - "{}"\n        - "其他（在下面写）"\n'.format(
                    courses[0]
                ),
                1,
            )
            problems = gen_forms._validate_template(
                path.name, other_option, **kwargs
            )
            self.assertTrue(
                any("不应包含「其他（在下面写）」" in item for item in problems),
                problems,
            )

            wrong_prefix = content.replace(
                courses[0],
                "不存在的专业 · {}".format(courses[0].split(" · ", 1)[-1]),
                1,
            )
            problems = gen_forms._validate_template(
                path.name, wrong_prefix, **kwargs
            )
            self.assertTrue(
                any("专业前缀" in item for item in problems), problems
            )

    def test_structure_check_catches_description_year_material_and_default_drift(self):
        college = self.colleges[0]
        path = self.path_for(college)
        content = self.templates[path]
        majors = gen_forms._majors(college)
        courses = gen_forms._college_course_options(college)
        kwargs = {"college": college, "majors": majors, "courses": courses}

        bad_description = content.replace(
            gen_forms.COLLEGE_COURSE_DESCRIPTION,
            "随便写的说明",
            1,
        )
        problems = gen_forms._validate_template(
            path.name, bad_description, **kwargs
        )
        self.assertTrue(
            any("课程名称 description" in item for item in problems), problems
        )

        bad_year = content.replace(gen_forms.YEAR_DESCRIPTION, "随便写的年份说明", 1)
        problems = gen_forms._validate_template(path.name, bad_year, **kwargs)
        self.assertTrue(any("年份学期" in item for item in problems), problems)

        bad_type = content.replace('        - "笔记"', '        - "笔记 "', 1)
        problems = gen_forms._validate_template(path.name, bad_type, **kwargs)
        self.assertTrue(any("资料类型" in item for item in problems), problems)

        bad_default = content.replace("      default: 0", "      default: 1", 1)
        problems = gen_forms._validate_template(path.name, bad_default, **kwargs)
        self.assertTrue(any("default: 0" in item for item in problems), problems)

    def test_filename_rule_rejects_forbidden_characters(self):
        self.assertEqual(
            gen_forms._college_template_filename("测试学院"),
            "上传资料-测试学院.yml",
        )
        sanitized = gen_forms._college_template_filename("测试 学院/分部")
        self.assertEqual(sanitized, "上传资料-测试-学院-分部.yml")
        for name in (
            "上传资料-学院·专业.yml",
            "上传资料-学院 专业.yml",
            "上传资料-学院/专业.yml",
        ):
            problems = gen_forms._filename_problems(
                gen_forms.TEMPLATE_DIR / name
            )
            self.assertTrue(problems, name)

    def test_generic_template_options_match_courses_tree(self):
        generic = gen_forms.TEMPLATE_DIR / "上传资料.yml"
        fields, _problems = gen_forms._parse_template_fields(
            generic.read_text(encoding="utf-8")
        )
        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["college"]["options"], gen_forms._colleges())
        expected_majors = [
            "{}·{}".format(college, major)
            for college in gen_forms._colleges()
            for major in gen_forms._majors(college)
        ]
        self.assertEqual(by_id["major"]["options"], expected_majors)

    def test_generic_template_check_detects_stale_options(self):
        self.assertEqual(gen_forms._generic_problems(), [])
        with mock.patch.object(
            gen_forms, "_colleges", return_value=["不存在的学院"]
        ):
            problems = gen_forms._generic_problems()
        self.assertTrue(any("开课学院" in item for item in problems), problems)
        self.assertTrue(any("所属专业" in item for item in problems), problems)

    def test_generator_quotes_parentheses_in_college_and_course_names(self):
        with tempfile.TemporaryDirectory(prefix="gen-forms-special-") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copy2(gen_forms.__file__, root / "scripts" / "gen_forms.py")
            (root / "courses" / "公共课" / "高等数学").mkdir(parents=True)
            (
                root
                / "courses"
                / "专业课"
                / "测试(学院)"
                / "专业(方向)"
                / "课程(方向)"
            ).mkdir(parents=True)
            result = subprocess.run(
                [sys.executable, str(root / "scripts" / "gen_forms.py")],
                cwd=str(root),
                text=True,
                capture_output=True,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            template = (
                root
                / ".github"
                / "ISSUE_TEMPLATE"
                / "上传资料-测试(学院).yml"
            )
            self.assertTrue(template.is_file(), result.stdout)
            fields, problems = gen_forms._parse_template_fields(
                template.read_text(encoding="utf-8")
            )
            self.assertEqual(problems, [])
            by_id = {field["id"]: field for field in fields}
            self.assertEqual(by_id["college"]["value"], "测试(学院)")
            self.assertEqual(by_id["major"]["options"], ["专业(方向)"])
            self.assertEqual(
                by_id["course"]["options"], ["专业(方向) · 课程(方向)"]
            )

    def test_check_cli_detects_drift_and_recovers(self):
        with tempfile.TemporaryDirectory(prefix="gen-forms-cli-") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copy2(gen_forms.__file__, root / "scripts" / "gen_forms.py")
            (root / "courses" / "公共课" / "高等数学").mkdir(parents=True)
            (
                root / "courses" / "专业课" / "医学院" / "护理学" / "人体解剖学"
            ).mkdir(parents=True)
            write_generic(root)

            def run(*args):
                return subprocess.run(
                    [sys.executable, str(root / "scripts" / "gen_forms.py"), *args],
                    cwd=str(root),
                    text=True,
                    capture_output=True,
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                )

            generated = run()
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            self.assertIn(
                "学院模板 1 个 / 公共课 1 个 / 兜底 1 个；课程选项总数 2",
                generated.stdout,
            )
            self.assertEqual(run("--check").returncode, 0)

            template = (
                root
                / ".github"
                / "ISSUE_TEMPLATE"
                / "上传资料-医学院.yml"
            )
            text = template.read_text(encoding="utf-8")
            template.write_text(
                text.replace(
                    '        - "护理学 · 人体解剖学"',
                    '        - "护理学 · 人体解剖学（旧）"',
                    1,
                ),
                encoding="utf-8",
            )
            drifted = run("--check")
            self.assertEqual(drifted.returncode, 1, drifted.stdout + drifted.stderr)
            self.assertIn("DIFF 上传资料-医学院.yml", drifted.stdout)
            self.assertIn("FAIL", drifted.stdout)

            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--check").returncode, 0)

            # 目录里新增专业、模板没更新时，--check 也必须失败（学院模板变了）。
            (
                root / "courses" / "专业课" / "医学院" / "新增专业" / "新课程"
            ).mkdir(parents=True)
            write_generic(root)
            stale = run("--check")
            self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
            self.assertIn("DIFF 上传资料-医学院.yml", stale.stdout)

            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--check").returncode, 0)

            # 新增课程会改变该学院的课程下拉。
            (
                root / "courses" / "专业课" / "医学院" / "护理学" / "新课程"
            ).mkdir()
            stale = run("--check")
            self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
            self.assertIn("DIFF 上传资料-医学院.yml", stale.stdout)

            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--check").returncode, 0)

            # 旧粒度（按学院 + 专业）模板必须被清理。
            old = (
                root
                / ".github"
                / "ISSUE_TEMPLATE"
                / "上传资料-医学院-护理学.yml"
            )
            old.write_text(
                gen_forms.AUTO_HEADER + "\nname: \"旧模板\"\n", encoding="utf-8"
            )
            self.assertIn("removed 上传资料-医学院-护理学.yml", run().stdout)
            self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
