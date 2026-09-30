# Development Guide

This guide helps you set up and develop the serviceline-backbone locally.

## Prerequisites

- Python 3.12+
- Docker & Docker Compose
- Git
- Redis CLI (optional but helpful): `brew install redis` or `apt install redis-tools`

## Local Setup

### 1. Clone & Install

```bash
git clone <repo>
cd serviceline-backbone

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e .
```

### 2. Configure Environment

```bash
# Copy template
cp .env.example .env

# Edit .env with:
# - Your OpenAI API key (required for testing)
# - Adjust localhost URLs for local services
nano .env
```

### 3. Start Dependencies

```bash
# Start Redis, RabbitMQ, Qdrant, and embedding service
docker-compose up -d redis rabbitmq qdrant

# Or start everything (includes webhook and workers)
docker-compose up -d

# Check services are running
docker-compose ps
```

### 4. Run Tests

```bash
# Run full test suite
pytest tests/ -v

# Run specific test
pytest tests/test_pipeline.py::PipelineTests::test_webhook_publishes_wa_inbound_and_stores_message -v

# Run with coverage
pytest tests/ --cov=serviceline_backbone --cov-report=html
```

## Development Workflow

### Running Locally (Python, Not Docker)

Terminal 1 - Start webhook:
```bash
uvicorn serviceline_backbone.main:app --reload --port 8000
```

Terminal 2 - Start wa_inbound_subscriber:
```bash
export WORKER_ROLE=wa_inbound_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"
```

Terminal 3 - Start redis_aggregator:
```bash
export WORKER_ROLE=redis_aggregator
python -c "from serviceline_backbone.main import run_worker; run_worker()"
```

Terminal 4 - Start wa_aggregated_subscriber:
```bash
export WORKER_ROLE=wa_aggregated_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"
```

Terminal 5 - Start ai_inbound_subscriber:
```bash
export WORKER_ROLE=ai_inbound_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"
```

Terminal 6 - Start ai_result_subscriber:
```bash
export WORKER_ROLE=ai_result_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"
```

### Testing the Webhook

```bash
# Send test message
curl -X POST http://localhost:8000/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "sender": "+15551234567",
    "text": "Hello test",
    "message_id": "test123",
    "timestamp": "2024-01-15T10:00:00Z"
  }'

# Check health
curl http://localhost:8000/health
```

### Inspecting Redis

```bash
redis-cli

# View sessions
KEYS "session:*"
LRANGE "session:+15551234567:messages" 0 -1

# View pending approvals
KEYS "approval:*"
GET "approval:session-123"

# Monitor all commands
MONITOR
```

### Inspecting RabbitMQ

- UI: http://localhost:15672 (user: guest, pass: guest)
- Check queues: `Queues` tab
- Monitor messages in flight
- View messages in queue (if needed for debugging)

### Code Style

```bash
# Format code with black
black serviceline_backbone/ tests/

# Check imports with isort
isort serviceline_backbone/ tests/

# Lint with ruff
ruff check serviceline_backbone/ tests/

# Type check with mypy (if configured)
mypy serviceline_backbone/
```

## Project Structure

```
serviceline-backbone/
├── serviceline_backbone/
│   ├── __init__.py
│   ├── main.py                 # App factory & worker entry point
│   ├── webhook.py              # FastAPI webhook handler
│   ├── config.py               # Settings & environment variables
│   ├── models.py               # Pydantic data models
│   ├── broker.py               # RabbitMQ & in-memory broker
│   ├── storage.py              # Redis & in-memory session/approval store
│   ├── pipeline.py             # Message processing pipeline stages
│   └── ...
├── config/
│   └── system_prompt.txt       # System prompt for OpenAI
├── tests/
│   └── test_pipeline.py        # Unit tests
├── docker-compose.yml          # Docker Compose setup
├── pyproject.toml              # Package metadata & dependencies
├── .env.example                # Configuration template
└── README.md
```

## Key Files to Understand

### 1. `pipeline.py` - The Core Logic

Contains all message processing stages:
- `EmbeddingClient` - Calls embedding service
- `ContextRetriever` - Searches Qdrant
- `WaInboundSubscriber` - Archives messages
- `RedisAggregationWorker` - Aggregates sessions
- `WaAggregatedSubscriber` - Enriches with context
- `AIInboundSubscriber` - Calls OpenAI
- `WhatsAppResultSubscriber` - Routes to reviewer
- `ApprovalFlow` - Handles reviewer approvals

### 2. `broker.py` - Message Routing

