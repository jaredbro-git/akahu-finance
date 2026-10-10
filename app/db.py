"""SQLite storage: a local cache of everything pulled from Akahu."""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL,
    status            TEXT NOT NULL,
    institution       TEXT,
    formatted_account TEXT,
    balance_current   REAL,
    balance_available REAL,
    currency          TEXT,
    refreshed_at      TEXT,
    raw               TEXT NOT NULL
);

-- One balance per account per day the app synced; this builds net-worth history.
CREATE TABLE IF NOT EXISTS balance_snapshots (
    account_id    TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    balance       REAL NOT NULL,
    currency      TEXT,
    PRIMARY KEY (account_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS transactions (
    id             TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    date           TEXT NOT NULL,   -- local calendar date, YYYY-MM-DD
    occurred_at    TEXT NOT NULL,   -- Akahu's original timestamp
    description    TEXT,
    amount         REAL NOT NULL,   -- negative = money out
    balance        REAL,
    type           TEXT,
    merchant       TEXT,
    category       TEXT,
    category_group TEXT,
    other_account  TEXT,
    raw            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date);

-- Pending transactions have no stable ID, so they are replaced wholesale each sync.
CREATE TABLE IF NOT EXISTS pending_transactions (
    account_id  TEXT NOT NULL,
    date        TEXT NOT NULL,
    description TEXT,
    amount      REAL NOT NULL,
    type        TEXT,
    raw         TEXT NOT NULL
);

-- Accounts Akahu can't see (KiwiSaver, investments, student loan), entered on the
-- Other accounts page. One row per value per day it changed, so net worth has history.
CREATE TABLE IF NOT EXISTS manual_values (
    item        TEXT NOT NULL,   -- kiwisaver, investment, student_loan
    field       TEXT NOT NULL,   -- balance, contrib, return, repay
    recorded_on TEXT NOT NULL,   -- local date, YYYY-MM-DD
    value       REAL NOT NULL,
    PRIMARY KEY (item, field, recorded_on)
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _local_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp).astimezone(config.TZ).date().isoformat()


def upsert_accounts(conn: sqlite3.Connection, accounts: list[dict], snapshot_date: date) -> None:
    for acc in accounts:
        balance = acc.get("balance") or {}
        conn.execute(
            """
            INSERT INTO accounts (id, name, type, status, institution, formatted_account,
                                  balance_current, balance_available, currency, refreshed_at, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                name = excluded.name, type = excluded.type, status = excluded.status,
                institution = excluded.institution, formatted_account = excluded.formatted_account,
                balance_current = excluded.balance_current,
                balance_available = excluded.balance_available,
                currency = excluded.currency, refreshed_at = excluded.refreshed_at, raw = excluded.raw
            """,
            (
                acc["_id"],
                acc["name"],
                acc["type"],
                acc["status"],
                (acc.get("connection") or {}).get("name"),
                acc.get("formatted_account"),
                balance.get("current"),
                balance.get("available"),
                balance.get("currency"),
                (acc.get("refreshed") or {}).get("balance"),
                json.dumps(acc),
            ),
        )
        if balance.get("current") is not None:
            conn.execute(
                """
                INSERT INTO balance_snapshots (account_id, snapshot_date, balance, currency)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (account_id, snapshot_date) DO UPDATE SET balance = excluded.balance
                """,
                (acc["_id"], snapshot_date.isoformat(), balance["current"], balance.get("currency")),
            )


def upsert_transactions(conn: sqlite3.Connection, transactions: Iterable[dict]) -> int:
    count = 0
    for txn in transactions:
        merchant = txn.get("merchant") or {}
        category = txn.get("category") or {}
        group = ((category.get("groups") or {}).get("personal_finance") or {}).get("name")
        conn.execute(
            """
            INSERT INTO transactions (id, account_id, date, occurred_at, description, amount,
                                      balance, type, merchant, category, category_group,
                                      other_account, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                account_id = excluded.account_id, date = excluded.date,
                occurred_at = excluded.occurred_at, description = excluded.description,
                amount = excluded.amount, balance = excluded.balance, type = excluded.type,
                merchant = excluded.merchant, category = excluded.category,
                category_group = excluded.category_group,
                other_account = excluded.other_account, raw = excluded.raw
            """,
            (
                txn["_id"],
                txn["_account"],
                _local_date(txn["date"]),
                txn["date"],
                txn.get("description"),
                txn["amount"],
                txn.get("balance"),
                txn.get("type"),
                merchant.get("name"),
                category.get("name"),
                group,
                (txn.get("meta") or {}).get("other_account"),
                json.dumps(txn),
            ),
        )
        count += 1
    return count


def replace_pending(conn: sqlite3.Connection, pending: list[dict]) -> None:
    conn.execute("DELETE FROM pending_transactions")
    conn.executemany(
        "INSERT INTO pending_transactions (account_id, date, description, amount, type, raw) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                p["_account"],
                _local_date(p["date"]),
                p.get("description"),
                p["amount"],
                p.get("type"),
                json.dumps(p),
            )
            for p in pending
        ],
    )
