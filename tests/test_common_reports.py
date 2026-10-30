"""Общие помощники (круг лиц, фильтры шаблонов, ориентиры, аналитика) и генерация PDF-документов."""

from __future__ import annotations

import json
import sqlite3

import pytest

import common
import database_setup as db
import reports
from conftest import issue_row


# ------------------------------------------------------------------ круг лиц выпуска
def _issue(investors: str | None = "all", only_qual: int = 0, raw: str | None = None) -> dict:
    return {"rov_json": raw if raw is not None else json.dumps({"investors": investors}), "only_qual": only_qual}


@pytest.mark.parametrize("issue, expected", [
    (_issue("business"), "business"),
    (_issue(None, only_qual=1), "qualified"),       # старый документ без раздела 12
    (_issue(None), "all"),
    (_issue(raw="{broken"), "all"),
    (_issue(raw=""), "all"),
])
def test_issue_audience(issue: dict, expected: str) -> None:
    assert common.issue_audience(issue) == expected


@pytest.mark.parametrize("role, qualified, audience, blocked", [
    ("FL", 0, "all", False), ("FL", 0, "business", True), ("FL", 1, "business", True),
    ("IP", 0, "business", False), ("UL", 1, "business", False),
    ("FL", 0, "qualified", True), ("FL", 1, "qualified", False), ("IP", 0, "qualified", True),
    ("UL", 1, "qualified", False), ("ADMIN", 0, "business", False),
])
def test_buy_block_reason(role: str, qualified: int, audience: str, blocked: bool) -> None:
    user = {"role": role, "is_qualified": qualified}
    assert bool(common.buy_block_reason(user, _issue(audience))) is blocked


def test_buy_block_reason_anonymous() -> None:
    assert common.buy_block_reason(None, _issue("business")) == ""


# ------------------------------------------------------------------ фильтры шаблонов
@pytest.fixture(scope="module")
def filters():
    from flask import Flask
    app = Flask(__name__)
    common.register_filters(app)
    return app.jinja_env.filters


@pytest.mark.parametrize("name, value, expected", [
    ("rub", 1234567, "1 234 567 ₽"), ("rub", 0, "0 ₽"), ("rub", None, "—"), ("rub", "abc", "—"),
    ("rub_short", 999_999, "999 999 ₽"), ("rub_short", 1_000_000, "1 млн ₽"), ("rub_short", 1_550_000, "1.6 млн ₽"),
    ("rub_short", 2_000_000_000, "2 млрд ₽"), ("rub_short", None, "0 ₽"),
    ("num", 10000, "10 000"), ("num", None, "0"),
    ("ddmm", "2026-10-30", "30.10.2026"), ("ddmm", "2026-10-30 14:05:59", "30.10.2026 14:05"),
    ("ddmm", None, "—"), ("ddmm", "вчера", "вчера"),
    ("role_short", "UL", "ЮЛ"), ("role_full", "IP", "Индивидуальный предприниматель"), ("role_short", "X", "X"),
])
def test_template_filters(filters, name: str, value, expected: str) -> None:
    assert filters[name](value) == expected


def test_short_hash(filters) -> None:
    h = "0x" + "a" * 64
    assert filters["short_hash"](h) == "0x" + "a" * 10 + "…" + "a" * 6
    assert filters["short_hash"]("0xabc") == "0xabc"
    assert filters["short_hash"](None) == ""


def test_status_dictionaries_cover_schema() -> None:
    """Каждый статус из CHECK-ограничений схемы имеет подпись в интерфейсе."""
    assert set(common.ISSUE_STATUS) == {"REVIEW", "REJECTED", "SUBSCRIPTION", "ISSUED", "CANCELLED", "REDEEMED"}
    assert set(common.ORDER_STATUS) == {"HOLD", "EXECUTED", "REFUNDED"}
    assert set(common.USER_STATUS) == {"PENDING", "APPROVED", "REJECTED", "BLOCKED"}


