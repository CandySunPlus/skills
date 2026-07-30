#!/usr/bin/env bash
# 校验镜像目录与上次同步基线一致。
#
# 用途：
#   - .githooks/pre-commit 调用，拦住对镜像的手工修改
#   - CI 调用，兜住没装 hook 的协作者
#
# 不需要访问 Obsidian vault，因此在任何机器和 CI 上都能跑。这条性质要守住：
# 一旦本脚本需要 vault 配置，CI 就再也校验不了它。
#
# 它查不出什么：vault 里删掉或漏掉的文件。基线由同一次同步重写，所以那种缺失
# 对本脚本不可见，只在 sync-docs.sh 末尾打的 git status 里露头。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=mirror-common.sh
source "$SCRIPT_DIR/mirror-common.sh"

if [[ ! -f "$MANIFEST" ]]; then
  echo "缺少 ${MIRROR_MANIFEST}，无法校验镜像完整性。" >&2
  echo "请运行 $(basename "$SCRIPT_DIR")/sync-docs.sh 生成。" >&2
  exit 1
fi

declare -a BAD=()
declare -a MISSING=()

while IFS='  ' read -r expected relpath; do
  [[ -n "$relpath" ]] || continue
  f="$DEST/$relpath"
  if [[ ! -f "$f" ]]; then
    MISSING+=("$relpath")
  elif [[ "$(sha "$f")" != "$expected" ]]; then
    BAD+=("$relpath")
  fi
done < "$MANIFEST"

# 反向：基线里没有、目录里却存在的文件
declare -a EXTRA=()
while IFS= read -r -d '' f; do
  rel="${f#"$DEST"/}"
  grep -qF "  $rel" "$MANIFEST" || EXTRA+=("$rel")
done < <(find "$DEST" \( "${mirror_find_args[@]}" \) -type f -print0 2>/dev/null)

if (( ${#BAD[@]} == 0 && ${#MISSING[@]} == 0 && ${#EXTRA[@]} == 0 )); then
  exit 0
fi

cat >&2 <<EOF
$MIRROR_DEST/ 与同步基线不一致。

这个目录是 Obsidian 的只读镜像，不能在仓库内直接修改——
改动不会回流，并且会在下次同步时被覆盖。
EOF

(( ${#BAD[@]} ))     && { echo >&2; echo "内容被修改：" >&2; printf '  %s\n' "${BAD[@]}" >&2; }
(( ${#MISSING[@]} )) && { echo >&2; echo "文件缺失：" >&2;   printf '  %s\n' "${MISSING[@]}" >&2; }
(( ${#EXTRA[@]} ))   && { echo >&2; echo "多出文件：" >&2;   printf '  %s\n' "${EXTRA[@]}" >&2; }

cat >&2 <<EOF

怎么办：
  - 若改动有价值 → 把内容搬到 Obsidian vault 的对应文档，然后重跑 sync-docs.sh
  - 若是误改     → git checkout -- $MIRROR_DEST $MIRROR_MANIFEST
  - 若你刚同步过 → 确认 $MIRROR_MANIFEST 也一并提交了
  - 若同步刚报错中断 → 那时镜像已更新、基线还没写，所有文件都会报成「内容被修改」。
                       修好报错的原因重跑同步即可，不是有人手改了镜像。
EOF
exit 1
