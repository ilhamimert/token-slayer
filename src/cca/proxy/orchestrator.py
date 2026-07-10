"""Layer 4 — Orchestration.

ProxyOrchestrator coordinates the full request pipeline:
  cache check → complexity routing → LLM call → cost tracking → cache store

server.py (Layer 5) is a pure HTTP adapter that calls handle() and returns JSON.
Neither layer knows the other's internals.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from cca.proxy.cost_tracker import CostTracker
from cca.proxy.failover import DEFAULT_CHAIN, FailoverChain, ProviderEndpoint
from cca.proxy.prompt_chunker import PromptChunker
from cca.proxy.router import (
    BASELINE_MODEL,
    Complexity,
    classify_complexity,
    get_model_for_complexity,
)

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResult:
    """Everything server.py needs to build its HTTP response."""

    response_data: dict          # Raw parsed JSON from the LLM provider
    response_text: str           # Concatenated text blocks
    model_used: str
    provider: str
    complexity: str
    cache_hit: bool
    latency_s: float
    actual_cost_usd: float
    baseline_cost_usd: float
    input_tokens: int
    output_tokens: int


class ProxyOrchestrator:
    """Coordinates cache, router, LLM call, and cost tracking.

    Parameters
    ----------
    cache:
        SemanticCache or TwoLayerCache. Must implement .get() and .put().
    tracker:
        CostTracker instance for recording every call.
    failover:
        FailoverChain instance wrapping provider endpoints.
    chunker:
        PromptChunker for token estimation and static/dynamic split.
    preferred_provider:
        'anthropic' | 'openai' | None (auto).
    disable_cache:
        Skip all cache read/write.
    disable_routing:
        Always use the COMPLEX tier (most capable model).
    """

    def __init__(
        self,
        cache: Any,
        tracker: CostTracker,
        failover: FailoverChain,
        chunker: PromptChunker,
        preferred_provider: str | None = None,
        disable_cache: bool = False,
        disable_routing: bool = False,
    ) -> None:
        self._cache = cache
        self._tracker = tracker
        self._failover = failover
        self._chunker = chunker
        self._preferred_provider = preferred_provider or "anthropic"
        self._disable_cache = disable_cache
        self._disable_routing = disable_routing

    # ── Public entry point ────────────────────────────────────────────────────

    def handle(
        self,
        messages: list[dict],
        system: str = "",
        model_override: str | None = None,
    ) -> OrchestratorResult:
        """Process one request end-to-end and return a structured result."""

        full_prompt = self._build_prompt_text(messages, system)
        chunk = self._chunker.split_messages(
            ([{"role": "system", "content": system}] if system else []) + messages
        )
        input_tokens_est = chunk.total_tokens

        # ── 1. Cache check ────────────────────────────────────────────────────
        if not self._disable_cache:
            hit = self._cache.get(full_prompt)
            if hit is not None:
                response_text = getattr(hit, "response", None) or hit.get("response", "")
                model_used = getattr(hit, "model_used", None) or hit.get("model_used", "cached")
                baseline_cost = BASELINE_MODEL.estimate_cost(input_tokens_est, 200)

                self._tracker.record(
                    prompt_tokens=input_tokens_est,
                    completion_tokens=len(response_text.split()),
                    model_used=model_used,
                    provider="cache",
                    actual_cost_usd=0.0,
                    baseline_cost_usd=baseline_cost,
                    cache_hit=True,
                    complexity="CACHED",
                )
                return OrchestratorResult(
                    response_data={
                        "id": "msg_cached",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "text", "text": response_text}],
                        "model": model_used,
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": input_tokens_est, "output_tokens": 0},
                    },
                    response_text=response_text,
                    model_used=model_used,
                    provider="cache",
                    complexity="CACHED",
                    cache_hit=True,
                    latency_s=0.0,
                    actual_cost_usd=0.0,
                    baseline_cost_usd=baseline_cost,
                    input_tokens=input_tokens_est,
                    output_tokens=0,
                )

        # ── 2. Complexity routing ─────────────────────────────────────────────
        complexity = classify_complexity(full_prompt, token_count=input_tokens_est)
        if self._disable_routing or model_override:
            chosen = get_model_for_complexity(Complexity.COMPLEX, self._preferred_provider)
        else:
            chosen = get_model_for_complexity(complexity, self._preferred_provider)

        # ── 3. LLM call with failover ─────────────────────────────────────────
        forward_body = {
            "model": model_override or chosen.model,
            "messages": messages,
        }
        if system:
            forward_body["system"] = system

        call_fn = self._make_call_fn(forward_body)
        start = time.monotonic()
        raw_response, used_endpoint = self._failover.execute(call_fn)
        latency_s = time.monotonic() - start

        # ── 4. Parse response ─────────────────────────────────────────────────
        response_data = json.loads(raw_response)
        response_text = "".join(
            block.get("text", "")
            for block in response_data.get("content", [])
            if block.get("type") == "text"
        )

        usage = response_data.get("usage", {})
        in_tok = usage.get("input_tokens", input_tokens_est)
        out_tok = usage.get("output_tokens", 100)
        actual_cost = chosen.estimate_cost(in_tok, out_tok)
        baseline_cost = BASELINE_MODEL.estimate_cost(in_tok, out_tok)

        # ── 5. Store in cache ─────────────────────────────────────────────────
        if not self._disable_cache and response_text:
            self._cache.put(full_prompt, response_text, chosen.model, actual_cost)

        # ── 6. Record cost ────────────────────────────────────────────────────
        self._tracker.record(
            prompt_tokens=in_tok,
            completion_tokens=out_tok,
            model_used=used_endpoint.model,
            provider=used_endpoint.provider,
            actual_cost_usd=actual_cost,
            baseline_cost_usd=baseline_cost,
            cache_hit=False,
            complexity=complexity.name,
        )

        return OrchestratorResult(
            response_data=response_data,
            response_text=response_text,
            model_used=used_endpoint.model,
            provider=used_endpoint.provider,
            complexity=complexity.name,
            cache_hit=False,
            latency_s=round(latency_s, 3),
            actual_cost_usd=actual_cost,
            baseline_cost_usd=baseline_cost,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt_text(messages: list[dict], system: str) -> str:
        parts = [system] if system else []
        parts += [f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages]
        return "\n".join(parts)

    @staticmethod
    def _make_call_fn(body: dict) -> Callable[[ProviderEndpoint], str]:
        """Return a closure that calls the given ProviderEndpoint with *body*."""
        def _call(endpoint: ProviderEndpoint) -> str:
            try:
                import httpx
            except ImportError as exc:
                raise ImportError(
                    "httpx is required for the proxy server.\n"
                    "Install: pip install 'token-slayer[proxy]'"
                ) from exc

            key = os.environ.get(endpoint.api_key_env, "")
            if not key:
                raise ValueError(f"Missing env var: {endpoint.api_key_env}")

            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{endpoint.base_url}/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={**body, "model": endpoint.model},
                )
                resp.raise_for_status()
                return resp.text

        return _call


def build_orchestrator(
    cache: Any,
    tracker: CostTracker,
    preferred_provider: str | None = None,
    disable_cache: bool = False,
    disable_routing: bool = False,
) -> ProxyOrchestrator:
    """Convenience factory — wires default FailoverChain and PromptChunker."""
    return ProxyOrchestrator(
        cache=cache,
        tracker=tracker,
        failover=FailoverChain(DEFAULT_CHAIN),
        chunker=PromptChunker(),
        preferred_provider=preferred_provider,
        disable_cache=disable_cache,
        disable_routing=disable_routing,
    )
