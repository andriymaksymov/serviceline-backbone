from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    rabbitmq_url: str = os.getenv("RABBITMQ_URL", "amqp://localhost:5672/%2F")
    rabbitmq_exchange: str = os.getenv("RABBITMQ_EXCHANGE", "serviceline")

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_session_ttl_seconds: int = int(os.getenv("REDIS_SESSION_TTL_SECONDS", "30"))

    storage_root: Path = Path(os.getenv("STORAGE_ROOT", "./data/messages")).resolve()

    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "knowledge")
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.35"))

    embedding_service_url: str = os.getenv("EMBEDDING_SERVICE_URL", "http://embeddings:8080/embed")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    whatsapp_outbound_url: str = os.getenv("WHATSAPP_OUTBOUND_URL", "")
    whatsapp_target_account: str = os.getenv("WHATSAPP_TARGET_ACCOUNT", "")

    system_prompt_path: Path = Path(os.getenv("SYSTEM_PROMPT_PATH", "./config/system_prompt.txt")).resolve()


TOPIC_WA_INBOUND = "wa.inbound"
TOPIC_WA_AGGREGATED = "wa.aggregated"
TOPIC_AI_INBOUND = "ai.inbound"
TOPIC_AI_RESULT = "ai.result"
