import asyncio

import pytest

from api import auth_routes
from job_monitor import telegram_client
from job_monitor.db import connection


class FakeTelegramClient:
    """Captures constructor args instead of touching real telethon internals
    (the real TelegramClient raises ValueError if api_id/api_hash are falsy,
    which would mask rather than reproduce the bug this test pins)."""

    last_args = None

    def __init__(self, session, api_id, api_hash):
        FakeTelegramClient.last_args = (session, api_id, api_hash)
        self.disconnected = False

    async def disconnect(self):
        # `reset_client()` обязан отпустить старого клиента, а не просто
        # забыть про него: два подключения к одной сессии Telethon — источник
        # трудноуловимых отказов.
        self.disconnected = True


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    connection.reset_connection()
    asyncio.run(telegram_client.reset_client())
    yield
    asyncio.run(telegram_client.reset_client())
    connection.reset_connection()


def test_uses_the_shared_telegram_client_not_a_local_singleton():
    """L8: auth_routes used to build its own TelegramClient against a second
    session file (telegram_web.session) — a second, independent login to
    the same account. It must now go through job_monitor.telegram_client,
    the one client the TG worker also uses."""
    assert auth_routes.get_client is telegram_client.get_client


def test_get_client_builds_with_api_hash_from_env(tmp_path, monkeypatch):
    """C1: api/auth_routes.py used to build the Telethon client with
    api_hash='' because it read `load_config()["api_hash"]` (a key
    load_config() never sets) and an unloaded-into-this-process os.getenv
    fallback. This pins that the client is built from job_monitor.settings
    .load_secrets(), which actually reads .env."""
    (tmp_path / ".env").write_text(
        "TG_API_ID=12345\nTG_API_HASH=deadbeefcafef00d\n", encoding="utf-8"
    )
    monkeypatch.setattr(telegram_client, "TelegramClient", FakeTelegramClient)

    client = auth_routes.get_client()

    assert isinstance(client, FakeTelegramClient)
    _session, api_id, api_hash = FakeTelegramClient.last_args
    assert api_id == 12345
    assert api_hash == "deadbeefcafef00d"


def test_get_client_is_a_singleton(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "TG_API_ID=1\nTG_API_HASH=abc\n", encoding="utf-8"
    )
    monkeypatch.setattr(telegram_client, "TelegramClient", FakeTelegramClient)

    first = auth_routes.get_client()
    second = auth_routes.get_client()
    assert first is second


# ── Поведение роутов входа в Telegram ─────────────────────────────────
#
# Всё выше проверяет, ОТКУДА берётся клиент. Сами роуты не вызывал ни один
# тест, и мутационный аудит показал результат: 5 мутантов из 5 выжили —
# `{"authorized": False}` → `True`, снятие `not` в ветке 2FA и в обоих
# 401-гардах, снятие `not` в `if not msg.text`. Это модуль, через который в
# приложение попадает доступ к аккаунту Telegram: вход по коду, 2FA, чтение
# и отправка сообщений от лица пользователя.
#
# Цена мутаций тут не «утечка» (все `/api/auth/*` под токеном, а Telethon
# сам не даст работать неавторизованной сессии), а «UI молча показывает не
# то»: инвертированный `authorized` рисует зелёный статус на мёртвой
# сессии, инвертированный `if not msg.text` опустошает окно чата, а
# пропавшая ветка 2FA превращает вход с двухфакторной защитой в
# `sign_in(password=None)`.
#
# Двойник Telethon — не заглушка «чтобы прошло»: он воспроизводит ровно те
# формы, на которые роуты и рассчитаны, включая настоящий
# `SessionPasswordNeededError`. В сеть при этом не ходит ничего.

from datetime import datetime

from telethon.errors import SessionPasswordNeededError

from job_monitor.security import APP_TOKEN, TOKEN_HEADER

AUTH = {TOKEN_HEADER: APP_TOKEN}


class FakeMessage:
    def __init__(self, id: int, text: str | None, out: bool = False):
        self.id = id
        self.text = text
        self.out = out
        self.date = datetime(2024, 5, 17, 12, 30)


class FakeCodeRequest:
    phone_code_hash = "hash-from-telegram"


class FakeAuthClient:
    """Двойник Telethon ровно на тех вызовах, которые делает api/auth_routes."""

    def __init__(
        self,
        authorized: bool = True,
        status_error: Exception | None = None,
        needs_password: bool = False,
        messages: list[FakeMessage] | None = None,
    ):
        self.authorized = authorized
        self.status_error = status_error
        self.needs_password = needs_password
        self.messages = messages or []
        self.sign_in_calls: list[dict] = []
        self.sent: list[tuple[str, str]] = []
        self.connected = 0

    async def connect(self):
        self.connected += 1

    async def is_user_authorized(self):
        if self.status_error is not None:
            raise self.status_error
        return self.authorized

    async def send_code_request(self, phone):
        return FakeCodeRequest()

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        self.sign_in_calls.append(
            {"phone": phone, "code": code, "phone_code_hash": phone_code_hash,
             "password": password}
        )
        if password is None and self.needs_password:
            raise SessionPasswordNeededError(request=None)
        self.authorized = True

    def iter_messages(self, entity, limit=None):
        recorded = self.messages

        async def generator():
            for message in recorded[:limit]:
                yield message

        return generator()

    async def send_message(self, entity, text):
        self.sent.append((entity, text))


