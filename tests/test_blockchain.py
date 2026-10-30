"""Блокчейн: утилиты хэширования и интеграция со смарт-контрактом CFAPlatform во встроенной EVM."""

from __future__ import annotations

import sqlite3

import pytest

import common
import database_setup as db
from blockchain import ChainError, _revert_reason, doc_hash, to_bytes32
from conftest import wallet

SMS = "1234"


# ------------------------------------------------------------------ утилиты (без EVM)
def test_doc_hash_is_canonical() -> None:
    a = doc_hash({"b": 1, "a": "Ромашка"})
    assert a == doc_hash({"a": "Ромашка", "b": 1})          # порядок ключей не важен
    assert a != doc_hash({"a": "Ромашка", "b": 2})
    assert a.startswith("0x") and len(a) == 66


@pytest.mark.parametrize("value, expected_len", [(None, 32), ("", 32), ("0x" + "ab" * 32, 32),
                                                 ("0x" + "ab" * 40, 32), ("PEP_SMS_abc", 32), ("0xzz", 32)])
def test_to_bytes32_always_32_bytes(value, expected_len: int) -> None:
    assert len(to_bytes32(value)) == expected_len


def test_to_bytes32_values() -> None:
    assert to_bytes32(None) == b"\x00" * 32
    assert to_bytes32("0x01") == b"\x00" * 31 + b"\x01"       # короткий hex дополняется слева
    assert to_bytes32("PEP_SMS_x") != to_bytes32("PEP_SMS_y")  # не-hex строки хэшируются


def test_revert_reason_extracts_require_text() -> None:
    e = Exception("execution reverted: Недостаточно свободных средств")
    assert _revert_reason(e) == "Блокчейн отклонил операцию: Недостаточно свободных средств"
    assert _revert_reason(Exception("x" * 500)).endswith("x" * 200)
    assert issubclass(ChainError, ValueError)                  # API ловит ValueError и отдаёт 400


def test_chain_disabled_is_noop(conn: sqlite3.Connection, make_user) -> None:
    """Без блокчейна платформа работает только на SQLite (evm_tx_hash = None)."""
    uid = make_user("FL")
    assert db.deposit_wallet(conn, uid, 100, chain=None)["evm_tx_hash"] is None


