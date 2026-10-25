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

    # Полная анкета (все поля формы, кроме паролей) — её сверяет администратор
    anketa = {k: v for k, v in f.items() if k not in ("password", "password2", "login")}
    if role == "FL" and not errors:
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
ROV_INVESTORS = {"all": "все участники Платформы (для неквалифицированных физлиц — в пределах 600 000 ₽ в год)",
                 "qualified": "только квалифицированные инвесторы и юридические лица"}
ROV_DEFAULTS = {
    "redemption_order": "Погашение осуществляется в дату погашения путём перечисления денежных средств в размере "
                        "номинальной стоимости ЦФА и начисленного фиксированного дохода на кошелёк владельца ЦФА "
                        "на Платформе. Ранее погашенные ЦФА повторно не выпускаются.",
    "liability": "Ответственность Эмитента ограничивается суммой номинальной стоимости ЦФА и начисленного "
                 "фиксированного дохода. За просрочку исполнения обязательств Эмитент несёт ответственность "
                 "в соответствии с законодательством Российской Федерации.",
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
    rov["website"] = text("website", "Сайт", 4, 200, required=False)

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

    # Разделы 8–12
    rov["payment_method"] = f.get("payment_method", "")
    if rov["payment_method"] not in ROV_PAYMENT:
        errors["payment_method"] = "Выберите способ оплаты."
    rov["liability"] = text("liability", "Ограничение ответственности", 20, 3000, default=ROV_DEFAULTS["liability"])
    rov["investors"] = f.get("investors", "")
    if rov["investors"] not in ROV_INVESTORS:
        errors["investors"] = "Выберите круг инвесторов."
    rov["buyback"] = f.get("buyback", "no")
    if rov["buyback"] not in ("yes", "no"):
        errors["buyback"] = "Выберите вариант."
    rov["buyback_terms"] = text("buyback_terms", "Условия выкупа", 10, 2000, required=rov["buyback"] == "yes")
    rov["collateral"] = f.get("collateral", "no")
    if rov["collateral"] not in ("yes", "no"):
        errors["collateral"] = "Выберите вариант."
    rov["collateral_desc"] = text("collateral_desc", "Описание обеспечения", 10, 2000, required=rov["collateral"] == "yes")
    rov["early_redemption"] = f.get("early_redemption", "no")
    if rov["early_redemption"] not in ("yes", "no"):
        errors["early_redemption"] = "Выберите вариант."
    rov["disputes"] = text("disputes", "Порядок разрешения споров", 20, 3000, default=ROV_DEFAULTS["disputes"])

    # Раздел 13 — риски
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
        ("Организация", [("ul_company_name", "Сокращённое наименование"), ("ul_ogrn", "ОГРН / ОГРНИП"),
                         ("ul_inn_kpp", "ИНН / КПП")]),
        ("Юридический адрес", [("ul_addr_city", "Город"), ("ul_addr_street", "Улица, дом, офис")]),
        ("Представитель", [("ul_rep_fio", "ФИО и контакты"), ("ul_rep_passport", "Паспорт"),
                           ("ul_rep_inn", "ИНН"), ("ul_rep_role", "Полномочия")]),
        ("Банковские реквизиты", [("ul_bank_account", "Расчётный счёт"), ("ul_bank_bik", "БИК")]),
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
            if key == "fl_birth_date" and len(value) == 10 and value[4] == "-":
                value = f"{value[8:]}.{value[5:7]}.{value[:4]}"
            rows.append((label, value))
        out.append((section, rows))
    return out
