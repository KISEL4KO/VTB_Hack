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

# ------------------------------------------------------------------------------
# Типы участников
# ------------------------------------------------------------------------------
ROLE_SHORT = {"FL": "ФЛ", "UL": "ЮЛ", "IP": "ИП", "ADMIN": "Админ"}
ROLE_FULL = {"FL": "Физическое лицо", "UL": "Юридическое лицо", "IP": "Индивидуальный предприниматель",
             "ADMIN": "Администратор"}
ISSUER_ROLES = ("UL", "IP")     # кто может выпускать ЦФА (259-ФЗ, ст. 2 ч. 3: юрлица и ИП)
PERSON_ROLES = ("FL", "IP")     # физические лица: для неквалифицированных действует лимит 600 000 ₽ в год
SIGN_PEP_ROLES = ("FL",)        # подписывают заявку кодом из СМС (ПЭП); ЮЛ и ИП — УКЭП


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


def check_ip_account(value: str) -> str:
    """Расчётный счёт ИП: 20 цифр, балансовый счёт 40802 (счета индивидуальных предпринимателей)."""
    if len(value) != 20:
        return "Расчётный счёт — 20 цифр."
    if not value.startswith("40802"):
        return "Счёт ИП начинается с 40802."
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
    role — 'FL', 'UL' или 'IP'; f — словарь полей формы (строки).
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

    # ---------- 2а. Анкета физического лица (и ИП — это тоже физлицо, плюс блок регистрации ИП) ----------
    if role in PERSON_ROLES:
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
            errors["fl_inn"] = "ИНН физического лица и ИП — 12 цифр."

        snils = digits(need("fl_snils", "Укажите СНИЛС."))
        if f.get("fl_snils") and len(snils) != 11:
            errors["fl_snils"] = "СНИЛС — 11 цифр."

        status = f.get("fl_investor_status", "")
        if status not in FL_STATUSES:
            errors["fl_investor_status"] = "Выберите статус инвестора."

        # ---------- 2б. Только для ИП: регистрация в ЕГРИП и расчётный счёт ----------
        ogrnip = account = bik = ""
        if role == "IP":
            ogrnip = digits(need("ip_ogrnip", "Укажите ОГРНИП."))
            if f.get("ip_ogrnip") and len(ogrnip) != 15:
                errors["ip_ogrnip"] = "ОГРНИП — 15 цифр."
            elif ogrnip and ogrnip[0] != "3":
                errors["ip_ogrnip"] = "ОГРНИП начинается с цифры 3."

            reg_date = need("ip_reg_date", "Укажите дату регистрации ИП.")
            if reg_date:
                try:
                    if date.fromisoformat(reg_date) > date.today():
                        errors["ip_reg_date"] = "Дата не может быть в будущем."
                except ValueError:
                    errors["ip_reg_date"] = "Укажите дату полностью."

            account = digits(need("ip_bank_account", "Укажите расчётный счёт."))
            check("ip_bank_account", account if f.get("ip_bank_account") else "", check_ip_account)
            bik = digits(need("ip_bank_bik", "Укажите БИК."))
            if f.get("ip_bank_bik") and (len(bik) != 9 or not bik.startswith("04")):
                errors["ip_bank_bik"] = "БИК — 9 цифр и начинается с 04."

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
            if role == "IP":   # у ИП вместо СНИЛС в карточке — ОГРНИП, деньги — на расчётном счёте 40802
                profile.update(full_name=f"ИП {fio}", snils_or_ogrn=ogrnip, bank_account=account, bank_bik=bik)

    # ---------- 2в. Анкета юридического лица ----------
    else:
        company = " ".join(need("ul_company_name", "Укажите сокращённое наименование.").split())
        if company and re.match(r"(ИП|КФХ)\s", company, re.IGNORECASE):
            errors["ul_company_name"] = "ИП регистрируется на вкладке «Для физ лиц» → «Да, я ИП»."
        elif company and (not COMPANY_RE.fullmatch(company) or len(company) > 120):
            errors["ul_company_name"] = "Начните с формы собственности: ООО «Ромашка», АО «Альфа», ПАО «Бета»."

        ogrn = digits(need("ul_ogrn", "Укажите ОГРН."))
        if f.get("ul_ogrn") and len(ogrn) == 15:
            errors["ul_ogrn"] = "15 цифр — это ОГРНИП. ИП регистрируется на вкладке «Для физ лиц» → «Да, я ИП»."
        elif f.get("ul_ogrn") and len(ogrn) != 13:
            errors["ul_ogrn"] = "ОГРН юридического лица — 13 цифр."

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

    # Полная анкета (все поля формы, кроме паролей) — её сверяет администратор
    anketa = {k: v for k, v in f.items() if k not in ("password", "password2", "login", "fl_is_ip")}
    if role in PERSON_ROLES and not errors:
        anketa["fl_phone"] = profile["phone"]
    return {"login": login, "profile": profile, "anketa": anketa}, errors