# ------------------------------------------------------------------ интеграция с EVM
@pytest.fixture
def ch(chain, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(common, "CHAIN", chain)               # для common.reconciliation()
    return chain


def _addr(ch, conn, uid: int) -> str:
    return ch.address_of(conn.execute("SELECT evm_address FROM users WHERE id = ?", (uid,)).fetchone()[0])


@pytest.mark.chain
def test_chain_deploys_contract(ch) -> None:
    st = ch.status()
    assert st["enabled"] and st["mode"] == "embedded" and st["contract"]
    assert st["total_rub"] == 0


@pytest.mark.chain
def test_kyc_whitelist_mirrors_status(conn: sqlite3.Connection, ch, make_user, admin_id: int) -> None:
    uid = make_user("FL", approve=False)
    assert ch.call("verified", _addr(ch, conn, uid)) is False
    db.approve_user(conn, ch, admin_id, uid)
    assert ch.call("verified", _addr(ch, conn, uid)) is True
    db.block_user(conn, ch, admin_id, uid, "Санкции")
    assert ch.call("verified", _addr(ch, conn, uid)) is False


@pytest.mark.chain
def test_wallet_operations_mirror_to_contract(conn: sqlite3.Connection, ch, make_user) -> None:
    uid = make_user("FL", chain=ch, balance=50_000)
    res = db.withdraw_wallet(conn, uid, 20_000, chain=ch)
    assert res["evm_tx_hash"].startswith("0x")
    assert ch.call("rubBalance", _addr(ch, conn, uid)) == wallet(conn, uid)["balance_rub"] == 30_000


@pytest.mark.chain
def test_contract_rejection_rolls_back_sqlite(conn: sqlite3.Connection, ch, make_user) -> None:
    """Если контракт отклонил операцию, SQLite откатывается целиком (атомарность двух реестров)."""
    uid = make_user("FL", chain=ch)
    with conn:   # деньги «появились» в SQLite в обход контракта
        conn.execute("UPDATE wallets SET balance_rub = 1000 WHERE user_id = ?", (uid,))
    ledger_before = conn.execute("SELECT COUNT(*) FROM blockchain_ledger").fetchone()[0]

    with pytest.raises(ChainError, match="Недостаточно свободных средств"):
        db.withdraw_wallet(conn, uid, 500, chain=ch)

    assert wallet(conn, uid)["balance_rub"] == 1000
    assert conn.execute("SELECT COUNT(*) FROM blockchain_ledger").fetchone()[0] == ledger_before


@pytest.mark.chain
def test_unverified_user_cannot_get_rubles_on_chain(conn: sqlite3.Connection, ch, make_user) -> None:
    uid = make_user("FL", approve=False)
    with pytest.raises(ChainError, match="не прошёл проверку"):
        db.deposit_wallet(conn, uid, 1_000, chain=ch)
    assert wallet(conn, uid)["balance_rub"] == 0


@pytest.mark.chain
def test_full_dvp_cycle_reconciles(conn: sqlite3.Connection, ch, make_user, make_issue) -> None:
    issuer = make_user("UL", chain=ch)
    issue_id = make_issue(issuer, chain=ch, min_qty="5")
    a, b = make_user("FL", chain=ch, balance=10_000), make_user("UL", chain=ch, balance=10_000)
    db.buy_cfa_on_showcase(conn, a, issue_id, 3, SMS, chain=ch)
    db.buy_cfa_on_showcase(conn, b, issue_id, 4, "cms", chain=ch)
    assert ch.call("rubLocked", _addr(ch, conn, a)) == 3_000

    res = db.finalize_issue_dvp(conn, issue_id, chain=ch)
    assert res["result"] == "ISSUED" and res["evm_tx_hash"]
    assert ch.call("balanceOf", issue_id, _addr(ch, conn, a)) == 3
    assert ch.call("rubBalance", _addr(ch, conn, issuer)) == 7_000
    assert ch.call("totalSupply", issue_id) == 7

    rows = common.reconciliation(conn)
    assert rows and all(r["match"] for r in rows), [r for r in rows if not r["match"]]


@pytest.mark.chain
def test_reconciliation_detects_tampering(conn: sqlite3.Connection, ch, make_user) -> None:
    uid = make_user("FL", chain=ch, balance=1_000)
    with conn:
        conn.execute("UPDATE wallets SET balance_rub = 999999 WHERE user_id = ?", (uid,))
    bad = [r for r in common.reconciliation(conn) if not r["match"]]
    assert len(bad) == 1 and bad[0]["what"] == "Свободные рубли"


@pytest.mark.chain
def test_qualified_only_enforced_by_contract_too(conn: sqlite3.Connection, ch, make_user, make_issue) -> None:
    issue_id = make_issue(make_user("UL", chain=ch), chain=ch, investors="qualified")
    inv = make_user("FL", chain=ch, balance=10_000)
    with conn:   # обходим проверку SQLite — контракт всё равно не пропустит
        conn.execute("UPDATE cfa_issues SET only_qual = 0 WHERE id = ?", (issue_id,))
        conn.execute("UPDATE cfa_issues SET rov_json = json_set(rov_json, '$.investors', 'all') WHERE id = ?",
                     (issue_id,))
    with pytest.raises(ChainError, match="квалифицированных"):
        db.buy_cfa_on_showcase(conn, inv, issue_id, 1, SMS, chain=ch)
    assert wallet(conn, inv)["balance_rub"] == 10_000


@pytest.mark.chain
def test_state_is_replayed_from_journal(conn: sqlite3.Connection, ch, make_user, patch_db) -> None:
    """Новый экземпляр сети восстанавливает то же состояние из журнала транзакций (детерминизм EVM)."""
    from blockchain import Chain
    uid = make_user("FL", chain=ch, balance=12_345)
    fresh = Chain(db.get_connection)
    assert fresh.enabled and fresh.address == ch.address
    assert fresh.replayed == conn.execute("SELECT COUNT(*) FROM evm_transactions").fetchone()[0]
    assert fresh.call("rubBalance", _addr(fresh, conn, uid)) == 12_345


@pytest.mark.chain
def test_chain_catches_up_with_other_process(conn: sqlite3.Connection, ch, make_user, patch_db) -> None:
    """Транзакции, записанные другим процессом (например, сторожем по cron), подхватываются при чтении."""
    from blockchain import Chain
    other = Chain(db.get_connection)
    uid = make_user("FL", chain=other, balance=777)
    ch._synced_at = 0.0
    assert ch.call("rubBalance", _addr(ch, conn, uid)) == 777
