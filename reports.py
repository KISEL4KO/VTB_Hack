#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reports.py — PDF-документы платформы ЦФА (библиотека reportlab, шрифты PT Serif / PT Sans).

Документы по образцу «Решения о выпуске» ПАО Сбербанк и типовых отчётов оператора ИС:
  • decision_pdf        — Решение о выпуске ЦФА (13 разделов);
  • results_pdf         — Отчёт об итогах выпуска ЦФА;
  • registry_pdf        — Реестр владельцев ЦФА выпуска (с остатками из смарт-контракта);
  • statement_pdf       — Выписка по счёту участника;
  • order_pdf           — Подтверждение заявки на приобретение ЦФА;
  • application_pdf     — Анкета участника и результаты проверки (для администратора);
  • participants_pdf    — Реестр участников платформы (для администратора);
  • reconciliation_pdf  — Акт сверки учёта: база данных ↔ смарт-контракт (для администратора).

Каждая функция получает уже подготовленные данные и возвращает PDF в виде bytes.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

import validators as V

FONTS = Path(__file__).parent / "static" / "fonts"
pdfmetrics.registerFont(TTFont("Serif", str(FONTS / "PT_Serif-Web-Regular.ttf")))
pdfmetrics.registerFont(TTFont("Serif-Bold", str(FONTS / "PT_Serif-Web-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Sans", str(FONTS / "PT_Sans-Web-Regular.ttf")))
pdfmetrics.registerFont(TTFont("Sans-Bold", str(FONTS / "PT_Sans-Web-Bold.ttf")))
pdfmetrics.registerFontFamily("Serif", normal="Serif", bold="Serif-Bold", italic="Serif", boldItalic="Serif-Bold")
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans", boldItalic="Sans-Bold")

PLATFORM = {
    "name": "Платформа ЦФА",
    "system": "Информационная система «Платформа ЦФА» (учебный прототип)",
    "operator": "Оператор информационной системы «Платформа ЦФА» (прототип, хакатон ВТБ)",
    "portal": "http://127.0.0.1:5000",
}

INK = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#64748B")
BLUE = colors.HexColor("#2563EB")
LINE = colors.HexColor("#CBD5E1")
SOFT = colors.HexColor("#EFF6FF")
GREEN = colors.HexColor("#047857")
AMBER = colors.HexColor("#B45309")
RED = colors.HexColor("#B91C1C")

ST = {
    "title": ParagraphStyle("title", fontName="Serif-Bold", fontSize=15, leading=19, alignment=TA_CENTER,
                            textColor=INK, spaceAfter=4),
    "subtitle": ParagraphStyle("subtitle", fontName="Serif", fontSize=10.5, leading=14, alignment=TA_CENTER,
                               textColor=MUTED, spaceAfter=10),
    "h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=11.5, leading=15, textColor=BLUE,
                         spaceBefore=10, spaceAfter=4),
    "body": ParagraphStyle("body", fontName="Serif", fontSize=10, leading=14, alignment=TA_JUSTIFY,
                           textColor=INK, spaceAfter=4),
    "small": ParagraphStyle("small", fontName="Sans", fontSize=8, leading=10.5, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Serif", fontSize=9, leading=12, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Sans-Bold", fontSize=8.5, leading=11, textColor=MUTED),
    "mono": ParagraphStyle("mono", fontName="Sans", fontSize=7.5, leading=10, textColor=INK),
}

STATUS_RU = {
    "REVIEW": "На проверке администратором", "REJECTED": "Возвращён на доработку",
    "SUBSCRIPTION": "Сбор заявок", "ISSUED": "Размещён (выпуск состоялся)",
    "CANCELLED": "Отменён (выпуск не состоялся)", "REDEEMED": "Погашен",
    "PENDING": "На проверке", "APPROVED": "Допущен", "BLOCKED": "Заблокирован",
    "HOLD": "Средства заморожены", "EXECUTED": "Исполнена (DvP)", "REFUNDED": "Возврат средств",
}


# ------------------------------------------------------------------------------
# Вспомогательное
# ------------------------------------------------------------------------------
def rub(v) -> str:
    try:
        return f"{int(v):,}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return "—"


def num(v) -> str:
    return f"{int(v or 0):,}".replace(",", " ")


