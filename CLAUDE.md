# CLAUDE.md

WhatsApp customer-support backbone: webhook → RabbitMQ pipeline → embeddings + Qdrant context → OpenAI draft → human reviewer approves via WhatsApp `/ok <session_id> <text>` → customer. Python 3.12, FastAPI, pika (sync), redis-py, Pydantic v2. ~800 LOC total; the whole package is small enough to read directly.

## Commands

```bash
# Tests (unittest; pytest is NOT installed in .venv, ~0.2s, no external services needed)
.venv/Scripts/python -m unittest discover -s tests
.venv/Scripts/python -m unittest tests.test_pipeline.PipelineTests.test_parse_approval_command

# Webhook locally
uvicorn serviceline_backbone.main:app --reload --port 8000

# A worker locally (roles listed below)
WORKER_ROLE=redis_aggregator python -c "from serviceline_backbone.main import run_worker; run_worker()"

# Full stack
docker-compose up -d
```

No linter/formatter/type-checker is configured. Dependencies in `pyproject.toml` + `uv.lock`.

## Layout (serviceline_backbone/)

| File | Contents |
|---|---|
| `config.py` | `Settings` frozen dataclass — every field reads an env var via `default_factory`; topic constants `wa.inbound`, `wa.aggregated`, `ai.inbound`, `ai.result` |
| `models.py` | Pydantic messages: `WhatsAppInboundMessage` → `AggregatedMessage` → `AIInboundMessage` (+`ContextItem`) → `AIResultMessage`; `PendingApproval` |
| `broker.py` | `RabbitMQBroker` (topic exchange + `<exchange>.dlx`; queues `<topic>.queue`, DLQ `<topic>.dlq`; 3 in-process retries w/ exponential backoff then nack→DLQ; publish is lock-guarded + one reconnect retry) and `InMemoryBroker` (synchronous fan-out, records `messages[topic]`) |
| `storage.py` | `RedisSessionStore` / `InMemorySessionStore`, `RedisApprovalStore` / `InMemoryApprovalStore` |
| `pipeline.py` | All business logic: `InboundService`, the 5 subscriber/worker classes, `EmbeddingClient`, `ContextRetriever`, `WhatsAppOutboundClient`, `ApprovalFlow`, approval-command parsing, `persist_inbound_to_file` |
| `webhook.py` | `build_app(inbound_service, webhook_secret)` — `POST /webhook/whatsapp`, `GET /health` |
| `main.py` | Wiring: module-level `app` (lazy, double-checked-lock service init) and `run_worker()` dispatching on `WORKER_ROLE` |
| `embeddings/` | Separate tiny FastAPI + sentence-transformers service (own Dockerfile, python 3.14); `/embed` takes `{"texts": [...]}` → `{"vectors": [...]}`; hardcodes `BAAI/bge-small-en-v1.5` and ignores the `model` field |

`config/system_prompt.txt` is re-read on every AI call (edit without restart).

## Pipeline & worker roles

1. **Webhook** → `InboundService.handle_message`. Reviewer sender (`== WHATSAPP_TARGET_ACCOUNT`): `/ok` command → `ApprovalFlow`, anything else → `"ignored"`. Customer: publish `wa.inbound` **first**, then `store.add_message` (dedupe on `message_id`). Returns status `accepted|duplicate|approved|approval_rejected|ignored`.
2. `wa_inbound_subscriber` → archives JSON to `STORAGE_ROOT/<sender>/YYYY/MM/DD/HHMMSS-<id>.json` (path components sanitized by `safe_path_component`).
3. `redis_aggregator` → loop `poll_once()` every 1s: claims expired sessions, joins texts with `\n`, publishes `wa.aggregated`, then `complete()`; on publish failure `release()`.
4. `wa_aggregated_subscriber` → embed + Qdrant `query_points` (score_threshold) → `ai.inbound`.
5. `ai_inbound_subscriber` → OpenAI chat completion → `ai.result`.
6. `ai_result_subscriber` → save `PendingApproval` in Redis, send formatted `[REVIEW REQUIRED]` message to reviewer.
7. Reviewer `/ok <session_id> <text>` → `ApprovalStore.pop` (Redis `GETDEL`) → send to original customer; on send failure the draft is re-saved and the exception propagates.

