"""Accounts Akahu can't see, entered by hand on the Other accounts page.

Each saved value is recorded against the day it was entered, so the net worth history
can include these accounts from then on.
"""

import sqlite3
from datetime import datetime

import pandas as pd

from . import config

# item -> (label, sign in net worth, {field: label})
ITEMS = {
    "kiwisaver": ("KiwiSaver", 1, {
        "balance": "Balance",
        "contrib": "Contributions per fortnight (yours + employer's)",
        "return": "Expected return, % a year",
    }),
    "investment": ("Investment account", 1, {
        "balance": "Value",
        "contrib": "Added per fortnight",
        "return": "Expected return, % a year",
    }),
    "student_loan": ("Student loan", -1, {
        "balance": "Balance owed",
        "repay": "Repayments per fortnight",
    }),
}
DEFAULT_RETURN = 5.0
# How each reads mid-sentence, e.g. "Include KiwiSaver, investments and student loan".
SHORT_NAMES = {"kiwisaver": "KiwiSaver", "investment": "investments", "student_loan": "student loan"}


def _today() -> str:
    return datetime.now(config.TZ).date().isoformat()


def current(conn: sqlite3.Connection) -> dict:
    """The latest value of every field: {item: {field: value, "updated": date}}."""
    rows = conn.execute(
        "SELECT item, field, value, recorded_on FROM manual_values m WHERE recorded_on = "
        "(SELECT MAX(recorded_on) FROM manual_values WHERE item = m.item AND field = m.field)"
    ).fetchall()
    values: dict[str, dict] = {item: {} for item in ITEMS}
    for r in rows:
        if r["item"] in values:
            values[r["item"]][r["field"]] = r["value"]
            values[r["item"]]["updated"] = max(values[r["item"]].get("updated", ""), r["recorded_on"])
    return values


def save(conn: sqlite3.Connection, submitted: dict[str, str]) -> int:
    """Record changed values against today. Keys look like "kiwisaver.balance". Returns how many changed."""
    latest = current(conn)
    changed = 0
    for item, (_, _, fields) in ITEMS.items():
        for field in fields:
            key = f"{item}.{field}"
            if key not in submitted:  # not on the form, so leave it alone
                continue
            raw = submitted[key].strip().replace(",", "").lstrip("$")
            before = latest[item].get(field)
            if raw == "":
                if before is None:
                    continue
                value = 0.0  # cleared a value that was set before
            else:
                value = float(raw)
            if before is not None and abs(before - value) < 0.005:
                continue
            conn.execute(
                "INSERT INTO manual_values (item, field, recorded_on, value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (item, field, recorded_on) DO UPDATE SET value = excluded.value",
                (item, field, _today(), value),
            )
            changed += 1
    return changed


def net_total(values: dict) -> float:
    """KiwiSaver + investments − student loan, from current() values."""
    return sum(sign * values[item].get("balance", 0.0) for item, (_, sign, _) in ITEMS.items())


def balances_on(conn: sqlite3.Connection, dates: pd.DatetimeIndex) -> pd.Series:
    """Net of these accounts on each date, using the value as last saved on or before it.

    Dates before the first save use the first saved value, since that's the best estimate.
    """
    rows = pd.read_sql_query(
        "SELECT item, recorded_on, value FROM manual_values WHERE field = 'balance'",
        conn, parse_dates=["recorded_on"],
    )
    if rows.empty or dates.empty:
        return pd.Series(0.0, index=dates)
    wide = rows.pivot_table(index="recorded_on", columns="item", values="value")
    wide = wide.reindex(wide.index.union(dates)).sort_index().ffill().bfill().reindex(dates).fillna(0)
    signs = pd.Series({item: sign for item, (_, sign, _) in ITEMS.items()})
    return (wide * signs.reindex(wide.columns).fillna(0)).sum(axis=1)
