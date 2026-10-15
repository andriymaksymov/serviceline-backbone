# Deploying to a Linux VM over SSH

The VM runs the existing `docker-compose.yml` stack, plus `docker-compose.prod.yml` on top of it. The code reaches the VM as a **git commit** sent over SSH. It does not need rsync, a container registry, or a git checkout on the VM.

```
your machine                                   VM  (/opt/serviceline)
------------                                   -------------------------------
git archive <sha> | ssh ... tar -x   ───────►  releases/<sha>/        (one dir per deploy)
                                               shared/.env            (secrets, lives only on the VM)
ssh ... activate.sh                  ───────►  docker compose -p serviceline build && up -d
                                               curl /health ok  →  current -> releases/<sha>
```

- Only **committed** code is deployed, so every deploy matches an exact SHA.
- The compose project name is fixed (`-p serviceline`). The named volumes (RabbitMQ data, Qdrant storage, message archive) are therefore kept from one release to the next.
- If the health check fails, `current` keeps pointing at the last good release. You then roll back to it by hand with `rollback.sh` (see below).
- The last 5 releases are kept (`KEEP_RELEASES`).

## Requirements

- **Your machine:** `git`, `ssh` and `bash`. Git Bash on Windows works.
- **VM:** Ubuntu or Debian, SSH key login, and enough RAM for the embeddings model plus Qdrant and RabbitMQ (4 GB minimum, 8 GB is comfortable). The first build downloads PyTorch for the embeddings image, which is a few GB.

## 1. Prepare the VM (once)

```bash
scp devops/bootstrap-vm.sh user@host:/tmp/
ssh -t user@host sudo bash /tmp/bootstrap-vm.sh user        # installs Docker, log rotation, /opt/serviceline
```

Log out and back in so the `docker` group applies. Then open only SSH and the webhook port in the cloud firewall or security group: 22, and 8000 (or 443 if you put a TLS proxy in front, see below).

## 2. Create the production `.env` (once, and whenever secrets change)

Copy `.env.example` to `.env.production`. The `.gitignore` excludes it. Fill in the real secrets. Inside the compose network, services are reached **by service name**, not localhost:

```ini
RABBITMQ_DEFAULT_USER=serviceline
RABBITMQ_DEFAULT_PASS=<openssl rand -hex 24>
RABBITMQ_URL=amqp://serviceline:<same-password>@rabbitmq:5672/%2F
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
EMBEDDING_SERVICE_URL=http://embeddings:8000/embed
WHATSAPP_WEBHOOK_SECRET=<openssl rand -hex 32>
OPENAI_API_KEY=sk-...
WHATSAPP_OUTBOUND_URL=...
WHATSAPP_TARGET_ACCOUNT=+...
```

Pass it with `ENV_FILE` on the first deploy (step 3). The file is uploaded to `/opt/serviceline/shared/.env` with mode 600. Leave `ENV_FILE` out on later deploys to keep the file that is already on the VM.

## 3. Deploy

```bash
ENV_FILE=.env.production bash devops/deploy.sh user@host     # first time
bash devops/deploy.sh user@host                              # deploy HEAD
bash devops/deploy.sh user@host v1.2.0                       # deploy a tag/branch/sha
SSH_OPTS="-p 2222 -i ~/.ssh/vm_key" bash devops/deploy.sh user@host
```

## 4. Roll back

```bash
bash devops/rollback.sh user@host              # list releases on the VM, (current) marked
bash devops/rollback.sh user@host 1a2b3c4d5e6f # re-activate one
```

## Operating the stack

```bash
ssh user@host
cd /opt/serviceline/current
docker compose -p serviceline -f docker-compose.yml -f devops/docker-compose.prod.yml ps
docker compose -p serviceline -f docker-compose.yml -f devops/docker-compose.prod.yml logs -f ai-result-subscriber
```

What `docker-compose.prod.yml` changes compared with the dev compose file:
- **Redis, RabbitMQ, Qdrant and embeddings are no longer published on `0.0.0.0`.** Docker-published ports bypass ufw. Redis has no password. To reach the RabbitMQ UI or Qdrant (for example to seed the `knowledge` collection), open an SSH tunnel: `ssh -L 15672:localhost:15672 -L 6333:localhost:6333 user@host`.
- RabbitMQ starts with the image's default command. The dev compose file imports `rabbitmq-definitions.json`, which isn't in the repo. The app declares its own exchanges and queues.
- This needs Docker Compose 2.24.4 or later, which `bootstrap-vm.sh` installs.

## Recommended next steps

- **TLS:** WhatsApp and webhook providers expect HTTPS. Put Caddy or nginx in front of port 8000 (Caddy obtains Let's Encrypt certificates automatically). Then publish the webhook only on `127.0.0.1:8000`.
- **CI:** run the same `deploy.sh` from GitHub Actions on pushes to `main`, using an SSH deploy key stored as a secret.
- **Scaling beyond one VM:** build the images in CI, push them to a registry (for example GHCR), and have the VM run `docker compose pull`. This replaces building on the VM.
- **Backups:** back up the `serviceline_qdrant_storage` and `serviceline_message-archive` volumes.
