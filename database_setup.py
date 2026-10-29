#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
database_setup.py — база данных SQLite и бизнес-логика платформы ЦФА.

Использует только встроенные библиотеки `sqlite3`, `hashlib`, `json`.
Запуск `python database_setup.py` пересоздаёт файл `cfa.db` с пустыми таблицами.

Каждая операция с деньгами или выпусками выполняется в ОДНОЙ транзакции SQLite (`with conn:`)
и, если блокчейн включён, дублируется вызовом смарт-контракта CFAPlatform. Вызов контракта стоит
в конце транзакции: если контракт отклонит операцию, исключение откатит и изменения в SQLite.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from blockchain import doc_hash, to_bytes32
from validators import ISSUER_ROLES, PERSON_ROLES, SIGN_PEP_ROLES

DB_PATH = Path(__file__).parent / "cfa.db"


# ==============================================================================
# 1. ПОДКЛЮЧЕНИЕ К БАЗЕ И ВКЛЮЧЕНИЕ ВНЕШНИХ КЛЮЧЕЙ (FOREIGN KEYS)
# ==============================================================================
def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Возвращает соединение с SQLite, где строки доступны по именам колонок."""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    # В SQLite проверка связей между таблицами по умолчанию выключена — включаем её!
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def calc_sha256(payload: str) -> str:
    """Короткая функция для генерации криптохэша SHA-256 (имитация УКЭП и блоков реестра)."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ==============================================================================
# 2. СХЕМА: 8 ТАБЛИЦ ПЛАТФОРМЫ (+ 2 таблицы журнала блокчейна создаёт blockchain.py)
# ==============================================================================
SCHEMA_SQL = """
-- 1. Участники: физлица (FL), юрлица (UL) и администраторы платформы (ADMIN)
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    role              TEXT NOT NULL CHECK (role IN ('FL', 'UL', 'IP', 'ADMIN')),   -- физлицо, юрлицо, ИП, админ
    full_name         TEXT NOT NULL,                              -- ФИО / сокращённое наименование / «ИП Фамилия И. О.»
    inn               TEXT UNIQUE,                                -- ИНН (у администратора нет)
    snils_or_ogrn     TEXT,                                       -- СНИЛС (ФЛ), ОГРН / КПП (ЮЛ) или ОГРНИП (ИП)
    phone             TEXT,
    email             TEXT,
    passport_or_rep   TEXT,                                       -- Паспорт ФЛ и ИП или представитель ЮЛ
    address           TEXT,
    is_qualified      INTEGER NOT NULL DEFAULT 0,                 -- 0 = неквал (лимит 600 000 ₽/год), 1 = квал / ЮЛ
    yearly_spent_rub  INTEGER NOT NULL DEFAULT 0,                 -- Сколько неквал ФЛ или ИП уже инвестировал за год
    evm_address       TEXT,                                       -- Адрес участника в EVM-сети платформы
    login             TEXT NOT NULL UNIQUE,                       -- Логин (в нижнем регистре)
    password_hash     TEXT NOT NULL,                              -- Хэш пароля (сам пароль НЕ хранится!)
    status            TEXT NOT NULL DEFAULT 'PENDING'             -- Допуск к платформе:
                      CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'BLOCKED')),
    profile_json      TEXT,                                       -- Полная анкета для проверки администратором
    reject_reason     TEXT,                                       -- Причина отказа / блокировки
    reviewed_by       INTEGER REFERENCES users(id),               -- Кто из администраторов проверил
    reviewed_at       TEXT,
    evm_tx_hash       TEXT,                                       -- Транзакция включения в белый список
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (role = 'ADMIN' OR inn IS NOT NULL)
);

-- 2. Цифровые кошельки (у администраторов кошелька нет)
CREATE TABLE IF NOT EXISTS wallets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL UNIQUE,
    wallet_name       TEXT NOT NULL DEFAULT 'Основной кошелёк',
    bank_account      TEXT NOT NULL,
    bank_bik          TEXT DEFAULT '044525225',
    balance_rub       INTEGER NOT NULL DEFAULT 0 CHECK (balance_rub >= 0), -- Свободные рубли
    locked_rub        INTEGER NOT NULL DEFAULT 0 CHECK (locked_rub >= 0),  -- Заморожено под заявки
    status            TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. Выпуски ЦФА = «Решения о выпуске». Проходят модерацию администратора.
CREATE TABLE IF NOT EXISTS cfa_issues (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,          -- он же идентификатор выпуска в смарт-контракте
    issuer_id         INTEGER NOT NULL,
    title             TEXT NOT NULL,
    price_per_cfa     INTEGER NOT NULL CHECK (price_per_cfa > 0),
    min_qty           INTEGER NOT NULL CHECK (min_qty > 0),       -- Порог состоявшегося выпуска
    max_qty           INTEGER NOT NULL CHECK (max_qty >= min_qty),
    collected_qty     INTEGER NOT NULL DEFAULT 0 CHECK (collected_qty <= max_qty),
    min_order_qty     INTEGER NOT NULL DEFAULT 1 CHECK (min_order_qty > 0),  -- Мин. ЦФА в одной заявке
    max_order_qty     INTEGER,                                    -- Макс. ЦФА в одной заявке (NULL — без ограничения)
    coupon_rate       REAL NOT NULL DEFAULT 0,                    -- Фиксированный доход, % годовых
    maturity_date     TEXT NOT NULL,                              -- Дата погашения
    placement_start   TEXT NOT NULL,                              -- Период размещения (сбора заявок)
    placement_end     TEXT NOT NULL,
    only_qual         INTEGER NOT NULL DEFAULT 0,                 -- 1 = только для квалифицированных
    rov_json          TEXT NOT NULL,                              -- Все поля «Решения о выпуске»
    doc_hash          TEXT NOT NULL,                              -- SHA-256 РоВ — записывается в блокчейн
    ukep_signature    TEXT NOT NULL,                              -- Подпись Решения о выпуске (имитация УКЭП)
    status            TEXT NOT NULL DEFAULT 'REVIEW'
                      CHECK (status IN ('REVIEW', 'REJECTED', 'SUBSCRIPTION', 'ISSUED', 'CANCELLED', 'REDEEMED')),
    reject_reason     TEXT,
    reviewed_by       INTEGER REFERENCES users(id),
    reviewed_at       TEXT,
    published_at      TEXT,
    finalized_at      TEXT,
    evm_tx_hash       TEXT,                                       -- Транзакция регистрации выпуска в контракте
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (issuer_id) REFERENCES users(id)
);

-- 4. Заявки на покупку
CREATE TABLE IF NOT EXISTS orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    issue_id          INTEGER NOT NULL,
    qty               INTEGER NOT NULL CHECK (qty > 0),
    total_rub         INTEGER NOT NULL CHECK (total_rub > 0),
    sign_type         TEXT NOT NULL CHECK (sign_type IN ('PEP_SMS', 'UKEP_CMS')),
    signature_hash    TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'HOLD'
                      CHECK (status IN ('HOLD', 'EXECUTED', 'REFUNDED')),
    evm_tx_hash       TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (issue_id) REFERENCES cfa_issues(id)
);

-- 5. Портфель инвестора
CREATE TABLE IF NOT EXISTS portfolio_assets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    issue_id            INTEGER NOT NULL,
    qty                 INTEGER NOT NULL CHECK (qty >= 0),
    invested_rub        INTEGER NOT NULL DEFAULT 0,
    expected_coupon_rub INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user_id, issue_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (issue_id) REFERENCES cfa_issues(id)
);

-- 6. Журнал операций с цепочкой хэшей (для «Истории операций» участника)
CREATE TABLE IF NOT EXISTS blockchain_ledger (
    block_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    op_type           TEXT NOT NULL,
    amount_rub        INTEGER NOT NULL DEFAULT 0,
    description       TEXT NOT NULL,
    prev_hash         TEXT NOT NULL,
    tx_hash           TEXT NOT NULL UNIQUE,
    evm_tx_hash       TEXT,                                       -- Хэш транзакции в EVM-сети (если включена)
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 7. Журнал действий администраторов (аудит)
CREATE TABLE IF NOT EXISTS admin_actions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id          INTEGER NOT NULL REFERENCES users(id),
    action            TEXT NOT NULL,                              -- USER_APPROVE, ISSUE_REJECT, ...
    target_type       TEXT NOT NULL,                              -- user / issue
    target_id         INTEGER NOT NULL,
    comment           TEXT,
    evm_tx_hash       TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 8. Настройки платформы (рыночные ориентиры для аналитики и т. п.), значение — JSON
CREATE TABLE IF NOT EXISTS platform_settings (
    key               TEXT PRIMARY KEY,
    value             TEXT NOT NULL,
    updated_by        INTEGER REFERENCES users(id),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 9. Выплаты по размещённым выпускам: купоны по графику и погашение номинала (с кошелька эмитента)
CREATE TABLE IF NOT EXISTS cfa_payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id          INTEGER NOT NULL REFERENCES cfa_issues(id),
    kind              TEXT NOT NULL CHECK (kind IN ('COUPON', 'REDEMPTION')),
    period_no         INTEGER NOT NULL,                           -- № купонного периода; 0 — погашение
    period_start      TEXT,
    period_end        TEXT,
    days              INTEGER NOT NULL DEFAULT 0,
    pay_date          TEXT NOT NULL,                              -- дата выплаты по графику
    rate_bp           INTEGER NOT NULL DEFAULT 0,                 -- ставка в сотых долях процента (18,5 % = 1850)
    amount_total      INTEGER NOT NULL CHECK (amount_total >= 0),
    holders           INTEGER NOT NULL DEFAULT 0,
    early             INTEGER NOT NULL DEFAULT 0,                 -- 1 = выплачено раньше даты по графику
    evm_tx_hash       TEXT,
    paid_at           TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (issue_id, kind, period_no)
);

-- 11. Журнал «сторожа» (keeper): что он проверил и выполнил по датам выплат
CREATE TABLE IF NOT EXISTS keeper_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id          INTEGER REFERENCES cfa_issues(id),
    action            TEXT NOT NULL,                              -- COUPON / REDEEM / RUN
    result            TEXT NOT NULL CHECK (result IN ('OK', 'NO_FUNDS', 'ERROR', 'IDLE')),
    message           TEXT NOT NULL,
    source            TEXT NOT NULL,                              -- thread / request / cli / admin
    evm_tx_hash       TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 10. Кому и сколько перечислено по каждой выплате
CREATE TABLE IF NOT EXISTS cfa_payouts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id        INTEGER NOT NULL REFERENCES cfa_payments(id),
    user_id           INTEGER NOT NULL REFERENCES users(id),
    qty               INTEGER NOT NULL,
    amount_rub        INTEGER NOT NULL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы (если их нет)."""
    with conn:
        conn.executescript(SCHEMA_SQL)


