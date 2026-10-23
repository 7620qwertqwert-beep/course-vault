#!/bin/sh
# 上传前自检脚本：查目录结构 / 命名规范 / 体积 / 敏感词
# 用法： bash scripts/check.sh [--root <仓库根目录>]
#
# 默认扫描脚本自身所在的仓库；CI 从仓库外运行可信副本时用 --root 指定
# 待检查的工作树。也可用环境变量 REPO_ROOT 指定仓库根。
#
# 说明：本脚本只扫描文本文件与目录结构；PDF/DOCX/PPTX 等二进制资料
# 无法通过本脚本判断内容，提交前请人工复核。
# 兼容 POSIX sh（可直接用 dash 运行），不使用进程替换等 bash 专有语法。
#
# 课程层级：
#   courses/公共课/<课程>/
#   courses/专业课/<学院>/<专业>/<课程>/

usage() {
  echo "用法： $0 [--root <仓库根目录>]" >&2
  echo "  默认使用脚本所在仓库的根目录；也可用 REPO_ROOT 环境变量指定。" >&2
}

root_override=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        echo "错误：--root 需要一个非空目录参数。" >&2
        usage
        exit 2
      fi
      root_override=$2
      shift 2
      ;;
    --root=*)
      root_override=${1#--root=}
      if [ -z "$root_override" ]; then
        echo "错误：--root 需要一个非空目录参数。" >&2
        usage
        exit 2
      fi
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "错误：未知参数：$1" >&2
      usage
      exit 2
      ;;
  esac
done

if [ -n "$root_override" ]; then
  REPO_ROOT=$root_override
elif [ -n "${REPO_ROOT:-}" ]; then
  : # 使用环境变量中的仓库根
else
  script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd) || exit 1
  REPO_ROOT=$(CDPATH= cd "$script_dir/.." && pwd) || exit 1
fi

if [ ! -d "$REPO_ROOT" ]; then
  echo "错误：仓库根目录不存在：$REPO_ROOT" >&2
  exit 2
fi
REPO_ROOT=$(CDPATH= cd "$REPO_ROOT" && pwd) || exit 1
if [ ! -d "$REPO_ROOT/courses" ]; then
  echo "错误：$REPO_ROOT 下没有 courses/ 目录，不像是本仓库根目录。" >&2
  exit 2
fi

cd "$REPO_ROOT" || exit 1

bad=0
tmpdir="${TMPDIR:-/tmp}/course-vault-check.$$"
if ! mkdir -p "$tmpdir"; then
  echo "无法创建临时目录：$tmpdir" >&2
  exit 1
fi
trap 'rm -rf "$tmpdir"' 0 1 2 3 15

check_course_dir() {
  # 参数：课程目录。检查 README.md 与六个资料子目录。
  course_dir=$1
  if [ ! -f "$course_dir/README.md" ]; then
    echo "  ⚠ 缺课程卡片: $course_dir"
    bad=$((bad + 1))
  fi
  for subdir in 笔记 课件 试卷 作业 实验报告 复习资料; do
    if [ ! -d "$course_dir/$subdir" ]; then
      echo "  ⚠ 缺子目录: $course_dir/$subdir"
      bad=$((bad + 1))
    fi
  done
}

echo "== 1. 检查课程目录结构（README.md + 笔记/课件/试卷/作业/实验报告/复习资料） =="

# 公共课：courses/公共课/<课程>/
: > "$tmpdir/public"
if [ -d courses/公共课 ]; then
  find courses/公共课 -mindepth 1 -maxdepth 1 -type d ! -path '*/.*' 2>/dev/null | sort > "$tmpdir/public"
  while IFS= read -r course_dir; do
    [ -n "$course_dir" ] || continue
    check_course_dir "$course_dir"
  done < "$tmpdir/public"
fi

