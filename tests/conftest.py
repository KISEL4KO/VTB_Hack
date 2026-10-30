"""Общие фикстуры: изолированная SQLite-база, фабрики участников и выпусков, Flask-клиент, встроенная EVM."""

from __future__ import annotations

import functools
import itertools
import json
import os
import secrets
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KEEPER_REQUEST_INTERVAL", "0")   # сторож не должен запускаться на каждом запросе теста
os.environ.pop("EVM_RPC_URL", None)                       # только встроенная EVM

import database_setup as db  # noqa: E402
import validators  # noqa: E402

TODAY = date.today()
CITY = "г. Москва"
BIK = "044525225"
PASSWORD = "Secret123"

_ORIG_GET_CONNECTION = db.get_connection
_seq = itertools.count(1)


# ------------------------------------------------------------------------------
# Генераторы реквизитов с корректными контрольными суммами (уникальны в пределах прогона)
# ------------------------------------------------------------------------------
def _ctrl(digits_: list[int], coeffs: list[int]) -> int:
    return sum(c * d for c, d in zip(coeffs, digits_)) % 11 % 10


def make_inn12(n: int) -> str:
    """ИНН физлица / ИП (12 цифр) с верными контрольными разрядами, регион 77."""
    d = [int(c) for c in f"77{n:08d}"]
    d.append(_ctrl(d, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]))
    d.append(_ctrl(d, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]))
    return "".join(map(str, d))


def make_inn10(n: int) -> str:
    """ИНН юрлица (10 цифр), регион 77."""
    d = [int(c) for c in f"77{n:07d}"]
    d.append(_ctrl(d, [2, 4, 10, 3, 5, 9, 4, 6, 8]))
    return "".join(map(str, d))


def make_snils(n: int) -> str:
    number = f"{2_000_000 + n:09d}"
    s = sum(int(x) * (9 - i) for i, x in enumerate(number))
    cs = s if s < 100 else 0 if s in (100, 101) else (s % 101) % 100
    return f"{number}{cs:02d}"


def make_ogrn(n: int) -> str:
    base = f"10277{n:07d}"
    return base + str(int(base) % 11 % 10)


def make_ogrnip(n: int) -> str:
    base = f"3047700{n:07d}"
    return base + str(int(base) % 13 % 10)


def make_account(prefix: str, n: int, bik: str = BIK) -> str:
    """Расчётный счёт, у которого сходится ключ по БИК (подбирается 9-я цифра)."""
    from kyc_checks import account_key_valid
    body = f"{prefix}810{n:012d}"
    for k in range(10):
        acc = body[:8] + str(k) + body[9:]
        if account_key_valid(acc, bik):
            return acc
    raise AssertionError("ключ счёта не подобран")


# ------------------------------------------------------------------------------
# Сырые формы регистрации (как их присылает браузер)
# ------------------------------------------------------------------------------
def fl_form(n: int | None = None, **over: str) -> dict[str, str]:
    n = n or next(_seq)
    form = {
        "login": f"investor{n}", "password": PASSWORD, "password2": PASSWORD,
        "fl_fio": "Иванов Иван Иванович", "fl_birth_date": "1990-05-17", "fl_birth_place": "г. Москва",
        "fl_citizenship": "РФ", "fl_phone": f"+7 900 {n:07d}", "fl_email": f"user{n}@example.ru",
        "fl_passport_num": f"45{n:08d}", "fl_passport_issued": "770-001, ГУ МВД России по г. Москве",
        "fl_reg_city": CITY, "fl_reg_street": "ул. Ленина, д. 1", "fl_inn": make_inn12(n),
        "fl_snils": make_snils(n), "fl_investor_status": "unqualified",
    }
    form.update(over)
    return form


def ip_form(n: int | None = None, **over: str) -> dict[str, str]:
    n = n or next(_seq)
    form = fl_form(n, login=f"ip{n}", fl_fio="Петров Пётр Петрович")
    form.update({"ip_ogrnip": make_ogrnip(n), "ip_reg_date": "2020-01-15",
                 "ip_bank_account": make_account("40802", n), "ip_bank_bik": BIK})
    form.update(over)
    return form


def ul_form(n: int | None = None, **over: str) -> dict[str, str]:
    n = n or next(_seq)
    form = {
        "login": f"company{n}", "password": PASSWORD, "password2": PASSWORD,
        "ul_company_name": "ООО «Ромашка»", "ul_ogrn": make_ogrn(n),
        "ul_inn_kpp": f"{make_inn10(n)} / 770101001", "ul_addr_city": CITY, "ul_addr_street": "ул. Тверская, д. 7",
        "ul_rep_fio": "Смирнов Алексей Викторович, +7 900 000-00-00", "ul_rep_passport": f"46{n:08d}",
        "ul_rep_inn": make_inn12(100_000 + n), "ul_rep_role": "signer",
        "ul_bank_account": make_account("40702", n), "ul_bank_bik": BIK,
    }
    form.update(over)
    return form