class FieldError(ValueError):
    """Ошибка, привязанная к конкретному полю формы (например, 'login' или 'inn')."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Даты выплат считаются по московскому времени — так же, как в смарт-контракте (00:00 МСК).
# Это важно на хостинге, где часы сервера идут по UTC (например, PythonAnywhere).
MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    return datetime.now(MSK)


def today_msk() -> date:
    return now_msk().date()


def msk_ts(d: date) -> int:
    """Unix-время начала дня по Москве — так дата хранится в контракте."""
    return int(datetime(d.year, d.month, d.day, tzinfo=MSK).timestamp())


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


# ==============================================================================
# 3. ЖУРНАЛЫ: цепочка хэшей операций и действия администраторов
# ==============================================================================
def append_ledger_block(
    conn: sqlite3.Connection,
    user_id: int,
    op_type: str,
    amount_rub: int,
    description: str,
    evm_tx_hash: Optional[str] = None,
) -> str:
    """Добавляет запись в `blockchain_ledger`, связывая её хэшем с предыдущим блоком."""
    last_row = conn.execute(
        "SELECT tx_hash FROM blockchain_ledger ORDER BY block_id DESC LIMIT 1"
    ).fetchone()
    prev_hash = last_row["tx_hash"] if last_row else "0" * 64

    timestamp = datetime.now().isoformat()
    raw_block = f"{prev_hash}|{user_id}|{op_type}|{amount_rub}|{description}|{timestamp}"
    tx_hash = "0x" + calc_sha256(raw_block)[:62]

    conn.execute(
        """
        INSERT INTO blockchain_ledger (user_id, op_type, amount_rub, description, prev_hash, tx_hash, evm_tx_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, op_type, amount_rub, description, prev_hash, tx_hash, evm_tx_hash),
    )
    return tx_hash


