# serviceline-backbone

Minimal backbone implementation for a WhatsApp customer-support serviceline with AI-powered responses and human review workflow.

## Features

- 🔄 **Async message processing** using RabbitMQ topic-based routing
- 🧠 **AI-powered responses** with OpenAI integration
- 📍 **Context retrieval** from vector database (Qdrant)
- ✅ **Human-in-the-loop approval** workflow for quality control
- 💾 **Session aggregation** with Redis
- 🐳 **Docker-ready** with complete compose setup
- 📝 **Type-safe** with Pydantic models and type hints

## Architecture

```
WhatsApp Client
    │
    ↓
[Webhook] (/webhook/whatsapp)
    │
    ├─→ Session Storage (Redis)
    ├─→ wa.inbound (RabbitMQ)
    │
    ↓
[wa_inbound_subscriber]
    └─→ Filesystem storage (messages/sender/YYYY/MM/DD/)
    
    [redis_aggregator] (polls every 1s)
    └─→ wa.aggregated (when session TTL expires)
    
    ↓
[wa_aggregated_subscriber]
    ├─→ Embed text (embedding service API)
    ├─→ Search context (Qdrant)
    └─→ ai.inbound (RabbitMQ)
    
    ↓
[ai_inbound_subscriber]
    ├─→ Load system prompt
    ├─→ Call OpenAI API
    └─→ ai.result (RabbitMQ)
    
    ↓
[ai_result_subscriber]
    ├─→ Store pending approval (Redis)
    └─→ Send to reviewer (WhatsApp)
    
    Reviewer edits & sends: /ok <session_id> <approved_text>
    
    ↓
[Webhook] (approval route)
    ├─→ Parse approval command
    ├─→ Resolve original sender
    └─→ Forward approved text to customer
```

## Implemented Message Flow

1. **WhatsApp webhook** receives incoming messages at `/webhook/whatsapp`
   - Optionally validates `X-Webhook-Secret` header
   - Stores message in Redis session storage
   - Publishes to `wa.inbound` topic
   
2. **wa.inbound subscriber** receives message and:
   - Persists to filesystem organized by `sender/YYYY/MM/DD/`
   - Useful for audit trail and debugging
   
3. **Redis aggregation worker** (runs every 1 second):
   - Detects session TTL expiry
   - Aggregates all messages from session
   - Publishes to `wa.aggregated` topic
   
4. **wa.aggregated subscriber** processes aggregated message:
   - Generates embeddings via embedding service
   - Searches Qdrant vector DB for relevant context (threshold: 0.35 default)
   - Enriches message with context
   - Publishes to `ai.inbound` topic
   
5. **ai.inbound subscriber** generates AI response:
   - Loads system prompt from `config/system_prompt.txt`
   - Calls OpenAI API with system prompt + context + user message
   - Publishes result to `ai.result` topic
   
6. **ai.result subscriber** routes to human reviewer:
   - Stores pending approval in Redis
   - Sends formatted message to reviewer WhatsApp account
   - Includes `source_session_id` for tracking
   
7. **Reviewer approval** workflow:
   - Reviewer receives suggested answer + metadata
   - Edits if needed and sends: `/ok <source_session_id> <approved_text>`
   - Webhook parses approval command
   - Original customer receives approved text

## Prerequisites

### External Services
- **Redis** 7.x: Session storage and approval tracking
- **RabbitMQ** 3.x: Message broker with topic exchange
- **Qdrant** 1.9.x: Vector database for context retrieval
- **OpenAI API**: For LLM responses (requires API key)
- **Embedding Service**: HTTP API for generating embeddings (e.g., local instance or embedding service)
- **WhatsApp Business API**: For sending/receiving messages (requires Account ID and credentials)

### System Requirements
- Python 3.12+
- Docker & Docker Compose (for containerized setup)

## Configuration

All configuration uses environment variables. See `.env.example` for template.

