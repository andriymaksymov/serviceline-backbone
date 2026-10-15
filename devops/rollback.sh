#!/usr/bin/env bash
# Re-activate a release that is still on the VM.
#
#   bash devops/rollback.sh user@host          # list releases (newest first)
#   bash devops/rollback.sh user@host <sha>    # activate that release
#
# Uses the same DEPLOY_PATH / KEEP_RELEASES / SSH_OPTS env vars as deploy.sh.
set -euo pipefail

TARGET="${1:?usage: bash devops/rollback.sh user@host [sha]}"
SHA="${2:-}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/serviceline}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
read -r -a SSH_ARGS <<< "${SSH_OPTS:-}"

if [ -z "$SHA" ]; then
  ssh "${SSH_ARGS[@]}" "$TARGET" "cd '$DEPLOY_PATH/releases' && current=\$(basename \"\$(readlink ../current)\" 2>/dev/null); for r in \$(ls -1t); do [ \"\$r\" = \"\$current\" ] && echo \"\$r  (current)\" || echo \"\$r\"; done"
  exit 0
fi

ssh "${SSH_ARGS[@]}" "$TARGET" "bash '$DEPLOY_PATH/releases/$SHA/devops/activate.sh' '$DEPLOY_PATH' '$SHA' '$KEEP_RELEASES'"
