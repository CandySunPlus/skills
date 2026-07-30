#!/usr/bin/env bash
# 把 Obsidian vault 里的一批文档单向同步到本仓库。
#
# 方向固定为 vault -> 仓库。仓库内的副本是只读镜像。
#
# 同步时会写入基线文件（每个文件的 sha256），作为"上次同步基线"。
# 有了它才能区分两种情况：
#   - vault 改了      -> 正常更新，直接覆盖
#   - 镜像被手工改了  -> 先备份再覆盖，并在报告中列出
# 该基线同时被 check-docs-mirror.sh 用于提交前与 CI 的校验。
#
# 仓库侧的取值在仓库根的 .mirror.conf（入 Git）：镜像装在哪、vault 的哪个子目录是
# 源、横幅模板。vault 根目录是本机路径，在 .mirror.conf.local（不入 Git）。
# 同名环境变量可以临时覆盖 MIRROR_VAULT_ROOT 与 MIRROR_VAULT_DOCS——临时指向另一个
# vault 时连本机配置都不必动。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=mirror-common.sh
source "$SCRIPT_DIR/mirror-common.sh"

# MIRROR_VAULT_DOCS 是完整路径，给了它就不看根目录与子路径。
VAULT_DOCS="${MIRROR_VAULT_DOCS:-}"
if [[ -z "$VAULT_DOCS" ]]; then
  if [[ -n "${MIRROR_VAULT_ROOT:-}" ]]; then
    VAULT_DOCS="${MIRROR_VAULT_ROOT%/}${MIRROR_VAULT_SUBPATH:+/$MIRROR_VAULT_SUBPATH}"
  else
    cat >&2 <<EOF
未配置 Obsidian vault 位置。

在 ${LOCAL_CONF} 里设 MIRROR_VAULT_ROOT，指向你这台机器上的 vault 根目录：

    echo 'MIRROR_VAULT_ROOT=/path/to/vault' > ${LOCAL_CONF}

这份文件不入 Git，每人一份。vault 里哪个子目录是源由 .mirror.conf 的
MIRROR_VAULT_SUBPATH 定，那是全项目一致的值，不在这儿改。

或临时用环境变量：

    MIRROR_VAULT_ROOT=/path/to/vault $0
EOF
    exit 1
  fi
fi

if [[ ! -d "$VAULT_DOCS" ]]; then
  echo "vault 里的源目录不存在：$VAULT_DOCS" >&2
  echo "检查 ${LOCAL_CONF} 里的 MIRROR_VAULT_ROOT（你这台机器上的 vault 根目录），" >&2
  echo "以及 $CONF 里的 MIRROR_VAULT_SUBPATH（vault 里哪个子目录是源）——" >&2
  echo "或者你这次用了 MIRROR_VAULT_ROOT / MIRROR_VAULT_DOCS 环境变量覆盖它们。" >&2
  echo "vault 侧的目录改了名，两处都要跟着改：SUBPATH 全项目共用，ROOT 只你自己用。" >&2
  exit 1
fi

# ---------------------------------------------------------------- 手改检测
# 与上次同步基线比对。差异说明镜像被就地改过——vault 侧的改动不会影响已同步文件的哈希。
declare -a TAMPERED=()
if [[ -f "$MANIFEST" ]]; then
  while IFS='  ' read -r expected relpath; do
    [[ -n "$relpath" ]] || continue
    local_file="$DEST/$relpath"
    [[ -f "$local_file" ]] || { TAMPERED+=("${relpath}（已被删除）"); continue; }
    [[ "$(sha "$local_file")" == "$expected" ]] || TAMPERED+=("$relpath")
  done < "$MANIFEST"
fi

