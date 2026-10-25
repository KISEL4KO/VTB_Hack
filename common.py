#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common.py — общее для main.py и admin.py: соединение с БД на время запроса, текущий пользователь,
блокчейн, декораторы доступа, словари статусов, фильтры шаблонов и сверка учёта с блокчейном.
"""

from __future__ import annotations

import sqlite3
from functools import wraps

from flask import abort, g, jsonify, redirect, request, session, url_for

import database_setup as db
from blockchain import Chain, ISSUE_STATUS as CHAIN_ISSUE_STATUS

# ------------------------------------------------------------------------------
# Блокчейн — один экземпляр на приложение (создаётся в main.py при запуске)
# ------------------------------------------------------------------------------
CHAIN: Chain | None = None


def init_chain() -> Chain:
    global CHAIN
    CHAIN = Chain(db.get_connection)
    return CHAIN


def chain() -> Chain | None:
    return CHAIN


# ------------------------------------------------------------------------------
# БД на время запроса и текущий пользователь
# ------------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = db.get_connection()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def current_user():
    if "me" not in g:
        uid = session.get("user_id")
        g.me = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone() if uid else None
    return g.me


def json_error(message: str, code: int = 400, errors: dict | None = None):
    body = {"status": "error", "message": message}
    if errors is not None:
        body["errors"] = errors
    return jsonify(body), code


# ------------------------------------------------------------------------------
# Декораторы доступа
# ------------------------------------------------------------------------------
def api_login_required(fn):
    """API: нужен вход."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return json_error("Необходимо войти на платформу.", 401)
        return fn(*args, **kwargs)
    return wrapper


def api_approved_required(fn):
    """API: нужен вход и допуск администратора (статус APPROVED)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        me = current_user()
        if not me:
            return json_error("Необходимо войти на платформу.", 401)
        if me["role"] == "ADMIN":
            return json_error("Администратор не совершает операций с активами.", 403)
        if me["status"] != "APPROVED":
            return json_error("Операции доступны после проверки анкеты администратором.", 403)
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    """Страницы и API админ-панели: только роль ADMIN."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        me = current_user()
        is_api = request.path.startswith("/api/")
        if not me:
            return json_error("Необходимо войти.", 401) if is_api else redirect(url_for("login_page", next=request.path))
        if me["role"] != "ADMIN":
            return json_error("Только для администраторов.", 403) if is_api else abort(403)
        return fn(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------------------
# Словари статусов (текст, цвет бейджа)
# ------------------------------------------------------------------------------
ISSUE_STATUS = {
    "REVIEW": ("На проверке", "amber"),
    "REJECTED": ("На доработке", "red"),
    "SUBSCRIPTION": ("Сбор заявок", "green"),
    "ISSUED": ("Размещён", "blue"),
    "CANCELLED": ("Отменён", "amber"),
    "REDEEMED": ("Погашен", "blue"),
}
ORDER_STATUS = {
    "HOLD": ("Заморожено до закрытия сбора", "amber"),
    "EXECUTED": ("Исполнена (DvP)", "green"),
    "REFUNDED": ("Возврат средств", "amber"),
}
USER_STATUS = {
    "PENDING": ("На проверке", "amber"),
    "APPROVED": ("Допущен", "green"),
    "REJECTED": ("Отказано", "red"),
    "BLOCKED": ("Заблокирован", "red"),
}
OP_TYPES = {
    "DEPOSIT": "Пополнение кошелька",
    "WITHDRAW": "Вывод средств",
    "ISSUE_SUBMIT": "РоВ направлено на проверку",
    "ISSUE_REJECT": "РоВ возвращено на доработку",
    "ISSUE_CREATE": "Публикация выпуска ЦФА",
    "BUY_HOLD": "Покупка ЦФА (холд)",
    "DVP_SETTLE": "Расчёт DvP",
    "ISSUE_REFUND": "Отмена выпуска / возврат",
    "KYC_SUBMIT": "Анкета на проверке",
    "KYC_APPROVE": "Допуск подтверждён",
    "KYC_REJECT": "Отказ в допуске",
    "KYC_BLOCK": "Блокировка доступа",
}
ADMIN_ACTIONS = {
    "USER_APPROVE": "Допуск участника подтверждён",
    "USER_REJECT": "Отказ в допуске",
    "USER_BLOCK": "Участник заблокирован",
    "ISSUE_APPROVE": "Выпуск одобрен и опубликован",
    "ISSUE_REJECT": "Выпуск возвращён на доработку",
}
CHAIN_METHODS = {
    "deploy": "Развёртывание контракта",
    "setParticipant": "Белый список (KYC)",
    "depositRub": "Зачисление цифровых рублей",
    "withdrawRub": "Вывод цифровых рублей",
    "registerIssue": "Регистрация выпуска",
    "subscribe": "Заявка (холд)",
    "finalize": "Закрытие сбора (DvP / возврат)",
}


# ------------------------------------------------------------------------------
# Фильтры шаблонов
# ------------------------------------------------------------------------------
def register_filters(app):
    @app.template_filter("rub")
    def fmt_rub(value):
        try:
            return f"{int(value):,}".replace(",", " ") + " ₽"
        except (TypeError, ValueError):
            return "—"

    @app.template_filter("rub_short")
    def fmt_rub_short(value):
        value = int(value or 0)
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}".rstrip("0").rstrip(".") + " млрд ₽"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + " млн ₽"
        return fmt_rub(value)

    @app.template_filter("num")
    def fmt_num(value):
        return f"{int(value or 0):,}".replace(",", " ")

    @app.template_filter("ddmm")
    def fmt_date(value):
        s = str(value or "")
        if len(s) >= 10 and s[4] == "-":
            return f"{s[8:10]}.{s[5:7]}.{s[:4]}" + s[10:16]
        return s or "—"

    @app.template_filter("short_hash")
    def short_hash(value, n=10):
        s = str(value or "")
        return s if len(s) <= 2 * n + 2 else f"{s[:n + 2]}…{s[-6:]}"


