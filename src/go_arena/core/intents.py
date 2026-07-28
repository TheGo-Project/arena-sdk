"""The typed intent: a proposal from a strategy, and the trust boundary.

Deterministic code (schema check -> policy engine -> executor) disposes of these.
No model touches anything after an Intent is emitted.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["Intent", "RiskBlock", "Side"]


class Side(StrEnum):
    buy = "buy"
    sell = "sell"


class RiskBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_loss_usd: Decimal = Field(ge=0)
    expected_value_bps: int
    confidence: float = Field(ge=0.0, le=1.0)


# Every free-text field is bounded. These are a public submission surface: the intent id
# in particular is retained per account and rewritten with the whole ledger on each
# request, so an unbounded id turns one cheap POST into an expensive repeated write.
MAX_ID_LEN = 128
MAX_RATIONALE_LEN = 2_000
MAX_VENUE_PARAMS_KEYS = 32


class Intent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # CLIENT-generated, idempotent, retry-safe
    id: str = Field(min_length=1, max_length=MAX_ID_LEN)
    created_at: datetime
    strategy_id: str = Field(min_length=1, max_length=MAX_ID_LEN)
    # attribution, non-negotiable
    insight_id: str = Field(min_length=1, max_length=MAX_ID_LEN)
    user_id: str = Field(min_length=1, max_length=MAX_ID_LEN)

    venue: str = Field(pattern=r"^(polymarket|kalshi)$")
    # OUR id, not the venue's
    canonical_event: str = Field(min_length=1, max_length=MAX_ID_LEN)
    # canonical instrument id
    instrument: str = Field(min_length=1, max_length=MAX_ID_LEN)
    side: Side
    # Buys specify paper-dollar notional; sells specify shares to close.
    size_usd: Decimal | None = Field(default=None, gt=0)
    size_shares: Decimal | None = Field(default=None, gt=0)

    max_price: Decimal | None = Field(default=None, gt=0, lt=1)
    min_price: Decimal | None = Field(default=None, gt=0, lt=1)
    max_slippage_bps: int = Field(default=0, ge=0, le=10_000)
    expiry: datetime
    # adapter-specific idempotency field
    venue_order_key: str | None = Field(default=None, max_length=MAX_ID_LEN)

    risk: RiskBlock
    rationale: str = Field(default="", max_length=MAX_RATIONALE_LEN)  # LOGGED, never parsed
    venue_params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _venue_params_is_bounded(self) -> Intent:
        """A dict field is an open door; cap the key count. Total request size is the
        reverse proxy's job — this only stops a well-formed intent being huge."""
        if len(self.venue_params) > MAX_VENUE_PARAMS_KEYS:
            raise ValueError(f"venue_params accepts at most {MAX_VENUE_PARAMS_KEYS} keys")
        return self

    @model_validator(mode="after")
    def _side_uses_one_size_and_limit(self) -> Intent:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.expiry.tzinfo is None or self.expiry.utcoffset() is None:
            raise ValueError("expiry must be timezone-aware")
        if self.expiry <= self.created_at:
            raise ValueError("expiry must be after created_at")
        if self.side is Side.buy:
            if self.size_usd is None or self.max_price is None:
                raise ValueError("buy intent requires size_usd and max_price")
            if self.size_shares is not None or self.min_price is not None:
                raise ValueError("buy intent cannot include sell fields")
        else:
            if self.size_shares is None or self.min_price is None:
                raise ValueError("sell intent requires size_shares and min_price")
            if self.size_usd is not None or self.max_price is not None:
                raise ValueError("sell intent cannot include buy fields")
        return self
