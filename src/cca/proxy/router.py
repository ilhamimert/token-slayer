"""Complexity-based LLM request router.

Routes each prompt to the cheapest capable model using heuristics:
  SIMPLE  → short/factual  → Tier-1 (cheap/fast: Haiku, GPT-3.5)
  MEDIUM  → structured/code → Tier-2 (balanced: Sonnet, GPT-4o-mini)
  COMPLEX → reasoning/long  → Tier-3 (powerful: Opus, GPT-4o)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum


class Complexity(IntEnum):
    SIMPLE = 1
    MEDIUM = 2
    COMPLEX = 3


@dataclass(frozen=True)
class ModelConfig:
    provider: str        # "openai" | "anthropic"
    model: str           # e.g. "claude-haiku-4-5-20251001"
    input_price: float   # USD per token
    output_price: float  # USD per token
    context_limit: int   # max context tokens

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * self.input_price + output_tokens * self.output_price


# Prices are per-token (source: public pricing pages, 2025-07)
_CATALOG: dict[Complexity, list[ModelConfig]] = {
    Complexity.SIMPLE: [
        ModelConfig("anthropic", "claude-haiku-4-5-20251001", 0.25e-6, 1.25e-6, 200_000),
        ModelConfig("openai", "gpt-3.5-turbo", 0.50e-6, 1.50e-6, 16_385),
    ],
    Complexity.MEDIUM: [
        ModelConfig("openai", "gpt-4o-mini", 0.15e-6, 0.60e-6, 128_000),
        ModelConfig("anthropic", "claude-sonnet-4-6", 3.00e-6, 15.00e-6, 200_000),
    ],
    Complexity.COMPLEX: [
        ModelConfig("openai", "gpt-4o", 2.50e-6, 10.00e-6, 128_000),
        ModelConfig("anthropic", "claude-opus-4-8", 15.00e-6, 75.00e-6, 200_000),
    ],
}

# Baseline: everything goes to GPT-4o (for savings comparison)
BASELINE_MODEL = _CATALOG[Complexity.COMPLEX][0]

_RE_COMPLEX = re.compile(
    r"\b(analyz|compar|evaluat|criticiz|design|architect|refactor|"
    r"implement|step[\s-]by[\s-]step|comprehensiv|pros?\s+and\s+cons?|"
    r"trade.?off|debug|optimiz|investigat)\w*\b",
    re.IGNORECASE,
)
_RE_CODE = re.compile(
    r"```|\bdef\s+\w+\s*\(|\bclass\s+\w+\b|\bfunction\b|\bimport\s+\w",
    re.IGNORECASE,
)
_RE_SIMPLE = re.compile(
    r"^(?:what\s+is|who\s+is|when\s+(?:is|was|did)|where\s+(?:is|was)|"
    r"define\s+|list\s+|translate\s+|convert\s+|format\s+)",
    re.IGNORECASE,
)


def classify_complexity(prompt: str, token_count: int = 0) -> Complexity:
    """Heuristic complexity classifier — no ML required."""
    if not prompt:
        return Complexity.SIMPLE

    word_count = token_count or len(prompt.split())

    if word_count > 2_000:
        return Complexity.COMPLEX

    if word_count < 30 and _RE_SIMPLE.match(prompt.strip()):
        return Complexity.SIMPLE

    complex_hits = len(_RE_COMPLEX.findall(prompt))
    code_hits = len(_RE_CODE.findall(prompt))

    if complex_hits >= 2 or (complex_hits >= 1 and word_count > 500):
        return Complexity.COMPLEX
    if code_hits >= 2 or complex_hits >= 1 or word_count > 200:
        return Complexity.MEDIUM

    return Complexity.SIMPLE


def get_model_for_complexity(
    complexity: Complexity,
    preferred_provider: str | None = None,
) -> ModelConfig:
    """Return the cheapest model that handles the given complexity tier."""
    options = _CATALOG[complexity]
    if preferred_provider:
        for m in options:
            if m.provider == preferred_provider:
                return m
    return options[0]


def estimate_savings(
    prompt: str,
    input_tokens: int,
    output_tokens: int,
    preferred_provider: str | None = None,
) -> dict:
    """Compare routed cost vs. sending everything to GPT-4o."""
    complexity = classify_complexity(prompt, token_count=input_tokens)
    chosen = get_model_for_complexity(complexity, preferred_provider)
    baseline_cost = BASELINE_MODEL.estimate_cost(input_tokens, output_tokens)
    routed_cost = chosen.estimate_cost(input_tokens, output_tokens)
    savings = baseline_cost - routed_cost
    return {
        "complexity": complexity.name,
        "chosen_model": chosen.model,
        "chosen_provider": chosen.provider,
        "baseline_cost_usd": round(baseline_cost, 8),
        "routed_cost_usd": round(routed_cost, 8),
        "savings_usd": round(savings, 8),
        "savings_pct": round(savings / baseline_cost * 100, 1) if baseline_cost else 0.0,
    }
