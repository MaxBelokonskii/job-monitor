"""Подстановки в шаблоне сообщения.

Только надёжные значения (решение D12): канал, совпавшее ключевое слово и
профессия из пресета. Заголовка вакансии среди них нет: пост в канале —
свободный текст без структуры, и любая эвристика по извлечению заголовка
иногда даёт мусор, а мусор уходит живому человеку в личку.
"""

from __future__ import annotations

from job_monitor.workers.telegram import render_template


def test_all_three_placeholders_are_substituted() -> None:
    rendered = render_template(
        "Здравствуйте! Увидел в {канал} вакансию {ключевое_слово}. Я {профессия}.",
        channel="qajobs",
        keyword="qa",
        profession="инженер по тестированию",
    )
    assert rendered == (
        "Здравствуйте! Увидел в qajobs вакансию qa. Я инженер по тестированию."
    )


def test_a_template_without_placeholders_is_unchanged() -> None:
    assert render_template("просто текст", "c", "k", "p") == "просто текст"


def test_braces_in_the_users_text_do_not_break_rendering() -> None:
    """Именно поэтому здесь `replace`, а не `str.format`.

    На пользовательском тексте `format` падает с `KeyError` на неизвестном
    ключе, а на конструкции вида `{x.__class__}` вообще открывает доступ к
    атрибутам переданных объектов: форматная строка от пользователя — это не
    только хрупкость, но и уязвимость.
    """
    template = "Ставка {30000} руб., режим {гибкий}, канал {канал}"
    assert render_template(template, "c", "k", "p") == (
        "Ставка {30000} руб., режим {гибкий}, канал c"
    )


def test_an_attribute_lookup_in_the_template_stays_literal() -> None:
    """Прямая проверка того, что `format` здесь не используется: с ним эта
    строка вернула бы внутренности объекта, а не сама себя."""
    template = "{канал.__class__.__mro__}"
    assert render_template(template, "qajobs", "k", "p") == template


def test_an_unknown_placeholder_is_left_as_written() -> None:
    assert render_template("а вот {заголовок}", "c", "k", "p") == "а вот {заголовок}"


def test_empty_values_do_not_leave_the_placeholder_text() -> None:
    assert render_template("канал {канал}", "", "", "") == "канал "


def test_a_placeholder_repeated_twice_is_substituted_twice() -> None:
    assert render_template("{канал} и ещё {канал}", "qajobs", "k", "p") == (
        "qajobs и ещё qajobs"
    )
