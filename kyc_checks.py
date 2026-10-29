#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kyc_checks.py — автоматическая сверка анкеты участника для администратора.

Решение о допуске принимает человек, а этот модуль подсказывает, на что смотреть:
контрольные суммы реквизитов (ИНН, СНИЛС, ОГРН, ключ расчётного счёта), возраст,
дубли паспорта / телефона / e-mail / счёта у других участников.

Каждая проверка возвращает словарь: {"title", "status": "ok" | "warn" | "fail", "detail"}.
  ok   — всё сходится;
  warn — стоит присмотреться (например, контрольная сумма не сходится — возможно, тестовые данные);
  fail — серьёзное препятствие (например, участнику меньше 18 лет или паспорт уже используется).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from validators import CITIES, digits


# ------------------------------------------------------------------------------
# Контрольные суммы российских реквизитов
# ------------------------------------------------------------------------------
def inn_valid(inn: str) -> bool:
    """ИНН: 10 цифр (ЮЛ) или 12 цифр (ФЛ, ИП) с контрольными разрядами."""
    d = [int(c) for c in inn]

    def ctrl(coeffs):
        return sum(c * x for c, x in zip(coeffs, d)) % 11 % 10

    if len(d) == 10:
        return ctrl([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(d) == 12:
        return (ctrl([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10]
                and ctrl([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11])
    return False


def snils_valid(snils: str) -> bool:
    """СНИЛС: 9 цифр номера + 2 контрольные (проверяется для номеров больше 001-001-998)."""
    if len(snils) != 11:
        return False
    number, control = snils[:9], int(snils[9:])
    if int(number) <= 1001998:
        return True
    s = sum(int(x) * (9 - i) for i, x in enumerate(number))
    if s < 100:
        cs = s
    elif s in (100, 101):
        cs = 0
    else:
        cs = s % 101
        if cs == 100:
            cs = 0
    return cs == control


def ogrn_valid(ogrn: str) -> bool:
    """ОГРН (13 цифр) или ОГРНИП (15 цифр)."""
    if len(ogrn) == 13:
        return int(ogrn[:12]) % 11 % 10 == int(ogrn[12])
    if len(ogrn) == 15:
        return int(ogrn[:14]) % 13 % 10 == int(ogrn[14])
    return False


def account_key_valid(account: str, bik: str) -> bool:
    """Ключ расчётного счёта по БИК: 3 последние цифры БИК + 20 цифр счёта, веса 7-1-3."""
    if len(account) != 20 or len(bik) != 9:
        return False
    number = bik[-3:] + account
    weights = [7, 1, 3] * 8
    return sum(int(n) * w % 10 for n, w in zip(number, weights)) % 10 == 0


# ------------------------------------------------------------------------------
# Сборка списка проверок
# ------------------------------------------------------------------------------
def _item(title: str, ok: bool, detail_ok: str, detail_bad: str, bad: str = "warn") -> dict:
    return {"title": title, "status": "ok" if ok else bad, "detail": detail_ok if ok else detail_bad}


def _dupes(conn: sqlite3.Connection, user_id: int, column: str, value: str) -> list[str]:
    if not value:
        return []
    rows = conn.execute(
        f"SELECT full_name, login FROM users WHERE {column} = ? AND id != ? AND role != 'ADMIN'",
        (value, user_id)).fetchall()
    return [f"{r['full_name']} ({r['login']})" for r in rows]


def run_checks(conn: sqlite3.Connection, user) -> list[dict]:
    """Все проверки для карточки участника в админ-панели."""
    a = json.loads(user["profile_json"] or "{}")
    checks: list[dict] = []
    inn = user["inn"] or ""

    if user["role"] in ("FL", "IP"):   # ИП — физлицо: те же проверки личных данных плюс блок ИП ниже
        checks.append(_item("Контрольная сумма ИНН", inn_valid(inn),
                            f"ИНН {inn} корректен", f"ИНН {inn} не проходит проверку контрольных цифр"))
        snils = digits(a.get("fl_snils", ""))
        checks.append(_item("Контрольная сумма СНИЛС", snils_valid(snils),
                            "СНИЛС корректен", f"СНИЛС {snils} не проходит проверку контрольного числа"))
        try:
            born = date.fromisoformat(a.get("fl_birth_date", ""))
            today = date.today()
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            checks.append(_item("Возраст", age >= 18, f"{age} лет", f"{age} лет — младше 18", bad="fail"))
        except ValueError:
            checks.append({"title": "Возраст", "status": "warn", "detail": "Дата рождения не указана"})
        checks.append(_item("Город регистрации в справочнике", a.get("fl_reg_city") in CITIES,
                            a.get("fl_reg_city", ""), "Города нет в справочнике"))
        passport = digits(a.get("fl_passport_num", ""))
        dup = _dupes_json(conn, user["id"], PASSPORT_KEYS, passport)
        checks.append(_item("Паспорт не используется другими участниками", not dup,
                            "Совпадений нет", "Тот же паспорт у: " + ", ".join(dup), bad="fail"))
        dup = _dupes(conn, user["id"], "phone", user["phone"])
        checks.append(_item("Телефон уникален", not dup, user["phone"] or "", "Тот же телефон у: " + ", ".join(dup)))
        dup = _dupes(conn, user["id"], "email", user["email"])
        checks.append(_item("E-mail уникален", not dup, user["email"] or "", "Тот же e-mail у: " + ", ".join(dup)))
        checks.append({"title": "Статус инвестора", "status": "ok",
                       "detail": "Квалифицированный — проверьте подтверждающие документы" if user["is_qualified"]
                       else "Неквалифицированный — лимит 600 000 ₽ в год"})
        if user["role"] == "IP":
            ogrnip = digits(a.get("ip_ogrnip", ""))
            checks.append(_item("Контрольная сумма ОГРНИП", ogrn_valid(ogrnip) and ogrnip[:1] == "3",
                                f"ОГРНИП {ogrnip} корректен", f"ОГРНИП {ogrnip} не проходит проверку контрольной цифры"))
            checks.append(_item("ОГРНИП и ИНН из одного региона", ogrnip[3:5] == inn[:2] if ogrnip and inn else False,
                                f"Регион {inn[:2]}", f"ИНН выдан в регионе {inn[:2]}, ИП зарегистрирован в регионе "
                                f"{ogrnip[3:5]} — уточните, не переезжал ли предприниматель"))
            account, bik = digits(a.get("ip_bank_account", "")), digits(a.get("ip_bank_bik", ""))
            checks.append(_item("Ключ расчётного счёта по БИК", account_key_valid(account, bik),
                                "Счёт согласован с БИК", f"Счёт {account} не сходится с БИК {bik}"))
            checks.append(_item("Счёт индивидуального предпринимателя (40802)", account[:5] == "40802",
                                f"Счёт {account[:5]}…", f"Счёт начинается с {account[:5]} — у ИП должен быть 40802"))
            checks.append({"title": "Право выпуска ЦФА", "status": "ok",
                           "detail": "ИП вправе выпускать ЦФА (259-ФЗ, ст. 2 ч. 3); Решение о выпуске подписывает сам предприниматель"})
    else:
        checks.append(_item("Контрольная сумма ИНН организации", inn_valid(inn),
                            f"ИНН {inn} корректен", f"ИНН {inn} не проходит проверку контрольной цифры"))
        ogrn = digits(a.get("ul_ogrn", ""))
        checks.append(_item("Контрольная сумма ОГРН", ogrn_valid(ogrn),
                            f"ОГРН {ogrn} корректен", f"ОГРН {ogrn} не проходит проверку контрольной цифры"))
        kpp = digits((a.get("ul_inn_kpp", "") + "/").split("/")[1])
        checks.append(_item("КПП согласован с ИНН (код региона)", kpp[:2] == inn[:2] if kpp and inn else False,
                            f"Регион {inn[:2]} совпадает", f"ИНН начинается с {inn[:2]}, КПП — с {kpp[:2]}"))
        account, bik = digits(a.get("ul_bank_account", "")), digits(a.get("ul_bank_bik", ""))
        checks.append(_item("Ключ расчётного счёта по БИК", account_key_valid(account, bik),
                            "Счёт согласован с БИК", f"Счёт {account} не сходится с БИК {bik}"))
        checks.append(_item("Счёт организации (40702 / 40701 / 40802)", account[:5] in ("40702", "40701", "40802"),
                            f"Счёт {account[:5]}…", f"Счёт начинается с {account[:5]} — нетипично для организации"))
        rep_inn = digits(a.get("ul_rep_inn", ""))
        checks.append(_item("ИНН представителя", inn_valid(rep_inn),
                            f"ИНН {rep_inn} корректен", f"ИНН {rep_inn} не проходит проверку"))
        checks.append(_item("Город юридического адреса в справочнике", a.get("ul_addr_city") in CITIES,
                            a.get("ul_addr_city", ""), "Города нет в справочнике"))
        role = a.get("ul_rep_role")
        checks.append({"title": "Полномочия представителя",
                       "status": "ok" if role == "signer" else "warn",
                       "detail": "Право подписи сделок и выпуска ЦФА (УКЭП)" if role == "signer"
                       else "Только просмотр — выпускать ЦФА представитель не вправе"})
    return checks


PASSPORT_KEYS = ("fl_passport_num",)   # у физлиц и ИП паспорт хранится в одном поле анкеты


def _dupes_json(conn: sqlite3.Connection, user_id: int, keys, value: str) -> list[str]:
    """Поиск дублей по полю (или нескольким полям), которое хранится только в анкете (profile_json)."""
    if not value:
        return []
    keys = (keys,) if isinstance(keys, str) else keys
    found = []
    for r in conn.execute("SELECT id, full_name, login, profile_json FROM users WHERE id != ? AND profile_json IS NOT NULL",
                          (user_id,)):
        a = json.loads(r["profile_json"])
        if any(digits(a.get(k, "")) == value for k in keys):
            found.append(f"{r['full_name']} ({r['login']})")
    return found


def summary(checks: list[dict]) -> str:
    """Итог для списка заявок: ok / warn / fail."""
    statuses = {c["status"] for c in checks}
    return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "ok"
