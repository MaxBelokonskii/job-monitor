"""Растяжка: чужого поиска в коде нет.

Приложение унаследовано, и значения по умолчанию были буквально поиском
предыдущего автора — казахстанские QA-каналы, «junior», «без опыта», регион
113 как настройка. Проверка сканирует исходники приложения, а не только
модель: вернуть список можно и мимо `SearchCriteria` — например константой в
воркере или значением в роуте.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("job_monitor", "api", "frontend")
SCANNED_SUFFIXES = (".py", ".js", ".html", ".css")

# Собираются из частей, чтобы файл теста не срабатывал на себе, если каталог
# сканирования когда-нибудь расширят.
#
# «Стажировка» в список НЕ входит, хотя была одним из ключевых слов прежнего
# автора: это ещё и официальная подпись типа занятости `probation` в
# справочнике hh.ru (`job_monitor/criteria.py`). Проверка не умеет отличить
# подпись справочника от чужого поискового слова, а ложное срабатывание на
# законном коде хуже пропуска: его чинят ослаблением проверки. Поэтому в
# списке только то, что не может появиться в коде ни по какой другой
# причине.
FOREIGN_TERMS = (
    "itvacancy" + "kz",
    "it_" + "interns",
    "jobfor" + "tester",
    "work" + "itkz",
    "qajob" + "offer",
    "jobfor" + "qa",
    "manual " + "qa",
    "junior " + "qa",
    "без " + "опыта",
)


def _sources() -> list[Path]:
    found: list[Path] = []
    for directory in SCANNED_DIRS:
        found.extend(
            item
            for item in (REPO_ROOT / directory).rglob("*")
            if item.is_file()
            and item.suffix in SCANNED_SUFFIXES
            and "__pycache__" not in item.parts
        )
    assert found, "не найдено ни одного исходника — структура переехала?"
    return sorted(found)


def test_no_previous_author_search_terms_in_the_sources() -> None:
    offenders: list[str] = []
    for source in _sources():
        text = source.read_text(encoding="utf-8").lower()
        for term in FOREIGN_TERMS:
            if term.lower() in text:
                offenders.append(f"{source.relative_to(REPO_ROOT)}: {term!r}")
    assert not offenders, "чужой поиск вернулся в код: " + "; ".join(offenders)


def test_a_fresh_install_searches_for_nothing() -> None:
    """Обратная сторона той же мысли: не только чужих слов нет, но и своих
    нет тоже. Пустой пресет — это не забывчивость, а решение: «пример из
    коробки» ровно тот механизм, которым чужой поиск возвращается назад."""
    from job_monitor.criteria import SearchCriteria

    empty = SearchCriteria()
    assert empty.channels == []
    assert empty.tg_keywords == []
    assert empty.professions == []


def test_the_scan_is_not_vacuous() -> None:
    """Сторож против вырождения.

    Мало проверить, что файлов «достаточно много»: сузить `SCANNED_DIRS` до
    одного каталога можно было незаметно — суммарного числа файлов хватало
    бы и без `api/`. Поэтому проверяется, что каждый каталог области
    сканирования действительно даёт файлы, и что среди них есть разметка:
    чужой поиск нашёлся именно в ней.
    """
    assert len(FOREIGN_TERMS) >= 8
    sources = _sources()
    assert len(sources) >= 10
    for directory in SCANNED_DIRS:
        assert any(
            str(item).startswith(str(REPO_ROOT / directory)) for item in sources
        ), f"каталог {directory} выпал из области сканирования"
    assert any(item.suffix == ".html" for item in sources), (
        "разметка не сканируется — а чужой поиск нашёлся именно там"
    )
