from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import get_settings
from app.models import User
from app.models.import_job import utcnow
from app.models.telegram_link import TelegramLinkCode
from app.services import telegram_link
from app.services.telegram_link import InvalidLinkCodeError

CHAT_ANA = 1001
CHAT_OTRO = 2002


@pytest.fixture(autouse=True)
def telegram_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:abc"))
    monkeypatch.setattr(settings, "telegram_bot_username", "VerMisNotasBot")


def code_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["start"][0]


async def new_code(client, headers) -> str:
    response = await client.post("/student/telegram/link", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"].startswith("https://t.me/VerMisNotasBot?start=")
    return code_from(body["url"])


async def test_vincular_con_codigo(client, campus, session):
    ana = campus["students"]["Ana"]
    status = (await client.get("/student/telegram", headers=ana["headers"])).json()
    assert status == {"enabled": True, "linked": False, "bot_username": "VerMisNotasBot"}

    code = await new_code(client, ana["headers"])
    student = await telegram_link.consume(session, code, CHAT_ANA)
    assert student.id == ana["id"]
    assert (await telegram_link.student_for_chat(session, CHAT_ANA)).id == ana["id"]

    status = (await client.get("/student/telegram", headers=ana["headers"])).json()
    assert status["linked"] is True


async def test_el_codigo_es_de_un_solo_uso_y_no_se_guarda_en_claro(client, campus, session):
    code = await new_code(client, campus["students"]["Ana"]["headers"])
    stored = (await session.get(TelegramLinkCode, code)) is None
    assert stored  # se guarda el hash, no el código
    await telegram_link.consume(session, code, CHAT_ANA)
    with pytest.raises(InvalidLinkCodeError):
        await telegram_link.consume(session, code, CHAT_OTRO)


async def test_solo_vale_el_ultimo_codigo(client, campus, session):
    headers = campus["students"]["Ana"]["headers"]
    first = await new_code(client, headers)
    second = await new_code(client, headers)
    with pytest.raises(InvalidLinkCodeError):
        await telegram_link.consume(session, first, CHAT_ANA)
    await telegram_link.consume(session, second, CHAT_ANA)


async def test_codigo_caducado(client, campus, session):
    code = await new_code(client, campus["students"]["Ana"]["headers"])
    stored = await session.scalar(select(TelegramLinkCode))
    stored.expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()
    with pytest.raises(InvalidLinkCodeError):
        await telegram_link.consume(session, code, CHAT_ANA)


async def test_codigo_inventado(session):
    with pytest.raises(InvalidLinkCodeError):
        await telegram_link.consume(session, "no-existe", CHAT_ANA)


async def test_un_telegram_solo_puede_estar_en_una_cuenta(client, campus, session):
    ana, beto = campus["students"]["Ana"], campus["students"]["Beto"]
    await telegram_link.consume(session, await new_code(client, ana["headers"]), CHAT_ANA)
    # El mismo Telegram se vincula ahora a Beto: Ana queda desvinculada
    await telegram_link.consume(session, await new_code(client, beto["headers"]), CHAT_ANA)
    session.expire_all()
    assert (await telegram_link.student_for_chat(session, CHAT_ANA)).id == beto["id"]
    assert (await session.get(User, ana["id"])).telegram_chat_id is None


async def test_estudiante_desactivado_no_se_vincula_ni_se_reconoce(client, campus, session):
    ana = campus["students"]["Ana"]
    code = await new_code(client, ana["headers"])
    user = await session.get(User, ana["id"])
    user.is_active = False
    await session.commit()
    with pytest.raises(InvalidLinkCodeError):
        await telegram_link.consume(session, code, CHAT_ANA)

    user.is_active, user.telegram_chat_id = True, CHAT_ANA
    await session.commit()
    user.is_active = False
    await session.commit()
    assert await telegram_link.student_for_chat(session, CHAT_ANA) is None


async def test_desvincular(client, campus, session):
    ana = campus["students"]["Ana"]
    await telegram_link.consume(session, await new_code(client, ana["headers"]), CHAT_ANA)
    assert (await client.delete("/student/telegram", headers=ana["headers"])).status_code == 204
    session.expire_all()
    assert await telegram_link.student_for_chat(session, CHAT_ANA) is None


async def test_sin_bot_configurado(client, campus, monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_bot_token", SecretStr(""))
    headers = campus["students"]["Ana"]["headers"]
    assert (await client.post("/student/telegram/link", headers=headers)).status_code == 503
    assert (await client.get("/student/telegram", headers=headers)).json()["enabled"] is False
