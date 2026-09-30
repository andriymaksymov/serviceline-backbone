from __future__ import annotations

import json
import logging
from datetime import timezone
from pathlib import Path
from urllib import request

from .config import TOPIC_AI_INBOUND, TOPIC_AI_RESULT, TOPIC_WA_AGGREGATED, TOPIC_WA_INBOUND, Settings
from .models import AIInboundMessage, AIResultMessage, AggregatedMessage, ContextItem, PendingApproval, WhatsAppInboundMessage

logger = logging.getLogger(__name__)


def persist_inbound_to_file(message: WhatsAppInboundMessage, root: Path) -> Path:
    dt = message.timestamp.astimezone(timezone.utc)
    folder = root / message.sender / dt.strftime("%Y") / dt.strftime("%m") / dt.strftime("%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{dt.strftime('%H%M%S')}-{message.message_id or 'message'}.json"
    path.write_text(message.model_dump_json(indent=2), encoding="utf-8")
    return path


class EmbeddingClient:
    def __init__(self, service_url: str, model: str) -> None:
        self._service_url = service_url
        self._model = model

    def embed(self, text: str) -> list[float]:
        payload = json.dumps({"model": self._model, "input": text}).encode("utf-8")
        req = request.Request(self._service_url, data=payload, headers={"Content-Type": "application/json"})
        with request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        vector = data.get("embedding") or data.get("data", [{}])[0].get("embedding")
        if not isinstance(vector, list):
            raise ValueError("Embedding response is missing vector")
        return [float(x) for x in vector]


class ContextRetriever:
    def __init__(self, qdrant_url: str, collection: str, threshold: float) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=qdrant_url)
        self._collection = collection
        self._threshold = threshold

    def search(self, vector: list[float], limit: int = 5) -> list[ContextItem]:
        points = self._client.search(
            collection_name=self._collection,
            query_vector=vector,
            limit=limit,
            score_threshold=self._threshold,
        )
        return [
            ContextItem(
                id=str(point.id),
                score=float(point.score),
                payload=point.payload or {},
            )
            for point in points
        ]


class InboundService:
    def __init__(self, settings: Settings, broker, session_store, approval_flow=None) -> None:
        self._settings = settings
        self._broker = broker
        self._store = session_store
        self._approval_flow = approval_flow

    def handle_message(self, message: WhatsAppInboundMessage) -> str:
        if self._approval_flow and self._approval_flow.is_approval_message(message):
            approved = self._approval_flow.handle_approval(message)
            return "approved" if approved else "approval_rejected"
        self._store.add_message(message)
        self._broker.publish(TOPIC_WA_INBOUND, message.model_dump(mode="json"))
        return "accepted"


class WaInboundSubscriber:
    def __init__(self, settings: Settings) -> None:
        self._root = settings.storage_root

    def handle(self, payload: dict) -> None:
        message = WhatsAppInboundMessage.model_validate(payload)
        persist_inbound_to_file(message, self._root)


class RedisAggregationWorker:
    def __init__(self, broker, session_store) -> None:
        self._broker = broker
        self._store = session_store

    def poll_once(self, now_ts: float | None = None) -> int:
        sent = 0
        for session in self._store.drain_ready_sessions(now_ts=now_ts):
            aggregate = AggregatedMessage(
                session_id=session.session_id,
                sender=session.sender,
                text="\n".join(item.text for item in session.messages),
                message_count=len(session.messages),
                started_at=min(item.timestamp for item in session.messages),
                ended_at=max(item.timestamp for item in session.messages),
            )
            self._broker.publish(TOPIC_WA_AGGREGATED, aggregate.model_dump(mode="json"))
            sent += 1
        return sent


class WaAggregatedSubscriber:
    def __init__(self, broker, embedding_client: EmbeddingClient, retriever: ContextRetriever) -> None:
        self._broker = broker
        self._embedding_client = embedding_client
        self._retriever = retriever

    def handle(self, payload: dict) -> None:
        aggregate = AggregatedMessage.model_validate(payload)
        vector = self._embedding_client.embed(aggregate.text)
        context = self._retriever.search(vector)
        ai_inbound = AIInboundMessage(aggregated=aggregate, context=context)
        self._broker.publish(TOPIC_AI_INBOUND, ai_inbound.model_dump(mode="json"))


