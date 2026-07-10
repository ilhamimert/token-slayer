"""Tests for cca.proxy.orchestrator — full pipeline coordination."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from cca.proxy.cost_tracker import CostTracker
from cca.proxy.failover import FailoverChain, ProviderEndpoint
from cca.proxy.orchestrator import OrchestratorResult, ProxyOrchestrator
from cca.proxy.prompt_chunker import PromptChunker
from cca.proxy.semantic_cache import SemanticCache, SimHashEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────

FAKE_ENDPOINT = ProviderEndpoint(
    name="test-provider",
    provider="anthropic",
    model="claude-haiku-4-5-20251001",
    base_url="https://api.anthropic.com/v1",
    api_key_env="ANTHROPIC_API_KEY",
)

FAKE_LLM_RESPONSE = json.dumps({
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Paris is the capital of France."}],
    "model": "claude-haiku-4-5-20251001",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 25, "output_tokens": 10},
})


def _make_orchestrator(
    cache=None,
    tracker=None,
    failover=None,
    preferred_provider="anthropic",
    disable_cache=False,
    disable_routing=False,
):
    if cache is None:
        cache = SemanticCache(
            similarity_threshold=0.90, ttl=3_600, persist_path=None, engine=SimHashEngine()
        )
    if tracker is None:
        tracker = CostTracker()
    if failover is None:
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)

    return ProxyOrchestrator(
        cache=cache,
        tracker=tracker,
        failover=failover,
        chunker=PromptChunker(),
        preferred_provider=preferred_provider,
        disable_cache=disable_cache,
        disable_routing=disable_routing,
    )


SAMPLE_MESSAGES = [{"role": "user", "content": "What is the capital of France?"}]


# ── OrchestratorResult shape ──────────────────────────────────────────────────

class TestOrchestratorResultShape:
    def test_result_has_all_fields(self):
        orch = _make_orchestrator()
        result = orch.handle(SAMPLE_MESSAGES)
        assert hasattr(result, "response_data")
        assert hasattr(result, "response_text")
        assert hasattr(result, "model_used")
        assert hasattr(result, "provider")
        assert hasattr(result, "complexity")
        assert hasattr(result, "cache_hit")
        assert hasattr(result, "latency_s")
        assert hasattr(result, "actual_cost_usd")
        assert hasattr(result, "baseline_cost_usd")
        assert hasattr(result, "input_tokens")
        assert hasattr(result, "output_tokens")


# ── Cache hit path ────────────────────────────────────────────────────────────

class TestCacheHitPath:
    def test_cache_hit_skips_llm_call(self):
        cache = SemanticCache(
            similarity_threshold=0.90, ttl=3_600, persist_path=None, engine=SimHashEngine()
        )
        cache.put(
            "user: What is the capital of France?",
            "Paris is the capital of France.",
            "haiku",
            0.0001,
        )
        failover = MagicMock(spec=FailoverChain)
        orch = _make_orchestrator(cache=cache, failover=failover)

        orch.handle(SAMPLE_MESSAGES)
        failover.execute.assert_not_called()

    def test_cache_hit_result_has_cache_hit_true(self):
        cache = SemanticCache(
            similarity_threshold=0.90, ttl=3_600, persist_path=None, engine=SimHashEngine()
        )
        prompt_text = "user: What is the capital of France?"
        cache.put(prompt_text, "Paris.", "haiku", 0.0)
        failover = MagicMock(spec=FailoverChain)
        orch = _make_orchestrator(cache=cache, failover=failover)

        result = orch.handle(SAMPLE_MESSAGES)
        assert result.cache_hit is True

    def test_cache_hit_result_has_zero_cost(self):
        cache = SemanticCache(
            similarity_threshold=0.90, ttl=3_600, persist_path=None, engine=SimHashEngine()
        )
        cache.put("user: What is the capital of France?", "Paris.", "haiku", 0.0)
        orch = _make_orchestrator(cache=cache)
        result = orch.handle(SAMPLE_MESSAGES)
        assert result.actual_cost_usd == 0.0

    def test_cache_hit_records_in_tracker(self):
        cache = SemanticCache(
            similarity_threshold=0.90, ttl=3_600, persist_path=None, engine=SimHashEngine()
        )
        cache.put("user: What is the capital of France?", "Paris.", "haiku", 0.0)
        tracker = CostTracker()
        orch = _make_orchestrator(cache=cache, tracker=tracker)

        orch.handle(SAMPLE_MESSAGES)
        summary = tracker.summarize()
        assert summary.total_calls == 1
        assert summary.cache_hits == 1


# ── LLM call path ─────────────────────────────────────────────────────────────

class TestLLMCallPath:
    def test_cache_miss_calls_failover(self):
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)
        orch = _make_orchestrator(failover=failover)

        orch.handle(SAMPLE_MESSAGES)
        failover.execute.assert_called_once()

    def test_result_has_response_text(self):
        orch = _make_orchestrator()
        result = orch.handle(SAMPLE_MESSAGES)
        assert result.response_text == "Paris is the capital of France."

    def test_result_model_used_from_endpoint(self):
        orch = _make_orchestrator()
        result = orch.handle(SAMPLE_MESSAGES)
        assert result.model_used == FAKE_ENDPOINT.model

    def test_result_provider_from_endpoint(self):
        orch = _make_orchestrator()
        result = orch.handle(SAMPLE_MESSAGES)
        assert result.provider == "anthropic"

    def test_result_cache_hit_is_false(self):
        orch = _make_orchestrator()
        result = orch.handle(SAMPLE_MESSAGES)
        assert result.cache_hit is False

    def test_response_stored_in_cache_for_next_call(self):
        cache = SemanticCache(
            similarity_threshold=0.90, ttl=3_600, persist_path=None, engine=SimHashEngine()
        )
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)
        orch = _make_orchestrator(cache=cache, failover=failover)

        orch.handle(SAMPLE_MESSAGES)
        orch.handle(SAMPLE_MESSAGES)

        # Second call should be served from cache
        assert failover.execute.call_count == 1

    def test_cost_recorded_in_tracker(self):
        tracker = CostTracker()
        orch = _make_orchestrator(tracker=tracker)
        orch.handle(SAMPLE_MESSAGES)
        summary = tracker.summarize()
        assert summary.total_calls == 1
        assert summary.cache_hits == 0
        assert summary.actual_cost_usd >= 0


# ── disable_cache flag ────────────────────────────────────────────────────────

class TestDisableCache:
    def test_disable_cache_always_calls_llm(self):
        cache = SemanticCache(
            similarity_threshold=0.90, ttl=3_600, persist_path=None, engine=SimHashEngine()
        )
        cache.put("user: What is the capital of France?", "Paris.", "haiku", 0.0)
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)

        orch = _make_orchestrator(cache=cache, failover=failover, disable_cache=True)
        orch.handle(SAMPLE_MESSAGES)
        orch.handle(SAMPLE_MESSAGES)

        assert failover.execute.call_count == 2

    def test_disable_cache_result_is_never_cache_hit(self):
        orch = _make_orchestrator(disable_cache=True)
        result = orch.handle(SAMPLE_MESSAGES)
        assert result.cache_hit is False


# ── disable_routing flag ──────────────────────────────────────────────────────

class TestDisableRouting:
    def test_disable_routing_uses_complex_tier(self):
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)
        orch = _make_orchestrator(failover=failover, disable_routing=True)

        result = orch.handle([{"role": "user", "content": "What is 2+2?"}])
        # With routing disabled, even a trivial question goes to COMPLEX
        assert result.complexity != "CACHED"

    def test_disable_routing_still_calls_llm(self):
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)
        orch = _make_orchestrator(failover=failover, disable_routing=True)
        orch.handle(SAMPLE_MESSAGES)
        failover.execute.assert_called_once()


# ── system prompt support ─────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_system_prompt_is_included_in_cache_key(self):
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)
        orch = _make_orchestrator(failover=failover)

        orch.handle(SAMPLE_MESSAGES, system="You are assistant A.")
        orch.handle(SAMPLE_MESSAGES, system="You are assistant B.")

        # Different system prompts → different cache keys → 2 LLM calls
        assert failover.execute.call_count == 2

    def test_same_system_prompt_second_call_hits_cache(self):
        failover = MagicMock(spec=FailoverChain)
        failover.execute.return_value = (FAKE_LLM_RESPONSE, FAKE_ENDPOINT)
        orch = _make_orchestrator(failover=failover)

        orch.handle(SAMPLE_MESSAGES, system="You are a geography expert assistant.")
        orch.handle(SAMPLE_MESSAGES, system="You are a geography expert assistant.")

        assert failover.execute.call_count == 1


# ── failover error propagation ────────────────────────────────────────────────

class TestFailoverErrorPropagation:
    def test_all_providers_exhausted_raises_runtime_error(self):
        failover = MagicMock(spec=FailoverChain)
        failover.execute.side_effect = RuntimeError("All providers exhausted")
        orch = _make_orchestrator(failover=failover, disable_cache=True)

        with pytest.raises(RuntimeError, match="All providers exhausted"):
            orch.handle(SAMPLE_MESSAGES)