def test_every_ledger_operation_has_a_label() -> None:
    """Каждый тип операции, который пишется в журнал, подписан в «Истории операций»."""
    import re
    from pathlib import Path
    import keeper
    src = "".join(Path(m.__file__).read_text(encoding="utf-8") for m in (db, keeper))
    used = set(re.findall(r'append_ledger_block\(.{0,80}?"([A-Z]{3,}(?:_[A-Z]+)*)"', src, re.S))
    used |= set(re.findall(r'op_type="([A-Z_]+)"', src))
    assert len(used) >= 10, used                     # регулярка действительно нашла операции
    assert used <= set(common.OP_TYPES), used - set(common.OP_TYPES)


# ------------------------------------------------------------------ ориентиры и аналитика
def test_benchmarks_default_and_saved(conn: sqlite3.Connection, admin_id: int) -> None:
    assert common.get_benchmarks(conn) == common.DEFAULT_BENCHMARKS
    common.save_benchmarks(conn, admin_id, {**common.DEFAULT_BENCHMARKS, "key_rate": 15.5})
    common.save_benchmarks(conn, admin_id, {**common.DEFAULT_BENCHMARKS, "key_rate": 16.0})   # upsert
    assert common.get_benchmarks(conn)["key_rate"] == 16.0
    assert conn.execute("SELECT COUNT(*) FROM admin_actions WHERE action = 'BENCHMARKS_UPDATE'").fetchone()[0] == 2


def test_issue_payload_and_issuer_track(conn: sqlite3.Connection, make_user, make_issue) -> None:
    issuer = make_user("UL")
    ok_issue, cancelled = make_issue(issuer, min_qty="1"), make_issue(issuer)
    inv = make_user("FL", balance=5_000)
    db.buy_cfa_on_showcase(conn, inv, ok_issue, 2, "1234")
    db.buy_cfa_on_showcase(conn, inv, ok_issue, 1, "1234")
    db.finalize_issue_dvp(conn, ok_issue)
    db.finalize_issue_dvp(conn, cancelled)

    assert common.issuer_track(conn, issuer) == {"issued": 1, "cancelled": 1}
    assert common.issuer_track(conn, issuer, exclude_issue=ok_issue) == {"issued": 0, "cancelled": 1}

    p = common.issue_payload(conn, issue_row(conn, ok_issue))
    assert (p["investors"], p["orders"], p["avgOrder"], p["collected"]) == (1, 2, 1500, 3)
    assert p["status"] == "ISSUED" and p["showDemand"] is True


def test_reconciliation_empty_without_chain(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "CHAIN", None)
    assert common.reconciliation(conn) == []


# ------------------------------------------------------------------ PDF
@pytest.mark.parametrize("value, expected", [(1234567, "1 234 567 ₽"), (None, "—")])
def test_reports_rub(value, expected: str) -> None:
    assert reports.rub(value).replace(" ", " ") in (expected, expected.replace(" ₽", "") + " ₽")


@pytest.mark.parametrize("value, expected", [("2026-10-30", "30.10.2026"), (None, "—"), ("", "—")])
def test_reports_date(value, expected: str) -> None:
    assert reports.ddmmyyyy(value) == expected


def test_decision_pdf_contains_valid_document(conn: sqlite3.Connection, make_user, make_issue) -> None:
    issue = issue_row(conn, make_issue(make_user("UL"), collateral_type="none"))
    pdf = reports.decision_pdf(issue, json.loads(issue["rov_json"]), "0x" + "1" * 40)
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 2_000


def test_participants_and_reconciliation_pdfs(conn: sqlite3.Connection, make_user) -> None:
    make_user("FL"), make_user("UL", approve=False)
    users = conn.execute("""SELECT u.*, COALESCE(w.balance_rub, 0) AS balance_rub, COALESCE(w.locked_rub, 0) AS locked_rub
                            FROM users u LEFT JOIN wallets w ON w.user_id = u.id ORDER BY u.id""").fetchall()
    assert reports.participants_pdf(users).startswith(b"%PDF")
    assert reports.participants_pdf([]).startswith(b"%PDF")                     # пустой список — тоже документ
    assert reports.reconciliation_pdf([], {"enabled": False, "reason": "off"}).startswith(b"%PDF")
