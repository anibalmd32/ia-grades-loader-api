import pytest
from pydantic import SecretStr

from app.ai.client import get_llm
from app.core.config import get_settings
from app.core.rate_limit import RateLimitedError, RateLimiter
from app.main import app as fastapi_app
from app.models import ChatChannel, User
from app.services import conversations
from app.telegram.handlers import TelegramBotService


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_ventana_deslizante():
    clock = Clock()
    limiter = RateLimiter(lambda: 2, window_seconds=60, clock=clock)
    limiter.hit("ana")
    clock.now += 10
    limiter.hit("ana")
    with pytest.raises(RateLimitedError) as exc:
        limiter.hit("ana")
    assert exc.value.retry_after == 50  # el primer uso sale de la ventana en 50 s
    limiter.hit("beto")  # cada clave tiene su propia cuenta

    clock.now += 51
    limiter.hit("ana")  # ya salió el primero


def test_cero_desactiva_el_limite():
    limiter = RateLimiter(lambda: 0)
    for _ in range(100):
        limiter.hit("ana")


class FakeLLM:
    async def run_tools(self, messages, tools, call_tool):
        return "ok"


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_rate_limit_per_minute", 2)
    fastapi_app.dependency_overrides[get_llm] = FakeLLM
    yield
    fastapi_app.dependency_overrides.pop(get_llm, None)


async def test_chat_responde_429_al_superar_el_limite(client, campus, limited):
    headers = campus["students"]["Ana"]["headers"]
    for _ in range(2):
        response = await client.post("/student/chat", json={"message": "?"}, headers=headers)
        assert response.status_code == 200
    blocked = await client.post("/student/chat", json={"message": "?"}, headers=headers)
    assert blocked.status_code == 429
    assert "Espera" in blocked.json()["detail"]

    # Otro estudiante no se ve afectado
    beto = campus["students"]["Beto"]["headers"]
    assert (
        await client.post("/student/chat", json={"message": "?"}, headers=beto)
    ).status_code == 200


async def test_el_limite_se_comparte_con_telegram(
    client, campus, limited, session, sessionmaker, monkeypatch
):
    monkeypatch.setattr(get_settings(), "telegram_bot_token", SecretStr("123:abc"))
    ana = campus["students"]["Ana"]
    await client.post("/student/chat", json={"message": "?"}, headers=ana["headers"])

    student = await session.get(User, ana["id"])
    student.telegram_chat_id = 777
    await session.commit()
    await conversations.create(session, ana["id"], ChatChannel.TELEGRAM)

    bot = TelegramBotService(sessionmaker, FakeLLM)
    assert await bot.message(777, "?") == "ok"
    assert "Espera" in await bot.message(777, "?")  # 3.ª pregunta del minuto
