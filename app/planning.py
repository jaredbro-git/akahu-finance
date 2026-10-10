"""Budget vs actual (per pay cycle) and recurring payments, using the budget spreadsheet."""

import re
import sqlite3
from datetime import datetime

import pandas as pd

from . import analysis, budget, config

CYCLES = 6  # pay cycles shown on the Budget page, newest last
ON_BUDGET = 0.5  # dollars either way that still count as "as budgeted"
# Budget lines that never reach a bank account.
OFF_BANK = {"kiwisaver": "Taken from your pay before it reaches the bank"}


def _load(conn: sqlite3.Connection):
    accounts = analysis.load_accounts(conn)
    txns = analysis.load_transactions(conn, accounts)
    cash = analysis._cashflow_frame(txns)
    return accounts, txns, cash


def _today() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(config.TZ).date())


# --- Budget vs actual -----------------------------------------------------------------

def _match_account(line: str, accounts: pd.DataFrame) -> pd.Series | None:
    """Pair a budget line ("Bills") with a bank account ("For Bills")."""
    active = accounts[accounts["status"] == "ACTIVE"]
    names = active["name"].str.lower()
    wanted = config.BUDGET_ACCOUNTS.get(line.lower(), line).lower()
    for mask in (
        names == wanted,
        names.str.contains(rf"\b{re.escape(wanted)}\b", regex=True),
    ):
        if mask.sum() == 1:
            return active[mask].iloc[0]
    if wanted == "credit card":
        cards = active[active["type"] == "CREDITCARD"]
        if len(cards) == 1:
            return cards.iloc[0]
    return None


def budget_page(conn: sqlite3.Connection) -> dict:
    plan = budget.load()
    if plan.error:
        return {"error": plan.error}
    accounts, txns, cash = _load(conn)

    payday = analysis.payday_anchor(cash)
    if payday is None:
        return {"error": "Couldn't find your paydays. Set PAYDAY or INCOME_MATCH in .env."}
    starts = pd.date_range(
        end=analysis._period_start(pd.Series([_today()]), "fortnightly", payday).iloc[0],
        periods=CYCLES, freq="14D",
    )
    cycle = analysis._period_start(txns["date"], "fortnightly", payday)
    in_window = cycle.isin(starts)

    def per_cycle(values: pd.Series) -> list[float]:
        return values.groupby(cycle[values.index]).sum().reindex(starts, fill_value=0).round(2).tolist()

    rows = []
    for line, amount in plan.table("Bank Accounts").items():
        row = {"line": line, "budget": amount, "account": None, "actual": None, "balance": None, "note": None}
        if line.lower() in OFF_BANK:
            row["note"] = OFF_BANK[line.lower()]
        elif (account := _match_account(line, accounts)) is None:
            row["note"] = "No bank account with this name. Pair one with BUDGET_ACCOUNTS in .env."
        else:
            # Money put in: deposits and transfers into the account, not interest.
            put_in = txns[
                in_window & (txns["account_id"] == account["id"]) & (txns["amount"] > 0) & (txns["type"] != "INTEREST")
            ]["amount"]
            row.update(account=account["name"], actual=per_cycle(put_in), balance=account["balance_current"])
        rows.append(row)

    salary = cash[cash["is_salary"] & cash["date"].isin(txns.loc[in_window, "date"])]
    income_budget = plan.table("Income and Bills").get("Income")
    return {
        "error": None,
        "sheet": plan.sheet,
        "file": config.BUDGET_FILE,
        "cycles": [
            {"start": s, "end": s + pd.Timedelta(days=13), "current": i == CYCLES - 1}
            for i, s in enumerate(starts)
        ],
        "income": {
            "budget": income_budget,
            "actual": per_cycle(salary["income"]),
        },
        "rows": rows,
        "on_budget": ON_BUDGET,
        "bills": {t: plan.table(t) for t in ("Income and Bills", "Utilities") if plan.table(t)},
    }


# --- Recurring payments ---------------------------------------------------------------