def log_admin_action(conn, admin_id: int, action: str, target_type: str, target_id: int,
                     comment: str = "", evm_tx_hash: Optional[str] = None) -> None:
    conn.execute(
        """INSERT INTO admin_actions (admin_id, action, target_type, target_id, comment, evm_tx_hash)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (admin_id, action, target_type, target_id, comment, evm_tx_hash),
    )


def _chain_tx(chain, conn, method: str, *args) -> Optional[str]:
    """Вызов контракта, если блокчейн включён; иначе None."""
    if chain is None or not chain.enabled:
        return None
    return chain.transact(conn, method, *args)


def _addr(chain, user) -> str:
    return chain.address_of(user["evm_address"]) if chain is not None and chain.enabled else user["evm_address"]


# ==============================================================================
# 4. УЧАСТНИКИ: регистрация, администраторы, допуск к платформе
# ==============================================================================
def get_user_by_login(conn: sqlite3.Connection, login: str):
    """Ищет участника по логину (логины хранятся в нижнем регистре)."""
    return conn.execute("SELECT * FROM users WHERE login = ?", (login.lower(),)).fetchone()


def register_user(
    conn: sqlite3.Connection,
    role: str,
    login: str,
    password_hash: str,
    profile: Dict[str, Any],
    profile_json: Dict[str, Any],
    evm_address: str,
    wallet_name: str,
    bank_account: str,
    bank_bik: str,
) -> Dict[str, Any]:
    """
    Регистрация участника: создаётся запись со статусом PENDING и кошелёк.
    Пользоваться платформой участник сможет после проверки анкеты администратором.
    """
    with conn:
        if conn.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
            raise FieldError("login", "Этот логин уже занят — придумайте другой.")
        if conn.execute("SELECT 1 FROM users WHERE inn = ?", (profile["inn"],)).fetchone():
            raise FieldError("inn", "Участник с таким ИНН уже зарегистрирован — войдите по логину и паролю.")

        cursor = conn.execute(
            """
            INSERT INTO users (role, full_name, inn, snils_or_ogrn, phone, email, passport_or_rep,
                               address, is_qualified, evm_address, login, password_hash, profile_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (role, profile["full_name"], profile["inn"], profile["snils_or_ogrn"], profile["phone"],
             profile["email"], profile["passport_or_rep"], profile["address"], profile["is_qualified"],
             evm_address, login, password_hash, json.dumps(profile_json, ensure_ascii=False)),
        )
        user_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO wallets (user_id, wallet_name, bank_account, bank_bik) VALUES (?, ?, ?, ?)",
            (user_id, wallet_name, bank_account, bank_bik),
        )
        append_ledger_block(conn, user_id, "KYC_SUBMIT", 0,
                            "Анкета участника направлена на проверку администратору")
    return {"user_id": user_id}


def create_admin(conn: sqlite3.Connection, login: str, password_hash: str, full_name: str) -> int:
    """Создаёт администратора платформы (без кошелька и анкеты, сразу APPROVED)."""
    with conn:
        if conn.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
            raise ValueError(f"Логин «{login}» уже занят.")
        cur = conn.execute(
            """INSERT INTO users (role, full_name, login, password_hash, status, is_qualified)
               VALUES ('ADMIN', ?, ?, ?, 'APPROVED', 0)""",
            (full_name, login, password_hash),
        )
    return cur.lastrowid


def approve_user(conn: sqlite3.Connection, chain, admin_id: int, user_id: int, comment: str = "") -> Dict[str, Any]:
    """Администратор подтверждает допуск участника: статус APPROVED + белый список в смарт-контракте."""
    with conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or user["role"] == "ADMIN":
            raise ValueError("Участник не найден.")
        if user["status"] == "APPROVED":
            raise ValueError("Участник уже допущен к платформе.")
        qualified = bool(user["is_qualified"]) or user["role"] == "UL"
        conn.execute(
            """UPDATE users SET status = 'APPROVED', reject_reason = NULL, reviewed_by = ?, reviewed_at = ?
               WHERE id = ?""", (admin_id, _now(), user_id))
        evm = _chain_tx(chain, conn, "setParticipant", _addr(chain, user), True, qualified)
        conn.execute("UPDATE users SET evm_tx_hash = ? WHERE id = ?", (evm, user_id))
        append_ledger_block(conn, user_id, "KYC_APPROVE", 0,
                            "Анкета проверена — допуск к платформе подтверждён", evm)
        log_admin_action(conn, admin_id, "USER_APPROVE", "user", user_id, comment, evm)
    return {"evm_tx_hash": evm}


def reject_user(conn: sqlite3.Connection, admin_id: int, user_id: int, reason: str) -> None:
    """Отказ в допуске (с обязательной причиной — её увидит участник)."""
    if not reason.strip():
        raise FieldError("reason", "Укажите причину отказа — её увидит участник.")
    with conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or user["role"] == "ADMIN":
            raise ValueError("Участник не найден.")
        if user["status"] != "PENDING":
            raise ValueError("Отклонить можно только анкету, ожидающую проверки.")
        conn.execute(
            """UPDATE users SET status = 'REJECTED', reject_reason = ?, reviewed_by = ?, reviewed_at = ?
               WHERE id = ?""", (reason.strip(), admin_id, _now(), user_id))
        append_ledger_block(conn, user_id, "KYC_REJECT", 0, f"В допуске отказано: {reason.strip()}")
        log_admin_action(conn, admin_id, "USER_REJECT", "user", user_id, reason.strip())


def block_user(conn: sqlite3.Connection, chain, admin_id: int, user_id: int, reason: str) -> None:
    """Блокировка допущенного участника: исключение из белого списка смарт-контракта."""
    if not reason.strip():
        raise FieldError("reason", "Укажите причину блокировки.")
    with conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or user["status"] != "APPROVED" or user["role"] == "ADMIN":
            raise ValueError("Заблокировать можно только допущенного участника.")
        conn.execute(
            """UPDATE users SET status = 'BLOCKED', reject_reason = ?, reviewed_by = ?, reviewed_at = ?
               WHERE id = ?""", (reason.strip(), admin_id, _now(), user_id))
        evm = _chain_tx(chain, conn, "setParticipant", _addr(chain, user), False, False)
        append_ledger_block(conn, user_id, "KYC_BLOCK", 0, f"Доступ к платформе заблокирован: {reason.strip()}", evm)
        log_admin_action(conn, admin_id, "USER_BLOCK", "user", user_id, reason.strip(), evm)
    return {"evm_tx_hash": evm}


