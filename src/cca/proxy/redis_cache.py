"""Redis-backed exact-match cache layer.

Used in front of SemanticCache for O(1) distributed exact-match lookups.
Multiple proxy replicas share one Redis, so a cache hit on instance A
is also available on instance B.

Architecture:
  Request → RedisCache.get() (exact SHA-256, O(1))
      HIT  → return immediately, record savings
      MISS → SemanticCache.get() (vector similarity, O(n))
          HIT  → backfill Redis, return
          MISS → call LLM → store in both caches

Installation:
  pip install 'token-slayer[redis]'

Configuration (env vars):
  REDIS_URL=redis://localhost:6379/0   (default)
  TSLAYER_REDIS_TTL=3600               (seconds, default 1h)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_REDIS_URL_ENV = "REDIS_URL"
_DEFAULT_URL = "redis://localhost:6379/0"
_DEFAULT_TTL = 3_600
_KEY_PREFIX = "tslayer:cache:"


def _exact_key(prompt: str) -> str:
    return _KEY_PREFIX + hashlib.sha256(prompt.encode()).hexdigest()


class RedisCache:
    """Thin wrapper around redis-py for exact-match prompt caching.

    Parameters
    ----------
    url:
        Redis connection URL. Defaults to REDIS_URL env var, then localhost.
    ttl:
        Entry TTL in seconds. Defaults to TSLAYER_REDIS_TTL env var, then 3600.
    """

    def __init__(
        self,
        url: str | None = None,
        ttl: int | None = None,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError as exc:
            raise ImportError(
                "redis package is required for RedisCache.\n"
                "Install: pip install 'token-slayer[redis]'"
            ) from exc

        self._url = url or os.environ.get(_REDIS_URL_ENV, _DEFAULT_URL)
        self._ttl = ttl or int(os.environ.get("TSLAYER_REDIS_TTL", _DEFAULT_TTL))
        self._client = redis_lib.from_url(self._url, decode_responses=True)
        logger.info("RedisCache connected: %s (ttl=%ds)", self._url, self._ttl)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, prompt: str) -> dict | None:
        """Return cached entry dict or None."""
        try:
            raw = self._client.get(_exact_key(prompt))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("Redis GET error: %s", exc)
        return None

    def put(
        self,
        prompt: str,
        response: str,
        model_used: str,
        cost_usd: float = 0.0,
    ) -> None:
        """Store response in Redis with TTL."""
        key = _exact_key(prompt)
        payload = json.dumps({
            "response": response,
            "model_used": model_used,
            "cost_usd": cost_usd,
        })
        try:
            self._client.setex(key, self._ttl, payload)
        except Exception as exc:
            logger.warning("Redis SET error: %s", exc)

    def invalidate(self, prompt: str) -> None:
        try:
            self._client.delete(_exact_key(prompt))
        except Exception as exc:
            logger.warning("Redis DEL error: %s", exc)

    def flush_prefix(self) -> int:
        """Delete all tslayer cache keys. Returns number of deleted keys."""
        try:
            keys = list(self._client.scan_iter(f"{_KEY_PREFIX}*"))
            if keys:
                return self._client.delete(*keys)
        except Exception as exc:
            logger.warning("Redis flush error: %s", exc)
        return 0

    def ping(self) -> bool:
        """Return True if Redis is reachable."""
        try:
            return self._client.ping()
        except Exception:
            return False

    def stats(self) -> dict:
        """Return basic Redis info."""
        try:
            info = self._client.info("stats")
            key_count = sum(
                1 for _ in self._client.scan_iter(f"{_KEY_PREFIX}*", count=1000)
            )
            return {
                "url": self._url,
                "reachable": True,
                "tslayer_keys": key_count,
                "total_commands_processed": info.get("total_commands_processed", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
            }
        except Exception as exc:
            return {"url": self._url, "reachable": False, "error": str(exc)}


# ── Optional: build a two-layer cache stack ───────────────────────────────────

def build_cache_stack(
    semantic_cache: Any,
    redis_url: str | None = None,
    redis_ttl: int | None = None,
) -> "TwoLayerCache":
    """Wrap a SemanticCache with a Redis fast-path."""
    redis = RedisCache(url=redis_url, ttl=redis_ttl)
    return TwoLayerCache(redis=redis, semantic=semantic_cache)


class TwoLayerCache:
    """Redis (L1 exact) → SemanticCache (L2 vector) two-layer lookup."""

    def __init__(self, redis: RedisCache, semantic: Any) -> None:
        self._redis = redis
        self._semantic = semantic

    def get(self, prompt: str) -> Any | None:
        # L1: Redis exact match
        hit = self._redis.get(prompt)
        if hit:
            logger.debug("L1 Redis cache hit")
            return hit  # raw dict

        # L2: Semantic similarity
        entry = self._semantic.get(prompt)
        if entry:
            # Backfill Redis so future exact hits are O(1)
            self._redis.put(prompt, entry.response, entry.model_used, entry.cost_usd)
            return entry
        return None

    def put(self, prompt: str, response: str, model_used: str, cost_usd: float = 0.0) -> Any:
        self._redis.put(prompt, response, model_used, cost_usd)
        return self._semantic.put(prompt, response, model_used, cost_usd)

    def clear(self) -> None:
        self._redis.flush_prefix()
        self._semantic.clear()

    def stats(self) -> dict:
        return {
            "redis": self._redis.stats(),
            "semantic": self._semantic.stats(),
        }
