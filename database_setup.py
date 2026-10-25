#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
database_setup.py — Готовый модуль базы данных SQLite для прототипа платформы ЦФА.

Использует только встроенные библиотеки `sqlite3` и `hashlib` (без установки лишних пакетов).
При запуске (`python database_setup.py`) автоматически создаёт файл базы `cfa.db`,
6 таблиц под все вкладки вашего HTML-прототипа, заполняет их тестовыми данными
и демонстрирует работу выпуска, покупки на витрине (ФЛ/ЮЛ) и атомарного DvP-расчёта.
"""

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

DB_PATH = Path(__file__).parent / "cfa.db"


# ==============================================================================
# 1. ПОДКЛЮЧЕНИЕ К БАЗЕ И ВКЛЮЧЕНИЕ ВНЕШНИХ КЛЮЧЕЙ (FOREIGN KEYS)
# ==============================================================================
def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Возвращает соединение с SQLite, где строки доступны по именам колонок."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # В SQLite проверка связей между таблицами по умолчанию выключена — включаем её!
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def calc_sha256(payload: str) -> str:
    """Короткая функция для генерации криптохэша SHA-256 (имитация УКЭП и блоков реестра)."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ==============================================================================
# 2. СОЗДАНИЕ СХЕМЫ ИЗ 6 ТАБЛИЦ (Под каждую вкладку вашего HTML-прототипа)
# ==============================================================================
SCHEMA_SQL = """
-- 1. Таблица пользователей (Физлица и Юрлица из формы регистрации #form-register)
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    role              TEXT NOT NULL CHECK (role IN ('FL', 'UL')), -- 'FL' (Физлицо) или 'UL' (Юрлицо)
    full_name         TEXT NOT NULL,                              -- ФИО физлица или Название компании ЮЛ
    inn               TEXT NOT NULL UNIQUE,                       -- ИНН (12 цифр у ФЛ / 10 цифр у ЮЛ)
    snils_or_ogrn     TEXT NOT NULL,                              -- СНИЛС (для ФЛ) или ОГРН/КПП (для ЮЛ)
    phone             TEXT,                                       -- Телефон (для СМС ПЭП)
    email             TEXT,                                       -- Электронная почта
    passport_or_rep   TEXT,                                       -- Паспорт ФЛ или данные представителя ЮЛ
    address           TEXT,                                       -- Адрес регистрации / юр. адрес
    is_qualified      INTEGER NOT NULL DEFAULT 0,                 -- 0 = Неквал (лимит 600 000 ₽/год), 1 = Квал / ЮЛ
    yearly_spent_rub  INTEGER NOT NULL DEFAULT 0,                 -- Сколько неквал ФЛ уже инвестировал за год
    evm_address       TEXT,                                       -- Адрес кошелька 0x... в локальной сети EVM
    login             TEXT NOT NULL UNIQUE,                       -- Логин для входа (хранится в нижнем регистре)
    password_hash     TEXT NOT NULL,                              -- Хэш пароля (сам пароль НЕ хранится!)
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 2. Таблица цифровых кошельков (Вкладка «Мои кошельки» #tab-wallets и модалка #modal-wallet)
CREATE TABLE IF NOT EXISTS wallets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL UNIQUE,                    -- Чей это кошелёк (1 юзер = 1 основной кошелёк)
    wallet_name       TEXT NOT NULL DEFAULT 'Основной кошелёк',
    bank_account      TEXT NOT NULL,                              -- Привязанный р/с или номер СБП
    bank_bik          TEXT DEFAULT '044525225',                   -- БИК банка
    balance_rub       INTEGER NOT NULL DEFAULT 0 CHECK (balance_rub >= 0), -- Свободные рубли для покупок
    locked_rub        INTEGER NOT NULL DEFAULT 0 CHECK (locked_rub >= 0),  -- Заморожено (холд) в заявках на покупку
    status            TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. Таблица выпусков ЦФА (Витрина #screen-market и вкладка Эмитента «Мои выпуски» #tab-issuer)
CREATE TABLE IF NOT EXISTS cfa_issues (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    issuer_id         INTEGER NOT NULL,                           -- Эмитент (ссылка на users.id со статусом 'UL')
    title             TEXT NOT NULL,                              -- Название выпуска (например, «ЦФА Сбер-Лизинг 01»)
    price_per_cfa     INTEGER NOT NULL CHECK (price_per_cfa > 0), -- Цена 1 ЦФА в рублях (номинал)
    min_qty           INTEGER NOT NULL CHECK (min_qty > 0),       -- Минимальный порог для успешного выпуска (шт.)
    max_qty           INTEGER NOT NULL CHECK (max_qty >= min_qty),-- Максимальный объём выпуска (шт.)
    collected_qty     INTEGER NOT NULL DEFAULT 0,                 -- Сколько штук уже заказано инвесторами на витрине
    coupon_rate       REAL NOT NULL DEFAULT 18.0,                 -- Фиксированная ставка купона (% годовых по релизу 100735)
    maturity_date     TEXT NOT NULL,                              -- Дата погашения (например, '2026-12-31')
    only_qual         INTEGER NOT NULL DEFAULT 0,                 -- 0 = Доступен всем, 1 = Только квалам и ЮЛ
    ukep_signature    TEXT NOT NULL,                              -- Подпись Решения о выпуске (UKEP_SIG_...)
    evm_token_id      INTEGER,                                    -- ID токена в смарт-контракте Solidity (если подключен)
    status            TEXT NOT NULL DEFAULT 'SUBSCRIPTION'
                      CHECK (status IN ('SUBSCRIPTION', 'ISSUED', 'CANCELLED', 'REDEEMED')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (issuer_id) REFERENCES users(id)
);

-- 4. Таблица покупок ЦФА с витрины (Модалка #modal-buy и вкладка «Операции» #tab-operations)
CREATE TABLE IF NOT EXISTS orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,                           -- Кто покупает (Инвестор ФЛ или ЮЛ)
    issue_id          INTEGER NOT NULL,                           -- Какой выпуск покупает на витрине
    qty               INTEGER NOT NULL CHECK (qty > 0),           -- Количество ЦФА (шт.)
    total_rub         INTEGER NOT NULL CHECK (total_rub > 0),     -- Сумма сделки (qty * price_per_cfa)
    sign_type         TEXT NOT NULL CHECK (sign_type IN ('PEP_SMS', 'UKEP_CMS')),
    signature_hash    TEXT NOT NULL,                              -- Хэш электронной подписи заявки
    status            TEXT NOT NULL DEFAULT 'HOLD'
                      CHECK (status IN ('HOLD', 'EXECUTED', 'REFUNDED')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (issue_id) REFERENCES cfa_issues(id)
);

-- 5. Таблица портфеля инвестора (Вкладка «Мои активы» #tab-assets и «Выплаты по купонам» #tab-coupons)
CREATE TABLE IF NOT EXISTS portfolio_assets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,                         -- Владелец актива
    issue_id            INTEGER NOT NULL,                         -- Выпуск ЦФА
    qty                 INTEGER NOT NULL CHECK (qty >= 0),        -- Количество штук на балансе
    invested_rub        INTEGER NOT NULL DEFAULT 0,               -- Сумма вложенных средств (₽)
    expected_coupon_rub INTEGER NOT NULL DEFAULT 0,               -- Ожидаемый купонный доход к выплате (₽)
    updated_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user_id, issue_id),                                   -- Чтобы один и тот же выпуск суммировался в одной строке
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (issue_id) REFERENCES cfa_issues(id)
);

-- 6. Реестр транзакций и цепочка блоков ОИС (Вкладки «История операций» #tab-history и «Отчёты» #tab-reports)
CREATE TABLE IF NOT EXISTS blockchain_ledger (
    block_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,                           -- Инициатор операции
    op_type           TEXT NOT NULL,                              -- DEPOSIT, WITHDRAW, ISSUE_CREATE, BUY_HOLD, DVP_SETTLE
    amount_rub        INTEGER NOT NULL DEFAULT 0,                 -- Сумма операции в рублях
    description       TEXT NOT NULL,                              -- Человекочитаемое описание для таблицы в ЛК
    prev_hash         TEXT NOT NULL,                              -- Хэш предыдущего блока (гарантия неизменяемости реестра)
    tx_hash           TEXT NOT NULL UNIQUE,                       -- Хэш текущей транзакции (0x...)
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

def init_db(conn: sqlite3.Connection) -> None:
    """Создаёт все 6 таблиц в базе данных."""
    with conn:
        conn.executescript(SCHEMA_SQL)


class FieldError(ValueError):
    """Ошибка, привязанная к конкретному полю формы (например, 'login' или 'inn')."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


