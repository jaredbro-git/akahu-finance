"""Turns the SQLite cache into the numbers the dashboard shows."""

import csv
import re
import sqlite3
from datetime import datetime

import pandas as pd

from . import config, manual

# Accounts whose money in/out counts as income and spending. Investment and
# KiwiSaver accounts are tracked through their balances instead.
EVERYDAY_TYPES = {"CHECKING", "SAVINGS", "CREDITCARD", "FOREIGN"}
# Money leaving one of your accounts and arriving in another within this window
# (same amount) is treated as a transfer, not spending or income.
TRANSFER_WINDOW = pd.Timedelta(days=3)
TOP_GROUPS = 7  # the rest fold into "Other" so the chart stays readable
UNCATEGORISED = "Uncategorised"


def _normalise_account_number(value: str | None) -> tuple[int, ...] | None:
    """'12-3456-0123456-00' and '12-3456-0123456-000' should compare equal."""
    if not isinstance(value, str):  # None, or NaN from pandas
        return None
    parts = re.findall(r"\d+", value)
    return tuple(int(p) for p in parts) if parts else None


def load_accounts(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT id, name, type, status, institution, formatted_account, "
        "balance_current, balance_available, currency FROM accounts ORDER BY institution, name",
        conn,
    )
    # Banks label bucket accounts as SAVINGS; only the configured one really is.
    savings = _normalise_account_number(config.SAVINGS_ACCOUNT)
    if savings:
        is_savings = df["formatted_account"].map(_normalise_account_number) == savings
        df.loc[df["type"] == "SAVINGS", "type"] = "CHECKING"
        df.loc[is_savings, "type"] = "SAVINGS"
    return df


def _savings_account(accounts: pd.DataFrame) -> pd.Series | None:
    savings = _normalise_account_number(config.SAVINGS_ACCOUNT)
    if not savings:
        return None
    match = accounts[accounts["formatted_account"].map(_normalise_account_number) == savings]
    return match.iloc[0] if not match.empty else None


def load_transactions(conn: sqlite3.Connection, accounts: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT t.id, t.account_id, t.date, t.description, t.amount, t.type, t.merchant, "
        "t.category, t.category_group, t.other_account, a.name AS account_name, "
        "a.type AS account_type "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id",
        conn,
        parse_dates=["date"],
    )
    df["is_internal"] = df["id"].isin(_internal_transfer_ids(df, accounts))
    df["category_group"] = df["category_group"].fillna(UNCATEGORISED)
    df["payee"] = df["merchant"].fillna(df["description"])
    _apply_category_rules(df)
    _apply_payee_names(df)  # after categories, whose rules may match the original text
    text = (df["payee"].fillna("") + " " + df["description"].fillna("")).str.lower()
    df["is_investment"] = text.apply(lambda t: any(m in t for m in config.INVESTMENT_MATCH))
    df["month"] = df["date"].dt.to_period("M")
    return df


