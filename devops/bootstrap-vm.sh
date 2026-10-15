#!/usr/bin/env bash
# One-time VM preparation (Ubuntu/Debian). Run as root ON THE VM:
#   scp devops/bootstrap-vm.sh user@host:/tmp/
#   ssh -t user@host sudo bash /tmp/bootstrap-vm.sh [deploy_user] [deploy_path]
set -euo pipefail

DEPLOY_USER="${1:-${SUDO_USER:-}}"
DEPLOY_PATH="${2:-/opt/serviceline}"

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
[ -n "$DEPLOY_USER" ] || { echo "Usage: bootstrap-vm.sh <deploy_user> [deploy_path]" >&2; exit 1; }
id "$DEPLOY_USER" >/dev/null 2>&1 || { echo "User $DEPLOY_USER does not exist." >&2; exit 1; }

echo "==> Installing base packages"
apt-get update -y
apt-get install -y ca-certificates curl tar

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker Engine + Compose plugin"
  curl -fsSL https://get.docker.com | sh
fi

# Container logs are unbounded by default and will eventually fill the disk.
if [ ! -f /etc/docker/daemon.json ]; then
  echo "==> Enabling Docker log rotation"
  cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
JSON
  systemctl restart docker
fi
systemctl enable --now docker

echo "==> Granting $DEPLOY_USER access to Docker"
usermod -aG docker "$DEPLOY_USER"

echo "==> Creating $DEPLOY_PATH"
mkdir -p "$DEPLOY_PATH/releases" "$DEPLOY_PATH/shared"
chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$DEPLOY_PATH"
chmod 700 "$DEPLOY_PATH/shared"

docker compose version
echo
echo "Done. Next (from your machine): upload the production .env and deploy, see devops/README.md."
echo "Note: $DEPLOY_USER must log out/in (new SSH session) for the docker group to apply."
