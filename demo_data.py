#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_data.py — демо-наполнение платформы для показа: `flask --app main seed-demo`.

Все участники и выпуски вымышленные. Реквизиты у «хороших» участников — с правильными
контрольными суммами, у части заявок специально допущены ошибки, чтобы показать
автоматическую сверку в админ-панели.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import database_setup as db
import validators

PASSWORD = "Demo2026pass"
ADMIN_PASSWORD = "Admin2026pass"

SUMMARY = f"""
Демо-данные созданы. Пароль у всех участников: {PASSWORD}

  Роль                          Логин            Статус
  ----------------------------  ---------------  ---------------------------------------
  Администратор                 admin            пароль: {ADMIN_PASSWORD}
  Эмитент (ЮЛ)                  demo_issuer      допущен, 3 выпуска + 1 на модерации
  Инвестор-физлицо              demo_investor    допущен, есть ЦФА и заявки
  Инвестор-юрлицо               demo_fund        допущен, есть ЦФА
  Эмитент и инвестор (ИП)       demo_ip          допущен, свой выпуск со сбором заявок + ЦФА в заявке
  Новая анкета (ФЛ)             new_anna         ждёт проверки — данные корректны
  Новая анкета (ЮЛ)             new_company      ждёт проверки — ошибки в ИНН и счёте
  Новая анкета (ФЛ)             new_ivan         ждёт проверки — паспорт уже используется

Запустите сервер: python main.py  →  http://127.0.0.1:5000
"""


def _fl(login, fio, inn, snils, passport, phone, email, birth, city, street, qualified=False):
    anketa = {
        "fl_fio": fio, "fl_birth_date": birth, "fl_birth_place": "г. Москва", "fl_citizenship": "РФ",
        "fl_phone": phone, "fl_email": email, "fl_passport_num": passport,
        "fl_passport_issued": "770-001, ГУ МВД России по г. Москве", "fl_reg_city": city, "fl_reg_street": street,
        "fl_inn": inn, "fl_snils": snils, "fl_investor_status": "qualified" if qualified else "unqualified",
    }
    profile = dict(full_name=fio, inn=inn, snils_or_ogrn=f"{snils[:3]}-{snils[3:6]}-{snils[6:9]} {snils[9:]}",
                   phone=phone, email=email, passport_or_rep=f"{validators.fmt_passport(passport)}, "
                   f"{anketa['fl_passport_issued']}", address=f"{city}, {street}", is_qualified=int(qualified))
    return dict(role="FL", login=login, profile=profile, anketa=anketa, account="40817810" + "0" * 11 + "1",
                bik="044525225", wallet="Основной кошелёк ФЛ")


def _ul(login, name, inn, kpp, ogrn, account, rep_fio, rep_passport, rep_inn, city, street):
    anketa = {
        "ul_company_name": name, "ul_ogrn": ogrn, "ul_inn_kpp": f"{inn} / {kpp}", "ul_addr_city": city,
        "ul_addr_street": street, "ul_rep_fio": rep_fio, "ul_rep_passport": rep_passport, "ul_rep_inn": rep_inn,
        "ul_rep_role": "signer", "ul_bank_account": account, "ul_bank_bik": "044525225",
    }
    profile = dict(full_name=name, inn=inn, snils_or_ogrn=f"{ogrn} / {kpp}", phone=None, email=None,
                   passport_or_rep=f"{rep_fio}, паспорт {validators.fmt_passport(rep_passport)}, ИНН {rep_inn}",
                   address=f"{city}, {street}", is_qualified=1)
    return dict(role="UL", login=login, profile=profile, anketa=anketa, account=account, bik="044525225",
                wallet="Корпоративный кошелёк")


