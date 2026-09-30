from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from serviceline_backbone.broker import InMemoryBroker
from serviceline_backbone.config import Settings
from serviceline_backbone.models import AIResultMessage, AggregatedMessage, PendingApproval, WhatsAppInboundMessage
from serviceline_backbone.pipeline import (
    ApprovalFlow,
    InboundService,
    RedisAggregationWorker,
    WaAggregatedSubscriber,
    WhatsAppOutboundClient,
    WhatsAppResultSubscriber,
    parse_approval_command,
    persist_inbound_to_file,
)
from serviceline_backbone.storage import InMemoryApprovalStore, InMemorySessionStore
from serviceline_backbone.webhook import build_app

SECRET = "test-secret"
AUTH = {"X-Webhook-Secret": SECRET}
REVIEWER = "+19999999999"


def make_settings(**overrides) -> Settings:
    values = {
        "whatsapp_target_account": REVIEWER,
        "whatsapp_outbound_url": "http://example.local/send",
        "approval_command_prefix": "/ok",
    }
    values.update(overrides)
    return Settings(**values)


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


class FakeRetriever:
    def search(self, vector: list[float]):
        return []


class FakeSessionStore:
    def __init__(self) -> None:
        self.items: list[WhatsAppInboundMessage] = []

    def add_message(self, message: WhatsAppInboundMessage) -> bool:
        if message.message_id and any(item.message_id == message.message_id for item in self.items):
            return False
        self.items.append(message)
        return True


class FakeOutboundClient(WhatsAppOutboundClient):
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self._fail = fail

    def send(self, to: str, text: str, extra_payload: dict | None = None) -> None:
        if self._fail:
            raise ConnectionError("outbound down")
        item = {"to": to, "text": text}
        if extra_payload:
            item.update(extra_payload)
        self.sent.append(item)


class FailingBroker(InMemoryBroker):
    def publish(self, topic: str, payload: dict) -> None:
        raise ConnectionError("broker down")


def inbound_payload(sender: str = "+10000000000", text: str = "Hello from WhatsApp", message_id: str = "abc") -> dict:
    return {"sender": sender, "text": text, "message_id": message_id, "timestamp": "2026-01-01T00:00:00Z"}


