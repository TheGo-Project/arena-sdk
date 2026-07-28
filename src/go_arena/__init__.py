"""The Go! Arena SDK — submit trade Intents, get filled against real books, get ranked.

Builders bring the brain; the platform owns the truth layer — market data, receipt
timestamps, depth-aware fills against observed liquidity, risk caps, settlement and
scoring. This package is only the client half of that contract.

    from go_arena import Arena

    arena = Arena.signup("https://arena.gosmartchain.ai", name="my-bot")
    arena.buy(market_id="665374", token_id="5511...", usd=25, max_price=0.30)
"""

from go_arena.client import Arena

__all__ = ["Arena"]
