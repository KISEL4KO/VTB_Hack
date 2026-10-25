from datetime import date
from functools import wraps
import csv
import io
import os
import random
import secrets
import sqlite3

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash  # werkzeug ставится вместе с Flask

import database_setup as db
import validators

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cfa-dev-secret-change-me")

# Автоматическая инициализация базы данных при старте приложения
if not db.DB_PATH.exists():
    _conn = db.get_connection()
    db.init_db(_conn)
    _conn.close()
    print("База данных 'cfa.db' успешно создана (пустая — пользователи появятся после регистрации).")


# ==============================================================================
# Вспомогательное: соединение с БД на время запроса, текущий пользователь, фильтры
# ==============================================================================
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = db.get_connection()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def api_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return jsonify({"status": "error", "message": "Необходимо войти на платформу."}), 401
        return fn(*args, **kwargs)
    return wrapper


@app.template_filter("rub")
def fmt_rub(value):
    try:
        return f"{int(value):,}".replace(",", " ") + " ₽"
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
    return f"{int(value or 0):,}".replace(",", " ")


ISSUE_STATUS = {
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
OP_TYPES = {
    "DEPOSIT": "Пополнение кошелька",
    "WITHDRAW": "Вывод средств",
    "ISSUE_CREATE": "Публикация выпуска ЦФА",
    "BUY_HOLD": "Покупка ЦФА (холд)",
    "DVP_SETTLE": "Расчёт DvP",
    "ISSUE_REFUND": "Отмена выпуска / возврат",
}


@app.context_processor
def inject_globals():
    user = current_user()
    wallet = (get_db().execute("SELECT * FROM wallets WHERE user_id = ?", (user["id"],)).fetchone()
              if user else None)
    return {
        "me": user,
        "my_wallet": wallet,
        "issue_status": ISSUE_STATUS,
        "order_status": ORDER_STATUS,
        "op_types": OP_TYPES,
    }


# ==============================================================================
# Страницы
# ==============================================================================
@app.route("/")
def home():
    """Главная страница: витрина выпусков ЦФА из базы данных."""
    issues = get_db().execute(
        """
        SELECT i.*, u.full_name AS issuer_name
        FROM cfa_issues i JOIN users u ON u.id = i.issuer_id
        ORDER BY CASE i.status WHEN 'SUBSCRIPTION' THEN 0 ELSE 1 END, i.id DESC
        """
    ).fetchall()
    return render_template("market.html", issues=issues)


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
    return redirect(url_for("cabinet_tab", tab="products"))


CABINET_TABS = ["wallets", "products", "history", "reports", "profile",
                "assets", "operations", "coupons", "issuer"]


@app.route("/cabinet/<tab>")
def cabinet_tab(tab):
    user = current_user()
    if not user:
        return redirect(url_for("login_page", next=request.path))
    if tab not in CABINET_TABS:
        abort(404)

    conn = get_db()
    uid = user["id"]
    ctx = {"tab": tab, "wallet": conn.execute(
        "SELECT * FROM wallets WHERE user_id = ?", (uid,)).fetchone()}

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
        ctx["coupons"] = conn.execute(
            """
            SELECT p.*, i.title, i.coupon_rate, i.maturity_date, i.status
            FROM portfolio_assets p JOIN cfa_issues i ON i.id = p.issue_id
            WHERE p.user_id = ? AND p.qty > 0 ORDER BY i.maturity_date
            """, (uid,)).fetchall()
    elif tab == "issuer":
        ctx["my_issues"] = conn.execute(
            "SELECT * FROM cfa_issues WHERE issuer_id = ? ORDER BY id DESC", (uid,)).fetchall()
    elif tab == "reports":
        ctx["today"] = date.today().strftime("%d.%m.%Y")

    return render_template(f"cabinet/{tab}.html", **ctx)


@app.route("/reports/<kind>.csv")
def report_csv(kind):
    user = current_user()
    if not user:
        abort(401)
    conn = get_db()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    if kind == "ledger":
        w.writerow(["Дата", "Тип", "Сумма, руб.", "Описание", "TxHash"])
        for r in conn.execute("SELECT * FROM blockchain_ledger WHERE user_id = ? ORDER BY block_id", (user["id"],)):
            w.writerow([r["created_at"], OP_TYPES.get(r["op_type"], r["op_type"]), r["amount_rub"],
                        r["description"], r["tx_hash"]])
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


# ==============================================================================
# API
# ==============================================================================
def _rand_digits(n: int) -> str:
    return "".join(random.choice("0123456789") for _ in range(n))


def _field_errors(errors: dict, message: str = "Проверьте поля, выделенные красным."):
    """Ответ 400 с ошибками по конкретным полям — app.js подсветит именно эти поля."""
    return jsonify({"status": "error", "message": message, "errors": errors}), 400


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
    # Одно и то же сообщение и для «нет такого логина», и для «неверный пароль»,
    # чтобы по ответу нельзя было подбирать существующие логины.
    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
        return _field_errors({"login": "", "password": ""}, "Неверный логин или пароль.")

    _log_in(user["id"])
    return jsonify({"status": "success"})


@app.route("/api/register", methods=["POST"])
def api_register():
    """Регистрация: логин + пароль + анкета ФЛ или ЮЛ. Создаёт участника и его кошелёк."""
    data = request.get_json(silent=True) or {}
    role = "UL" if data.get("role") == "ul" else "FL"
    raw = data.get("fields") or {}
    # Все поля — в строки без пробелов по краям; пароли оставляем как есть.
    f = {k: ("" if v is None else str(v) if k in ("password", "password2") else str(v).strip())
         for k, v in raw.items()}

    clean, errors = validators.validate_registration(role, f)
    if errors:
        return _field_errors(errors)

    profile = clean["profile"]
    if role == "FL":
        account, bik, wallet_name = "40817810" + _rand_digits(12), "044525225", "Основной кошелёк ФЛ (СБП)"
    else:
        account, bik, wallet_name = profile["bank_account"], profile["bank_bik"], "Корпоративный кошелёк"

    try:
        res = db.register_user(
            get_db(), role=role, login=clean["login"],
            password_hash=generate_password_hash(f["password"]),
            profile=profile, evm_address="0x" + secrets.token_hex(20),
            wallet_name=wallet_name, bank_account=account, bank_bik=bik,
        )
    except db.FieldError as e:
        # Ошибку «inn» из слоя БД показываем на нужном поле формы (у ФЛ и ЮЛ оно называется по-разному)
        field = {"inn": "fl_inn" if role == "FL" else "ul_inn_kpp"}.get(e.field, e.field)
        return _field_errors({field: str(e)})
    except sqlite3.IntegrityError:
        # Два одновременных запроса с одним логином: UNIQUE в БД не даст создать дубль
        return _field_errors({"login": "Этот логин уже занят — придумайте другой."})

    _log_in(res["user_id"])
    return jsonify({"status": "success"})


@app.route("/api/issues", methods=["GET"])
def api_get_issues():
    """Список всех выпусков ЦФА из базы данных."""
    issues = get_db().execute("SELECT * FROM cfa_issues").fetchall()
    return jsonify([dict(row) for row in issues])


@app.route("/api/issues", methods=["POST"])
@api_login_required
def api_create_issue():
    """Создание выпуска ЦФА эмитентом (только ЮЛ)."""
    d = request.get_json(silent=True) or {}
    try:
        title = str(d.get("title", "")).strip()
        if not title:
            raise ValueError("Укажите наименование выпуска.")
        try:
            params = dict(price_per_cfa=int(d["price_per_cfa"]), min_qty=int(d["min_qty"]),
                          max_qty=int(d["max_qty"]), coupon_rate=float(d["coupon_rate"]),
                          maturity_date=str(d["maturity_date"]))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Заполните все числовые поля и дату погашения.")
        if params["price_per_cfa"] <= 0 or params["min_qty"] <= 0 or params["max_qty"] < params["min_qty"]:
            raise ValueError("Цена и объёмы должны быть положительными, максимум — не меньше минимума.")
        res = db.create_cfa_issue(get_db(), issuer_id=session["user_id"], title=title,
                                  only_qual=bool(d.get("only_qual")), **params)
        return jsonify({"status": "success", **res})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/issues/<int:issue_id>/finalize", methods=["POST"])
@api_login_required
def api_finalize(issue_id):
    """Закрытие сбора заявок: атомарный расчёт DvP или возврат средств."""
    conn = get_db()
    issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue_id,)).fetchone()
    if not issue or issue["issuer_id"] != session["user_id"]:
        return jsonify({"status": "error", "message": "Закрыть сбор может только эмитент этого выпуска."}), 403
    try:
        return jsonify({"status": "success", **db.finalize_issue_dvp(conn, issue_id)})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/buy", methods=["POST"])
@api_login_required
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
        res = db.buy_cfa_on_showcase(get_db(), user_id=session["user_id"],
                                     issue_id=issue_id, qty=qty, sign_code=sign_code)
        return jsonify({"status": "success", **res})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/deposit", methods=["POST"])
@api_login_required
def api_deposit():
    """Пополнение цифрового кошелька текущего пользователя."""
    d = request.get_json(silent=True) or {}
    try:
        res = db.deposit_wallet(get_db(), user_id=session["user_id"], amount_rub=int(d.get("amount_rub")))
        return jsonify({"status": "success", **res})
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": str(e) if isinstance(e, ValueError) else "Введите сумму."}), 400


@app.route("/api/withdraw", methods=["POST"])
@api_login_required
def api_withdraw():
    d = request.get_json(silent=True) or {}
    try:
        res = db.withdraw_wallet(get_db(), user_id=session["user_id"], amount_rub=int(d.get("amount_rub")))
        return jsonify({"status": "success", **res})
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": str(e) if isinstance(e, ValueError) else "Введите сумму."}), 400


if __name__ == "__main__":
    app.run(debug=True)