### Essential Configuration

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `RABBITMQ_URL` | `amqp://localhost:5672/%2F` | Yes | RabbitMQ connection URL |
| `RABBITMQ_EXCHANGE` | `serviceline` | No | RabbitMQ exchange name for topics |
| `REDIS_URL` | `redis://localhost:6379/0` | Yes | Redis connection URL |
| `OPENAI_API_KEY` | `` | **Yes** | OpenAI API key for GPT access |
| `WHATSAPP_OUTBOUND_URL` | `` | Conditional | HTTP endpoint to send WhatsApp messages |
| `WHATSAPP_TARGET_ACCOUNT` | `` | Conditional | WhatsApp account to send reviews to (e.g., `+1234567890`) |

### Session & Approval Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_SESSION_TTL_SECONDS` | `30` | Session timeout before aggregation (in seconds) |
| `APPROVAL_TTL_SECONDS` | `86400` | How long approval decisions are cached (24 hours) |
| `APPROVAL_COMMAND_PREFIX` | `/ok` | Command prefix for reviewer approval |
| `WHATSAPP_WEBHOOK_SECRET` | `` | Optional security header for webhook validation |

### AI & Context Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model ID to use |
| `EMBEDDING_SERVICE_URL` | `http://embeddings:8080/embed` | Embedding service HTTP endpoint |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model name |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant vector DB endpoint |
| `QDRANT_COLLECTION` | `knowledge` | Qdrant collection name for context |
| `CONFIDENCE_THRESHOLD` | `0.35` | Minimum similarity score for context retrieval (0.0-1.0) |
| `SYSTEM_PROMPT_PATH` | `./config/system_prompt.txt` | Path to system prompt file |

### Storage Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_ROOT` | `./data/messages` | Root directory for message archive |

### Example .env File

```bash
# Core services
RABBITMQ_URL=amqp://user:password@rabbitmq:5672/%2F
RABBITMQ_EXCHANGE=serviceline
REDIS_URL=redis://redis:6379/0

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Embeddings
EMBEDDING_SERVICE_URL=http://embeddings:8080/embed
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

# Qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=knowledge
CONFIDENCE_THRESHOLD=0.35

# WhatsApp
WHATSAPP_OUTBOUND_URL=https://api.whatsapp.business/v21.0/...
WHATSAPP_TARGET_ACCOUNT=+15551234567
WHATSAPP_WEBHOOK_SECRET=your-secret-key-here

# Session management
REDIS_SESSION_TTL_SECONDS=30
APPROVAL_TTL_SECONDS=86400

# Storage
STORAGE_ROOT=./data/messages
SYSTEM_PROMPT_PATH=./config/system_prompt.txt
```

## API Documentation

### Webhook Endpoint

**POST `/webhook/whatsapp`**

Receives incoming WhatsApp messages and approval commands.

**Request Headers:**
```
Content-Type: application/json
X-Webhook-Secret: <value> (if WHATSAPP_WEBHOOK_SECRET is configured)
```

**Request Body (Incoming Message):**
```json
{
  "sender": "+15551234567",
  "text": "Hello, I need help with my order",
  "message_id": "wamid.xxxxx",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Request Body (Approval Command):**
```json
{
  "sender": "+15559876543",
  "text": "/ok session-1234-5678 Yes, your order has been shipped!",
  "message_id": "wamid.yyyyy",
  "timestamp": "2024-01-15T10:35:00Z"
}
```

**Response:**
```json
{
  "status": "accepted"  // or "approved" / "approval_rejected"
}
```

**Response Codes:**
- `200 OK` - Message successfully processed
- `401 Unauthorized` - Invalid webhook secret header
- `422 Unprocessable Entity` - Invalid message format

### Health Check Endpoint

**GET `/health`**

```json
{
  "status": "ok"
}
```

## Approval Workflow

The approval workflow allows a human reviewer to quality-check and approve (or edit) AI-generated responses before they reach the customer.

### Flow

1. **Customer sends message** to WhatsApp:
   ```
   User: "I need to return my order"
   ```

2. **System processes** message through pipeline (1-5 seconds)

3. **Reviewer receives** suggested answer:
   ```
   [REVIEW REQUIRED]
   source_session_id: session-abc-123
   original_sender: +15551234567
   
   Edit the answer if needed and send:
   /ok session-abc-123 <approved_or_edited_answer>
   
   Suggested answer:
   We can help you with a return. Please provide your order number.
   ```

4. **Reviewer approves or edits**:
   ```
   /ok session-abc-123 We can process your return. Please reply with your order number within 48 hours.
   ```

5. **Customer receives approved message**:
   ```
   We can process your return. Please reply with your order number within 48 hours.
   ```

### Approval Command Format

```
/ok <source_session_id> <approved_text>
```

- `source_session_id`: Session ID from review message (no spaces)
- `approved_text`: Approved/edited response text (can contain spaces)

**Examples:**
```
/ok session-1234 Thanks for choosing us!
/ok abc-def-ghi Your refund has been processed. Check your account in 3-5 business days.
```

### Approval Expiry

- Pending approvals expire after `APPROVAL_TTL_SECONDS` (default: 24 hours)
- If reviewer doesn't respond in time, the approval is discarded
- No message is sent to customer if approval expires

## Quick Start

### Local Development (Docker Compose)

```bash
# Clone and enter directory
git clone <repo>
cd serviceline-backbone

