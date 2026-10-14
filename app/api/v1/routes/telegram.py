import hmac

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import get_settings

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Webhook del bot (lo llama Telegram)",
    include_in_schema=False,
)
async def telegram_webhook(
    request: Request,
    secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict:
    bot = getattr(request.app.state, "telegram_bot", None)
    if bot is None or not bot.webhook:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    expected = get_settings().telegram_webhook_secret.get_secret_value()
    # Sin el secreto correcto la petición no viene de Telegram
    if not expected or not hmac.compare_digest(secret or "", expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Secreto inválido")
    bot.feed(await request.json())
    return {"ok": True}
