from __future__ import annotations

import os
import time

from .broker import RabbitMQBroker
from .config import TOPIC_AI_INBOUND, TOPIC_AI_RESULT, TOPIC_WA_AGGREGATED, TOPIC_WA_INBOUND, Settings
from .pipeline import (
    AIInboundSubscriber,
    ApprovalFlow,
    ContextRetriever,
    EmbeddingClient,
    InboundService,
    RedisAggregationWorker,
    WaAggregatedSubscriber,
    WaInboundSubscriber,
    WhatsAppOutboundClient,
    WhatsAppResultSubscriber,
)
from .storage import RedisApprovalStore, RedisSessionStore
from .webhook import build_app


def create_app():
    class LazyInboundService:
        def __init__(self) -> None:
            self._service = None

        def handle_message(self, message) -> None:
            if self._service is None:
                settings = Settings()
                broker = RabbitMQBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
                store = RedisSessionStore(settings.redis_url, settings.redis_session_ttl_seconds)
                approval_store = RedisApprovalStore(settings.redis_url, settings.approval_ttl_seconds)
                outbound = WhatsAppOutboundClient(settings.whatsapp_outbound_url)
                approval_flow = ApprovalFlow(settings, approval_store, outbound)
                self._service = InboundService(settings, broker, store, approval_flow=approval_flow)
            return self._service.handle_message(message)

    return build_app(LazyInboundService())


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
        approval_store = RedisApprovalStore(settings.redis_url, settings.approval_ttl_seconds)
        outbound = WhatsAppOutboundClient(settings.whatsapp_outbound_url)
        handler = WhatsAppResultSubscriber(settings, approval_store, outbound).handle
        broker.consume_forever(TOPIC_AI_RESULT, handler)
        return

    else:
        raise RuntimeError("Unknown WORKER_ROLE")
