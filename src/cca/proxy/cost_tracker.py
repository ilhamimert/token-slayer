"""Real-time cost and savings tracker.

Tracks every LLM call through the proxy and computes:
  - Actual spend (routed model cost)
  - Baseline spend (what it would cost without routing/caching)
  - Savings = baseline - actual
  - Cache hit rate

All state is in-memory; use persist_path for disk-backed durability.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class CallRecord:
    timestamp: float
    prompt_tokens: int
    completion_tokens: int
    model_used: str
    provider: str
    actual_cost_usd: float
    baseline_cost_usd: float
    cache_hit: bool
    complexity: str     # "SIMPLE" | "MEDIUM" | "COMPLEX"


@dataclass
class PeriodSummary:
    period_start: float
    period_end: float
    total_calls: int
    cache_hits: int
    actual_cost_usd: float
    baseline_cost_usd: float
    savings_usd: float
    savings_pct: float
    cache_hit_rate_pct: float
    by_model: dict[str, int]
    by_complexity: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)


class CostTracker:
    """Thread-safe cost & savings tracker.

    Parameters
    ----------
    persist_path:
        Optional JSON file for durable storage across restarts.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._records: list[CallRecord] = []
        self._path = persist_path
        if persist_path and persist_path.exists():
            self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        model_used: str,
        provider: str,
        actual_cost_usd: float,
        baseline_cost_usd: float,
        cache_hit: bool = False,
        complexity: str = "MEDIUM",
    ) -> None:
        record = CallRecord(
            timestamp=time.time(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_used=model_used,
            provider=provider,
            actual_cost_usd=actual_cost_usd,
            baseline_cost_usd=baseline_cost_usd,
            cache_hit=cache_hit,
            complexity=complexity,
        )
        with self._lock:
            self._records.append(record)
            if self._path:
                self._save()

    def summarize(self, since_hours: float = 24.0) -> PeriodSummary:
        """Return aggregated stats for the last *since_hours* hours."""
        cutoff = time.time() - since_hours * 3_600
        with self._lock:
            window = [r for r in self._records if r.timestamp >= cutoff]

        if not window:
            return PeriodSummary(
                period_start=cutoff,
                period_end=time.time(),
                total_calls=0,
                cache_hits=0,
                actual_cost_usd=0.0,
                baseline_cost_usd=0.0,
                savings_usd=0.0,
                savings_pct=0.0,
                cache_hit_rate_pct=0.0,
                by_model={},
                by_complexity={},
            )

        total = len(window)
        hits = sum(1 for r in window if r.cache_hit)
        actual = sum(r.actual_cost_usd for r in window)
        baseline = sum(r.baseline_cost_usd for r in window)
        savings = baseline - actual
        savings_pct = savings / baseline * 100 if baseline else 0.0

        by_model: dict[str, int] = {}
        by_complexity: dict[str, int] = {}
        for r in window:
            by_model[r.model_used] = by_model.get(r.model_used, 0) + 1
            by_complexity[r.complexity] = by_complexity.get(r.complexity, 0) + 1

        return PeriodSummary(
            period_start=cutoff,
            period_end=time.time(),
            total_calls=total,
            cache_hits=hits,
            actual_cost_usd=round(actual, 6),
            baseline_cost_usd=round(baseline, 6),
            savings_usd=round(savings, 6),
            savings_pct=round(savings_pct, 1),
            cache_hit_rate_pct=round(hits / total * 100, 1) if total else 0.0,
            by_model=by_model,
            by_complexity=by_complexity,
        )

    def dashboard_text(self, since_hours: float = 24.0) -> str:
        """Return a plain-text savings dashboard (for CLI output)."""
        s = self.summarize(since_hours)
        lines = [
            f"── Cost Dashboard (last {since_hours:.0f}h) ──────────────────────────",
            f"  Total calls     : {s.total_calls:>8,}",
            f"  Cache hits      : {s.cache_hits:>8,}  ({s.cache_hit_rate_pct:.1f}%)",
            f"  Baseline cost   : ${s.baseline_cost_usd:>10.4f}  (all GPT-4o)",
            f"  Actual cost     : ${s.actual_cost_usd:>10.4f}  (routed + cached)",
            f"  Savings         : ${s.savings_usd:>10.4f}  ({s.savings_pct:.1f}%)",
            "  By model:",
        ]
        for model, count in sorted(s.by_model.items(), key=lambda x: -x[1]):
            lines.append(f"    {model:<40} {count:>5} calls")
        lines.append("  By complexity:")
        for cplx, count in sorted(s.by_complexity.items()):
            lines.append(f"    {cplx:<12} {count:>5} calls")
        return "\n".join(lines)

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            if self._path:
                self._save()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        assert self._path is not None
        try:
            data = [asdict(r) for r in self._records[-10_000:]]  # cap at 10k records
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = [CallRecord(**r) for r in raw]
        except Exception:
            pass