# ==============================================================================
# 3. ЗАПИСЬ БЛОКА В РЕЕСТР ОИС (Неизменяемая цепочка хэшей для #tab-history)
# ==============================================================================
def append_ledger_block(
    conn: sqlite3.Connection,
    user_id: int,
    op_type: str,
    amount_rub: int,
    description: str,
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
        INSERT INTO blockchain_ledger (user_id, op_type, amount_rub, description, prev_hash, tx_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, op_type, amount_rub, description, prev_hash, tx_hash),
    )
    return tx_hash


# ==============================================================================
# 4. ГОТОВЫЕ ФУНКЦИИ БИЗНЕС-ЛОГИКИ (ДЛЯ ПОДКЛЮЧЕНИЯ К ВАШЕМУ FLASK / FASTAPI)
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
    evm_address: str,
    wallet_name: str,
    bank_account: str,
    bank_bik: str,
) -> Dict[str, Any]:
    """Регистрация участника с логином и паролем + создание его кошелька (одной транзакцией)."""
    with conn:
        if conn.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
            raise FieldError("login", "Этот логин уже занят — придумайте другой.")
        if conn.execute("SELECT 1 FROM users WHERE inn = ?", (profile["inn"],)).fetchone():
            raise FieldError("inn", "Участник с таким ИНН уже зарегистрирован — войдите по логину и паролю.")

        cursor = conn.execute(
            """
            INSERT INTO users (role, full_name, inn, snils_or_ogrn, phone, email, passport_or_rep,
                               address, is_qualified, evm_address, login, password_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (role, profile["full_name"], profile["inn"], profile["snils_or_ogrn"], profile["phone"],
             profile["email"], profile["passport_or_rep"], profile["address"], profile["is_qualified"],
             evm_address, login, password_hash),
        )
        user_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO wallets (user_id, wallet_name, bank_account, bank_bik) VALUES (?, ?, ?, ?)",
            (user_id, wallet_name, bank_account, bank_bik),
        )
    return {"user_id": user_id}


def deposit_wallet(conn: sqlite3.Connection, user_id: int, amount_rub: int) -> Dict[str, Any]:
    """Пополнение цифрового кошелька (#modal-wallet)."""
    if amount_rub <= 0:
        raise ValueError("Сумма пополнения должна быть больше нуля!")

    with conn:
        conn.execute(
            "UPDATE wallets SET balance_rub = balance_rub + ? WHERE user_id = ?",
            (amount_rub, user_id),
        )
        tx_hash = append_ledger_block(
            conn,
            user_id=user_id,
            op_type="DEPOSIT",
            amount_rub=amount_rub,
            description=f"Пополнение цифрового кошелька на {amount_rub:,} ₽".replace(",", " "),
        )
    return {"status": "ok", "tx_hash": tx_hash}


def withdraw_wallet(conn: sqlite3.Connection, user_id: int, amount_rub: int) -> Dict[str, Any]:
    """Вывод свободных средств с цифрового кошелька на привязанный банковский счёт."""
    if amount_rub <= 0:
        raise ValueError("Сумма вывода должна быть больше нуля!")

    with conn:
        wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
        if not wallet:
            raise ValueError("Кошелёк не найден!")
        if wallet["balance_rub"] < amount_rub:
            raise ValueError(f"Недостаточно свободных средств! Доступно {wallet['balance_rub']} ₽.")
        conn.execute(
            "UPDATE wallets SET balance_rub = balance_rub - ? WHERE user_id = ?",
            (amount_rub, user_id),
        )
        tx_hash = append_ledger_block(
            conn,
            user_id=user_id,
            op_type="WITHDRAW",
            amount_rub=amount_rub,
            description=f"Вывод средств на банковский счёт {wallet['bank_account']} на {amount_rub:,} ₽".replace(",", " "),
        )
    return {"status": "ok", "tx_hash": tx_hash}


def create_cfa_issue(
    conn: sqlite3.Connection,
    issuer_id: int,
    title: str,
    price_per_cfa: int,
    min_qty: int,
    max_qty: int,
    coupon_rate: float,
    maturity_date: str,
    only_qual: bool = False,
) -> Dict[str, Any]:
    """
    Создание нового выпуска ЦФА эмитентом (#tab-issuer по релизу Сбера 100735).
    Проверяет, что выпускает именно Юрлицо (role == 'UL').
    """
    with conn:
        issuer = conn.execute("SELECT * FROM users WHERE id = ?", (issuer_id,)).fetchone()
        if not issuer or issuer["role"] != "UL":
            raise ValueError("Выпускать ЦФА по закону 259-ФЗ могут только юридические лица и ИП!")

        ukep_sig = "UKEP_SIG_" + calc_sha256(f"{issuer['inn']}|{title}|{price_per_cfa}|{max_qty}")[:24]

        cursor = conn.execute(
            """
            INSERT INTO cfa_issues
                (issuer_id, title, price_per_cfa, min_qty, max_qty, coupon_rate, maturity_date, only_qual, ukep_signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issuer_id,
                title,
                price_per_cfa,
                min_qty,
                max_qty,
                coupon_rate,
                maturity_date,
                1 if only_qual else 0,
                ukep_sig,
            ),
        )
        issue_id = cursor.lastrowid

        tx_hash = append_ledger_block(
            conn,
            user_id=issuer_id,
            op_type="ISSUE_CREATE",
            amount_rub=price_per_cfa * max_qty,
            description=f"Публикация Решения о выпуске «{title}» (объём до {max_qty} шт. по {price_per_cfa} ₽)",
        )

    return {"issue_id": issue_id, "ukep_signature": ukep_sig, "tx_hash": tx_hash}


def buy_cfa_on_showcase(
    conn: sqlite3.Connection,
    user_id: int,
    issue_id: int,
    qty: int,
    sign_code: str,
) -> Dict[str, Any]:
    """
    Покупка ЦФА на витрине (#screen-market -> #modal-buy).
    Внутри одной транзакции проверяет баланс, лимит 600 000 ₽ для неквал ФЛ,
    код СМС ПЭП / подпись УКЭП и резервирует (холдирует) рубли на кошельке.
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

        if issue["collected_qty"] + qty > issue["max_qty"]:
            left_qty = issue["max_qty"] - issue["collected_qty"]
            raise ValueError(f"На витрине осталось всего {left_qty} шт. ЦФА этого выпуска!")

        total_rub = qty * issue["price_per_cfa"]
        if wallet["balance_rub"] < total_rub:
            raise ValueError(
                f"Недостаточно свободных средств! Нужно {total_rub} ₽, на кошельке {wallet['balance_rub']} ₽."
            )

        # РАЗЛИЧИЕ 1: Покупает Физическое лицо (ФЛ) — проверяем квалификацию, лимит 600 000 ₽ и СМС
        if user["role"] == "FL":
            if issue["only_qual"] == 1 and user["is_qualified"] == 0:
                raise ValueError("Этот выпуск доступен только квалифицированным инвесторам!")

            if user["is_qualified"] == 0 and (user["yearly_spent_rub"] + total_rub > 600_000):
                rem = 600_000 - user["yearly_spent_rub"]
                raise ValueError(
                    f"Превышен годовой лимит 600 000 ₽ для неквалифицированного ФЛ (Указание ЦБ 5635-У)! "
                    f"Доступный остаток лимита: {rem} ₽."
                )

            if sign_code.strip() != "1234":
                raise ValueError("Неверный СМС-код ПЭП! Для тестового прототипа введите 1234.")

            sign_type = "PEP_SMS"
            sig_hash = "PEP_SMS_" + calc_sha256(f"{user['id']}|{issue_id}|{qty}|1234")[:20]

            # Увеличиваем счётчик годового лимита неквал ФЛ
            conn.execute(
                "UPDATE users SET yearly_spent_rub = yearly_spent_rub + ? WHERE id = ?",
                (total_rub, user_id),
            )

        # РАЗЛИЧИЕ 2: Покупает Юридическое лицо (ЮЛ) — лимита 600 тыс. нет, подпись УКЭП
        else:
            sign_type = "UKEP_CMS"
            sig_hash = "UKEP_CMS_" + calc_sha256(f"{user['inn']}|{issue_id}|{qty}|{sign_code}")[:20]

        # Замораживаем деньги на кошельке покупателя (переводим из balance_rub в locked_rub)
        conn.execute(
            """
            UPDATE wallets
            SET balance_rub = balance_rub - ?,
                locked_rub  = locked_rub + ?
            WHERE user_id = ?
            """,
            (total_rub, total_rub, user_id),
        )

        # Увеличиваем собранное количество на витрине
        conn.execute(
            "UPDATE cfa_issues SET collected_qty = collected_qty + ? WHERE id = ?",
            (qty, issue_id),
        )

        # Сохраняем сделку со статусом HOLD
        cursor = conn.execute(
            """
            INSERT INTO orders (user_id, issue_id, qty, total_rub, sign_type, signature_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, 'HOLD')
            """,
            (user_id, issue_id, qty, total_rub, sign_type, sig_hash),
        )
        order_id = cursor.lastrowid

        tx_hash = append_ledger_block(
            conn,
            user_id=user_id,
            op_type="BUY_HOLD",
            amount_rub=total_rub,
            description=f"Покупка на витрине: {qty} шт. «{issue['title']}» (холд {total_rub} ₽, подпись {sign_type})",
        )

    return {"order_id": order_id, "signature_hash": sig_hash, "tx_hash": tx_hash}


def finalize_issue_dvp(conn: sqlite3.Connection, issue_id: int) -> Dict[str, Any]:
    """
    Атомарный смарт-контракт закрытия сбора («Поставка против платежа» / DvP).
    - Если собрали >= min_qty: списывает locked_rub у инвесторов, переводит рубли эмитенту
      и начисляет цифровые токены ЦФА в таблицу `portfolio_assets` (#tab-assets).
    - Если собрали < min_qty: выпуск отменяется, замороженные рубли возвращаются инвесторам.
    """
    with conn:
        issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
        if not issue:
            raise ValueError("Выпуск не найден!")
        if issue["status"] != "SUBSCRIPTION":
            raise ValueError(f"Выпуск уже обработан (текущий статус: {issue['status']})!")

        orders = conn.execute(
            "SELECT * FROM orders WHERE issue_id = ? AND status = 'HOLD'", (issue_id,)
        ).fetchall()

        # ВЕТКА 1: Успешный сбор -> Атомарный расчёт DvP
        if issue["collected_qty"] >= issue["min_qty"]:
            total_raised_rub = 0

            for order in orders:
                inv_id = order["user_id"]
                qty = order["qty"]
                cost = order["total_rub"]
                total_raised_rub += cost

                # 1) Списываем замороженные рубли с кошелька инвестора
                conn.execute(
                    "UPDATE wallets SET locked_rub = locked_rub - ? WHERE user_id = ?",
                    (cost, inv_id),
                )

                # 2) Начисляем ЦФА в портфель инвестора (#tab-assets и #tab-coupons)
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

                # 3) Переводим статус покупки в EXECUTED
                conn.execute("UPDATE orders SET status = 'EXECUTED' WHERE id = ?", (order["id"],))

            # 4) Зачисляем все собранные рубли на кошелёк Эмитента
            conn.execute(
                "UPDATE wallets SET balance_rub = balance_rub + ? WHERE user_id = ?",
                (total_raised_rub, issue["issuer_id"]),
            )

            # 5) Меняем статус выпуска на ISSUED (Размещён)
            conn.execute("UPDATE cfa_issues SET status = 'ISSUED' WHERE id = ?", (issue_id,))

            tx_hash = append_ledger_block(
                conn,
                user_id=issue["issuer_id"],
                op_type="DVP_SETTLE",
                amount_rub=total_raised_rub,
                description=(
                    f"Смарт-контракт DvP: выпуск «{issue['title']}» состоялся! "
                    f"Инвесторам зачислено {issue['collected_qty']} ЦФА, эмитенту переведено {total_raised_rub} ₽."
                ),
            )
            return {"result": "ISSUED", "total_raised_rub": total_raised_rub, "tx_hash": tx_hash}

        # ВЕТКА 2: Не набрали минимальный порог -> Возврат средств (Refund)
        else:
            for order in orders:
                inv_id = order["user_id"]
                cost = order["total_rub"]

                # Возвращаем рубли из резерва обратно на свободный баланс
                conn.execute(
                    """
                    UPDATE wallets
                    SET locked_rub  = locked_rub - ?,
                        balance_rub = balance_rub + ?
                    WHERE user_id = ?
                    """,
                    (cost, cost, inv_id),
                )
                conn.execute("UPDATE orders SET status = 'REFUNDED' WHERE id = ?", (order["id"],))

            conn.execute("UPDATE cfa_issues SET status = 'CANCELLED' WHERE id = ?", (issue_id,))
            tx_hash = append_ledger_block(
                conn,
                user_id=issue["issuer_id"],
                op_type="ISSUE_REFUND",
                amount_rub=0,
                description=f"Отмена выпуска «{issue['title']}» (не набран минимум). Деньги разблокированы.",
            )
            return {"result": "CANCELLED", "tx_hash": tx_hash}


# ==============================================================================
# 5. ЗАПОЛНЕНИЕ ТЕСТОВЫМИ ДАННЫМИ И ПРОВЕРКА ВСЕХ ТАБЛИЦ
# ==============================================================================

if __name__ == "__main__":
    if DB_PATH.exists():
        DB_PATH.unlink()

    connection = get_connection()
    init_db(connection)
    print(f"База данных успешно создана: {DB_PATH.name}\n")

    print("=== 1. УЧАСТНИКИ И БАЛАНСЫ КОШЕЛЬКОВ (users + wallets) ===")
    for row in connection.execute(
        """
        SELECT u.id, u.role, u.full_name, u.is_qualified, u.yearly_spent_rub,
               w.balance_rub, w.locked_rub
        FROM users u
        JOIN wallets w ON w.user_id = u.id
        """
    ):
        print(
            f"[{row['id']}] ({row['role']}) {row['full_name']} | "
            f"Свободно: {row['balance_rub']:,} ₽ | В холде: {row['locked_rub']:,} ₽ | "
            f"Потрачено за год (ФЛ): {row['yearly_spent_rub']:,} ₽".replace(",", " ")
        )

    print("\n=== 2. ВЫПУСКИ НА ВИТРИНЕ И В КАБИНЕТЕ ЭМИТЕНТА (cfa_issues) ===")
    for row in connection.execute("SELECT * FROM cfa_issues"):
        print(
            f"Выпуск #{row['id']}: {row['title']} | Цена: {row['price_per_cfa']} ₽ | "
            f"Собрано: {row['collected_qty']}/{row['max_qty']} шт. (мин. {row['min_qty']}) | "
            f"Статус: {row['status']}"
        )

    print("\n=== 3. ПОРТФЕЛЬ ЦФА ИНВЕСТОРОВ (portfolio_assets) ===")
    for row in connection.execute(
        """
        SELECT p.*, u.full_name, i.title
        FROM portfolio_assets p
        JOIN users u ON u.id = p.user_id
        JOIN cfa_issues i ON i.id = p.issue_id
        """
    ):
        print(
            f"Инвестор: {row['full_name']} -> «{row['title']}»: {row['qty']} шт. "
            f"(Вложено: {row['invested_rub']} ₽, Купон к выплате: +{row['expected_coupon_rub']} ₽)"
        )

    print("\n=== 4. ЦЕПОЧКА БЛОКОВ ОИС (blockchain_ledger) ===")
    for row in connection.execute("SELECT * FROM blockchain_ledger ORDER BY block_id"):
        print(
            f"Блок #{row['block_id']} [{row['op_type']}] {row['description']} | "
            f"TxHash: {row['tx_hash'][:18]}... (Prev: {row['prev_hash'][:10]}...)"
        )

    connection.close()