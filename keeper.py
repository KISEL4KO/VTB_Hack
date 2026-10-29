#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keeper.py — «сторож» выплат по ЦФА.

Смарт-контракт не может проснуться сам: код в EVM выполняется, только когда кто-то отправил транзакцию.
Поэтому оператор держит «сторожа» (keeper): он регулярно смотрит, у каких выпусков наступила дата
выплаты, и отправляет транзакции payCoupon / redeem. Правила при этом проверяет контракт:
  • дата выплаты наступила (block.timestamp ≥ даты из графика, записанного при размещении);
  • купоны идут по порядку, погашение — после всех купонов;
  • у эмитента хватает цифровых рублей — иначе выплата не проходит целиком.

Если денег не хватает, сторож записывает «нет средств» (раз в день — в историю операций эмитента)
и пробует снова при следующей проверке: как только эмитент пополнит кошелёк, выплата уйдёт сама.

Как запускается (все способы безопасны одновременно — повторно одну выплату провести нельзя:
это не даст уникальный индекс в базе и проверка порядка в контракте):
  1. Фоновый поток, когда сервер запущен командой `python main.py` (раз в KEEPER_INTERVAL секунд, по умолчанию 60).
  2. При обычных запросах к сайту — не чаще раза в KEEPER_REQUEST_INTERVAL секунд (по умолчанию 300).
     Это нужно хостингам без фоновых потоков и расписаний (например, бесплатный PythonAnywhere).
  3. Командой `flask --app main keeper` (один проход) или `flask --app main keeper --loop 60` — для cron / планировщика.
  4. Кнопкой «Проверить сейчас» в админ-панели (раздел «Сторож выплат»).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import database_setup as db

_run_lock = threading.Lock()          # в одном процессе проход идёт не больше чем в одном потоке
_last_request_run = 0.0
LAST_RUN: dict = {}                   # для админки: когда и откуда был последний проход


def _log(conn, issue_id, action, result, message, source, evm=None) -> dict:
    with conn:
        conn.execute("""INSERT INTO keeper_events (issue_id, action, result, message, source, evm_tx_hash)
                        VALUES (?, ?, ?, ?, ?, ?)""", (issue_id, action, result, message, source, evm))
    return {"issue_id": issue_id, "action": action, "result": result, "message": message, "evm_tx_hash": evm}


def _no_funds_today(conn, issue_id: int, action: str) -> bool:
    """Нехватку денег пишем в журнал и в историю эмитента не чаще раза в сутки по одному выпуску."""
    return conn.execute(
        """SELECT 1 FROM keeper_events WHERE issue_id = ? AND action = ? AND result = 'NO_FUNDS'
           AND date(created_at) = date('now', 'localtime')""", (issue_id, action)).fetchone() is not None


def _no_funds(conn, issue, action: str, need: int, what: str, source: str) -> dict | None:
    if _no_funds_today(conn, issue["id"], action):
        return None
    free = db.issuer_free_rub(conn, issue["issuer_id"])
    msg = (f"«{issue['title']}»: {what} не выплачен(о) — у эмитента {db._fmt(free)} ₽ из {db._fmt(need)} ₽. "
           f"Выплата пройдёт автоматически после пополнения кошелька на {db._fmt(need - free)} ₽.")
    with conn:
        db.append_ledger_block(conn, issue["issuer_id"], "PAY_FAIL", 0, "Сторож выплат: " + msg)
    return _log(conn, issue["id"], action, "NO_FUNDS", msg, source)


