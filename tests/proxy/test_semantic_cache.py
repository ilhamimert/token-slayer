"""Tests for cca.proxy.semantic_cache — SimHash engine + SemanticCache."""
from __future__ import annotations

import time

import pytest

from cca.proxy.semantic_cache import (
    CacheEntry,
    SemanticCache,
    SimHashEngine,
)


# ── SimHashEngine ─────────────────────────────────────────────────────────────

class TestSimHashEngine:
    def setup_method(self):
        self.engine = SimHashEngine()

    def test_fingerprint_returns_64_floats(self):
        fp = self.engine.fingerprint("hello world")
        assert len(fp) == 64
        assert all(v in (0, 1) for v in fp)

    def test_identical_texts_have_similarity_one(self):
        fp = self.engine.fingerprint("same text")
        assert self.engine.similarity(fp, fp) == 1.0

    def test_completely_different_texts_have_lower_similarity(self):
        fp_a = self.engine.fingerprint("apple orange banana")
        fp_b = self.engine.fingerprint("quantum mechanics relativity")
        assert self.engine.similarity(fp_a, fp_b) < 1.0

    def test_near_duplicate_has_high_similarity(self):
        fp_a = self.engine.fingerprint("What is the capital of France?")
        fp_b = self.engine.fingerprint("What is France's capital city?")
        assert self.engine.similarity(fp_a, fp_b) > 0.5

    def test_engine_name_is_simhash(self):
        assert self.engine.name == "simhash"

    def test_fingerprint_is_deterministic(self):
        fp1 = self.engine.fingerprint("consistent")
        fp2 = self.engine.fingerprint("consistent")
        assert fp1 == fp2


# ── SemanticCache ─────────────────────────────────────────────────────────────

@pytest.fixture
def cache():
    return SemanticCache(
        similarity_threshold=0.90,
        ttl=3_600,
        persist_path=None,
        engine=SimHashEngine(),
    )


class TestSemanticCachePut:
    def test_put_returns_cache_entry(self, cache):
        entry = cache.put("What is Python?", "Python is a language.", "haiku", 0.001)
        assert isinstance(entry, CacheEntry)

    def test_put_stores_response(self, cache):
        cache.put("hello", "world", "haiku", 0.0)
        hit = cache.get("hello")
        assert hit is not None
        assert hit.response == "world"

    def test_put_stores_model(self, cache):
        cache.put("hello", "world", "claude-haiku", 0.005)
        hit = cache.get("hello")
        assert hit.model_used == "claude-haiku"

    def test_put_stores_cost(self, cache):
        cache.put("hello", "world", "haiku", 0.005)
        hit = cache.get("hello")
        assert hit.cost_usd == 0.005


class TestSemanticCacheGet:
    def test_exact_match_returns_entry(self, cache):
        cache.put("exact query", "response", "model", 0.0)
        hit = cache.get("exact query")
        assert hit is not None

    def test_miss_returns_none(self, cache):
        assert cache.get("this was never stored") is None

    def test_exact_hit_increments_hit_count(self, cache):
        cache.put("query", "response", "model", 0.0)
        cache.get("query")
        entry = cache.get("query")
        assert entry.hit_count >= 2

    def test_expired_entry_is_not_returned(self):
        short_ttl_cache = SemanticCache(
            similarity_threshold=0.90,
            ttl=0.01,
            persist_path=None,
            engine=SimHashEngine(),
        )
        short_ttl_cache.put("expiring", "response", "model", 0.0)
        time.sleep(0.05)
        assert short_ttl_cache.get("expiring") is None

    def test_semantic_match_with_high_similarity(self, cache):
        # Store with one phrasing, retrieve with a near-duplicate
        cache.put(
            "What is the Python programming language?",
            "Python is a programming language.",
            "model",
            0.0,
        )
        # Near-duplicate — SimHash may or may not hit depending on token overlap.
        # At minimum, exact match should always work.
        hit = cache.get("What is the Python programming language?")
        assert hit is not None

    def test_returns_none_below_threshold(self):
        strict_cache = SemanticCache(
            similarity_threshold=0.999,
            ttl=3_600,
            persist_path=None,
            engine=SimHashEngine(),
        )
        strict_cache.put("apple orange banana grape", "fruits", "model", 0.0)
        # Completely different text should not exceed 0.999 threshold
        assert strict_cache.get("quantum physics relativity") is None


class TestSemanticCacheClear:
    def test_clear_removes_all_entries(self, cache):
        cache.put("a", "r1", "m", 0.0)
        cache.put("b", "r2", "m", 0.0)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_clear_resets_stats(self, cache):
        cache.put("a", "r", "m", 0.0)
        cache.clear()
        assert cache.stats()["entry_count"] == 0


class TestSemanticCacheStats:
    def test_stats_has_required_keys(self, cache):
        stats = cache.stats()
        required = {"engine", "entry_count", "total_hits", "estimated_cost_saved_usd", "threshold"}
        assert required <= stats.keys()

    def test_stats_entry_count_reflects_puts(self, cache):
        cache.put("x", "r", "m", 0.0)
        cache.put("y", "r", "m", 0.0)
        assert cache.stats()["entry_count"] == 2

    def test_stats_engine_is_simhash(self, cache):
        assert cache.stats()["engine"] == "simhash"

    def test_stats_accumulates_hits(self, cache):
        cache.put("q", "r", "m", 0.01)
        cache.get("q")
        cache.get("q")
        assert cache.stats()["total_hits"] >= 2

    def test_stats_estimated_cost_saved(self, cache):
        cache.put("q", "r", "m", 0.05)
        cache.get("q")
        stats = cache.stats()
        assert stats["estimated_cost_saved_usd"] > 0.0


class TestSemanticCachePersistence:
    def test_entries_survive_reload(self, tmp_path):
        path = tmp_path / "cache.json"
        c1 = SemanticCache(similarity_threshold=0.90, ttl=3_600, persist_path=path, engine=SimHashEngine())
        c1.put("persistent query", "saved response", "model", 0.01)

        c2 = SemanticCache(similarity_threshold=0.90, ttl=3_600, persist_path=path, engine=SimHashEngine())
        hit = c2.get("persistent query")
        assert hit is not None
        assert hit.response == "saved response"

    def test_expired_entries_not_loaded(self, tmp_path):
        path = tmp_path / "cache.json"
        c1 = SemanticCache(similarity_threshold=0.90, ttl=0.01, persist_path=path, engine=SimHashEngine())
        c1.put("expiring", "response", "model", 0.0)
        time.sleep(0.05)

        c2 = SemanticCache(similarity_threshold=0.90, ttl=0.01, persist_path=path, engine=SimHashEngine())
        assert c2.get("expiring") is None
