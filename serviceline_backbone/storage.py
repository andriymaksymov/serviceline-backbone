from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import redis

from .models import PendingApproval, WhatsAppInboundMessage


@dataclass
class SessionAggregate:
    sender: str
    session_id: str
    messages: list[WhatsAppInboundMessage]


class RedisSessionStore:
    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _messages_key(sender: str) -> str:
        return f"session:{sender}:messages"

    @staticmethod
    def _session_id_key(sender: str) -> str:
        return f"session:{sender}:id"

    @staticmethod
    def _expiry_key() -> str:
        return "sessions:expiry"

    def add_message(self, message: WhatsAppInboundMessage, now_ts: float | None = None) -> None:
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        expires_at = now + self._ttl
        messages_key = self._messages_key(message.sender)
        session_id_key = self._session_id_key(message.sender)

        with self._redis.pipeline() as pipe:
            pipe.rpush(messages_key, message.model_dump_json())
            pipe.expire(messages_key, self._ttl)
            pipe.setnx(session_id_key, f"{message.sender}:{int(now)}")
            pipe.expire(session_id_key, self._ttl)
            pipe.zadd(self._expiry_key(), {message.sender: expires_at})
            pipe.execute()

    def drain_ready_sessions(self, now_ts: float | None = None) -> Iterable[SessionAggregate]:
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        expiry_key = self._expiry_key()
        senders = self._redis.zrangebyscore(expiry_key, 0, now)
        for sender in senders:
            if self._redis.zrem(expiry_key, sender) == 0:
                continue
            messages_key = self._messages_key(sender)
            session_id_key = self._session_id_key(sender)
            with self._redis.pipeline() as pipe:
                pipe.lrange(messages_key, 0, -1)
                pipe.get(session_id_key)
                pipe.delete(messages_key)
                pipe.delete(session_id_key)
                rows, session_id, *_ = pipe.execute()
            messages = [WhatsAppInboundMessage.model_validate_json(item) for item in rows]
            if messages:
                yield SessionAggregate(
                    sender=sender,
                    session_id=session_id or f"{sender}:{int(now)}",
                    messages=messages,
                )


class InMemorySessionStore:
    """In-memory session store that uses sliding TTL, matching RedisSessionStore behavior."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._sessions: dict[str, tuple[float, str, list[WhatsAppInboundMessage]]] = {}

    def add_message(self, message: WhatsAppInboundMessage, now_ts: float | None = None) -> None:
        current_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        if message.sender not in self._sessions:
            self._sessions[message.sender] = (
                current_ts + self._ttl,
                f"{message.sender}:{int(current_ts)}",
                [message],
            )
            return
        expires_at, session_id, messages = self._sessions[message.sender]
        messages.append(message)
        self._sessions[message.sender] = (current_ts + self._ttl, session_id, messages)

    def drain_ready_sessions(self, now_ts: float | None = None) -> list[SessionAggregate]:
        current_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        ready: list[SessionAggregate] = []
        finished = [sender for sender, (expires, _, _) in self._sessions.items() if expires <= current_ts]
        for sender in finished:
            _, session_id, messages = self._sessions.pop(sender)
            ready.append(SessionAggregate(sender=sender, session_id=session_id, messages=messages))
        return ready


class RedisApprovalStore:
    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f"approval:{session_id}"

    def save(self, item: PendingApproval) -> None:
        self._redis.set(self._key(item.source_session_id), item.model_dump_json(), ex=self._ttl)

    def pop(self, source_session_id: str) -> PendingApproval | None:
        key = self._key(source_session_id)
        value = self._redis.getdel(key)
        if not value:
            return None
        return PendingApproval.model_validate_json(value)


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._items: dict[str, PendingApproval] = {}

    def save(self, item: PendingApproval) -> None:
        self._items[item.source_session_id] = item

    def pop(self, source_session_id: str) -> PendingApproval | None:
        return self._items.pop(source_session_id, None)
