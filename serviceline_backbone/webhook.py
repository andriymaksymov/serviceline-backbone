from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

from .models import WhatsAppInboundMessage


def build_app(inbound_service, webhook_secret: str = "") -> FastAPI:
    app = FastAPI(title="Serviceline WhatsApp Webhook")

    @app.post("/webhook/whatsapp")
    async def whatsapp_webhook(message: WhatsAppInboundMessage, x_webhook_secret: str | None = Header(default=None)) -> dict[str, str]:
        if webhook_secret and x_webhook_secret != webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")
        status = inbound_service.handle_message(message)
        return {"status": status}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
