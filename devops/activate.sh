#!/usr/bin/env bash
# Runs ON THE VM. Builds and starts one release, health-checks it, then points `current` at it.
# Called by deploy.sh and rollback.sh; can also be run by hand:
#   bash /opt/serviceline/releases/<sha>/devops/activate.sh /opt/serviceline <sha> [keep_releases]
set -euo pipefail

DEPLOY_PATH="$1"
SHA="$2"
KEEP_RELEASES="${3:-5}"

RELEASE="$DEPLOY_PATH/releases/$SHA"
SHARED_ENV="$DEPLOY_PATH/shared/.env"
HEALTH_URL="http://127.0.0.1:8000/health"

[ -d "$RELEASE" ] || { echo "Release not found: $RELEASE" >&2; exit 1; }
[ -f "$SHARED_ENV" ] || { echo "Missing $SHARED_ENV - upload it first (see devops/README.md)" >&2; exit 1; }

# Inside the compose network, services are reached by name, not localhost.
if grep -Eq '^(REDIS_URL|RABBITMQ_URL|QDRANT_URL|EMBEDDING_SERVICE_URL)=.*(localhost|127\.0\.0\.1)' "$SHARED_ENV"; then
  echo "WARNING: $SHARED_ENV points a service URL at localhost; containers need redis/rabbitmq/qdrant/embeddings hostnames." >&2
fi

ln -sfn "$SHARED_ENV" "$RELEASE/.env"
cd "$RELEASE"

# Fixed project name so named volumes (rabbitmq_data, qdrant_storage, message-archive) survive across releases.
compose() { docker compose -p serviceline -f docker-compose.yml -f devops/docker-compose.prod.yml "$@"; }

echo "==> Building $SHA"
compose build
echo "==> Starting $SHA"
compose up -d --remove-orphans

echo "==> Waiting for $HEALTH_URL"
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    ln -sfn "$RELEASE" "$DEPLOY_PATH/current"
    echo "==> $SHA is healthy and now current"
    compose ps

    # Prune old releases (never the current one) and dangling images.
    cd "$DEPLOY_PATH/releases"
    ls -1t | tail -n +"$((KEEP_RELEASES + 1))" | while read -r old; do
      [ "$old" = "$SHA" ] || rm -rf -- "$old"
    done
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done

echo "ERROR: webhook did not become healthy; recent logs:" >&2
compose logs --tail=50 webhook >&2 || true
previous="$(readlink "$DEPLOY_PATH/current" 2>/dev/null || true)"
echo "The 'current' symlink was not changed (still: ${previous:-none}). Roll back with devops/rollback.sh." >&2
exit 1
