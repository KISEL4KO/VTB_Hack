"""Валидация форм: регистрация ФЛ / ИП / ЮЛ и «Решение о выпуске», календарь купонов."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import validators as v
from conftest import TODAY, fl_form, ip_form, rov_form, ul_form


# ------------------------------------------------------------------ отдельные поля
@pytest.mark.parametrize("login, ok", [
    ("abc", True), ("a" * 32, True), ("user_1.x-y", True),
    ("ab", False), ("a" * 33, False), ("", False), ("Иван", False), ("user name", False), ("ADMIN", False),
])
def test_check_login(login: str, ok: bool) -> None:
    assert (v.check_login(login) == "") is ok


@pytest.mark.parametrize("password, ok", [
    ("abcdefg1", True),            # ровно 8
    ("a1" * 32, True),             # ровно 64
    ("abcdef1", False),            # 7
    ("a1" * 32 + "x", False),      # 65
    ("abcdefgh", False),           # без цифры
    ("12345678", False),           # без буквы
    ("пароль12", True),            # кириллица — тоже буквы
])
def test_check_password_bounds(password: str, ok: bool) -> None:
    assert (v.check_password(password) == "") is ok


def _years_ago(n: int, extra_days: int = 0) -> str:
    d = TODAY.replace(year=TODAY.year - n) if not (TODAY.month == 2 and TODAY.day == 29) else date(TODAY.year - n, 2, 28)
    return (d + timedelta(days=extra_days)).isoformat()


@pytest.mark.parametrize("value, ok", [
    (_years_ago(18), True),                    # 18 лет исполняется сегодня
    (_years_ago(18, extra_days=1), False),     # 18 исполнится завтра
    (_years_ago(120), True),
    (_years_ago(121), False),
    ((TODAY + timedelta(days=1)).isoformat(), False),
    ("17.05.1990", False), ("", False), ("1990-02-30", False),
])
def test_check_birth_date(value: str, ok: bool) -> None:
    assert (v.check_birth_date(value) == "") is ok


@pytest.mark.parametrize("value, ok", [("4500123456", True), ("450012345", False), ("45001234567", False),
                                       ("45 00 123456", False), ("45001234ab", False), ("", False)])
def test_check_passport(value: str, ok: bool) -> None:
    assert (v.check_passport(value) == "") is ok


@pytest.mark.parametrize("raw, expected", [
    ("+7 (900) 123-45-67", "+79001234567"), ("89001234567", "+79001234567"), ("9001234567", "+79001234567"),
    ("79001234567", "+79001234567"), ("+1 900 123 45 67", ""), ("12345", ""), ("", ""), ("8900123456", ""),
])
def test_normalize_phone(raw: str, expected: str) -> None:
    assert v.normalize_phone(raw) == expected


@pytest.mark.parametrize("acc, ok", [("40802810000000000001", True), ("40702810000000000001", False),
                                     ("4080281000000000000", False)])
def test_check_ip_account(acc: str, ok: bool) -> None:
    assert (v.check_ip_account(acc) == "") is ok


def test_city_must_be_from_directory() -> None:
    assert v.check_city("г. Москва") == ""
    assert v.check_city("Москва") != ""
    assert v.check_city("Нарния") != ""


def test_digits_and_passport_format() -> None:
    assert v.digits("770-001 12") == "77000112"
    assert v.digits(None) == ""  # type: ignore[arg-type]
    assert v.fmt_passport("4500123456") == "45 00 № 123456"


# ------------------------------------------------------------------ регистрация
@pytest.mark.parametrize("role, form", [("FL", fl_form), ("IP", ip_form), ("UL", ul_form)])
def test_registration_happy_path(role: str, form) -> None:
    clean, errors = v.validate_registration(role, form(1))
    assert errors == {}
    p = clean["profile"]
    assert p["inn"] and p["full_name"] and p["address"].startswith("г. Москва")
    assert "password" not in clean["anketa"] and "password2" not in clean["anketa"]


def test_registration_normalizes_values() -> None:
    clean, errors = v.validate_registration("FL", fl_form(2, login="MixedCase", fl_fio="  Иванов   Иван  "))
    assert not errors
    assert clean["login"] == "mixedcase"
    assert clean["profile"]["full_name"] == "Иванов Иван"
    assert clean["profile"]["phone"].startswith("+7") and len(clean["profile"]["phone"]) == 12
    assert clean["anketa"]["fl_phone"] == clean["profile"]["phone"]


def test_ip_profile_specifics() -> None:
    clean, _ = v.validate_registration("IP", ip_form(3))
    p = clean["profile"]
    assert p["full_name"].startswith("ИП ")
    assert p["snils_or_ogrn"].startswith("3") and len(p["snils_or_ogrn"]) == 15
    assert p["bank_account"].startswith("40802")


def test_ul_is_always_qualified() -> None:
    clean, _ = v.validate_registration("UL", ul_form(4))
    assert clean["profile"]["is_qualified"] == 1


def test_registration_empty_form_reports_every_required_field() -> None:
    _, errors = v.validate_registration("FL", {})
    for field in ("login", "password", "password2", "fl_fio", "fl_birth_date", "fl_phone", "fl_email",
                  "fl_passport_num", "fl_reg_city", "fl_inn", "fl_snils", "fl_investor_status"):
        assert field in errors, field


@pytest.mark.parametrize("over, field", [
    ({"password2": "Other12345"}, "password2"),
    ({"fl_fio": "Иванов"}, "fl_fio"),                       # одно слово
    ({"fl_fio": "Ivanov 1van"}, "fl_fio"),
    ({"fl_email": "user@@example"}, "fl_email"),
    ({"fl_inn": "1234567890"}, "fl_inn"),                   # 10 цифр — это ИНН юрлица
    ({"fl_snils": "123"}, "fl_snils"),
    ({"fl_passport_issued": "ОВД района"}, "fl_passport_issued"),  # без кода подразделения
    ({"fl_reg_street": "Ленина"}, "fl_reg_street"),         # нет номера дома
    ({"fl_phone": "123"}, "fl_phone"),
    ({"fl_investor_status": "vip"}, "fl_investor_status"),
])
def test_fl_registration_field_errors(over: dict, field: str) -> None:
    _, errors = v.validate_registration("FL", fl_form(5, **over))
    assert field in errors
    assert set(errors) == {field}, "ошибка должна подсвечивать только своё поле"


@pytest.mark.parametrize("over, field", [
    ({"ip_ogrnip": "1" * 15}, "ip_ogrnip"),                 # ОГРНИП начинается с 3
    ({"ip_ogrnip": "3" * 13}, "ip_ogrnip"),
    ({"ip_reg_date": (TODAY + timedelta(days=1)).isoformat()}, "ip_reg_date"),
    ({"ip_bank_account": "40702810000000000001"}, "ip_bank_account"),
    ({"ip_bank_bik": "123456789"}, "ip_bank_bik"),
])
def test_ip_registration_field_errors(over: dict, field: str) -> None:
    _, errors = v.validate_registration("IP", ip_form(6, **over))
    assert field in errors


@pytest.mark.parametrize("over, field", [
    ({"ul_company_name": "Ромашка"}, "ul_company_name"),            # без ОПФ
    ({"ul_company_name": "ИП Иванов И. И."}, "ul_company_name"),    # ИП — не на этой вкладке
    ({"ul_ogrn": "3" * 15}, "ul_ogrn"),                              # 15 цифр — ОГРНИП
    ({"ul_inn_kpp": "7700000000"}, "ul_inn_kpp"),                    # нет «/»
    ({"ul_inn_kpp": "77000000 / 770101001"}, "ul_inn_kpp"),
    ({"ul_inn_kpp": "7700000000 / 7701"}, "ul_inn_kpp"),
    ({"ul_rep_inn": "77000000000"}, "ul_rep_inn"),
    ({"ul_rep_role": "owner"}, "ul_rep_role"),
    ({"ul_bank_account": "123"}, "ul_bank_account"),
])
def test_ul_registration_field_errors(over: dict, field: str) -> None:
    _, errors = v.validate_registration("UL", ul_form(7, **over))
    assert field in errors


# ------------------------------------------------------------------ Решение о выпуске
def test_rov_happy_path() -> None:
    rov, errors = v.validate_rov(rov_form())
    assert errors == {}
    assert rov["price_per_cfa"] == 1000 and rov["coupon_rate"] == 18.5
    assert rov["max_order_qty"] is None
    assert rov["debt_clauses"] == v.DEBT_CLAUSES               # обязательные положения 259-ФЗ вставлены
    assert rov["redemption_order"] == v.ROV_DEFAULTS["redemption_order"]


def test_rov_accepts_comma_rate_and_spaced_numbers() -> None:
    rov, errors = v.validate_rov(rov_form(coupon_rate="12,75", max_qty="1 000 000"))
    assert not errors
    assert rov["coupon_rate"] == 12.75 and rov["max_qty"] == 1_000_000


@pytest.mark.parametrize("rate, ok", [("0", True), ("100", True), ("100.01", False), ("-1", False), ("abc", False)])
def test_rov_coupon_rate_bounds(rate: str, ok: bool) -> None:
    _, errors = v.validate_rov(rov_form(coupon_rate=rate))
    assert ("coupon_rate" not in errors) is ok


@pytest.mark.parametrize("over, field", [
    ({"max_qty": "5", "min_qty": "10"}, "max_qty"),
    ({"min_qty": "0"}, "min_qty"),
    ({"max_qty": "10000001"}, "max_qty"),
    ({"min_order_qty": "2000"}, "min_order_qty"),
    ({"min_order_qty": "10", "max_order_qty": "5"}, "max_order_qty"),
    ({"price_per_cfa": "0"}, "price_per_cfa"),
    ({"price_per_cfa": "10.5"}, "price_per_cfa"),
    ({"placement_start": (TODAY - timedelta(days=1)).isoformat()}, "placement_start"),
    ({"placement_end": (TODAY - timedelta(days=1)).isoformat()}, "placement_end"),
    ({"placement_end": (TODAY + timedelta(days=367)).isoformat(),
      "maturity_date": (TODAY + timedelta(days=800)).isoformat()}, "placement_end"),
    ({"maturity_date": (TODAY + timedelta(days=30)).isoformat()}, "maturity_date"),   # = окончанию размещения
    ({"maturity_date": (TODAY + timedelta(days=3654)).isoformat()}, "maturity_date"),
    ({"risks": ["market", "credit"]}, "risks"),
    ({"risks": ["market", "credit", "unknown"]}, "risks"),
    ({"signer_name": "Директор"}, "signer_name"),
    ({"confirm": ""}, "confirm"),
    ({"investors": "everyone"}, "investors"),
    ({"coupon_period": "weekly"}, "coupon_period"),
    ({"website": "not a site"}, "website"),
    ({"liability_limited": "yes", "liability": ""}, "liability"),
    ({"buyback": "yes", "buyback_terms": ""}, "buyback_terms"),
])
def test_rov_field_errors(over: dict, field: str) -> None:
    _, errors = v.validate_rov(rov_form(**over))
    assert field in errors


def test_rov_no_website_flag_skips_site() -> None:
    rov, errors = v.validate_rov(rov_form(website="", no_website="on"))
    assert not errors and rov["website"] == ""


def test_rov_collateral_requires_details_and_guarantor_signature() -> None:
    _, errors = v.validate_rov(rov_form(collateral_type="pledge"))
    assert {"collateral_provider", "collateral_subject", "collateral_terms",
            "guarantor_signer", "guarantor_confirm"} <= set(errors)

    rov, errors = v.validate_rov(rov_form(
        collateral_type="pledge", collateral_provider="ООО «Залогодатель»", collateral_subject="Склад в Химках",
        collateral_terms="Залог действует до полного погашения", guarantor_signer="Петров П. П., директор",
        guarantor_confirm="true"))
    assert not errors
    assert rov["collateral"] == "yes" and rov["collateral_desc"].startswith("Залог")


def test_rov_risks_single_string_is_accepted_as_list() -> None:
    _, errors = v.validate_rov(rov_form(risks="market"))
    assert "risks" in errors          # одного риска мало, но строка не роняет валидатор


# ------------------------------------------------------------------ календарь купонов
@pytest.mark.parametrize("d, n, expected", [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2028, 1, 31), 1, date(2028, 2, 29)),       # високосный год
    (date(2026, 11, 30), 3, date(2027, 2, 28)),
    (date(2026, 12, 15), 1, date(2027, 1, 15)),       # переход через год
    (date(2026, 5, 10), 0, date(2026, 5, 10)),
])
def test_add_months(d: date, n: int, expected: date) -> None:
    assert v.add_months(d, n) == expected


def test_coupon_periods_quarterly_cover_whole_term_without_gaps() -> None:
    start, maturity = date(2026, 1, 1), date(2027, 1, 1)
    periods = v.coupon_periods(start, maturity, "quarterly")
    assert [p["no"] for p in periods] == [1, 2, 3, 4]
    assert periods[0]["start"] == start.isoformat() and periods[-1]["end"] == maturity.isoformat()
    assert all(a["end"] == b["start"] for a, b in zip(periods, periods[1:]))
    assert sum(p["days"] for p in periods) == (maturity - start).days


def test_coupon_periods_short_tail_merges_into_last_period() -> None:
    periods = v.coupon_periods(date(2026, 1, 1), date(2026, 4, 10), "monthly")   # хвост 9 дней
    assert len(periods) == 3
    assert periods[-1] == {"no": 3, "start": "2026-03-01", "end": "2026-04-10", "days": 40}


@pytest.mark.parametrize("period", ["maturity", "unknown"])
def test_coupon_periods_single_payment(period: str) -> None:
    periods = v.coupon_periods(date(2026, 1, 1), date(2026, 12, 31), period)
    assert len(periods) == 1 and periods[0]["days"] == 364


@pytest.mark.parametrize("maturity", [date(2026, 1, 1), date(2025, 12, 31)])
def test_coupon_periods_empty_when_maturity_not_after_start(maturity: date) -> None:
    assert v.coupon_periods(date(2026, 1, 1), maturity, "monthly") == []


def test_planned_schedule_coupon_per_cfa_and_bad_input() -> None:
    rows = v.planned_schedule({"placement_end": "2026-01-01", "maturity_date": "2027-01-01",
                               "price_per_cfa": 1000, "coupon_rate": 18.25, "coupon_period": "maturity"})
    assert rows[0]["coupon"] == 182.5
    assert v.planned_schedule({}) == []
    assert v.planned_schedule({"placement_end": "bad", "maturity_date": "2027-01-01",
                               "price_per_cfa": 1, "coupon_rate": 1}) == []


@pytest.mark.parametrize("value, expected", [(18.5, "18,5"), (22.0, "22"), ("x", "x"), (None, "—")])
def test_pct_ru(value, expected: str) -> None:
    assert v.pct_ru(value) == expected


def test_rov_summary_supports_legacy_documents() -> None:
    legacy = {"collateral": "yes", "collateral_desc": "Залог склада", "liability": "Номиналом", "investors": "business"}
    s = v.rov_summary(legacy)
    assert s["collateral_type"] == "pledge" and s["collateral"].endswith("Залог склада")
    assert s["liability"] == "Номиналом"
    assert s["investors_badge"] == v.ROV_INVESTORS_BADGE["business"]


def test_anketa_rows_formats_dates_and_dictionary_values() -> None:
    rows = dict(v.anketa_rows("FL", {"fl_birth_date": "1990-05-17", "fl_investor_status": "qualified"}))
    main_data = dict(rows["Основные данные"])
    assert main_data["Дата рождения"] == "17.05.1990"
    assert dict(rows["Налоговые данные и статус"])["Статус инвестора"] == "Квалифицированный инвестор"
    assert main_data["ФИО"] == "—"
    assert v.anketa_rows("ADMIN", {}) == []