# ==============================================================================
# РЕШЕНИЕ О ВЫПУСКЕ ЦФА (форма эмитента по образцу РоВ ПАО Сбербанк)
# ==============================================================================
ROV_RISKS = {
    "market": "Рыночный риск",
    "liquidity": "Риск ликвидности",
    "credit": "Кредитный риск эмитента",
    "conflict": "Риск конфликта интересов",
    "operational": "Операционный (технологический) риск",
    "legal": "Правовой и налоговый риск",
    "country": "Страновой риск",
}
ROV_COUPON_PERIODS = {"maturity": "единовременно в дату погашения", "quarterly": "ежеквартально",
                      "monthly": "ежемесячно"}
ROV_PAYMENT = {"wallet": "цифровыми рублями с кошелька участника на Платформе (смарт-контракт DvP)",
               "noncash": "безналичными рублями на счёт, привязанный к кошельку участника"}
# Раздел 12 «Круг лиц»: кто может купить ЦФА выпуска
ROV_INVESTORS = {"all": "все пользователи Платформы (неквалифицированные физлица — не больше чем на 600 000 ₽ в год)",
                 "business": "только индивидуальные предприниматели и юридические лица",
                 "qualified": "только квалифицированные инвесторы (юридические лица Платформа признаёт квалифицированными)"}
ROV_INVESTORS_SHORT = {"all": "Все пользователи", "business": "Только ИП и компании",
                       "qualified": "Только квалифицированные инвесторы"}
# Значок выпуска на витрине и странице выпуска
ROV_INVESTORS_BADGE = {"all": "Для всех инвесторов", "business": "Для ИП и компаний", "qualified": "Для квал. инвесторов"}

# Раздел 10 «Обеспечение»
ROV_COLLATERAL = {"none": "Нет", "surety": "Поручительство", "pledge": "Залог", "guarantee": "Гарантия"}

# Раздел 8 «Смарт-контракт» — текст стоит сам, эмитент его не редактирует
ROV_SMART_CONTRACT = ("Размещение ЦФА, возврат денег, если выпуск не состоялся, выплаты дохода и погашение выполняет "
                      "смарт-контракт CFAPlatform в распределённом реестре Платформы автоматически.")

# Долговые ЦФА: обязательные положения Решения о выпуске (259-ФЗ ст. 3 ч. 1.2; после перехода на 282-ФЗ — ст. 7 ч. 15).
# Конструктор вставляет их в документ сам — эмитент их не пишет и не может удалить.
DEBT_LAW_REF = "259-ФЗ, ст. 3, ч. 1.2"
DEBT_CLAUSES = [
    "ЦФА являются долговыми цифровыми финансовыми активами.",
    "Изменение вида и объёма обязательств по ЦФА не допускается.",
    "Каждый ЦФА выпуска независимо от даты его приобретения удостоверяет одинаковые (единые и неизменные) вид "
    "и объём обязательств Эмитента.",
    "Если Эмитент становится обладателем выпущенных им ЦФА, записи о таких ЦФА погашаются.",
]
ROV_DEFAULTS = {
    "redemption_order": "Погашение осуществляется в дату погашения путём перечисления денежных средств в размере "
                        "номинальной стоимости ЦФА и начисленного фиксированного дохода на кошелёк владельца ЦФА "
                        "на Платформе. Ранее погашенные ЦФА повторно не выпускаются.",
    "liability": "суммой номинальной стоимости ЦФА и начисленного фиксированного дохода; за просрочку исполнения "
                 "обязательств Эмитент отвечает в соответствии с законодательством Российской Федерации.",
    "disputes": "Споры разрешаются в суде по месту нахождения Эмитента после соблюдения претензионного порядка. "
                "Срок ответа на претензию — 15 рабочих дней.",
}