def due_actions(conn) -> list[dict]:
    """Что сейчас пора выполнить по всем выпускам в обращении (для админки и для самого прохода)."""
    out = []
    for issue in conn.execute("SELECT * FROM cfa_issues WHERE status = 'ISSUED' ORDER BY id").fetchall():
        plan = db.payment_plan(conn, issue)
        nxt, red = plan["next"], plan["redemption"]
        free = db.issuer_free_rub(conn, issue["issuer_id"])
        if nxt is not None:
            out.append({"issue": issue, "action": "COUPON", "what": f"купон № {nxt['no']}", "date": nxt["end"],
                        "amount": nxt["total"], "due": nxt["state"] in ("due", "overdue"), "funds_ok": free >= nxt["total"],
                        "overdue": nxt["state"] == "overdue"})
        else:
            out.append({"issue": issue, "action": "REDEEM", "what": "погашение номинала", "date": red["date"],
                        "amount": red["total"], "due": red["state"] in ("due", "overdue"), "funds_ok": free >= red["total"],
                        "overdue": red["state"] == "overdue"})
    return out


def run_once(chain, source: str) -> list[dict]:
    """Один проход сторожа: все наступившие купоны и погашения по всем выпускам. Возвращает события."""
    if not _run_lock.acquire(blocking=False):
        return []
    events: list[dict] = []
    conn = db.get_connection()
    try:
        for issue in conn.execute("SELECT * FROM cfa_issues WHERE status = 'ISSUED' ORDER BY id").fetchall():
            for _ in range(500):                       # догоняем все наступившие периоды подряд (сервер мог быть выключен)
                issue = conn.execute("SELECT * FROM cfa_issues WHERE id = ?", (issue["id"],)).fetchone()
                if issue["status"] != "ISSUED":
                    break
                plan = db.payment_plan(conn, issue)
                nxt, red = plan["next"], plan["redemption"]
                if nxt is not None and nxt["state"] in ("due", "overdue"):
                    action, need, what = "COUPON", nxt["total"], f"купон № {nxt['no']}"
                elif nxt is None and red["state"] in ("due", "overdue") and not red["block"]:
                    action, need, what = "REDEEM", red["total"], "погашение"
                else:
                    break                               # по этому выпуску пока нечего исполнять
                if db.issuer_free_rub(conn, issue["issuer_id"]) < need:
                    ev = _no_funds(conn, issue, action, need, what, source)
                    if ev:
                        events.append(ev)
                    break
                try:
                    if action == "COUPON":
                        r = db.pay_coupon(conn, issue["id"], None, chain, by="keeper")
                        msg = f"«{issue['title']}»: выплачен купон № {r['period']} — {db._fmt(r['total_rub'])} ₽, владельцев {r['holders']}"
                    else:
                        r = db.redeem_issue(conn, issue["id"], None, chain, by="keeper")
                        msg = f"«{issue['title']}»: выпуск погашен — {db._fmt(r['total_rub'])} ₽, владельцев {r['holders']}"
                    events.append(_log(conn, issue["id"], action, "OK", msg, source, r.get("evm_tx_hash")))
                except ValueError as e:                 # отказ контракта или гонка с другим процессом
                    events.append(_log(conn, issue["id"], action, "ERROR", f"«{issue['title']}»: {e}", source))
                    break
        LAST_RUN.update(at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), source=source, events=len(events))
    finally:
        conn.close()
        _run_lock.release()
    return events


def maybe_run_on_request(chain, interval: float) -> None:
    """Вызывается перед обработкой запроса: проход не чаще раза в interval секунд (для хостингов без потоков)."""
    global _last_request_run
    if interval <= 0 or time.monotonic() - _last_request_run < interval:
        return
    _last_request_run = time.monotonic()
    try:
        run_once(chain, "request")
    except Exception as e:  # сторож не должен ломать страницу сайта
        print("Сторож выплат: ошибка прохода —", e)


def start_thread(chain, interval: float) -> threading.Thread | None:
    """Фоновый поток для локального запуска (python main.py)."""
    if interval <= 0:
        return None

    def loop():
        while True:
            try:
                run_once(chain, "thread")
            except Exception as e:
                print("Сторож выплат: ошибка прохода —", e)
            time.sleep(interval)

    t = threading.Thread(target=loop, name="cfa-keeper", daemon=True)
    t.start()
    return t
