# serviceline-backbone

Minimal backbone implementation for a WhatsApp customer-support serviceline.

## Implemented message flow

1. WhatsApp webhook receives incoming messages (`/webhook/whatsapp`).
2. Webhook stores each message in Redis session storage and publishes to RabbitMQ topic `wa.inbound`.
3. `wa.inbound` subscriber stores each message on filesystem by sender/date.
4. Redis aggregation worker emits one aggregated message to `wa.aggregated` when session TTL expires.
5. `wa.aggregated` subscriber generates embeddings (`BAAI/bge-small-en-v1.5` via embedding service API), queries Qdrant with threshold `CONFIDENCE_THRESHOLD` (default 0.35), then publishes to `ai.inbound`.
6. `ai.inbound` subscriber enriches with `config/system_prompt.txt`, calls OpenAI API, then publishes to `ai.result`.
7. `ai.result` subscriber forwards answer to configured WhatsApp account.

## Topics

- `wa.inbound`
- `wa.aggregated`
- `ai.inbound`
- `ai.result`

## Run (local)

```bash
python -m pip install .
uvicorn serviceline_backbone.main:app --reload
```

Workers are started by setting `WORKER_ROLE` and running:

```bash
python -c "from serviceline_backbone.main import run_worker; run_worker()"
```

Supported roles:
- `wa_inbound_subscriber`
- `redis_aggregator`
- `wa_aggregated_subscriber`
- `ai_inbound_subscriber`
- `ai_result_subscriber`

## Docker

Use `docker-compose.yml` to run Redis, RabbitMQ, Qdrant, webhook, and all workers.
