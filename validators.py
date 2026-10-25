#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validators.py — проверка полей формы регистрации.

Сервер — главный «судья»: даже если JavaScript в браузере отключён или подделан,
некорректные данные не попадут в базу. Те же правила продублированы в static/app.js,
чтобы подсвечивать ошибки сразу, ещё до отправки формы.

Главная функция: validate_registration(role, fields) -> (clean, errors)
  * clean  — нормализованные данные, готовые к записи в БД;
  * errors — словарь {имя_поля_формы: текст_ошибки}; пустой словарь = всё верно.
"""

from __future__ import annotations   # чтобы подсказки типов вида tuple[dict, dict] работали и на старых Python

import json
import re
from datetime import date
from pathlib import Path

# ------------------------------------------------------------------------------
# Справочник городов России (тот же файл, из которого браузер берёт подсказки).
# Источник: открытый справочник hflabs/city, 1117 городов, отсортированы по населению.
# ------------------------------------------------------------------------------
CITIES_PATH = Path(__file__).parent / "static" / "data" / "cities.json"
CITIES: set[str] = set(json.loads(CITIES_PATH.read_text(encoding="utf-8")))

# ------------------------------------------------------------------------------
# Регулярные выражения (шаблоны строк)
# ------------------------------------------------------------------------------
LOGIN_RE = re.compile(r"[a-z0-9_.-]{3,32}")                 # латиница в нижнем регистре, цифры, _ . -
_WORD = r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*"        # слово из букв, допускается дефис: «Римский-Корсаков»
FIO_RE = re.compile(rf"{_WORD}(?:\s+{_WORD}){{1,3}}")       # от 2 до 4 слов: «Иванов Иван Иванович»
REP_FIO_RE = re.compile(rf"{_WORD}\s+{_WORD}.*")            # начинается с фамилии и имени, дальше — контакты
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-zА-Яа-я]{2,}")
DEPT_CODE_RE = re.compile(r"\d{3}-\d{3}")                   # код подразделения «770-001»
# Сокращённое наименование начинается с организационно-правовой формы: ООО «Ромашка», ИП Иванов И. И.
COMPANY_RE = re.compile(r"(ООО|АО|ПАО|НАО|ЗАО|ОАО|ИП|АНО|НКО|ГУП|МУП|ФГУП|КФХ)\s+\S.*", re.IGNORECASE)
HAS_LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")
HAS_DIGIT_RE = re.compile(r"\d")

FL_STATUSES = {"unqualified", "qualified"}
UL_REP_ROLES = {"signer", "viewer"}


def digits(s: str) -> str:
    """Оставляет только цифры: '770-001 12' -> '77000112'."""
    return "".join(ch for ch in (s or "") if ch.isdigit())


def fmt_passport(p: str) -> str:
    """'4500123456' -> '45 00 № 123456' (так паспорт показывается в профиле)."""
    return f"{p[:2]} {p[2:4]} № {p[4:]}"


# ------------------------------------------------------------------------------
# Проверки отдельных полей. Каждая возвращает текст ошибки или "" (если всё хорошо).
# ------------------------------------------------------------------------------
def check_login(login: str) -> str:
    if not login:
        return "Придумайте логин."
    if not LOGIN_RE.fullmatch(login):
        return "От 3 до 32 символов: латинские буквы, цифры и знаки _ . -"
    return ""


def check_password(password: str) -> str:
    if len(password) < 8:
        return "Пароль должен быть не короче 8 символов."
    if len(password) > 64:
        return "Пароль должен быть не длиннее 64 символов."
    if not HAS_LETTER_RE.search(password) or not HAS_DIGIT_RE.search(password):
        return "Пароль должен содержать хотя бы одну букву и одну цифру."
    return ""


def check_birth_date(value: str) -> str:
    """Поле <input type="date"> присылает дату в формате ГГГГ-ММ-ДД."""
    try:
        born = date.fromisoformat(value)
    except ValueError:
        return "Укажите дату рождения полностью."
    today = date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if born > today:
        return "Дата рождения не может быть в будущем."
    if age < 18:
        return "Инвестировать на платформе можно только с 18 лет."
    if age > 120:
        return "Проверьте год рождения."
    return ""


def check_passport(value: str) -> str:
    """Серия и номер паспорта — ровно 10 цифр подряд, без пробелов и символов."""
    if not value.isdigit() or len(value) != 10:
        return "Ровно 10 цифр без пробелов и символов: серия (4) и номер (6)."
    return ""


def check_city(value: str) -> str:
    if value not in CITIES:
        return "Начните вводить название и выберите город из списка."
    return ""


def check_street(value: str) -> str:
    if len(value) < 5 or not HAS_LETTER_RE.search(value) or not HAS_DIGIT_RE.search(value):
        return "Укажите улицу и номер дома, например: ул. Ленина, д. 1."
    return ""


def normalize_phone(value: str) -> str:
    """Возвращает телефон в виде +7XXXXXXXXXX или "" если номер некорректный."""
    d = digits(value)
    if len(d) == 11 and d[0] in "78":
        d = "7" + d[1:]
    elif len(d) == 10 and d[0] == "9":
        d = "7" + d
    else:
        return ""
    return "+" + d


# ------------------------------------------------------------------------------
# Главная функция
# ------------------------------------------------------------------------------
def validate_registration(role: str, f: dict) -> tuple[dict, dict]:
    """
    role — 'FL' или 'UL'; f — словарь полей формы (строки).
    Возвращает (clean, errors).
    """
    errors: dict[str, str] = {}

    def need(name: str, message: str = "Заполните поле.") -> str:
        """Достаёт значение; если оно пустое — записывает ошибку."""
        value = f.get(name, "")
        if not value:
            errors[name] = message
        return value

    def check(name: str, value: str, checker) -> None:
        """Если поле заполнено — прогоняет его через функцию-проверку."""
        if value and (err := checker(value)):
            errors[name] = err

    # ---------- 1. Учётная запись: логин и пароль ----------
    login = f.get("login", "").lower()
    if err := check_login(login):
        errors["login"] = err

    password = f.get("password", "")
    if err := check_password(password):
        errors["password"] = err
    if not f.get("password2"):
        errors["password2"] = "Повторите пароль."
    elif f.get("password2") != password:
        errors["password2"] = "Пароли не совпадают."

    profile: dict = {}

    # ---------- 2а. Анкета физического лица ----------
    if role == "FL":
        fio = " ".join(need("fl_fio", "Укажите ФИО.").split())   # схлопываем лишние пробелы
        if fio and not FIO_RE.fullmatch(fio):
            errors["fl_fio"] = "Фамилия, имя и (при наличии) отчество — только буквы и дефис."

        check("fl_birth_date", need("fl_birth_date", "Укажите дату рождения."), check_birth_date)

        birth_place = need("fl_birth_place", "Укажите место рождения.")
        if birth_place and (len(birth_place) < 2 or not HAS_LETTER_RE.search(birth_place)):
            errors["fl_birth_place"] = "Укажите населённый пункт."

        citizenship = need("fl_citizenship", "Укажите гражданство.")
        if citizenship and len(citizenship) < 2:
            errors["fl_citizenship"] = "Слишком короткое значение."

        phone = normalize_phone(need("fl_phone", "Укажите телефон."))
        if f.get("fl_phone") and not phone:
            errors["fl_phone"] = "Российский номер: +7 и 10 цифр."

        email = need("fl_email", "Укажите e-mail.")
        if email and not EMAIL_RE.fullmatch(email):
            errors["fl_email"] = "Некорректный e-mail, пример: user@example.ru"

        passport = need("fl_passport_num", "Укажите серию и номер паспорта.")
        check("fl_passport_num", passport, check_passport)

        issued = need("fl_passport_issued")
        if issued and (not DEPT_CODE_RE.search(issued) or len(issued) < 10):
            errors["fl_passport_issued"] = "Укажите код подразделения (000-000) и кем выдан."

        reg_city = need("fl_reg_city", "Выберите город регистрации.")
        check("fl_reg_city", reg_city, check_city)
        reg_street = need("fl_reg_street", "Укажите улицу, дом и квартиру.")
        check("fl_reg_street", reg_street, check_street)

        inn = digits(need("fl_inn", "Укажите ИНН."))
        if f.get("fl_inn") and len(inn) != 12:
            errors["fl_inn"] = "ИНН физического лица — 12 цифр."

        snils = digits(need("fl_snils", "Укажите СНИЛС."))
        if f.get("fl_snils") and len(snils) != 11:
            errors["fl_snils"] = "СНИЛС — 11 цифр."

        status = f.get("fl_investor_status", "")
        if status not in FL_STATUSES:
            errors["fl_investor_status"] = "Выберите статус инвестора."

        if not errors:
            profile = dict(
                full_name=fio,
                inn=inn,
                snils_or_ogrn=f"{snils[:3]}-{snils[3:6]}-{snils[6:9]} {snils[9:]}",
                phone=phone,
                email=email,
                passport_or_rep=f"{fmt_passport(passport)}, {issued}",
                address=f"{reg_city}, {reg_street}",
                is_qualified=1 if status == "qualified" else 0,
            )

    # ---------- 2б. Анкета юридического лица ----------
    else:
        company = " ".join(need("ul_company_name", "Укажите сокращённое наименование.").split())
        if company and (not COMPANY_RE.fullmatch(company) or len(company) > 120):
            errors["ul_company_name"] = "Начните с формы собственности: ООО «Ромашка», АО «Альфа», ИП Иванов И. И."

        ogrn = digits(need("ul_ogrn", "Укажите ОГРН / ОГРНИП."))
        if f.get("ul_ogrn") and len(ogrn) not in (13, 15):
            errors["ul_ogrn"] = "ОГРН — 13 цифр, ОГРНИП — 15 цифр."

        inn = kpp = ""
        inn_kpp = need("ul_inn_kpp", "Укажите ИНН и КПП.")
        if inn_kpp:
            parts = inn_kpp.split("/")
            if len(parts) != 2:
                errors["ul_inn_kpp"] = "Формат: ИНН / КПП, например 7700000000 / 770001001."
            else:
                inn, kpp = digits(parts[0]), digits(parts[1])
                if len(inn) != 10:
                    errors["ul_inn_kpp"] = "ИНН юридического лица — 10 цифр (до знака «/»)."
                elif len(kpp) != 9:
                    errors["ul_inn_kpp"] = "КПП — 9 цифр (после знака «/»)."

        addr_city = need("ul_addr_city", "Выберите город.")
        check("ul_addr_city", addr_city, check_city)
        addr_street = need("ul_addr_street", "Укажите улицу, дом и офис.")
        check("ul_addr_street", addr_street, check_street)

        rep_fio = " ".join(need("ul_rep_fio", "Укажите ФИО представителя.").split())
        if rep_fio and not REP_FIO_RE.fullmatch(rep_fio):
            errors["ul_rep_fio"] = "Начните с фамилии и имени представителя (только буквы)."

        rep_passport = need("ul_rep_passport", "Укажите серию и номер паспорта.")
        check("ul_rep_passport", rep_passport, check_passport)

        rep_inn = need("ul_rep_inn", "Укажите ИНН представителя.")
        if rep_inn and (not rep_inn.isdigit() or len(rep_inn) != 12):
            errors["ul_rep_inn"] = "ИНН физического лица — ровно 12 цифр."

        if f.get("ul_rep_role", "") not in UL_REP_ROLES:
            errors["ul_rep_role"] = "Выберите полномочия."

        account = digits(need("ul_bank_account", "Укажите расчётный счёт."))
        if f.get("ul_bank_account") and len(account) != 20:
            errors["ul_bank_account"] = "Расчётный счёт — 20 цифр."

        bik = digits(need("ul_bank_bik", "Укажите БИК."))
        if f.get("ul_bank_bik") and (len(bik) != 9 or not bik.startswith("04")):
            errors["ul_bank_bik"] = "БИК — 9 цифр и начинается с 04."

        if not errors:
            profile = dict(
                full_name=company,
                inn=inn,
                snils_or_ogrn=f"{ogrn} / {kpp}",
                phone=None,
                email=None,
                passport_or_rep=f"{rep_fio}, паспорт {fmt_passport(rep_passport)}, ИНН {rep_inn}",
                address=f"{addr_city}, {addr_street}",
                is_qualified=1,
                bank_account=account,
                bank_bik=bik,
            )

    return {"login": login, "profile": profile}, errors
