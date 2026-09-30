from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Callable


Handler = Callable[[dict], None]
logger = logging.getLogger(__name__)


class RabbitMQBroker:
    def __init__(
        self,
        url: str,
        exchange: str,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        reconnect_delay_seconds: float = 5.0,
    ) -> None:
        self._url = url
        self._exchange = exchange
        self._dlx = f"{exchange}.dlx"
        self._max_attempts = max_attempts
        self._retry_backoff = retry_backoff_seconds
        self._reconnect_delay = reconnect_delay_seconds
        self._connection = None
        self._channel = None
        # pika's BlockingConnection is not thread-safe; the webhook publishes from a threadpool.
        self._lock = threading.Lock()

    def _connect(self):
        if self._connection and self._channel and self._connection.is_open and self._channel.is_open:
            return self._connection, self._channel

        import pika

        params = pika.URLParameters(self._url)
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(exchange=self._exchange, exchange_type="topic", durable=True)
        self._channel.exchange_declare(exchange=self._dlx, exchange_type="topic", durable=True)
        return self._connection, self._channel

    def _reset(self) -> None:
        try:
            if self._connection and self._connection.is_open:
                self._connection.close()
        except Exception:
            logger.debug("Ignoring error while closing stale RabbitMQ connection", exc_info=True)
        self._connection = None
        self._channel = None

    def publish(self, topic: str, payload: dict) -> None:
        import pika

        body = json.dumps(payload).encode("utf-8")
        with self._lock:
            # An idle connection may have been dropped by the server (missed heartbeats); retry once on a fresh one.
            for attempt in (1, 2):
                try:
                    _connection, channel = self._connect()
                    channel.basic_publish(
                        exchange=self._exchange,
                        routing_key=topic,
                        body=body,
                        properties=pika.BasicProperties(delivery_mode=2),
                    )
                    return
                except pika.exceptions.AMQPError:
                    self._reset()
                    if attempt == 2:
                        raise
                    logger.warning("RabbitMQ publish failed, reconnecting topic=%s", topic)

    def _declare_queue(self, channel, topic: str) -> str:
        queue = f"{topic}.queue"
        dead_letter_queue = f"{topic}.dlq"
        channel.queue_declare(queue=dead_letter_queue, durable=True)
        channel.queue_bind(exchange=self._dlx, queue=dead_letter_queue, routing_key=topic)
        channel.queue_declare(
            queue=queue,
            durable=True,
            arguments={"x-dead-letter-exchange": self._dlx, "x-dead-letter-routing-key": topic},
        )
        channel.queue_bind(exchange=self._exchange, queue=queue, routing_key=topic)
        return queue

    def _handle_with_retries(self, topic: str, handler: Handler, payload: dict) -> bool:
        for attempt in range(1, self._max_attempts + 1):
            try:
                handler(payload)
                return True
            except Exception:
                logger.exception("Failed to process message topic=%s attempt=%s/%s", topic, attempt, self._max_attempts)
                if attempt < self._max_attempts:
                    time.sleep(self._retry_backoff * 2 ** (attempt - 1))
        return False

    def consume_forever(self, topic: str, handler: Handler) -> None:
        import pika

        def wrapped(ch, method, _props, body: bytes) -> None:
            try:
                payload = json.loads(body.decode("utf-8"))
            except ValueError:
                logger.exception("Dropping undecodable message to dead-letter queue topic=%s", topic)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            if self._handle_with_retries(topic, handler, payload):
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                # Routed to <topic>.dlq via the dead-letter exchange, not discarded.
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        while True:
            try:
                _connection, channel = self._connect()
                queue = self._declare_queue(channel, topic)
                channel.basic_qos(prefetch_count=1)
                channel.basic_consume(queue=queue, on_message_callback=wrapped, auto_ack=False)
                channel.start_consuming()
            except pika.exceptions.AMQPConnectionError:
                logger.exception("RabbitMQ connection lost topic=%s, reconnecting in %ss", topic, self._reconnect_delay)
                self._reset()
                time.sleep(self._reconnect_delay)


class InMemoryBroker:
    def __init__(self) -> None:
        self.messages: dict[str, deque[dict]] = defaultdict(deque)
        self.subscribers: dict[str, list[Handler]] = defaultdict(list)

    def publish(self, topic: str, payload: dict) -> None:
        self.messages[topic].append(payload)
        for handler in self.subscribers.get(topic, []):
            handler(payload)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self.subscribers[topic].append(handler)
