# Akahu Finance

A personal dashboard for your NZ bank and credit card accounts, using Akahu's API.
It runs only on your machine, at http://127.0.0.1:8000.

## Setup

1. Copy `.env.example` to `.env` and paste in your two tokens from my.akahu.nz → Developers.
   Optionally copy `categories.example.csv` and `payees.example.csv` to `categories.csv` and `payees.csv`.
2. Run `./run.sh`. The first run creates `.venv` and installs the dependencies.
3. Open http://127.0.0.1:8000.

## How data is fetched

- The **first page load each day** (NZ time) pulls accounts, new transactions and pending
  transactions from Akahu into `data/finance.db`. Later loads that day read only from that file.
- Skipped days are caught up on the next sync, which re-reads from 7 days before the last sync.
- **Refresh from banks** asks Akahu to fetch new data from your banks (at most once an hour).
  The page reloads with the new data about two minutes later.
- `python -m app.sync` (with `.venv` active) forces a sync from the terminal.

## Code layout

| File | What it does |
|---|---|
| `app/akahu.py` | Akahu API client (auth headers, pagination, retries) |
| `app/db.py` | SQLite tables and the code that saves Akahu data |
| `app/sync.py` | Decides when to sync and runs it |
| `app/analysis.py` | pandas: spending, income, transfers, net worth |
| `app/budget.py` | Reads the budgeting spreadsheet |
| `app/planning.py` | Budget vs actual per pay cycle, and recurring payments |
| `app/manual.py` | Accounts entered by hand on the Other accounts page |
| `app/main.py` | FastAPI routes |
| `app/templates/` | One template per page, sharing `base.html` (header, nav, Hide numbers) |
| `app/static/charts.js` | Shared Chart.js setup and helpers; `dashboard.js`, `spending.js` and `projections.js` draw each page |

## Pages

- **Dashboard:** summary tiles, net worth, accounts and recent transactions.
- **Spending:** spending by category, income vs spending and top payees. One date range
  (last 1 to 24 months) covers every chart on the page and is remembered. Each chart has its own
  Daily/Weekly/Fortnightly/Monthly and Actual/Average switches. Click a category bar for a breakdown.
- **Projections:** savings account and net worth projections, with "what if" controls and a
  switch to include the accounts from Other accounts.
- **Other accounts:** KiwiSaver, an investment account and a student loan, which Akahu can't see.
  Each save is recorded against that day in the database, so the Dashboard's net worth graph can
  show a second line that includes them (switch on the graph).
- **Budget** and **Recurring:** see below.

## Budget and Recurring pages

Both read your budgeting spreadsheet (`BUDGET_FILE` in `.env`), which is the source of truth.
Edits show up on the next page load.

- **Budget** uses the "Bank Accounts" table on the sheet named by `BUDGET_SHEET`. For the last 6
  pay cycles, it compares each line's fortnightly amount with what actually went into the bank
  account of the same name (e.g. "Bills" → "Bills Account"). If the names don't match, pair them
  with `BUDGET_ACCOUNTS=Budget Line=Account Name` (comma-separated).
- **Recurring** matches each item on the Subscriptions sheet to payments that share a word with
  its name and fit its price. The actual monthly cost is averaged over the last 3 full months.
  It also lists other payments that repeat at a steady price but aren't on the sheet.

## Notes

- Transfers between your own accounts are left out of spending and income. They're detected
  by the other account's number, or by the same amount leaving one of your accounts and
  arriving in another within 3 days. Adjust `_internal_transfer_ids` in `app/analysis.py`
  if your data needs it.
- `SAVINGS_ACCOUNT` in `.env` names your one real savings account. It's shown as Savings and
  drives the savings projection. Every other account the bank labels Savings is shown as Checking.
- **Categories:** Akahu only categorises merchants it recognises. `categories.csv` fills in the
  rest (rent, cash withdrawals, overseas purchases, bills paid by transfer) by matching text in
  the description. The first matching rule wins, and rules never override Akahu's own categories.
- **Payee names:** `payees.csv` gives one name to a payee the bank describes in several ways
  (e.g. "TFR TO Payer Name" and "XX-XXXX-XXXXXXX-XX" both become "Payer Name"). Rules can match the
  description or the other party's account number.
- `categories.csv` and `payees.csv` hold your own rules, so they aren't committed. Start from
  `categories.example.csv` and `payees.example.csv`. Without them, no extra rules apply.
- **Income is your salary only**: deposits whose text contains `INCOME_MATCH` (e.g. "Employer Salaries").
  Other money in (flatmates' rent and bills, reimbursements, refunds) is subtracted from spending,
  so spending is your share of costs. A single day can show negative spending when a
  reimbursement lands on a different day from the purchase.
- Money to and from investments (`INVESTMENT_MATCH`, comma-separated) is left out of income and
  spending, like a transfer. It shows as "Investment" in the transactions table.
- Weeks start on Monday, and fortnights run payday to payday. The payday
  is found from income that arrives every ~14 days; set `PAYDAY=YYYY-MM-DD` in `.env` to override.
- **Projections** (Savings account and Net worth tabs) grow today's balance by its average net
  change per month (money in minus money out) over the chosen period, for 5 years. The "what if"
  amount per fortnight draws a second line on the same chart (×26/12 per month).
- With the switch on, net worth projections add the KiwiSaver balance and investment account and
  subtract the student loan from the Other accounts page. Each grows at its own return (5% a year
  unless you set one). Before the first value was saved, the net worth graph assumes it was the same.
  KiwiSaver contributions and loan repayments are taken out of your pay before it reaches the bank,
  so they aren't counted twice. The student loan is assumed to be interest-free (NZ-based borrower).
- Net worth is the sum of all account balances. Akahu reports money owed on cards and loans
  as a negative balance, so card debt is subtracted automatically.
- Spending and income only count everyday accounts (checking, savings, credit card). If you
  connect KiwiSaver or investment accounts later, they count toward net worth via balances.
- Each sync saves a copy of Akahu's raw JSON in the `raw` column, so new analyses can use
  fields that aren't in their own columns yet.
