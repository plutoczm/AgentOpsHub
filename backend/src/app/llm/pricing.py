"""Deterministic token-based cost estimates with explicit unknown semantics."""

from decimal import Decimal

from app.llm.config import ModelPricing
from app.llm.models import CostEstimate, Usage


def estimate_cost(usage: Usage, pricing: ModelPricing | None) -> CostEstimate | None:
    """Price a successful response only when both counts and rates are known."""
    if pricing is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    denominator = Decimal(1_000_000)
    input_cost = Decimal(usage.input_tokens) / denominator * pricing.input_per_million
    output_cost = Decimal(usage.output_tokens) / denominator * pricing.output_per_million
    return CostEstimate(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
        currency=pricing.currency,
    )
