"""`GET /api/state` — единственный периодический запрос дашборда (L13).

Раз он один, каждое поле в нём должно кому-то понадобиться. `hh.login_state`
не понадобилось никому: фронтенд брал состояние входа отдельным
`GET /api/hh/login/status`, причём ровно один раз — при открытии страницы
настроек, — так что строка на экране устаревала молча (приложение
перезапустили, окна давно нет, а написано «Окно открыто»). Два источника
одной правды, из которых живым был худший.

Дубль убран в пользу `/api/state`: он уже опрашивается каждые 3 секунды,
поэтому состояние перестало устаревать и появилось на что дизейблить кнопку
«Я вошёл». Этот файл держит инвариант в обе стороны — новое поле в ответе
нельзя завести и не прочитать, а прочитанное нельзя выкинуть из ответа.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "frontend/app.js"

# Поля, которых во фронтенде нет и не должно быть, с причиной. Не «список
# исключений на всякий случай»: каждая строка — обещание, что поле не забыто.
NOT_READ_BY_THE_UI = {
    "name": "имя воркера; во фронтенде оно и так известно из того, какую кнопку рисуем",
    "started_at": (
        "момент запуска. Кнопка показывает состояние, а не аптайм; вводить его в UI —"
        " продуктовое решение, а не уборка долга"
    ),
    "channels_count": (
        "число каналов; страница настроек показывает сам список из GET /api/config,"
        " а на дашборде счётчика каналов нет"
    ),
}


def _app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _state_payload(client) -> dict:
    response = client.get("/api/state")
    assert response.status_code == 200, response.text
    return response.json()


def _leaf_keys(payload: dict) -> set[str]:
    """Имена полей внутри `tg` и `hh`. `recent` — список строк
    worker_events, он разбирается `renderRecent()` целиком и сюда не идёт."""
    keys: set[str] = set()
    for section in ("tg", "hh"):
        assert section in payload, f"в ответе нет секции {section!r}"
        keys.update(payload[section])
    assert len(keys) >= 10, f"полей всего {len(keys)} — форма ответа изменилась?"
    return keys


def test_every_field_of_the_state_payload_has_a_reader(client) -> None:
    source = _app_source()
    unread = sorted(
        key
        for key in _leaf_keys(_state_payload(client))
        if key not in NOT_READ_BY_THE_UI and not re.search(rf"\.{re.escape(key)}\b", source)
    )
    assert not unread, (
        f"GET /api/state отдаёт поля, которых не читает frontend/app.js: {unread}. "
        "Либо начните их читать, либо уберите из ответа, либо внесите в "
        "NOT_READ_BY_THE_UI с объяснением."
    )


def test_the_exemptions_still_describe_real_fields(client) -> None:
    """Исключение, пережившее переименование поля, — это забытая строка,
    которая молча покрывает не то, что задумано."""
    stale = sorted(set(NOT_READ_BY_THE_UI) - _leaf_keys(_state_payload(client)))
    assert not stale, f"NOT_READ_BY_THE_UI называет поля, которых в ответе нет: {stale}"


def test_the_login_state_is_the_one_the_ui_uses(client) -> None:
    """Конкретный случай, ради которого написан файл: состояние входа в hh.ru
    приходит в `/api/state` и читается оттуда, а не отдельным запросом."""
    assert "login_state" in _state_payload(client)["hh"]
    source = _app_source()
    assert bool(re.search(r"\bhh\.login_state\b", source)), (
        "фронтенд не читает login_state из /api/state"
    )
    # Ищется именно вызов, а не упоминание: комментарий рядом с
    # applyHHLoginState() объясняет, откуда взялся дубль, и называет
    # удалённый роут по имени — это история, а не запрос.
    assert not bool(re.search(r"api(?:Get|Post)\([^)]*/hh/login/status", source)), (
        "фронтенд снова ходит за состоянием входа отдельным запросом — вернулись"
        " два источника одного состояния"
    )


@pytest.mark.parametrize("path", ["/api/hh/login/status"])
def test_the_duplicate_login_endpoint_is_gone(client, path: str) -> None:
    assert client.get(path).status_code == 404, (
        f"{path} снова существует: login.state отдаётся в GET /api/state, и второй"
        " роут для того же значения — это то, что расходится"
    )


def test_the_reader_scan_is_not_vacuous(client) -> None:
    """Проверка обязана падать на поле, которое никто не читает."""
    source = _app_source()
    assert not re.search(r"\.definitely_not_a_field\b", source)
    assert re.search(r"\.sent_today\b", source), "сканер не видит даже читаемого поля"
