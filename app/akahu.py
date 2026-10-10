"""Thin client for the Akahu API (personal app)."""

import random
import time
from collections.abc import Iterator
from datetime import datetime

import httpx

from . import config

BASE_URL = "https://api.akahu.io/v1"


class AkahuError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AkahuClient:
    def __init__(
        self,
        app_token: str = config.AKAHU_APP_TOKEN,
        user_token: str = config.AKAHU_USER_TOKEN,
    ):
        if not app_token or not user_token:
            raise AkahuError(
                "Akahu tokens are missing. Copy .env.example to .env and add your tokens."
            )
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {user_token}",
                "X-Akahu-Id": app_token,
                "Accept": "application/json",
            },
            timeout=30,
        )

    def _request(self, method: str, path: str, *, retry_429: bool = True, **kwargs) -> dict:
        for attempt in range(5):
            resp = self._http.request(method, path, **kwargs)
            if resp.status_code == 429 and retry_429 and attempt < 4:
                # Akahu asks for exponential backoff with jitter.
                time.sleep(2**attempt + random.random())
                continue
            break

        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or not body.get("success", False):
            message = body.get("message") or resp.text or "no response body"
            raise AkahuError(f"{method} {path} failed ({resp.status_code}): {message}", resp.status_code)
        return body

    def accounts(self) -> list[dict]:
        return self._request("GET", "/accounts")["items"]

    def transactions(self, start: datetime | None = None) -> Iterator[dict]:
        """Yield settled transactions, following Akahu's cursor pagination.

        `start` is an exclusive lower bound; None fetches everything Akahu holds.
        """
        params: dict[str, str] = {}
        if start is not None:
            params["start"] = start.isoformat(timespec="milliseconds")
        while True:
            page = self._request("GET", "/transactions", params=params)
            yield from page["items"]
            cursor = (page.get("cursor") or {}).get("next")
            if not cursor:
                return
            params["cursor"] = cursor

    def pending_transactions(self) -> list[dict]:
        return self._request("GET", "/transactions/pending")["items"]

    def refresh(self) -> None:
        """Ask Akahu to fetch fresh data from the banks. Rate limited, so never retried."""
        self._request("POST", "/refresh", retry_429=False)

    def close(self) -> None:
        self._http.close()
