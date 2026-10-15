#!/usr/bin/env bash
# Deploy a committed git ref to a remote Linux VM over SSH.
#
#   bash devops/deploy.sh user@host [git-ref]
#
# Env vars:
#   DEPLOY_PATH    remote base dir (default /opt/serviceline)
#   KEEP_RELEASES  releases to keep on the VM (default 5)
#   ENV_FILE       local file to upload as the remote shared/.env before deploying (optional)
#   REMOTE_SUDO    1 = run the install/activate steps on the VM with sudo (default 0)
#   SSH_OPTS       extra ssh options, e.g. "-p 2222 -i ~/.ssh/vm_key"
set -euo pipefail

TARGET="${1:?usage: bash devops/deploy.sh user@host [git-ref]}"
REF="${2:-HEAD}"
DEPLOY_PATH="${DEPLOY_PATH:-/opt/serviceline}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
read -r -a SSH_ARGS <<< "${SSH_OPTS:-}"

SUDO=""
TTY_ARGS=()
if [ "${REMOTE_SUDO:-0}" = "1" ]; then
  SUDO="sudo"
  # A tty lets sudo prompt for a password; without one, sudo must be passwordless (NOPASSWD).
  [ -t 0 ] && TTY_ARGS=(-t)
fi

cd "$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short=12 "$REF^{commit}")"
RELEASE="$DEPLOY_PATH/releases/$SHA"
# Uploads land in a directory the SSH user can always write; the privileged step moves them into place.
STAGE="/tmp/serviceline-$SHA"

if [ "$REF" = "HEAD" ] && ! git diff --quiet HEAD; then
  echo "WARNING: uncommitted changes are NOT deployed (only commit $SHA is shipped)." >&2
fi

echo "==> Shipping $SHA to $TARGET:$STAGE"
git archive --format=tar "$SHA" | ssh "${SSH_ARGS[@]}" "$TARGET" "rm -rf '$STAGE' '$STAGE.env' && mkdir -p '$STAGE' && tar -x -C '$STAGE'"

if [ -n "${ENV_FILE:-}" ]; then
  echo "==> Uploading $ENV_FILE"
  ssh "${SSH_ARGS[@]}" "$TARGET" "umask 077 && cat > '$STAGE.env'" < "$ENV_FILE"
fi

echo "==> Installing and activating $SHA${SUDO:+ (sudo)}"
ssh "${SSH_ARGS[@]}" "${TTY_ARGS[@]}" "$TARGET" "set -e
$SUDO mkdir -p '$DEPLOY_PATH/releases' '$DEPLOY_PATH/shared'
if [ -f '$STAGE.env' ]; then $SUDO install -m 600 '$STAGE.env' '$DEPLOY_PATH/shared/.env'; rm -f '$STAGE.env'; fi
$SUDO rm -rf '$RELEASE'
$SUDO mv '$STAGE' '$RELEASE'
$SUDO bash '$RELEASE/devops/activate.sh' '$DEPLOY_PATH' '$SHA' '$KEEP_RELEASES'"