# Charge cadence -> (days between charges, charges per month)
CADENCES = {"weekly": (7, 52 / 12), "fortnightly": (14, 26 / 12), "monthly": (30.4, 1), "quarterly": (91, 1 / 3), "yearly": (365, 1 / 12)}
PRICE_TOLERANCE = 0.35  # a charge within 35% of an expected amount is a candidate match
LOOKBACK_DAYS = 365
RECENT_MONTHS = 3  # the actual monthly cost averages the last few complete months


# Words too common in bank descriptions to identify a subscription on their own.
GENERIC_WORDS = {"car", "card", "the", "and", "nz", "new", "zealand", "insurance", "service", "services",
                 "payment", "subscription", "monthly", "plan", "ltd", "limited"}


def _words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 2 and w not in GENERIC_WORDS]


def _best_fit(amount: float, monthly: float) -> tuple[str, float] | None:
    """Which cadence makes this charge closest to the budgeted monthly price."""
    best = None
    for cadence, (_, per_month) in CADENCES.items():
        expected = monthly / per_month
        error = abs(amount - expected) / expected if expected else 1
        if error <= PRICE_TOLERANCE and (best is None or error < best[1]):
            best = (cadence, error)
    return best


def recurring_page(conn: sqlite3.Connection) -> dict:
    plan = budget.load()
    _, txns, _ = _load(conn)
    today = _today()
    # Real outgoing payments: not transfers between your accounts or to investments.
    out = txns[
        (txns["amount"] < 0) & ~txns["is_internal"] & ~txns["is_investment"]
        & (txns["date"] >= today - pd.Timedelta(days=LOOKBACK_DAYS))
    ].copy()
    out["charge"] = -out["amount"]
    text = (out["payee"].fillna("") + " " + out["description"].fillna("")).str.lower()

    # A charge is a candidate for a subscription when it shares a distinctive word with its
    # name (more shared words is a stronger match) and its price fits the budgeted price.
    words = [_words(item["name"]) for item in plan.subscriptions]
    candidates: dict[int, dict[int, tuple[int, float]]] = {}  # item -> charge -> (words matched, price error)
    for idx, charge, t in zip(out.index, out["charge"], text):
        for i, item in enumerate(plan.subscriptions):
            hits = sum(bool(re.search(rf"\b{re.escape(w)}", t)) for w in words[i])
            if not hits:
                continue
            fit = _best_fit(charge, item["monthly"])
            if fit:
                candidates.setdefault(i, {})[idx] = (hits, fit[1])
    # Keep each item's strongest matches, then give any charge claimed twice to the closer price.
    for i, found in candidates.items():
        top = max(h for h, _ in found.values())
        candidates[i] = {idx: v for idx, v in found.items() if v[0] == top}
    owner: dict[int, int] = {}
    for i, found in candidates.items():
        for idx, (_, err) in found.items():
            if idx not in owner or err < candidates[owner[idx]][idx][1]:
                owner[idx] = i

    items, used = [], set()
    for i, item in enumerate(plan.subscriptions):
        charges = out.loc[[idx for idx, o in owner.items() if o == i]]
        if not charges.empty:
            # Several payees can share a word (AA Insurance, AA New Zealand): keep the payee
            # whose charges best fit the budgeted price.
            err = pd.Series({idx: candidates[i][idx][1] for idx in charges.index})
            best_payee = err.groupby(charges["payee"]).mean().idxmin()
            charges = charges[charges["payee"] == best_payee].sort_values("date")
            used.update(charges.index)
        row = {**item, "last_date": None, "last_amount": None, "cadence": None, "actual_monthly": None,
               "count": len(charges), "payee": None, "change": None, "prices": [],
               "status": "missing", "message": "No matching payment in the last 12 months"}
        if not charges.empty:
            last = charges.iloc[-1]
            since = (today - last["date"]).days
            # Average over the last 3 complete calendar months, so several subscriptions
            # under one payee (or a fortnightly charge) add up to a true monthly cost.
            months = pd.period_range(end=pd.Period(today, "M") - 1, periods=RECENT_MONTHS, freq="M")
            recent = charges[charges["date"].dt.to_period("M").isin(months)]
            prices = sorted({round(c, 2) for c in recent["charge"]}, reverse=True)
            if not recent.empty:
                per_month = len(recent) / RECENT_MONTHS
                cadence = ("several a month" if len(prices) > 1 and per_month >= 2 else
                           "weekly" if per_month >= 3.5 else "fortnightly" if per_month >= 1.7 else "monthly")
                if cadence in ("weekly", "fortnightly"):
                    gap, per_month = CADENCES[cadence]  # 26 fortnights a year, not "3 this month"
                    actual_monthly = recent["charge"].mean() * per_month
                else:
                    gap = 30.4  # one charge (or set of charges) a month
                    actual_monthly = recent["charge"].sum() / RECENT_MONTHS
            else:
                # Nothing in the last 3 months: a new monthly charge, a yearly one, or stopped.
                fit = _best_fit(last["charge"], item["monthly"])
                cadence = fit[0] if fit and fit[0] in ("quarterly", "yearly") else "monthly"
                gap, per_month = CADENCES[cadence]
                actual_monthly = last["charge"] * per_month
            row.update(last_date=last["date"], last_amount=round(last["charge"], 2), cadence=cadence,
                       actual_monthly=round(float(actual_monthly), 2), payee=last["payee"], prices=prices)
            change = round(row["actual_monthly"] - item["monthly"], 2)
            if since > gap * 1.5 + 5:
                row.update(status="stale", message=f"Last charged {since} days ago, so it may have stopped")
            elif abs(change) > max(0.5, item["monthly"] * 0.02):
                row.update(status="up" if change > 0 else "down", change=change,
                           message="Costs more than budgeted" if change > 0 else "Costs less than budgeted")
            else:
                row.update(status="ok", message="As budgeted")
        items.append(row)

    budgeted = sum(i["monthly"] for i in items)
    actual = sum(i["actual_monthly"] or 0 for i in items if i["status"] not in ("stale", "missing"))
    return {
        "error": plan.error,
        "items": items,
        "totals": {
            "budget": round(budgeted, 2),
            "budget_active": round(sum(i["monthly"] for i in items if i["status"] not in ("stale", "missing")), 2),
            "actual": round(float(actual), 2),
        },
        "other": _other_recurring(out[~out.index.isin(used)], today),
    }


