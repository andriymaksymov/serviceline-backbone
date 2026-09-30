from __future__ import annotations

import os
import time

from .broker import RabbitMQBroker
from .config import TOPIC_AI_INBOUND, TOPIC_AI_RESULT, TOPIC_WA_AGGREGATED, TOPIC_WA_INBOUND, Settings
from .pipeline import (
    AIInboundSubscriber,
    ContextRetriever,
    EmbeddingClient,
    InboundService,
    RedisAggregationWorker,
    WaAggregatedSubscriber,
    WaInboundSubscriber,
    WhatsAppResultSubscriber,
)
from .storage import RedisSessionStore
from .webhook import build_app


settings = Settings()
broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
store = RedisSessionStore(settings.redis_url, settings.redis_session_ttl_seconds)
inbound_service = InboundService(settings, broker, store)
app = build_app(inbound_service)


def run_worker() -> None:
    role = os.getenv("WORKER_ROLE", "")
    if role == "wa_inbound_subscriber":
        handler = WaInboundSubscriber(settings).handle
        broker.consume_forever(TOPIC_WA_INBOUND, handler)
        return

    if role == "redis_aggregator":
        worker = RedisAggregationWorker(broker, store)
        while True:
            worker.poll_once()
            time.sleep(1)
        return

    if role == "wa_aggregated_subscriber":
        handler = WaAggregatedSubscriber(
            broker,
            EmbeddingClient(settings.embedding_service_url, settings.embedding_model),
            ContextRetriever(settings.qdrant_url, settings.qdrant_collection, settings.confidence_threshold),
        ).handle
        broker.consume_forever(TOPIC_WA_AGGREGATED, handler)
        return

    if role == "ai_inbound_subscriber":
        handler = AIInboundSubscriber(settings, broker).handle
        broker.consume_forever(TOPIC_AI_INBOUND, handler)
        return

    if role == "ai_result_subscriber":
        handler = WhatsAppResultSubscriber(settings).handle
        broker.consume_forever(TOPIC_AI_RESULT, handler)
        return

    raise RuntimeError("Unknown WORKER_ROLE")
