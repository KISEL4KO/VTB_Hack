#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
blockchain.py — лёгкий EVM-блокчейн платформы ЦФА.

Два режима:

1. ВСТРОЕННЫЙ (по умолчанию). Внутри приложения работает настоящая виртуальная машина
   Ethereum (py-evm через eth-tester). Каждая транзакция подписывается ключом оператора,
   исполняется EVM и сохраняется в таблицу evm_transactions базы cfa.db (сырые подписанные
   байты). При запуске журнал воспроизводится в чистой сети — EVM детерминирована, поэтому
   состояние контракта, его адрес и хэши транзакций получаются ровно такими же.
   Это и есть суть блокчейна: состояние = журнал транзакций + детерминированная машина.

2. ВНЕШНИЙ УЗЕЛ. Если задана переменная окружения EVM_RPC_URL (например, Anvil или Hardhat:
   http://127.0.0.1:8545, или закрытая сеть Besu/Quorum), платформа работает с ним.
   Ключ оператора — EVM_OPERATOR_KEY (по умолчанию — публичный тестовый ключ №0 Anvil/Hardhat).

Если библиотеки web3 / py-evm не установлены, блокчейн выключается (enabled = False),
а платформа продолжает работать на SQLite и журнале хэшей.

Все вызовы контракта идут через Chain.transact(): сначала операция «примеряется» (eth_call) —
если контракт её отклонит, пользователь получит понятный текст ошибки из require(), а база
данных откатит свою транзакцию. Затем транзакция подписывается, отправляется и попадает в журнал.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

ARTIFACT_PATH = Path(__file__).parent / "blockchain" / "CFAPlatform.json"

# Публичный тестовый ключ №0 Anvil/Hardhat — известен всем, годится только для локальной сети.
ANVIL_DEFAULT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

ISSUE_STATUS = {0: "—", 1: "Сбор заявок", 2: "Размещён (DvP)", 3: "Отменён (возврат)", 4: "Погашен"}

CHAIN_SCHEMA_SQL = """
-- Журнал транзакций EVM: из него при запуске восстанавливается состояние встроенной сети
CREATE TABLE IF NOT EXISTS evm_transactions (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash       TEXT NOT NULL UNIQUE,
    method        TEXT NOT NULL,                 -- вызванная функция контракта
    args_json     TEXT NOT NULL,                 -- аргументы (для обозревателя блоков)
    raw_tx        BLOB NOT NULL,                 -- подписанная транзакция целиком
    block_number  INTEGER NOT NULL,
    gas_used      INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
-- Служебные значения: адрес контракта, идентификатор сети
CREATE TABLE IF NOT EXISTS evm_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def doc_hash(data: dict) -> str:
    """SHA-256 от канонического JSON — «отпечаток» документа, который записывается в блокчейн."""
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "0x" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def to_bytes32(hex_str: str | None) -> bytes:
    """'0xabc…' (до 32 байт) → 32 байта; для строк-подписей вида PEP_SMS_… берём их SHA-256."""
    if not hex_str:
        return b"\x00" * 32
    s = hex_str[2:] if hex_str.startswith("0x") else hex_str
    try:
        raw = bytes.fromhex(s)
    except ValueError:
        raw = hashlib.sha256(hex_str.encode("utf-8")).digest()
    return raw[:32].rjust(32, b"\x00")


class Chain:
    """Обёртка над смарт-контрактом CFAPlatform. Один экземпляр на приложение."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]):
        self.enabled = False
        self.mode = "off"
        self.reason = ""
        self.contract = None
        self.address = None
        self.replayed = 0
        self._lock = threading.RLock()   # EVM и nonce оператора — общий ресурс для всех потоков Flask
        self._connect = connect
        self._synced_at = 0.0

        try:
            from web3 import Web3
        except ImportError:
            self.reason = "Не установлены библиотеки web3 / py-evm — выполните pip install -r requirements.txt"
            return
        self.Web3 = Web3

        if not ARTIFACT_PATH.exists():
            self.reason = f"Не найден {ARTIFACT_PATH.name} — скомпилируйте контракт (blockchain/compile.js)"
            return
        artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
        self.abi, self.bytecode = artifact["abi"], artifact["bytecode"]

        try:
            rpc = os.environ.get("EVM_RPC_URL")
            if rpc:
                self.w3 = Web3(Web3.HTTPProvider(rpc))
                if not self.w3.is_connected():
                    raise ConnectionError(f"узел {rpc} недоступен")
                key = os.environ.get("EVM_OPERATOR_KEY", ANVIL_DEFAULT_KEY)
                self.mode = "rpc"
            else:
                key = self._new_embedded()
                self.mode = "embedded"
            self.account = self.w3.eth.account.from_key(key)
            self.chain_id = self.w3.eth.chain_id

            conn = connect()
            try:
                with conn:
                    conn.executescript(CHAIN_SCHEMA_SQL)
                if self.mode == "embedded":
                    self._replay(conn)
                self._ensure_contract(conn)
            finally:
                conn.close()
            self.enabled = True
        except Exception as e:  # блокчейн — дополнительный слой: его сбой не должен ронять платформу
            self.reason = f"Блокчейн недоступен: {e}"
            self.enabled = False

    # ------------------------------------------------------------------ запуск
    def _new_embedded(self):
        """Чистая встроенная EVM-сеть. Возвращает ключ оператора (детерминированный — одинаковый при каждом запуске)."""
        from eth_tester import EthereumTester, PyEVMBackend
        from web3 import EthereumTesterProvider
        backend = PyEVMBackend()
        self.w3 = self.Web3(EthereumTesterProvider(EthereumTester(backend)))
        return backend.account_keys[0]

    def _resync(self, conn: sqlite3.Connection) -> None:
        """
        Самовосстановление встроенной сети. Все транзакции отправляет оператор, поэтому его nonce
        обязан равняться числу записей в журнале.
          • В журнале больше записей — их добавил другой процесс (например, «сторож», запущенный
            командой по расписанию): догоняем, воспроизводя только недостающий хвост.
          • В журнале меньше — транзакция SQLite откатилась уже после исполнения в EVM:
            пересобираем EVM из журнала целиком.
        """
        if self.mode != "embedded":
            return
        journal = conn.execute("SELECT COUNT(*) FROM evm_transactions").fetchone()[0]
        nonce = self.w3.eth.get_transaction_count(self.account.address)
        if journal > nonce:
            rows = conn.execute("SELECT raw_tx, tx_hash FROM evm_transactions ORDER BY seq LIMIT -1 OFFSET ?",
                                (nonce,)).fetchall()
            for row in rows:
                h = self.w3.eth.send_raw_transaction(bytes(row["raw_tx"]))
                if self.w3.eth.wait_for_transaction_receipt(h).status != 1 or h.to_0x_hex() != row["tx_hash"]:
                    journal = -1   # хвост не сходится — пересобираем целиком
                    break
        if journal != self.w3.eth.get_transaction_count(self.account.address):
            self._new_embedded()
            self._replay(conn)
            self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)
        self._synced_at = time.monotonic()

    def _replay(self, conn: sqlite3.Connection) -> None:
        """Воспроизводит журнал транзакций во встроенной EVM (детерминированно)."""
        rows = conn.execute("SELECT raw_tx, tx_hash FROM evm_transactions ORDER BY seq").fetchall()
        for row in rows:
            h = self.w3.eth.send_raw_transaction(bytes(row["raw_tx"]))
            receipt = self.w3.eth.wait_for_transaction_receipt(h)
            if receipt.status != 1 or h.to_0x_hex() != row["tx_hash"]:
                raise RuntimeError(f"журнал не воспроизводится на транзакции {row['tx_hash']}")
        self.replayed = len(rows)

    def _meta(self, conn, key, value=None):
        if value is None:
            row = conn.execute("SELECT value FROM evm_meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        conn.execute("INSERT OR REPLACE INTO evm_meta (key, value) VALUES (?, ?)", (key, str(value)))

    def _ensure_contract(self, conn: sqlite3.Connection) -> None:
        """Находит развёрнутый контракт или разворачивает новый (первая транзакция сети)."""
        address = self._meta(conn, "contract_address")
        if address and len(self.w3.eth.get_code(address)) > 0:
            self.address = address
        else:
            factory = self.w3.eth.contract(abi=self.abi, bytecode=self.bytecode)
            with conn:
                receipt = self._send(conn, factory.constructor(), "deploy", [])
                self.address = receipt.contractAddress
                self._meta(conn, "contract_address", self.address)
                self._meta(conn, "chain_id", self.chain_id)
        self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)

    # ------------------------------------------------------------------ транзакции
    def _send(self, conn, fn, method: str, args: list):
        """Примерка → подпись → отправка → запись в журнал (в транзакции БД вызывающего кода)."""
        sender = {"from": self.account.address}
        try:
            fn.call(sender) if method != "deploy" else None
            gas = int(fn.estimate_gas(sender) * 1.25)
        except Exception as e:
            raise ChainError(_revert_reason(e)) from e

        tx = fn.build_transaction({
            **sender,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": gas,
            "maxFeePerGas": 50 * 10**9,
            "maxPriorityFeePerGas": 10**9,
            "chainId": self.chain_id,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise ChainError("Транзакция отклонена сетью")
        conn.execute(
            """INSERT INTO evm_transactions (tx_hash, method, args_json, raw_tx, block_number, gas_used)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tx_hash.to_0x_hex(), method, json.dumps(args, ensure_ascii=False, default=str),
             bytes(signed.raw_transaction), receipt.blockNumber, receipt.gasUsed),
        )
        return receipt

    def transact(self, conn: sqlite3.Connection, method: str, *args) -> str | None:
        """
        Вызывает функцию контракта. Возвращает хэш транзакции (или None, если блокчейн выключен).
        Бросает ChainError (подкласс ValueError) с текстом из require() — вызывающий код
        находится внутри `with conn:`, поэтому изменения в SQLite тоже откатятся.
        """
        if not self.enabled:
            return None
        with self._lock:
            self._resync(conn)
            fn = getattr(self.contract.functions, method)(*args)
            shown = [a.hex() if isinstance(a, bytes) else a for a in args]
            receipt = self._send(conn, fn, method, shown)
            return receipt.transactionHash.to_0x_hex()

    def call(self, method: str, *args) -> Any:
        """Чтение состояния контракта (без транзакции). Раз в секунду сверяемся с журналом — вдруг
        транзакции добавил другой процесс («сторож»)."""
        if not self.enabled:
            return None
        with self._lock:
            if self.mode == "embedded" and time.monotonic() - self._synced_at > 1.0:
                conn = self._connect()
                try:
                    self._resync(conn)
                finally:
                    conn.close()
            return getattr(self.contract.functions, method)(*args).call()

    def address_of(self, evm_address: str) -> str:
        return self.Web3.to_checksum_address(evm_address)

    # ------------------------------------------------------------------ для админки
    def status(self) -> dict:
        info = {"enabled": self.enabled, "mode": self.mode, "reason": self.reason,
                "contract": self.address, "replayed": self.replayed}
        if self.enabled:
            with self._lock:
                info.update(
                    block=self.w3.eth.block_number,
                    chain_id=self.chain_id,
                    operator=self.account.address,
                    total_rub=self.contract.functions.totalRub().call(),
                )
        return info


class ChainError(ValueError):
    """Контракт отклонил операцию (текст — из require)."""


def _revert_reason(e: Exception) -> str:
    text = str(e)
    for marker in ("execution reverted: ", "reverted: "):
        if marker in text:
            return "Блокчейн отклонил операцию: " + text.split(marker, 1)[1].strip(" '\")")
    return "Блокчейн отклонил операцию: " + text[:200]
