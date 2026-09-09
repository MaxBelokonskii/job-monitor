"""Токен приложения обязан быть непредсказуемым, а не просто присутствовать.

`APP_TOKEN` — вся защита `/api/*` (`job_monitor/security.py::
app_token_middleware`). Браузерная модель тут особая: сервер слушает
loopback, поэтому любой процесс пользователя и любая открытая вкладка
дотягиваются до него напрямую; предсказуемый токен превращает «локально и
потому безопасно» в «кто угодно локально».

Мутационный аудит показал, что и `APP_TOKEN = "job-monitor"`, и
`secrets.token_urlsafe(1)` проходили зелёными, причём причина структурная:
**все** тесты токен-гейта берут значение из самого проверяемого модуля
(`from job_monitor.security import APP_TOKEN`), поэтому «правильным»
выглядит любое значение — единственное, что от него отличалось в наборе,
это строка `"wrong"`.

Значит проверять надо свойство, а не значение, и часть свойств видна только
СНАРУЖИ процесса: «токен не постоянен между запусками» невозможно
установить, глядя на одно значение. Отсюда подпроцессы.
"""

from __future__ import annotations

import ast
import string
import subprocess
import sys
from pathlib import Path

from job_monitor.security import APP_TOKEN

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_SOURCE = REPO_ROOT / "job_monitor" / "security.py"

# Минимум — 32 символа. Столько даёт `token_urlsafe(24)`; текущая реализация
# берёт 32 байта энтропии и выдаёт 43 символа. Порог, а не равенство: тест
# не должен запрещать сделать токен ДЛИННЕЕ.
MINIMUM_LENGTH = 32

# Сколько независимых интерпретаторов опрашивается. Двух достаточно, чтобы
# отличить константу от случайного значения; третий — запас на случай, если
# реализация станет брать значение из короткого перечня.
SAMPLES = 3

URLSAFE_ALPHABET = set(string.ascii_letters + string.digits + "-_")


def _token_from_a_fresh_interpreter() -> str:
    """Значение `APP_TOKEN` в отдельном, ничего не знающем процессе.

    Импорт `job_monitor.security` не трогает каталог данных (модуль не
    зависит от `job_monitor.paths`), поэтому подпроцесс ничего не создаёт
    ни в `HOME`, ни в репозитории — то же свойство, на котором держится
    `test_paths::test_importing_the_app_creates_nothing_on_disk`.
    """
    result = subprocess.run(
        [sys.executable, "-c", "from job_monitor.security import APP_TOKEN; print(APP_TOKEN)"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"подпроцесс не смог получить токен: {result.stderr}"
    token = result.stdout.strip()
    assert token, f"подпроцесс напечатал пустой токен: {result.stderr}"
    return token


def test_the_token_is_long_enough_to_be_unguessable():
    assert len(APP_TOKEN) >= MINIMUM_LENGTH, (
        f"токен длиной {len(APP_TOKEN)} символов перебирается локальным процессом "
        f"или скриптом на странице; минимум — {MINIMUM_LENGTH}"
    )


def test_the_token_is_not_a_repeated_or_degenerate_string():
    """Длина без разнообразия ничего не значит: `"a" * 43` длинный и при
    этом угадывается с первой попытки."""
    assert len(set(APP_TOKEN)) >= 16, (
        f"в токене всего {len(set(APP_TOKEN))} различных символов — длина набрана "
        "повторением, а не энтропией"
    )
    assert set(APP_TOKEN) <= URLSAFE_ALPHABET, (
        "токен содержит символы вне URL-safe алфавита: он уезжает в HTTP-заголовок "
        f"и в HTML главной страницы — {sorted(set(APP_TOKEN) - URLSAFE_ALPHABET)}"
    )


def test_the_token_is_not_a_literal_in_the_source():
    """Захардкоженный токен ловится и без подпроцесса — прямым сравнением.

    Проверка нарочно смотрит на исходник, а не на значение: значение в этом
    процессе задаёт тот же модуль, который мы проверяем, и потому само по
    себе ничего не подтверждает.
    """
    source = SECURITY_SOURCE.read_text(encoding="utf-8")
    assert APP_TOKEN not in source, (
        "действующий токен лежит в job_monitor/security.py строкой — он одинаков у "
        "всех пользователей и известен любому, кто видел репозиторий"
    )


def test_the_token_is_not_produced_by_a_predictable_generator():
    """`random` — генератор для игр и выборок, а не для секретов: его
    состояние восстанавливается по нескольким выданным значениям. Токен,
    собранный им, прошёл бы и проверку длины, и проверку разнообразия, и
    проверку различия между запусками — поэтому эта проверка отдельная.
    """
    tree = ast.parse(SECURITY_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "random" not in imported, (
        "job_monitor/security.py импортирует `random`: значения этого модуля "
        "предсказуемы по построению, для токена нужен `secrets`/`os.urandom`"
    )
    assert "secrets" in imported, (
        "из job_monitor/security.py исчез источник криптографической случайности — "
        "чем теперь порождается APP_TOKEN?"
    )


def test_the_token_is_different_in_every_process():
    """Свойство, которое невозможно проверить изнутри процесса.

    Постоянный между запусками токен — это общий секрет, который переживает
    перезапуск приложения и попадает в чужие руки навсегда: он остаётся в
    истории команд, в скриншоте вкладки, в кэше браузера. Каждый запуск
    должен выдавать свой.
    """
    tokens = [_token_from_a_fresh_interpreter() for _ in range(SAMPLES)]
    assert len(set(tokens)) == SAMPLES, (
        f"{SAMPLES} независимых процесса выдали {len(set(tokens))} различных токена: "
        "токен постоянен между запусками"
    )
    for token in tokens:
        assert len(token) >= MINIMUM_LENGTH, (
            f"токен из чистого процесса короче {MINIMUM_LENGTH} символов: {len(token)}"
        )
        assert token != APP_TOKEN, (
            "чужой процесс получил тот же токен, что и этот прогон — значение общее"
        )
