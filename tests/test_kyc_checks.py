"""Автоматическая сверка анкеты (KYC): контрольные суммы реквизитов, возраст, дубли."""

from __future__ import annotations

import sqlite3

import pytest

import kyc_checks as kyc
from conftest import make_account, make_inn10, make_inn12, make_ogrn, make_ogrnip, make_snils


def _by_title(checks: list[dict]) -> dict[str, dict]:
    return {c["title"]: c for c in checks}


# ------------------------------------------------------------------ контрольные суммы
@pytest.mark.parametrize("inn, ok", [
    ("7707083893", True),            # ПАО Сбербанк
    ("7707083894", False),
    ("500100732259", True),
    ("500100732258", False),
    (make_inn12(42), True), (make_inn10(42), True),
    ("12345", False), ("", False), ("7707083893" + "0", False),
])
def test_inn_valid(inn: str, ok: bool) -> None:
    assert kyc.inn_valid(inn) is ok


@pytest.mark.parametrize("snils, ok", [
    ("11223344595", True), ("11223344596", False),
    ("00100199800", True),           # номера до 001-001-998 контрольным числом не проверяются
    (make_snils(7), True), ("1122334459", False), ("", False),
])
def test_snils_valid(snils: str, ok: bool) -> None:
    assert kyc.snils_valid(snils) is ok


@pytest.mark.parametrize("ogrn, ok", [
    ("1027700132195", True), ("1027700132196", False),
    (make_ogrn(5), True), (make_ogrnip(5), True), ("123", False), ("", False),
])
def test_ogrn_valid(ogrn: str, ok: bool) -> None:
    assert kyc.ogrn_valid(ogrn) is ok


def test_account_key_valid() -> None:
    acc = make_account("40702", 1)
    assert kyc.account_key_valid(acc, "044525225")
    broken = acc[:-1] + str((int(acc[-1]) + 1) % 10)
    assert not kyc.account_key_valid(broken, "044525225")
    assert not kyc.account_key_valid(acc[:-1], "044525225")
    assert not kyc.account_key_valid(acc, "04452522")


@pytest.mark.parametrize("statuses, expected", [
    (["ok", "ok"], "ok"), (["ok", "warn"], "warn"), (["warn", "fail", "ok"], "fail"), ([], "ok"),
])
def test_summary(statuses: list[str], expected: str) -> None:
    assert kyc.summary([{"status": s} for s in statuses]) == expected


# ------------------------------------------------------------------ сборка проверок
@pytest.mark.parametrize("role", ["FL", "IP", "UL"])
def test_clean_profile_has_no_failures(conn: sqlite3.Connection, make_user, role: str) -> None:
    uid = make_user(role, approve=False)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    checks = kyc.run_checks(conn, user)
    assert checks
    assert kyc.summary(checks) == "ok", [c for c in checks if c["status"] != "ok"]


def test_duplicate_passport_is_a_failure(conn: sqlite3.Connection, make_user) -> None:
    first = make_user("FL", approve=False, fl_passport_num="4512345678")
    second = make_user("FL", approve=False, fl_passport_num="4512345678")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (second,)).fetchone()
    check = _by_title(kyc.run_checks(conn, user))["Паспорт не используется другими участниками"]
    assert check["status"] == "fail"
    login = conn.execute("SELECT login FROM users WHERE id = ?", (first,)).fetchone()["login"]
    assert login in check["detail"]


def test_duplicate_phone_and_email_are_warnings(conn: sqlite3.Connection, make_user) -> None:
    make_user("FL", approve=False, fl_phone="+79990000000", fl_email="same@example.ru")
    second = make_user("FL", approve=False, fl_phone="8 999 000-00-00", fl_email="same@example.ru")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (second,)).fetchone()
    checks = _by_title(kyc.run_checks(conn, user))
    assert checks["Телефон уникален"]["status"] == "warn"
    assert checks["E-mail уникален"]["status"] == "warn"


def test_bad_inn_checksum_is_warning_not_blocking(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL", approve=False, fl_inn="770000000000")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    checks = kyc.run_checks(conn, user)
    assert _by_title(checks)["Контрольная сумма ИНН"]["status"] == "warn"
    assert kyc.summary(checks) == "warn"


def test_viewer_representative_is_flagged(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("UL", approve=False, ul_rep_role="viewer")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    assert _by_title(kyc.run_checks(conn, user))["Полномочия представителя"]["status"] == "warn"


def test_minor_detected_from_tampered_profile(conn: sqlite3.Connection, make_user) -> None:
    """Анкета могла попасть в базу в обход формы — проверка возраста должна это поймать."""
    import json
    from datetime import date
    uid = make_user("FL", approve=False)
    row = conn.execute("SELECT profile_json FROM users WHERE id = ?", (uid,)).fetchone()
    anketa = json.loads(row["profile_json"]) | {"fl_birth_date": date.today().replace(year=date.today().year - 10).isoformat()}
    with conn:
        conn.execute("UPDATE users SET profile_json = ? WHERE id = ?", (json.dumps(anketa), uid))
    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    checks = kyc.run_checks(conn, user)
    assert _by_title(checks)["Возраст"]["status"] == "fail"
    assert kyc.summary(checks) == "fail"