# ==============================================================================
# 5. КОШЕЛЁК: пополнение и вывод (зеркалируются в «цифровые рубли» контракта)
# ==============================================================================
def deposit_wallet(conn: sqlite3.Connection, user_id: int, amount_rub: int, chain=None) -> Dict[str, Any]:
    """Пополнение цифрового кошелька."""
    if amount_rub <= 0:
        raise ValueError("Сумма пополнения должна быть больше нуля!")
    if amount_rub > 1_000_000_000:
        raise ValueError("Слишком большая сумма для одного пополнения.")

    with conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.execute(
            "UPDATE wallets SET balance_rub = balance_rub + ? WHERE user_id = ?",
            (amount_rub, user_id),
        )
        evm = _chain_tx(chain, conn, "depositRub", _addr(chain, user), amount_rub)
        tx_hash = append_ledger_block(
            conn, user_id=user_id, op_type="DEPOSIT", amount_rub=amount_rub,
            description=f"Пополнение цифрового кошелька на {_fmt(amount_rub)} ₽", evm_tx_hash=evm,
        )
    return {"tx_hash": tx_hash, "evm_tx_hash": evm}


def withdraw_wallet(conn: sqlite3.Connection, user_id: int, amount_rub: int, chain=None) -> Dict[str, Any]:
    """Вывод свободных средств с цифрового кошелька на привязанный банковский счёт."""
    if amount_rub <= 0:
        raise ValueError("Сумма вывода должна быть больше нуля!")

    with conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
        if not wallet:
            raise ValueError("Кошелёк не найден!")
        if wallet["balance_rub"] < amount_rub:
            raise ValueError(f"Недостаточно свободных средств! Доступно {_fmt(wallet['balance_rub'])} ₽.")
        conn.execute(
            "UPDATE wallets SET balance_rub = balance_rub - ? WHERE user_id = ?",
            (amount_rub, user_id),
        )
        evm = _chain_tx(chain, conn, "withdrawRub", _addr(chain, user), amount_rub)
        tx_hash = append_ledger_block(
            conn, user_id=user_id, op_type="WITHDRAW", amount_rub=amount_rub,
            description=f"Вывод средств на банковский счёт {wallet['bank_account']} на {_fmt(amount_rub)} ₽",
            evm_tx_hash=evm,
        )
    return {"tx_hash": tx_hash, "evm_tx_hash": evm}


