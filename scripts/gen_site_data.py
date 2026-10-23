#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 courses/ 生成 docs/data/courses.json，供 GitHub Pages 搜索页使用。

用法：
    python3 scripts/gen_site_data.py            # 重新生成
    python3 scripts/gen_site_data.py --check    # 只校验，不一致 exit 1
"""
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "docs" / "data" / "courses.json"


def scan():
    data = {"public": [], "colleges": []}
    pub = REPO_ROOT / "courses" / "公共课"
    if pub.is_dir():
        data["public"] = sorted(
            d.name for d in pub.iterdir() if d.is_dir() and not d.name.startswith(".")
        )
    base = REPO_ROOT / "courses" / "专业课"
    if base.is_dir():
        for col in sorted(base.iterdir()):
            if not col.is_dir() or col.name.startswith("."):
                continue
            majors = []
            for mj in sorted(col.iterdir()):
                if not mj.is_dir() or mj.name.startswith("."):
                    continue
                courses = sorted(
                    c.name for c in mj.iterdir()
                    if c.is_dir() and not c.name.startswith(".")
                )
                majors.append({"name": mj.name, "courses": courses})
            data["colleges"].append({"name": col.name, "majors": majors})
    data["counts"] = {
        "colleges": len(data["colleges"]),
        "majors": sum(len(c["majors"]) for c in data["colleges"]),
        "courses": sum(len(m["courses"]) for c in data["colleges"] for m in c["majors"])
        + len(data["public"]),
    }
    return data


def dump(data):
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main(argv):
    data = scan()
    text = dump(data)
    if "--check" in argv:
        if not OUT.exists():
            print("缺少 docs/data/courses.json，请运行 python3 scripts/gen_site_data.py")
            return 1
        if OUT.read_text(encoding="utf-8") != text:
            print("docs/data/courses.json 与目录结构不一致，请重新生成")
            return 1
        print("OK 站点数据与课程目录一致（%d 学院 / %d 专业 / %d 门课）"
              % (data["counts"]["colleges"], data["counts"]["majors"],
                 data["counts"]["courses"]))
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print("已生成 docs/data/courses.json：%d 学院 / %d 专业 / %d 门课"
          % (data["counts"]["colleges"], data["counts"]["majors"],
             data["counts"]["courses"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))