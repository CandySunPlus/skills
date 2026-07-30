#!/usr/bin/env bash
# 镜像机制的项目相关取值与共用工具，由 sync-docs.sh 与 check-docs-mirror.sh 共同读取。
#
# 收成一份是因为两个脚本都要算镜像目录、基线路径与文件哈希。各写一遍必然分叉，
# 而分叉的症状是校验与同步对「哪些文件算镜像内容」的看法不一致——那时候校验会
# 稳定地报告成功或稳定地报假警，两种都比没有校验坏。
#
# 调用方负责先设好 REPO_ROOT，然后 source 本文件。

CONF="$REPO_ROOT/.mirror.conf"
if [[ ! -f "$CONF" ]]; then
  cat >&2 <<EOF
缺少 ${CONF}。

这份配置说明镜像装在哪、vault 的哪个子目录是源、横幅用哪个模板。
没有它就无法判断哪些文件属于镜像，所以不猜、直接停。

从 vault-docs skill 的 templates/mirror.conf 复制一份改掉即可。
EOF
  exit 1
fi

# vault 位置允许用同名环境变量临时覆盖，所以要先记下环境里的值——source 会把
# 配置里的赋值盖在环境变量上，顺序反了覆盖就静默失效（写着能覆盖、实际不能）。
_env_vault_root="${MIRROR_VAULT_ROOT:-}"
_env_vault_docs="${MIRROR_VAULT_DOCS:-}"

# shellcheck disable=SC1090
source "$CONF"

if [[ -n "$_env_vault_root" ]]; then
  MIRROR_VAULT_ROOT="$_env_vault_root"
fi
if [[ -n "$_env_vault_docs" ]]; then
  MIRROR_VAULT_DOCS="$_env_vault_docs"
fi
unset _env_vault_root _env_vault_docs

# 没有能通用的默认值：猜错的后果是往错的地方写。
: "${MIRROR_DEST:?$CONF 缺 MIRROR_DEST}"

# 基线跟镜像同名加后缀。分开配置只会多一处可以写错的地方。
MIRROR_MANIFEST="${MIRROR_MANIFEST:-$MIRROR_DEST.manifest}"

# 进镜像的扩展名。默认只有 Markdown——图片与二进制留存是可选的，
# 而多收一类文件就多一类「vault 里删了、镜像跟着被 --delete 清掉」的东西。
MIRROR_TYPES="${MIRROR_TYPES:-md}"

MIRROR_BANNER="${MIRROR_BANNER:-}"

# 排除项写成数组（`MIRROR_EXCLUDES=(/开发)`），目录名带空格才不会被重新分词。
if [[ -z ${MIRROR_EXCLUDES+x} ]]; then
  MIRROR_EXCLUDES=()
fi

DEST="$REPO_ROOT/$MIRROR_DEST"
MANIFEST="$REPO_ROOT/$MIRROR_MANIFEST"

# 手改备份放在镜像的兄弟位置，名字带上镜像目录名，一眼看得出是谁的备份。
MIRROR_BACKUP_PREFIX="${MIRROR_BACKUP_PREFIX:-$(dirname "$MIRROR_DEST")/.$(basename "$MIRROR_DEST")-手改备份-}"

# shasum 在 macOS 上有，sha256sum 在多数 Linux / CI 镜像上有。
if command -v shasum >/dev/null 2>&1; then
  sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  sha() { sha256sum "$1" | cut -d' ' -f1; }
fi

# 供 find 用的 -name '*.md' -o -name '*.png' 这一串，调用方自己套括号。
# 用数组传而不是拼字符串：拼出来的那串会被重新分词，扩展名一旦带特殊字符就散。
mirror_find_args=()
for _type in $MIRROR_TYPES; do
  (( ${#mirror_find_args[@]} )) && mirror_find_args+=(-o)
  mirror_find_args+=(-name "*.$_type")
done
unset _type