# Copy and configure environment
cp .env.example .env
# Edit .env with your OpenAI API key and WhatsApp credentials

# Start all services
docker-compose up -d

# Verify services are running
curl http://localhost:8000/health
```

Services available at:
- Webhook: `http://localhost:8000`
- RabbitMQ: `http://localhost:15672` (guest/guest)
- Redis: `localhost:6379`
- Qdrant: `http://localhost:6333`

### Local Development (Python)

```bash
# Install dependencies
python -m pip install -e .

# Start webhook server (one terminal)
uvicorn serviceline_backbone.main:app --reload --port 8000

# Start workers (separate terminals for each)
export WORKER_ROLE=wa_inbound_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"

export WORKER_ROLE=redis_aggregator
python -c "from serviceline_backbone.main import run_worker; run_worker()"

export WORKER_ROLE=wa_aggregated_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"

export WORKER_ROLE=ai_inbound_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"

export WORKER_ROLE=ai_result_subscriber
python -c "from serviceline_backbone.main import run_worker; run_worker()"
```

## Worker Roles

Each worker processes a specific stage of the pipeline:

| Role | Subscribes To | Publishes To | Function |
|------|---------------|--------------|----------|
| `wa_inbound_subscriber` | `wa.inbound` | (filesystem) | Archives messages to disk |
| `redis_aggregator` | (polling) | `wa.aggregated` | Aggregates sessions after TTL |
| `wa_aggregated_subscriber` | `wa.aggregated` | `ai.inbound` | Generates embeddings & retrieves context |
| `ai_inbound_subscriber` | `ai.inbound` | `ai.result` | Calls OpenAI API for response |
| `ai_result_subscriber` | `ai.result` | (WhatsApp) | Sends to reviewer for approval |

## Message Topics

Internal RabbitMQ topics used for async communication:

- **`wa.inbound`**: Incoming WhatsApp messages from webhook
- **`wa.aggregated`**: Session-aggregated messages ready for AI processing
- **`ai.inbound`**: Messages enriched with context, ready for LLM
- **`ai.result`**: AI-generated responses waiting for human approval

## Testing

Run the test suite:

```bash
python -m pytest tests/
```

Tests use in-memory implementations of Redis and RabbitMQ to avoid external dependencies.

## Logging & Debugging

### Set Log Level

```bash
# Via environment variable
export PYTHONUNBUFFERED=1
export LOG_LEVEL=DEBUG

# For uvicorn
uvicorn serviceline_backbone.main:app --log-level debug

# For Python
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from serviceline_backbone.main import run_worker
run_worker()
"
```

### Inspect Message Files

Messages are archived at `$STORAGE_ROOT/<sender>/YYYY/MM/DD/HHMMSS-message_id.json`:

```bash
# View all messages from a sender
find ./data/messages/+15551234567 -name "*.json" -exec cat {} \;

# Watch for new messages (Linux/Mac)
watch -n 1 "find ./data/messages -name '*.json' | sort -r | head -5"
```

### Debug RabbitMQ

Access RabbitMQ Management UI: `http://localhost:15672` (default: guest/guest)

- Monitor queues: `wa.inbound.queue`, `wa.aggregated.queue`, etc.
- Check for unacked messages (indicates processing errors)

