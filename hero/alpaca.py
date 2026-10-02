"""Minimal Alpaca REST client (paper trading only).

Credentials come from ALPACA_API_KEY / ALPACA_SECRET_KEY (or the official
APCA_API_KEY_ID / APCA_API_SECRET_KEY names). If neither is set, requests go
out without auth headers so an egress proxy that injects them still works.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import requests

PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


class Alpaca:
    def __init__(self, base_url: str = PAPER_URL, data_url: str = DATA_URL,
                 session: requests.Session | None = None):
        # Hard guard: this project must never touch a live account.
        if "paper" not in base_url:
            raise AlpacaError(f"refusing non-paper endpoint: {base_url}")
        self.base_url = base_url.rstrip("/")
        self.data_url = data_url.rstrip("/")
        self.s = session or requests.Session()
        key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        if key and secret:
            self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _req(self, method: str, url: str, retry: bool = True, **kw) -> Any:
        # Only idempotent requests are retried. Order submission is made idempotent with a
        # client_order_id (see submit_order); position closes are never retried.
        for attempt in range(4 if retry else 1):
            r = self.s.request(method, url, timeout=30, **kw)
            if (r.status_code == 429 or r.status_code >= 500) and retry:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise AlpacaError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else None
            break
        raise AlpacaError(f"{method} {url} -> {r.status_code} after {attempt + 1} attempt(s): {r.text[:300]}")

    def _t(self, method: str, path: str, **kw) -> Any:
        return self._req(method, self.base_url + path, **kw)

    def _d(self, path: str, params: dict) -> Any:
        return self._req("GET", self.data_url + path, params=params)

    # --- trading ---
    def clock(self) -> dict:
        return self._t("GET", "/v2/clock")

    def account(self) -> dict:
        return self._t("GET", "/v2/account")

    def positions(self) -> list[dict]:
        return self._t("GET", "/v2/positions")

    def open_orders(self) -> list[dict]:
        return self._t("GET", "/v2/orders", params={"status": "open", "limit": 500})

    def submit_order(self, **order) -> dict:
        """Submit with a unique client_order_id so a retry after a lost response can never
        create a second order: the broker rejects the duplicate id and we fetch the original."""
        order.setdefault("client_order_id", uuid.uuid4().hex)
        try:
            return self._t("POST", "/v2/orders", json=order)
        except AlpacaError as e:
            if "client_order_id" not in str(e):
                raise
            return self.order_by_client_id(order["client_order_id"])

    def order_by_client_id(self, client_order_id: str) -> dict:
        return self._t("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})

    def cancel_order(self, order_id: str, wait_s: float = 5.0) -> str:
        """Cancel and wait until the broker reports a final state, so the shares the order held
        are released before we sell them. Returns that state (e.g. "canceled", or "filled" if
        the stop triggered first)."""
        self._t("DELETE", f"/v2/orders/{order_id}", retry=False)
        status, deadline = "pending_cancel", time.monotonic() + wait_s
        while time.monotonic() < deadline:
            status = self._t("GET", f"/v2/orders/{order_id}").get("status", status)
            if status in ("canceled", "filled", "expired", "rejected", "replaced"):
                break
            time.sleep(0.5)
        return status

    def close_position(self, symbol: str) -> dict:
        return self._t("DELETE", f"/v2/positions/{symbol}", retry=False)

    def fill_activities(self, day: str) -> list[dict]:
        """All FILL activities on a trading day (paged, oldest first)."""
        params = {"date": day, "direction": "asc", "page_size": 100}
        out: list[dict] = []
        while True:
            page = self._t("GET", "/v2/account/activities/FILL", params=params) or []
            out += page
            if len(page) < 100:
                return out
            params["page_token"] = page[-1]["id"]

    def option_contracts(self, underlying: str, **params) -> list[dict]:
        params = {"underlying_symbols": underlying, "limit": 1000, **params}
        out: list[dict] = []
        while True:
            page = self._t("GET", "/v2/options/contracts", params=params)
            out += page.get("option_contracts") or []
            token = page.get("next_page_token")
            if not token:
                return out
            params["page_token"] = token

    # --- market data ---
    def daily_bars(self, symbols: list[str], start: str) -> dict[str, list[dict]]:
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start,
                  "adjustment": "all", "feed": "iex", "limit": 10000}
        out: dict[str, list[dict]] = {}
        while True:
            page = self._d("/v2/stocks/bars", params)
            for sym, bars in (page.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = page.get("next_page_token")
            if not token:
                return out
            params["page_token"] = token

    def option_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 100):
            page = self._d("/v1beta1/options/snapshots",
                           {"symbols": ",".join(symbols[i:i + 100]), "feed": "indicative"})
            out.update(page.get("snapshots") or {})
        return out
