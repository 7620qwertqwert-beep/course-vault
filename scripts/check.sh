#!/usr/bin/env bash
# 上传前自检脚本：查目录结构 / 命名规范 / 体积 / 敏感词
# 用法： bash scripts/check.sh
#
# 说明：本脚本只扫描文本文件与目录结构；PDF/DOCX/PPTX 等二进制资料
# 无法通过本脚本判断内容，提交前请人工复核。
# 兼容 POSIX sh（可直接用 dash 运行），不使用进程替换等 bash 专有语法。

cd "$(dirname "$0")/.." || exit 1

bad=0
tmpdir="${TMPDIR:-/tmp}/course-vault-check.$$"
if ! mkdir -p "$tmpdir"; then
  echo "无法创建临时目录：$tmpdir" >&2
  exit 1
fi
trap 'rm -rf "$tmpdir"' 0 1 2 3 15

check_course_dir() {
  # 参数：课程目录。检查 README.md 与四个资料子目录。
  course_dir=$1
  if [ ! -f "$course_dir/README.md" ]; then
    echo "  ⚠ 缺课程卡片: $course_dir"
    bad=$((bad + 1))
  fi
  for subdir in 笔记 课件 试卷 作业; do
    if [ ! -d "$course_dir/$subdir" ]; then
      echo "  ⚠ 缺子目录: $course_dir/$subdir"
      bad=$((bad + 1))
    fi
  done
}

echo "== 1. 检查课程目录结构（README.md + 笔记/课件/试卷/作业） =="
if [ -d courses/公共课 ]; then
  find courses/公共课 -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort > "$tmpdir/public"
  while IFS= read -r course_dir; do
    [ -n "$course_dir" ] || continue
    check_course_dir "$course_dir"
  done < "$tmpdir/public"
fi
if [ -d courses/专业课 ]; then
  find courses/专业课 -mindepth 2 -maxdepth 2 -type d 2>/dev/null | sort > "$tmpdir/major"
  while IFS= read -r course_dir; do
    [ -n "$course_dir" ] || continue
    check_course_dir "$course_dir"
  done < "$tmpdir/major"
fi

echo "== 2. 检查文件名规范（应为 YYYY春/秋-类型-描述） =="
find courses -type f 2>/dev/null | sort > "$tmpdir/files"
while IFS= read -r file; do
  [ -n "$file" ] || continue
  base=${file##*/}
  case "$base" in
    README.md|.gitkeep) continue ;;
  esac
  # 除 README.md 与 .gitkeep 外，.md 笔记也必须遵守命名规范。
  if ! printf '%s\n' "$base" | grep -qE '^20[0-9]{2}(春|秋)-(期末|期中|笔记|作业|实验|课件|复习|答案)-'; then
    echo "  ⚠ 命名不规范: $file"
    bad=$((bad + 1))
  fi
done < "$tmpdir/files"

echo "== 3. 检查超过 50MB 的文件 =="
find courses -type f -size +50M 2>/dev/null | sort > "$tmpdir/big"
if [ -s "$tmpdir/big" ]; then
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    echo "  ⚠ 过大: $file"
    bad=$((bad + 1))
  done < "$tmpdir/big"
else
  echo "  [OK] 未发现超过 50MB 的文件"
fi

echo "== 4. 检查文本文件中的疑似个人信息 =="
find courses -type f \( -name '*.md' -o -name '*.txt' \) \
  -exec grep -lE '学号[:：][0-9]{6,}|身份证|[0-9]{17}[0-9Xx]' {} + \
  2>/dev/null | sort > "$tmpdir/privacy"
if [ -s "$tmpdir/privacy" ]; then
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    echo "  ⚠ 疑似含个人信息: $file"
    bad=$((bad + 1))
  done < "$tmpdir/privacy"
else
  echo "  [OK] 未在 .md/.txt 文本文件中发现明文个人信息"
fi

echo
echo "扫描范围：仅覆盖课程目录结构、文件名以及 .md/.txt 文本内容；"
echo "PDF/DOCX/PPTX 等二进制资料的内容需人工复核，本脚本不对其作结论。"
if [ "$bad" -eq 0 ]; then
  echo "[OK] 共 0 处问题（文本文件与目录结构范围内）。"
  exit 0
else
  echo "❌ 共 $bad 处问题"
  echo "请修复后再提交。"
  exit 1
fi