def _regular(g: pd.DataFrame, today: pd.Timestamp) -> dict | None:
    """If these payments repeat on a schedule at a steady price and are still happening, describe them."""
    g = g.sort_values("date")
    if len(g) < 3:
        return None
    gaps = g["date"].diff().dt.days.dropna()
    gaps = gaps[gaps > 0]
    if gaps.empty:
        return None
    median_gap = gaps.median()
    cadence = next(
        (c for c, lo, hi in (("weekly", 6, 8), ("fortnightly", 13, 15), ("monthly", 26, 35), ("quarterly", 85, 95))
         if lo <= median_gap <= hi),
        None,
    )
    if cadence is None:
        return None
    regular = gaps.between(median_gap * 0.7, median_gap * 1.3).mean() >= 0.6
    steady = g["charge"].std(ddof=0) <= 0.3 * g["charge"].mean()
    days, per_month = CADENCES[cadence]
    active = (today - g["date"].iloc[-1]).days <= days * 2 + 5
    if not (regular and steady and active):
        return None
    typical = round(float(g["charge"].median()), 2)
    return {"cadence": cadence, "typical": typical, "count": len(g),
            "monthly": round(typical * per_month, 2), "last_date": g["date"].iloc[-1]}


def _other_recurring(out: pd.DataFrame, today: pd.Timestamp) -> list[dict]:
    """Regular payments to the same payee at a steady price that aren't in the budget sheet.

    A payee with one-off payments too (rent to a flatmate you also pay back for dinner) is
    checked again per amount, so its regular payment still shows.
    """
    found = []
    for payee, g in out.groupby("payee"):
        if (hit := _regular(g, today)) is not None:
            found.append({"payee": payee, **hit})
            continue
        for amount, same in g.groupby(g["charge"].round(2)):
            # Needs more repeats than a whole payee, or three same-price lunches would count.
            if len(same) >= 4 and (hit := _regular(same, today)) is not None:
                found.append({"payee": payee, **hit})
    return sorted(found, key=lambda r: -r["monthly"])
