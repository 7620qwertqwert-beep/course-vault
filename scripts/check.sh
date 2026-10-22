#!/usr/bin/env bash
# 上传前自检脚本：查命名规范 / 体积 / 敏感词
# 用法： bash scripts/check.sh

cd "$(dirname "$0")/.." || exit 1

bad=0
echo "== 1. 检查文件名规范（应为 YYYY秋/春-类型-描述） =="
while IFS= read -r f; do
  base=$(basename "$f")
  case "$base" in
    README.md|.gitkeep|*.md) continue ;;
  esac
  if ! echo "$base" | grep -qE '^20[0-9]{2}(春|秋)-'; then
    echo "  ⚠ 命名不规范: $f"
    bad=$((bad+1))
  fi
done < <(find courses -type f 2>/dev/null)

echo "== 2. 检查超过 50MB 的文件 =="
big=$(find courses -type f -size +50M 2>/dev/null)
if [ -n "$big" ]; then
  echo "$big" | sed 's/^/  ⚠ 过大: /'
  bad=$((bad+1))
else
  echo "  ✓ 无超大文件"
fi

echo "== 3. 检查敏感词（姓名/学号/身份证） =="
hit=$(find courses -type f \( -name '*.md' -o -name '*.txt' \) -exec grep -lE '学号[:：][0-9]{6,}|身份证|[0-9]{17}[0-9Xx]' {} + 2>/dev/null)
if [ -n "$hit" ]; then
  echo "$hit" | sed 's/^/  ⚠ 疑似含个人信息: /'
  bad=$((bad+1))
else
  echo "  ✓ 未发现明文个人信息"
fi

echo "== 4. 检查空课程目录 =="
find courses -mindepth 2 -maxdepth 2 -type d | while read -r d; do
  if [ ! -f "$d/README.md" ]; then echo "  ⚠ 缺课程卡片: $d"; fi
done

echo
if [ "$bad" -eq 0 ]; then
  echo "✅ 全部通过，可以提交。"
else
  echo "❌ 有 $bad 处需要修，改完再提交。"
  exit 1
fi