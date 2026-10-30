"""HTTP API (Flask test client): вход, регистрация, права доступа, кошелёк, выпуски, админка, PDF."""

from __future__ import annotations

import sqlite3

import pytest

import database_setup as db
from conftest import PASSWORD, fl_form, issue_row, json_of, login_as, rov_form, ul_form, wallet


def _post(client, url: str, payload: dict | None = None):
    return client.post(url, json=payload or {})


# ------------------------------------------------------------------ вход
def test_login_happy_path_and_redirects(client, conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    uid = make_user("FL")
    login = conn.execute("SELECT login FROM users WHERE id = ?", (uid,)).fetchone()["login"]
    r = _post(client, "/api/login", {"login": f"  {login.upper()} ", "password": PASSWORD})
    assert r.status_code == 200 and json_of(r)["redirect"] == "/cabinet"
    with client.session_transaction() as s:
        assert s["user_id"] == uid

    r = _post(client, "/api/login", {"login": "admin", "password": PASSWORD})
    assert json_of(r)["redirect"] == "/admin"


def test_login_empty_fields(client) -> None:
    r = _post(client, "/api/login", {})
    assert r.status_code == 400
    assert set(json_of(r)["errors"]) == {"login", "password"}


def test_login_same_message_for_unknown_user_and_wrong_password(client, admin_id: int) -> None:
    """Нельзя по ответу понять, существует ли логин (защита от перебора)."""
    wrong_pwd = _post(client, "/api/login", {"login": "admin", "password": "wrong-pass1"})
    no_user = _post(client, "/api/login", {"login": "nobody", "password": "wrong-pass1"})
    assert wrong_pwd.status_code == no_user.status_code == 400
    assert json_of(wrong_pwd) == json_of(no_user)


def test_login_password_is_not_trimmed(client, admin_id: int) -> None:
    r = _post(client, "/api/login", {"login": "admin", "password": f" {PASSWORD} "})
    assert r.status_code == 400


def test_login_rotates_session(client, admin_id: int) -> None:
    with client.session_transaction() as s:
        s["planted"] = "attacker"
    _post(client, "/api/login", {"login": "admin", "password": PASSWORD})
    with client.session_transaction() as s:
        assert "planted" not in s


@pytest.mark.parametrize("payload", [None, "not json", "null"])
def test_login_malformed_body(client, payload) -> None:
    r = client.post("/api/login", data=payload, content_type="application/json" if payload == "null" else None)
    assert r.status_code == 400 and json_of(r)["status"] == "error"


@pytest.mark.xfail(strict=True, raises=AttributeError,
                   reason="БАГ: `request.get_json(silent=True) or {}` пропускает JSON-массив → .get() падает с 500")
@pytest.mark.parametrize("url", ["/api/login", "/api/register"])
def test_json_array_body_returns_400(client, url: str) -> None:
    r = client.post(url, json=["login", "password"])
    assert r.status_code == 400


def test_logout_clears_session(client, make_user) -> None:
    login_as(client, make_user("FL"))
    client.get("/logout")
    with client.session_transaction() as s:
        assert "user_id" not in s


# ------------------------------------------------------------------ регистрация
@pytest.mark.parametrize("role, form", [("fl", fl_form), ("ul", ul_form)])
def test_register_happy_path(client, conn: sqlite3.Connection, role: str, form) -> None:
    fields = form(9001)
    r = _post(client, "/api/register", {"role": role, "fields": fields})
    assert r.status_code == 200, json_of(r)
    user = db.get_user_by_login(conn, fields["login"])
    assert user["status"] == "PENDING"
    with client.session_transaction() as s:
        assert s["user_id"] == user["id"]


def test_register_validation_errors_are_per_field(client) -> None:
    r = _post(client, "/api/register", {"role": "fl", "fields": fl_form(9002, fl_email="bad", password2="x")})
    assert r.status_code == 400
    assert {"fl_email", "password2"} <= set(json_of(r)["errors"])


def test_register_duplicate_login_and_inn(client) -> None:
    first = fl_form(9003)
    assert _post(client, "/api/register", {"role": "fl", "fields": first}).status_code == 200
    client.get("/logout")

    dup_login = fl_form(9004, login=first["login"])
    r = _post(client, "/api/register", {"role": "fl", "fields": dup_login})
    assert r.status_code == 400 and "login" in json_of(r)["errors"]

    dup_inn = fl_form(9005, fl_inn=first["fl_inn"])
    r = _post(client, "/api/register", {"role": "fl", "fields": dup_inn})
    assert r.status_code == 400 and "fl_inn" in json_of(r)["errors"]


def test_register_ignores_role_injection(client, conn: sqlite3.Connection) -> None:
    """Нельзя зарегистрироваться администратором через поле role."""
    fields = fl_form(9006)
    _post(client, "/api/register", {"role": "ADMIN", "fields": fields})
    assert db.get_user_by_login(conn, fields["login"])["role"] == "FL"


def test_register_null_values_do_not_crash(client) -> None:
    r = _post(client, "/api/register", {"role": "fl", "fields": {"login": None, "password": None}})
    assert r.status_code == 400


# ------------------------------------------------------------------ права доступа
PROTECTED = ["/api/deposit", "/api/withdraw", "/api/buy", "/api/issues", "/api/issues/1/finalize",
             "/api/issues/1/coupon", "/api/issues/1/redeem"]


@pytest.mark.parametrize("url", PROTECTED)
def test_anonymous_gets_401(client, url: str) -> None:
    assert _post(client, url).status_code == 401


@pytest.mark.parametrize("url", PROTECTED)
def test_pending_user_gets_403(client, make_user, url: str) -> None:
    login_as(client, make_user("FL", approve=False))
    assert _post(client, url).status_code == 403


@pytest.mark.parametrize("url", PROTECTED)
def test_admin_cannot_operate_assets(client, admin_id: int, url: str) -> None:
    login_as(client, admin_id)
    assert _post(client, url).status_code == 403


def test_blocked_user_is_cut_off(client, conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    uid = make_user("FL", balance=1_000)
    db.block_user(conn, None, admin_id, uid, "Подозрительно")
    login_as(client, uid)
    assert _post(client, "/api/withdraw", {"amount_rub": 100}).status_code == 403


@pytest.mark.parametrize("url", ["/api/admin/users/1/approve", "/api/admin/users/1/reject",
                                 "/api/admin/users/1/block", "/api/admin/issues/1/approve",
                                 "/api/admin/issues/1/reject", "/api/admin/benchmarks"])
def test_admin_api_requires_admin(client, make_user, url: str) -> None:
    assert _post(client, url).status_code == 401
    login_as(client, make_user("UL"))
    assert _post(client, url).status_code == 403


def test_admin_pages_redirect_anonymous_and_forbid_users(client, make_user) -> None:
    r = client.get("/admin")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    login_as(client, make_user("FL"))
    assert client.get("/admin").status_code == 403


def test_stale_session_of_deleted_user(client) -> None:
    login_as(client, 987_654)
    assert _post(client, "/api/deposit", {"amount_rub": 1}).status_code == 401


# ------------------------------------------------------------------ кошелёк
def test_deposit_withdraw_via_api(client, conn: sqlite3.Connection, make_user) -> None:
    uid = make_user("FL")
    login_as(client, uid)
    assert _post(client, "/api/deposit", {"amount_rub": "15000"}).status_code == 200
    assert _post(client, "/api/withdraw", {"amount_rub": 5000}).status_code == 200
    assert wallet(conn, uid)["balance_rub"] == 10_000


@pytest.mark.parametrize("amount, message", [
    ("abc", "целым числом"), ("10.5", "целым числом"), ("", "целым числом"),
    (0, "больше нуля"), (-100, "больше нуля"), (10**10, "Слишком большая"),
])
def test_deposit_bad_amounts(client, conn: sqlite3.Connection, make_user, amount, message: str) -> None:
    uid = make_user("FL")
    login_as(client, uid)
    r = _post(client, "/api/deposit", {"amount_rub": amount})
    assert r.status_code == 400 and message in json_of(r)["message"]
    assert wallet(conn, uid)["balance_rub"] == 0


def test_withdraw_over_balance_via_api(client, make_user) -> None:
    login_as(client, make_user("FL", balance=100))
    r = _post(client, "/api/withdraw", {"amount_rub": 101})
    assert r.status_code == 400 and "Недостаточно" in json_of(r)["message"]


# ------------------------------------------------------------------ выпуски и покупка
def test_issuer_submits_decision(client, conn: sqlite3.Connection, make_user) -> None:
    issuer = make_user("UL")
    login_as(client, issuer)
    r = _post(client, "/api/issues", rov_form())
    assert r.status_code == 200
    assert issue_row(conn, json_of(r)["issue_id"])["status"] == "REVIEW"


def test_investor_cannot_submit_decision(client, make_user) -> None:
    login_as(client, make_user("FL"))
    assert _post(client, "/api/issues", rov_form()).status_code == 403


def test_decision_validation_errors(client, make_user) -> None:
    login_as(client, make_user("UL"))
    r = _post(client, "/api/issues", rov_form(price_per_cfa="0", risks=[]))
    assert r.status_code == 400 and {"price_per_cfa", "risks"} <= set(json_of(r)["errors"])


def test_cannot_edit_foreign_or_published_decision(client, make_user, make_issue) -> None:
    owner, stranger = make_user("UL"), make_user("UL")
    draft = make_issue(owner, publish=False)
    login_as(client, stranger)
    assert _post(client, f"/api/issues/{draft}", rov_form()).status_code == 400

    published = make_issue(owner)
    login_as(client, owner)
    assert _post(client, f"/api/issues/{published}", rov_form()).status_code == 400


def test_public_issue_list_hides_drafts_and_documents(client, make_user, make_issue) -> None:
    issuer = make_user("UL")
    published, draft = make_issue(issuer), make_issue(issuer, publish=False)
    data = json_of(client.get("/api/issues"))
    ids = {i["id"] for i in data}
    assert published in ids and draft not in ids
    assert all("rov_json" not in i for i in data)


@pytest.mark.parametrize("payload, message", [
    ({"issue_id": 1, "qty": 1}, "код подтверждения"),
    ({"issue_id": "x", "qty": 1, "sign_code": "1234"}, "Укажите выпуск"),
    ({"issue_id": 1, "qty": None, "sign_code": "1234"}, "Укажите выпуск"),
])
def test_buy_bad_payload(client, make_user, payload: dict, message: str) -> None:
    login_as(client, make_user("FL", balance=10_000))
    r = _post(client, "/api/buy", payload)
    assert r.status_code == 400 and message in json_of(r)["message"]


def test_full_cycle_via_api(client, conn: sqlite3.Connection, make_user, make_issue) -> None:
    issuer = make_user("UL")
    issue_id = make_issue(issuer, min_qty="5")
    inv = make_user("FL", balance=10_000)

    login_as(client, inv)
    r = _post(client, "/api/buy", {"issue_id": issue_id, "qty": "5", "sign_code": "1234"})
    assert r.status_code == 200, json_of(r)
    assert _post(client, f"/api/issues/{issue_id}/finalize").status_code == 403   # не эмитент

    login_as(client, issuer)
    r = _post(client, f"/api/issues/{issue_id}/finalize")
    assert r.status_code == 200 and json_of(r)["result"] == "ISSUED"
    assert wallet(conn, issuer)["balance_rub"] == 5_000


def test_finalize_unknown_issue(client, make_user) -> None:
    login_as(client, make_user("UL"))
    assert _post(client, "/api/issues/99999/finalize").status_code == 403


def test_coupon_by_non_issuer_rejected(client, make_user, make_issue) -> None:
    issue_id = make_issue(make_user("UL"))
    login_as(client, make_user("UL"))
    r = _post(client, f"/api/issues/{issue_id}/coupon")
    assert r.status_code == 400 and "эмитент" in json_of(r)["message"]


# ------------------------------------------------------------------ админка
def test_admin_approves_and_rejects_users(client, conn: sqlite3.Connection, make_user, admin_id: int) -> None:
    a, b = make_user("FL", approve=False), make_user("FL", approve=False)
    login_as(client, admin_id)
    assert _post(client, f"/api/admin/users/{a}/approve").status_code == 200

    r = _post(client, f"/api/admin/users/{b}/reject", {"reason": ""})
    assert r.status_code == 400 and "reason" in json_of(r)["errors"]
    assert _post(client, f"/api/admin/users/{b}/reject", {"reason": "Нет СНИЛС"}).status_code == 200

    statuses = dict(conn.execute("SELECT id, status FROM users WHERE id IN (?, ?)", (a, b)).fetchall())
    assert statuses == {a: "APPROVED", b: "REJECTED"}
    assert _post(client, f"/api/admin/users/{a}/approve").status_code == 400     # повторно


def test_admin_approves_issue(client, conn: sqlite3.Connection, make_user, make_issue, admin_id: int) -> None:
    issue_id = make_issue(make_user("UL"), publish=False)
    login_as(client, admin_id)
    assert _post(client, f"/api/admin/issues/{issue_id}/approve").status_code == 200
    assert issue_row(conn, issue_id)["status"] == "SUBSCRIPTION"


def _bench(**over) -> dict:
    data = {"key_rate": "14", "deposit_rate": "13,06", "inflation": "6.35", "ndfl": "13",
            "as_of": "2026-10-23", "ofz_curve": "1 : 13,27\n2,4 : 14,84\n\n10 : 16,22", "sources": "ЦБ"}
    data.update(over)
    return data


def test_admin_benchmarks_save_and_reset(client, conn: sqlite3.Connection, admin_id: int) -> None:
    from common import DEFAULT_BENCHMARKS, get_benchmarks
    login_as(client, admin_id)
    assert _post(client, "/api/admin/benchmarks", _bench()).status_code == 200
    b = get_benchmarks(conn)
    assert b["deposit_rate"] == 13.06 and b["ofz_curve"] == [[1.0, 13.27], [2.4, 14.84], [10.0, 16.22]]

    assert _post(client, "/api/admin/benchmarks", {"reset": True}).status_code == 200
    assert get_benchmarks(conn)["key_rate"] == DEFAULT_BENCHMARKS["key_rate"]


@pytest.mark.parametrize("over, field", [
    ({"key_rate": "abc"}, "key_rate"), ({"key_rate": "101"}, "key_rate"), ({"inflation": "-21"}, "inflation"),
    ({"as_of": "вчера"}, "as_of"), ({"ofz_curve": "1 : 13"}, "ofz_curve"), ({"ofz_curve": "x : y\n1 : 2"}, "ofz_curve"),
    ({"ofz_curve": "0 : 10\n1 : 11"}, "ofz_curve"),
])
def test_admin_benchmarks_validation(client, admin_id: int, over: dict, field: str) -> None:
    login_as(client, admin_id)
    r = _post(client, "/api/admin/benchmarks", _bench(**over))
    assert r.status_code == 400 and field in json_of(r)["errors"]


# ------------------------------------------------------------------ PDF и доступ к документам
def test_public_decision_pdf(client, make_user, make_issue) -> None:
    issue_id = make_issue(make_user("UL"))
    r = client.get(f"/docs/issues/{issue_id}/decision.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")


def test_draft_decision_is_private(client, make_user, make_issue) -> None:
    issuer = make_user("UL")
    draft = make_issue(issuer, publish=False)
    assert client.get(f"/docs/issues/{draft}/decision.pdf").status_code == 403
    login_as(client, make_user("FL"))
    assert client.get(f"/docs/issues/{draft}/decision.pdf").status_code == 403
    login_as(client, issuer)
    assert client.get(f"/docs/issues/{draft}/decision.pdf").status_code == 200


def test_registry_only_for_issuer_and_admin(client, make_user, make_issue, admin_id: int) -> None:
    issuer = make_user("UL")
    issue_id = make_issue(issuer)
    login_as(client, make_user("FL"))
    assert client.get(f"/docs/issues/{issue_id}/registry.pdf").status_code == 403
    for who in (issuer, admin_id):
        login_as(client, who)
        assert client.get(f"/docs/issues/{issue_id}/registry.pdf").status_code == 200


def test_order_pdf_only_for_owner(client, conn: sqlite3.Connection, make_user, make_issue) -> None:
    issue_id = make_issue(make_user("UL"))
    owner = make_user("FL", balance=1_000)
    order_id = db.buy_cfa_on_showcase(conn, owner, issue_id, 1, "1234")["order_id"]
    login_as(client, make_user("FL"))
    assert client.get(f"/docs/orders/{order_id}.pdf").status_code == 404
    login_as(client, owner)
    assert client.get(f"/docs/orders/{order_id}.pdf").data.startswith(b"%PDF")


def test_statement_pdf(client, make_user, admin_id: int) -> None:
    assert client.get("/docs/statement.pdf").status_code == 403
    login_as(client, admin_id)
    assert client.get("/docs/statement.pdf").status_code == 403
    login_as(client, make_user("FL", balance=500))
    r = client.get("/docs/statement.pdf?download=1")
    assert r.data.startswith(b"%PDF") and r.headers["Content-Disposition"].startswith("attachment")


def test_missing_issue_document_404(client) -> None:
    assert client.get("/docs/issues/99999/decision.pdf").status_code == 404
