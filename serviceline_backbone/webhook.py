from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Header, HTTPException

from .models import WhatsAppInboundMessage

logger = logging.getLogger(__name__)


def build_app(inbound_service, webhook_secret: str) -> FastAPI:
    app = FastAPI(title="Serviceline WhatsApp Webhook")
    if not webhook_secret:
        logger.error("WHATSAPP_WEBHOOK_SECRET is not set; the webhook will reject all requests")

    # Plain `def` (not async): the handler does blocking Redis/RabbitMQ/HTTP I/O, so FastAPI must run it
    # in its threadpool instead of on the event loop.
    @app.post("/webhook/whatsapp")
    def whatsapp_webhook(message: WhatsAppInboundMessage, x_webhook_secret: str | None = Header(default=None)) -> dict[str, str]:
        # Fail closed: `sender` comes from the request body, so without a secret anyone could impersonate the reviewer.
        if not webhook_secret:
            raise HTTPException(status_code=503, detail="Webhook secret is not configured")
        if not hmac.compare_digest((x_webhook_secret or "").encode("utf-8"), webhook_secret.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Invalid webhook secret")
        status = inbound_service.handle_message(message)
        return {"status": status}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
