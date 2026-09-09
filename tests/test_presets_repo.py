from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from job_monitor.db.connection import connect
from job_monitor.db.repositories import PresetsRepo

NOW = datetime(2026, 9, 9, 12, 0, 0)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    # Соединение приложения, а не сырое `sqlite3.connect`: `transaction()`
    # выдаёт явный BEGIN IMMEDIATE и требует `isolation_level=None`, иначе
    # получается «cannot start a transaction within a transaction».
    return connect(str(tmp_path / "t.db"))


def test_created_preset_reads_back_with_parsed_criteria(conn) -> None:
    preset_id = PresetsRepo(conn).create("Мой поиск", {"channels": ["a"]}, NOW)
    stored = PresetsRepo(conn).get(preset_id)
    assert stored is not None
    assert stored["criteria"] == {"channels": ["a"]}, (
        "criteria должны возвращаться разобранным словарём, а не строкой JSON"
    )
    assert stored["name"] == "Мой поиск"


def test_positions_are_assigned_after_the_last_one(conn) -> None:
    repo = PresetsRepo(conn)
    first = repo.create("A", {}, NOW)
    second = repo.create("B", {}, NOW)
    assert repo.get(first)["position"] < repo.get(second)["position"]
    assert repo.next_position() > repo.get(second)["position"]


def test_update_criteria_merges_and_keeps_the_rest(conn) -> None:
    repo = PresetsRepo(conn)
    preset_id = repo.create("A", {"channels": ["a"], "template": "привет"}, NOW)
    result = repo.update_criteria(
        preset_id, lambda current: {**current, "channels": ["a", "b"]}
    )
    assert result == {"channels": ["a", "b"], "template": "привет"}
    assert repo.get(preset_id)["criteria"] == result


def test_criteria_of_one_preset_do_not_leak_into_another(conn) -> None:
    repo = PresetsRepo(conn)
    a = repo.create("A", {"channels": ["a"]}, NOW)
    b = repo.create("B", {"channels": ["b"]}, NOW)
    repo.update_criteria(a, lambda current: {**current, "channels": ["a", "z"]})
    assert repo.get(b)["criteria"] == {"channels": ["b"]}


def test_update_criteria_of_a_missing_preset_is_an_error(conn) -> None:
    with pytest.raises(KeyError):
        PresetsRepo(conn).update_criteria(999, lambda current: current)


def test_list_is_ordered_by_position(conn) -> None:
    repo = PresetsRepo(conn)
    repo.create("A", {}, NOW)
    second = repo.create("B", {}, NOW)
    repo.set_position(second, -1, NOW)
    assert [row["name"] for row in repo.list()] == ["B", "A"]


def test_get_by_name_finds_the_preset(conn) -> None:
    repo = PresetsRepo(conn)
    repo.create("Поддержка", {}, NOW)
    assert repo.get_by_name("Поддержка")["name"] == "Поддержка"
    assert repo.get_by_name("Нет такого") is None


def test_set_name_renames_in_place(conn) -> None:
    repo = PresetsRepo(conn)
    preset_id = repo.create("Старое", {"channels": ["a"]}, NOW)
    repo.set_name(preset_id, "Новое", NOW)
    assert repo.get(preset_id)["name"] == "Новое"
    assert repo.get(preset_id)["criteria"] == {"channels": ["a"]}


def test_delete_removes_only_the_named_preset(conn) -> None:
    repo = PresetsRepo(conn)
    a = repo.create("A", {}, NOW)
    repo.create("B", {}, NOW)
    repo.delete(a)
    assert [row["name"] for row in repo.list()] == ["B"]
    assert repo.count() == 1