class PipelineTests(unittest.TestCase):
    def test_webhook_publishes_wa_inbound_and_stores_message(self) -> None:
        broker = InMemoryBroker()
        store = FakeSessionStore()

        inbound_service = InboundService(make_settings(), broker, store)
        client = TestClient(build_app(inbound_service, webhook_secret=SECRET))

        response = client.post("/webhook/whatsapp", json=inbound_payload(), headers=AUTH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(len(store.items), 1)
        self.assertEqual(len(broker.messages["wa.inbound"]), 1)

    def test_webhook_rejects_wrong_or_missing_secret(self) -> None:
        store = FakeSessionStore()
        client = TestClient(build_app(InboundService(make_settings(), InMemoryBroker(), store), webhook_secret=SECRET))

        self.assertEqual(client.post("/webhook/whatsapp", json=inbound_payload()).status_code, 401)
        self.assertEqual(
            client.post("/webhook/whatsapp", json=inbound_payload(), headers={"X-Webhook-Secret": "nope"}).status_code, 401
        )
        self.assertEqual(len(store.items), 0)

    def test_webhook_fails_closed_without_configured_secret(self) -> None:
        store = FakeSessionStore()
        client = TestClient(build_app(InboundService(make_settings(), InMemoryBroker(), store), webhook_secret=""))

        response = client.post("/webhook/whatsapp", json=inbound_payload(), headers={"X-Webhook-Secret": ""})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(store.items), 0)

    def test_duplicate_message_id_is_not_stored_twice(self) -> None:
        store = FakeSessionStore()
        client = TestClient(build_app(InboundService(make_settings(), InMemoryBroker(), store), webhook_secret=SECRET))

        client.post("/webhook/whatsapp", json=inbound_payload(), headers=AUTH)
        response = client.post("/webhook/whatsapp", json=inbound_payload(), headers=AUTH)

        self.assertEqual(response.json()["status"], "duplicate")
        self.assertEqual(len(store.items), 1)

    def test_approval_ok_forwards_to_original_customer(self) -> None:
        broker = InMemoryBroker()
        store = FakeSessionStore()
        approval_store = InMemoryApprovalStore()
        outbound = FakeOutboundClient()
        settings = make_settings()

        approval_store.save(
            PendingApproval(
                source_session_id="session-1",
                original_sender="+10000000000",
                suggested_response_text="AI draft answer",
            )
        )

        approval_flow = ApprovalFlow(settings, approval_store, outbound)
        inbound_service = InboundService(settings, broker, store, approval_flow=approval_flow)
        client = TestClient(build_app(inbound_service, webhook_secret=SECRET))

        response = client.post(
            "/webhook/whatsapp",
            json=inbound_payload(sender=REVIEWER, text="/ok session-1 Final approved answer", message_id="m1"),
            headers=AUTH,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")
        self.assertEqual(len(outbound.sent), 1)
        self.assertEqual(outbound.sent[0]["to"], "+10000000000")
        self.assertEqual(outbound.sent[0]["text"], "Final approved answer")
        self.assertEqual(len(store.items), 0)
        self.assertNotIn("wa.inbound", broker.messages)

    def test_non_command_reviewer_message_is_ignored(self) -> None:
        broker = InMemoryBroker()
        store = FakeSessionStore()
        settings = make_settings()
        approval_flow = ApprovalFlow(settings, InMemoryApprovalStore(), FakeOutboundClient())
        inbound_service = InboundService(settings, broker, store, approval_flow=approval_flow)

        status = inbound_service.handle_message(WhatsAppInboundMessage(sender=REVIEWER, text="/okay thanks"))

        self.assertEqual(status, "ignored")
        self.assertEqual(len(store.items), 0)
        self.assertNotIn("wa.inbound", broker.messages)

    def test_failed_approval_send_keeps_pending_draft(self) -> None:
        approval_store = InMemoryApprovalStore()
        pending = PendingApproval(source_session_id="s1", original_sender="+10000000000", suggested_response_text="x")
        approval_store.save(pending)
        approval_flow = ApprovalFlow(make_settings(), approval_store, FakeOutboundClient(fail=True))

        with self.assertRaises(ConnectionError):
            approval_flow.handle_approval(WhatsAppInboundMessage(sender=REVIEWER, text="/ok s1 Final"))

        self.assertEqual(approval_store.pop("s1"), pending)

    def test_parse_approval_command(self) -> None:
        self.assertEqual(parse_approval_command("/ok session-123 Thanks!", "/ok"), ("session-123", "Thanks!"))
        self.assertEqual(parse_approval_command("  /ok s1 multi word\nanswer ", "/ok"), ("s1", "multi word\nanswer"))
        self.assertIsNone(parse_approval_command("/ok session-123", "/ok"))
        self.assertIsNone(parse_approval_command("/ok", "/ok"))
        self.assertIsNone(parse_approval_command("/okay s1 text", "/ok"))
        self.assertIsNone(parse_approval_command("hello /ok s1 text", "/ok"))

    def test_ai_result_is_sent_to_reviewer_with_metadata(self) -> None:
        approval_store = InMemoryApprovalStore()
        outbound = FakeOutboundClient()

        subscriber = WhatsAppResultSubscriber(make_settings(), approval_store, outbound)
        subscriber.handle(
            AIResultMessage(
                sender="+10000000000",
                response_text="Draft answer",
                source_session_id="session-42",
                used_context=[],
            ).model_dump(mode="json")
        )

        self.assertEqual(len(outbound.sent), 1)
        self.assertEqual(outbound.sent[0]["to"], REVIEWER)
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
        self.assertNotIn("+10000000000", aggregated.session_id)

    def test_aggregation_keeps_session_when_publish_fails(self) -> None:
        store = InMemorySessionStore(ttl_seconds=5)
        store.add_message(WhatsAppInboundMessage(sender="+10000000000", text="Hello", message_id="1"), now_ts=10)

        self.assertEqual(RedisAggregationWorker(FailingBroker(), store).poll_once(now_ts=20), 0)

        broker = InMemoryBroker()
        self.assertEqual(RedisAggregationWorker(broker, store).poll_once(now_ts=21), 1)
        self.assertEqual(AggregatedMessage.model_validate(broker.messages["wa.aggregated"][0]).text, "Hello")

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

    def test_persist_inbound_cannot_escape_storage_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "messages"
            message = WhatsAppInboundMessage(
                sender="../../evil", text="x", message_id="../../../../etc/passwd", timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc)
            )

            path = persist_inbound_to_file(message, root)

            self.assertTrue(path.resolve().is_relative_to(root.resolve()))
            self.assertEqual(path.relative_to(root).parts[0], "______evil")


if __name__ == "__main__":
    unittest.main()