# ==============================================================================
# 6. ВЫПУСКИ: Решение о выпуске → модерация → публикация на витрине
# ==============================================================================
def save_issue_decision(conn: sqlite3.Connection, issuer_id: int, rov: Dict[str, Any],
                        issue_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Эмитент подаёт (или исправляет) «Решение о выпуске». Выпуск получает статус REVIEW
    и ждёт проверки администратором. Исправлять можно, пока выпуск на проверке или отклонён.
    """
    with conn:
        issuer = conn.execute("SELECT * FROM users WHERE id = ?", (issuer_id,)).fetchone()
        if not issuer or issuer["role"] not in ISSUER_ROLES:
            raise ValueError("Выпускать ЦФА по закону 259-ФЗ могут только юридические лица и ИП!")

        # В документ попадают и сведения об эмитенте — на момент подачи
        document = {**rov, "issuer": {
            "name": issuer["full_name"], "inn_kpp_ogrn": issuer["snils_or_ogrn"], "inn": issuer["inn"],
            "address": issuer["address"], "type": issuer["role"],
        }}
        dhash = doc_hash(document)
        ukep_sig = "UKEP_SIG_" + calc_sha256(f"{issuer['inn']}|{dhash}")[:24]
        params = (rov["title"], rov["price_per_cfa"], rov["min_qty"], rov["max_qty"], rov["min_order_qty"],
                  rov["max_order_qty"], rov["coupon_rate"], rov["maturity_date"], rov["placement_start"],
                  rov["placement_end"], 1 if rov["investors"] == "qualified" else 0,
                  json.dumps(document, ensure_ascii=False), dhash, ukep_sig)

        if issue_id is None:
            cur = conn.execute(
                """INSERT INTO cfa_issues (title, price_per_cfa, min_qty, max_qty, min_order_qty, max_order_qty,
                       coupon_rate, maturity_date, placement_start, placement_end, only_qual, rov_json, doc_hash,
                       ukep_signature, issuer_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'REVIEW')""", (*params, issuer_id))
            issue_id = cur.lastrowid
            op_text = f"Решение о выпуске «{rov['title']}» подписано УКЭП и направлено на проверку"
        else:
            current = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
            if not current or current["issuer_id"] != issuer_id:
                raise ValueError("Выпуск не найден.")
            if current["status"] not in ("REVIEW", "REJECTED"):
                raise ValueError("Опубликованный выпуск изменить нельзя.")
            conn.execute(
                """UPDATE cfa_issues SET title = ?, price_per_cfa = ?, min_qty = ?, max_qty = ?, min_order_qty = ?,
                       max_order_qty = ?, coupon_rate = ?, maturity_date = ?, placement_start = ?, placement_end = ?,
                       only_qual = ?, rov_json = ?, doc_hash = ?, ukep_signature = ?, status = 'REVIEW',
                       reject_reason = NULL, updated_at = datetime('now', 'localtime')
                   WHERE id = ?""", (*params, issue_id))
            op_text = f"Исправленное Решение о выпуске «{rov['title']}» повторно направлено на проверку"

        tx_hash = append_ledger_block(conn, issuer_id, "ISSUE_SUBMIT", rov["price_per_cfa"] * rov["max_qty"], op_text)
    return {"issue_id": issue_id, "doc_hash": dhash, "ukep_signature": ukep_sig, "tx_hash": tx_hash}


def approve_issue(conn: sqlite3.Connection, chain, admin_id: int, issue_id: int, comment: str = "") -> Dict[str, Any]:
    """Администратор одобрил РоВ → выпуск публикуется на витрине и регистрируется в смарт-контракте."""
    with conn:
        issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
        if not issue:
            raise ValueError("Выпуск не найден.")
        if issue["status"] != "REVIEW":
            raise ValueError("Одобрить можно только выпуск, ожидающий проверки.")
        issuer = conn.execute("SELECT * FROM users WHERE id = ?", (issue["issuer_id"],)).fetchone()
        if issuer["status"] != "APPROVED":
            raise ValueError("Эмитент не допущен к платформе — сначала проверьте его анкету.")
        if issue["placement_end"] < date.today().isoformat():
            raise ValueError("Период размещения уже закончился — попросите эмитента исправить даты.")

        conn.execute(
            """UPDATE cfa_issues SET status = 'SUBSCRIPTION', reviewed_by = ?, reviewed_at = ?, published_at = ?
               WHERE id = ?""", (admin_id, _now(), _now(), issue_id))
        evm = _chain_tx(chain, conn, "registerIssue", issue_id, _addr(chain, issuer), issue["price_per_cfa"],
                        issue["min_qty"], issue["max_qty"], bool(issue["only_qual"]), to_bytes32(issue["doc_hash"]))
        conn.execute("UPDATE cfa_issues SET evm_tx_hash = ? WHERE id = ?", (evm, issue_id))
        append_ledger_block(
            conn, issue["issuer_id"], "ISSUE_CREATE", issue["price_per_cfa"] * issue["max_qty"],
            f"Решение о выпуске «{issue['title']}» одобрено и опубликовано на витрине "
            f"(объём до {_fmt(issue['max_qty'])} шт. по {_fmt(issue['price_per_cfa'])} ₽)", evm)
        log_admin_action(conn, admin_id, "ISSUE_APPROVE", "issue", issue_id, comment, evm)
    return {"evm_tx_hash": evm}


def reject_issue(conn: sqlite3.Connection, admin_id: int, issue_id: int, reason: str) -> None:
    """Отклонение РоВ с причиной: эмитент исправит документ и отправит повторно."""
    if not reason.strip():
        raise FieldError("reason", "Укажите, что нужно исправить в Решении о выпуске.")
    with conn:
        issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
        if not issue or issue["status"] != "REVIEW":
            raise ValueError("Отклонить можно только выпуск, ожидающий проверки.")
        conn.execute(
            """UPDATE cfa_issues SET status = 'REJECTED', reject_reason = ?, reviewed_by = ?, reviewed_at = ?
               WHERE id = ?""", (reason.strip(), admin_id, _now(), issue_id))
        append_ledger_block(conn, issue["issuer_id"], "ISSUE_REJECT", 0,
                            f"Решение о выпуске «{issue['title']}» возвращено на доработку: {reason.strip()}")
        log_admin_action(conn, admin_id, "ISSUE_REJECT", "issue", issue_id, reason.strip())


# ==============================================================================
# 7. ПОКУПКА НА ВИТРИНЕ И АТОМАРНЫЙ DvP
# ==============================================================================
def buy_cfa_on_showcase(
    conn: sqlite3.Connection,
    user_id: int,
    issue_id: int,
    qty: int,
    sign_code: str,
    chain=None,
) -> Dict[str, Any]:
    """
    Покупка ЦФА на витрине. Внутри одной транзакции проверяет период размещения, ограничения
    на одну заявку, баланс, лимит 600 000 ₽ для неквал ФЛ, код ПЭП / подпись УКЭП,
    резервирует рубли и регистрирует заявку в смарт-контракте.
    """
    if qty <= 0:
        raise ValueError("Количество ЦФА должно быть больше 0!")

    with conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
        issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()

        if not user or not wallet or not issue:
            raise ValueError("Пользователь, кошелёк или выпуск не найден!")
        if issue["status"] != "SUBSCRIPTION":
            raise ValueError("Сбор заявок по этому выпуску уже закрыт!")
        if issue["issuer_id"] == user_id:
            raise ValueError("Эмитент не может покупать ЦФА собственного выпуска.")

        today = date.today().isoformat()
        if today < issue["placement_start"]:
            raise ValueError(f"Приём заявок начнётся {issue['placement_start']}.")
        if today > issue["placement_end"]:
            raise ValueError("Период размещения закончился.")
        if qty < issue["min_order_qty"]:
            raise ValueError(f"Минимум в одной заявке — {_fmt(issue['min_order_qty'])} шт.")
        if issue["max_order_qty"] and qty > issue["max_order_qty"]:
            raise ValueError(f"Максимум в одной заявке — {_fmt(issue['max_order_qty'])} шт.")
        if issue["collected_qty"] + qty > issue["max_qty"]:
            left_qty = issue["max_qty"] - issue["collected_qty"]
            raise ValueError(f"На витрине осталось всего {_fmt(left_qty)} шт. ЦФА этого выпуска!")

        total_rub = qty * issue["price_per_cfa"]
        if wallet["balance_rub"] < total_rub:
            raise ValueError(
                f"Недостаточно свободных средств! Нужно {_fmt(total_rub)} ₽, на кошельке {_fmt(wallet['balance_rub'])} ₽."
            )

        # РАЗЛИЧИЕ 1: физические лица (ФЛ и ИП) — квалификация и лимит 600 000 ₽;
        #              ФЛ подписывает кодом из СМС, ИП — своей УКЭП
        if user["role"] in PERSON_ROLES:
            if issue["only_qual"] == 1 and user["is_qualified"] == 0:
                raise ValueError("Этот выпуск доступен только квалифицированным инвесторам!")
            if user["is_qualified"] == 0 and (user["yearly_spent_rub"] + total_rub > 600_000):
                rem = 600_000 - user["yearly_spent_rub"]
                raise ValueError(
                    f"Превышен годовой лимит 600 000 ₽ для неквалифицированного инвестора-физлица "
                    f"(Указание ЦБ 5635-У)! Доступный остаток лимита: {_fmt(rem)} ₽."
                )
            conn.execute("UPDATE users SET yearly_spent_rub = yearly_spent_rub + ? WHERE id = ?",
                         (total_rub, user_id))

        # РАЗЛИЧИЕ 2: подпись заявки — ФЛ кодом из СМС (ПЭП), ЮЛ и ИП — УКЭП (лимита у ЮЛ нет)
        if user["role"] in SIGN_PEP_ROLES:
            if sign_code.strip() != "1234":
                raise ValueError("Неверный СМС-код ПЭП! Для тестового прототипа введите 1234.")
            sign_type = "PEP_SMS"
            sig_hash = "PEP_SMS_" + calc_sha256(f"{user['id']}|{issue_id}|{qty}|1234")[:20]
        else:
            sign_type = "UKEP_CMS"
            sig_hash = "UKEP_CMS_" + calc_sha256(f"{user['inn']}|{issue_id}|{qty}|{sign_code}")[:20]

        conn.execute(
            "UPDATE wallets SET balance_rub = balance_rub - ?, locked_rub = locked_rub + ? WHERE user_id = ?",
            (total_rub, total_rub, user_id),
        )
        conn.execute("UPDATE cfa_issues SET collected_qty = collected_qty + ? WHERE id = ?", (qty, issue_id))
        cursor = conn.execute(
            """INSERT INTO orders (user_id, issue_id, qty, total_rub, sign_type, signature_hash, status)
               VALUES (?, ?, ?, ?, ?, ?, 'HOLD')""",
            (user_id, issue_id, qty, total_rub, sign_type, sig_hash),
        )
        order_id = cursor.lastrowid

        # Блокчейн — последним: если контракт откажет, откатится и всё, что выше
        evm = _chain_tx(chain, conn, "subscribe", issue_id, _addr(chain, user), qty, to_bytes32(sig_hash))
        conn.execute("UPDATE orders SET evm_tx_hash = ? WHERE id = ?", (evm, order_id))
        tx_hash = append_ledger_block(
            conn, user_id=user_id, op_type="BUY_HOLD", amount_rub=total_rub,
            description=f"Покупка на витрине: {_fmt(qty)} шт. «{issue['title']}» (холд {_fmt(total_rub)} ₽, подпись {sign_type})",
            evm_tx_hash=evm,
        )

    return {"order_id": order_id, "signature_hash": sig_hash, "tx_hash": tx_hash, "evm_tx_hash": evm}


def finalize_issue_dvp(conn: sqlite3.Connection, issue_id: int, chain=None) -> Dict[str, Any]:
    """
    Закрытие сбора («Поставка против платежа» / DvP), атомарно в SQLite и в смарт-контракте:
    - собрали >= min_qty: рубли инвесторов → эмитенту, ЦФА → в портфели инвесторов;
    - собрали <  min_qty: выпуск отменяется, замороженные рубли и годовой лимит возвращаются.
    """
    with conn:
        issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
        if not issue:
            raise ValueError("Выпуск не найден!")
        if issue["status"] != "SUBSCRIPTION":
            raise ValueError("Выпуск уже обработан или ещё не опубликован!")

        orders = conn.execute(
            """SELECT o.*, u.role, u.is_qualified FROM orders o JOIN users u ON u.id = o.user_id
               WHERE o.issue_id = ? AND o.status = 'HOLD'""", (issue_id,)
        ).fetchall()

        issued = issue["collected_qty"] >= issue["min_qty"]
        if issued:
            total_raised_rub = 0
            for order in orders:
                inv_id, qty, cost = order["user_id"], order["qty"], order["total_rub"]
                total_raised_rub += cost
                conn.execute("UPDATE wallets SET locked_rub = locked_rub - ? WHERE user_id = ?", (cost, inv_id))
                expected_coupon = int(round(cost * (issue["coupon_rate"] / 100.0)))
                conn.execute(
                    """
                    INSERT INTO portfolio_assets (user_id, issue_id, qty, invested_rub, expected_coupon_rub)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, issue_id) DO UPDATE SET
                        qty                 = qty + excluded.qty,
                        invested_rub        = invested_rub + excluded.invested_rub,
                        expected_coupon_rub = expected_coupon_rub + excluded.expected_coupon_rub,
                        updated_at          = datetime('now', 'localtime')
                    """,
                    (inv_id, issue_id, qty, cost, expected_coupon),
                )
                conn.execute("UPDATE orders SET status = 'EXECUTED' WHERE id = ?", (order["id"],))

            conn.execute("UPDATE wallets SET balance_rub = balance_rub + ? WHERE user_id = ?",
                         (total_raised_rub, issue["issuer_id"]))
            conn.execute("UPDATE cfa_issues SET status = 'ISSUED', finalized_at = ? WHERE id = ?",
                         (now_msk().strftime("%Y-%m-%d %H:%M:%S"), issue_id))
        else:
            for order in orders:
                inv_id, cost = order["user_id"], order["total_rub"]
                conn.execute(
                    "UPDATE wallets SET locked_rub = locked_rub - ?, balance_rub = balance_rub + ? WHERE user_id = ?",
                    (cost, cost, inv_id),
                )
                if order["role"] in PERSON_ROLES:   # годовой лимит ФЛ и ИП возвращается вместе с деньгами
                    conn.execute(
                        "UPDATE users SET yearly_spent_rub = MAX(yearly_spent_rub - ?, 0) WHERE id = ?", (cost, inv_id))
                conn.execute("UPDATE orders SET status = 'REFUNDED' WHERE id = ?", (order["id"],))
            conn.execute("UPDATE cfa_issues SET status = 'CANCELLED', finalized_at = ? WHERE id = ?", (_now(), issue_id))

        evm = _chain_tx(chain, conn, "finalize", issue_id)
        if issued:
            # График купонов — в контракт: дальше он сам проверяет даты выплат и погашения
            placed = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
            periods = coupon_schedule(placed)
            _chain_tx(chain, conn, "setSchedule", issue_id, msk_ts(today_msk()), rate_bp(placed),
                      [msk_ts(date.fromisoformat(p["end"])) for p in periods], [p["days"] for p in periods],
                      json.loads(placed["rov_json"]).get("early_redemption") == "yes")
            tx_hash = append_ledger_block(
                conn, issue["issuer_id"], "DVP_SETTLE", total_raised_rub,
                f"Смарт-контракт DvP: выпуск «{issue['title']}» состоялся! Инвесторам зачислено "
                f"{_fmt(issue['collected_qty'])} ЦФА, эмитенту переведено {_fmt(total_raised_rub)} ₽.", evm)
            return {"result": "ISSUED", "total_raised_rub": total_raised_rub, "tx_hash": tx_hash, "evm_tx_hash": evm}

        tx_hash = append_ledger_block(
            conn, issue["issuer_id"], "ISSUE_REFUND", 0,
            f"Отмена выпуска «{issue['title']}» (не набран минимум). Деньги разблокированы.", evm)
        return {"result": "CANCELLED", "tx_hash": tx_hash, "evm_tx_hash": evm}


# ==============================================================================
# 7б. ВЫПЛАТЫ ВЛАДЕЛЬЦАМ: КУПОНЫ ПО ГРАФИКУ И ПОГАШЕНИЕ (с кошелька эмитента)
# ==============================================================================
# Те же правила, что в смарт-контракте (payCoupon / redeem) и в аналитике (static/analytics.js):
#   купон владельца = qty × номинал × ставка × дни периода / 365, целые рубли с округлением вниз;
#   график — от даты закрытия сбора (DvP) шагом 1 или 3 месяца, хвост короче 15 дней сливается с последним периодом.
COUPON_STEP_MONTHS = {"monthly": 1, "quarterly": 3, "maturity": 0}


def _add_months(d: date, n: int) -> date:
    """31 января + 1 месяц → 28/29 февраля (как в analytics.js)."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days
    return date(y, m, min(d.day, last))