def _install(monkeypatch, fake: FakeAuthClient) -> FakeAuthClient:
    monkeypatch.setattr(auth_routes, "get_client", lambda: fake)
    return fake


# ── GET /api/auth/status ──────────────────────────────────────────────


def test_status_reports_an_authorized_session(client, monkeypatch):
    _install(monkeypatch, FakeAuthClient(authorized=True))
    body = client.get("/api/auth/status", headers=AUTH).json()
    assert body == {"authorized": True}


def test_status_reports_an_unauthorized_session(client, monkeypatch):
    _install(monkeypatch, FakeAuthClient(authorized=False))
    assert client.get("/api/auth/status", headers=AUTH).json()["authorized"] is False


def test_status_reports_not_authorized_when_telethon_fails(client, monkeypatch):
    """Отказ Telethon — это НЕ «авторизован».

    Мутация `{"authorized": False, ...}` → `True` рисует пользователю
    зелёный статус «✓ Авторизован — чаты доступны» (frontend/app.js::
    checkWebAuth) на мёртвой сессии и прячет форму входа, то есть
    единственный способ починиться.
    """
    _install(monkeypatch, FakeAuthClient(status_error=ConnectionError("нет связи")))
    body = client.get("/api/auth/status", headers=AUTH).json()
    assert body["authorized"] is False
    assert "нет связи" in body["error"]


# ── POST /api/auth/send-code и verify-code ────────────────────────────


def test_send_code_returns_the_phone_hash_the_frontend_needs(client, monkeypatch):
    _install(monkeypatch, FakeAuthClient(authorized=False))
    body = client.post("/api/auth/send-code", json={"phone": "+70000000000"},
                       headers=AUTH).json()
    assert body["status"] == "code_sent"
    assert body["phone_hash"] == FakeCodeRequest.phone_code_hash


def test_send_code_short_circuits_an_already_authorized_session(client, monkeypatch):
    _install(monkeypatch, FakeAuthClient(authorized=True))
    body = client.post("/api/auth/send-code", json={"phone": "+70000000000"},
                       headers=AUTH).json()
    assert body == {"status": "already_authorized"}


