"""Token prices, for the cost estimates in eval reports.

USD per million tokens (input, output), from Anthropic's published list prices, checked on
2026-09-29 at https://platform.claude.com/docs/en/about-claude/pricing. Prices change: update
this table, or pass the eval CLI's --price-in / --price-out flags. A model that isn't listed
gets no estimate rather than a guess.
"""

PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
}


def price_for(model: str) -> tuple[float, float] | None:
    for prefix, price in PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return price
    return None


def cost_usd(input_tokens: int, output_tokens: int, price: tuple[float, float]) -> float:
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
