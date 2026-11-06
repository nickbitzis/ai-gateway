"""Model price table and cost math. Prices are USD per million tokens."""

from __future__ import annotations

import math

PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = PRICES[model]
    return (input_tokens * inp + output_tokens * out) / 1_000_000


def estimate_input_tokens(text: str) -> int:
    """Rough count: about 4 characters per token, plus a small per-request overhead."""
    return math.ceil(len(text) / 4) + 8


def estimate_cost_usd(model: str, prompt_text: str, max_tokens: int) -> float:
    """Worst-case cost before the call: estimated input plus the full max_tokens of output."""
    return cost_usd(model, estimate_input_tokens(prompt_text), max_tokens)
