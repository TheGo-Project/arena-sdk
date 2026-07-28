"""The builder-side SDK: three lines from zero to a filled paper trade.

    from go_arena.client import Arena

    arena = Arena.signup("https://arena.example", name="my-bot")
    result = arena.buy(market_id="516710", token_id="1234...", usd=50, max_price=0.62)
    exit = arena.sell(market_id="516710", token_id="1234...", shares=25, min_price=0.54)
    print(arena.account())
    print(arena.positions())   # open lots, marked against the live book

Builders bring their own signals and (optionally) their own data. The platform
timestamps every intent at receipt and fills it against the live book — the
track record cannot be backdated or price-improved after the fact.

The API key is shown once at signup and only its digest is stored, so it cannot
be recovered. Keep it the way you would keep any other key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from go_arena.core.intents import Intent, RiskBlock, Side

__all__ = ["Arena", "ArenaError"]


class ArenaError(RuntimeError):
    """An error the Arena explained, surfaced with that explanation intact.

    ``httpx.raise_for_status()`` reports only "Client error '422 Unprocessable
    Entity'" and leaves the reason in a response body the caller has to know to
    unpack. The Arena always says exactly what was wrong — an unusable agent name,
    a spent rate limit, a rejected key — so that message belongs in the exception.
    """

    def __init__(self, status_code: int, detail: str, response: httpx.Response) -> None:
        super().__init__(f"[{status_code}] {detail}")
        self.status_code = status_code
        self.detail = detail
        self.response = response


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    detail: object = response.text[:400]
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        # FastAPI reports validation failures as a list of per-field errors.
        if isinstance(detail, list):
            detail = "; ".join(
                f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg', '')}".strip(": ")
                for d in detail
                if isinstance(d, dict)
            )
    raise ArenaError(response.status_code, str(detail), response)


class Arena:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.account_id: str | None = None
        self._http = httpx.Client(
            timeout=timeout, headers={"X-Arena-Key": api_key}, follow_redirects=True
        )

    @classmethod
    def signup(cls, base_url: str, *, name: str, timeout: float = 30.0) -> Arena:
        response = httpx.post(
            f"{base_url.rstrip('/')}/v1/accounts", json={"name": name}, timeout=timeout
        )
        _raise_for_status(response)
        payload = response.json()
        client = cls(base_url, payload["api_key"], timeout=timeout)
        client.account_id = payload["account_id"]
        return client

    # ------------------------------------------------------------------ sugar

    def buy(
        self,
        *,
        market_id: str,
        token_id: str,
        usd: float,
        max_price: float,
        strategy_id: str = "arena-sdk",
        ttl_seconds: float = 60.0,
        rationale: str = "",
    ) -> dict:
        """Build a well-formed Intent and submit it. The idempotent id is generated
        client-side so a network retry can never double-spend."""
        now = datetime.now(UTC)
        intent = Intent(
            id=f"int_{uuid.uuid4().hex}",
            created_at=now,
            strategy_id=strategy_id,
            insight_id=f"ins_{uuid.uuid4().hex[:24]}",
            user_id="arena",
            venue="polymarket",
            canonical_event=f"gamma-{market_id}",
            instrument=token_id,
            side=Side.buy,
            size_usd=Decimal(str(round(usd, 2))),
            max_price=Decimal(str(max_price)),
            expiry=now + timedelta(seconds=ttl_seconds),
            rationale=rationale[:500],
            risk=RiskBlock(
                max_loss_usd=Decimal(str(round(usd, 2))),
                expected_value_bps=0,  # unknown from the sugar path; declare nothing
                confidence=0.5,
            ),
        )
        return self.submit(market_id=market_id, intent=intent)

    def sell(
        self,
        *,
        market_id: str,
        token_id: str,
        shares: float,
        min_price: float,
        strategy_id: str = "arena-sdk",
        ttl_seconds: float = 60.0,
        rationale: str = "",
    ) -> dict:
        """Close held shares against live bids without crossing ``min_price``."""
        now = datetime.now(UTC)
        intent = Intent(
            id=f"int_{uuid.uuid4().hex}",
            created_at=now,
            strategy_id=strategy_id,
            insight_id=f"ins_{uuid.uuid4().hex[:24]}",
            user_id="arena",
            venue="polymarket",
            canonical_event=f"gamma-{market_id}",
            instrument=token_id,
            side=Side.sell,
            size_shares=Decimal(str(round(shares, 6))),
            min_price=Decimal(str(min_price)),
            expiry=now + timedelta(seconds=ttl_seconds),
            rationale=rationale[:500],
            risk=RiskBlock(
                max_loss_usd=Decimal("0"),
                expected_value_bps=0,
                confidence=0.5,
            ),
        )
        return self.submit(market_id=market_id, intent=intent)

    # ------------------------------------------------------------------- api

    def submit(self, *, market_id: str, intent: Intent) -> dict:
        response = self._http.post(
            f"{self.base_url}/v1/intents",
            json={"market_id": market_id, "intent": intent.model_dump(mode="json")},
        )
        _raise_for_status(response)
        return response.json()

    def account(self) -> dict:
        response = self._http.get(f"{self.base_url}/v1/account")
        _raise_for_status(response)
        return response.json()

    def positions(self) -> list[dict]:
        """Open lots with their current mark. ``value_usd`` is what the position would
        fetch on the live book right now, so a bet moving against you is visible
        before it settles."""
        response = self._http.get(f"{self.base_url}/v1/positions")
        _raise_for_status(response)
        return response.json()["positions"]

    def settle(self) -> dict:
        response = self._http.post(f"{self.base_url}/v1/settle")
        _raise_for_status(response)
        return response.json()

    def leaderboard(self) -> list[dict]:
        response = self._http.get(f"{self.base_url}/v1/leaderboard")
        _raise_for_status(response)
        return response.json()["leaderboard"]