# ------------------------------------------------------------------------------
# Сверка учёта в SQLite с состоянием смарт-контракта
# ------------------------------------------------------------------------------
def reconciliation(conn: sqlite3.Connection) -> list[dict]:
    """Построчное сравнение: рубли участников, количество ЦФА у владельцев, собранный объём выпусков."""
    ch = chain()
    if not ch or not ch.enabled:
        return []
    rows: list[dict] = []
    fmt = lambda v: f"{int(v):,}".replace(",", " ")  # noqa: E731

    users = conn.execute(
        """SELECT u.*, w.balance_rub, w.locked_rub FROM users u JOIN wallets w ON w.user_id = u.id
           WHERE u.status IN ('APPROVED', 'BLOCKED') ORDER BY u.id""").fetchall()
    for u in users:
        addr = ch.address_of(u["evm_address"])
        free, locked = ch.call("rubBalance", addr), ch.call("rubLocked", addr)
        who = f"#{u['id']} {u['full_name']}"
        rows.append({"who": who, "what": "Свободные рубли", "db": fmt(u["balance_rub"]), "chain": fmt(free),
                     "match": free == u["balance_rub"]})
        rows.append({"who": who, "what": "Рубли в холде", "db": fmt(u["locked_rub"]), "chain": fmt(locked),
                     "match": locked == u["locked_rub"]})
        rows.append({"who": who, "what": "В белом списке (KYC)",
                     "db": "да" if u["status"] == "APPROVED" else "нет",
                     "chain": "да" if ch.call("verified", addr) else "нет",
                     "match": ch.call("verified", addr) == (u["status"] == "APPROVED")})

    issues = conn.execute(
        "SELECT * FROM cfa_issues WHERE status IN ('SUBSCRIPTION', 'ISSUED', 'CANCELLED') ORDER BY id").fetchall()
    for i in issues:
        on_chain = ch.call("issues", i["id"])
        status_map = {"SUBSCRIPTION": 1, "ISSUED": 2, "CANCELLED": 3}
        rows.append({"who": f"Выпуск #{i['id']} {i['title']}", "what": "Заявлено ЦФА",
                     "db": fmt(i["collected_qty"]), "chain": fmt(on_chain[4]), "match": on_chain[4] == i["collected_qty"]})
        rows.append({"who": f"Выпуск #{i['id']} {i['title']}", "what": "Статус выпуска",
                     "db": ISSUE_STATUS[i["status"]][0], "chain": CHAIN_ISSUE_STATUS.get(on_chain[7], "?"),
                     "match": on_chain[7] == status_map[i["status"]]})
        if i["status"] == "ISSUED":
            for h in conn.execute(
                    """SELECT p.qty, u.full_name, u.evm_address FROM portfolio_assets p JOIN users u ON u.id = p.user_id
                       WHERE p.issue_id = ?""", (i["id"],)):
                bal = ch.call("balanceOf", i["id"], ch.address_of(h["evm_address"]))
                rows.append({"who": f"Выпуск #{i['id']} · {h['full_name']}", "what": "ЦФА на счёте владельца",
                             "db": fmt(h["qty"]), "chain": fmt(bal), "match": bal == h["qty"]})
    return rows
