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


class GenFormsTest(unittest.TestCase):
    def setUp(self):
        self.templates = gen_forms._expected_templates()

    def fields_of(self, content):
        fields, problems = gen_forms._parse_template_fields(content)
        self.assertEqual(problems, [])
        return {field["id"]: field for field in fields}

    def test_all_generated_templates_pass_structure_check(self):
        self.assertEqual(len(self.templates), 25)
        self.assertEqual(gen_forms._validate_expected(self.templates), [])

    def test_college_templates_use_bare_major_names(self):
        college_count = 0
        for path, content in self.templates.items():
            if path.name == gen_forms.PUBLIC_TEMPLATE_NAME:
                continue
            college_count += 1
            college = path.stem[len(gen_forms.GENERATED_PREFIX) :]
            fields = self.fields_of(content)
            self.assertEqual(fields["college"]["value"], college)
            options = fields["major"]["options"]
            self.assertEqual(options, gen_forms._majors(college))
            for option in options:
                self.assertNotIn("·", option, path.name)
                self.assertNotIn("/", option, path.name)
        self.assertEqual(college_count, 24)

    def test_public_template_keeps_course_fallback(self):
        fields = self.fields_of(
            self.templates[
                gen_forms.TEMPLATE_DIR / gen_forms.PUBLIC_TEMPLATE_NAME
            ]
        )
        self.assertNotIn("college", fields)
        self.assertNotIn("major", fields)
        self.assertEqual(fields["category"]["options"], ["公共课"])
        self.assertEqual(fields["course"]["options"][-1], gen_forms.OTHER_OPTION)

    def test_structure_check_catches_misordered_fields(self):
        path = gen_forms.TEMPLATE_DIR / "上传资料-医学院.yml"
        content = self.templates[path]
        broken = content.replace("    id: college", "    id: wrong_id", 1)
        problems = gen_forms._validate_template(
            path.name,
            broken,
            college="医学院",
            majors=gen_forms._majors("医学院"),
        )
        self.assertTrue(problems)
        self.assertTrue(any("wrong_id" in item for item in problems))

    def test_structure_check_catches_duplicate_ids(self):
        path = gen_forms.TEMPLATE_DIR / "上传资料-医学院.yml"
        content = self.templates[path]
        broken = content.replace("    id: college", "    id: category", 1)
        problems = gen_forms._validate_template(
            path.name,
            broken,
            college="医学院",
            majors=gen_forms._majors("医学院"),
        )
        self.assertTrue(any("字段 id 重复" in item for item in problems), problems)

    def test_structure_check_catches_college_prefill_and_option_drift(self):
        path = gen_forms.TEMPLATE_DIR / "上传资料-医学院.yml"
        content = self.templates[path]
        majors = gen_forms._majors("医学院")

        wrong_value = content.replace('      value: "医学院"', '      value: "文学院"', 1)
        problems = gen_forms._validate_template(
            path.name, wrong_value, college="医学院", majors=majors
        )
        self.assertTrue(any("开课学院 value" in item for item in problems), problems)

        missing_option = content.replace('        - "护理学"\n', "", 1)
        problems = gen_forms._validate_template(
            path.name, missing_option, college="医学院", majors=majors
        )
        self.assertTrue(any("所属专业选项" in item for item in problems), problems)

    def test_structure_check_catches_year_material_and_default_drift(self):
        path = gen_forms.TEMPLATE_DIR / "上传资料-医学院.yml"
        content = self.templates[path]
        majors = gen_forms._majors("医学院")

        bad_year = content.replace('        - "2025秋"', '        - "2025夏"', 1)
        problems = gen_forms._validate_template(
            path.name, bad_year, college="医学院", majors=majors
        )
        self.assertTrue(any("年份学期" in item for item in problems), problems)

        bad_type = content.replace('        - "笔记"', '        - "笔记 "', 1)
        problems = gen_forms._validate_template(
            path.name, bad_type, college="医学院", majors=majors
        )
        self.assertTrue(any("资料类型" in item for item in problems), problems)

        bad_default = content.replace("      default: 0", "      default: 1", 1)
        problems = gen_forms._validate_template(
            path.name, bad_default, college="医学院", majors=majors
        )
        self.assertTrue(any("default: 0" in item for item in problems), problems)

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

    def test_structure_check_rejects_empty_and_prefixed_options(self):
        path = gen_forms.TEMPLATE_DIR / "上传资料-医学院.yml"
        content = self.templates[path]

        empty = gen_forms._validate_template(
            path.name, content, college="医学院", majors=()
        )
        self.assertTrue(any("没有专业目录" in item for item in empty), empty)

        prefixed = content.replace('        - "护理学"', '        - "医学院·护理学"', 1)
        problems = gen_forms._validate_template(
            path.name,
            prefixed,
            college="医学院",
            majors=gen_forms._majors("医学院"),
        )
        self.assertTrue(any("学院前缀" in item for item in problems), problems)

    def test_generator_quotes_parentheses_in_college_and_major_names(self):
        with tempfile.TemporaryDirectory(prefix="gen-forms-special-") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copy2(gen_forms.__file__, root / "scripts" / "gen_forms.py")
            (root / "courses" / "公共课" / "高等数学").mkdir(parents=True)
            (root / "courses" / "专业课" / "测试(学院)" / "专业(方向)").mkdir(
                parents=True
            )
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
            fields, problems = gen_forms._parse_template_fields(
                template.read_text(encoding="utf-8")
            )
            self.assertEqual(problems, [])
            by_id = {field["id"]: field for field in fields}
            self.assertEqual(by_id["college"]["value"], "测试(学院)")
            self.assertEqual(by_id["major"]["options"], ["专业(方向)"])

    def test_check_cli_detects_drift_and_recovers(self):
        with tempfile.TemporaryDirectory(prefix="gen-forms-cli-") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copy2(gen_forms.__file__, root / "scripts" / "gen_forms.py")
            (root / "courses" / "公共课" / "高等数学").mkdir(parents=True)
            (root / "courses" / "专业课" / "医学院" / "护理学").mkdir(
                parents=True
            )

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
            self.assertEqual(run("--check").returncode, 0)

            template = root / ".github" / "ISSUE_TEMPLATE" / "上传资料-医学院.yml"
            text = template.read_text(encoding="utf-8")
            template.write_text(
                text.replace('        - "护理学"', '        - "护理学（旧）"', 1),
                encoding="utf-8",
            )
            drifted = run("--check")
            self.assertEqual(drifted.returncode, 1, drifted.stdout + drifted.stderr)
            self.assertIn("FAIL", drifted.stdout)

            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--check").returncode, 0)

            # 目录里新增专业、模板没更新时，--check 也必须失败。
            (root / "courses" / "专业课" / "医学院" / "新增专业").mkdir()
            stale = run("--check")
            self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
            self.assertIn("DIFF", stale.stdout)

            self.assertEqual(run().returncode, 0)
            self.assertEqual(run("--check").returncode, 0)


if __name__ == "__main__":
    unittest.main()
