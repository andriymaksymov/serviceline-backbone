---
name: deploy
description: Deploy serviceline-backbone to the dev VM (dev@5.189.162.246) over SSH, roll back, or show status. Use only when the user runs /deploy.
argument-hint: "[git-ref] | rollback [sha] | status"
disable-model-invocation: true
---

# /deploy

Deploys to the dev VM with the scripts in `devops/` (see `devops/README.md` for how they work).

- Target: `dev@5.189.162.246`
- The `dev` user needs `sudo` on the VM, so **always** set `REMOTE_SUDO=1`.
- Run every command from the repo root in the Bash tool (Git Bash).

Arguments: `$ARGUMENTS`
- empty → deploy `HEAD`
- `<git-ref>` → deploy that branch/tag/sha
- `rollback` → list releases; `rollback <sha>` → re-activate that release
- `status` → show the running stack

## 0. Preflight (always)

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 dev@5.189.162.246 'sudo -n true && echo SUDO_OK || echo SUDO_NEEDS_PASSWORD'
```

- `Permission denied (publickey,password)`: key login isn't set up. Stop and ask the user to run this once in their own terminal: `! ssh-copy-id -i ~/.ssh/id_rsa.pub dev@5.189.162.246`. Then retry.
- `SUDO_NEEDS_PASSWORD`: you can't type the sudo password, because the Bash tool has no tty. Do steps 1–2 yourself. Then, instead of running step 3, give the user the exact command to run with the `!` prefix, e.g. `! REMOTE_SUDO=1 bash devops/deploy.sh dev@5.189.162.246 <ref>`. ssh then gets a tty and sudo prompts once. A permanent fix is a NOPASSWD sudoers rule for `dev`, but only suggest it; never set it up unprompted.
- `SUDO_OK`: run everything yourself.

## 1. Gate on tests

```bash
.venv/Scripts/python -m unittest discover -s tests
```

If anything fails, stop and report the failures. Do not deploy.

## 2. Check what will ship

Only **committed** code is deployed (`git archive`).

```bash
git status --short
git log -1 --oneline <ref or HEAD>
```

If there are uncommitted changes, tell the user they won't be included and ask whether to continue or commit first. Never commit on the user's behalf just to deploy. Also check that `devops/` exists in the commit being deployed (`git ls-tree <ref> devops/`). Older commits don't have it and can't be deployed with this flow.

## 3. Deploy

```bash
REMOTE_SUDO=1 bash devops/deploy.sh dev@5.189.162.246 <ref>
```

Use the Bash tool with a long timeout (600000 ms): the first build downloads PyTorch for the embeddings image.

- **First deploy, or the user wants to change secrets:** the VM needs `/opt/serviceline/shared/.env`. Ask the user which local file to use (e.g. `.env.production`) and add `ENV_FILE=<file>`. Never print, cat or summarize that file's contents. If `activate.sh` reports `Missing .../shared/.env`, this is the cause.
- **VM not bootstrapped** (`docker: command not found`, or Compose < 2.24.4): stop. Point the user to step 1 of `devops/README.md` (`bootstrap-vm.sh`, needs interactive sudo, so the user runs it with `!`).

## 4. Report

On success: the deployed SHA, the `compose ps` output summary, and the health result.

On failure (`webhook did not become healthy`):
1. Show the webhook log tail that the script printed.
2. Get more detail if needed:
   ```bash
   ssh dev@5.189.162.246 'cd /opt/serviceline/releases/<sha> && sudo docker compose -p serviceline -f docker-compose.yml -f devops/docker-compose.prod.yml logs --tail=80'
   ```
3. Common causes: a localhost URL in `.env` (the script warns about it), a missing `RABBITMQ_DEFAULT_PASS` or `WHATSAPP_WEBHOOK_SECRET`, a wrong `RABBITMQ_URL` credential.
4. Offer a rollback to the release marked `(current)`, which is the last healthy one. Don't run the rollback without the user's OK.

## rollback

```bash
REMOTE_SUDO=1 bash devops/rollback.sh dev@5.189.162.246          # list
REMOTE_SUDO=1 bash devops/rollback.sh dev@5.189.162.246 <sha>    # activate (confirm the sha with the user first)
```

## status

```bash
ssh dev@5.189.162.246 'readlink /opt/serviceline/current; cd /opt/serviceline/current && sudo docker compose -p serviceline -f docker-compose.yml -f devops/docker-compose.prod.yml ps; curl -fsS http://127.0.0.1:8000/health'
```
(If sudo needs a password, use the `!` route from step 0.)

## Rules

- Never print secrets from `.env` files, locally or on the VM.
- Don't change VM configuration (sudoers, firewall, Docker daemon) unless the user explicitly asks.
- Deploying is outward-facing. `/deploy` with no argument means "deploy HEAD", but a rollback or a non-default `ENV_FILE` upload needs an explicit confirmation.