if (( ${#TAMPERED[@]} > 0 )); then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  BACKUP="$REPO_ROOT/${MIRROR_BACKUP_PREFIX}$STAMP"
  echo "检测到镜像被就地修改（${#TAMPERED[@]} 个文件）。这些改动不会回流到 Obsidian，"
  echo "同步前先备份到 ${MIRROR_BACKUP_PREFIX}$STAMP/："
  for f in "${TAMPERED[@]}"; do
    echo "  $f"
    src="$DEST/${f%%（*}"
    [[ -f "$src" ]] || continue
    mkdir -p "$BACKUP/$(dirname "${f%%（*}")"
    cp "$src" "$BACKUP/${f%%（*}"
  done
  echo "  → 若这些改动有价值，请手工搬回 Obsidian，再重新同步。"
  echo
fi

# ---------------------------------------------------------------- 同步
# 只收 MIRROR_TYPES 列出的扩展名。多收一类就多一类「vault 里删了、镜像跟着被
# --delete 清掉」的东西，而那种删除三条校验都看不见（基线由同一次同步重写）。
#
# MIRROR_EXCLUDES 要显式列。靠"那个东西既不是目录也不是 .md，会被 --exclude='*'
# 兜住"是碰运气：往那儿放一篇 .md 就会跟着进仓库。vault 侧指回本仓库的软链尤其
# 要排除——rsync 一旦加了 -L，会顺着它把仓库自己抄进镜像。
mkdir -p "$DEST"
declare -a RSYNC_ARGS=(-a --delete --prune-empty-dirs)
for pattern in ${MIRROR_EXCLUDES[@]+"${MIRROR_EXCLUDES[@]}"}; do
  RSYNC_ARGS+=("--exclude=$pattern")
done
RSYNC_ARGS+=(--include='*/')
for type in $MIRROR_TYPES; do
  RSYNC_ARGS+=("--include=*.$type")
done
RSYNC_ARGS+=(--exclude='*')
rsync "${RSYNC_ARGS[@]}" "$VAULT_DOCS/" "$DEST/"

# ---------------------------------------------------------------- 注入只读横幅
# 让任何打开文件的人立刻知道这是镜像。横幅内容固定、不含机器相关路径，保证各机器
# 同步结果一致（基线依赖这一点）。位置在 YAML frontmatter 之后——插在最前面会让
# frontmatter 不再是 frontmatter。
#
# 模板里的 {{UP}} 会替换成「从当前文件所在目录回到镜像父目录」的相对前缀，
# 这样横幅里指向说明文档的链接在每一层子目录里都点得动。
if [[ -n "$MIRROR_BANNER" ]]; then
  BANNER_FILE="$REPO_ROOT/$MIRROR_BANNER"
  if [[ ! -f "$BANNER_FILE" ]]; then
    echo "横幅模板不存在：${BANNER_FILE}（.mirror.conf 的 MIRROR_BANNER）" >&2
    exit 1
  fi
  BANNER_TEMPLATE="$(cat "$BANNER_FILE")"

  # 横幅里 {{UP}} 打头的链接目标必须真的存在。{{UP}} 在镜像顶层展开成 ../，所以
  # ]({{UP}}x) 指向镜像父目录下的 x；更深的文件按构造指向同一个目标，查一次就够。
  #
  # 必须在这儿查：横幅指错的后果是每个文件顶部一条死链，而三道防线全绿——它们
  # 只比对镜像与基线，而死链是基线的一部分。默认模板假设说明文档在镜像的父目录，
  # 换个仓库很容易不成立。
  declare -a BANNER_TARGETS=()
  _rest="$BANNER_TEMPLATE"
  while [[ "$_rest" =~ \]\(\{\{UP\}\}([^\)]*)\) ]]; do
    BANNER_TARGETS+=("${BASH_REMATCH[1]}")
    _rest="${_rest#*"${BASH_REMATCH[0]}"}"
  done
  for target in ${BANNER_TARGETS[@]+"${BANNER_TARGETS[@]}"}; do
    if [[ ! -e "$DEST/../$target" ]]; then
      echo "横幅里的链接指向不存在的文件：{{UP}}$target" >&2
      echo "它会解析到镜像父目录下的 ${target}，那儿没有这个文件。" >&2
      echo "改 $MIRROR_BANNER 里的链接，或把说明文档放到镜像的父目录。" >&2
      exit 1
    fi
  done

  while IFS= read -r -d '' f; do
    tmp="$f.tmp$$"
    rel="${f#"$DEST"/}"
    depth="$(printf '%s' "${rel//[^\/]/}" | wc -c | tr -d ' ')"   # rel 里的斜杠数
    up=""
    for ((i = 0; i <= depth; i++)); do up+="../"; done
    banner="${BANNER_TEMPLATE//\{\{UP\}\}/$up}"
    first_line=""
    IFS= read -r first_line < "$f" || true
    fm_end=""
    if [[ "$first_line" == "---" ]]; then
      fm_end="$(awk 'NR>1 && $0=="---" {print NR; exit}' "$f")"
    fi
    if [[ -n "$fm_end" ]]; then
      {
        sed -n "1,${fm_end}p" "$f"
        printf '\n'
        printf '%s\n\n' "$banner"
        sed -n "$((fm_end + 1)),\$p" "$f"
      } > "$tmp"
    else
      { printf '%s\n\n' "$banner"; cat "$f"; } > "$tmp"
    fi
    mv "$tmp" "$f"
  done < <(find "$DEST" -name '*.md' -type f -print0)
fi

# ---------------------------------------------------------------- 改写 wikilink
# vault 里跨文引用写作 [[某文档]]，只有 Obsidian 认。镜像里改写成相对链接，
# GitHub 与各类编辑器才点得动。指向未同步笔记的链接原样保留 [[...]]。
#
# 必须在写基线之前——改写结果是基线的一部分，这样 check-docs-mirror.sh、
# pre-commit hook 与 CI 都不需要知道这一步存在。
if ! command -v python3 >/dev/null 2>&1; then
  echo "缺少 python3，无法改写 wikilink。装一个 python3 再同步。" >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "python3 版本过低：$(python3 -V 2>&1)，需要 3.12+。" >&2
  echo "改写用到 Path.relative_to(walk_up=True)，3.12 才有。" >&2
  exit 1
fi
if ! python3 "$SCRIPT_DIR/rewrite_wikilinks.py" "$DEST"; then
  echo >&2
  echo "wikilink 改写失败，同步中断。此刻镜像文件已更新，基线还没写——" >&2
  echo "check-docs-mirror.sh 会把所有文件报成「内容被修改」，那是这个中断状态的表象，" >&2
  echo "不是有人手改了镜像。修好上面的问题后重跑本脚本即可。" >&2
  exit 1
fi
echo

# ---------------------------------------------------------------- 写基线
: > "$MANIFEST"
while IFS= read -r -d '' f; do
  rel="${f#"$DEST"/}"
  printf '%s  %s\n' "$(sha "$f")" "$rel" >> "$MANIFEST"
done < <(find "$DEST" \( "${mirror_find_args[@]}" \) -type f -print0 | sort -z)

echo "已同步：$VAULT_DOCS"
echo "     → $DEST"
echo "基线：${MIRROR_MANIFEST}（$(wc -l < "$MANIFEST" | tr -d ' ') 个文件）"
echo

# 打出镜像目录的改动。删除只在这里看得见——基线由本次同步重写，所以
# check-docs-mirror.sh 与 shasum -c 对「vault 里少了东西」一律全绿。
# 出现 D 就要停下来确认那是有意的，别顺手连着提交。
git -C "$REPO_ROOT" status --short -- "$MIRROR_DEST" "$MIRROR_MANIFEST"
