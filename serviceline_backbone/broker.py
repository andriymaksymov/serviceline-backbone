from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Callable

Handler = Callable[[dict], None]


class RabbitMQBroker:
    def __init__(self, url: str, exchange: str) -> None:
        self._url = url
        self._exchange = exchange
        self._connection = None
        self._channel = None

    def _connect(self):
        if self._connection and self._channel and self._connection.is_open and self._channel.is_open:
            return self._connection, self._channel

        import pika

        params = pika.URLParameters(self._url)
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(exchange=self._exchange, exchange_type="topic", durable=True)
        return self._connection, self._channel

    def publish(self, topic: str, payload: dict) -> None:
        _connection, channel = self._connect()
        body = json.dumps(payload).encode("utf-8")
        channel.basic_publish(exchange=self._exchange, routing_key=topic, body=body)

    def consume_forever(self, topic: str, handler: Handler) -> None:
        connection, channel = self._connect()
        queue = f"{topic}.queue"
        channel.queue_declare(queue=queue, durable=True)
        channel.queue_bind(exchange=self._exchange, queue=queue, routing_key=topic)

        def wrapped(ch, method, _props, body: bytes) -> None:
            handler(json.loads(body.decode("utf-8")))
            ch.basic_ack(delivery_tag=method.delivery_tag)

        channel.basic_consume(queue=queue, on_message_callback=wrapped, auto_ack=False)
        channel.start_consuming()


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
