import os
import stat
from pathlib import Path

import pytest

from job_monitor import envfile
from job_monitor.settings import load_secrets

from conftest import TELEGRAM_SECRET_VARS


def test_rejects_newline_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        envfile.write_env({"TG_API_ID": "123\nSAFE_MODE=false"})


def test_env_file_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", "TG_API_HASH": "abc"})
    mode = stat.S_IMODE((tmp_path / ".env").stat().st_mode)
    assert mode == 0o600


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", "HTTPS_PROXY": "http://127.0.0.1:3128"})
    assert envfile.read_env() == {"TG_API_ID": "123", "HTTPS_PROXY": "http://127.0.0.1:3128"}


def test_read_env_rejects_null_byte_in_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("TG_API_HASH=abc\x00def\n", encoding="utf-8")
    with pytest.raises(ValueError):
        envfile.read_env()


def test_write_env_preserves_previously_written_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_HASH": "abc"})
    envfile.write_env({"HTTPS_PROXY": "http://127.0.0.1:3128"})
    assert envfile.read_env() == {
        "TG_API_HASH": "abc", "HTTPS_PROXY": "http://127.0.0.1:3128",
    }


# Ключи перечислены здесь буквально, а не взяты из `envfile.RETIRED_KEYS`:
# тесты ниже должны падать на СВОЙСТВЕ («мёртвый ключ остался в файле»), а не
# на отсутствии константы. Синхронность списков держит отдельная проверка.
RETIRED_IN_ENV = ("SAFE_MODE", "PARSE_HISTORY", "HISTORY_LIMIT")


def test_the_retired_key_list_matches_the_module() -> None:
    assert set(envfile.RETIRED_KEYS) == set(RETIRED_IN_ENV), (
        "список мёртвых ключей разошёлся с job_monitor/envfile.py — тесты ниже "
        "перестали покрывать часть из них"
    )


@pytest.mark.parametrize("retired", RETIRED_IN_ENV)
def test_a_retired_key_is_swept_out_on_the_next_write(tmp_path, monkeypatch, retired):
    """`.env` пользователя, обновившегося с версии, которая писала туда копию
    прикладных настроек, носил бы `SAFE_MODE=false` вечно: `write_env`
    сливается со старым содержимым. Читателя у ключа нет, но его ВИД и был
    первопричиной находки — пользователь правит `SAFE_MODE` на `true` и
    считает себя в безопасном режиме, пока воркер читает `safe_mode` из базы
    и продолжает писать людям."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        f"TG_API_ID=123\n{retired}=false\nHTTPS_PROXY=http://127.0.0.1:3128\n",
        encoding="utf-8",
    )

    envfile.write_env({"TG_API_HASH": "abc"})

    assert envfile.read_env() == {
        "TG_API_ID": "123",
        "TG_API_HASH": "abc",
        "HTTPS_PROXY": "http://127.0.0.1:3128",
    }, "мёртвый ключ остался, либо вместе с ним снесло что-то пользовательское"


@pytest.mark.parametrize("retired", RETIRED_IN_ENV)
def test_a_retired_key_cannot_be_written_back(tmp_path, monkeypatch, retired):
    """Вычистка стоит после слияния, поэтому мёртвый ключ не вернуть даже
    прямым вызовом: в `.env` живут только секреты (D5)."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "123", retired: "false"})
    assert envfile.read_env() == {"TG_API_ID": "123"}


