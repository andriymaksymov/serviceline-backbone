from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from serviceline_backbone.broker import InMemoryBroker
from serviceline_backbone.models import AIResultMessage, AggregatedMessage, PendingApproval, WhatsAppInboundMessage
from serviceline_backbone.pipeline import (
    ApprovalFlow,
    InboundService,
    RedisAggregationWorker,
    WaAggregatedSubscriber,
    WhatsAppOutboundClient,
    WhatsAppResultSubscriber,
)
from serviceline_backbone.storage import InMemoryApprovalStore, InMemorySessionStore
from serviceline_backbone.webhook import build_app


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


class FakeOutboundClient(WhatsAppOutboundClient):
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, to: str, text: str, extra_payload: dict | None = None) -> None:
        item = {"to": to, "text": text}
        if extra_payload:
            item.update(extra_payload)
        self.sent.append(item)


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

    def test_approval_ok_forwards_to_original_customer(self) -> None:
        broker = InMemoryBroker()
        store = FakeSessionStore()
        approval_store = InMemoryApprovalStore()
        outbound = FakeOutboundClient()

        class _Settings:
            whatsapp_target_account = "+19999999999"
            approval_command_prefix = "/ok"

        result = AIResultMessage(
            sender="+10000000000",
            response_text="AI draft answer",
            source_session_id="session-1",
            used_context=[],
        )
        approval_store.save(
            PendingApproval(
                source_session_id=result.source_session_id,
                original_sender=result.sender,
                suggested_response_text=result.response_text,
            )
        )

        approval_flow = ApprovalFlow(_Settings(), approval_store, outbound)
        inbound_service = InboundService(_Settings(), broker, store, approval_flow=approval_flow)
        app = build_app(inbound_service)
        client = TestClient(app)

        response = client.post(
            "/webhook/whatsapp",
            json={
                "sender": "+19999999999",
                "text": "/ok session-1 Final approved answer",
                "message_id": "m1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")
        self.assertEqual(len(outbound.sent), 1)
        self.assertEqual(outbound.sent[0]["to"], "+10000000000")
        self.assertEqual(outbound.sent[0]["text"], "Final approved answer")
        self.assertEqual(len(store.items), 0)
        self.assertNotIn("wa.inbound", broker.messages)

    def test_ai_result_is_sent_to_reviewer_with_metadata(self) -> None:
        approval_store = InMemoryApprovalStore()
        outbound = FakeOutboundClient()

        class _Settings:
            whatsapp_outbound_url = "http://example.local/send"
            whatsapp_target_account = "+19999999999"
            approval_command_prefix = "/ok"

        subscriber = WhatsAppResultSubscriber(_Settings(), approval_store, outbound)
        subscriber.handle(
            AIResultMessage(
                sender="+10000000000",
                response_text="Draft answer",
                source_session_id="session-42",
                used_context=[],
            ).model_dump(mode="json")
        )

        self.assertEqual(len(outbound.sent), 1)
        self.assertEqual(outbound.sent[0]["to"], "+19999999999")
        self.assertEqual(outbound.sent[0]["source_session_id"], "session-42")
        self.assertEqual(outbound.sent[0]["original_sender"], "+10000000000")
        self.assertIn("source_session_id: session-42", outbound.sent[0]["text"])
        self.assertIn("original_sender: +10000000000", outbound.sent[0]["text"])

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
