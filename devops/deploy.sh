#!/usr/bin/env bash
# Deploy a committed git ref to a remote Linux VM over SSH.
#
#   bash devops/deploy.sh user@host [git-ref]
#
# Env vars:
#   DEPLOY_PATH    remote base dir (default /opt/serviceline)
#   KEEP_RELEASES  releases to keep on the VM (default 5)
#   ENV_FILE       local file to upload as the remote shared/.env before deploying (optional)
#   SSH_OPTS       extra ssh options, e.g. "-p 2222 -i ~/.ssh/vm_key"
set -euo pipefail

TARGET="${1:?usage: bash devops/deploy.sh user@host [git-ref]}"
REF="${2:-HEAD}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/serviceline}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
read -r -a SSH_ARGS <<< "${SSH_OPTS:-}"

cd "$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short=12 "$REF^{commit}")"
RELEASE="$DEPLOY_PATH/releases/$SHA"

if [ "$REF" = "HEAD" ] && ! git diff --quiet HEAD; then
  echo "WARNING: uncommitted changes are NOT deployed (only commit $SHA is shipped)." >&2
fi

if [ -n "${ENV_FILE:-}" ]; then
  echo "==> Uploading $ENV_FILE to $TARGET:$DEPLOY_PATH/shared/.env"
  ssh "${SSH_ARGS[@]}" "$TARGET" "mkdir -p '$DEPLOY_PATH/shared' && umask 077 && cat > '$DEPLOY_PATH/shared/.env'" < "$ENV_FILE"
fi

echo "==> Shipping $SHA to $TARGET:$RELEASE"
git archive --format=tar "$SHA" | ssh "${SSH_ARGS[@]}" "$TARGET" "mkdir -p '$RELEASE' && tar -x -C '$RELEASE'"

echo "==> Activating $SHA"
ssh "${SSH_ARGS[@]}" "$TARGET" "bash '$RELEASE/devops/activate.sh' '$DEPLOY_PATH' '$SHA' '$KEEP_RELEASES'"
