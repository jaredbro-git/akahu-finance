import time
from datetime import datetime
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analysis, config, db, manual, planning, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Akahu Finance", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.BASE_DIR / "app" / "templates")


def money(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "–"
    sign = "−" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


templates.env.filters["money"] = money
templates.env.filters["nice_date"] = lambda iso: datetime.fromisoformat(iso).strftime("%-d %b %Y")
# Changes on every server start (and --reload restarts on code changes), so browsers
# fetch fresh CSS and JS instead of a cached copy.
templates.env.globals["asset_version"] = str(int(time.time()))


def _page(request: Request, template: str, build, notice: str | None = None):
    """Render a page with the shared header (sync status, refresh button)."""
    sync_error = sync.ensure_synced()
    with db.connect() as conn:
        data = build(conn)
        status = sync.status(conn)
    return templates.TemplateResponse(
        request, template, {**data, "status": status, "sync_error": sync_error, "notice": notice}
    )


# Plain `def` routes run in FastAPI's thread pool, so a slow sync doesn't block the server.
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, notice: str | None = None):
    return _page(request, "dashboard.html", analysis.dashboard_data, notice)


@app.get("/spending", response_class=HTMLResponse)
def spending_page(request: Request, range: int | None = None, notice: str | None = None):
    # The chosen range is remembered in a cookie, so it sticks between visits.
    if range not in analysis.RANGES:
        saved = request.cookies.get("spending_range", "")
        range = int(saved) if saved.isdigit() and int(saved) in analysis.RANGES else analysis.DEFAULT_RANGE
    response = _page(request, "spending.html", lambda conn: analysis.spending_data(conn, range), notice)
    response.set_cookie("spending_range", str(range), max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@app.get("/projections", response_class=HTMLResponse)
def projections_page(request: Request, notice: str | None = None):
    return _page(request, "projections.html", analysis.projections_data, notice)


def _other_accounts_data(conn) -> dict:
    values = manual.current(conn)
    has_any = any(v for v in values.values())
    return {
        "items": manual.ITEMS,
        "values": values,
        "empty": not has_any,
        "total": manual.net_total(values) if has_any else None,
        "default_return": manual.DEFAULT_RETURN,
    }


@app.get("/other-accounts", response_class=HTMLResponse)
def other_accounts_page(request: Request, notice: str | None = None):
    return _page(request, "other_accounts.html", _other_accounts_data, notice)


@app.post("/other-accounts")
async def save_other_accounts(request: Request):
    # A plain HTML form post; parsed here so the app doesn't need python-multipart.
    form = {k: v[-1] for k, v in parse_qs((await request.body()).decode(), keep_blank_values=True).items()}
    try:
        with db.connect() as conn:
            changed = manual.save(conn, form)
    except ValueError:
        return RedirectResponse(f"/other-accounts?notice={quote('Please enter numbers only.')}", status_code=303)
    message = "Saved." if changed else "Nothing changed."
    return RedirectResponse(f"/other-accounts?notice={quote(message)}", status_code=303)


@app.get("/budget", response_class=HTMLResponse)
def budget_page(request: Request, notice: str | None = None):
    return _page(request, "budget.html", planning.budget_page, notice)


@app.get("/recurring", response_class=HTMLResponse)
def recurring_page(request: Request, notice: str | None = None):
    return _page(request, "recurring.html", planning.recurring_page, notice)


@app.get("/api/category-breakdown")
def category_breakdown(freq: str, category: str, range: int, period: int | None = None):
    """Data for the popup when a category bar is clicked."""
    try:
        with db.connect() as conn:
            return analysis.category_breakdown(conn, freq, category, period, range)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/refresh")
def refresh(request: Request):
    message = sync.request_bank_refresh()
    # Back to the page the button was pressed on.
    referer = urlparse(request.headers.get("referer", "")).path
    back = referer if referer in ("/", "/spending", "/projections", "/other-accounts", "/budget", "/recurring") else "/"
    return RedirectResponse(f"{back}?notice={quote(message)}", status_code=303)