def coupon_schedule(issue) -> list[dict]:
    """Купонные периоды размещённого выпуска: [{no, start, end, days}]. Начало — дата DvP."""
    if not issue["finalized_at"]:
        return []
    start = date.fromisoformat(issue["finalized_at"][:10])
    maturity = date.fromisoformat(issue["maturity_date"])
    if maturity <= start:
        return []
    period = json.loads(issue["rov_json"]).get("coupon_period", "maturity")
    step = COUPON_STEP_MONTHS.get(period, 0)
    ends = []
    if step:
        k = 1
        while True:
            d = _add_months(start, step * k)
            if (maturity - d).days < 15:
                break
            ends.append(d)
            k += 1
    ends.append(maturity)
    out, prev = [], start
    for no, end in enumerate(ends, 1):
        out.append({"no": no, "start": prev.isoformat(), "end": end.isoformat(), "days": (end - prev).days})
        prev = end
    return out


def rate_bp(issue) -> int:
    return int(round(float(issue["coupon_rate"]) * 100))


def coupon_amount(qty: int, price: int, bp: int, days: int) -> int:
    """Ровно та же формула, что couponOf в контракте: целочисленное деление вниз."""
    return qty * price * bp * days // 3650000


def _holders(conn, issue_id: int):
    return conn.execute(
        """SELECT p.user_id, p.qty, u.full_name, u.evm_address FROM portfolio_assets p JOIN users u ON u.id = p.user_id
           WHERE p.issue_id = ? AND p.qty > 0 ORDER BY p.user_id""", (issue_id,)).fetchall()