def rov_form(**over: Any) -> dict[str, Any]:
    """Валидная форма «Решения о выпуске» (сбор заявок с сегодняшнего дня)."""
    form = {
        "issuer_full_name": "Общество с ограниченной ответственностью «Ромашка»",
        "ogrn_date": "2018-03-14", "beneficiary": "Иванов Сергей Петрович (100 % долей)",
        "website": "romashka.example", "title": "Ромашка-01", "price_per_cfa": "1000", "coupon_rate": "18.5",
        "coupon_period": "quarterly", "min_qty": "10", "max_qty": "1000", "min_order_qty": "1",
        "max_order_qty": "", "placement_start": TODAY.isoformat(),
        "placement_end": (TODAY + timedelta(days=30)).isoformat(),
        "maturity_date": (TODAY + timedelta(days=400)).isoformat(),
        "payment_method": "wallet", "investors": "all", "buyback": "no", "collateral_type": "none",
        "liability_limited": "no", "early_redemption": "no",
        "risks": ["market", "liquidity", "credit"],
        "signer_name": "Смирнов Алексей Викторович", "signer_position": "Генеральный директор", "confirm": "true",
    }
    form.update(over)
    return form


def valid_rov(**over: Any) -> dict[str, Any]:
    rov, errors = validators.validate_rov(rov_form(**over))
    assert not errors, errors
    return rov


# ------------------------------------------------------------------------------
# База данных
# ------------------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cfa_test.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Чистая база со схемой платформы."""
    c = _ORIG_GET_CONNECTION(db_path)
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def patch_db(monkeypatch: pytest.MonkeyPatch, db_path: Path, conn: sqlite3.Connection) -> Path:
    """Все модули, открывающие соединение сами (keeper, common.get_db), работают с тестовой базой."""
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "get_connection", functools.partial(_ORIG_GET_CONNECTION, db_path))
    return db_path


@pytest.fixture
def admin_id(conn: sqlite3.Connection) -> int:
    from werkzeug.security import generate_password_hash
    return db.create_admin(conn, "admin", generate_password_hash(PASSWORD), "Администратор")


UserFactory = Callable[..., int]


@pytest.fixture
def make_user(conn: sqlite3.Connection, admin_id: int) -> UserFactory:
    """Регистрирует участника через validate_registration + register_user; по умолчанию сразу допускает."""
    from werkzeug.security import generate_password_hash

    def _make(role: str = "FL", *, approve: bool = True, balance: int = 0, qualified: bool = False,
              chain=None, **over: str) -> int:
        n = next(_seq)
        form = {"FL": fl_form, "IP": ip_form, "UL": ul_form}[role](
            n, **({"fl_investor_status": "qualified"} if qualified and role != "UL" else {}), **over)
        clean, errors = validators.validate_registration(role, form)
        assert not errors, errors
        p = clean["profile"]
        uid = db.register_user(
            conn, role=role, login=clean["login"], password_hash=generate_password_hash(PASSWORD), profile=p,
            profile_json=clean["anketa"], evm_address="0x" + secrets.token_hex(20), wallet_name="Кошелёк",
            bank_account=p.get("bank_account", "40817810" + "0" * 12), bank_bik=p.get("bank_bik", BIK),
        )["user_id"]
        if approve:
            db.approve_user(conn, chain, admin_id, uid)
        if balance:
            db.deposit_wallet(conn, uid, balance, chain)
        return uid

    return _make


IssueFactory = Callable[..., int]


@pytest.fixture
def make_issue(conn: sqlite3.Connection, admin_id: int) -> IssueFactory:
    """Подаёт РоВ от эмитента и (по умолчанию) публикует его на витрине."""

    def _make(issuer_id: int, *, publish: bool = True, chain=None, **rov_over: Any) -> int:
        issue_id = db.save_issue_decision(conn, issuer_id, valid_rov(**rov_over))["issue_id"]
        if publish:
            db.approve_issue(conn, chain, admin_id, issue_id)
        return issue_id

    return _make


def wallet(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()


def issue_row(conn: sqlite3.Connection, issue_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()


def shift_dvp_date(conn: sqlite3.Connection, issue_id: int, days_ago: int, maturity: date | None = None) -> None:
    """Переносит дату размещения (DvP) в прошлое, чтобы наступили даты купонов / погашения."""
    finalized = (TODAY - timedelta(days=days_ago)).isoformat() + " 12:00:00"
    with conn:
        conn.execute("UPDATE cfa_issues SET finalized_at = ? WHERE id = ?", (finalized, issue_id))
        if maturity:
            conn.execute("UPDATE cfa_issues SET maturity_date = ? WHERE id = ?", (maturity.isoformat(), issue_id))


# ------------------------------------------------------------------------------
# Flask-приложение (блокчейн выключен — быстрые и изолированные API-тесты)
# ------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def flask_app(tmp_path_factory: pytest.TempPathFactory):
    """Импорт main создаёт базу и сеть EVM при старте — делаем это один раз на отдельном файле."""
    boot = tmp_path_factory.mktemp("boot") / "boot.db"
    mp = pytest.MonkeyPatch()
    mp.setattr(db, "DB_PATH", boot)
    mp.setattr(db, "get_connection", functools.partial(_ORIG_GET_CONNECTION, boot))
    import main
    mp.undo()
    main.app.config.update(TESTING=True)
    return main.app


@pytest.fixture
def app(flask_app, patch_db: Path, monkeypatch: pytest.MonkeyPatch):
    import common
    monkeypatch.setattr(common, "CHAIN", None)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def login_as(client, user_id: int) -> None:
    with client.session_transaction() as s:
        s["user_id"] = user_id


# ------------------------------------------------------------------------------
# Встроенная EVM (настоящий смарт-контракт CFAPlatform)
# ------------------------------------------------------------------------------
@pytest.fixture
def chain(patch_db: Path):
    pytest.importorskip("web3")
    from blockchain import Chain
    ch = Chain(db.get_connection)
    if not ch.enabled:
        pytest.skip(f"блокчейн недоступен: {ch.reason}")
    return ch


def json_of(resp) -> dict:
    return json.loads(resp.data)