def ddmmyyyy(iso: str | None) -> str:
    if not iso:
        return "—"
    s = str(iso)
    if len(s) >= 10 and s[4] == "-":
        return f"{s[8:10]}.{s[5:7]}.{s[:4]}" + (s[10:16] if len(s) > 10 else "")
    return s


def P(text, style="body") -> Paragraph:
    """Абзац: экранируем пользовательский текст, переносы строк сохраняем."""
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), ST[style])


def Praw(markup, style="body") -> Paragraph:
    return Paragraph(markup, ST[style])


def kv_table(rows, widths=(62 * mm, 108 * mm)) -> Table:
    """Таблица «реквизит — значение»."""
    data = [[P(k, "cellb"), P(v if v not in (None, "") else "—", "cell")] for k, v in rows]
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def grid_table(header, rows, widths, align_right=()) -> Table:
    data = [[P(h, "cellb") for h in header]] + [[P(c, "cell") for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), SOFT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for col in align_right:
        style.append(("ALIGN", (col, 1), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _build(title: str, subtitle: str, doc_no: str, story: list, watermark: str = "",
           pagesize=A4, signature: list | None = None) -> bytes:
    buf = io.BytesIO()
    generated = datetime.now().strftime("%d.%m.%Y %H:%M")

    def on_page(canvas, doc):
        w, h = pagesize
        canvas.saveState()
        canvas.setFont("Sans-Bold", 8.5)
        canvas.setFillColor(BLUE)
        canvas.drawString(18 * mm, h - 12 * mm, PLATFORM["name"].upper())
        canvas.setFont("Sans", 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(w - 18 * mm, h - 12 * mm, f"{title} · {doc_no}")
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, h - 14 * mm, w - 18 * mm, h - 14 * mm)
        canvas.line(18 * mm, 15 * mm, w - 18 * mm, 15 * mm)
        canvas.drawString(18 * mm, 10 * mm, f"Сформировано {generated} · документ учебного прототипа, "
                                            f"не является официальным документом оператора ИС")
        canvas.drawRightString(w - 18 * mm, 10 * mm, f"Стр. {doc.page}")
        if watermark:
            canvas.setFont("Sans-Bold", 54)
            canvas.setFillColor(colors.Color(0.85, 0.12, 0.12, alpha=0.10))
            canvas.translate(w / 2, h / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, watermark)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=22 * mm, bottomMargin=22 * mm, title=title, author=PLATFORM["name"])
    head = [Praw(escape(title.upper()), "title"), Praw(escape(subtitle), "subtitle")]
    doc.build(head + story + (signature or []), onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def _sign_block(lines: list[tuple[str, str]]) -> list:
    t = kv_table(lines, widths=(52 * mm, 118 * mm))
    return [Spacer(1, 8), KeepTogether([P("Реквизиты подписи и блокчейна", "h1"), t])]


# ------------------------------------------------------------------------------
# 1. Решение о выпуске ЦФА
# ------------------------------------------------------------------------------
def decision_pdf(issue, rov: dict, chain_address: str | None) -> bytes:
    iss = rov.get("issuer", {})
    ogrn_kpp = (iss.get("inn_kpp_ogrn") or " / ").split(" / ")
    cfa_id = f"CFA-{issue['id']:06d}"
    s = []

    s.append(P(f"Идентификатор ЦФА в информационной системе: {cfa_id}. Настоящее Решение о выпуске цифровых "
               f"финансовых активов (далее — Решение) принято в соответствии с Федеральным законом от 31.07.2020 "
               f"№ 259-ФЗ «О цифровых финансовых активах, цифровой валюте и о внесении изменений в отдельные "
               f"законодательные акты Российской Федерации». Учёт ЦФА ведётся записями в распределённом реестре "
               f"информационной системы — смарт-контрактом CFAPlatform"
               + (f" по адресу {chain_address}." if chain_address else ".")))
    s.append(P("Термины: «Платформа» — информационная система, в которой выпускаются ЦФА; «Эмитент» — лицо, "
               "выпускающее ЦФА; «Инвестор» — участник Платформы, приобретающий ЦФА; «Кошелёк» — учётная запись "
               "участника для цифровых рублей и ЦФА; «DvP» — расчёт по принципу «поставка против платежа»."))

    s.append(P("1. Сведения об Эмитенте", "h1"))
    s.append(kv_table([
        ("Полное наименование", rov.get("issuer_full_name")),
        ("Сокращённое наименование", iss.get("name")),
        ("ОГРН", ogrn_kpp[0].strip()),
        ("Дата присвоения ОГРН", ddmmyyyy(rov.get("ogrn_date"))),
        ("ИНН / КПП", f"{iss.get('inn', '—')} / {ogrn_kpp[1].strip() if len(ogrn_kpp) > 1 else '—'}"),
        ("Адрес юридического лица", iss.get("address")),
        ("Бенефициарный владелец", rov.get("beneficiary")),
        ("Официальный сайт", rov.get("website") or "—"),
    ]))

    s.append(P("2. Сведения об операторе информационной системы", "h1"))
    s.append(kv_table([
        ("Оператор", PLATFORM["operator"]),
        ("Информационная система", PLATFORM["system"]),
        ("Веб-портал", PLATFORM["portal"]),
        ("Распределённый реестр", "EVM-совместимая сеть оператора, смарт-контракт CFAPlatform"),
    ]))

    s.append(P("3. Вид и объём прав, удостоверяемых ЦФА", "h1"))
    s.append(P("3.1. ЦФА удостоверяют цифровые права, включающие денежные требования к Эмитенту: право на получение "
               "номинальной стоимости ЦФА и фиксированного дохода. ЦФА не являются электронными денежными "
               "средствами. Все ЦФА выпуска удостоверяют одинаковый объём прав."))
    s.append(kv_table([
        ("Наименование выпуска", rov.get("title")),
        ("Номинальная стоимость 1 ЦФА", rub(rov.get("price_per_cfa"))),
        ("Фиксированный доход", f"{rov.get('coupon_rate')} % годовых от номинальной стоимости"),
        ("Выплата дохода", V.ROV_COUPON_PERIODS.get(rov.get("coupon_period"), "—")),
        ("Дата погашения", ddmmyyyy(rov.get("maturity_date"))),
    ]))
    s.append(P("3.2. Порядок погашения: " + (rov.get("redemption_order") or "")))

    s.append(P("4. Количество выпускаемых ЦФА", "h1"))
    s.append(kv_table([
        ("Минимальное количество ЦФА выпуска", f"{num(rov.get('min_qty'))} шт."),
        ("Максимальное количество ЦФА выпуска", f"{num(rov.get('max_qty'))} шт."),
        ("Минимум в одной заявке", f"{num(rov.get('min_order_qty'))} шт."),
        ("Максимум в одной заявке", f"{num(rov.get('max_order_qty'))} шт." if rov.get("max_order_qty")
         else "не ограничен (в пределах объёма выпуска)"),
        ("Максимальный объём выпуска", rub((rov.get("max_qty") or 0) * (rov.get("price_per_cfa") or 0))),
    ]))

    s.append(P("5. Цена приобретения ЦФА при выпуске", "h1"))
    s.append(P(f"Цена приобретения одного ЦФА фиксирована и равна номинальной стоимости — "
               f"{rub(rov.get('price_per_cfa'))}. Сумма заявки равна произведению цены на количество ЦФА в заявке."))

    s.append(P("6. Период размещения", "h1"))
    s.append(P(f"Дата начала приёма заявок — {ddmmyyyy(rov.get('placement_start'))}. "
               f"Дата окончания приёма заявок — {ddmmyyyy(rov.get('placement_end'))} включительно."))

    s.append(P("7. Условие признания выпуска состоявшимся", "h1"))
    s.append(P(f"Выпуск признаётся состоявшимся, если по итогам периода размещения заявлено не менее "
               f"{num(rov.get('min_qty'))} ЦФА. В этом случае смарт-контракт одной транзакцией зачисляет ЦФА "
               f"инвесторам и переводит денежные средства Эмитенту (DvP). Если условие не выполнено, выпуск "
               f"признаётся несостоявшимся, а замороженные средства возвращаются инвесторам."))

    s.append(P("8. Способ оплаты", "h1"))
    s.append(P("Оплата производится " + V.ROV_PAYMENT.get(rov.get("payment_method"), "—") +
               ". При подаче заявки сумма заявки замораживается на кошельке инвестора до закрытия сбора."))

    s.append(P("9. Ограничение ответственности Эмитента", "h1"))
    s.append(P(rov.get("liability") or ""))

    s.append(P("10. Круг лиц, которым предлагаются ЦФА", "h1"))
    s.append(P("10.1. Настоящее Решение не является публичной офертой. ЦФА предлагаются участникам Платформы, "
               "прошедшим проверку оператора информационной системы."))
    s.append(P("10.2. Приобретать ЦФА вправе: " + V.ROV_INVESTORS.get(rov.get("investors"), "—") + "."))

    s.append(P("11. Выкуп ЦФА Эмитентом", "h1"))
    s.append(P("Выкуп предусмотрен. " + (rov.get("buyback_terms") or "") if rov.get("buyback") == "yes"
               else "Выкуп ЦФА Эмитентом до даты погашения не предусмотрен."))

    s.append(P("12. Иные условия", "h1"))
    s.append(P("12.1. Обеспечение: " + ("предусмотрено. " + (rov.get("collateral_desc") or "")
                                        if rov.get("collateral") == "yes" else "не предусмотрено.")))
    s.append(P("12.2. Досрочное погашение: " + ("допускается по решению Эмитента с уведомлением владельцев ЦФА "
                                                "через Платформу." if rov.get("early_redemption") == "yes"
                                                else "не допускается.")))
    s.append(P("12.3. Дробление ЦФА и частичное исполнение заявок не допускаются."))
    s.append(P("12.4. Порядок разрешения споров: " + (rov.get("disputes") or "")))

    s.append(P("13. Риски, связанные с приобретением ЦФА", "h1"))
    risks = [V.ROV_RISKS[r] for r in rov.get("risks", []) if r in V.ROV_RISKS]
    s.append(P("13.1. Инвестор принимает следующие риски: " + "; ".join(risks).lower() + "."))
    if rov.get("risks_extra"):
        s.append(P("13.2. " + rov["risks_extra"]))
    s.append(P("13.3. Эмитент не гарантирует доходность и сохранность вложенных средств сверх обязательств, "
               "указанных в настоящем Решении, и не является инвестиционным советником. Приобретая ЦФА, инвестор "
               "подтверждает, что ознакомился с настоящим Решением и правилами Платформы."))

    sign = _sign_block([
        ("Подписант", f"{rov.get('signer_position', '')} — {rov.get('signer_name', '')}"),
        ("Электронная подпись", issue["ukep_signature"] + " (имитация УКЭП)"),
        ("SHA-256 документа", issue["doc_hash"]),
        ("Статус", STATUS_RU.get(issue["status"], issue["status"])),
        ("Опубликовано", ddmmyyyy(issue["published_at"]) if issue["published_at"] else "не опубликовано"),
        ("Транзакция регистрации в блокчейне", issue["evm_tx_hash"] or "—"),
    ])
    watermark = "ПРОЕКТ" if issue["status"] in ("REVIEW", "REJECTED") else ""
    return _build("Решение о выпуске цифровых финансовых активов",
                  f"{iss.get('name', '')} · ЦФА, удостоверяющие денежные требования, с фиксированным доходом",
                  cfa_id, s, watermark=watermark, signature=sign)


# ------------------------------------------------------------------------------
# 2. Отчёт об итогах выпуска
# ------------------------------------------------------------------------------
def results_pdf(issue, issuer, orders: list, chain_info: dict | None) -> bytes:
    cfa_id = f"CFA-{issue['id']:06d}"
    executed = [o for o in orders if o["status"] == "EXECUTED"]
    refunded = [o for o in orders if o["status"] == "REFUNDED"]
    investors = {o["user_id"] for o in orders}
    fl = {o["user_id"] for o in orders if o["role"] == "FL"}
    raised = sum(o["total_rub"] for o in executed)
    s = [P("1. Параметры выпуска", "h1"), kv_table([
        ("Эмитент", f"{issuer['full_name']}, ИНН {issuer['inn']}"),
        ("Наименование выпуска", issue["title"]),
        ("Цена 1 ЦФА", rub(issue["price_per_cfa"])),
        ("Объём: минимум / максимум", f"{num(issue['min_qty'])} / {num(issue['max_qty'])} шт."),
        ("Период размещения", f"{ddmmyyyy(issue['placement_start'])} — {ddmmyyyy(issue['placement_end'])}"),
        ("Дата погашения", ddmmyyyy(issue["maturity_date"])),
        ("SHA-256 Решения о выпуске", issue["doc_hash"]),
    ])]
    done = issue["status"] == "ISSUED"
    s += [P("2. Итоги размещения", "h1"), kv_table([
        ("Результат", "ВЫПУСК СОСТОЯЛСЯ — расчёт DvP выполнен" if done else
         "ВЫПУСК НЕ СОСТОЯЛСЯ — средства возвращены инвесторам" if issue["status"] == "CANCELLED" else
         STATUS_RU.get(issue["status"], issue["status"])),
        ("Дата подведения итогов", ddmmyyyy(issue["finalized_at"])),
        ("Заявлено ЦФА", f"{num(issue['collected_qty'])} шт. "
                         f"({issue['collected_qty'] * 100 // max(issue['max_qty'], 1)} % от максимума)"),
        ("Размещено ЦФА", f"{num(sum(o['qty'] for o in executed))} шт."),
        ("Привлечено средств", rub(raised)),
        ("Возвращено инвесторам", rub(sum(o["total_rub"] for o in refunded))),
        ("Количество инвесторов", f"{len(investors)} (физлиц — {len(fl)}, юрлиц — {len(investors) - len(fl)})"),
        ("Количество заявок", str(len(orders))),
    ])]
    if chain_info:
        s += [P("3. Подтверждение в распределённом реестре", "h1"), kv_table([
            ("Смарт-контракт", chain_info.get("contract") or "—"),
            ("Статус выпуска в контракте", chain_info.get("status") or "—"),
            ("Размещено по данным контракта", f"{num(chain_info.get('supply'))} шт."),
            ("Транзакция закрытия сбора", chain_info.get("tx") or "—"),
        ])]
    s += [P("4. Заявки", "h1"), grid_table(
        ["№", "Дата", "Инвестор", "Кол-во", "Сумма", "Статус"],
        [[str(o["id"]), ddmmyyyy(o["created_at"]), ("ФЛ" if o["role"] == "FL" else "ЮЛ") + f" · участник #{o['user_id']}",
          num(o["qty"]), rub(o["total_rub"]), STATUS_RU.get(o["status"], o["status"])] for o in orders]
        or [["—", "", "Заявок не было", "", "", ""]],
        [12 * mm, 30 * mm, 42 * mm, 20 * mm, 30 * mm, 40 * mm], align_right=(3, 4))]
    return _build("Отчёт об итогах выпуска ЦФА", f"{issue['title']} · {issuer['full_name']}", cfa_id, s)


# ------------------------------------------------------------------------------
# 3. Реестр владельцев ЦФА выпуска
# ------------------------------------------------------------------------------
def registry_pdf(issue, issuer, holders: list[dict]) -> bytes:
    cfa_id = f"CFA-{issue['id']:06d}"
    total = sum(h["qty"] for h in holders) or 1
    rows = [[str(i + 1), h["name"], h["type"], h["evm"], num(h["qty"]),
             "—" if h.get("chain_qty") is None else num(h["chain_qty"]),
             f"{h['qty'] * 100 / total:.2f} %"] for i, h in enumerate(holders)]
    s = [kv_table([
        ("Выпуск", f"{issue['title']} ({cfa_id})"),
        ("Эмитент", f"{issuer['full_name']}, ИНН {issuer['inn']}"),
        ("Статус выпуска", STATUS_RU.get(issue["status"], issue["status"])),
        ("Дата составления реестра", datetime.now().strftime("%d.%m.%Y %H:%M")),
        ("Всего ЦФА в обращении", f"{num(sum(h['qty'] for h in holders))} шт."),
    ]), Spacer(1, 6), grid_table(
        ["№", "Владелец", "Тип", "Адрес в реестре (EVM)", "ЦФА (учёт)", "ЦФА (контракт)", "Доля"],
        rows or [["—", "Владельцев нет", "", "", "", "", ""]],
        [10 * mm, 52 * mm, 14 * mm, 72 * mm, 26 * mm, 28 * mm, 20 * mm], align_right=(4, 5, 6)),
        Spacer(1, 6),
        P("Колонка «ЦФА (контракт)» — остаток на адресе владельца по данным смарт-контракта CFAPlatform. "
          "Совпадение колонок подтверждает, что учёт платформы соответствует распределённому реестру.", "small")]
    return _build("Реестр владельцев ЦФА", f"{issue['title']} · {issuer['full_name']}", cfa_id, s,
                  pagesize=landscape(A4))


# ------------------------------------------------------------------------------
# 4. Выписка по счёту участника
# ------------------------------------------------------------------------------
def statement_pdf(user, wallet, holdings: list, ops: list, chain_bal: dict | None) -> bytes:
    no = f"ST-{user['id']:05d}-{datetime.now():%Y%m%d}"
    s = [P("1. Участник", "h1"), kv_table([
        ("Участник", user["full_name"]),
        ("Тип", "Физическое лицо" if user["role"] == "FL" else "Юридическое лицо"),
        ("ИНН", user["inn"]),
        ("Адрес в распределённом реестре", user["evm_address"]),
        ("Статус инвестора", "квалифицированный" if user["is_qualified"] or user["role"] == "UL"
         else f"неквалифицированный, использовано лимита {rub(user['yearly_spent_rub'])} из 600 000 ₽"),
    ]), P("2. Денежные средства", "h1"), kv_table([
        ("Свободно", rub(wallet["balance_rub"]) + (f" (контракт: {rub(chain_bal['free'])})" if chain_bal else "")),
        ("Заморожено под заявки", rub(wallet["locked_rub"]) + (f" (контракт: {rub(chain_bal['locked'])})" if chain_bal else "")),
        ("Привязанный счёт", f"{wallet['bank_account']}, БИК {wallet['bank_bik']}"),
    ]), P("3. Цифровые финансовые активы", "h1"), grid_table(
        ["Выпуск", "Эмитент", "Кол-во", "Вложено", "Ожидаемый доход", "Погашение"],
        [[h["title"], h["issuer_name"], num(h["qty"]), rub(h["invested_rub"]), rub(h["expected_coupon_rub"]),
          ddmmyyyy(h["maturity_date"])] for h in holdings] or [["ЦФА нет", "", "", "", "", ""]],
        [44 * mm, 36 * mm, 16 * mm, 26 * mm, 24 * mm, 24 * mm], align_right=(2, 3, 4)),
        P("4. Операции", "h1"), grid_table(
        ["Дата", "Операция", "Сумма", "Описание"],
        [[ddmmyyyy(o["created_at"]), o["op_name"], rub(o["amount_rub"]) if o["amount_rub"] else "—",
          o["description"]] for o in ops] or [["", "Операций нет", "", ""]],
        [28 * mm, 34 * mm, 26 * mm, 82 * mm], align_right=(2,))]
    return _build("Выписка по счёту участника", user["full_name"], no, s)


# ------------------------------------------------------------------------------
# 5. Подтверждение заявки
# ------------------------------------------------------------------------------
def order_pdf(order, issue, user, issuer) -> bytes:
    no = f"ORD-{order['id']:06d}"
    s = [P("Оператор информационной системы подтверждает приём заявки на приобретение ЦФА при выпуске. "
           "Денежные средства в сумме заявки заморожены на кошельке инвестора до закрытия сбора заявок."),
         kv_table([
             ("Номер заявки", no),
             ("Дата и время подачи", ddmmyyyy(order["created_at"])),
             ("Инвестор", f"{user['full_name']}, ИНН {user['inn']}"),
             ("Выпуск", f"{issue['title']} (CFA-{issue['id']:06d})"),
             ("Эмитент", issuer["full_name"]),
             ("Количество ЦФА", f"{num(order['qty'])} шт."),
             ("Цена 1 ЦФА", rub(issue["price_per_cfa"])),
             ("Сумма заявки", rub(order["total_rub"])),
             ("Способ подписания", "ПЭП (СМС-код)" if order["sign_type"] == "PEP_SMS" else "УКЭП"),
             ("Хэш подписи", order["signature_hash"]),
             ("Статус заявки", STATUS_RU.get(order["status"], order["status"])),
             ("Транзакция в блокчейне", order["evm_tx_hash"] or "—"),
         ])]
    return _build("Подтверждение заявки на приобретение ЦФА", issue["title"], no, s)


# ------------------------------------------------------------------------------
# 6. Анкета участника и результаты проверки (админ)
# ------------------------------------------------------------------------------
def application_pdf(user, sections, checks: list[dict], reviewer: str | None) -> bytes:
    no = f"KYC-{user['id']:05d}"
    s = [kv_table([
        ("Участник", user["full_name"]),
        ("Тип", "Физическое лицо" if user["role"] == "FL" else "Юридическое лицо"),
        ("Логин", user["login"]),
        ("Дата подачи анкеты", ddmmyyyy(user["created_at"])),
        ("Статус допуска", STATUS_RU.get(user["status"], user["status"])),
        ("Проверил", reviewer or "—"),
        ("Причина отказа / блокировки", user["reject_reason"] or "—"),
        ("Транзакция белого списка", user["evm_tx_hash"] or "—"),
    ])]
    for title, rows in sections:
        s += [P(title, "h1"), kv_table(rows)]
    mark = {"ok": ("сходится", GREEN), "warn": ("проверить", AMBER), "fail": ("препятствие", RED)}
    s += [P("Автоматическая сверка данных", "h1"), grid_table(
        ["Проверка", "Результат", "Подробности"],
        [[c["title"], mark[c["status"]][0], c["detail"]] for c in checks],
        [58 * mm, 30 * mm, 82 * mm])]
    return _build("Анкета участника и результаты проверки", user["full_name"], no, s)


# ------------------------------------------------------------------------------
# 7. Реестр участников (админ)
# ------------------------------------------------------------------------------
def participants_pdf(users: list) -> bytes:
    rows = [[str(u["id"]), u["full_name"], "ФЛ" if u["role"] == "FL" else "ЮЛ", u["inn"] or "—", u["login"],
             STATUS_RU.get(u["status"], u["status"]), ddmmyyyy(u["created_at"]),
             rub(u["balance_rub"]), rub(u["locked_rub"])] for u in users]
    s = [P(f"Всего участников: {len(users)}. Допущено: {sum(u['status'] == 'APPROVED' for u in users)}, "
           f"на проверке: {sum(u['status'] == 'PENDING' for u in users)}, отказано: "
           f"{sum(u['status'] == 'REJECTED' for u in users)}, заблокировано: {sum(u['status'] == 'BLOCKED' for u in users)}."),
         grid_table(["ID", "Участник", "Тип", "ИНН", "Логин", "Статус", "Регистрация", "Свободно", "В холде"],
                    rows or [["", "Участников нет", "", "", "", "", "", "", ""]],
                    [12 * mm, 60 * mm, 12 * mm, 30 * mm, 30 * mm, 30 * mm, 32 * mm, 28 * mm, 26 * mm],
                    align_right=(7, 8))]
    return _build("Реестр участников платформы", PLATFORM["system"], f"REG-{datetime.now():%Y%m%d}", s,
                  pagesize=landscape(A4))


# ------------------------------------------------------------------------------
# 8. Акт сверки учёта и блокчейна (админ)
# ------------------------------------------------------------------------------
def reconciliation_pdf(rows: list[dict], status: dict) -> bytes:
    bad = [r for r in rows if not r["match"]]
    s = [kv_table([
        ("Режим сети", {"embedded": "встроенная EVM (py-evm)", "rpc": "внешний узел (RPC)"}.get(status.get("mode"), "выключен")),
        ("Смарт-контракт", status.get("contract") or "—"),
        ("Высота блока", str(status.get("block", "—"))),
        ("Цифровых рублей в контракте", rub(status.get("total_rub"))),
        ("Проверено позиций", str(len(rows))),
        ("Расхождений", "нет — учёт полностью совпадает с блокчейном" if not bad else str(len(bad))),
    ]), Spacer(1, 6), grid_table(
        ["Участник / позиция", "Показатель", "База данных", "Смарт-контракт", "Итог"],
        [[r["who"], r["what"], r["db"], r["chain"], "совпадает" if r["match"] else "РАСХОЖДЕНИЕ"] for r in rows]
        or [["", "Нет данных", "", "", ""]],
        [70 * mm, 50 * mm, 40 * mm, 40 * mm, 30 * mm], align_right=(2, 3))]
    return _build("Акт сверки учёта с распределённым реестром", PLATFORM["system"],
                  f"REC-{datetime.now():%Y%m%d-%H%M}", s, pagesize=landscape(A4))
