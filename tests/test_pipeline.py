from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from serviceline_backbone.broker import InMemoryBroker
from serviceline_backbone.models import AggregatedMessage, WhatsAppInboundMessage
from serviceline_backbone.pipeline import InboundService, RedisAggregationWorker, WaAggregatedSubscriber
from serviceline_backbone.webhook import build_app
from serviceline_backbone.storage import InMemorySessionStore


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


class FakeRetriever:
    def search(self, vector: list[float]):
        return []


class FakeSessionStore:
    def __init__(self) -> None:
        self.items: list[WhatsAppInboundMessage] = []

    def add_message(self, message: WhatsAppInboundMessage) -> None:
        self.items.append(message)


class PipelineTests(unittest.TestCase):
    def test_webhook_publishes_wa_inbound_and_stores_message(self) -> None:
        broker = InMemoryBroker()
        store = FakeSessionStore()

        class _Settings:
            pass

        inbound_service = InboundService(_Settings(), broker, store)
        app = build_app(inbound_service)
        client = TestClient(app)

        response = client.post(
            "/webhook/whatsapp",
            json={
                "sender": "+10000000000",
                "text": "Hello from WhatsApp",
                "message_id": "abc",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(len(store.items), 1)
        self.assertEqual(len(broker.messages["wa.inbound"]), 1)

    def test_aggregates_after_ttl_expiry(self) -> None:
        broker = InMemoryBroker()
        store = InMemorySessionStore(ttl_seconds=5)

        first = WhatsAppInboundMessage(
            sender="+10000000000",
            text="Hello",
            message_id="1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        second = WhatsAppInboundMessage(
            sender="+10000000000",
            text="Need appointment",
            message_id="2",
            timestamp=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        )

        store.add_message(first, now_ts=10)
        store.add_message(second, now_ts=12)

        worker = RedisAggregationWorker(broker, store)
        sent = worker.poll_once(now_ts=14)
        self.assertEqual(sent, 0)

        sent = worker.poll_once(now_ts=18)
        self.assertEqual(sent, 1)

        aggregated_payload = broker.messages["wa.aggregated"].popleft()
        aggregated = AggregatedMessage.model_validate(aggregated_payload)
        self.assertEqual(aggregated.message_count, 2)
        self.assertIn("Hello", aggregated.text)
        self.assertIn("Need appointment", aggregated.text)

    def test_wa_aggregated_publishes_ai_inbound(self) -> None:
        broker = InMemoryBroker()
        subscriber = WaAggregatedSubscriber(broker, FakeEmbeddingClient(), FakeRetriever())

        aggregate = AggregatedMessage(
            session_id="s1",
            sender="+10000000000",
            text="Need treatment plan",
            message_count=1,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        subscriber.handle(aggregate.model_dump(mode="json"))

        self.assertEqual(len(broker.messages["ai.inbound"]), 1)


if __name__ == "__main__":
    unittest.main()