def _read_rules(filename: str) -> list[list[str]]:
    """Rows of a rules file in the app folder: comments, blank lines and the header skipped."""
    path = config.BASE_DIR / filename
    if not path.exists():
        return []
    lines = [line for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return [[cell.strip() for cell in row] for row in csv.reader(lines) if row and row[0].strip().lower() != "match"]


def _pattern(match: str) -> re.Pattern:
    return re.compile(match[3:] if match.startswith("re:") else re.escape(match), re.IGNORECASE)


def _category_rules() -> list[tuple[re.Pattern, str, float | None]]:
    """Rules from categories.csv: text or re:pattern, category, optional exact amount. First match wins."""
    rules = []
    for row in _read_rules("categories.csv"):
        amount = row[2].lstrip("$") if len(row) > 2 else ""
        rules.append((_pattern(row[0]), row[1], float(amount) if amount else None))
    return rules


def _apply_payee_names(df: pd.DataFrame) -> None:
    """Give one name to a payee the bank describes in several ways (payees.csv)."""
    text = df["payee"].fillna("") + " " + df["description"].fillna("") + " " + df["other_account"].fillna("")
    todo = pd.Series(True, index=df.index)
    for row in _read_rules("payees.csv"):
        pattern = _pattern(row[0])
        hit = todo & text.map(lambda t: pattern.search(t) is not None)
        df.loc[hit, "payee"] = row[1]
        todo &= ~hit


def _apply_category_rules(df: pd.DataFrame) -> None:
    """Fill in a category for transactions Akahu didn't categorise."""
    text = df["payee"].fillna("") + " " + df["description"].fillna("")
    todo = df["category_group"] == UNCATEGORISED
    for pattern, category, amount in _category_rules():
        hit = todo & text.map(lambda t: pattern.search(t) is not None)
        if amount is not None:  # money in or out of exactly this amount
            hit &= (df["amount"].abs() - amount).abs() < 0.005
        df.loc[hit, "category_group"] = category
        todo &= ~hit


def _internal_transfer_ids(df: pd.DataFrame, accounts: pd.DataFrame) -> set[str]:
    """Best-effort detection of money moving between your own accounts."""
    own_numbers = {
        n for n in accounts["formatted_account"].map(_normalise_account_number) if n is not None
    }
    by_number = df["other_account"].map(_normalise_account_number).isin(own_numbers)
    ids = set(df.loc[by_number, "id"])

    cols = ["id", "account_id", "date"]
    out = df.loc[df["amount"] < 0, cols].assign(key=(-df["amount"]).round(2))
    into = df.loc[df["amount"] > 0, cols].assign(key=df["amount"].round(2))
    pairs = out.merge(into, on="key", suffixes=("_out", "_in"))
    pairs = pairs[
        (pairs["account_id_out"] != pairs["account_id_in"])
        & ((pairs["date_out"] - pairs["date_in"]).abs() <= TRANSFER_WINDOW)
    ]
    return ids | set(pairs["id_out"]) | set(pairs["id_in"])


def _regular_pay_payee(money_in: pd.DataFrame) -> str | None:
    """The payee of the largest income that arrives every ~14 days (your salary)."""
    best = None
    for payee, g in money_in.groupby("payee"):
        dates = g["date"].drop_duplicates().sort_values()
        if len(dates) < 4:
            continue
        if 13 <= dates.diff().dt.days.dropna().tail(6).median() <= 15:
            if best is None or g["amount"].sum() > best[1]:
                best = (payee, g["amount"].sum())
    return best[0] if best else None


def _cashflow_frame(txns: pd.DataFrame) -> pd.DataFrame:
    """Transactions that represent real money in or out of your everyday accounts.

    Only salary counts as income. Any other money in (flatmates' rent and bills,
    reimbursements, refunds) offsets spending, so spending is your share of costs.
    """
    # Money into or out of investments is your own money moving, like a transfer.
    cash = txns[txns["account_type"].isin(EVERYDAY_TYPES) & ~txns["is_internal"] & ~txns["is_investment"]].copy()
    money_in = cash["amount"] > 0
    match = config.INCOME_MATCH or _regular_pay_payee(cash[money_in])
    if match:
        text = cash["payee"].fillna("") + " " + cash["description"].fillna("")
        is_salary = money_in & text.str.contains(match, case=False, regex=False)
    else:
        is_salary = money_in & False
    cash["is_salary"] = is_salary
    cash["income"] = cash["amount"].where(is_salary, 0.0)
    cash["spend"] = (-cash["amount"]).where(~is_salary, 0.0)
    return cash


# The Spending page's period views, and its date ranges (months back from today).
PERIODS = ("daily", "weekly", "fortnightly", "monthly")
RANGES = {1: "Last month", 3: "Last 3 months", 6: "Last 6 months", 12: "Last 12 months", 24: "Last 24 months"}
DEFAULT_RANGE = 6
_STEP_DAYS = {"daily": 1, "weekly": 7, "fortnightly": 14}
UNIT = {"daily": "day", "weekly": "week", "fortnightly": "fortnight", "monthly": "month"}
_FALLBACK_ANCHOR = pd.Timestamp("2024-01-01")  # a Monday


def payday_anchor(cash: pd.DataFrame) -> pd.Timestamp | None:
    """A recent payday, so fortnights run payday to payday: PAYDAY from .env, or the last salary."""
    if config.PAYDAY:
        return pd.Timestamp(config.PAYDAY)
    paydays = cash.loc[cash["is_salary"], "date"]
    return paydays.max() if not paydays.empty else None


def _period_start(dates: pd.Series, freq: str, anchor: pd.Timestamp) -> pd.Series:
    d = dates.dt.normalize()
    if freq == "daily":
        return d
    if freq == "weekly":
        return d - pd.to_timedelta(d.dt.weekday, unit="D")
    if freq == "fortnightly":
        return d - pd.to_timedelta((d - anchor).dt.days % 14, unit="D")
    return d.dt.to_period("M").dt.start_time


def _period_index(freq: str, anchor: pd.Timestamp, months: int) -> pd.DatetimeIndex:
    """The periods covering the last `months` months, ending with the current (partial) one."""
    today = pd.Timestamp(datetime.now(config.TZ).date())
    current = _period_start(pd.Series([today]), freq, anchor).iloc[0]
    if freq == "monthly":
        return pd.date_range(end=current, periods=months, freq="MS")
    first = _period_start(pd.Series([today - pd.DateOffset(months=months) + pd.Timedelta(days=1)]), freq, anchor).iloc[0]
    return pd.date_range(start=first, end=current, freq=f"{_STEP_DAYS[freq]}D")


def _period_labels(index: pd.DatetimeIndex, freq: str) -> dict:
    """Short axis labels, plus fuller titles for tooltips and tables."""
    if freq == "monthly":
        return {"labels": [d.strftime("%b %Y") for d in index], "titles": [d.strftime("%B %Y") for d in index]}
    if freq == "daily":
        titles = [d.strftime("%a %-d %b %Y") for d in index]
    elif freq == "weekly":
        titles = [f"Week of {d:%-d %b %Y}" for d in index]
    else:
        titles = [f"{d:%-d %b} – {d + pd.Timedelta(days=13):%-d %b %Y}" for d in index]
    return {"labels": [d.strftime("%-d %b") for d in index], "titles": titles}


def cashflow(cash: pd.DataFrame, freq: str, anchor: pd.Timestamp, months: int) -> dict:
    index = _period_index(freq, anchor, months)
    period = _period_start(cash["date"], freq, anchor)
    income = cash["income"].groupby(period).sum().reindex(index, fill_value=0)
    spending = cash["spend"].groupby(period).sum().reindex(index, fill_value=0)
    return {
        **_period_labels(index, freq),
        "income": income.round(2).tolist(),
        "spending": spending.round(2).tolist(),
    }


def _grouped_spend(cash: pd.DataFrame, freq: str, anchor: pd.Timestamp, months: int) -> tuple[pd.DataFrame, pd.DatetimeIndex, list[str]]:
    """Spending in the view's window, with each category folded into the top few or "Other"."""
    index = _period_index(freq, anchor, months)
    spend = cash[~cash["is_salary"]].assign(period=_period_start(cash["date"], freq, anchor))
    spend = spend[spend["period"].isin(index)]
    totals = spend.groupby("category_group")["spend"].sum().sort_values(ascending=False)

    top = list(totals.index[:TOP_GROUPS])
    spend["group"] = spend["category_group"].where(spend["category_group"].isin(top), "Other")
    order = top + (["Other"] if len(totals) > TOP_GROUPS else [])
    return spend, index, order


def spend_by_group(cash: pd.DataFrame, freq: str, anchor: pd.Timestamp, months: int) -> dict:
    spend, index, order = _grouped_spend(cash, freq, anchor, months)
    table = (
        spend.pivot_table(index="period", columns="group", values="spend", aggfunc="sum")
        .reindex(index=index, columns=order)
        .fillna(0)
    )
    return {
        **_period_labels(index, freq),
        "series": [{"name": g, "data": table[g].round(2).tolist()} for g in order],
    }


TOP_MERCHANTS = 5


def category_breakdown(conn: sqlite3.Connection, freq: str, category: str, period: int | None, months: int) -> dict:
    """What makes up one category bar: its most frequent merchants, then everything else.

    With `period` (an index into the view), the actual amounts in that period; without
    it, the average per period over the view's complete periods, like the Average chart.
    """
    if freq not in PERIODS:
        raise ValueError(f"unknown period {freq!r}")
    if months not in RANGES:
        raise ValueError(f"unknown range {months!r}")
    accounts = load_accounts(conn)
    cash = _cashflow_frame(load_transactions(conn, accounts))
    payday = payday_anchor(cash)
    spend, index, _ = _grouped_spend(cash, freq, payday if payday is not None else _FALLBACK_ANCHOR, months)
    labels = _period_labels(index, freq)
    spend = spend[spend["group"] == category]

    if period is not None:
        if not 0 <= period < len(index):
            raise ValueError("period out of range")
        spend = spend[spend["period"] == index[period]]
        divisor, heading = 1, labels["titles"][period]
    else:
        complete = index[:-1]  # the current period isn't over yet
        spend = spend[spend["period"].isin(complete)]
        divisor, heading = len(complete), f"Average per {UNIT[freq]}, over the last {len(complete)} full {UNIT[freq]}s"

    by_payee = spend.groupby("payee").agg(count=("spend", "size"), total=("spend", "sum"))
    by_payee = by_payee.sort_values(["count", "total"], ascending=False)
    top, rest = by_payee.head(TOP_MERCHANTS), by_payee.iloc[TOP_MERCHANTS:]
    rows = [
        {"name": name, "count": int(r["count"]), "amount": round(r["total"] / divisor, 2)}
        for name, r in top.iterrows()
    ]
    if not rest.empty:
        rows.append({
            "name": f"Other ({len(rest)} merchant{'s' if len(rest) != 1 else ''})", "other": True,
            "count": int(rest["count"].sum()), "amount": round(rest["total"].sum() / divisor, 2),
        })

    result = {
        "category": category,
        "heading": heading,
        "mode": "actual" if period is not None else "average",
        "total": round(spend["spend"].sum() / divisor, 2),
        "count": int(len(spend)),
        "merchants": rows,
        "transactions": [],
    }
    if period is not None:
        txns = spend.sort_values("date", ascending=False)
        result["transactions"] = [
            {"date": d.strftime("%a %-d %b"), "payee": p, "account": a, "amount": round(x, 2), "category": c}
            for d, p, a, x, c in zip(txns["date"], txns["payee"], txns["account_name"], txns["spend"], txns["category_group"])
        ]
    return result


def top_payees(cash: pd.DataFrame, months: int, n: int = 10) -> dict:
    since = pd.Timestamp(datetime.now(config.TZ).date()) - pd.DateOffset(months=months)
    recent = cash[(cash["amount"] < 0) & (cash["date"] >= since)]
    totals = (-recent.groupby("payee")["amount"].sum()).sort_values(ascending=False).head(n)
    return {"labels": totals.index.tolist(), "values": totals.round(2).tolist()}


def net_worth_history(conn: sqlite3.Connection) -> dict:
    snaps = pd.read_sql_query(
        "SELECT s.account_id, s.snapshot_date, s.balance FROM balance_snapshots s "
        "JOIN accounts a ON a.id = s.account_id WHERE a.status = 'ACTIVE'",
        conn,
        parse_dates=["snapshot_date"],
    )
    if snaps.empty:
        return {"labels": [], "values": [], "with_other": None}
    # Carry each account's last known balance forward so a missing snapshot isn't read as $0.
    wide = snaps.pivot_table(index="snapshot_date", columns="account_id", values="balance").ffill().fillna(0)
    total = wide.sum(axis=1)
    has_other = conn.execute("SELECT 1 FROM manual_values WHERE field = 'balance' LIMIT 1").fetchone()
    return {
        "labels": [d.strftime("%-d %b %Y") for d in total.index],
        "values": total.round(2).tolist(),
        # Plus KiwiSaver and investments, minus the student loan (Other accounts page).
        "with_other": (total + manual.balances_on(conn, total.index)).round(2).tolist() if has_other else None,
    }


def _balance_history(own: pd.DataFrame, balance: float, months: int) -> dict:
    """Month-end balances and money in/out for complete months, worked back from today.

    The page projects forward from these. Net change (in minus out) is what the
    projection uses, so money that leaves again doesn't count as saved.
    """
    current = pd.Period(datetime.now(config.TZ).date(), freq="M")
    index = pd.period_range(end=current - 1, periods=months, freq="M")
    first = own["month"].min()
    if not pd.isna(first):
        index = index[index >= first]  # no flat $0 line from before the account existed

    by_month = own.groupby("month")["amount"]
    money_in = by_month.apply(lambda s: s[s > 0].sum()).reindex(index, fill_value=0)
    money_out = -by_month.apply(lambda s: s[s < 0].sum()).reindex(index, fill_value=0)
    # Each month-end balance is today's balance minus everything that happened after it.
    month_ends = [balance - own.loc[own["month"] > p, "amount"].sum() for p in index]
    # The first month with transactions holds the account's opening deposit (often money
    # moved from another account), so the current pace only counts full months after it.
    pace_start = len(index) if pd.isna(first) else int((index <= first).sum())
    return {
        "pace_start": pace_start,
        "balance": round(balance, 2),
        "labels": [p.strftime("%b %Y") for p in index],
        "money_in": money_in.round(2).tolist(),
        "money_out": money_out.round(2).tolist(),
        "month_end_balance": [round(b, 2) for b in month_ends],
    }


def projections(txns: pd.DataFrame, accounts: pd.DataFrame, months: int = 24) -> dict:
    active = accounts[accounts["status"] == "ACTIVE"]
    result = {
        "start_label": pd.Period(datetime.now(config.TZ).date(), freq="M").strftime("%b %Y"),
        "net_worth": _balance_history(
            txns[txns["account_id"].isin(active["id"])],
            float(active["balance_current"].fillna(0).sum()),
            months,
        ),
        "savings": None,
    }
    account = _savings_account(accounts)
    if account is not None:
        result["savings"] = {
            "account_name": account["name"],
            **_balance_history(
                txns[txns["account_id"] == account["id"]],
                float(account["balance_current"] or 0),
                months,
            ),
        }
    return result


def _records(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    out = df[columns].copy()
    if "date" in out:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%-d %b")
    return out.astype(object).where(out.notna(), None).to_dict("records")


def dashboard_data(conn: sqlite3.Connection) -> dict:
    accounts = load_accounts(conn)
    txns = load_transactions(conn, accounts)
    cash = _cashflow_frame(txns)

    active = accounts[accounts["status"] == "ACTIVE"]
    balances = active["balance_current"].fillna(0)

    this_month = pd.Period(datetime.now(config.TZ).date(), freq="M")
    month = cash[cash["month"] == this_month]

    other = manual.current(conn)
    payday = payday_anchor(cash)
    monthly = cashflow(cash, "monthly", payday if payday is not None else _FALLBACK_ANCHOR, 12)
    full_months_spend = monthly["spending"][-7:-1]  # last 6 complete months
    income_12m, spend_12m = sum(monthly["income"]), sum(monthly["spending"])

    pending = pd.read_sql_query(
        "SELECT p.date, p.description, p.amount, a.name AS account_name "
        "FROM pending_transactions p JOIN accounts a ON a.id = p.account_id ORDER BY p.date DESC",
        conn,
    )
    recent = txns.sort_values("date", ascending=False).head(50)

    return {
        "has_data": not accounts.empty,
        "other": other,
        "other_names": [manual.SHORT_NAMES[item] for item in manual.ITEMS if other[item].get("balance")],
        "tiles": {
            "net_worth": float(balances.sum()),
            "net_worth_with_other": float(balances.sum()) + manual.net_total(other),
            "spend_this_month": float(month["spend"].sum()),
            "income_this_month": float(month["income"].sum()),
            "avg_monthly_spend": (
                sum(full_months_spend) / len(full_months_spend) if full_months_spend else 0.0
            ),
            "savings_rate_12m": (income_12m - spend_12m) / income_12m if income_12m else None,
        },
        "accounts": _records(
            active, ["name", "type", "institution", "formatted_account", "balance_current"]
        ),
        "recent": _records(
            recent,
            ["date", "payee", "category_group", "account_name", "amount", "is_internal", "is_investment"],
        ),
        "pending": _records(pending, ["date", "description", "account_name", "amount"]),
        "charts": {"net_worth": net_worth_history(conn)},
    }


def spending_data(conn: sqlite3.Connection, months: int) -> dict:
    accounts = load_accounts(conn)
    cash = _cashflow_frame(load_transactions(conn, accounts))
    payday = payday_anchor(cash)
    anchor = payday if payday is not None else _FALLBACK_ANCHOR
    return {
        "has_data": not accounts.empty,
        "range": months,
        "range_label": RANGES[months],
        "range_options": list(RANGES.items()),
        "charts": {
            "range": months,
            "cashflow": {f: cashflow(cash, f, anchor, months) for f in PERIODS},
            "spend_by_group": {f: spend_by_group(cash, f, anchor, months) for f in PERIODS},
            "payday": payday.strftime("%A") if payday is not None else None,
            "top_payees": top_payees(cash, months),
        },
    }


def projections_data(conn: sqlite3.Connection) -> dict:
    accounts = load_accounts(conn)
    txns = load_transactions(conn, accounts)
    other = manual.current(conn)
    pj = projections(txns, accounts)
    pj["other"] = {
        item: {"label": label, "sign": sign, **{k: v for k, v in other[item].items() if k != "updated"}}
        for item, (label, sign, _) in manual.ITEMS.items()
    }
    pj["default_return"] = manual.DEFAULT_RETURN
    return {
        "has_data": not accounts.empty,
        "other_names": [manual.SHORT_NAMES[item] for item in manual.ITEMS if other[item].get("balance")],
        "charts": {"projections": pj},
    }
