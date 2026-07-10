"""Privacy-preserving semantic cache with two-tier similarity engine.

Tier 1 — SimHash (always available, zero dependencies):
  64-bit fingerprint, Hamming distance. Fast, lightweight.
  Catches exact rewrites and minor paraphrases.
  Typical hit rate: ~30-40%.

Tier 2 — Embedding (optional, requires sentence-transformers):
  Dense vector cosine similarity. Catches cross-lingual queries,
  synonyms, and structural rephrasing.
  Typical hit rate: ~65-80%.

Install Tier 2:
  pip install sentence-transformers

Privacy guarantee (both tiers):
  NO prompt plaintext is persisted — only fingerprints/vectors and
  SHA-256 keys. The response text IS stored (you cached it for reuse).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Similarity engine protocol ────────────────────────────────────────────────

@runtime_checkable
class SimilarityEngine(Protocol):
    """Any object that can fingerprint text and compute similarity."""

    def fingerprint(self, text: str) -> list[float]:
        """Return a numeric vector for *text*."""
        ...

    def similarity(self, a: list[float], b: list[float]) -> float:
        """Return similarity in [0.0, 1.0]."""
        ...

    @property
    def name(self) -> str:
        ...


# ── Tier 1: SimHash ───────────────────────────────────────────────────────────

def _simhash_int(text: str, n_bits: int = 64) -> int:
    text = text.lower().strip()
    v = [0] * n_bits
    for word in text.split():
        h = int(hashlib.md5(word.encode(), usedforsecurity=False).hexdigest(), 16)
        for i in range(n_bits):
            v[i] += 1 if (h >> (i % 128)) & 1 else -1
    result = 0
    for i in range(n_bits):
        if v[i] > 0:
            result |= 1 << i
    return result


class SimHashEngine:
    """64-bit SimHash — no external dependencies."""

    n_bits: int = 64

    def fingerprint(self, text: str) -> list[float]:
        h = _simhash_int(text, self.n_bits)
        # Store as list of bits so JSON serialisation works
        return [(h >> i) & 1 for i in range(self.n_bits)]

    def similarity(self, a: list[float], b: list[float]) -> float:
        differing = sum(int(x) ^ int(y) for x, y in zip(a, b))
        return 1.0 - differing / self.n_bits

    @property
    def name(self) -> str:
        return "simhash"


# ── Tier 2: Sentence-Transformers ────────────────────────────────────────────

class EmbeddingEngine:
    """Dense embedding similarity via sentence-transformers.

    Uses 'all-MiniLM-L6-v2' by default:
      - 384-dim vectors, 80MB model, ~5ms/query on CPU
      - Multilingual queries work (Turkish ↔ English cache hits)
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            self._model = SentenceTransformer(model_name)
            self._model_name = model_name
            logger.info("EmbeddingEngine loaded: %s", model_name)
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for embedding-based cache.\n"
                "Install: pip install sentence-transformers"
            ) from exc

    def fingerprint(self, text: str) -> list[float]:
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def similarity(self, a: list[float], b: list[float]) -> float:
        # Both vectors are L2-normalised, so dot product = cosine similarity
        return float(sum(x * y for x, y in zip(a, b)))

    @property
    def name(self) -> str:
        return f"embedding:{self._model_name}"


