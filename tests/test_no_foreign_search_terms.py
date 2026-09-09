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
SCANNED_DIRS = ("job_monitor", "api")

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
            for item in (REPO_ROOT / directory).rglob("*.py")
            if "__pycache__" not in item.parts
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
    """Сторож против вырождения: если список терминов опустеет или сканер
    перестанет читать файлы, проверка выше станет зелёной навсегда."""
    assert len(FOREIGN_TERMS) >= 8
    assert len(_sources()) >= 10
