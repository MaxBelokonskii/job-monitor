"""Один словарь статусов на оба списка найденного.

Списки раздельные (решение D14), но статусы общие: иначе «откликнулся сам» в
Telegram и в hh.ru стали бы разными строками и каждый счётчик пришлось бы
писать дважды.
"""

from __future__ import annotations

from job_monitor import statuses


def test_decided_is_everything_except_new() -> None:
    """`DECIDED` — это и есть новая дедупликация (решение D19): воркер
    пропускает решённое и обрабатывает новое."""
    assert statuses.DECIDED == statuses.ALL - {statuses.NEW}
    assert statuses.NEW not in statuses.DECIDED


def test_skipped_counts_as_decided() -> None:
    """Намеренно, а не по недосмотру: так ведёт себя нынешний код —
    пропущенная вакансия больше не берётся в работу. Менять это заодно с
    переездом на статусы значило бы смешать два изменения в одном; вернуть
    вакансию в очередь можно вручную."""
    assert statuses.SKIPPED in statuses.DECIDED


def test_manual_is_the_only_thing_a_human_may_set() -> None:
    """Разрешить ставить «отклик отправлен» руками — значит сделать счётчик
    «отправлено сегодня» неправдой: это слово робота."""
    assert statuses.MANUAL == {statuses.MANUAL_APPLIED, statuses.DISMISSED}
    assert statuses.AUTO_APPLIED not in statuses.MANUAL


def test_nothing_already_applied_can_be_reopened() -> None:
    """Возврат в «новую» означает «обработать заново». Для уже отправленного
    отклика это второй отклик тому же работодателю: для Telegram от него
    защищает дедупликация контактов, для hh.ru — ничто."""
    assert statuses.REOPENABLE == {statuses.DISMISSED, statuses.SKIPPED}
    assert statuses.REOPENABLE & statuses.APPLIED == frozenset()
    assert statuses.APPLIED == {statuses.AUTO_APPLIED, statuses.MANUAL_APPLIED}


def test_every_status_is_distinct() -> None:
    """Страховка от вакуумности всех проверок выше: если бы две константы
    совпали строками, множества сложились бы неотличимо."""
    names = [
        statuses.NEW, statuses.AUTO_APPLIED, statuses.MANUAL_APPLIED,
        statuses.DISMISSED, statuses.SKIPPED, statuses.SCENARIO_ERROR,
    ]
    assert len(set(names)) == len(names)
    assert statuses.ALL == set(names)


def test_the_old_hh_names_still_point_at_the_same_strings() -> None:
    """Прежние имена остаются алиасами, чтобы не править места
    использования разом. Разойдись они — счётчики молча обнулятся."""
    from job_monitor.db.repositories import HH_STATUS_APPLIED
    from job_monitor.workers.hh import HH_STATUS_SCENARIO_ERROR

    assert HH_STATUS_APPLIED == statuses.AUTO_APPLIED
    assert HH_STATUS_SCENARIO_ERROR == statuses.SCENARIO_ERROR


def test_sources_name_who_set_the_status() -> None:
    assert statuses.SOURCE_ROBOT != statuses.SOURCE_HUMAN