class AIInboundSubscriber:
    def __init__(self, settings: Settings, broker) -> None:
        from openai import OpenAI

        self._settings = settings
        self._broker = broker
        self._openai = OpenAI(api_key=settings.openai_api_key)

    def handle(self, payload: dict) -> None:
        inbound = AIInboundMessage.model_validate(payload)
        system_prompt = self._settings.system_prompt_path.read_text(encoding="utf-8")

        context_block = "\n\n".join(json.dumps(item.model_dump(), ensure_ascii=False) for item in inbound.context)
        user_prompt = (
            f"Customer message:\n{inbound.aggregated.text}\n\n"
            f"Knowledge context:\n{context_block if context_block else 'No extra context found.'}"
        )

        response = self._openai.chat.completions.create(
            model=self._settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        output_text = (response.choices[0].message.content or "") if response.choices else ""

        result = AIResultMessage(
            sender=inbound.aggregated.sender,
            response_text=output_text,
            source_session_id=inbound.aggregated.session_id,
            used_context=inbound.context,
        )
        self._broker.publish(TOPIC_AI_RESULT, result.model_dump(mode="json"))


class WhatsAppOutboundClient:
    def __init__(self, outbound_url: str) -> None:
        self._outbound_url = outbound_url

    def send(self, to: str, text: str, extra_payload: dict | None = None) -> None:
        outbound = {"to": to, "text": text}
        if extra_payload:
            outbound.update(extra_payload)
        req = request.Request(
            self._outbound_url,
            data=json.dumps(outbound).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=10):
            return


def format_reviewer_message(result: AIResultMessage, command_prefix: str) -> str:
    return (
        "[REVIEW REQUIRED]\n"
        f"source_session_id: {result.source_session_id}\n"
        f"original_sender: {result.sender}\n\n"
        "Edit the answer if needed and send:\n"
        f"{command_prefix} {result.source_session_id} <approved_or_edited_answer>\n\n"
        f"Suggested answer:\n{result.response_text}"
    )


def parse_approval_command(text: str, command_prefix: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if not stripped.startswith(command_prefix):
        return None
    remainder = stripped[len(command_prefix) :].strip()
    if not remainder:
        return None
    parts = remainder.split(maxsplit=1)
    if len(parts) < 2:
        return None
    source_session_id, approved_text = parts[0].strip(), parts[1].strip()
    if not source_session_id or not approved_text:
        return None
    return source_session_id, approved_text


class ApprovalFlow:
    def __init__(self, settings: Settings, approval_store, outbound_client: WhatsAppOutboundClient) -> None:
        self._settings = settings
        self._approval_store = approval_store
        self._outbound_client = outbound_client

    def is_approval_message(self, message: WhatsAppInboundMessage) -> bool:
        return (
            bool(self._settings.whatsapp_target_account)
            and message.sender == self._settings.whatsapp_target_account
            and message.text.strip().startswith(self._settings.approval_command_prefix)
        )

    def handle_approval(self, message: WhatsAppInboundMessage) -> bool:
        parsed = parse_approval_command(message.text, self._settings.approval_command_prefix)
        if not parsed:
            return False
        source_session_id, approved_text = parsed
        pending = self._approval_store.pop(source_session_id)
        if not pending:
            logger.warning("Approval rejected: no pending approval found for session_id=%s", source_session_id)
            return False
        self._outbound_client.send(
            to=pending.original_sender,
            text=approved_text,
            extra_payload={
                "source_session_id": pending.source_session_id,
                "approved_by": message.sender,
            },
        )
        return True


class WhatsAppResultSubscriber:
    def __init__(self, settings: Settings, approval_store, outbound_client: WhatsAppOutboundClient | None = None) -> None:
        self._settings = settings
        self._approval_store = approval_store
        self._outbound_client = outbound_client

    def handle(self, payload: dict) -> None:
        result = AIResultMessage.model_validate(payload)
        pending = PendingApproval(
            source_session_id=result.source_session_id,
            original_sender=result.sender,
            suggested_response_text=result.response_text,
        )
        self._approval_store.save(pending)
        outbound_url = getattr(self._settings, "whatsapp_outbound_url", "")
        target_account = getattr(self._settings, "whatsapp_target_account", "")
        if not target_account:
            logger.warning("Skipping WhatsApp outbound delivery: WHATSAPP_TARGET_ACCOUNT is missing")
            return
        if self._outbound_client is None and not outbound_url:
            logger.warning("Skipping WhatsApp outbound delivery: WHATSAPP_OUTBOUND_URL is missing")
            return
        outbound_client = self._outbound_client or WhatsAppOutboundClient(outbound_url)
        outbound_client.send(
            to=target_account,
            text=format_reviewer_message(result, self._settings.approval_command_prefix),
            extra_payload={
                "source_session_id": result.source_session_id,
                "original_sender": result.sender,
            },
        )
