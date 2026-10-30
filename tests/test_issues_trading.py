"""Жизненный цикл выпуска: РоВ → модерация → витрина → заявки → DvP / отмена с возвратом."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest

import database_setup as db
from blockchain import doc_hash
from conftest import TODAY, issue_row, valid_rov, wallet

SMS = "1234"


def _user(conn: sqlite3.Connection, uid: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


@pytest.fixture
def issuer(make_user) -> int:
    return make_user("UL")


# ------------------------------------------------------------------ Решение о выпуске и модерация
def test_submit_decision_goes_to_review_with_signed_hash(conn: sqlite3.Connection, issuer: int) -> None:
    res = db.save_issue_decision(conn, issuer, valid_rov())
    issue = issue_row(conn, res["issue_id"])
    assert issue["status"] == "REVIEW"
    assert issue["doc_hash"] == res["doc_hash"] == doc_hash(json.loads(issue["rov_json"]))
    assert issue["ukep_signature"].startswith("UKEP_SIG_")
    assert json.loads(issue["rov_json"])["issuer"]["inn"] == _user(conn, issuer)["inn"]


@pytest.mark.parametrize("role", ["FL"])
def test_only_ul_and_ip_can_issue(conn: sqlite3.Connection, make_user, role: str) -> None:
    with pytest.raises(ValueError, match="259-ФЗ"):
        db.save_issue_decision(conn, make_user(role), valid_rov())


def test_ip_can_issue(conn: sqlite3.Connection, make_user) -> None:
    assert db.save_issue_decision(conn, make_user("IP"), valid_rov())["issue_id"]


def test_edit_decision_after_rejection_returns_to_review(conn: sqlite3.Connection, issuer: int, admin_id: int,
                                                       make_issue) -> None:
    issue_id = make_issue(issuer, publish=False)
    db.reject_issue(conn, admin_id, issue_id, "Уточните бенефициара")
    assert issue_row(conn, issue_id)["status"] == "REJECTED"
    db.save_issue_decision(conn, issuer, valid_rov(title="Ромашка-01 испр"), issue_id=issue_id)
    row = issue_row(conn, issue_id)
    assert row["status"] == "REVIEW" and row["reject_reason"] is None and row["title"] == "Ромашка-01 испр"


def test_changed_document_changes_hash(conn: sqlite3.Connection, issuer: int) -> None:
    h1 = db.save_issue_decision(conn, issuer, valid_rov())["doc_hash"]
    h2 = db.save_issue_decision(conn, issuer, valid_rov(coupon_rate="18.6"))["doc_hash"]
    assert h1 != h2


def test_approve_issue_publishes(conn: sqlite3.Connection, issuer: int, admin_id: int, make_issue) -> None:
    issue_id = make_issue(issuer, publish=False)
    db.approve_issue(conn, None, admin_id, issue_id)
    row = issue_row(conn, issue_id)
    assert row["status"] == "SUBSCRIPTION" and row["published_at"]


def test_approve_issue_negative_cases(conn: sqlite3.Connection, make_user, admin_id: int, make_issue) -> None:
    with pytest.raises(ValueError, match="не найден"):
        db.approve_issue(conn, None, admin_id, 999)

    published = make_issue(make_user("UL"))
    with pytest.raises(ValueError, match="ожидающий проверки"):
        db.approve_issue(conn, None, admin_id, published)

    blocked_issuer = make_user("UL")
    pending_issue = make_issue(blocked_issuer, publish=False)
    db.block_user(conn, None, admin_id, blocked_issuer, "Санкции")
    with pytest.raises(ValueError, match="Эмитент не допущен"):
        db.approve_issue(conn, None, admin_id, pending_issue)


def test_cannot_approve_issue_with_expired_placement(conn: sqlite3.Connection, issuer: int, admin_id: int,
                                                     make_issue) -> None:
    issue_id = make_issue(issuer, publish=False)
    with conn:
        conn.execute("UPDATE cfa_issues SET placement_end = ? WHERE id = ?",
                     ((TODAY - timedelta(days=1)).isoformat(), issue_id))
    with pytest.raises(ValueError, match="Период размещения"):
        db.approve_issue(conn, None, admin_id, issue_id)


def test_reject_issue_requires_reason_and_review_status(conn: sqlite3.Connection, issuer: int, admin_id: int,
                                                       make_issue) -> None:
    issue_id = make_issue(issuer, publish=False)
    with pytest.raises(db.FieldError):
        db.reject_issue(conn, admin_id, issue_id, "")
    published = make_issue(issuer)
    with pytest.raises(ValueError):
        db.reject_issue(conn, admin_id, published, "поздно")


# ------------------------------------------------------------------ покупка на витрине
def test_buy_happy_path_holds_money(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer)
    inv = make_user("FL", balance=100_000)
    res = db.buy_cfa_on_showcase(conn, inv, issue_id, 10, SMS)

    w = wallet(conn, inv)
    assert (w["balance_rub"], w["locked_rub"]) == (90_000, 10_000)
    assert issue_row(conn, issue_id)["collected_qty"] == 10
    assert _user(conn, inv)["yearly_spent_rub"] == 10_000
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (res["order_id"],)).fetchone()
    assert (order["status"], order["sign_type"], order["total_rub"]) == ("HOLD", "PEP_SMS", 10_000)


def test_ul_signs_with_ukep_and_has_no_limit(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, price_per_cfa="100000", min_qty="1", max_qty="100")
    company = make_user("UL", balance=5_000_000)
    res = db.buy_cfa_on_showcase(conn, company, issue_id, 50, "any-cms-signature")
    assert res["signature_hash"].startswith("UKEP_CMS_")
    assert _user(conn, company)["yearly_spent_rub"] == 0     # лимит 600 000 ₽ к ЮЛ не применяется


@pytest.mark.parametrize("code", ["0000", "", "12345"])
def test_wrong_sms_code_rolls_back_everything(conn: sqlite3.Connection, issuer: int, make_user, make_issue,
                                              code: str) -> None:
    issue_id = make_issue(issuer)
    inv = make_user("FL", balance=50_000)
    with pytest.raises(ValueError, match="СМС-код"):
        db.buy_cfa_on_showcase(conn, inv, issue_id, 5, code)
    assert wallet(conn, inv)["balance_rub"] == 50_000
    assert _user(conn, inv)["yearly_spent_rub"] == 0          # лимит не «съеден» отклонённой заявкой
    assert issue_row(conn, issue_id)["collected_qty"] == 0
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_sms_code_with_spaces_is_accepted(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer)
    inv = make_user("FL", balance=5_000)
    assert db.buy_cfa_on_showcase(conn, inv, issue_id, 1, " 1234 ")["order_id"]


@pytest.mark.parametrize("qty", [0, -1])
def test_buy_non_positive_qty(conn: sqlite3.Connection, issuer: int, make_user, make_issue, qty: int) -> None:
    issue_id = make_issue(issuer)
    with pytest.raises(ValueError, match="больше 0"):
        db.buy_cfa_on_showcase(conn, make_user("FL", balance=1000), issue_id, qty, SMS)


def test_order_size_limits(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, min_order_qty="5", max_order_qty="20")
    inv = make_user("FL", balance=500_000)
    with pytest.raises(ValueError, match="Минимум"):
        db.buy_cfa_on_showcase(conn, inv, issue_id, 4, SMS)
    with pytest.raises(ValueError, match="Максимум"):
        db.buy_cfa_on_showcase(conn, inv, issue_id, 21, SMS)
    db.buy_cfa_on_showcase(conn, inv, issue_id, 5, SMS)        # обе границы включительно
    db.buy_cfa_on_showcase(conn, inv, issue_id, 20, SMS)


def test_cannot_exceed_issue_volume(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, min_qty="1", max_qty="10", price_per_cfa="100")
    a, b = make_user("FL", balance=10_000), make_user("FL", balance=10_000)
    db.buy_cfa_on_showcase(conn, a, issue_id, 7, SMS)
    with pytest.raises(ValueError, match="осталось всего 3"):
        db.buy_cfa_on_showcase(conn, b, issue_id, 4, SMS)
    db.buy_cfa_on_showcase(conn, b, issue_id, 3, SMS)          # ровно до максимума
    assert issue_row(conn, issue_id)["collected_qty"] == 10


def test_insufficient_funds(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer)
    inv = make_user("FL", balance=9_999)
    with pytest.raises(ValueError, match="Недостаточно"):
        db.buy_cfa_on_showcase(conn, inv, issue_id, 10, SMS)
    db.deposit_wallet(conn, inv, 1)
    db.buy_cfa_on_showcase(conn, inv, issue_id, 10, SMS)        # ровно на всю сумму
    assert wallet(conn, inv)["balance_rub"] == 0


def test_unqualified_yearly_limit_600k_boundary(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, price_per_cfa="1000", max_qty="10000")
    inv = make_user("FL", balance=1_000_000)
    db.buy_cfa_on_showcase(conn, inv, issue_id, 600, SMS)      # ровно 600 000 ₽ — можно
    with pytest.raises(ValueError, match="600 000"):
        db.buy_cfa_on_showcase(conn, inv, issue_id, 1, SMS)


def test_ip_is_subject_to_limit_but_signs_with_ukep(conn: sqlite3.Connection, issuer: int, make_user,
                                                    make_issue) -> None:
    issue_id = make_issue(issuer, price_per_cfa="1000", max_qty="10000")
    ip = make_user("IP", balance=1_000_000)
    res = db.buy_cfa_on_showcase(conn, ip, issue_id, 600, "cms")
    assert res["signature_hash"].startswith("UKEP_CMS_")
    with pytest.raises(ValueError, match="600 000"):
        db.buy_cfa_on_showcase(conn, ip, issue_id, 1, "cms")


def test_qualified_investor_has_no_limit(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, price_per_cfa="1000", max_qty="10000")
    inv = make_user("FL", balance=1_000_000, qualified=True)
    db.buy_cfa_on_showcase(conn, inv, issue_id, 700, SMS)


def test_qualified_only_issue(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, investors="qualified")
    with pytest.raises(ValueError, match="квалифицированным"):
        db.buy_cfa_on_showcase(conn, make_user("FL", balance=10_000), issue_id, 1, SMS)
    db.buy_cfa_on_showcase(conn, make_user("FL", balance=10_000, qualified=True), issue_id, 1, SMS)
    db.buy_cfa_on_showcase(conn, make_user("UL", balance=10_000), issue_id, 1, "cms")


def test_business_only_issue(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, investors="business")
    with pytest.raises(ValueError, match="предпринимателям и компаниям"):
        db.buy_cfa_on_showcase(conn, make_user("FL", balance=10_000, qualified=True), issue_id, 1, SMS)
    db.buy_cfa_on_showcase(conn, make_user("IP", balance=10_000), issue_id, 1, "cms")


def test_issuer_cannot_buy_own_issue(conn: sqlite3.Connection, make_user, make_issue) -> None:
    issuer = make_user("UL", balance=100_000)
    issue_id = make_issue(issuer)
    with pytest.raises(ValueError, match="собственного выпуска"):
        db.buy_cfa_on_showcase(conn, issuer, issue_id, 1, "cms")


def test_cannot_buy_issue_under_review(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, publish=False)
    with pytest.raises(ValueError, match="закрыт"):
        db.buy_cfa_on_showcase(conn, make_user("FL", balance=10_000), issue_id, 1, SMS)


@pytest.mark.parametrize("field, shift, message", [
    ("placement_start", 1, "начнётся"), ("placement_end", -1, "закончился"),
])
def test_buy_outside_placement_window(conn: sqlite3.Connection, issuer: int, make_user, make_issue,
                                      field: str, shift: int, message: str) -> None:
    issue_id = make_issue(issuer)
    with conn:
        conn.execute(f"UPDATE cfa_issues SET {field} = ? WHERE id = ?",
                     ((TODAY + timedelta(days=shift)).isoformat(), issue_id))
    with pytest.raises(ValueError, match=message):
        db.buy_cfa_on_showcase(conn, make_user("FL", balance=10_000), issue_id, 1, SMS)


def test_buy_unknown_entities(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer)
    with pytest.raises(ValueError, match="не найден"):
        db.buy_cfa_on_showcase(conn, 99_999, issue_id, 1, SMS)
    with pytest.raises(ValueError, match="не найден"):
        db.buy_cfa_on_showcase(conn, make_user("FL", balance=100), 99_999, 1, SMS)


# ------------------------------------------------------------------ DvP
def test_dvp_success_transfers_money_and_assets(conn: sqlite3.Connection, issuer: int, make_user,
                                                make_issue) -> None:
    issue_id = make_issue(issuer, min_qty="10", coupon_rate="10")
    a, b = make_user("FL", balance=50_000), make_user("UL", balance=50_000)
    db.buy_cfa_on_showcase(conn, a, issue_id, 6, SMS)
    db.buy_cfa_on_showcase(conn, b, issue_id, 4, "cms")
    db.buy_cfa_on_showcase(conn, a, issue_id, 2, SMS)          # вторая заявка того же инвестора

    res = db.finalize_issue_dvp(conn, issue_id)
    assert res["result"] == "ISSUED" and res["total_raised_rub"] == 12_000
    assert issue_row(conn, issue_id)["status"] == "ISSUED"
    assert wallet(conn, issuer)["balance_rub"] == 12_000
    assert wallet(conn, a)["locked_rub"] == wallet(conn, b)["locked_rub"] == 0
    pa = conn.execute("SELECT * FROM portfolio_assets WHERE user_id = ? AND issue_id = ?", (a, issue_id)).fetchone()
    assert (pa["qty"], pa["invested_rub"], pa["expected_coupon_rub"]) == (8, 8_000, 800)
    statuses = {r[0] for r in conn.execute("SELECT status FROM orders WHERE issue_id = ?", (issue_id,))}
    assert statuses == {"EXECUTED"}


def test_dvp_conserves_money(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    """Сумма денег в системе до и после DvP одинакова (рубли не создаются и не исчезают)."""
    issue_id = make_issue(issuer, min_qty="1")
    investors = [make_user("FL", balance=20_000) for _ in range(3)]
    for i, inv in enumerate(investors, 1):
        db.buy_cfa_on_showcase(conn, inv, issue_id, i * 3, SMS)
    total = lambda: conn.execute("SELECT SUM(balance_rub + locked_rub) FROM wallets").fetchone()[0]  # noqa: E731
    before = total()
    db.finalize_issue_dvp(conn, issue_id)
    assert total() == before


def test_dvp_exactly_min_qty_is_issued(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, min_qty="10")
    db.buy_cfa_on_showcase(conn, make_user("FL", balance=10_000), issue_id, 10, SMS)
    assert db.finalize_issue_dvp(conn, issue_id)["result"] == "ISSUED"


def test_dvp_below_min_refunds_money_and_limit(conn: sqlite3.Connection, issuer: int, make_user,
                                               make_issue) -> None:
    issue_id = make_issue(issuer, min_qty="10")
    inv = make_user("FL", balance=20_000)
    db.buy_cfa_on_showcase(conn, inv, issue_id, 9, SMS)
    res = db.finalize_issue_dvp(conn, issue_id)
    assert res["result"] == "CANCELLED"
    w = wallet(conn, inv)
    assert (w["balance_rub"], w["locked_rub"]) == (20_000, 0)
    assert _user(conn, inv)["yearly_spent_rub"] == 0
    assert wallet(conn, issuer)["balance_rub"] == 0
    assert issue_row(conn, issue_id)["status"] == "CANCELLED"
    assert conn.execute("SELECT status FROM orders").fetchone()[0] == "REFUNDED"
    assert conn.execute("SELECT COUNT(*) FROM portfolio_assets").fetchone()[0] == 0


def test_dvp_without_orders_cancels(conn: sqlite3.Connection, issuer: int, make_issue) -> None:
    issue_id = make_issue(issuer)
    assert db.finalize_issue_dvp(conn, issue_id)["result"] == "CANCELLED"


def test_dvp_cannot_run_twice_or_on_review(conn: sqlite3.Connection, issuer: int, make_issue) -> None:
    issue_id = make_issue(issuer)
    db.finalize_issue_dvp(conn, issue_id)
    with pytest.raises(ValueError, match="уже обработан"):
        db.finalize_issue_dvp(conn, issue_id)
    with pytest.raises(ValueError):
        db.finalize_issue_dvp(conn, make_issue(issuer, publish=False))
    with pytest.raises(ValueError, match="не найден"):
        db.finalize_issue_dvp(conn, 99_999)


def test_cannot_buy_after_dvp(conn: sqlite3.Connection, issuer: int, make_user, make_issue) -> None:
    issue_id = make_issue(issuer, min_qty="1")
    db.buy_cfa_on_showcase(conn, make_user("FL", balance=1_000), issue_id, 1, SMS)
    db.finalize_issue_dvp(conn, issue_id)
    with pytest.raises(ValueError, match="закрыт"):
        db.buy_cfa_on_showcase(conn, make_user("FL", balance=1_000), issue_id, 1, SMS)