## Invariants / design decisions (don't break these)

- **Webhook fails closed**: no `WHATSAPP_WEBHOOK_SECRET` → 503 for everything; secret compared with `hmac.compare_digest`. Reason: `sender` is from the request body, so without auth anyone could impersonate the reviewer.
- Webhook handler is plain `def` (not `async`) on purpose — blocking I/O must run in FastAPI's threadpool. That's also why `RabbitMQBroker.publish` holds a `threading.Lock` (pika `BlockingConnection` isn't thread-safe).
- Publish-before-dedupe ordering in `InboundService` is deliberate (provider retries after a failed publish must not be treated as duplicates; re-archiving is idempotent).
- Redis session keys: `session:<sender>:messages` (list), `session:<sender>:id`, zset `sessions:expiry` (sliding TTL score), `seen:<message_id>` (24h dedupe), `approval:<session_id>`. `ZREM` is the claim (multi-aggregator safe). `complete()` uses `LTRIM` to keep messages that arrived after the claim. Session keys retained TTL+24h as a safety net.
- Session IDs are random `uuid4().hex` (unguessable, since they authorize approvals).
- `/ok` must match as a whole word (`/okay` is not a command).
- Heavy clients (`QdrantClient`, `OpenAI`, `pika`) are imported lazily inside constructors/methods so tests run without those services.
- In-memory stores must mirror Redis store semantics (sliding TTL, dedupe, claim/complete/release) — update both when changing one.

## Testing conventions

- Single file `tests/test_pipeline.py`, `unittest.TestCase`, uses `fastapi.testclient.TestClient`.
- Build settings with `make_settings(**overrides)` (`Settings(**kw)` overrides env defaults); fakes: `FakeEmbeddingClient`, `FakeRetriever`, `FakeSessionStore`, `FakeOutboundClient(fail=...)`, `FailingBroker`; plus `InMemoryBroker`/`InMemory*Store` from the package.
- Time is injected via `now_ts` params rather than mocked.
- `ContextRetriever` and `AIInboundSubscriber` have no unit tests (need Qdrant/OpenAI).

## Deployment (devops/)

SSH deploy to a Linux VM; full runbook in `devops/README.md`. `deploy.sh user@host [ref]` pipes `git archive <sha>` over ssh into `/opt/serviceline/releases/<sha>`, then runs `activate.sh` on the VM: symlinks `shared/.env`, `docker compose -p serviceline -f docker-compose.yml -f devops/docker-compose.prod.yml build && up -d`, curls `/health`, and only then moves the `current` symlink. `rollback.sh` re-activates an older release. `bootstrap-vm.sh` is one-time VM setup (Docker, log rotation, dirs). The prod override un-publishes infra ports (Docker bypasses ufw) and skips the missing rabbitmq-definitions import; it uses `!reset`/`!override`, so it needs Compose ≥ 2.24.4. Only committed code is deployed. `.gitattributes` forces LF on `*.sh`.

## Known gaps / gotchas

- `docker-compose.yml` mounts `./rabbitmq-definitions.json`, which is **not in the repo** — rabbitmq container startup needs it created.
- `.env.example` uses docker hostnames for some URLs (`rabbitmq`, `embeddings`) and localhost for others (`REDIS_URL`, `QDRANT_URL`) — adjust per environment. Embeddings: host port 8001, in-network `embeddings:8000`.
- Qdrant collection (`knowledge`) is never created/seeded by this code; vector size must match bge-small (384).
- `README.md`, `DEVELOPMENT.md`, `CODE_ANALYSIS.md` are long and partly AI-generated; trust the code over them (e.g. they mention pytest and an `:8080` embeddings URL).
- OS here is Windows; use `.venv/Scripts/python`.