def test_write_env_cleans_up_temp_file_on_failure(tmp_path, monkeypatch):
    """write_env() writes through a temp file (mkstemp) then os.replace()s it
    onto .env. A failure anywhere in between used to leave that 0600 temp
    file sitting in the data directory forever — no try/finally around the
    sequence."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(envfile.os, "replace", boom)
    with pytest.raises(OSError):
        envfile.write_env({"TG_API_ID": "123"})

    leftover = list(tmp_path.iterdir())
    assert leftover == [], f"temp file(s) left behind after a failed write: {leftover}"


# ── Сторож: настоящие ключи Telegram не видны из тестов ───────────────


@pytest.mark.parametrize("name", TELEGRAM_SECRET_VARS)
def test_real_telegram_credentials_are_invisible_to_the_suite(name):
    """Падает, если прогон видит настоящие `TG_API_ID`/`TG_API_HASH`.

    Разработчик, следующий SECURITY.md, держит их в окружении. Без
    session-фикстуры `_no_real_telegram_credentials` любой тест, который
    входит в настоящий `lifespan` с `tg_autostart=True`, соединялся бы с
    Telegram его реальным `api_id` — молча, потому что исключение воркера
    глотает супервизор. Этот сторож — то, что делает промах видимым:
    сними фикстуру, запусти `env TG_API_ID=1 TG_API_HASH=x pytest`, и он
    покраснеет.
    """
    assert os.getenv(name) is None, (
        f"{name} виден тестам: фикстура _no_real_telegram_credentials из "
        "tests/conftest.py не сработала, и suite может уйти в сеть Telegram"
    )


def test_load_secrets_is_incomplete_without_credentials(tmp_path, monkeypatch):
    """Следствие сторожа выше на уровне приложения: `get_client()` в
    `job_monitor/telegram_client.py` строит `TelegramClient` только когда
    `Secrets.is_complete`, поэтому «секретов не видно» означает «клиента не
    будет»."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    assert load_secrets().is_complete is False


def test_reveal_hash_endpoint_is_gone(client):
    assert client.get("/api/config/reveal-hash").status_code == 404


def test_config_response_never_contains_hash(client):
    body = client.get("/api/config").json()
    assert "api_hash" not in body
    assert body["api_hash_set"] in (True, False)


# ── Битая руками строка `.env` не должна ронять сохранение ────────────


def test_a_line_without_a_name_is_skipped_instead_of_breaking_the_read(tmp_path, monkeypatch):
    """`=значение` доводило `_validate("")` до ValueError, `read_env()`
    бросал, и PATCH /api/config отвечал 500: пользователь не мог сохранить
    НИЧЕГО, пока не починит файл руками, — и узнавал об этом из «ошибка
    сервера». Строка без имени переменной не задаёт настройки; правильно её
    пропустить, сказав об этом в лог."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "TG_API_ID=123\n=забытое-имя\nTG_API_HASH\nTG_API_HASH=abc\n", encoding="utf-8"
    )

    assert envfile.read_env() == {"TG_API_ID": "123", "TG_API_HASH": "abc"}


def test_saving_settings_works_over_a_hand_broken_env(tmp_path, monkeypatch):
    """Сквозная проверка того же: сохранение должно проходить."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("=забытое-имя\nTG_API_ID=123\n", encoding="utf-8")

    envfile.write_env({"TG_API_HASH": "abc"})

    assert envfile.read_env() == {"TG_API_ID": "123", "TG_API_HASH": "abc"}


def test_the_broken_line_is_named_once_not_on_every_poll(tmp_path, monkeypatch, caplog):
    """`read_env()` зовётся из `load_secrets()`, а его дёргает
    `GET /api/state` — раз в три секунды на каждый открытый дашборд. Без
    дедупликации одна битая строка дописывала бы предупреждение в оба файла
    логов при каждом опросе, а логи отдаются в UI.

    В сообщении — номер строки, но не её содержимое: в `.env` лежат секреты.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("=oops-secret-looking-value\n", encoding="utf-8")
    monkeypatch.setattr(envfile, "_REPORTED_BAD_LINES", set())

    with caplog.at_level("WARNING", logger="job_monitor.envfile"):
        for _poll in range(5):
            envfile.read_env()

    warnings = [record for record in caplog.records if record.name == "job_monitor.envfile"]
    assert len(warnings) == 1, f"предупреждений {len(warnings)}, а опросов было 5"
    message = warnings[0].getMessage()
    assert "1" in message, message
    assert "oops-secret-looking-value" not in message, (
        f"содержимое строки .env попало в лог: {message}"
    )


def test_a_forbidden_character_still_stops_the_read(tmp_path, monkeypatch):
    """Граница снисходительности. Пропускается СТРУКТУРНО битая строка —
    опечатка. Запрещённый символ (`\\n`, `\\r`, `\\x00`) опечаткой не бывает:
    это признак того, что файл писали не руками, и это первопричина S7.
    Свойство «значение с таким символом не порождает переменной ни на
    чтении, ни на записи» сохраняется в самой строгой форме — чтение
    останавливается."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("TG_API_HASH=abc\x00def\n", encoding="utf-8")
    with pytest.raises(ValueError):
        envfile.read_env()


