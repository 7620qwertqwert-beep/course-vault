#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 docs/data/search.json：课程树 + 每门课的实体资料清单，供搜索页使用。

只收真正的资料文件，.gitkeep / README.md 不进索引（它们不是资料）。
用法：
    python3 scripts/gen_search_index.py            # 重新生成
    python3 scripts/gen_search_index.py --check    # 只校验，不一致 exit 1
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "docs" / "data" / "search.json"
TYPES = ["笔记", "课件", "试卷", "作业", "实验报告", "复习资料"]
SKIP = {".gitkeep", "README.md"}


def files_of(course_dir):
    out = []
    for t in TYPES:
        d = course_dir / t
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.name in SKIP or f.name.startswith("."):
                continue
            st = f.stat()
            out.append({"t": t, "n": f.name, "s": st.st_size, "u": int(st.st_mtime)})
    return out


def scan():
    courses = []
    pub = REPO_ROOT / "courses" / "公共课"
    if pub.is_dir():
        for c in sorted(pub.iterdir()):
            if c.is_dir() and not c.name.startswith("."):
                courses.append({
                    "c": "公共课", "m": "", "n": c.name,
                    "p": "courses/公共课/" + c.name,
                    "f": files_of(c),
                })
    base = REPO_ROOT / "courses" / "专业课"
    if base.is_dir():
        for col in sorted(base.iterdir()):
            if not col.is_dir() or col.name.startswith("."):
                continue
            for mj in sorted(col.iterdir()):
                if not mj.is_dir() or mj.name.startswith("."):
                    continue
                for c in sorted(mj.iterdir()):
                    if not c.is_dir() or c.name.startswith("."):
                        continue
                    courses.append({
                        "c": col.name, "m": mj.name, "n": c.name,
                        "p": "courses/专业课/%s/%s/%s" % (col.name, mj.name, c.name),
                        "f": files_of(c),
                    })
    counts = {
        "colleges": len({c["c"] for c in courses if c["c"] != "公共课"}),
        "majors": len({(c["c"], c["m"]) for c in courses if c["m"]}),
        "courses": len(courses),
        "files": sum(len(c["f"]) for c in courses),
    }
    return {"counts": counts, "courses": courses}


def dump(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


def main(argv):
    data = scan()
    text = dump(data)
    if "--check" in argv:
        if not OUT.exists():
            print("缺少 docs/data/search.json，请运行 python3 scripts/gen_search_index.py")
            return 1
        if OUT.read_text(encoding="utf-8") != text:
            print("docs/data/search.json 与课程目录不一致，请重新生成")
            return 1
        print("OK 搜索索引一致（%d 门课 / %d 份资料）"
              % (data["counts"]["courses"], data["counts"]["files"]))
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print("已生成 docs/data/search.json：%d 门课 / %d 份资料（%.1f KB）"
          % (data["counts"]["courses"], data["counts"]["files"], len(text.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))