def _best_available_engine() -> SimilarityEngine:
    """Return EmbeddingEngine if sentence-transformers is installed, else SimHash.

    Set ``TSLAYER_EMBEDDING_MODEL`` to override the model name, e.g.::

        TSLAYER_EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
    """
    import os
    model_name = os.environ.get("TSLAYER_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    try:
        engine = EmbeddingEngine(model_name=model_name)
        logger.info("Using Tier-2 embedding cache (sentence-transformers): %s", model_name)
        return engine
    except ImportError:
        logger.info("sentence-transformers not found — using Tier-1 SimHash cache")
        return SimHashEngine()


# ── SHA-256 helpers ───────────────────────────────────────────────────────────

def _exact_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── Cache entry ───────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    fingerprint: list[float]  # Engine-specific vector (bits or floats)
    exact_key: str            # SHA-256 of prompt
    response: str             # Cached response
    model_used: str
    engine: str               # "simhash" | "embedding:model-name"
    created_at: float
    cost_usd: float
    hit_count: int = 0

    def is_expired(self, ttl: float) -> bool:
        return (time.time() - self.created_at) > ttl

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CacheEntry":
        return cls(**data)


# ── Semantic cache ────────────────────────────────────────────────────────────

class SemanticCache:
    """Two-tier semantic cache: exact SHA-256 lookup + vector similarity search.

    Parameters
    ----------
    similarity_threshold:
        Minimum similarity score (0–1) for a cache hit.
        SimHash:  0.90 ≈ 6/64 bits differ   (tight)
        Embedding: 0.85 ≈ cosine ≥ 0.85     (broad)
    ttl:
        Entry lifetime in seconds.
    persist_path:
        JSON file for cache durability across restarts.
    engine:
        Pass a custom SimilarityEngine, or None to auto-detect.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.88,
        ttl: float = 3_600,
        persist_path: Path | None = None,
        engine: SimilarityEngine | None = None,
    ) -> None:
        self._threshold = similarity_threshold
        self._ttl = ttl
        self._path = persist_path
        self._lock = threading.Lock()
        self._engine: SimilarityEngine = engine or _best_available_engine()
        self._entries: dict[str, CacheEntry] = {}

        if persist_path and persist_path.exists():
            self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def engine_name(self) -> str:
        return self._engine.name

    def get(self, prompt: str) -> CacheEntry | None:
        """Return cached entry for *prompt*, or None on miss."""
        key = _exact_key(prompt)
        fp = self._engine.fingerprint(prompt)

        with self._lock:
            self._evict_expired()

            # 1. Exact match — O(1)
            entry = self._entries.get(key)
            if entry is not None:
                entry.hit_count += 1
                return entry

            # 2. Semantic match — O(n)
            best: CacheEntry | None = None
            best_sim = 0.0
            for e in self._entries.values():
                # Skip entries from a different engine dimension
                if len(e.fingerprint) != len(fp):
                    continue
                sim = self._engine.similarity(fp, e.fingerprint)
                if sim >= self._threshold and sim > best_sim:
                    best_sim = sim
                    best = e

            if best is not None:
                best.hit_count += 1
                logger.debug("Semantic cache hit (sim=%.3f, engine=%s)", best_sim, self._engine.name)
                return best

        return None

    def put(
        self,
        prompt: str,
        response: str,
        model_used: str,
        cost_usd: float = 0.0,
    ) -> CacheEntry:
        """Store a prompt→response pair."""
        entry = CacheEntry(
            fingerprint=self._engine.fingerprint(prompt),
            exact_key=_exact_key(prompt),
            response=response,
            model_used=model_used,
            engine=self._engine.name,
            created_at=time.time(),
            cost_usd=cost_usd,
        )
        with self._lock:
            self._entries[entry.exact_key] = entry
            if self._path:
                self._save()
        return entry

    def invalidate(self, prompt: str) -> None:
        key = _exact_key(prompt)
        with self._lock:
            self._entries.pop(key, None)
            if self._path:
                self._save()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            if self._path:
                self._save()

    def stats(self) -> dict:
        with self._lock:
            entries = list(self._entries.values())
        total_hits = sum(e.hit_count for e in entries)
        total_saved = sum(e.cost_usd * e.hit_count for e in entries)
        return {
            "engine": self._engine.name,
            "entry_count": len(entries),
            "total_hits": total_hits,
            "estimated_cost_saved_usd": round(total_saved, 6),
            "threshold": self._threshold,
            "oldest_entry_age_s": (
                round(time.time() - min(e.created_at for e in entries))
                if entries else 0
            ),
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        assert self._path is not None
        try:
            data = {k: v.to_dict() for k, v in self._entries.items()}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            loaded = 0
            for k, v in raw.items():
                e = CacheEntry.from_dict(v)
                if not e.is_expired(self._ttl):
                    self._entries[k] = e
                    loaded += 1
            logger.debug("Loaded %d cache entries from %s", loaded, self._path)
        except Exception:
            pass

    def _evict_expired(self) -> None:
        expired = [k for k, e in self._entries.items() if e.is_expired(self._ttl)]
        for k in expired:
            del self._entries[k]