def _int(f: dict, name: str, errors: dict, lo: int, hi: int, label: str, required: bool = True):
    raw = str(f.get(name, "")).strip().replace(" ", "")
    if not raw:
        if required:
            errors[name] = f"Укажите {label}."
        return None
    if not raw.isdigit():
        errors[name] = "Только целое число."
        return None
    v = int(raw)
    if v < lo or v > hi:
        errors[name] = f"Допустимо от {lo:,} до {hi:,}.".replace(",", " ")
        return None
    return v


def _date(f: dict, name: str, errors: dict, label: str):
    raw = str(f.get(name, "")).strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[name] = f"Укажите {label}."
        return None


# ------------------------------------------------------------------------------
# График выплат долговых ЦФА: те же правила, что в database_setup.coupon_schedule и analytics.js
# ------------------------------------------------------------------------------
COUPON_STEP_MONTHS = {"monthly": 1, "quarterly": 3, "maturity": 0}


def add_months(d: date, n: int) -> date:
    """31 января + 1 месяц → 28/29 февраля (как в analytics.js)."""
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days
    return date(y, m, min(d.day, last))


def coupon_periods(start: date, maturity: date, period: str) -> list[dict]:
    """Купонные периоды от даты начала начисления до погашения: [{no, start, end, days}].
    Шаг — 1 или 3 месяца; хвост короче 15 дней присоединяется к последнему периоду."""
    if maturity <= start:
        return []
    step = COUPON_STEP_MONTHS.get(period, 0)
    ends = []
    if step:
        k = 1
        while True:
            d = add_months(start, step * k)
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


def planned_schedule(rov: dict) -> list[dict]:
    """Плановый график для Решения о выпуске: если выпуск будет размещён (DvP) в последний день приёма заявок.
    Купон на 1 ЦФА = цена × ставка × дни периода / 365 (с копейками)."""
    try:
        start = date.fromisoformat(rov["placement_end"])
        maturity = date.fromisoformat(rov["maturity_date"])
        price, rate = float(rov["price_per_cfa"]), float(rov["coupon_rate"])
    except (KeyError, TypeError, ValueError):
        return []
    rows = coupon_periods(start, maturity, rov.get("coupon_period", "maturity"))
    for r in rows:
        r["coupon"] = round(price * rate / 100 * r["days"] / 365, 2)
    return rows


def pct_ru(v) -> str:
    """18.5 → «18,5», 22.0 → «22»."""
    try:
        return f"{float(v):g}".replace(".", ",")
    except (TypeError, ValueError):
        return str(v or "—")


def coupon_formula(rov: dict) -> str:
    """Формула купона с подставленными цифрами — для формы и PDF."""
    rate = pct_ru(rov.get("coupon_rate"))
    price = f"{int(rov.get('price_per_cfa') or 0):,}".replace(",", " ")
    return f"Купон на 1 ЦФА = Цена ({price} ₽) × {rate} % годовых × число дней купонного периода / 365"


def rov_summary(rov: dict) -> dict:
    """Готовые тексты разделов для показа (админка, страница выпуска, PDF).
    Понимает и новые документы (разделы 1–15), и сохранённые раньше (обеспечение да/нет, ответственность текстом)."""
    ctype = rov.get("collateral_type") or ("pledge" if rov.get("collateral") == "yes" else "none")
    if ctype == "none":
        collateral = "не предусмотрено"
    elif rov.get("collateral_subject"):
        collateral = (f"{ROV_COLLATERAL[ctype].lower()}: {rov['collateral_subject']}. Предоставляет: "
                      f"{rov.get('collateral_provider', '')}. Условия: {rov.get('collateral_terms', '')}")
    else:
        collateral = "предусмотрено: " + (rov.get("collateral_desc") or "")
    limited = rov.get("liability_limited") or ("yes" if rov.get("liability") else "no")
    if limited == "no":
        liability = "не ограничена"
    elif rov.get("liability_limited"):
        liability = "ограничена: " + rov.get("liability", "")
    else:
        liability = rov.get("liability", "")          # старый документ: раздел был готовым абзацем
    return {
        "website": rov.get("website") or ("сайта нет" if rov.get("no_website") else "—"),
        "collateral_type": ctype, "collateral_name": ROV_COLLATERAL.get(ctype, "—"), "collateral": collateral,
        "liability_limited": limited, "liability": liability,
        "investors": ROV_INVESTORS.get(rov.get("investors"), "—"),
        "investors_badge": ROV_INVESTORS_BADGE.get(rov.get("investors"), ROV_INVESTORS_BADGE["all"]),
        "guarantor": rov.get("guarantor_signer", ""),
        "smart_contract": rov.get("smart_contract") or ROV_SMART_CONTRACT,
        "debt_clauses": rov.get("debt_clauses") or DEBT_CLAUSES,
        "formula": coupon_formula(rov), "schedule": planned_schedule(rov),
    }


