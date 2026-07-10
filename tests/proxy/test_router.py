"""Tests for cca.proxy.router — complexity classification and model routing."""
from __future__ import annotations

import pytest

from cca.proxy.router import (
    BASELINE_MODEL,
    Complexity,
    ModelConfig,
    classify_complexity,
    estimate_savings,
    get_model_for_complexity,
)


# ── ModelConfig ───────────────────────────────────────────────────────────────

class TestModelConfigEstimateCost:
    def test_zero_tokens_returns_zero(self):
        model = ModelConfig("openai", "gpt-4o", 2.50e-6, 10.00e-6, 128_000)
        assert model.estimate_cost(0, 0) == 0.0

    def test_input_only_cost(self):
        model = ModelConfig("openai", "gpt-4o", 2.50e-6, 10.00e-6, 128_000)
        cost = model.estimate_cost(1_000, 0)
        assert abs(cost - 0.0025) < 1e-9

    def test_combined_cost(self):
        model = ModelConfig("openai", "gpt-4o", 2.50e-6, 10.00e-6, 128_000)
        cost = model.estimate_cost(1_000, 500)
        expected = 1_000 * 2.50e-6 + 500 * 10.00e-6
        assert abs(cost - expected) < 1e-9

    def test_haiku_is_cheaper_than_gpt4o(self):
        haiku = ModelConfig("anthropic", "claude-haiku-4-5-20251001", 0.25e-6, 1.25e-6, 200_000)
        gpt4o = ModelConfig("openai", "gpt-4o", 2.50e-6, 10.00e-6, 128_000)
        assert haiku.estimate_cost(1_000, 500) < gpt4o.estimate_cost(1_000, 500)


# ── classify_complexity ───────────────────────────────────────────────────────

class TestClassifyComplexity:
    def test_empty_prompt_returns_simple(self):
        assert classify_complexity("") == Complexity.SIMPLE

    def test_short_factual_question_is_simple(self):
        assert classify_complexity("What is Python?", token_count=4) == Complexity.SIMPLE

    def test_list_question_is_simple(self):
        assert classify_complexity("List the planets in our solar system", token_count=7) == Complexity.SIMPLE

    def test_very_long_prompt_is_complex(self):
        long_prompt = "word " * 2_100
        assert classify_complexity(long_prompt, token_count=2_100) == Complexity.COMPLEX

    def test_complex_keyword_analyze_is_complex(self):
        prompt = "Analyze the trade-offs between microservices and monolith architecture in depth"
        assert classify_complexity(prompt) == Complexity.COMPLEX

    def test_multiple_complex_keywords_is_complex(self):
        prompt = "Analyze and compare the performance implications, then evaluate the design"
        assert classify_complexity(prompt) == Complexity.COMPLEX

    def test_code_block_is_medium(self):
        prompt = "Fix this:\n```python\ndef foo(): pass\n```"
        assert classify_complexity(prompt) == Complexity.MEDIUM

    def test_multiple_code_patterns_is_medium(self):
        # Two code patterns (def + class) push it to MEDIUM
        prompt = "def calculate_total(items) and class Cart: how do they work?"
        assert classify_complexity(prompt) == Complexity.MEDIUM

    def test_medium_length_prose_is_medium(self):
        prompt = "word " * 250
        assert classify_complexity(prompt, token_count=250) == Complexity.MEDIUM

    def test_single_complex_keyword_short_is_medium(self):
        prompt = "Analyze this code"
        assert classify_complexity(prompt) == Complexity.MEDIUM


# ── get_model_for_complexity ──────────────────────────────────────────────────

class TestGetModelForComplexity:
    def test_simple_complexity_returns_a_model(self):
        model = get_model_for_complexity(Complexity.SIMPLE)
        assert isinstance(model, ModelConfig)

    def test_preferred_anthropic_returns_anthropic_model(self):
        model = get_model_for_complexity(Complexity.SIMPLE, preferred_provider="anthropic")
        assert model.provider == "anthropic"

    def test_preferred_openai_returns_openai_model(self):
        model = get_model_for_complexity(Complexity.MEDIUM, preferred_provider="openai")
        assert model.provider == "openai"

    def test_unknown_provider_falls_back_to_first_option(self):
        model = get_model_for_complexity(Complexity.SIMPLE, preferred_provider="azure")
        assert isinstance(model, ModelConfig)

    def test_complex_model_is_more_expensive_than_simple(self):
        simple_model = get_model_for_complexity(Complexity.SIMPLE, "anthropic")
        complex_model = get_model_for_complexity(Complexity.COMPLEX, "openai")
        assert simple_model.input_price < complex_model.input_price

    def test_baseline_model_is_complex_tier(self):
        complex_model = get_model_for_complexity(Complexity.COMPLEX, "openai")
        assert BASELINE_MODEL.model == complex_model.model


# ── estimate_savings ──────────────────────────────────────────────────────────

class TestEstimateSavings:
    def test_simple_prompt_has_positive_savings(self):
        result = estimate_savings("What is Python?", input_tokens=50, output_tokens=100)
        assert result["savings_usd"] > 0
        assert result["savings_pct"] > 0

    def test_result_has_required_keys(self):
        result = estimate_savings("Analyze this", input_tokens=200, output_tokens=100)
        assert {"complexity", "chosen_model", "chosen_provider",
                "baseline_cost_usd", "routed_cost_usd", "savings_usd", "savings_pct"} <= result.keys()

    def test_complex_prompt_routes_to_complex_tier(self):
        prompt = "Analyze and compare the architectural trade-offs of distributed systems in depth"
        result = estimate_savings(prompt, input_tokens=500, output_tokens=200)
        assert result["complexity"] == "COMPLEX"

    def test_savings_pct_is_zero_when_no_baseline_cost(self):
        result = estimate_savings("", input_tokens=0, output_tokens=0)
        assert result["savings_pct"] == 0.0

    def test_routed_cost_never_exceeds_baseline(self):
        for prompt in ["What is 2+2?", "Write a function", "Analyze architecture"]:
            result = estimate_savings(prompt, input_tokens=100, output_tokens=50)
            assert result["routed_cost_usd"] <= result["baseline_cost_usd"]