- `RabbitMQBroker` - Production RabbitMQ client
- `InMemoryBroker` - Test implementation
- Handles publish/subscribe to topics

### 3. `storage.py` - Session & Approval Tracking

- `RedisSessionStore` - Production session storage
- `InMemorySessionStore` - Test implementation
- `RedisApprovalStore` - Pending approval storage

## Adding a Feature

### Example: Add a new subscriber

1. Create handler class in `pipeline.py`:
```python
class MyNewSubscriber:
    def __init__(self, broker):
        self._broker = broker
    
    def handle(self, payload: dict) -> None:
        # Process message
        pass
```

2. Register in `main.py` under `run_worker()`:
```python
elif role == "my_new_subscriber":
    settings = Settings()
    broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
    handler = MyNewSubscriber(broker).handle
    broker.consume_forever("my.topic", handler)
    return
```

3. Add to `docker-compose.yml`:
```yaml
my-new-subscriber:
    build: .
    command: python -c "from serviceline_backbone.main import run_worker; run_worker()"
    environment:
      WORKER_ROLE: my_new_subscriber
    env_file:
      - .env
    depends_on:
      - rabbitmq
```

4. Add tests in `tests/test_pipeline.py`

## Debugging Tips

### Enable Debug Logging

```bash
export PYTHONUNBUFFERED=1
uvicorn serviceline_backbone.main:app --log-level debug --reload
```

### Use Python Debugger

```python
# In code
import pdb; pdb.set_trace()

# Or with breakpoint() in Python 3.7+
breakpoint()
```

### Check Message Flow

Monitor the topics in order:
```bash
# Terminal 1 - Watch wa.inbound queue
docker-compose exec rabbitmq rabbitmqctl list_consumers | grep wa.inbound

# Terminal 2 - Watch Redis sessions
watch -n 1 "redis-cli KEYS 'session:*'"

# Terminal 3 - Watch filesystem
watch -n 1 "find data/messages -type f -mmin -1"
```

### Simulate Slow Processing

Add delays to test TTL and aggregation:
```python
import time
time.sleep(5)  # 5 second delay
```

## Common Issues

### "ImportError: No module named 'serviceline_backbone'"

Install the package:
```bash
pip install -e .
```

### "Connection refused" for Redis/RabbitMQ

Check services are running:
```bash
docker-compose ps
docker-compose logs redis
docker-compose logs rabbitmq
```

### OpenAI API rate limited

- Check your API quota: https://platform.openai.com/account/rate-limits
- Use a model with lower rate limits (gpt-4o-mini instead of gpt-4)
- Implement exponential backoff in retry logic

### Tests fail with "Connection refused"

Tests use in-memory implementations, but if you see connection errors:
```bash
# Make sure external services aren't required
grep -r "redis.Redis\|pika\|QdrantClient" tests/
```

## Contributing

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make changes and test: `pytest tests/`
3. Format code: `black serviceline_backbone/`
4. Commit with clear message: `git commit -am "Add feature: ..."`
5. Push and open a PR

## Performance Testing

### Load Test with Apache Bench

```bash
# 1000 requests, 10 concurrent
ab -n 1000 -c 10 -p payload.json -T application/json http://localhost:8000/webhook/whatsapp
```

Create `payload.json`:
```json
{
  "sender": "+15551234567",
  "text": "Test message",
  "message_id": "test123",
  "timestamp": "2024-01-15T10:00:00Z"
}
```

### Monitor Performance

```bash
# CPU & Memory usage
docker stats

# RabbitMQ queue depth
docker-compose exec rabbitmq rabbitmqctl list_queues name messages

# Redis memory
redis-cli INFO memory
```

## Deployment Prep Checklist

- [ ] All tests passing: `pytest tests/ -v`
- [ ] Code formatted: `black .`
- [ ] No unused imports: `isort --check-only .`
- [ ] Environment variables documented in README
- [ ] `.env.example` updated with all vars
- [ ] Docker image builds: `docker-compose build`
- [ ] All services can communicate in Docker
- [ ] Health checks working: `curl http://localhost:8000/health`
- [ ] Error handling covers edge cases
- [ ] Logging is configured for production

## Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Pydantic Docs](https://docs.pydantic.dev/)
- [RabbitMQ Python Client](https://pika.readthedocs.io/)
- [Redis Python Client](https://redis-py.readthedocs.io/)
- [Qdrant Python Client](https://python-client.qdrant.tech/)
- [OpenAI Python Client](https://github.com/openai/openai-python)