# ── write_env() не наводит порядок в чужом файле ──────────────────────


def test_write_env_keeps_the_users_comments_and_line_order(tmp_path, monkeypatch):
    """`read_env()` выбрасывает `#`-строки, а `write_env()` писал файл заново
    из словаря — то есть первое же сохранение съедало `# мой комментарий` и
    перетасовывало строки. Это тот же класс сюрприза, от которого заведён
    список RETIRED_KEYS: приложение убирает за собой, а не переписывает
    чужой файл."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# ключи от my.telegram.org, не терять\n"
        "TG_API_ID=123\n"
        "\n"
        "# прокси на работе\n"
        "HTTPS_PROXY=http://127.0.0.1:3128\n",
        encoding="utf-8",
    )

    envfile.write_env({"TG_API_HASH": "abc"})

    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "# ключи от my.telegram.org, не терять\n"
        "TG_API_ID=123\n"
        "\n"
        "# прокси на работе\n"
        "HTTPS_PROXY=http://127.0.0.1:3128\n"
        "TG_API_HASH=abc\n"
    )


def test_write_env_updates_a_key_in_place(tmp_path, monkeypatch):
    """Новое значение встаёт на место старого, а не уезжает в конец файла:
    иначе комментарий над ключом переставал относиться к тому, что под ним."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# идентификатор приложения\nTG_API_ID=123\nHTTPS_PROXY=http://127.0.0.1:3128\n",
        encoding="utf-8",
    )

    envfile.write_env({"TG_API_ID": "456"})

    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "# идентификатор приложения\nTG_API_ID=456\nHTTPS_PROXY=http://127.0.0.1:3128\n"
    )


def test_write_env_collapses_a_duplicated_key(tmp_path, monkeypatch):
    """Действующим `read_env()` считает ПОСЛЕДНЕЕ вхождение. Оставить рядом
    строку с другим значением значило бы записать в файл неправду, поэтому
    прежние вхождения выбрасываются."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("TG_API_ID=old\nTG_API_ID=123\n", encoding="utf-8")

    envfile.write_env({"TG_API_HASH": "abc"})

    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        "TG_API_ID=123\nTG_API_HASH=abc\n"
    )
    assert envfile.read_env() == {"TG_API_ID": "123", "TG_API_HASH": "abc"}


def test_a_retired_key_goes_away_but_its_neighbours_do_not(tmp_path, monkeypatch):
    """Вычистка мёртвых ключей не должна унести с собой ни комментарий, ни
    строку пользователя — проверка сохранения комментариев и вычистки в
    одном файле, потому что ломаются они друг о друга."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "TG_API_ID=123\n# безопасный режим\nSAFE_MODE=false\nHTTPS_PROXY=proxy\n",
        encoding="utf-8",
    )

    envfile.write_env({"TG_API_HASH": "abc"})

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "SAFE_MODE" not in text
    assert "# безопасный режим" in text, "комментарий пользователя пропал вместе с ключом"
    assert "HTTPS_PROXY=proxy" in text


# ── Разбор `.env` с комментариями и пустыми строками ──────────────────
#
# `envfile.read_env()` пропускает пустые строки и `#`-комментарии, и мутация
# `or` → `and` в этом условии проходила зелёной: ни один тест не читал `.env`
# с комментарием или пустой строкой. Между тем поставляемый `.env.example`
# состоит как раз из комментариев и двух присваиваний, а SECURITY.md
# предлагает пользователю его скопировать — то есть непроверенным оставался
# самый первый `GET /api/config` у нового пользователя.
#
# Волна C научила `write_env` СОХРАНЯТЬ комментарии (`_render`) и добавила
# `_key_of`, который заодно смягчил последствия этой мутации (структурно
# битая строка теперь пропускается, а не валит распаковку). Но свойство
# «комментарий не превращается в переменную» так и осталось без теста.

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_read_env_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# Ключи Telegram: my.telegram.org -> API development tools\n"
        "\n"
        "TG_API_ID=12345\n"
        "   \n"
        "  # отступ перед решёткой — тоже комментарий\n"
        "TG_API_HASH=deadbeefcafef00d\n"
        "\n",
        encoding="utf-8",
    )

    assert envfile.read_env() == {
        "TG_API_ID": "12345",
        "TG_API_HASH": "deadbeefcafef00d",
    }


