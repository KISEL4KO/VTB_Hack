#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common.py — общее для main.py и admin.py: соединение с БД на время запроса, текущий пользователь,
блокчейн, декораторы доступа, словари статусов, фильтры шаблонов и сверка учёта с блокчейном.
"""

from __future__ import annotations

import json
import sqlite3
from functools import wraps

from flask import Response, abort, g, jsonify, redirect, request, session, url_for

import database_setup as db
from validators import ISSUER_ROLES, PERSON_ROLES, ROLE_FULL, ROLE_SHORT, SIGN_PEP_ROLES
from blockchain import Chain, ISSUE_STATUS as CHAIN_ISSUE_STATUS

# ------------------------------------------------------------------------------
# PDF-документы: если reportlab не установлен, платформа работает, а PDF недоступны
# ------------------------------------------------------------------------------
try:
    import reports as REPORTS
    PDF_ERROR = ""
except ImportError as _e:  # pragma: no cover
    REPORTS = None
    PDF_ERROR = f"PDF-документы недоступны: не установлена библиотека reportlab ({_e}). Выполните: pip install reportlab"


def pdf_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if REPORTS is None:
            return Response(PDF_ERROR, status=503, mimetype="text/plain; charset=utf-8")
        return fn(*args, **kwargs)
    return wrapper


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
    "COUPON_PAY": "Выплата купона владельцам",
    "COUPON_IN": "Получен купон",
    "REDEEM_PAY": "Погашение выпуска",
    "REDEEM_IN": "Погашение ЦФА",
    "PAY_FAIL": "Выплата не прошла — нет средств",
}
ADMIN_ACTIONS = {
    "USER_APPROVE": "Допуск участника подтверждён",
    "USER_REJECT": "Отказ в допуске",
    "USER_BLOCK": "Участник заблокирован",
    "ISSUE_APPROVE": "Выпуск одобрен и опубликован",
    "ISSUE_REJECT": "Выпуск возвращён на доработку",
    "BENCHMARKS_UPDATE": "Обновлены рыночные ориентиры",
}
CHAIN_METHODS = {
    "deploy": "Развёртывание контракта",
    "setParticipant": "Белый список (KYC)",
    "depositRub": "Зачисление цифровых рублей",
    "withdrawRub": "Вывод цифровых рублей",
    "registerIssue": "Регистрация выпуска",
    "subscribe": "Заявка (холд)",
    "finalize": "Закрытие сбора (DvP / возврат)",
    "setSchedule": "График выплат в контракт",
    "payCoupon": "Выплата купона",
    "redeem": "Погашение выпуска",
}


# ------------------------------------------------------------------------------
# Фильтры шаблонов
# ------------------------------------------------------------------------------
def register_filters(app):
    app.jinja_env.globals.update(issuer_roles=ISSUER_ROLES, person_roles=PERSON_ROLES, sign_pep_roles=SIGN_PEP_ROLES)

    @app.template_filter("role_short")
    def role_short(value):
        return ROLE_SHORT.get(value, value)

    @app.template_filter("role_full")
    def role_full(value):
        return ROLE_FULL.get(value, value)

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
        "SELECT * FROM cfa_issues WHERE status IN ('SUBSCRIPTION', 'ISSUED', 'CANCELLED', 'REDEEMED') ORDER BY id").fetchall()
    for i in issues:
        on_chain = ch.call("issues", i["id"])
        status_map = {"SUBSCRIPTION": 1, "ISSUED": 2, "CANCELLED": 3, "REDEEMED": 4}
        rows.append({"who": f"Выпуск #{i['id']} {i['title']}", "what": "Заявлено ЦФА",
                     "db": fmt(i["collected_qty"]), "chain": fmt(on_chain[4]), "match": on_chain[4] == i["collected_qty"]})
        rows.append({"who": f"Выпуск #{i['id']} {i['title']}", "what": "Статус выпуска",
                     "db": ISSUE_STATUS[i["status"]][0], "chain": CHAIN_ISSUE_STATUS.get(on_chain[7], "?"),
                     "match": on_chain[7] == status_map[i["status"]]})
        if i["status"] in ("ISSUED", "REDEEMED"):
            paid = conn.execute("SELECT COUNT(*) FROM cfa_payments WHERE issue_id = ? AND kind = 'COUPON'",
                                (i["id"],)).fetchone()[0]
            on_chain_paid = ch.call("couponsPaid", i["id"])
            rows.append({"who": f"Выпуск #{i['id']} {i['title']}", "what": "Выплачено купонов",
                         "db": fmt(paid), "chain": fmt(on_chain_paid), "match": paid == on_chain_paid})
            for h in conn.execute(
                    """SELECT p.qty, u.full_name, u.evm_address FROM portfolio_assets p JOIN users u ON u.id = p.user_id
                       WHERE p.issue_id = ?""", (i["id"],)):
                bal = ch.call("balanceOf", i["id"], ch.address_of(h["evm_address"]))
                rows.append({"who": f"Выпуск #{i['id']} · {h['full_name']}", "what": "ЦФА на счёте владельца",
                             "db": fmt(h["qty"]), "chain": fmt(bal), "match": bal == h["qty"]})
    return rows


# ------------------------------------------------------------------------------
# Рыночные ориентиры для аналитики выпусков (админ меняет их в /admin/benchmarks)
# ------------------------------------------------------------------------------
DEFAULT_BENCHMARKS = {
    "as_of": "2026-10-23",
    "key_rate": 14.0,         # ключевая ставка ЦБ — решение Совета директоров 23.10.2026
    "deposit_rate": 13.06,    # средняя максимальная ставка по вкладам ТОП-10 банков, II декада октября 2026
    "inflation": 6.35,        # годовая инфляция, сентябрь 2026 (Росстат)
    "ndfl": 13.0,             # НДФЛ с купонного дохода (базовая ставка)
    # Доходность ОФЗ-ПД по срокам, лет → % (MOEX ISS, 15.10.2026)
    "ofz_curve": [[1, 13.27], [1.3, 14.03], [2.4, 14.84], [2.9, 14.99], [3.9, 15.65], [5, 15.68], [10, 16.22]],
    "sources": "Банк России — ключевая ставка (23.10.2026) и средняя максимальная ставка по вкладам (II декада "
               "октября 2026); Росстат — годовая инфляция (сентябрь 2026); MOEX ISS — доходности ОФЗ-ПД (15.10.2026)",
}


def get_benchmarks(conn) -> dict:
    row = conn.execute("SELECT value FROM platform_settings WHERE key = 'benchmarks'").fetchone()
    data = dict(DEFAULT_BENCHMARKS)
    if row:
        data.update(json.loads(row["value"]))
    return data


def save_benchmarks(conn, admin_id: int, data: dict) -> None:
    import database_setup as db_
    with conn:
        conn.execute(
            """INSERT INTO platform_settings (key, value, updated_by, updated_at) VALUES ('benchmarks', ?, ?, datetime('now', 'localtime'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by,
                                              updated_at = excluded.updated_at""",
            (json.dumps(data, ensure_ascii=False), admin_id))
        db_.log_admin_action(conn, admin_id, "BENCHMARKS_UPDATE", "settings", 0,
                             f"ключевая {data['key_rate']} %, вклады {data['deposit_rate']} %, инфляция {data['inflation']} %, "
                             f"на {data['as_of']}")


def issuer_track(conn, issuer_id: int, exclude_issue: int | None = None) -> dict:
    """История эмитента на платформе: сколько выпусков состоялось и сколько отменено."""
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM cfa_issues WHERE issuer_id = ? AND id != ? GROUP BY status",
                        (issuer_id, exclude_issue or 0)).fetchall()
    by = {r["status"]: r["n"] for r in rows}
    return {"issued": by.get("ISSUED", 0) + by.get("REDEEMED", 0), "cancelled": by.get("CANCELLED", 0)}


def issue_payload(conn, issue) -> dict:
    """Параметры выпуска для analytics.js (в том же виде, что собирает конструктор)."""
    rov = json.loads(issue["rov_json"] or "{}")
    stats = conn.execute(
        """SELECT COUNT(DISTINCT user_id) AS investors, COUNT(*) AS orders, COALESCE(AVG(total_rub), 0) AS avg_order
           FROM orders WHERE issue_id = ? AND status != 'REFUNDED'""", (issue["id"],)).fetchone()
    track = issuer_track(conn, issue["issuer_id"], exclude_issue=issue["id"])
    return {
        "id": issue["id"], "title": issue["title"], "status": issue["status"],
        "price": issue["price_per_cfa"], "rate": issue["coupon_rate"], "period": rov.get("coupon_period", "maturity"),
        "placementStart": issue["placement_start"], "placementEnd": issue["placement_end"], "maturity": issue["maturity_date"],
        "collateral": rov.get("collateral") == "yes", "buyback": rov.get("buyback") == "yes",
        "minQty": issue["min_qty"], "maxQty": issue["max_qty"], "minOrder": issue["min_order_qty"],
        "maxOrder": issue["max_order_qty"], "collected": issue["collected_qty"],
        "showDemand": issue["status"] in ("SUBSCRIPTION", "ISSUED", "CANCELLED"),
        "issuerIssued": track["issued"], "issuerCancelled": track["cancelled"],
        "investors": stats["investors"], "orders": stats["orders"], "avgOrder": round(stats["avg_order"]),
    }
