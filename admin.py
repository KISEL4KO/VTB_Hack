#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admin.py — админ-панель платформы ЦФА (Flask Blueprint).

  /admin                 — сводка: что ждёт проверки, ключевые цифры
  /admin/users           — заявки на допуск: анкеты, автоматическая сверка, одобрение / отказ / блокировка
  /admin/issues          — модерация «Решений о выпуске»: одобрение (→ витрина и смарт-контракт) или доработка
  /admin/blockchain      — обозреватель встроенной EVM-сети и сверка учёта с контрактом
  /admin/audit           — журнал действий администраторов
  /admin/reports         — PDF / CSV отчёты оператора
"""

from __future__ import annotations

import csv
import io
import json

from flask import Blueprint, Response, abort, current_app, jsonify, render_template, request

import database_setup as db
import kyc_checks
import validators
from common import REPORTS as reports
from common import (ADMIN_ACTIONS, CHAIN_METHODS, DEFAULT_BENCHMARKS, admin_required, chain, current_user, get_benchmarks,
                    get_db, issue_payload, json_error, pdf_required, reconciliation, save_benchmarks)

bp = Blueprint("admin", __name__)


def _pdf(data: bytes, name: str) -> Response:
    return Response(data, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


def _counts(conn):
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "users_pending": q("SELECT COUNT(*) FROM users WHERE status = 'PENDING' AND role != 'ADMIN'"),
        "issues_review": q("SELECT COUNT(*) FROM cfa_issues WHERE status = 'REVIEW'"),
    }


@bp.app_context_processor
def admin_globals():
    me = current_user()
    if me and me["role"] == "ADMIN":
        return {"admin_counts": _counts(get_db())}
    return {}


# ==============================================================================
# Сводка
# ==============================================================================
@bp.route("/admin")
@admin_required
def dashboard():
    conn = get_db()
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    stats = {
        "participants": q("SELECT COUNT(*) FROM users WHERE role != 'ADMIN'"),
        "approved": q("SELECT COUNT(*) FROM users WHERE status = 'APPROVED' AND role != 'ADMIN'"),
        "issues_live": q("SELECT COUNT(*) FROM cfa_issues WHERE status = 'SUBSCRIPTION'"),
        "issued_volume": q("SELECT COALESCE(SUM(total_rub), 0) FROM orders WHERE status = 'EXECUTED'"),
        "hold_volume": q("SELECT COALESCE(SUM(total_rub), 0) FROM orders WHERE status = 'HOLD'"),
        "wallets_total": q("SELECT COALESCE(SUM(balance_rub + locked_rub), 0) FROM wallets"),
    }
    pending_users = conn.execute(
        "SELECT * FROM users WHERE status = 'PENDING' AND role != 'ADMIN' ORDER BY id LIMIT 5").fetchall()
    review_issues = conn.execute(
        """SELECT i.*, u.full_name AS issuer_name FROM cfa_issues i JOIN users u ON u.id = i.issuer_id
           WHERE i.status = 'REVIEW' ORDER BY i.updated_at LIMIT 5""").fetchall()
    actions = conn.execute(
        """SELECT a.*, u.full_name AS admin_name FROM admin_actions a JOIN users u ON u.id = a.admin_id
           ORDER BY a.id DESC LIMIT 6""").fetchall()
    ch = chain()
    return render_template("admin/dashboard.html", section="dashboard", stats=stats, pending_users=pending_users,
                           review_issues=review_issues, actions=actions, action_names=ADMIN_ACTIONS,
                           chain_status=ch.status() if ch else {"enabled": False})


# ==============================================================================
# Участники
# ==============================================================================
@bp.route("/admin/users")
@admin_required
def users():
    conn = get_db()
    status = request.args.get("status", "PENDING")
    where = "" if status == "ALL" else "AND u.status = ?"
    args = () if status == "ALL" else (status,)
    rows = conn.execute(
        f"""SELECT u.*, w.balance_rub, w.locked_rub FROM users u LEFT JOIN wallets w ON w.user_id = u.id
            WHERE u.role != 'ADMIN' {where} ORDER BY CASE u.status WHEN 'PENDING' THEN 0 ELSE 1 END, u.id DESC""",
        args).fetchall()
    items = [{"u": r, "check": kyc_checks.summary(kyc_checks.run_checks(conn, r))} for r in rows]
    counts = {s: conn.execute("SELECT COUNT(*) FROM users WHERE role != 'ADMIN' AND status = ?", (s,)).fetchone()[0]
              for s in ("PENDING", "APPROVED", "REJECTED", "BLOCKED")}
    return render_template("admin/users.html", section="users", items=items, status=status, counts=counts)


@bp.route("/admin/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id = ? AND role != 'ADMIN'", (user_id,)).fetchone()
    if not u:
        abort(404)
    anketa = json.loads(u["profile_json"] or "{}")
    reviewer = conn.execute("SELECT full_name FROM users WHERE id = ?", (u["reviewed_by"],)).fetchone() \
        if u["reviewed_by"] else None
    wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    history = conn.execute(
        """SELECT a.*, adm.full_name AS admin_name FROM admin_actions a JOIN users adm ON adm.id = a.admin_id
           WHERE a.target_type = 'user' AND a.target_id = ? ORDER BY a.id DESC""", (user_id,)).fetchall()
    return render_template("admin/user.html", section="users", u=u, wallet=wallet,
                           sections=validators.anketa_rows(u["role"], anketa),
                           checks=kyc_checks.run_checks(conn, u), reviewer=reviewer, history=history,
                           action_names=ADMIN_ACTIONS)


@bp.route("/admin/users/<int:user_id>/application.pdf")
@pdf_required
@admin_required
def user_pdf(user_id):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id = ? AND role != 'ADMIN'", (user_id,)).fetchone()
    if not u:
        abort(404)
    reviewer = conn.execute("SELECT full_name FROM users WHERE id = ?", (u["reviewed_by"],)).fetchone() \
        if u["reviewed_by"] else None
    pdf = reports.application_pdf(u, validators.anketa_rows(u["role"], json.loads(u["profile_json"] or "{}")),
                                  kyc_checks.run_checks(conn, u), reviewer["full_name"] if reviewer else None)
    return _pdf(pdf, f"Anketa_{user_id:05d}.pdf")


def _decision(fn, *args):
    """Общая обёртка для кнопок «Одобрить / Отклонить»: ошибки → JSON с подсветкой поля."""
    try:
        res = fn(*args) or {}
        return jsonify({**res, "status": "success"})
    except db.FieldError as e:
        return json_error(str(e), 400, {e.field: str(e)})
    except ValueError as e:
        return json_error(str(e))


def _reason():
    return str((request.get_json(silent=True) or {}).get("reason", ""))


@bp.route("/api/admin/users/<int:user_id>/approve", methods=["POST"])
@admin_required
def api_user_approve(user_id):
    return _decision(db.approve_user, get_db(), chain(), current_user()["id"], user_id, _reason())


@bp.route("/api/admin/users/<int:user_id>/reject", methods=["POST"])
@admin_required
def api_user_reject(user_id):
    return _decision(db.reject_user, get_db(), current_user()["id"], user_id, _reason())


@bp.route("/api/admin/users/<int:user_id>/block", methods=["POST"])
@admin_required
def api_user_block(user_id):
    return _decision(db.block_user, get_db(), chain(), current_user()["id"], user_id, _reason())


# ==============================================================================
# Выпуски (модерация Решений о выпуске)
# ==============================================================================
@bp.route("/admin/issues")
@admin_required
def issues():
    conn = get_db()
    status = request.args.get("status", "REVIEW")
    where = "" if status == "ALL" else "WHERE i.status = ?"
    args = () if status == "ALL" else (status,)
    rows = conn.execute(
        f"""SELECT i.*, u.full_name AS issuer_name, u.status AS issuer_status FROM cfa_issues i
            JOIN users u ON u.id = i.issuer_id {where}
            ORDER BY CASE i.status WHEN 'REVIEW' THEN 0 ELSE 1 END, i.id DESC""", args).fetchall()
    counts = {s: conn.execute("SELECT COUNT(*) FROM cfa_issues WHERE status = ?", (s,)).fetchone()[0]
              for s in ("REVIEW", "REJECTED", "SUBSCRIPTION", "ISSUED", "CANCELLED", "REDEEMED")}
    return render_template("admin/issues.html", section="issues", rows=rows, status=status, counts=counts)


def _issue_checks(issue, issuer, rov) -> list[dict]:
    """Подсказки модератору по Решению о выпуске."""
    from datetime import date
    today = date.today().isoformat()
    item = lambda t, ok, a, b, bad="warn": {"title": t, "status": "ok" if ok else bad, "detail": a if ok else b}  # noqa
    return [
        item("Эмитент допущен к платформе", issuer["status"] == "APPROVED", "Анкета эмитента одобрена",
             "Сначала проверьте анкету эмитента", "fail"),
        item("Период размещения не истёк", issue["placement_end"] >= today,
             f"до {issue['placement_end']}", "Дата окончания уже прошла — верните на доработку", "fail"),
        item("Наименование эмитента в РоВ совпадает с анкетой",
             issuer["full_name"].split("«")[-1].strip("» ").lower() in rov.get("issuer_full_name", "").lower(),
             rov.get("issuer_full_name", ""), f"В анкете: {issuer['full_name']}, в РоВ: {rov.get('issuer_full_name')}"),
        item("Порог выпуска разумен (минимум ≥ 10 % максимума)", issue["min_qty"] * 10 >= issue["max_qty"],
             f"{issue['min_qty']} из {issue['max_qty']} шт.", "Очень низкий порог — выпуск состоится почти при любом спросе"),
        item("Доходность в рыночном диапазоне (до 35 % годовых)", issue["coupon_rate"] <= 35,
             f"{issue['coupon_rate']} %", f"{issue['coupon_rate']} % — проверьте обоснование и риски"),
        item("Раскрыт кредитный риск эмитента", "credit" in rov.get("risks", []),
             "Указан", "Кредитный риск не отмечен"),
        item("Подписант указан", bool(rov.get("signer_name")), f"{rov.get('signer_position')} {rov.get('signer_name')}",
             "Нет подписанта", "fail"),
    ]


@bp.route("/admin/issues/<int:issue_id>")
@admin_required
def issue_detail(issue_id):
    conn = get_db()
    issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
    if not issue:
        abort(404)
    issuer = conn.execute("SELECT * FROM users WHERE id = ?", (issue["issuer_id"],)).fetchone()
    rov = json.loads(issue["rov_json"])
    orders = conn.execute(
        """SELECT o.*, u.full_name FROM orders o JOIN users u ON u.id = o.user_id WHERE o.issue_id = ?
           ORDER BY o.id DESC""", (issue_id,)).fetchall()
    history = conn.execute(
        """SELECT a.*, adm.full_name AS admin_name FROM admin_actions a JOIN users adm ON adm.id = a.admin_id
           WHERE a.target_type = 'issue' AND a.target_id = ? ORDER BY a.id DESC""", (issue_id,)).fetchall()
    return render_template("admin/issue.html", section="issues", issue=issue, issuer=issuer, rov=rov,
                           payload=issue_payload(conn, issue), bench=get_benchmarks(conn),
                           checks=_issue_checks(issue, issuer, rov), orders=orders, history=history,
                           action_names=ADMIN_ACTIONS, risk_names=[validators.ROV_RISKS.get(r, r) for r in rov.get('risks', [])],
                           investors_all=validators.ROV_INVESTORS, payments=validators.ROV_PAYMENT,
                           coupon_periods=validators.ROV_COUPON_PERIODS)


@bp.route("/api/admin/issues/<int:issue_id>/approve", methods=["POST"])
@admin_required
def api_issue_approve(issue_id):
    return _decision(db.approve_issue, get_db(), chain(), current_user()["id"], issue_id, _reason())


@bp.route("/api/admin/issues/<int:issue_id>/reject", methods=["POST"])
@admin_required
def api_issue_reject(issue_id):
    return _decision(db.reject_issue, get_db(), current_user()["id"], issue_id, _reason())


# ==============================================================================
# Блокчейн: обозреватель и сверка
# ==============================================================================
@bp.route("/admin/blockchain")
@admin_required
def blockchain_page():
    conn = get_db()
    ch = chain()
    txs = []
    if ch and ch.enabled:
        txs = conn.execute("SELECT seq, tx_hash, method, args_json, block_number, gas_used, created_at "
                           "FROM evm_transactions ORDER BY seq DESC LIMIT 100").fetchall()
    rows = reconciliation(conn)
    return render_template("admin/blockchain.html", section="blockchain", status=ch.status() if ch else {},
                           txs=txs, methods=CHAIN_METHODS, rec=rows, rec_bad=sum(not r["match"] for r in rows))


# ==============================================================================
# Сторож выплат (keeper): что пора выплатить, что уже сделано, ручной запуск
# ==============================================================================
@bp.route("/admin/keeper")
@admin_required
def keeper_page():
    import keeper
    conn = get_db()
    events = conn.execute(
        """SELECT k.*, i.title FROM keeper_events k LEFT JOIN cfa_issues i ON i.id = k.issue_id
           ORDER BY k.id DESC LIMIT 50""").fetchall()
    actions = sorted(keeper.due_actions(conn), key=lambda a: (not a["due"], a["date"]))
    return render_template("admin/keeper.html", section="keeper", actions=actions, events=events, last=keeper.LAST_RUN,
                           interval=current_app.config.get("KEEPER_INTERVAL", 0),
                           req_interval=current_app.config.get("KEEPER_REQUEST_INTERVAL", 0))


@bp.route("/api/admin/keeper/run", methods=["POST"])
@admin_required
def api_keeper_run():
    import keeper
    events = keeper.run_once(chain(), "admin")
    return jsonify({"status": "success", "events": events})


# ==============================================================================
# Журнал действий и отчёты
# ==============================================================================
@bp.route("/admin/audit")
@admin_required
def audit():
    rows = get_db().execute(
        """SELECT a.*, adm.full_name AS admin_name,
                  CASE a.target_type WHEN 'user' THEN (SELECT full_name FROM users WHERE id = a.target_id)
                                     WHEN 'issue' THEN (SELECT title FROM cfa_issues WHERE id = a.target_id)
                                     ELSE 'Рыночные ориентиры' END AS target_name
           FROM admin_actions a JOIN users adm ON adm.id = a.admin_id ORDER BY a.id DESC""").fetchall()
    return render_template("admin/audit.html", section="audit", rows=rows, action_names=ADMIN_ACTIONS)


@bp.route("/admin/reports")
@admin_required
def reports_page():
    issues = get_db().execute(
        """SELECT i.*, u.full_name AS issuer_name FROM cfa_issues i JOIN users u ON u.id = i.issuer_id
           ORDER BY i.id DESC""").fetchall()
    return render_template("admin/reports.html", section="reports", issues=issues)


@bp.route("/admin/reports/participants.pdf")
@pdf_required
@admin_required
def participants_pdf():
    users = get_db().execute(
        """SELECT u.*, COALESCE(w.balance_rub, 0) AS balance_rub, COALESCE(w.locked_rub, 0) AS locked_rub
           FROM users u LEFT JOIN wallets w ON w.user_id = u.id WHERE u.role != 'ADMIN' ORDER BY u.id""").fetchall()
    return _pdf(reports.participants_pdf(users), "Reestr_uchastnikov.pdf")


@bp.route("/admin/reports/reconciliation.pdf")
@pdf_required
@admin_required
def reconciliation_pdf():
    ch = chain()
    return _pdf(reports.reconciliation_pdf(reconciliation(get_db()), ch.status() if ch else {}), "Akt_sverki.pdf")


@bp.route("/admin/reports/audit.csv")
@admin_required
def audit_csv():
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Дата", "Администратор", "Действие", "Объект", "ID", "Комментарий", "EVM tx"])
    for r in get_db().execute("""SELECT a.*, u.full_name FROM admin_actions a JOIN users u ON u.id = a.admin_id
                                 ORDER BY a.id"""):
        w.writerow([r["created_at"], r["full_name"], ADMIN_ACTIONS.get(r["action"], r["action"]), r["target_type"],
                    r["target_id"], r["comment"] or "", r["evm_tx_hash"] or ""])
    return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=admin_audit.csv"})


# ==============================================================================
# Рыночные ориентиры для аналитики (ключевая ставка, вклады, инфляция, кривая ОФЗ)
# ==============================================================================
@bp.route("/admin/benchmarks")
@admin_required
def benchmarks():
    conn = get_db()
    b = get_benchmarks(conn)
    row = conn.execute("""SELECT s.updated_at, u.full_name FROM platform_settings s LEFT JOIN users u ON u.id = s.updated_by
                          WHERE s.key = 'benchmarks'""").fetchone()
    curve = "\n".join(f"{str(t).replace('.', ',')} : {str(y).replace('.', ',')}" for t, y in b["ofz_curve"])
    return render_template("admin/benchmarks.html", section="benchmarks", b=b, curve=curve, updated=row,
                           defaults=DEFAULT_BENCHMARKS)


def _num(d, key, label, lo, hi, errors):
    raw = str(d.get(key, "")).strip().replace(",", ".").replace("%", "")
    try:
        v = float(raw)
    except ValueError:
        errors[key] = f"{label}: введите число, например 14 или 13,06."
        return None
    if not lo <= v <= hi:
        errors[key] = f"{label}: от {lo} до {hi}."
        return None
    return round(v, 2)


@bp.route("/api/admin/benchmarks", methods=["POST"])
@admin_required
def api_benchmarks():
    from datetime import date as _date
    d = request.get_json(silent=True) or {}
    if d.get("reset"):
        data = {k: v for k, v in DEFAULT_BENCHMARKS.items()}
    else:
        errors: dict = {}
        data = {
            "key_rate": _num(d, "key_rate", "Ключевая ставка", 0, 100, errors),
            "deposit_rate": _num(d, "deposit_rate", "Ставка по вкладам", 0, 100, errors),
            "inflation": _num(d, "inflation", "Инфляция", -20, 200, errors),
            "ndfl": _num(d, "ndfl", "НДФЛ", 0, 50, errors),
            "sources": str(d.get("sources", "")).strip()[:600] or "—",
        }
        try:
            data["as_of"] = _date.fromisoformat(str(d.get("as_of", ""))).isoformat()
        except ValueError:
            errors["as_of"] = "Укажите дату, на которую актуальны значения."
        curve = []
        for n, line in enumerate(str(d.get("ofz_curve", "")).splitlines(), 1):
            if not line.strip():
                continue
            parts = line.replace(",", ".").replace(";", ":").split(":")
            try:
                t, y = float(parts[0]), float(parts[1])
                if not (0 < t <= 50 and 0 <= y <= 100):
                    raise ValueError
                curve.append([round(t, 2), round(y, 2)])
            except (ValueError, IndexError):
                errors["ofz_curve"] = f"Строка {n}: формат «срок в годах : доходность», например «2,4 : 14,84»."
                break
        if len(curve) < 2 and "ofz_curve" not in errors:
            errors["ofz_curve"] = "Нужно хотя бы две точки кривой ОФЗ."
        data["ofz_curve"] = sorted(curve)
        if errors:
            return json_error("Проверьте поля, выделенные красным.", 400, errors)
    save_benchmarks(get_db(), current_user()["id"], data)
    return jsonify({"status": "success"})