WEBSITE_RE = re.compile(r"^(https?://)?([\w-]+\.)+[\w-]{2,}(/\S*)?$", re.IGNORECASE)


def validate_rov(f: dict) -> tuple[dict, dict]:
    """Проверяет форму «Решения о выпуске». Возвращает (rov, errors)."""
    errors: dict[str, str] = {}
    today = date.today()

    def text(name, label, lo=3, hi=2000, required=True, default=""):
        v = " ".join(str(f.get(name, "") or default).split()) if name not in ROV_DEFAULTS else \
            str(f.get(name, "") or default).strip()
        if required and not v:
            errors[name] = f"Заполните поле «{label}»."
        elif v and (len(v) < lo or len(v) > hi):
            errors[name] = f"Длина — от {lo} до {hi} символов."
        return v

    rov: dict = {}
    # Раздел 1 — эмитент (то, чего нет в анкете)
    rov["issuer_full_name"] = text("issuer_full_name", "Полное наименование эмитента", 5, 300)
    ogrn_date = _date(f, "ogrn_date", errors, "дату присвоения ОГРН")
    if ogrn_date and ogrn_date > today:
        errors["ogrn_date"] = "Дата не может быть в будущем."
    rov["ogrn_date"] = ogrn_date.isoformat() if ogrn_date else ""
    rov["beneficiary"] = text("beneficiary", "Бенефициарный владелец", 3, 300)
    # Сайт: Решение о выпуске публикуется на сайте эмитента — поле обязательно, если не отмечено «Сайта нет»
    rov["no_website"] = str(f.get("no_website", "")).lower() in ("true", "on", "1", "yes")
    rov["website"] = "" if rov["no_website"] else text("website", "Сайт эмитента", 4, 200)
    if rov["website"] and "website" not in errors and not WEBSITE_RE.match(rov["website"]):
        errors["website"] = "Адрес сайта, например https://example.ru — или отметьте «Сайта нет»."

    # Раздел 3 — вид и объём прав
    rov["title"] = text("title", "Наименование выпуска", 3, 80)
    rov["coupon_rate"] = None
    raw_rate = str(f.get("coupon_rate", "")).strip().replace(",", ".")
    try:
        rate = float(raw_rate)
        if not 0 <= rate <= 100:
            raise ValueError
        rov["coupon_rate"] = round(rate, 2)
    except ValueError:
        errors["coupon_rate"] = "Ставка — число от 0 до 100, например 18.5."
    rov["coupon_period"] = f.get("coupon_period", "")
    if rov["coupon_period"] not in ROV_COUPON_PERIODS:
        errors["coupon_period"] = "Выберите периодичность выплаты дохода."
    maturity = _date(f, "maturity_date", errors, "дату погашения")
    rov["redemption_order"] = text("redemption_order", "Порядок погашения", 20, 3000, default=ROV_DEFAULTS["redemption_order"])

    # Раздел 4 — количество
    rov["min_qty"] = _int(f, "min_qty", errors, 1, 10_000_000, "минимальное количество")
    rov["max_qty"] = _int(f, "max_qty", errors, 1, 10_000_000, "максимальное количество")
    if rov["min_qty"] and rov["max_qty"] and rov["max_qty"] < rov["min_qty"]:
        errors["max_qty"] = "Максимум не может быть меньше минимума."
    rov["min_order_qty"] = _int(f, "min_order_qty", errors, 1, 10_000_000, "минимум в одной заявке")
    rov["max_order_qty"] = _int(f, "max_order_qty", errors, 1, 10_000_000, "максимум в одной заявке", required=False)
    if rov["min_order_qty"] and rov["max_qty"] and rov["min_order_qty"] > rov["max_qty"]:
        errors["min_order_qty"] = "Не может превышать максимальный объём выпуска."
    if rov["max_order_qty"] and rov["min_order_qty"] and rov["max_order_qty"] < rov["min_order_qty"]:
        errors["max_order_qty"] = "Не может быть меньше минимума в одной заявке."

    # Раздел 5 — цена
    rov["price_per_cfa"] = _int(f, "price_per_cfa", errors, 1, 1_000_000_000, "цену 1 ЦФА")

    # Раздел 6 — период размещения
    start = _date(f, "placement_start", errors, "дату начала размещения")
    end = _date(f, "placement_end", errors, "дату окончания размещения")
    if start and start < today:
        errors["placement_start"] = "Начало размещения — не раньше сегодняшнего дня."
    if start and end:
        if end < start:
            errors["placement_end"] = "Окончание не может быть раньше начала."
        elif (end - start).days > 366:
            errors["placement_end"] = "Период размещения — не больше года."
    if maturity:
        if end and maturity <= end:
            errors["maturity_date"] = "Погашение — позже окончания периода размещения."
        elif (maturity - today).days > 3653:
            errors["maturity_date"] = "Срок обращения — не больше 10 лет."
    rov["placement_start"] = start.isoformat() if start else ""
    rov["placement_end"] = end.isoformat() if end else ""
    rov["maturity_date"] = maturity.isoformat() if maturity else ""

    # Раздел 9 — оплата
    rov["payment_method"] = f.get("payment_method", "")
    if rov["payment_method"] not in ROV_PAYMENT:
        errors["payment_method"] = "Выберите способ оплаты."

    # Раздел 10 — обеспечение: нет / поручительство / залог / гарантия; если есть — кто, что и на каких условиях
    rov["collateral_type"] = f.get("collateral_type", "")
    if rov["collateral_type"] not in ROV_COLLATERAL:
        errors["collateral_type"] = "Выберите, есть ли обеспечение."
        rov["collateral_type"] = "none"
    has_collateral = rov["collateral_type"] != "none"
    rov["collateral_provider"] = text("collateral_provider", "Кто обеспечивает", 5, 300, required=has_collateral) \
        if has_collateral else ""
    rov["collateral_subject"] = text("collateral_subject", "Что именно", 5, 1000, required=has_collateral) \
        if has_collateral else ""
    rov["collateral_terms"] = text("collateral_terms", "Условия обеспечения", 10, 2000, required=has_collateral) \
        if has_collateral else ""
    # Совместимость с аналитикой и старыми документами: «есть обеспечение» + описание одной строкой
    rov["collateral"] = "yes" if has_collateral else "no"
    rov["collateral_desc"] = (f"{ROV_COLLATERAL[rov['collateral_type']]}: {rov['collateral_subject']}. "
                              f"Предоставляет: {rov['collateral_provider']}. Условия: {rov['collateral_terms']}"
                              if has_collateral else "")

    # Раздел 11 — ответственность: не ограничена или ограничена прямо названным способом
    rov["liability_limited"] = f.get("liability_limited", "")
    if rov["liability_limited"] not in ("no", "yes"):
        errors["liability_limited"] = "Выберите: ответственность ограничена или нет."
        rov["liability_limited"] = "no"
    rov["liability"] = text("liability", "Чем ограничена ответственность", 10, 3000) \
        if rov["liability_limited"] == "yes" else ""

    # Раздел 12 — круг лиц
    rov["investors"] = f.get("investors", "")
    if rov["investors"] not in ROV_INVESTORS:
        errors["investors"] = "Выберите, кто может купить ЦФА."

    # Разделы 13–14 — выкуп и иные условия
    rov["buyback"] = f.get("buyback", "no")
    if rov["buyback"] not in ("yes", "no"):
        errors["buyback"] = "Выберите вариант."
    rov["buyback_terms"] = text("buyback_terms", "Условия выкупа", 10, 2000, required=rov["buyback"] == "yes")
    rov["early_redemption"] = f.get("early_redemption", "no")
    if rov["early_redemption"] not in ("yes", "no"):
        errors["early_redemption"] = "Выберите вариант."
    rov["disputes"] = text("disputes", "Порядок разрешения споров", 20, 3000, default=ROV_DEFAULTS["disputes"])

    # Раздел 15 — риски и подпись
    risks = f.get("risks") or []
    if isinstance(risks, str):
        risks = [risks]
    rov["risks"] = [r for r in risks if r in ROV_RISKS]
    if len(rov["risks"]) < 3:
        errors["risks"] = "Отметьте не меньше трёх рисков, которые несёт инвестор."
    rov["risks_extra"] = text("risks_extra", "Дополнительные риски", 10, 3000, required=False)

    # Подпись
    signer = " ".join(str(f.get("signer_name", "")).split())
    if not signer:
        errors["signer_name"] = "Укажите ФИО подписанта."
    elif not FIO_RE.fullmatch(signer):
        errors["signer_name"] = "Фамилия, имя и отчество — только буквы."
    rov["signer_name"] = signer
    rov["signer_position"] = text("signer_position", "Должность подписанта", 3, 150)
    if str(f.get("confirm", "")).lower() not in ("true", "on", "1", "yes"):
        errors["confirm"] = "Подтвердите достоверность сведений."
    # Лицо, предоставившее обеспечение, тоже подписывает Решение
    if has_collateral:
        rov["guarantor_signer"] = text("guarantor_signer", "Подписант со стороны обеспечения", 5, 300)
        if str(f.get("guarantor_confirm", "")).lower() not in ("true", "on", "1", "yes"):
            errors["guarantor_confirm"] = "Нужна подпись лица, предоставившего обеспечение."
    else:
        rov["guarantor_signer"] = ""

    # Долговые ЦФА: обязательные положения и график — собираются автоматически, эмитент их не меняет
    rov["debt_clauses"] = list(DEBT_CLAUSES)
    rov["smart_contract"] = ROV_SMART_CONTRACT
    return rov, errors