def test_verify_code_signs_in_with_the_code(client, monkeypatch):
    fake = _install(monkeypatch, FakeAuthClient(authorized=False))
    response = client.post(
        "/api/auth/verify-code",
        json={"phone": "+70000000000", "code": "12345", "phone_hash": "h"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "authorized"}
    assert fake.sign_in_calls == [
        {"phone": "+70000000000", "code": "12345", "phone_code_hash": "h", "password": None}
    ]


def test_verify_code_asks_for_the_second_factor_in_the_shape_the_frontend_reads(
    client, monkeypatch
):
    """Форма ответа — часть контракта, а не деталь.

    `frontend/app.js` смотрит именно на 428 и на `detail == '2FA_REQUIRED'`,
    чтобы показать поле пароля. Мутация «снять `not`» отправляла бы вместо
    этого `sign_in(password=None)` в Telethon.
    """
    fake = _install(monkeypatch, FakeAuthClient(authorized=False, needs_password=True))
    response = client.post(
        "/api/auth/verify-code",
        json={"phone": "+70000000000", "code": "12345", "phone_hash": "h"},
        headers=AUTH,
    )
    assert response.status_code == 428, response.text
    assert response.json() == {"detail": "2FA_REQUIRED"}
    # Пароля не было — второй sign_in делать нечем, и попытки быть не должно.
    assert [call["password"] for call in fake.sign_in_calls] == [None]


def test_verify_code_completes_the_second_factor_with_the_password(client, monkeypatch):
    fake = _install(monkeypatch, FakeAuthClient(authorized=False, needs_password=True))
    response = client.post(
        "/api/auth/verify-code",
        json={"phone": "+70000000000", "code": "12345", "phone_hash": "h",
              "password": "две-звезды"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "authorized"}
    assert [call["password"] for call in fake.sign_in_calls] == [None, "две-звезды"]


def test_verify_code_reports_a_wrong_code_as_a_client_error(client, monkeypatch):
    class Rejecting(FakeAuthClient):
        async def sign_in(self, *args, **kwargs):
            raise ValueError("The confirmation code is invalid")

    _install(monkeypatch, Rejecting(authorized=False))
    response = client.post(
        "/api/auth/verify-code",
        json={"phone": "+70000000000", "code": "00000", "phone_hash": "h"},
        headers=AUTH,
    )
    assert response.status_code == 400, response.text


# ── Гарды 401 на чтении и отправке сообщений ──────────────────────────


def test_reading_messages_requires_an_authorized_telegram_session(client, monkeypatch):
    _install(monkeypatch, FakeAuthClient(authorized=False,
                                        messages=[FakeMessage(1, "секрет")]))
    response = client.get("/api/auth/messages/hr_anna", headers=AUTH)
    assert response.status_code == 401, response.text
    assert "секрет" not in response.text


def test_sending_a_message_requires_an_authorized_telegram_session(client, monkeypatch):
    fake = _install(monkeypatch, FakeAuthClient(authorized=False))
    response = client.post("/api/auth/messages/hr_anna", json={"text": "привет"},
                           headers=AUTH)
    assert response.status_code == 401, response.text
    assert fake.sent == [], "сообщение ушло из неавторизованной сессии"


def test_reading_messages_returns_oldest_first_and_skips_empty_ones(client, monkeypatch):
    """`if not msg.text: continue` пропускает вложения без подписи.

    Со снятым `not` в окно чата попадали бы ТОЛЬКО сообщения без текста,
    то есть окно выглядело бы пустым при живой переписке.
    """
    _install(monkeypatch, FakeAuthClient(
        authorized=True,
        messages=[
            FakeMessage(3, "последнее", out=True),
            FakeMessage(2, None),                       # стикер/фото без подписи
            FakeMessage(1, "первое"),
        ],
    ))
    body = client.get("/api/auth/messages/@hr_anna", headers=AUTH).json()

    assert [message["text"] for message in body] == ["первое", "последнее"], (
        "порядок в окне чата — от старых к новым, а сообщения без текста "
        "показывать нечем"
    )
    assert body[1]["out"] is True
    assert body[0]["date"] == "2024-05-17 12:30"


def test_sending_a_message_strips_the_leading_at_sign(client, monkeypatch):
    fake = _install(monkeypatch, FakeAuthClient(authorized=True))
    response = client.post("/api/auth/messages/@hr_anna", json={"text": "привет"},
                           headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "sent"}
    assert fake.sent == [("hr_anna", "привет")]


# ── Ни один ответ не несёт секретов ───────────────────────────────────


def test_no_auth_response_carries_the_telegram_credentials(client, monkeypatch, tmp_path):
    """`.env` рядом, и клиент собирается из него — но в ответах роутов ни
    `api_hash`, ни `api_id` появиться не должны ни при каком исходе."""
    planted_hash = "deadbeefcafef00ddeadbeefcafef00d"
    (tmp_path / ".env").write_text(
        f"TG_API_ID=12345\nTG_API_HASH={planted_hash}\n", encoding="utf-8"
    )
    _install(monkeypatch, FakeAuthClient(authorized=True,
                                         messages=[FakeMessage(1, "текст")]))

    bodies = [
        client.get("/api/auth/status", headers=AUTH).text,
        client.post("/api/auth/send-code", json={"phone": "+70000000000"},
                    headers=AUTH).text,
        client.post("/api/auth/verify-code",
                    json={"phone": "+70000000000", "code": "1", "phone_hash": "h"},
                    headers=AUTH).text,
        client.get("/api/auth/messages/hr_anna", headers=AUTH).text,
        client.post("/api/auth/messages/hr_anna", json={"text": "привет"},
                    headers=AUTH).text,
    ]
    for body in bodies:
        assert planted_hash not in body
        assert "12345" not in body
        assert "api_hash" not in body


def test_status_without_api_keys_is_not_a_server_error(client, monkeypatch) -> None:
    """`get_client()` стоял ВНЕ `try`, хотя `try` заводился ровно под этот
    отказ: без `TG_API_ID`/`TG_API_HASH` он бросает `RuntimeError`, и роут
    отдавал 500.

    Это состояние не исключительное, а стартовое: у нового пользователя
    ключей ещё нет — он как раз пришёл в «Настройки» их ввести. Экран при
    этом показывал «Проверка…» навсегда и ронял ошибку в консоль браузера,
    вместо того чтобы сказать «не авторизован» и открыть форму входа.
    """
    from job_monitor import telegram_client

    def no_keys():
        raise RuntimeError("TG_API_ID и TG_API_HASH не заданы")

    monkeypatch.setattr(telegram_client, "get_client", no_keys)
    monkeypatch.setattr("api.auth_routes.get_client", no_keys)

    reply = client.get("/api/auth/status")
    assert reply.status_code == 200
    body = reply.json()
    assert body["authorized"] is False
    assert "TG_API_ID" in body["error"], (
        "причина должна доехать до интерфейса: без неё человек не поймёт, "
        "что не хватает именно ключей"
    )
