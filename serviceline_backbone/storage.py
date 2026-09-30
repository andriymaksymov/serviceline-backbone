from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import redis

from .models import PendingApproval, WhatsAppInboundMessage

# Safety net only: session keys must outlive the aggregation window so an aggregator outage doesn't
# silently expire unprocessed messages. Normal cleanup happens in complete().
SESSION_KEY_RETENTION_SECONDS = 24 * 3600
SEEN_MESSAGE_RETENTION_SECONDS = 24 * 3600


def new_session_id() -> str:
    # Random, so it cannot be guessed from the sender's phone number and a timestamp.
    return uuid.uuid4().hex


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

    @staticmethod
    def _seen_key(message_id: str) -> str:
        return f"seen:{message_id}"

    def add_message(self, message: WhatsAppInboundMessage, now_ts: float | None = None) -> bool:
        """Add a message to the sender's session. Returns False if this message_id was already accepted."""
        if message.message_id and not self._redis.set(
            self._seen_key(message.message_id), 1, nx=True, ex=SEEN_MESSAGE_RETENTION_SECONDS
        ):
            return False

        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        expires_at = now + self._ttl
        retention = self._ttl + SESSION_KEY_RETENTION_SECONDS
        messages_key = self._messages_key(message.sender)
        session_id_key = self._session_id_key(message.sender)

        with self._redis.pipeline() as pipe:
            pipe.rpush(messages_key, message.model_dump_json())
            pipe.expire(messages_key, retention)
            pipe.setnx(session_id_key, new_session_id())
            pipe.expire(session_id_key, retention)
            pipe.zadd(self._expiry_key(), {message.sender: expires_at})
            pipe.execute()
        return True

    def claim_ready_sessions(self, now_ts: float | None = None) -> Iterable[SessionAggregate]:
        """Claim expired sessions. Each must be finished with complete() or handed back with release()."""
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        expiry_key = self._expiry_key()
        senders = self._redis.zrangebyscore(expiry_key, 0, now)
        for sender in senders:
            # zrem is the claim: only one aggregator instance wins it.
            if self._redis.zrem(expiry_key, sender) == 0:
                continue
            with self._redis.pipeline() as pipe:
                pipe.lrange(self._messages_key(sender), 0, -1)
                pipe.get(self._session_id_key(sender))
                rows, session_id = pipe.execute()
            messages = [WhatsAppInboundMessage.model_validate_json(item) for item in rows]
            if messages:
                yield SessionAggregate(sender=sender, session_id=session_id or new_session_id(), messages=messages)

    def complete(self, session: SessionAggregate) -> None:
        # Trim only what was aggregated: messages that arrived after the claim stay queued, and their
        # zadd has already rescheduled the sender. They get a fresh session id on the next aggregation.
        with self._redis.pipeline() as pipe:
            pipe.ltrim(self._messages_key(session.sender), len(session.messages), -1)
            pipe.delete(self._session_id_key(session.sender))
            pipe.execute()

    def release(self, session: SessionAggregate, now_ts: float | None = None) -> None:
        now = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        self._redis.zadd(self._expiry_key(), {session.sender: now})


class InMemorySessionStore:
    """In-memory session store that uses sliding TTL, matching RedisSessionStore behavior."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._sessions: dict[str, tuple[float, str, list[WhatsAppInboundMessage]]] = {}
        self._seen: set[str] = set()

    def add_message(self, message: WhatsAppInboundMessage, now_ts: float | None = None) -> bool:
        if message.message_id:
            if message.message_id in self._seen:
                return False
            self._seen.add(message.message_id)
        current_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        if message.sender not in self._sessions:
            self._sessions[message.sender] = (current_ts + self._ttl, new_session_id(), [message])
            return True
        _expires_at, session_id, messages = self._sessions[message.sender]
        messages.append(message)
        self._sessions[message.sender] = (current_ts + self._ttl, session_id, messages)
        return True

    def claim_ready_sessions(self, now_ts: float | None = None) -> list[SessionAggregate]:
        current_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        ready: list[SessionAggregate] = []
        finished = [sender for sender, (expires, _, _) in self._sessions.items() if expires <= current_ts]
        for sender in finished:
            _, session_id, messages = self._sessions.pop(sender)
            ready.append(SessionAggregate(sender=sender, session_id=session_id, messages=messages))
        return ready

    def complete(self, session: SessionAggregate) -> None:
        pass

    def release(self, session: SessionAggregate, now_ts: float | None = None) -> None:
        current_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        if session.sender in self._sessions:
            _expires_at, session_id, messages = self._sessions[session.sender]
            self._sessions[session.sender] = (current_ts, session.session_id, session.messages + messages)
        else:
            self._sessions[session.sender] = (current_ts, session.session_id, session.messages)


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