# ==============================================================================
# Подписи полей анкеты — для карточки участника в админ-панели и PDF
# ==============================================================================
ANKETA_LABELS = {
    "FL": [
        ("Основные данные", [("fl_fio", "ФИО"), ("fl_birth_date", "Дата рождения"),
                             ("fl_birth_place", "Место рождения"), ("fl_citizenship", "Гражданство")]),
        ("Контакты", [("fl_phone", "Телефон"), ("fl_email", "E-mail")]),
        ("Паспорт", [("fl_passport_num", "Серия и номер"), ("fl_passport_issued", "Код подразделения, кем выдан")]),
        ("Адрес регистрации", [("fl_reg_city", "Город"), ("fl_reg_street", "Улица, дом, квартира")]),
        ("Налоговые данные и статус", [("fl_inn", "ИНН"), ("fl_snils", "СНИЛС"),
                                       ("fl_investor_status", "Статус инвестора")]),
    ],
    "UL": [
        ("Организация", [("ul_company_name", "Сокращённое наименование"), ("ul_ogrn", "ОГРН"),
                         ("ul_inn_kpp", "ИНН / КПП")]),
        ("Юридический адрес", [("ul_addr_city", "Город"), ("ul_addr_street", "Улица, дом, офис")]),
        ("Представитель", [("ul_rep_fio", "ФИО и контакты"), ("ul_rep_passport", "Паспорт"),
                           ("ul_rep_inn", "ИНН"), ("ul_rep_role", "Полномочия")]),
        ("Банковские реквизиты", [("ul_bank_account", "Расчётный счёт"), ("ul_bank_bik", "БИК")]),
    ],
    "IP": [
        ("Основные данные", [("fl_fio", "ФИО предпринимателя"), ("fl_birth_date", "Дата рождения"),
                             ("fl_birth_place", "Место рождения"), ("fl_citizenship", "Гражданство")]),
        ("Регистрация ИП", [("ip_ogrnip", "ОГРНИП"), ("ip_reg_date", "Дата регистрации"), ("fl_inn", "ИНН")]),
        ("Контакты", [("fl_phone", "Телефон"), ("fl_email", "E-mail")]),
        ("Паспорт", [("fl_passport_num", "Серия и номер"), ("fl_passport_issued", "Код подразделения, кем выдан")]),
        ("Место жительства", [("fl_reg_city", "Город"), ("fl_reg_street", "Улица, дом, квартира")]),
        ("Реквизиты и статус", [("ip_bank_account", "Расчётный счёт"), ("ip_bank_bik", "БИК"), ("fl_snils", "СНИЛС"),
                                ("fl_investor_status", "Статус инвестора")]),
    ],
}
ANKETA_VALUES = {
    "fl_investor_status": {"unqualified": "Неквалифицированный инвестор", "qualified": "Квалифицированный инвестор"},
    "ul_rep_role": {"signer": "Право подписи сделок и выпуска ЦФА (УКЭП)", "viewer": "Только просмотр"},
}


def anketa_rows(role: str, anketa: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(раздел, [(подпись, значение), …]), …] — анкета в человекочитаемом виде."""
    out = []
    for section, fields in ANKETA_LABELS.get(role, []):
        rows = []
        for key, label in fields:
            value = anketa.get(key, "") or "—"
            value = ANKETA_VALUES.get(key, {}).get(value, value)
            if key in ("fl_birth_date", "ip_reg_date") and len(value) == 10 and value[4] == "-":
                value = f"{value[8:]}.{value[5:7]}.{value[:4]}"
            rows.append((label, value))
        out.append((section, rows))
    return out
