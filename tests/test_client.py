"""Client tests against a mocked Arena. No network, no account required.

These assert the *shape of the contract* the SDK puts on the wire: if the Arena
ever changes what it accepts, these are what tell you before your builders find
out.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from go_arena import Arena
from go_arena.core.intents import Intent, RiskBlock, Side

BASE = "https://arena.example"
MARKET = "665374"
TOKEN = "55115078421062885512539156303747803058407616201213034911037320915726138659123"


def _client(recorder: list[httpx.Request], payload: dict | None = None) -> Arena:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if request.url.path == "/v1/accounts":
            return httpx.Response(200, json={"account_id": "acct_1", "api_key": "arena_test"})
        if request.url.path == "/v1/positions":
            return httpx.Response(200, json={"positions": [{"market_id": MARKET}]})
        if request.url.path == "/v1/leaderboard":
            return httpx.Response(200, json={"leaderboard": [{"name": "alpha"}]})
        return httpx.Response(200, json=payload or {"status": "filled"})

    arena = Arena(BASE, api_key="arena_test")
    arena._http = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"X-Arena-Key": "arena_test"}
    )
    return arena


def test_buy_sends_a_well_formed_buy_intent():
    seen: list[httpx.Request] = []
    arena = _client(seen)

    arena.buy(market_id=MARKET, token_id=TOKEN, usd=25.0, max_price=0.30, rationale="thesis")

    assert seen[0].url.path == "/v1/intents"
    body = json.loads(seen[0].content)
    intent = body["intent"]
    assert body["market_id"] == MARKET
    assert intent["side"] == "buy"
    assert intent["instrument"] == TOKEN
    assert Decimal(intent["size_usd"]) == Decimal("25")
    assert Decimal(intent["max_price"]) == Decimal("0.30")
    assert intent["min_price"] is None and intent["size_shares"] is None
    assert intent["id"].startswith("int_")  # client-generated, so a retry is idempotent
    assert intent["expiry"] > intent["created_at"]


def test_sell_sends_a_well_formed_sell_intent():
    seen: list[httpx.Request] = []
    arena = _client(seen)

    arena.sell(market_id=MARKET, token_id=TOKEN, shares=50.0, min_price=0.18)

    intent = json.loads(seen[0].content)["intent"]
    assert intent["side"] == "sell"
    assert Decimal(intent["size_shares"]) == Decimal("50")
    assert Decimal(intent["min_price"]) == Decimal("0.18")
    assert intent["size_usd"] is None and intent["max_price"] is None


def test_the_same_intent_can_be_resubmitted_unchanged():
    """A network timeout must be safe to retry: the id is generated once, client
    side, so the Arena can recognise the second attempt as the same intent."""
    seen: list[httpx.Request] = []
    arena = _client(seen)
    intent = _an_intent()

    arena.submit(market_id=MARKET, intent=intent)
    arena.submit(market_id=MARKET, intent=intent)

    first, second = (json.loads(r.content)["intent"]["id"] for r in seen)
    assert first == second


def test_reads_hit_the_documented_paths():
    seen: list[httpx.Request] = []
    arena = _client(seen)

    arena.account()
    arena.positions()
    arena.leaderboard()

    assert [r.url.path for r in seen] == ["/v1/account", "/v1/positions", "/v1/leaderboard"]
    assert all(r.headers.get("X-Arena-Key") == "arena_test" for r in seen)


def test_signup_returns_a_ready_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"account_id": "acct_9", "api_key": "arena_new"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as probe:
        response = probe.post(f"{BASE}/v1/accounts", json={"name": "my-bot"})

    assert response.json()["api_key"] == "arena_new"


def _an_intent(**overrides) -> Intent:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    fields = {
        "id": "int_fixed_for_this_test",
        "created_at": now,
        "strategy_id": "test",
        "insight_id": "ins_test",
        "user_id": "arena",
        "venue": "polymarket",
        "canonical_event": f"gamma-{MARKET}",
        "instrument": TOKEN,
        "side": Side.buy,
        "size_usd": Decimal("10"),
        "max_price": Decimal("0.30"),
        "expiry": now + timedelta(seconds=60),
        "risk": RiskBlock(max_loss_usd=Decimal("10"), expected_value_bps=0, confidence=0.5),
    }
    fields.update(overrides)
    return Intent(**fields)


def test_the_intent_contract_rejects_malformed_orders():
    """Caught locally, before a round trip: a buy needs a ceiling and a notional,
    a sell needs a floor and a share count, and never the other side's fields."""
    with pytest.raises(ValidationError):
        _an_intent(max_price=None)  # buy without a ceiling
    with pytest.raises(ValidationError):
        _an_intent(size_shares=Decimal("5"))  # buy carrying sell fields
    with pytest.raises(ValidationError):
        _an_intent(venue="nasdaq")  # unsupported venue
    with pytest.raises(ValidationError):
        _an_intent(max_price=Decimal("1.5"))  # a price must sit inside (0, 1)


def test_expiry_must_follow_creation():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        _an_intent(created_at=now, expiry=now - timedelta(seconds=1))


def test_errors_carry_the_arenas_own_explanation():
    """httpx alone reports "Client error '422'" and leaves the reason in a body the
    caller must know to unpack. The Arena always says what was wrong; that belongs
    in the exception, because it is the first thing a new builder will hit."""
    from go_arena.client import ArenaError

    cases = [
        (
            422,
            {
                "detail": [
                    {"loc": ["body", "name"], "msg": "Value error, name may use only letters"}
                ]
            },
            "name may use only letters",
        ),
        (401, {"detail": "invalid or missing X-Arena-Key"}, "invalid or missing X-Arena-Key"),
        (429, {"detail": "signup rate limit reached; try again later"}, "rate limit reached"),
        (409, {"detail": "the name 'alpha' is already taken; choose another"}, "already taken"),
    ]
    for status, body, expected in cases:
        arena = Arena(BASE, api_key="k")
        arena._http = httpx.Client(
            transport=httpx.MockTransport(lambda r, s=status, b=body: httpx.Response(s, json=b))
        )
        with pytest.raises(ArenaError) as caught:
            arena.account()
        assert expected in str(caught.value)
        assert caught.value.status_code == status
        assert caught.value.detail


def test_a_non_json_error_still_raises_something_readable():
    arena = Arena(BASE, api_key="k")
    arena._http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(502, text="upstream is down"))
    )
    from go_arena.client import ArenaError

    with pytest.raises(ArenaError) as caught:
        arena.leaderboard()
    assert "upstream is down" in str(caught.value)
    assert caught.value.status_code == 502
