"""Bot de Telegram (python-telegram-bot) arrancado junto a la API.

- Sin TELEGRAM_WEBHOOK_URL (o si no es HTTPS público): modo polling, ideal en desarrollo.
- Con TELEGRAM_WEBHOOK_URL https://...: registra el webhook y Telegram envía los mensajes
  a POST /api/v1/telegram/webhook, verificados con TELEGRAM_WEBHOOK_SECRET.

Ojo: en modo polling debe haber UNA sola instancia de la API con el bot activo.
"""

import asyncio
import logging
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.ai.client import get_llm
from app.core.config import Settings
from app.telegram.handlers import TelegramBotService, split_message

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand("nueva", "Empezar una conversación nueva"),
    BotCommand("desvincular", "Desvincular este Telegram de tu cuenta"),
    BotCommand("ayuda", "Qué puedo preguntar"),
]


def webhook_url(settings: Settings) -> str | None:
    """URL del webhook si es utilizable (HTTPS y no local); si no, None = polling."""
    url = settings.telegram_webhook_url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0"}:
        logger.warning(
            "TELEGRAM_WEBHOOK_URL=%s no es una URL HTTPS pública (Telegram no podría llamarla); "
            "se usa polling. Déjala vacía en desarrollo o usa un túnel (p. ej. ngrok).",
            url,
        )
        return None
    return url


class TelegramBot:
    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]):
        self.settings = settings
        self.service = TelegramBotService(sessionmaker, get_llm)
        self.webhook = webhook_url(settings)
        builder = Application.builder().token(settings.telegram_bot_token.get_secret_value())
        if self.webhook:
            builder = builder.updater(None)
        # Varias conversaciones a la vez: una respuesta lenta de la IA no bloquea a los demás
        self.app = builder.concurrent_updates(True).build()
        self._register_handlers()
        self._pending: set[asyncio.Task] = set()

    def _register_handlers(self) -> None:
        async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE, coro) -> None:
            chat = update.effective_chat
            await context.bot.send_chat_action(chat.id, ChatAction.TYPING)
            for part in split_message(await coro):
                await context.bot.send_message(chat.id, part, parse_mode=ParseMode.HTML)

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            code = context.args[0] if context.args else None
            await reply(update, context, self.service.start(update.effective_chat.id, code))

        async def new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await reply(update, context, self.service.new_conversation(update.effective_chat.id))

        async def unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await reply(update, context, self.service.unlink(update.effective_chat.id))

        async def help_(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await reply(update, context, self.service.help(update.effective_chat.id))

        async def message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            text = update.effective_message.text or ""
            await reply(update, context, self.service.message(update.effective_chat.id, text))

        async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            logger.error("Error en el bot de Telegram", exc_info=context.error)

        self.app.add_handler(CommandHandler("start", start))
        self.app.add_handler(CommandHandler("nueva", new))
        self.app.add_handler(CommandHandler("desvincular", unlink))
        self.app.add_handler(CommandHandler(["ayuda", "help"], help_))
        # Solo chats privados: el bot no responde notas en grupos
        self.app.add_handler(
            MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, message)
        )
        self.app.add_error_handler(on_error)

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.bot.set_my_commands(COMMANDS)
        if self.webhook:
            await self.app.bot.set_webhook(
                self.webhook,
                secret_token=self.settings.telegram_webhook_secret.get_secret_value() or None,
                allowed_updates=Update.ALL_TYPES,
            )
            logger.info("Bot de Telegram en modo webhook: %s", self.webhook)
        else:
            # start_polling borra un webhook previo para poder recibir por polling
            await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            logger.info("Bot de Telegram en modo polling")
        await self.app.start()

    async def stop(self) -> None:
        if self.app.updater and self.app.updater.running:
            await self.app.updater.stop()
        if self.app.running:
            await self.app.stop()
        await self.app.shutdown()

    def feed(self, data: dict) -> None:
        """Procesa una actualización recibida por webhook sin hacer esperar a Telegram."""
        update = Update.de_json(data, self.app.bot)
        task = asyncio.create_task(self.app.process_update(update))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