def _ip(login, fio, inn, snils, ogrnip, reg_date, account, passport, phone, email, birth, city, street, qualified=False):
    """ИП регистрируется на вкладке физлица («Да, я ИП»): личная анкета fl_* + блок регистрации ИП ip_*."""
    anketa = {
        "fl_fio": fio, "fl_birth_date": birth, "fl_birth_place": city, "fl_citizenship": "РФ",
        "fl_phone": phone, "fl_email": email, "fl_passport_num": passport,
        "fl_passport_issued": "770-002, ГУ МВД России по г. Москве", "fl_reg_city": city, "fl_reg_street": street,
        "fl_inn": inn, "fl_snils": snils, "fl_investor_status": "qualified" if qualified else "unqualified",
        "ip_ogrnip": ogrnip, "ip_reg_date": reg_date, "ip_bank_account": account, "ip_bank_bik": "044525225",
    }
    profile = dict(full_name=f"ИП {fio}", inn=inn, snils_or_ogrn=ogrnip, phone=phone, email=email,
                   passport_or_rep=f"{validators.fmt_passport(passport)}, {anketa['fl_passport_issued']}",
                   address=f"{city}, {street}", is_qualified=int(qualified))
    return dict(role="IP", login=login, profile=profile, anketa=anketa, account=account, bik="044525225",
                wallet="Кошелёк ИП")


def _register(conn, p) -> int:
    return db.register_user(conn, role=p["role"], login=p["login"], password_hash=generate_password_hash(PASSWORD),
                            profile=p["profile"], profile_json=p["anketa"], evm_address="0x" + secrets.token_hex(20),
                            wallet_name=p["wallet"], bank_account=p["account"], bank_bik=p["bik"])["user_id"]


def _rov(title, price, min_qty, max_qty, rate, investors="all", days_to_maturity=400, **extra):
    today = date.today()
    raw = {
        "issuer_full_name": "Общество с ограниченной ответственностью «Демо-Лизинг»",
        "ogrn_date": "2018-03-14", "beneficiary": "Иванов Сергей Петрович (100 % долей)",
        "website": "demo-leasing.example", "title": title, "price_per_cfa": str(price), "coupon_rate": str(rate),
        "coupon_period": "quarterly", "min_qty": str(min_qty), "max_qty": str(max_qty), "min_order_qty": "1",
        "max_order_qty": "", "placement_start": today.isoformat(),
        "placement_end": (today + timedelta(days=30)).isoformat(),
        "maturity_date": (today + timedelta(days=days_to_maturity)).isoformat(),
        "payment_method": "wallet", "investors": investors, "buyback": "no", "collateral": "no",
        "early_redemption": "no", "risks": ["market", "liquidity", "credit", "operational", "legal"],
        "signer_name": "Смирнов Алексей Викторович", "signer_position": "Генеральный директор", "confirm": "true",
        **validators.ROV_DEFAULTS,
        **extra,
    }
    rov, errors = validators.validate_rov(raw)
    assert not errors, errors
    return rov


