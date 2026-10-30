"""Выплаты по размещённым выпускам (купоны, погашение, досрочное погашение) и «сторож» выплат."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

import database_setup as db
import keeper
from conftest import TODAY, issue_row, shift_dvp_date, wallet

SMS = "1234"


@pytest.fixture
def placed(conn: sqlite3.Connection, make_user, make_issue):
    """Фабрика размещённого выпуска: эмитент ЮЛ, два инвестора (6 + 4 ЦФА по 1000 ₽), DvP проведён."""

    def _make(days_ago: int = 100, maturity=None, issuer_extra: int = 0, **rov) -> dict:
        issuer = make_user("UL")
        issue_id = make_issue(issuer, min_qty="1", coupon_rate=rov.pop("coupon_rate", "10"), **rov)
        a, b = make_user("FL", balance=6_000), make_user("FL", balance=4_000)
        db.buy_cfa_on_showcase(conn, a, issue_id, 6, SMS)
        db.buy_cfa_on_showcase(conn, b, issue_id, 4, SMS)
        db.finalize_issue_dvp(conn, issue_id)
        shift_dvp_date(conn, issue_id, days_ago, maturity)
        if issuer_extra:
            db.deposit_wallet(conn, issuer, issuer_extra)
        return {"issuer": issuer, "issue": issue_id, "a": a, "b": b}

    return _make


# ------------------------------------------------------------------ формулы
@pytest.mark.parametrize("qty, price, bp, days, expected", [
    (10, 1000, 1850, 91, 461),          # 461.23 → вниз
    (1, 1000, 1000, 365, 100),
    (0, 1000, 1000, 365, 0),
    (5, 1000, 0, 365, 0),               # нулевая ставка
    (1, 1, 1, 1, 0),                    # копейки отбрасываются
    (10_000_000, 1_000_000_000, 10_000, 3653, 100_082_191_780_821_917),  # максимумы без переполнения
])
def test_coupon_amount(qty: int, price: int, bp: int, days: int, expected: int) -> None:
    assert db.coupon_amount(qty, price, bp, days) == expected


@pytest.mark.parametrize("rate, bp", [(18.5, 1850), (18.55, 1855), (0, 0), (100, 10_000), (12.345, 1234)])
def test_rate_bp(rate: float, bp: int) -> None:
    assert db.rate_bp({"coupon_rate": rate}) == bp


def test_coupon_schedule_empty_before_dvp(conn: sqlite3.Connection, make_user, make_issue) -> None:
    issue_id = make_issue(make_user("UL"))
    assert db.coupon_schedule(issue_row(conn, issue_id)) == []


# ------------------------------------------------------------------ купоны
def test_pay_coupon_happy_path(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=100)
    plan = db.payment_plan(conn, issue_row(conn, p["issue"]))
    first = plan["coupons"][0]
    assert first["state"] == "overdue" and first["is_next"]
    issuer_before = wallet(conn, p["issuer"])["balance_rub"]

    res = db.pay_coupon(conn, p["issue"], p["issuer"])
    exp_a = db.coupon_amount(6, 1000, 1000, first["days"])
    exp_b = db.coupon_amount(4, 1000, 1000, first["days"])
    assert res["period"] == 1 and res["total_rub"] == exp_a + exp_b and res["holders"] == 2
    assert wallet(conn, p["a"])["balance_rub"] == exp_a
    assert wallet(conn, p["b"])["balance_rub"] == exp_b
    assert wallet(conn, p["issuer"])["balance_rub"] == issuer_before - res["total_rub"]
    payouts = conn.execute("SELECT SUM(amount_rub) FROM cfa_payouts").fetchone()[0]
    assert payouts == res["total_rub"]

    plan = db.payment_plan(conn, issue_row(conn, p["issue"]))
    assert plan["coupons"][0]["state"] == "paid" and plan["next_no"] == 2


def test_cannot_pay_future_coupon(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=10)
    with pytest.raises(ValueError, match="раньше нельзя"):
        db.pay_coupon(conn, p["issue"], p["issuer"])


def test_cannot_pay_same_coupon_twice(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=100)
    db.pay_coupon(conn, p["issue"], p["issuer"])
    with pytest.raises(ValueError, match="раньше нельзя"):
        db.pay_coupon(conn, p["issue"], p["issuer"])
    assert conn.execute("SELECT COUNT(*) FROM cfa_payments").fetchone()[0] == 1


def test_schema_unique_payment_per_period(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=100)
    db.pay_coupon(conn, p["issue"], p["issuer"])
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("""INSERT INTO cfa_payments (issue_id, kind, period_no, pay_date, amount_total)
                            VALUES (?, 'COUPON', 1, '2026-01-01', 0)""", (p["issue"],))


def test_only_issuer_can_pay(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=100)
    with pytest.raises(ValueError, match="только его эмитент"):
        db.pay_coupon(conn, p["issue"], p["a"])


def test_pay_coupon_without_funds_changes_nothing(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=100)
    db.withdraw_wallet(conn, p["issuer"], wallet(conn, p["issuer"])["balance_rub"])
    with pytest.raises(ValueError, match="Недостаточно средств на кошельке эмитента"):
        db.pay_coupon(conn, p["issue"], p["issuer"])
    assert conn.execute("SELECT COUNT(*) FROM cfa_payments").fetchone()[0] == 0
    assert wallet(conn, p["a"])["balance_rub"] == 0


def test_payments_only_for_issued(conn: sqlite3.Connection, make_user, make_issue) -> None:
    issuer = make_user("UL")
    issue_id = make_issue(issuer)
    with pytest.raises(ValueError, match="размещённому"):
        db.pay_coupon(conn, issue_id, issuer)
    with pytest.raises(ValueError, match="размещённому"):
        db.redeem_issue(conn, issue_id, issuer)


def test_zero_rate_coupon_pays_zero(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=100, coupon_rate="0")
    assert db.pay_coupon(conn, p["issue"], p["issuer"])["total_rub"] == 0


# ------------------------------------------------------------------ погашение
def test_redeem_at_maturity_requires_all_coupons_first(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=200, maturity=TODAY - timedelta(days=1), coupon_period="maturity", issuer_extra=50_000)
    with pytest.raises(ValueError, match="Сначала выплачиваются все купоны"):
        db.redeem_issue(conn, p["issue"], p["issuer"])

    db.pay_coupon(conn, p["issue"], p["issuer"])
    a_after_coupon = wallet(conn, p["a"])["balance_rub"]
    res = db.redeem_issue(conn, p["issue"], p["issuer"])

    assert res["total_rub"] == 10_000                          # номинал без купона
    assert issue_row(conn, p["issue"])["status"] == "REDEEMED"
    assert wallet(conn, p["a"])["balance_rub"] == a_after_coupon + 6_000
    assert conn.execute("SELECT SUM(qty) FROM portfolio_assets WHERE issue_id = ?", (p["issue"],)).fetchone()[0] == 0
    with pytest.raises(ValueError, match="не погашен"):
        db.redeem_issue(conn, p["issue"], p["issuer"])


def test_redeem_before_maturity_not_allowed_by_rov(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=10, early_redemption="no", issuer_extra=50_000)
    with pytest.raises(ValueError, match="Досрочное погашение"):
        db.redeem_issue(conn, p["issue"], p["issuer"])


def test_early_redemption_pays_accrued_coupon_and_cancels_rest(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=30, early_redemption="yes", issuer_extra=50_000)
    res = db.redeem_issue(conn, p["issue"], p["issuer"])
    accrued = 6 * 1000 + db.coupon_amount(6, 1000, 1000, 30)
    assert wallet(conn, p["a"])["balance_rub"] == accrued
    payment = conn.execute("SELECT * FROM cfa_payments WHERE kind = 'REDEMPTION'").fetchone()
    assert payment["early"] == 1 and payment["days"] == 30 and res["total_rub"] == payment["amount_total"]
    plan = db.payment_plan(conn, issue_row(conn, p["issue"]))
    assert {c["state"] for c in plan["coupons"]} == {"cancelled"}


def test_early_redemption_blocked_while_coupon_is_due(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=100, early_redemption="yes", issuer_extra=50_000)
    with pytest.raises(ValueError, match="наступивший купон"):
        db.redeem_issue(conn, p["issue"], p["issuer"])


def test_redeem_without_funds_is_atomic(conn: sqlite3.Connection, placed) -> None:
    p = placed(days_ago=30, early_redemption="yes")
    db.withdraw_wallet(conn, p["issuer"], wallet(conn, p["issuer"])["balance_rub"])
    with pytest.raises(ValueError, match="погашения"):
        db.redeem_issue(conn, p["issue"], p["issuer"])
    assert issue_row(conn, p["issue"])["status"] == "ISSUED"
    assert conn.execute("SELECT SUM(qty) FROM portfolio_assets").fetchone()[0] == 10


# ------------------------------------------------------------------ сторож выплат
def test_keeper_catches_up_all_overdue_coupons(conn: sqlite3.Connection, patch_db, placed) -> None:
    p = placed(days_ago=200, issuer_extra=10_000)              # квартальные купоны: два уже просрочены
    events = keeper.run_once(None, "test")
    assert [e["result"] for e in events] == ["OK", "OK"]
    assert db.payment_plan(conn, issue_row(conn, p["issue"]))["next_no"] == 3
    assert keeper.run_once(None, "test") == []                  # повторный проход ничего не платит


def test_keeper_redeems_at_maturity(conn: sqlite3.Connection, patch_db, placed) -> None:
    p = placed(days_ago=200, maturity=TODAY, coupon_period="maturity", issuer_extra=50_000)
    events = keeper.run_once(None, "test")
    assert [e["action"] for e in events] == ["COUPON", "REDEEM"]
    assert issue_row(conn, p["issue"])["status"] == "REDEEMED"


def test_keeper_no_funds_logged_once_per_day_then_pays(conn: sqlite3.Connection, patch_db, placed) -> None:
    p = placed(days_ago=100)
    db.withdraw_wallet(conn, p["issuer"], wallet(conn, p["issuer"])["balance_rub"])

    events = keeper.run_once(None, "test")
    assert [e["result"] for e in events] == ["NO_FUNDS"]
    assert keeper.run_once(None, "test") == []                  # не спамим журнал в тот же день
    fails = conn.execute("SELECT COUNT(*) FROM blockchain_ledger WHERE op_type = 'PAY_FAIL'").fetchone()[0]
    assert fails == 1

    db.deposit_wallet(conn, p["issuer"], 10_000)
    assert [e["result"] for e in keeper.run_once(None, "test")] == ["OK"]


def test_keeper_idle_when_nothing_due(conn: sqlite3.Connection, patch_db, placed) -> None:
    placed(days_ago=5)
    assert keeper.run_once(None, "test") == []
    actions = keeper.due_actions(conn)
    assert len(actions) == 1 and actions[0]["action"] == "COUPON" and actions[0]["due"] is False


def test_keeper_skips_when_another_run_in_progress(patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    assert keeper._run_lock.acquire(blocking=False)
    try:
        assert keeper.run_once(None, "test") == []
    finally:
        keeper._run_lock.release()


def test_keeper_on_request_respects_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(keeper, "run_once", lambda chain, source: calls.append(source) or [])
    monkeypatch.setattr(keeper, "_last_request_run", 0.0)
    keeper.maybe_run_on_request(None, 0)                         # 0 — выключено
    assert calls == []
    keeper.maybe_run_on_request(None, 1e-9)
    keeper.maybe_run_on_request(None, 3600)                      # слишком рано для второго прохода
    assert calls == ["request"]


def test_keeper_on_request_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a):
        raise RuntimeError("db down")
    monkeypatch.setattr(keeper, "run_once", boom)
    monkeypatch.setattr(keeper, "_last_request_run", 0.0)
    keeper.maybe_run_on_request(None, 1e-9)                      # исключение не должно уронить запрос


def test_keeper_thread_disabled_with_zero_interval() -> None:
    assert keeper.start_thread(None, 0) is None
