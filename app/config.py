import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

AKAHU_APP_TOKEN = os.getenv("AKAHU_APP_TOKEN", "").strip()
AKAHU_USER_TOKEN = os.getenv("AKAHU_USER_TOKEN", "").strip()

DB_PATH = BASE_DIR / os.getenv("DB_PATH", "data/finance.db")

# The account number of your one real savings account. It is shown as Savings and
# drives the savings projection; every other bank-labelled savings account is
# shown as Checking, since those are used as spending buckets.
SAVINGS_ACCOUNT = os.getenv("SAVINGS_ACCOUNT", "").strip()

# "Today" for the once-a-day sync is judged in this timezone.
TZ = ZoneInfo(os.getenv("TIMEZONE", "Pacific/Auckland"))

# Any one of your paydays (YYYY-MM-DD). Fortnightly charts run payday to payday.
# Left blank, it's worked out from income that arrives every ~14 days.
PAYDAY = os.getenv("PAYDAY", "").strip()

# Text that identifies your salary in a deposit's description (e.g. "Employer Salaries").
# Only matching deposits count as income; other money in reduces spending instead.
# Left blank, it's the largest deposit that arrives every ~14 days.
INCOME_MATCH = os.getenv("INCOME_MATCH", "").strip()

# Your budgeting spreadsheet, read fresh whenever it changes. BUDGET_SHEET picks the tab
# (blank = the first one with a "Bank Accounts" table).
BUDGET_FILE = os.getenv("BUDGET_FILE", "").strip()
BUDGET_SHEET = os.getenv("BUDGET_SHEET", "").strip()
# Pairs a budget line with a bank account when their names don't line up,
# e.g. "Budget Line=Account Name, Other Line=Other Account".
BUDGET_ACCOUNTS = dict(
    (k.strip().lower(), v.strip())
    for k, _, v in (p.partition("=") for p in os.getenv("BUDGET_ACCOUNTS", "").split(","))
    if k.strip() and v.strip()
)

# Comma-separated text identifying deposits to and withdrawals from your investments
# (e.g. "Investment Platform, Managed Fund"). These are left out of income and spending.
INVESTMENT_MATCH = [m.strip().lower() for m in os.getenv("INVESTMENT_MATCH", "").split(",") if m.strip()]