### Debug Redis

```bash
# Connect to Redis
redis-cli

# Inspect session storage
KEYS "session:*"
GET "session:+15551234567:messages"

# Inspect pending approvals
KEYS "approval:*"
GET "approval:session-1234"
```

### Check Webhook Payload

```bash
# Test webhook with sample message
curl -X POST http://localhost:8000/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: test-secret" \
  -d '{
    "sender": "+15551234567",
    "text": "Hello, help me!",
    "message_id": "test123",
    "timestamp": "2024-01-15T10:00:00Z"
  }'
```

## Troubleshooting

### Issue: "Failed to connect to RabbitMQ"

**Cause**: RabbitMQ service not running or misconfigured URL

**Solutions**:
- Verify `RABBITMQ_URL` in `.env`
- Check RabbitMQ is running: `docker-compose logs rabbitmq`
- Verify credentials if using authentication
- Ensure RABBITMQ_URL format: `amqp://user:pass@host:5672/%2F`

### Issue: "No context found" for every query

**Cause**: Vector DB (Qdrant) is empty or `CONFIDENCE_THRESHOLD` too high

**Solutions**:
- Populate Qdrant with knowledge base (separate import process)
- Lower `CONFIDENCE_THRESHOLD` (e.g., 0.25 instead of 0.35)
- Verify Qdrant is running: `curl http://localhost:6333/health`

### Issue: Approvals not reaching customer

**Cause**: `WHATSAPP_OUTBOUND_URL` or `WHATSAPP_TARGET_ACCOUNT` not configured

**Solutions**:
- Set `WHATSAPP_TARGET_ACCOUNT` to reviewer's phone number
- Set `WHATSAPP_OUTBOUND_URL` to WhatsApp Business API endpoint
- Check logs: `docker-compose logs ai-result-subscriber`

### Issue: Messages stuck in queue

**Cause**: Worker crashed or exception during processing

**Solutions**:
- Check worker logs: `docker-compose logs wa-inbound-subscriber`
- Fix underlying issue (e.g., API key, service URL)
- Restart worker: `docker-compose restart wa-inbound-subscriber`
- Messages are automatically nacked and requeued

### Issue: Session aggregation not triggering

**Cause**: Redis aggregator not running or `REDIS_SESSION_TTL_SECONDS` too high

**Solutions**:
- Verify aggregator is running: `docker-compose ps redis-aggregator`
- Check logs: `docker-compose logs redis-aggregator`
- Check Redis connection: `redis-cli PING`
- Reduce `REDIS_SESSION_TTL_SECONDS` for testing

### Issue: "Embedding response is missing vector"

**Cause**: Embedding service returned unexpected format

**Solutions**:
- Verify `EMBEDDING_SERVICE_URL` is correct
- Check embedding service logs
- Ensure embedding service is running and responding
- Test embedding endpoint: `curl http://localhost:8080/embed -X POST -d '{"model":"...", "input":"test"}'`

## Performance & Scaling

### Capacity Planning

| Component | Bottleneck | Scaling Strategy |
|-----------|-----------|------------------|
| **Webhook** | Concurrent requests | Run multiple instances behind load balancer |
| **Aggregator** | Session TTL + polling | Lower poll interval or run multiple instances |
| **Subscribers** | Consumer group parallelism | Run multiple instances per role |
| **Redis** | Memory for sessions/approvals | Tune TTL values, consider Redis Cluster |
| **RabbitMQ** | Queue depth | Monitor queue lengths, scale workers horizontally |
| **OpenAI API** | Rate limits | Use batching, implement retry logic |

### Optimization Tips

- **Session TTL**: Lower values (5-15s) process faster but risk incomplete messages. Higher (60-120s) aggregate more but have latency.
- **Confidence Threshold**: Higher (0.5-0.7) limits context retrieval but improves relevance. Lower (0.1-0.3) gets more context.
- **Parallel Workers**: Run 2-3 instances of high-volume workers (wa_inbound_subscriber, ai_result_subscriber).
- **Embedding Caching**: Cache embeddings for common queries to reduce API calls.

## License

See LICENSE file.
