#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gen_forms.py 的结构自检测试（标准库）。

运行：
    python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
