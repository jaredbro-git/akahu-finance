"""Decides when to pull from Akahu, and does the pulling.

The rule: the first page load of each day (local time) syncs; later loads read
from SQLite only. Days the app isn't opened cost nothing, and the next sync
catches up on everything since the last one.
"""

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

from . import config, db
from .akahu import AkahuClient, AkahuError

# Re-fetch a few days before the last sync to pick up late-posting transactions
# and enrichment (merchant/category) that Akahu adds after the fact.
OVERLAP = timedelta(days=7)
# Personal apps may ask Akahu to refresh from the banks once an hour.
REFRESH_COOLDOWN = timedelta(hours=1)
# How long to give Akahu to fetch from the banks before we pull the results.
REFRESH_SETTLE = timedelta(minutes=2)
# Don't retry a failed sync on every page load if Akahu is down.
FAILURE_BACKOFF = timedelta(minutes=15)

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return datetime.now(config.TZ).date()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def needs_sync(conn) -> bool:
    last_synced = _parse(db.get_state(conn, "last_synced_at"))
    last_failed = _parse(db.get_state(conn, "last_failed_at"))
    if last_failed and (last_synced is None or last_failed > last_synced):
        if _now() - last_failed < FAILURE_BACKOFF:
            return False

    if last_synced is None:
        return True
    if last_synced.astimezone(config.TZ).date() < _today():
        return True

    # A bank refresh was requested after the last sync: pull once it has had time to land.
    refresh_requested = _parse(db.get_state(conn, "refresh_requested_at"))
    return bool(
        refresh_requested
        and refresh_requested > last_synced
        and _now() - refresh_requested >= REFRESH_SETTLE
    )


def run_sync(conn) -> int:
    """Pull accounts, transactions and pending transactions. Returns transactions upserted."""
    started = _now()
    client = AkahuClient()
    try:
        db.upsert_accounts(conn, client.accounts(), _today())

        last_synced = _parse(db.get_state(conn, "last_synced_at"))
        start = last_synced - OVERLAP if last_synced else None
        count = db.upsert_transactions(conn, client.transactions(start=start))

        db.replace_pending(conn, client.pending_transactions())
        db.set_state(conn, "last_synced_at", started.isoformat())
        db.set_state(conn, "last_error", None)
        return count
    finally:
        client.close()


def ensure_synced() -> str | None:
    """Sync if today's sync hasn't happened yet. Returns an error message, or None."""
    with db.connect() as conn:
        if not needs_sync(conn):
            return None

    # Only one sync at a time; a request that waited re-checks before syncing again.
    with _lock:
        try:
            with db.connect() as conn:
                if needs_sync(conn):
                    run_sync(conn)
            return None
        except (AkahuError, httpx.HTTPError) as exc:
            with db.connect() as conn:
                db.set_state(conn, "last_failed_at", _now().isoformat())
                db.set_state(conn, "last_error", str(exc))
            return str(exc)


def request_bank_refresh() -> str:
    """Ask Akahu to re-fetch from the banks, respecting the hourly limit. Returns a message."""
    with db.connect() as conn:
        last = _parse(db.get_state(conn, "refresh_requested_at"))
        if last and _now() - last < REFRESH_COOLDOWN:
            available = (last + REFRESH_COOLDOWN).astimezone(config.TZ)
            return f"You can refresh again at {available:%-I:%M %p}."

        try:
            client = AkahuClient()
            try:
                client.refresh()
            finally:
                client.close()
        except AkahuError as exc:
            if exc.status_code == 429:
                return "Akahu says it refreshed recently. Try again later."
            return f"Refresh failed: {exc}"
        except httpx.HTTPError as exc:
            return f"Refresh failed: {exc}"

        db.set_state(conn, "refresh_requested_at", _now().isoformat())
    return "Asked your banks for fresh data. The page will reload with it in a couple of minutes."


@dataclass
class SyncStatus:
    last_synced: datetime | None
    error: str | None
    reload_in_seconds: int | None  # set while waiting for a requested bank refresh
    refresh_available: bool


def status(conn) -> SyncStatus:
    last_synced = _parse(db.get_state(conn, "last_synced_at"))
    refresh_requested = _parse(db.get_state(conn, "refresh_requested_at"))

    reload_in = None
    if refresh_requested and (last_synced is None or refresh_requested > last_synced):
        remaining = refresh_requested + REFRESH_SETTLE - _now()
        reload_in = max(int(remaining.total_seconds()) + 1, 5)

    return SyncStatus(
        last_synced=last_synced.astimezone(config.TZ) if last_synced else None,
        error=db.get_state(conn, "last_error"),
        reload_in_seconds=reload_in,
        refresh_available=not refresh_requested or _now() - refresh_requested >= REFRESH_COOLDOWN,
    )


if __name__ == "__main__":
    # `python -m app.sync` forces a sync from the command line.
    db.init()
    with db.connect() as conn:
        print(f"Synced {run_sync(conn)} transactions.")
