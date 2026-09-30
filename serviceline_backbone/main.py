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


def create_app():
    settings = Settings()
    broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
    store = RedisSessionStore(settings.redis_url, settings.redis_session_ttl_seconds)
    inbound_service = InboundService(settings, broker, store)
    return build_app(inbound_service)


app = create_app()


def run_worker() -> None:
    role = os.getenv("WORKER_ROLE", "")
    if role == "wa_inbound_subscriber":
        settings = Settings()
        broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
        handler = WaInboundSubscriber(settings).handle
        broker.consume_forever(TOPIC_WA_INBOUND, handler)
        return

    elif role == "redis_aggregator":
        settings = Settings()
        broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
        store = RedisSessionStore(settings.redis_url, settings.redis_session_ttl_seconds)
        worker = RedisAggregationWorker(broker, store)
        while True:
            worker.poll_once()
            time.sleep(1)

    elif role == "wa_aggregated_subscriber":
        settings = Settings()
        broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
        handler = WaAggregatedSubscriber(
            broker,
            EmbeddingClient(settings.embedding_service_url, settings.embedding_model),
            ContextRetriever(settings.qdrant_url, settings.qdrant_collection, settings.confidence_threshold),
        ).handle
        broker.consume_forever(TOPIC_WA_AGGREGATED, handler)
        return

    elif role == "ai_inbound_subscriber":
        settings = Settings()
        broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
        handler = AIInboundSubscriber(settings, broker).handle
        broker.consume_forever(TOPIC_AI_INBOUND, handler)
        return

    elif role == "ai_result_subscriber":
        settings = Settings()
        broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
        handler = WhatsAppResultSubscriber(settings).handle
        broker.consume_forever(TOPIC_AI_RESULT, handler)
        return

    else:
        raise RuntimeError("Unknown WORKER_ROLE")