# 专业课：courses/专业课/<学院>/<专业>/<课程>/
: > "$tmpdir/professional"
if [ -d courses/专业课 ]; then
  # 先找旧的三层结构（专业课/<专业>/<课程>），它落在 depth 2；
  # 自身含 README.md 或任一资料子目录，即视为层级残留。
  : > "$tmpdir/legacy"
  find courses/专业课 -mindepth 2 -maxdepth 2 -type d ! -path '*/.*' 2>/dev/null | sort > "$tmpdir/legacy_raw"
  while IFS= read -r legacy_dir; do
    [ -n "$legacy_dir" ] || continue
    legacy_hit=0
    if [ -f "$legacy_dir/README.md" ]; then
      legacy_hit=1
    fi
    for subdir in 笔记 课件 试卷 作业 实验报告 复习资料; do
      if [ -d "$legacy_dir/$subdir" ]; then
        legacy_hit=1
      fi
    done
    if [ "$legacy_hit" -eq 1 ]; then
      echo "  ⚠ 专业课层级错误（应为 学院/专业/课程）: $legacy_dir"
      bad=$((bad + 1))
      printf '%s\n' "$legacy_dir" >> "$tmpdir/legacy"
    fi
  done < "$tmpdir/legacy_raw"

  # 再扫新结构的课程；旧课程目录内部的「笔记/课件/...」不是课程，跳过以免噪声。
  find courses/专业课 -mindepth 3 -maxdepth 3 -type d ! -path '*/.*' 2>/dev/null | sort > "$tmpdir/professional_raw"
  while IFS= read -r course_dir; do
    [ -n "$course_dir" ] || continue
    under_legacy=0
    while IFS= read -r legacy_dir; do
      [ -n "$legacy_dir" ] || continue
      case "$course_dir" in
        "$legacy_dir"/*) under_legacy=1; break ;;
      esac
    done < "$tmpdir/legacy"
    if [ "$under_legacy" -eq 1 ]; then
      continue
    fi
    printf '%s\n' "$course_dir" >> "$tmpdir/professional"
    check_course_dir "$course_dir"
  done < "$tmpdir/professional_raw"
fi

public_count=$(wc -l < "$tmpdir/public" | tr -d ' ')
professional_count=$(wc -l < "$tmpdir/professional" | tr -d ' ')
echo "  课程数：公共课 ${public_count} 门，专业课 ${professional_count} 门。"

echo "== 2. 检查文件名规范（应为 YYYY春/秋-类型-描述） =="
find courses -type f 2>/dev/null | sort > "$tmpdir/files"
while IFS= read -r file; do
  [ -n "$file" ] || continue
  base=${file##*/}
  # README.md 与 .gitkeep 是固定文件；备份/临时/系统文件不应按资料命名规范误报。
  case "$base" in
    README.md|.gitkeep|*.bak|*.bak-*|*.tmp|.DS_Store|Thumbs.db|desktop.ini|'~$'*) continue ;;
  esac
  # 除 README.md 与 .gitkeep 外，.md 笔记也必须遵守命名规范。
  if ! printf '%s\n' "$base" | grep -qE '^20[0-9]{2}(春|秋)-(期末|期中|笔记|作业|实验报告|实验|课件|复习|答案)-'; then
    echo "  ⚠ 命名不规范: $file"
    bad=$((bad + 1))
  fi

  # 文件名里的类型词必须与所在资料子目录一致，避免“实验报告”放进 作业/。
  # 这里独立于上面的命名规范检查：即使文件名格式有误，只要类型词可识别，
  # 也会指出目录放错（不新增致命退出逻辑）。
  rest=${base#*-}
  type_word=${rest%%-*}
  case "$type_word" in
    期末|期中|试卷|复习|答案) allowed_dirs="试卷 复习资料" ;;
    笔记) allowed_dirs="笔记" ;;
    作业) allowed_dirs="作业" ;;
    实验报告|实验) allowed_dirs="实验报告" ;;
    课件) allowed_dirs="课件" ;;
    *) allowed_dirs="" ;;
  esac
  if [ -n "$allowed_dirs" ]; then
    parent=${file%/*}
    subdir=${parent##*/}
    case " $allowed_dirs " in
      *" $subdir "*) ;;
      *)
        echo "  ⚠ 类型与目录不一致: $file（文件名类型「$type_word」，应放在 $allowed_dirs）"
        bad=$((bad + 1))
        ;;
    esac
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