def payment_plan(conn: sqlite3.Connection, issue) -> Dict[str, Any]:
    """
    План выплат выпуска для эмитента, инвесторов и «сторожа»: купонные периоды со статусами,
    погашение, сколько денег нужно и что можно сделать сейчас. Даты — по московскому времени.
    """
    today = today_msk().isoformat()
    paid = {(r["kind"], r["period_no"]): r for r in
            conn.execute("SELECT * FROM cfa_payments WHERE issue_id = ?", (issue["id"],))}
    holders = _holders(conn, issue["id"])
    placed = sum(h["qty"] for h in holders)
    bp, price = rate_bp(issue), issue["price_per_cfa"]
    red = paid.get(("REDEMPTION", 0))
    rows, next_row = [], None
    for p in coupon_schedule(issue):
        rec = paid.get(("COUPON", p["no"]))
        total = rec["amount_total"] if rec else sum(coupon_amount(h["qty"], price, bp, p["days"]) for h in holders)
        if rec:
            state = "paid"
        elif red:
            state = "cancelled"            # выпуск погашен досрочно — оставшиеся купоны не выплачиваются
        else:
            state = "overdue" if p["end"] < today else "due" if p["end"] == today else "planned"
        row = {**p, "per_cfa": price * bp * p["days"] / 3650000, "total": total, "state": state,
               "paid_at": rec["paid_at"] if rec else None, "evm_tx_hash": rec["evm_tx_hash"] if rec else None,
               "is_next": False}
        if state in ("due", "overdue", "planned") and next_row is None:
            next_row = row
            row["is_next"] = True
        rows.append(row)

    rov = json.loads(issue["rov_json"])
    early_allowed = rov.get("early_redemption") == "yes"
    at_maturity = today >= issue["maturity_date"]
    redemption = {
        "date": issue["maturity_date"], "paid_at": red["paid_at"] if red else None,
        "evm_tx_hash": red["evm_tx_hash"] if red else None, "accrued_days": 0, "early": False,
        "state": "paid" if red else ("overdue" if issue["maturity_date"] < today else "due" if at_maturity else "planned"),
    }
    block = None
    if issue["status"] != "ISSUED":
        block = "Выпуск не находится в обращении."
    elif at_maturity:
        if next_row is not None:
            block = "Сначала выплачиваются все купоны."
    elif not early_allowed:
        block = (f"Погашение — {issue['maturity_date'][8:10]}.{issue['maturity_date'][5:7]}.{issue['maturity_date'][:4]}, "
                 "его выполнит сторож. Досрочное погашение Решением о выпуске не предусмотрено.")
    elif next_row is not None and next_row["state"] in ("due", "overdue"):
        block = "Сначала выплачивается наступивший купон."
    else:
        # Досрочное погашение: номинал + купон, накопленный в текущем периоде; оставшиеся купоны отменяются
        redemption["early"] = True
        redemption["accrued_days"] = (today_msk() - date.fromisoformat(next_row["start"])).days if next_row else 0
    redemption["block"] = block
    redemption["total"] = red["amount_total"] if red else sum(
        h["qty"] * price + coupon_amount(h["qty"], price, bp, redemption["accrued_days"]) for h in holders)
    paid_coupons = sum(r["total"] for r in rows if r["state"] == "paid")
    return {"coupons": rows, "redemption": redemption, "next": next_row,
            "next_no": next_row["no"] if next_row else None, "placed": placed, "holders": len(holders),
            "paid_coupons": paid_coupons, "early_allowed": early_allowed}


def _issue_for_payment(conn, issue_id: int, issuer_id: Optional[int]):
    issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
    if not issue or (issuer_id is not None and issue["issuer_id"] != issuer_id):
        raise ValueError("Выплаты по выпуску проводит только его эмитент.")
    if issue["status"] != "ISSUED":
        raise ValueError("Выплаты возможны только по размещённому выпуску, который ещё не погашен.")
    return issue


def issuer_free_rub(conn, issuer_id: int) -> int:
    return conn.execute("SELECT balance_rub FROM wallets WHERE user_id = ?", (issuer_id,)).fetchone()["balance_rub"]


def _need_money(conn, issuer_id: int, total: int, what: str) -> None:
    bal = issuer_free_rub(conn, issuer_id)
    if bal < total:
        raise ValueError(f"Недостаточно средств на кошельке эмитента для {what}: нужно {_fmt(total)} ₽, "
                         f"свободно {_fmt(bal)} ₽. Пополните кошелёк на {_fmt(total - bal)} ₽.")


