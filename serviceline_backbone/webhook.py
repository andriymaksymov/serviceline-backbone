from __future__ import annotations

from fastapi import FastAPI

from .models import WhatsAppInboundMessage


def build_app(inbound_service) -> FastAPI:
    app = FastAPI(title="Serviceline WhatsApp Webhook")

    @app.post("/webhook/whatsapp")
    async def whatsapp_webhook(message: WhatsAppInboundMessage) -> dict[str, str]:
        inbound_service.handle_message(message)
        return {"status": "accepted"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
