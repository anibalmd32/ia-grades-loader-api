from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.main import app as fastapi_app
from app.models import ChatChannel, Conversation, User
from app.telegram.bot import webhook_url
from app.telegram.handlers import (
    MAX_MESSAGE_LENGTH,
    NOT_LINKED,
    TelegramBotService,
    split_message,
    to_telegram_html,
)

CHAT = 555


class FakeLLM:
    def __init__(self, answer: str | Exception = "Vas **bien** en todo"):
        self.answer = answer
        self.calls: list[list[dict]] = []

    async def run_tools(self, messages, tools, call_tool):
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture(autouse=True)
def telegram_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:abc"))
    monkeypatch.setattr(settings, "telegram_bot_username", "VerMisNotasBot")


@pytest.fixture
def bot(sessionmaker):
    llm = FakeLLM()
    service = TelegramBotService(sessionmaker, lambda: llm)
    service.fake_llm = llm
    return service


async def link(client, headers, bot) -> str:
    url = (await client.post("/student/telegram/link", headers=headers)).json()["url"]
    return await bot.start(CHAT, parse_qs(urlparse(url).query)["start"][0])


# --- Conversación ------------------------------------------------------------


async def test_sin_vincular_explica_como_hacerlo(bot):
    assert await bot.message(CHAT, "¿Cómo voy?") == NOT_LINKED
    assert await bot.start(CHAT, None) == NOT_LINKED
    assert bot.fake_llm.calls == []


async def test_vincular_y_preguntar(client, campus, bot, session):
    welcome = await link(client, campus["students"]["Ana"]["headers"], bot)
    assert welcome.startswith("¡Listo, Ana!")

    answer = await bot.message(CHAT, "¿Cómo voy?")
    assert answer == "Vas <b>bien</b> en todo"
    system, question = bot.fake_llm.calls[0][0], bot.fake_llm.calls[0][-1]
    assert "Ana" in system["content"]
    assert "Telegram" in system["content"]  # estilo adaptado a Telegram
    assert question == {"role": "user", "content": "¿Cómo voy?"}

    conversation = await session.scalar(
        select(Conversation).where(Conversation.channel == ChatChannel.TELEGRAM)
    )
    assert conversation.student_id == campus["students"]["Ana"]["id"]


async def test_recuerda_el_contexto_y_nueva_conversacion(client, campus, bot):
    await link(client, campus["students"]["Ana"]["headers"], bot)
    await bot.message(CHAT, "¿Cómo voy en Física?")
    await bot.message(CHAT, "¿Y en Mate?")
    assert [m["content"] for m in bot.fake_llm.calls[-1][1:]] == [
        "¿Cómo voy en Física?",
        "Vas **bien** en todo",
        "¿Y en Mate?",
    ]

    assert "nueva" in (await bot.new_conversation(CHAT))
    await bot.message(CHAT, "Hola")
    assert [m["role"] for m in bot.fake_llm.calls[-1]] == ["system", "user"]


async def test_codigo_invalido(bot):
    assert "no es válido" in await bot.start(CHAT, "inventado")


async def test_desvincular_desde_telegram(client, campus, bot, session):
    await link(client, campus["students"]["Ana"]["headers"], bot)
    assert "ya no está vinculado" in await bot.unlink(CHAT)
    assert await bot.message(CHAT, "hola") == NOT_LINKED
    session.expire_all()
    assert (await session.get(User, campus["students"]["Ana"]["id"])).telegram_chat_id is None


async def test_si_la_ia_falla_responde_con_amabilidad(client, campus, bot):
    await link(client, campus["students"]["Ana"]["headers"], bot)
    bot.fake_llm.answer = AppError("caído")
    assert "No pude responder" in await bot.message(CHAT, "¿Cómo voy?")


# --- Formato -----------------------------------------------------------------


def test_markdown_a_html_de_telegram():
    md = "### Física\n- Lapso 1: **8.0**\n* Lapso 2: *pendiente*\nUsa `x<y` & sigue"
    assert to_telegram_html(md).splitlines() == [
        "<b>Física</b>",
        "• Lapso 1: <b>8.0</b>",
        "• Lapso 2: <i>pendiente</i>",
        "Usa <code>x&lt;y</code> &amp; sigue",
    ]


def test_html_del_modelo_se_escapa():
    assert to_telegram_html("<script>x</script>") == "&lt;script&gt;x&lt;/script&gt;"


def test_partir_mensajes_largos():
    paragraph = "a" * 3000
    parts = split_message(f"{paragraph}\n\n{paragraph}\n\n{'b' * 5000}")
    assert all(len(p) <= MAX_MESSAGE_LENGTH for p in parts)
    assert "".join(parts).replace("\n", "") == "a" * 6000 + "b" * 5000


# --- Polling o webhook -------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", None),
        ("http://localhost:8000", None),
        ("https://localhost/api/v1/telegram/webhook", None),
        ("http://mi-dominio.com/api/v1/telegram/webhook", None),
        (
            "https://abc.ngrok-free.app/api/v1/telegram/webhook",
            "https://abc.ngrok-free.app/api/v1/telegram/webhook",
        ),
    ],
)
def test_url_de_webhook(url, expected):
    assert webhook_url(Settings(telegram_webhook_url=url)) == expected


class FakeRunningBot:
    webhook = "https://x/api/v1/telegram/webhook"

    def __init__(self):
        self.updates = []

    def feed(self, data):
        self.updates.append(data)


@pytest.fixture
def webhook_bot(monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", SecretStr("s3cr3t"))
    fake = FakeRunningBot()
    fastapi_app.state.telegram_bot = fake
    yield fake
    fastapi_app.state.telegram_bot = None


async def test_webhook_exige_el_secreto(client, webhook_bot):
    update = {"update_id": 1}
    bad = await client.post(
        "/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "no"}
    )
    assert bad.status_code == 401
    assert (await client.post("/telegram/webhook", json=update)).status_code == 401

    ok = await client.post(
        "/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"}
    )
    assert ok.status_code == 200
    assert webhook_bot.updates == [update]


async def test_webhook_inactivo_en_modo_polling(client):
    fastapi_app.state.telegram_bot = None
    assert (await client.post("/telegram/webhook", json={})).status_code == 404