def test_the_shipped_example_file_is_readable_as_an_env(tmp_path, monkeypatch):
    """SECURITY.md предлагает скопировать `.env.example` в `~/.job-monitor/.env`.
    Значит файл обязан читаться — иначе первый же `GET /api/config` у нового
    пользователя отвечал бы 500."""
    example = REPO_ROOT / ".env.example"
    assert example.exists(), ".env.example исчез, а документация на него ссылается"
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    parsed = envfile.read_env()

    assert set(parsed) <= {"TG_API_ID", "TG_API_HASH"}, (
        f"в примере `.env` появились посторонние ключи: {sorted(parsed)}"
    )
    # Страховка от вакуумности: в примере есть комментарии, и именно они —
    # предмет проверки. Пустой разбор пустого файла ничего не доказывает.
    assert "#" in example.read_text(encoding="utf-8"), (
        "в .env.example больше нет комментариев — проверка их пропуска стала "
        "вакуумной, замените её на файл с комментариями"
    )


def test_a_users_own_comment_is_not_reported_as_broken(tmp_path, monkeypatch, caplog):
    """Комментарий и пустая строка — не «битая строка».

    Волна C сделала разбор снисходительным (`_key_of` + `_report_broken`),
    и это заодно превратило мутацию `or` → `and` в условии пропуска
    комментариев из «500 на первом же запросе» в «поток жалоб на файл
    пользователя»: каждая его собственная `#`-строка получала бы
    предупреждение «строка N не задаёт переменной», а логи отдаются в UI.
    Результат разбора при этом не меняется — поэтому проверка смотрит
    именно на лог, а не на словарь.
    """
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# Ключи Telegram: my.telegram.org\n"
        "\n"
        "TG_API_ID=12345\n"
        "  # мой комментарий\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(envfile, "_REPORTED_BAD_LINES", set())

    with caplog.at_level("WARNING", logger="job_monitor.envfile"):
        assert envfile.read_env() == {"TG_API_ID": "12345"}

    complaints = [record for record in caplog.records if record.name == "job_monitor.envfile"]
    assert complaints == [], (
        "приложение жалуется на собственные комментарии пользователя: "
        f"{[record.getMessage() for record in complaints]}"
    )


def test_a_comment_never_becomes_a_variable(tmp_path, monkeypatch):
    """Отдельно от разбора выше: комментарий, внутри которого есть `=`.
    Именно на нём «пропускать комментарии» и «пропускать строки без `=`»
    расходятся."""
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# TG_API_HASH=закомментированный-старый-ключ\n"
        "TG_API_ID=7\n",
        encoding="utf-8",
    )
    parsed = envfile.read_env()
    assert parsed == {"TG_API_ID": "7"}
    assert not any(key.startswith("#") for key in parsed)


# ── Инъекция через ИМЯ переменной, а не только через значение ─────────
#
# `test_rejects_newline_injection` выше проверяет перевод строки в
# ЗНАЧЕНИИ. Newline в ИМЕНИ — та же инъекция в `.env` и тот же способ
# дописать туда чужую переменную, — не был закреплён ничем: мутация
# `_validate` (`or` → `and` в условии на имя) проходила зелёной.


@pytest.mark.parametrize("bad_key", [
    "TG_API_ID\nSAFE_MODE",
    "TG_API_ID\rSAFE_MODE",
    "TG_API_ID\x00",
    "TG_API_ID=SMUGGLED",
    "",
])
def test_write_env_rejects_a_forbidden_variable_name(tmp_path, monkeypatch, bad_key):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        envfile.write_env({bad_key: "false"})
    assert not (tmp_path / ".env").exists(), (
        "файл создан несмотря на отказ — проверка стоит после записи"
    )


def test_a_rejected_name_does_not_damage_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_MONITOR_DATA_DIR", str(tmp_path))
    envfile.write_env({"TG_API_ID": "7"})
    before = (tmp_path / ".env").read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        envfile.write_env({"TG_API_ID\nSAFE_MODE": "false"})

    assert (tmp_path / ".env").read_text(encoding="utf-8") == before
