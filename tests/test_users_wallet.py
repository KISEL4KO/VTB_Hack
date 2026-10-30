"""Участники (регистрация, допуск, блокировка), кошелёк и журнал операций с цепочкой хэшей."""

from __future__ import annotations

import sqlite3

import pytest

import database_setup as db
from conftest import wallet


def _user(conn: sqlite3.Connection, uid: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


# ------------------------------------------------------------------ регистрация и допуск
def test_register_creates_pending_user_wallet_and_ledger(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL", approve=False)
    u = _user(conn, uid)
    assert u["status"] == "PENDING" and u["yearly_spent_rub"] == 0
    w = wallet(conn, uid)
    assert (w["balance_rub"], w["locked_rub"]) == (0, 0)
    ops = [r["op_type"] for r in conn.execute("SELECT op_type FROM blockchain_ledger WHERE user_id = ?", (uid,))]
    assert ops == ["KYC_SUBMIT"]


def test_register_rejects_duplicate_login_and_inn(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL", approve=False)
    u = _user(conn, uid)
    profile = {k: u[k] for k in ("full_name", "inn", "snils_or_ogrn", "phone", "email", "passport_or_rep",
                                 "address", "is_qualified")}
    kwargs = dict(password_hash="x", profile_json={}, evm_address="0x" + "1" * 40, wallet_name="w",
                  bank_account="4" * 20, bank_bik="044525225")

    with pytest.raises(db.FieldError) as e:
        db.register_user(conn, "FL", u["login"], profile={**profile, "inn": "999999999999"}, **kwargs)
    assert e.value.field == "login"

    with pytest.raises(db.FieldError) as e:
        db.register_user(conn, "FL", "brand_new", profile=profile, **kwargs)
    assert e.value.field == "inn"
    assert conn.execute("SELECT COUNT(*) FROM users WHERE login = 'brand_new'").fetchone()[0] == 0


def test_create_admin_duplicate_login(conn: sqlite3.Connection, admin_id: int) -> None:
    with pytest.raises(ValueError, match="занят"):
        db.create_admin(conn, "admin", "hash", "Второй")


def test_approve_user_happy_path(conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    uid = make_user("FL", approve=False)
    db.approve_user(conn, None, admin_id, uid, "ok")
    u = _user(conn, uid)
    assert u["status"] == "APPROVED" and u["reviewed_by"] == admin_id
    audit = conn.execute("SELECT * FROM admin_actions WHERE target_id = ?", (uid,)).fetchone()
    assert audit["action"] == "USER_APPROVE"


def test_approve_twice_and_unknown_and_admin(conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    uid = make_user("FL")
    with pytest.raises(ValueError, match="уже допущен"):
        db.approve_user(conn, None, admin_id, uid)
    with pytest.raises(ValueError, match="не найден"):
        db.approve_user(conn, None, admin_id, 999_999)
    with pytest.raises(ValueError, match="не найден"):
        db.approve_user(conn, None, admin_id, admin_id)


@pytest.mark.parametrize("reason", ["", "   "])
def test_reject_requires_reason(conn: sqlite3.Connection, make_user, admin_id: int, reason: str) -> None:
    uid = make_user("FL", approve=False)
    with pytest.raises(db.FieldError) as e:
        db.reject_user(conn, admin_id, uid, reason)
    assert e.value.field == "reason"
    assert _user(conn, uid)["status"] == "PENDING"


def test_reject_only_pending(conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    pending = make_user("FL", approve=False)
    db.reject_user(conn, admin_id, pending, "  Нечитаемый паспорт  ")
    u = _user(conn, pending)
    assert u["status"] == "REJECTED" and u["reject_reason"] == "Нечитаемый паспорт"

    approved = make_user("FL")
    with pytest.raises(ValueError, match="ожидающую проверки"):
        db.reject_user(conn, admin_id, approved, "поздно")


def test_rejected_user_can_be_approved_later(conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    uid = make_user("FL", approve=False)
    db.reject_user(conn, admin_id, uid, "Нет СНИЛС")
    db.approve_user(conn, None, admin_id, uid)
    u = _user(conn, uid)
    assert u["status"] == "APPROVED" and u["reject_reason"] is None


def test_block_user(conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    uid = make_user("FL")
    with pytest.raises(db.FieldError):
        db.block_user(conn, None, admin_id, uid, " ")
    db.block_user(conn, None, admin_id, uid, "Подозрительные операции")
    assert _user(conn, uid)["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="только допущенного"):
        db.block_user(conn, None, admin_id, uid, "повторно")


def test_cannot_block_pending_or_admin(conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    pending = make_user("FL", approve=False)
    for target in (pending, admin_id):
        with pytest.raises(ValueError):
            db.block_user(conn, None, admin_id, target, "причина")


# ------------------------------------------------------------------ кошелёк
def test_deposit_and_withdraw_happy_path(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL")
    res = db.deposit_wallet(conn, uid, 100_000)
    assert res["tx_hash"].startswith("0x") and res["evm_tx_hash"] is None
    db.withdraw_wallet(conn, uid, 30_000)
    assert wallet(conn, uid)["balance_rub"] == 70_000


def test_withdraw_entire_balance_to_zero(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL", balance=5_000)
    db.withdraw_wallet(conn, uid, 5_000)
    assert wallet(conn, uid)["balance_rub"] == 0


@pytest.mark.parametrize("amount", [0, -1, -100_000])
def test_deposit_non_positive(conn: sqlite3.Connection, make_user, amount: int) -> None:
    uid = make_user("FL")
    with pytest.raises(ValueError, match="больше нуля"):
        db.deposit_wallet(conn, uid, amount)


@pytest.mark.parametrize("amount, ok", [(1_000_000_000, True), (1_000_000_001, False)])
def test_deposit_upper_bound(conn: sqlite3.Connection, make_user, amount: int, ok: bool) -> None:
    uid = make_user("FL")
    if ok:
        db.deposit_wallet(conn, uid, amount)
        assert wallet(conn, uid)["balance_rub"] == amount
    else:
        with pytest.raises(ValueError, match="Слишком большая"):
            db.deposit_wallet(conn, uid, amount)


@pytest.mark.parametrize("amount", [0, -5])
def test_withdraw_non_positive(conn: sqlite3.Connection, make_user, amount: int) -> None:
    uid = make_user("FL", balance=100)
    with pytest.raises(ValueError, match="больше нуля"):
        db.withdraw_wallet(conn, uid, amount)


def test_withdraw_more_than_balance_changes_nothing(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL", balance=1_000)
    ledger_before = conn.execute("SELECT COUNT(*) FROM blockchain_ledger").fetchone()[0]
    with pytest.raises(ValueError, match="Недостаточно"):
        db.withdraw_wallet(conn, uid, 1_001)
    assert wallet(conn, uid)["balance_rub"] == 1_000
    assert conn.execute("SELECT COUNT(*) FROM blockchain_ledger").fetchone()[0] == ledger_before


def test_withdraw_cannot_touch_locked_funds(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL", balance=1_000)
    with conn:
        conn.execute("UPDATE wallets SET balance_rub = 0, locked_rub = 1000 WHERE user_id = ?", (uid,))
    with pytest.raises(ValueError):
        db.withdraw_wallet(conn, uid, 1)


def test_withdraw_unknown_wallet(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="Кошелёк не найден"):
        db.withdraw_wallet(conn, 424242, 10)


@pytest.mark.xfail(strict=True, raises=TypeError,
                   reason="БАГ: deposit_wallet не проверяет, что участник существует (в отличие от withdraw_wallet)")
def test_deposit_unknown_user_raises_value_error(conn: sqlite3.Connection) -> None:
    """Пополнение несуществующего участника не должно ни создавать записи, ни проходить молча."""
    with pytest.raises(ValueError, match="не найден"):
        db.deposit_wallet(conn, 424242, 10)
    assert conn.execute("SELECT COUNT(*) FROM blockchain_ledger WHERE user_id = 424242").fetchone()[0] == 0


def test_schema_forbids_negative_balance(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL")
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("UPDATE wallets SET balance_rub = -1 WHERE user_id = ?", (uid,))


# ------------------------------------------------------------------ журнал операций
def test_ledger_is_an_unbroken_hash_chain(conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL", balance=10_000)
    db.withdraw_wallet(conn, uid, 1_000)
    make_user("UL", balance=5_000)
    rows = conn.execute("SELECT prev_hash, tx_hash FROM blockchain_ledger ORDER BY block_id").fetchall()
    assert rows[0]["prev_hash"] == "0" * 64
    for prev, cur in zip(rows, rows[1:]):
        assert cur["prev_hash"] == prev["tx_hash"]
    hashes = [r["tx_hash"] for r in rows]
    assert len(set(hashes)) == len(hashes)
    assert all(h.startswith("0x") and len(h) == 64 for h in hashes)


def test_password_is_stored_hashed(conn: sqlite3.Connection, make_user) -> None:
    from werkzeug.security import check_password_hash
    from conftest import PASSWORD
    u = _user(conn, make_user("FL"))
    assert PASSWORD not in u["password_hash"]
    assert check_password_hash(u["password_hash"], PASSWORD)


def test_get_user_by_login_is_case_insensitive(conn: sqlite3.Connection, make_user) -> None:
    u = _user(conn, make_user("FL"))
    assert db.get_user_by_login(conn, u["login"].upper())["id"] == u["id"]
    assert db.get_user_by_login(conn, "' OR 1=1 --") is None


def test_foreign_keys_are_enabled(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
