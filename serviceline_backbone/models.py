from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class WhatsAppInboundMessage(BaseModel):
    sender: str
    text: str
    message_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AggregatedMessage(BaseModel):
    session_id: str
    sender: str
    text: str
    message_count: int
    started_at: datetime
    ended_at: datetime


class ContextItem(BaseModel):
    id: str
    score: float
    payload: dict[str, Any]


class AIInboundMessage(BaseModel):
    aggregated: AggregatedMessage
    context: list[ContextItem]


class AIResultMessage(BaseModel):
    sender: str
    response_text: str
    source_session_id: str
    used_context: list[ContextItem]


class PendingApproval(BaseModel):
    source_session_id: str
    original_sender: str
    suggested_response_text: str
