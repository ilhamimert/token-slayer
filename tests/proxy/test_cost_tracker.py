"""Tests for cca.proxy.cost_tracker."""
from __future__ import annotations

import time

import pytest

from cca.proxy.cost_tracker import CostTracker


def _record(tracker: CostTracker, *, cache_hit=False, actual=0.001, baseline=0.01, complexity="SIMPLE"):
    tracker.record(
        prompt_tokens=100,
        completion_tokens=50,
        model_used="claude-haiku-4-5-20251001",
        provider="anthropic",
        actual_cost_usd=actual,
        baseline_cost_usd=baseline,
        cache_hit=cache_hit,
        complexity=complexity,
    )


# ── record ────────────────────────────────────────────────────────────────────

class TestRecord:
    def test_single_record_appears_in_summary(self):
        tracker = CostTracker()
        _record(tracker)
        summary = tracker.summarize()
        assert summary.total_calls == 1

    def test_multiple_records_counted(self):
        tracker = CostTracker()
        for _ in range(5):
            _record(tracker)
        assert tracker.summarize().total_calls == 5

    def test_cache_hit_is_counted(self):
        tracker = CostTracker()
        _record(tracker, cache_hit=True)
        _record(tracker, cache_hit=False)
        summary = tracker.summarize()
        assert summary.cache_hits == 1

    def test_cost_is_accumulated(self):
        tracker = CostTracker()
        _record(tracker, actual=0.001, baseline=0.010)
        _record(tracker, actual=0.002, baseline=0.020)
        summary = tracker.summarize()
        assert abs(summary.actual_cost_usd - 0.003) < 1e-9
        assert abs(summary.baseline_cost_usd - 0.030) < 1e-9


# ── summarize ─────────────────────────────────────────────────────────────────

class TestSummarize:
    def test_empty_tracker_returns_zero_summary(self):
        tracker = CostTracker()
        summary = tracker.summarize()
        assert summary.total_calls == 0
        assert summary.savings_usd == 0.0

    def test_savings_is_baseline_minus_actual(self):
        tracker = CostTracker()
        _record(tracker, actual=0.001, baseline=0.010)
        summary = tracker.summarize()
        assert abs(summary.savings_usd - 0.009) < 1e-9

    def test_savings_pct_is_correct(self):
        tracker = CostTracker()
        _record(tracker, actual=0.001, baseline=0.010)
        summary = tracker.summarize()
        assert abs(summary.savings_pct - 90.0) < 0.1

    def test_cache_hit_rate_is_correct(self):
        tracker = CostTracker()
        _record(tracker, cache_hit=True)
        _record(tracker, cache_hit=True)
        _record(tracker, cache_hit=False)
        summary = tracker.summarize()
        assert abs(summary.cache_hit_rate_pct - (2 / 3 * 100)) < 0.1

    def test_time_window_filters_old_records(self):
        tracker = CostTracker()
        # Manually inject a record with an old timestamp
        from cca.proxy.cost_tracker import CallRecord
        old_record = CallRecord(
            timestamp=time.time() - 48 * 3600,
            prompt_tokens=100, completion_tokens=50,
            model_used="haiku", provider="anthropic",
            actual_cost_usd=0.001, baseline_cost_usd=0.01,
            cache_hit=False, complexity="SIMPLE",
        )
        tracker._records.append(old_record)
        _record(tracker)  # recent record

        summary = tracker.summarize(since_hours=24.0)
        assert summary.total_calls == 1  # only the recent one

    def test_by_model_counts_correctly(self):
        tracker = CostTracker()
        _record(tracker)
        _record(tracker)
        summary = tracker.summarize()
        assert summary.by_model["claude-haiku-4-5-20251001"] == 2

    def test_by_complexity_counts_correctly(self):
        tracker = CostTracker()
        _record(tracker, complexity="SIMPLE")
        _record(tracker, complexity="COMPLEX")
        _record(tracker, complexity="SIMPLE")
        summary = tracker.summarize()
        assert summary.by_complexity["SIMPLE"] == 2
        assert summary.by_complexity["COMPLEX"] == 1

    def test_to_dict_is_serialisable(self):
        tracker = CostTracker()
        _record(tracker)
        d = tracker.summarize().to_dict()
        assert isinstance(d, dict)
        assert "total_calls" in d


# ── dashboard_text ────────────────────────────────────────────────────────────

class TestDashboardText:
    def test_dashboard_text_contains_cost_info(self):
        tracker = CostTracker()
        _record(tracker, actual=0.001, baseline=0.010)
        text = tracker.dashboard_text()
        assert "Actual cost" in text
        assert "Savings" in text

    def test_dashboard_text_shows_call_count(self):
        tracker = CostTracker()
        _record(tracker)
        _record(tracker)
        text = tracker.dashboard_text()
        assert "2" in text

    def test_empty_tracker_dashboard_shows_zeros(self):
        tracker = CostTracker()
        text = tracker.dashboard_text()
        assert "0" in text


# ── reset + persistence ───────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears_all_records(self):
        tracker = CostTracker()
        _record(tracker)
        _record(tracker)
        tracker.reset()
        assert tracker.summarize().total_calls == 0


class TestPersistence:
    def test_records_survive_reload(self, tmp_path):
        path = tmp_path / "tracker.json"
        t1 = CostTracker(persist_path=path)
        _record(t1, actual=0.005, baseline=0.05)

        t2 = CostTracker(persist_path=path)
        summary = t2.summarize()
        assert summary.total_calls == 1
        assert abs(summary.actual_cost_usd - 0.005) < 1e-9