def seed(conn, chain) -> None:
    admin_id = db.create_admin(conn, "admin", generate_password_hash(ADMIN_PASSWORD), "Администратор платформы")

    issuer = _register(conn, _ul("demo_issuer", "ООО «Демо-Лизинг»", "7700000009", "770001001", "1187700000003",
                                 "40702810200000000001", "Смирнов Алексей Викторович", "4510654321", "770000000082",
                                 "г. Москва", "Пресненская наб., д. 10, оф. 5"))
    fund = _register(conn, _ul("demo_fund", "ПАО «Тест-Инвест»", "7710000008", "771001001", "1197700000002",
                               "40702810500000000002", "Орлова Дарья Сергеевна", "4512111222", "770300000051",
                               "г. Санкт-Петербург", "Невский пр., д. 1"))
    investor = _register(conn, _fl("demo_investor", "Петров Пётр Петрович", "770100000079", "12345678964",
                                   "4500123456", "+79990000001", "investor@example.ru", "1995-04-12",
                                   "г. Казань, Республика Татарстан", "ул. Баумана, д. 5, кв. 12"))
    ip = _register(conn, _ip("demo_ip", "Волков Андрей Николаевич", "772400001214", "22334455639", "319770000123451", "2019-06-03",
                             "40802810800000000123", "4511223344", "+79990000005", "ip@example.ru", "1986-11-02",
                             "г. Москва", "ул. Профсоюзная, д. 12, кв. 40"))
    for uid in (issuer, fund, investor, ip):
        db.approve_user(conn, chain, admin_id, uid, "Анкета проверена, реквизиты сходятся")

    # Заявки, которые ждут проверки — для демонстрации админ-панели
    _register(conn, _fl("new_anna", "Сидорова Анна Сергеевна", "500100000015", "98765432183", "4600987654",
                        "+79990000002", "anna@example.ru", "1998-09-30", "г. Новосибирск, Новосибирская область",
                        "Красный пр., д. 25, кв. 7"))
    _register(conn, _ul("new_company", "ООО «Новый Эмитент»", "7720000000", "500101001", "1187700000004",
                        "40702810000000000003", "Кузнецов Олег Игоревич", "4519000111", "770000000000",
                        "г. Екатеринбург, Свердловская область", "ул. Ленина, д. 50"))
    _register(conn, _fl("new_ivan", "Козлов Иван Андреевич", "770200000065", "11223344595", "4500123456",
                        "+79990000001", "ivan@example.ru", "1990-01-20", "г. Москва", "ул. Арбат, д. 10"))

    # Деньги на кошельках
    db.deposit_wallet(conn, investor, 500_000, chain)
    db.deposit_wallet(conn, fund, 5_000_000, chain)
    db.deposit_wallet(conn, ip, 300_000, chain)

    # Выпуск 1: размещён (DvP)
    i1 = db.save_issue_decision(conn, issuer, _rov("ЦФА Демо-Лизинг 01", 1000, 200, 1000, 18.5))["issue_id"]
    db.approve_issue(conn, chain, admin_id, i1, "Документ соответствует требованиям")
    db.buy_cfa_on_showcase(conn, investor, i1, 150, "1234", chain)
    db.buy_cfa_on_showcase(conn, fund, i1, 300, "UKEP", chain)
    db.finalize_issue_dvp(conn, i1, chain)

    # Выпуск 2: отменён — минимум не набран, деньги и лимит вернулись
    i2 = db.save_issue_decision(conn, issuer, _rov("ЦФА Демо-Лизинг 00", 1000, 500, 2000, 16))["issue_id"]
    db.approve_issue(conn, chain, admin_id, i2)
    db.buy_cfa_on_showcase(conn, investor, i2, 20, "1234", chain)
    db.finalize_issue_dvp(conn, i2, chain)

    # Выпуск 3: идёт сбор заявок
    i3 = db.save_issue_decision(conn, issuer, _rov("ЦФА Демо-Лизинг 02", 5000, 100, 600, 17))["issue_id"]
    db.approve_issue(conn, chain, admin_id, i3)
    db.buy_cfa_on_showcase(conn, investor, i3, 40, "1234", chain)
    db.buy_cfa_on_showcase(conn, fund, i3, 120, "UKEP", chain)

    db.buy_cfa_on_showcase(conn, ip, i3, 10, "UKEP", chain)          # ИП покупает как инвестор (лимит физлица, УКЭП)

    # Выпуск 4: ждёт модерации
    db.save_issue_decision(conn, issuer, _rov("ЦФА Демо-Девелопмент 01", 100_000, 10, 50, 21,
                                              investors="qualified", days_to_maturity=700))

    # Выпуск 5: эмитент — индивидуальный предприниматель, ежемесячный купон, идёт сбор заявок
    i5 = db.save_issue_decision(conn, ip, _rov(
        "ЦФА ИП Волков 01", 10_000, 20, 100, 22, days_to_maturity=210,
        issuer_full_name="Индивидуальный предприниматель Волков Андрей Николаевич", ogrn_date="2019-06-03",
        beneficiary="Волков Андрей Николаевич — индивидуальный предприниматель, 100 %", website="",
        coupon_period="monthly", collateral="yes", collateral_desc="Залог оборудования кофейни по договору залога "
        "№ 1/2026 оценочной стоимостью 1 500 000 ₽", signer_name="Волков Андрей Николаевич",
        signer_position="Индивидуальный предприниматель"))["issue_id"]
    db.approve_issue(conn, chain, admin_id, i5, "ИП вправе выпускать ЦФА (259-ФЗ, ст. 2), реквизиты сходятся")
    db.buy_cfa_on_showcase(conn, investor, i5, 5, "1234", chain)
    db.buy_cfa_on_showcase(conn, fund, i5, 10, "UKEP", chain)
