#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — веб-сервер платформы ЦФА на Flask.

Здесь: витрина, вход и регистрация, личный кабинет, кабинет эмитента с формой «Решения о выпуске»,
JSON API, PDF-документы участника и команды командной строки (create-admin, seed-demo).
Админ-панель — в admin.py, общие помощники — в common.py.
"""

from __future__ import annotations

import csv
import io
import json
import os
import random
import secrets
import sqlite3
from datetime import date, timedelta

import click
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash  # werkzeug ставится вместе с Flask

import database_setup as db
import keeper
import validators
from blockchain import ISSUE_STATUS as CHAIN_ISSUE_STATUS
from common import REPORTS as reports
from common import (ISSUE_STATUS, OP_TYPES, ORDER_STATUS, PDF_ERROR, USER_STATUS, api_approved_required, get_benchmarks,
                    issue_payload, issuer_track, pdf_required, chain, close_db, current_user, get_db, init_chain, json_error, register_filters)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cfa-dev-secret-change-me")
app.teardown_appcontext(close_db)
register_filters(app)

# Автоматическая инициализация базы данных при старте приложения.
# init_db выполняет CREATE TABLE IF NOT EXISTS, поэтому её безопасно вызывать каждый раз:
# существующие таблицы и данные не трогаются, а пустой файл cfa.db получит все таблицы.
_is_new_db = not db.DB_PATH.exists() or db.DB_PATH.stat().st_size == 0
_conn = db.get_connection()
db.init_db(_conn)
_columns = {r["name"] for r in _conn.execute("PRAGMA table_info(users)")}
if "status" not in _columns or "doc_hash" not in {r["name"] for r in _conn.execute("PRAGMA table_info(cfa_issues)")}:
    _conn.close()
    raise SystemExit("\nБаза cfa.db создана старой версией программы (нет статусов допуска и модерации).\n"
                     "Пересоздайте её:  python database_setup.py   и при желании:  flask --app main seed-demo\n")
_users_sql = _conn.execute("SELECT sql FROM sqlite_master WHERE name = 'users'").fetchone()[0]
if "'IP'" not in _users_sql:
    _conn.close()
    raise SystemExit("\nБаза cfa.db создана до появления индивидуальных предпринимателей (роль IP).\n"
                     "Пересоздайте её:  python database_setup.py   и при желании:  flask --app main seed-demo\n")
_has_admin = _conn.execute("SELECT 1 FROM users WHERE role = 'ADMIN'").fetchone()
_conn.close()
if _is_new_db:
    print("База данных 'cfa.db' успешно создана (пустая — пользователи появятся после регистрации).")
if not _has_admin:
    print("Администратора ещё нет. Создайте его:  flask --app main create-admin   "
          "(или демо-данные: flask --app main seed-demo)")

# Блокчейн: встроенная EVM-сеть воспроизводит журнал транзакций и находит смарт-контракт
CHAIN = init_chain()
if CHAIN.enabled:
    print(f"Блокчейн: {'встроенная EVM' if CHAIN.mode == 'embedded' else 'внешний узел'}, контракт {CHAIN.address}, "
          f"воспроизведено транзакций: {CHAIN.replayed}")
else:
    print(f"Блокчейн выключен: {CHAIN.reason}")
if PDF_ERROR:
    print(PDF_ERROR)

# «Сторож» выплат: при обычных запросах — не чаще раза в KEEPER_REQUEST_INTERVAL секунд.
# Нужен хостингам без фоновых потоков и расписаний (бесплатный PythonAnywhere). 0 — выключить.
KEEPER_REQUEST_INTERVAL = float(os.environ.get("KEEPER_REQUEST_INTERVAL", "300"))
KEEPER_INTERVAL = float(os.environ.get("KEEPER_INTERVAL", "60"))   # фоновый поток при запуске python main.py
app.config.update(KEEPER_INTERVAL=KEEPER_INTERVAL, KEEPER_REQUEST_INTERVAL=KEEPER_REQUEST_INTERVAL)


@app.before_request
def keeper_on_request():
    if request.endpoint != "static":
        keeper.maybe_run_on_request(chain(), KEEPER_REQUEST_INTERVAL)


import admin  # noqa: E402  (регистрируем админ-панель после создания app)

app.register_blueprint(admin.bp)


@app.context_processor
def inject_globals():
    user = current_user()
    wallet = (get_db().execute("SELECT * FROM wallets WHERE user_id = ?", (user["id"],)).fetchone()
              if user and user["role"] != "ADMIN" else None)
    return {
        "me": user,
        "my_wallet": wallet,
        "issue_status": ISSUE_STATUS,
        "order_status": ORDER_STATUS,
        "user_status": USER_STATUS,
        "op_types": OP_TYPES,
        "chain_on": bool(chain() and chain().enabled),
        "today_iso": date.today().isoformat(),
    }


# ==============================================================================
# Страницы
# ==============================================================================
PUBLIC_STATUSES = ("SUBSCRIPTION", "ISSUED", "CANCELLED", "REDEEMED")


@app.route("/")
def home():
    """Главная страница: витрина опубликованных выпусков ЦФА."""
    issues = get_db().execute(
        f"""
        SELECT i.*, u.full_name AS issuer_name
        FROM cfa_issues i JOIN users u ON u.id = i.issuer_id
        WHERE i.status IN {PUBLIC_STATUSES}
        ORDER BY CASE i.status WHEN 'SUBSCRIPTION' THEN 0 ELSE 1 END, i.id DESC
        """
    ).fetchall()
    conn = get_db()
    cfa_data = {"bench": get_benchmarks(conn), "issues": {str(i["id"]): issue_payload(conn, i) for i in issues}}
    return render_template("market.html", issues=issues, cfa_data=cfa_data)


@app.route("/issues/<int:issue_id>")
def issue_page(issue_id):
    """Аналитика выпуска: доходность, сравнение с ОФЗ и вкладами, график выплат, калькулятор, спрос, оценка."""
    conn = get_db()
    issue = conn.execute("""SELECT i.*, u.full_name AS issuer_name FROM cfa_issues i JOIN users u ON u.id = i.issuer_id
                            WHERE i.id = ?""", (issue_id,)).fetchone()
    if not issue:
        abort(404)
    me = current_user()
    owner = me and (me["role"] == "ADMIN" or me["id"] == issue["issuer_id"])
    if issue["status"] not in PUBLIC_STATUSES and not owner:
        abort(404)
    return render_template("issue_page.html", issue=issue, payload=issue_payload(conn, issue),
                           bench=get_benchmarks(conn), rov=json.loads(issue["rov_json"]))


@app.route("/login")
def login_page():
    if current_user():
        return redirect(url_for("cabinet"))
    return render_template("auth.html", mode="login", next=request.args.get("next", ""))


@app.route("/register")
def register_page():
    """Та же страница, что и /login, но сразу открыта вкладка «Регистрация»."""
    if current_user():
        return redirect(url_for("cabinet"))
    return render_template("auth.html", mode="register", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/cabinet")
def cabinet():
    me = current_user()
    if me and me["role"] == "ADMIN":
        return redirect(url_for("admin.dashboard"))
    if me and me["status"] != "APPROVED":
        return redirect(url_for("cabinet_tab", tab="profile"))
    return redirect(url_for("cabinet_tab", tab="products"))


CABINET_TABS = ["wallets", "products", "history", "reports", "profile",
                "assets", "operations", "coupons", "issuer"]


@app.route("/cabinet/<tab>")
def cabinet_tab(tab):
    user = current_user()
    if not user:
        return redirect(url_for("login_page", next=request.path))
    if user["role"] == "ADMIN":
        return redirect(url_for("admin.dashboard"))
    if tab not in CABINET_TABS:
        abort(404)

    conn = get_db()
    uid = user["id"]
    ctx = {"tab": tab, "wallet": conn.execute("SELECT * FROM wallets WHERE user_id = ?", (uid,)).fetchone()}

    # Пока анкета не одобрена, доступны только «Данные клиента» и «История операций»
    if user["status"] != "APPROVED" and tab not in ("profile", "history"):
        return render_template("cabinet/pending.html", **ctx)

    if tab == "history":
        ctx["ledger"] = conn.execute(
            "SELECT * FROM blockchain_ledger WHERE user_id = ? ORDER BY block_id DESC", (uid,)).fetchall()
    elif tab == "assets":
        ctx["assets"] = conn.execute(
            """
            SELECT p.*, i.title, i.status, i.price_per_cfa, u.full_name AS issuer_name
            FROM portfolio_assets p
            JOIN cfa_issues i ON i.id = p.issue_id
            JOIN users u ON u.id = i.issuer_id
            WHERE p.user_id = ? AND p.qty > 0
            ORDER BY p.updated_at DESC
            """, (uid,)).fetchall()
    elif tab == "operations":
        ctx["orders"] = conn.execute(
            """
            SELECT o.*, i.title FROM orders o JOIN cfa_issues i ON i.id = o.issue_id
            WHERE o.user_id = ? ORDER BY o.id DESC
            """, (uid,)).fetchall()
    elif tab == "coupons":
        # Полученные выплаты (купоны и погашения) — фактически перечисленные эмитентом
        ctx["received"] = conn.execute(
            """
            SELECT po.amount_rub, po.qty, pa.kind, pa.period_no, pa.period_start, pa.period_end, pa.days, pa.paid_at,
                   pa.early, pa.evm_tx_hash, i.id AS issue_id, i.title, i.coupon_rate
            FROM cfa_payouts po JOIN cfa_payments pa ON pa.id = po.payment_id JOIN cfa_issues i ON i.id = pa.issue_id
            WHERE po.user_id = ? ORDER BY pa.paid_at DESC, pa.id DESC
            """, (uid,)).fetchall()
        # Ожидаемые: следующие периоды по выпускам в портфеле, по текущему количеству ЦФА
        upcoming = []
        for h in conn.execute(
                """SELECT p.qty, i.* FROM portfolio_assets p JOIN cfa_issues i ON i.id = p.issue_id
                   WHERE p.user_id = ? AND p.qty > 0 AND i.status = 'ISSUED'""", (uid,)):
            plan = db.payment_plan(conn, h)
            bp = db.rate_bp(h)
            for c in plan["coupons"]:
                if c["state"] != "paid":
                    upcoming.append({"issue_id": h["id"], "title": h["title"], "what": f"Купон № {c['no']}",
                                     "date": c["end"], "amount": db.coupon_amount(h["qty"], h["price_per_cfa"], bp, c["days"]),
                                     "state": c["state"]})
            upcoming.append({"issue_id": h["id"], "title": h["title"], "what": "Погашение номинала",
                             "date": h["maturity_date"], "amount": h["qty"] * h["price_per_cfa"],
                             "state": plan["redemption"]["state"]})
        ctx["upcoming"] = sorted(upcoming, key=lambda r: r["date"])
    elif tab == "issuer":
        ctx["my_issues"] = conn.execute(
            "SELECT * FROM cfa_issues WHERE issuer_id = ? ORDER BY id DESC", (uid,)).fetchall()
    elif tab == "reports":
        ctx["today"] = date.today().strftime("%d.%m.%Y")
        ctx["my_issues"] = conn.execute(
            "SELECT * FROM cfa_issues WHERE issuer_id = ? AND status IN ('SUBSCRIPTION', 'ISSUED', 'CANCELLED') "
            "ORDER BY id DESC", (uid,)).fetchall()
        ctx["orders"] = conn.execute(
            "SELECT o.*, i.title FROM orders o JOIN cfa_issues i ON i.id = o.issue_id WHERE o.user_id = ? "
            "ORDER BY o.id DESC", (uid,)).fetchall()
    elif tab == "profile":
        ctx["anketa"] = validators.anketa_rows(user["role"], json.loads(user["profile_json"] or "{}"))

    return render_template(f"cabinet/{tab}.html", **ctx)


# ---------- Решение о выпуске: форма эмитента ----------
def _issuer_or_redirect():
    user = current_user()
    if not user:
        return None, redirect(url_for("login_page", next=request.path))
    if user["role"] not in validators.ISSUER_ROLES or user["status"] != "APPROVED":
        return None, redirect(url_for("cabinet_tab", tab="issuer"))
    return user, None


@app.route("/cabinet/issuer/new")
def issuer_new():
    user, resp = _issuer_or_redirect()
    if resp:
        return resp
    start = date.today()
    ip = _ip_prefill(user)
    draft = {  # разумные значения по умолчанию — эмитент меняет то, что нужно
        "issuer_full_name": "", "beneficiary": "", "website": "", "ogrn_date": "",
        "title": "", "price_per_cfa": "1000", "coupon_rate": "", "coupon_period": "maturity",
        "min_qty": "", "max_qty": "", "min_order_qty": "1", "max_order_qty": "",
        "placement_start": start.isoformat(), "placement_end": (start + timedelta(days=30)).isoformat(),
        "maturity_date": (start + timedelta(days=395)).isoformat(),
        "payment_method": "wallet", "investors": "all", "buyback": "no", "buyback_terms": "",
        "collateral": "no", "collateral_desc": "", "early_redemption": "no",
        "risks": ["market", "liquidity", "credit", "operational", "legal"], "risks_extra": "",
        "signer_name": "", "signer_position": "Генеральный директор", **validators.ROV_DEFAULTS,
        **ip,
    }
    return render_template("cabinet/issuer_form.html", tab="issuer", rov=draft, issue=None, **_rov_choices(user["id"]))


@app.route("/cabinet/issuer/<int:issue_id>/edit")
def issuer_edit(issue_id):
    user, resp = _issuer_or_redirect()
    if resp:
        return resp
    issue = get_db().execute("SELECT * FROM cfa_issues WHERE id = ? AND issuer_id = ?", (issue_id, user["id"])).fetchone()
    if not issue:
        abort(404)
    if issue["status"] not in ("REVIEW", "REJECTED"):
        return redirect(url_for("cabinet_tab", tab="issuer"))
    rov = json.loads(issue["rov_json"])
    rov = {k: ("" if v is None else v) for k, v in rov.items()}
    return render_template("cabinet/issuer_form.html", tab="issuer", rov=rov, issue=issue, **_rov_choices(user["id"], issue_id))


@app.route("/cabinet/issuer/<int:issue_id>/payments")
def issuer_payments(issue_id):
    """Эмитент: график купонов и погашения, выплата с кошелька эмитента."""
    user, resp = _issuer_or_redirect()
    if resp:
        return resp
    conn = get_db()
    issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ? AND issuer_id = ?", (issue_id, user["id"])).fetchone()
    if not issue:
        abort(404)
    if issue["status"] not in ("ISSUED", "REDEEMED"):
        return redirect(url_for("cabinet_tab", tab="issuer"))
    wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user["id"],)).fetchone()
    return render_template("cabinet/issuer_payments.html", tab="issuer", issue=issue, wallet=wallet,
                           plan=db.payment_plan(conn, issue), rov=json.loads(issue["rov_json"]),
                           periods=validators.ROV_COUPON_PERIODS, today_iso=date.today().isoformat())


def _ip_prefill(user) -> dict:
    """У ИП раздел 1 Решения о выпуске заполняется из анкеты: эмитент, бенефициар и подписант — сам предприниматель."""
    if user["role"] != "IP":
        return {}
    a = json.loads(user["profile_json"] or "{}")
    fio = a.get("fl_fio", "")
    return {"issuer_full_name": f"Индивидуальный предприниматель {fio}", "ogrn_date": a.get("ip_reg_date", ""),
            "beneficiary": f"{fio} — индивидуальный предприниматель, 100 %", "signer_name": fio,
            "signer_position": "Индивидуальный предприниматель"}


def _rov_choices(issuer_id: int, issue_id: int | None = None):
    conn = get_db()
    return {"risks_all": validators.ROV_RISKS, "coupon_periods": validators.ROV_COUPON_PERIODS,
            "payments": validators.ROV_PAYMENT, "investors_all": validators.ROV_INVESTORS,
            "bench": get_benchmarks(conn), "track": issuer_track(conn, issuer_id, exclude_issue=issue_id)}


# ==============================================================================
# CSV-отчёты и PDF-документы участника
# ==============================================================================
@app.route("/reports/<kind>.csv")
def report_csv(kind):
    user = current_user()
    if not user:
        abort(401)
    conn = get_db()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    if kind == "ledger":
        w.writerow(["Дата", "Тип", "Сумма, руб.", "Описание", "TxHash (журнал)", "TxHash (EVM)"])
        for r in conn.execute("SELECT * FROM blockchain_ledger WHERE user_id = ? ORDER BY block_id", (user["id"],)):
            w.writerow([r["created_at"], OP_TYPES.get(r["op_type"], r["op_type"]), r["amount_rub"],
                        r["description"], r["tx_hash"], r["evm_tx_hash"] or ""])
    elif kind == "portfolio":
        w.writerow(["Выпуск ЦФА", "Количество", "Вложено, руб.", "Ожидаемый купон, руб."])
        for r in conn.execute(
            """SELECT i.title, p.qty, p.invested_rub, p.expected_coupon_rub
               FROM portfolio_assets p JOIN cfa_issues i ON i.id = p.issue_id
               WHERE p.user_id = ?""", (user["id"],)):
            w.writerow(list(r))
    else:
        abort(404)
    data = "﻿" + out.getvalue()  # BOM — чтобы Excel правильно открыл кириллицу
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={kind}.csv"})


def pdf_response(data: bytes, filename: str, download: bool = False) -> Response:
    disposition = "attachment" if download or request.args.get("download") else "inline"
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


def _issue_with_access(issue_id: int, public_ok: bool):
    """Выпуск и проверка доступа: эмитент выпуска, администратор, или любой — если документ публичный."""
    conn = get_db()
    issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
    if not issue:
        abort(404)
    me = current_user()
    owner = me and (me["role"] == "ADMIN" or me["id"] == issue["issuer_id"])
    if not owner and not (public_ok and issue["status"] in PUBLIC_STATUSES):
        abort(403)
    return issue


@app.route("/docs/issues/<int:issue_id>/decision.pdf")
@pdf_required
def doc_decision(issue_id):
    """Решение о выпуске — публичный документ для опубликованных выпусков."""
    issue = _issue_with_access(issue_id, public_ok=True)
    ch = chain()
    pdf = reports.decision_pdf(issue, json.loads(issue["rov_json"]), ch.address if ch and ch.enabled else None)
    return pdf_response(pdf, f"ROV_CFA-{issue_id:06d}.pdf")


@app.route("/docs/issues/<int:issue_id>/results.pdf")
@pdf_required
def doc_results(issue_id):
    """Отчёт об итогах выпуска — эмитенту и администратору."""
    issue = _issue_with_access(issue_id, public_ok=False)
    conn = get_db()
    issuer = conn.execute("SELECT * FROM users WHERE id = ?", (issue["issuer_id"],)).fetchone()
    orders = conn.execute("""SELECT o.*, u.role FROM orders o JOIN users u ON u.id = o.user_id
                             WHERE o.issue_id = ? ORDER BY o.id""", (issue_id,)).fetchall()
    info = None
    ch = chain()
    if ch and ch.enabled and issue["status"] in PUBLIC_STATUSES:
        c = ch.call("issues", issue_id)
        fin = conn.execute("""SELECT tx_hash FROM evm_transactions WHERE method = 'finalize' AND args_json = ?""",
                           (json.dumps([issue_id]),)).fetchone()
        info = {"contract": ch.address, "status": CHAIN_ISSUE_STATUS.get(c[7]),
                "supply": ch.call("totalSupply", issue_id), "tx": fin["tx_hash"] if fin else None}
    return pdf_response(reports.results_pdf(issue, issuer, orders, info), f"Itogi_CFA-{issue_id:06d}.pdf")


@app.route("/docs/issues/<int:issue_id>/registry.pdf")
@pdf_required
def doc_registry(issue_id):
    """Реестр владельцев ЦФА выпуска — эмитенту и администратору."""
    issue = _issue_with_access(issue_id, public_ok=False)
    conn = get_db()
    issuer = conn.execute("SELECT * FROM users WHERE id = ?", (issue["issuer_id"],)).fetchone()
    ch = chain()
    holders = []
    for h in conn.execute(
            """SELECT p.qty, u.full_name, u.role, u.evm_address FROM portfolio_assets p JOIN users u ON u.id = p.user_id
               WHERE p.issue_id = ? AND p.qty > 0 ORDER BY p.qty DESC""", (issue_id,)):
        holders.append({"name": h["full_name"], "type": validators.ROLE_SHORT.get(h["role"], h["role"]), "evm": h["evm_address"],
                        "qty": h["qty"], "chain_qty": ch.call("balanceOf", issue_id, ch.address_of(h["evm_address"]))
                        if ch and ch.enabled else None})
    return pdf_response(reports.registry_pdf(issue, issuer, holders), f"Reestr_CFA-{issue_id:06d}.pdf")


@app.route("/docs/statement.pdf")
@pdf_required
def doc_statement():
    """Выписка по счёту текущего участника."""
    me = current_user()
    if not me or me["role"] == "ADMIN":
        abort(403)
    conn = get_db()
    wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (me["id"],)).fetchone()
    holdings = conn.execute(
        """SELECT p.*, i.title, i.maturity_date, u.full_name AS issuer_name FROM portfolio_assets p
           JOIN cfa_issues i ON i.id = p.issue_id JOIN users u ON u.id = i.issuer_id
           WHERE p.user_id = ? AND p.qty > 0""", (me["id"],)).fetchall()
    ops = [dict(r, op_name=OP_TYPES.get(r["op_type"], r["op_type"])) for r in conn.execute(
        "SELECT * FROM blockchain_ledger WHERE user_id = ? ORDER BY block_id", (me["id"],))]
    ch = chain()
    bal = None
    if ch and ch.enabled and me["status"] in ("APPROVED", "BLOCKED"):
        addr = ch.address_of(me["evm_address"])
        bal = {"free": ch.call("rubBalance", addr), "locked": ch.call("rubLocked", addr)}
    return pdf_response(reports.statement_pdf(me, wallet, holdings, ops, bal), f"Vypiska_{date.today():%Y%m%d}.pdf")


@app.route("/docs/orders/<int:order_id>.pdf")
@pdf_required
def doc_order(order_id):
    """Подтверждение заявки — её автору и администратору."""
    me = current_user()
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order or not me or (me["role"] != "ADMIN" and me["id"] != order["user_id"]):
        abort(404)
    issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (order["issue_id"],)).fetchone()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (order["user_id"],)).fetchone()
    issuer = conn.execute("SELECT * FROM users WHERE id = ?", (issue["issuer_id"],)).fetchone()
    return pdf_response(reports.order_pdf(order, issue, user, issuer), f"Zayavka_{order_id:06d}.pdf")


# ==============================================================================
# API
# ==============================================================================
def _rand_digits(n: int) -> str:
    return "".join(random.choice("0123456789") for _ in range(n))


def _field_errors(errors: dict, message: str = "Проверьте поля, выделенные красным."):
    """Ответ 400 с ошибками по конкретным полям — app.js подсветит именно эти поля."""
    return json_error(message, 400, errors)


def _log_in(user_id: int) -> None:
    """Запоминает участника в сессии. Старую сессию сначала очищаем (защита от фиксации сессии)."""
    session.clear()
    session["user_id"] = user_id


@app.route("/api/login", methods=["POST"])
def api_login():
    """Вход по логину и паролю."""
    data = request.get_json(silent=True) or {}
    login = str(data.get("login") or "").strip().lower()
    password = str(data.get("password") or "")       # пароль НЕ обрезаем: пробелы — тоже символы

    errors = {}
    if not login:
        errors["login"] = "Введите логин."
    if not password:
        errors["password"] = "Введите пароль."
    if errors:
        return _field_errors(errors)

    user = db.get_user_by_login(get_db(), login)
    # Одно и то же сообщение и для «нет такого логина», и для «неверный пароль»
    if not user or not check_password_hash(user["password_hash"], password):
        return _field_errors({"login": "", "password": ""}, "Неверный логин или пароль.")

    _log_in(user["id"])
    target = "/admin" if user["role"] == "ADMIN" else "/cabinet"
    return jsonify({"status": "success", "redirect": target})


@app.route("/api/register", methods=["POST"])
def api_register():
    """Регистрация: логин + пароль + анкета ФЛ, ЮЛ или ИП. Анкета уходит на проверку администратору."""
    data = request.get_json(silent=True) or {}
    role = {"ul": "UL", "ip": "IP"}.get(data.get("role"), "FL")
    raw = data.get("fields") or {}
    f = {k: ("" if v is None else str(v) if k in ("password", "password2") else str(v).strip())
         for k, v in raw.items()}

    clean, errors = validators.validate_registration(role, f)
    if errors:
        return _field_errors(errors)

    profile = clean["profile"]
    if role == "FL":
        account, bik, wallet_name = "40817810" + _rand_digits(12), "044525225", "Основной кошелёк ФЛ"
    elif role == "IP":
        account, bik, wallet_name = profile["bank_account"], profile["bank_bik"], "Кошелёк ИП"
    else:
        account, bik, wallet_name = profile["bank_account"], profile["bank_bik"], "Корпоративный кошелёк"

    try:
        res = db.register_user(
            get_db(), role=role, login=clean["login"],
            password_hash=generate_password_hash(f["password"]),
            profile=profile, profile_json=clean["anketa"], evm_address="0x" + secrets.token_hex(20),
            wallet_name=wallet_name, bank_account=account, bank_bik=bik,
        )
    except db.FieldError as e:
        field = {"inn": "fl_inn" if role in validators.PERSON_ROLES else "ul_inn_kpp"}.get(e.field, e.field)
        return _field_errors({field: str(e)})
    except sqlite3.IntegrityError:
        return _field_errors({"login": "Этот логин уже занят — придумайте другой."})

    _log_in(res["user_id"])
    return jsonify({"status": "success", "redirect": "/cabinet"})


@app.route("/api/issues", methods=["GET"])
def api_get_issues():
    """Список опубликованных выпусков ЦФА."""
    issues = get_db().execute(f"SELECT * FROM cfa_issues WHERE status IN {PUBLIC_STATUSES}").fetchall()
    return jsonify([{k: r[k] for k in r.keys() if k != "rov_json"} for r in issues])


def _save_decision(issue_id=None):
    me = current_user()
    if me["role"] not in validators.ISSUER_ROLES:
        return json_error("Выпускать ЦФА могут только юридические лица и ИП (259-ФЗ).", 403)
    rov, errors = validators.validate_rov(request.get_json(silent=True) or {})
    if errors:
        return _field_errors(errors)
    try:
        res = db.save_issue_decision(get_db(), issuer_id=me["id"], rov=rov, issue_id=issue_id)
    except ValueError as e:
        return json_error(str(e))
    return jsonify({**res, "status": "success"})


@app.route("/api/issues", methods=["POST"])
@api_approved_required
def api_create_issue():
    """Эмитент подаёт Решение о выпуске → статус «На проверке»."""
    return _save_decision()


@app.route("/api/issues/<int:issue_id>", methods=["POST"])
@api_approved_required
def api_update_issue(issue_id):
    """Исправление РоВ (пока выпуск на проверке или возвращён на доработку)."""
    return _save_decision(issue_id)


@app.route("/api/issues/<int:issue_id>/finalize", methods=["POST"])
@api_approved_required
def api_finalize(issue_id):
    """Закрытие сбора заявок: атомарный расчёт DvP или возврат средств."""
    conn = get_db()
    issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
    if not issue or issue["issuer_id"] != session["user_id"]:
        return json_error("Закрыть сбор может только эмитент этого выпуска.", 403)
    try:
        return jsonify({**db.finalize_issue_dvp(conn, issue_id, chain=chain()), "status": "success"})
    except ValueError as e:
        return json_error(str(e))


@app.route("/api/issues/<int:issue_id>/coupon", methods=["POST"])
@api_approved_required
def api_pay_coupon(issue_id):
    """Эмитент выплачивает наступивший купон сам (например, сразу после пополнения кошелька), не дожидаясь сторожа."""
    try:
        return jsonify({**db.pay_coupon(get_db(), issue_id, session["user_id"], chain=chain()), "status": "success"})
    except ValueError as e:
        return json_error(str(e))


@app.route("/api/issues/<int:issue_id>/redeem", methods=["POST"])
@api_approved_required
def api_redeem(issue_id):
    """Эмитент погашает выпуск: в дату погашения или досрочно, если это разрешено Решением о выпуске."""
    try:
        return jsonify({**db.redeem_issue(get_db(), issue_id, session["user_id"], chain=chain()), "status": "success"})
    except ValueError as e:
        return json_error(str(e))


@app.route("/api/buy", methods=["POST"])
@api_approved_required
def api_buy():
    d = request.get_json(silent=True) or {}
    try:
        sign_code = str(d.get("sign_code", "")).strip()
        if not sign_code:
            raise ValueError("Введите код подтверждения.")
        try:
            issue_id, qty = int(d.get("issue_id")), int(d.get("qty"))
        except (TypeError, ValueError):
            raise ValueError("Укажите выпуск и количество ЦФА.")
        res = db.buy_cfa_on_showcase(get_db(), user_id=session["user_id"], issue_id=issue_id, qty=qty,
                                     sign_code=sign_code, chain=chain())
        return jsonify({**res, "status": "success"})
    except ValueError as e:
        return json_error(str(e))


def _amount(d) -> int:
    try:
        return int(str(d.get("amount_rub", "")).strip())
    except ValueError:
        raise ValueError("Введите сумму целым числом.")


@app.route("/api/deposit", methods=["POST"])
@api_approved_required
def api_deposit():
    """Пополнение цифрового кошелька текущего пользователя."""
    try:
        res = db.deposit_wallet(get_db(), user_id=session["user_id"],
                                amount_rub=_amount(request.get_json(silent=True) or {}), chain=chain())
        return jsonify({**res, "status": "success"})
    except ValueError as e:
        return json_error(str(e))


@app.route("/api/withdraw", methods=["POST"])
@api_approved_required
def api_withdraw():
    try:
        res = db.withdraw_wallet(get_db(), user_id=session["user_id"],
                                 amount_rub=_amount(request.get_json(silent=True) or {}), chain=chain())
        return jsonify({**res, "status": "success"})
    except ValueError as e:
        return json_error(str(e))


# ==============================================================================
# Команды командной строки:  flask --app main create-admin  /  flask --app main seed-demo
# ==============================================================================
@app.cli.command("create-admin")
@click.argument("login", default="admin")
@click.option("--name", default="Администратор платформы", help="ФИО администратора")
@click.password_option(help="Пароль (спросит, если не указан)")
def create_admin_cmd(login, name, password):
    """Создать администратора платформы."""
    err = validators.check_login(login.lower()) or validators.check_password(password)
    if err:
        raise click.ClickException(err)
    conn = db.get_connection()
    try:
        db.create_admin(conn, login.lower(), generate_password_hash(password), name)
    except ValueError as e:
        raise click.ClickException(str(e))
    finally:
        conn.close()
    click.echo(f"Администратор «{login.lower()}» создан. Войдите на /login — откроется админ-панель.")


@app.cli.command("seed-demo")
def seed_demo_cmd():
    """Заполнить пустую базу демо-данными (админ, эмитент, инвесторы, выпуски, сделки)."""
    import demo_data
    conn = db.get_connection()
    try:
        if conn.execute("SELECT 1 FROM users").fetchone():
            raise click.ClickException("База не пустая. Пересоздайте её: python database_setup.py")
        demo_data.seed(conn, chain())
    finally:
        conn.close()
    click.echo(demo_data.SUMMARY)


@app.cli.command("keeper")
@click.option("--loop", "loop", type=float, default=0, help="Повторять каждые N секунд (0 — один проход).")
def keeper_cmd(loop):
    """Сторож выплат: выплатить наступившие купоны и погашения (для cron / планировщика задач)."""
    import time
    while True:
        events = keeper.run_once(chain(), "cli")
        stamp = date.today().strftime("%d.%m.%Y")
        for e in events:
            click.echo(f"[{stamp}] {e['result']}: {e['message']}")
        if not events:
            nearest = [a for a in keeper.due_actions(db.get_connection()) if not a["due"]]
            hint = min(nearest, key=lambda a: a["date"]) if nearest else None
            click.echo(f"[{stamp}] Нечего исполнять." + (
                f" Ближайшее: {hint['what']} по «{hint['issue']['title']}» — {hint['date'][8:10]}.{hint['date'][5:7]}."
                f"{hint['date'][:4]}" if hint else ""))
        if loop <= 0:
            break
        time.sleep(loop)


if __name__ == "__main__":
    # Фоновый поток сторожа. В режиме отладки Flask запускает два процесса (наблюдатель за файлами и сервер) —
    # поток нужен только в процессе сервера (WERKZEUG_RUN_MAIN=true).
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        keeper.start_thread(chain(), KEEPER_INTERVAL)
        print(f"Сторож выплат: проверка каждые {KEEPER_INTERVAL:g} с")
    app.run(debug=True)