def _ddmmyyyy(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def pay_coupon(conn: sqlite3.Connection, issue_id: int, issuer_id: Optional[int], chain=None,
               by: str = "issuer") -> Dict[str, Any]:
    """
    Выплата наступившего купона: деньги с кошелька эмитента — владельцам ЦФА, в базе и в контракте.
    by='keeper' — выплату запустил сторож (issuer_id=None), 'issuer' — эмитент кнопкой.
    """
    with conn:
        issue = _issue_for_payment(conn, issue_id, issuer_id)
        plan = payment_plan(conn, issue)
        period = plan["next"]
        if period is None:
            raise ValueError("Все купоны по этому выпуску уже выплачены.")
        if period["state"] == "planned":
            raise ValueError(f"Купон № {period['no']} выплачивается {_ddmmyyyy(period['end'])} — раньше нельзя.")
        bp, price = rate_bp(issue), issue["price_per_cfa"]
        holders = _holders(conn, issue_id)
        parts = [(h, coupon_amount(h["qty"], price, bp, period["days"])) for h in holders]
        total = sum(a for _, a in parts)
        _need_money(conn, issue["issuer_id"], total, f"купона № {period['no']}")

        cur = conn.execute(
            """INSERT INTO cfa_payments (issue_id, kind, period_no, period_start, period_end, days, pay_date, rate_bp,
                                         amount_total, holders, early)
               VALUES (?, 'COUPON', ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (issue_id, period["no"], period["start"], period["end"], period["days"], period["end"], bp, total,
             len(holders)))
        payment_id = cur.lastrowid
        conn.execute("UPDATE wallets SET balance_rub = balance_rub - ? WHERE user_id = ?", (total, issue["issuer_id"]))
        for h, amount in parts:
            conn.execute("UPDATE wallets SET balance_rub = balance_rub + ? WHERE user_id = ?", (amount, h["user_id"]))
            conn.execute("INSERT INTO cfa_payouts (payment_id, user_id, qty, amount_rub) VALUES (?, ?, ?, ?)",
                         (payment_id, h["user_id"], h["qty"], amount))

        # Блокчейн — последним: контракт сам проверит дату, порядок и деньги; если откажет — откатится и всё выше
        evm = _chain_tx(chain, conn, "payCoupon", issue_id)
        conn.execute("UPDATE cfa_payments SET evm_tx_hash = ? WHERE id = ?", (evm, payment_id))
        who = "автоматически (сторож)" if by == "keeper" else "эмитентом"
        label = f"купон № {period['no']} по «{issue['title']}» ({period['days']} дн., {issue['coupon_rate']:g} % годовых)"
        tx_hash = append_ledger_block(conn, issue["issuer_id"], "COUPON_PAY", total,
                                      f"Выплачен {who} {label}: {_fmt(len(holders))} владельцам, {_fmt(total)} ₽", evm)
        for h, amount in parts:
            append_ledger_block(conn, h["user_id"], "COUPON_IN", amount,
                                f"Получен {label} за {_fmt(h['qty'])} шт.: {_fmt(amount)} ₽", evm)
    return {"period": period["no"], "total_rub": total, "holders": len(holders), "by": by,
            "tx_hash": tx_hash, "evm_tx_hash": evm}


def redeem_issue(conn: sqlite3.Connection, issue_id: int, issuer_id: Optional[int], chain=None,
                 by: str = "issuer") -> Dict[str, Any]:
    """
    Погашение: номинал владельцам с кошелька эмитента, ЦФА списываются, выпуск — «Погашен».
    В дату погашения его выполняет сторож. Досрочно (если разрешено РоВ) — эмитент: тогда владельцы получают
    ещё и купон, накопленный в текущем периоде, а оставшиеся купоны отменяются.
    """
    with conn:
        issue = _issue_for_payment(conn, issue_id, issuer_id)
        plan = payment_plan(conn, issue)
        red = plan["redemption"]
        if red["block"]:
            raise ValueError(red["block"])
        holders = _holders(conn, issue_id)
        price, bp, accrued = issue["price_per_cfa"], rate_bp(issue), red["accrued_days"]
        parts = [(h, h["qty"] * price + coupon_amount(h["qty"], price, bp, accrued)) for h in holders]
        total = sum(a for _, a in parts)
        _need_money(conn, issue["issuer_id"], total, "погашения")

        cur = conn.execute(
            """INSERT INTO cfa_payments (issue_id, kind, period_no, period_start, days, pay_date, rate_bp,
                                         amount_total, holders, early)
               VALUES (?, 'REDEMPTION', 0, ?, ?, ?, ?, ?, ?, ?)""",
            (issue_id, plan["next"]["start"] if red["early"] and plan["next"] else None, accrued,
             issue["maturity_date"], bp, total, len(holders), int(red["early"])))
        payment_id = cur.lastrowid
        conn.execute("UPDATE wallets SET balance_rub = balance_rub - ? WHERE user_id = ?", (total, issue["issuer_id"]))
        for h, amount in parts:
            conn.execute("UPDATE wallets SET balance_rub = balance_rub + ? WHERE user_id = ?", (amount, h["user_id"]))
            conn.execute("INSERT INTO cfa_payouts (payment_id, user_id, qty, amount_rub) VALUES (?, ?, ?, ?)",
                         (payment_id, h["user_id"], h["qty"], amount))
            conn.execute("UPDATE portfolio_assets SET qty = 0, updated_at = datetime('now', 'localtime') "
                         "WHERE user_id = ? AND issue_id = ?", (h["user_id"], issue_id))
        conn.execute("UPDATE cfa_issues SET status = 'REDEEMED', updated_at = datetime('now', 'localtime') WHERE id = ?",
                     (issue_id,))

        evm = _chain_tx(chain, conn, "redeem", issue_id, accrued)
        conn.execute("UPDATE cfa_payments SET evm_tx_hash = ? WHERE id = ?", (evm, payment_id))
        how = ("досрочно, с купоном за " + _fmt(accrued) + " дн." if red["early"] else
               "автоматически (сторож)" if by == "keeper" else "в дату погашения")
        tx_hash = append_ledger_block(conn, issue["issuer_id"], "REDEEM_PAY", total,
                                      f"Погашен выпуск «{issue['title']}» {how}: {_fmt(len(holders))} владельцам "
                                      f"{_fmt(total)} ₽", evm)
        for h, amount in parts:
            append_ledger_block(conn, h["user_id"], "REDEEM_IN", amount,
                                f"Погашение «{issue['title']}» ({how}): {_fmt(h['qty'])} шт., {_fmt(amount)} ₽", evm)
    return {"total_rub": total, "holders": len(holders), "early": red["early"], "accrued_days": accrued,
            "by": by, "tx_hash": tx_hash, "evm_tx_hash": evm}


# ==============================================================================
# 8. ЗАПУСК ФАЙЛА НАПРЯМУЮ — ПЕРЕСОЗДАТЬ ПУСТУЮ БАЗУ
# ==============================================================================
if __name__ == "__main__":
    if DB_PATH.exists():
        DB_PATH.unlink()
    connection = get_connection()
    init_db(connection)
    connection.close()
    print(f"База данных пересоздана: {DB_PATH.name} (пустая).")
    print("Создайте администратора:  flask --app main create-admin")
    print("Или заполните демо-данными: flask --app main seed-demo")
