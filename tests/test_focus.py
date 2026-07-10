"""Tests for cca.focus — task-focused file relevance ranking."""
from __future__ import annotations

from pathlib import Path

import pytest

from cca.focus import _score_file, _tokenize, focus_context_tokens, rank_files


# ── _tokenize ─────────────────────────────────────────────────────────────────

class TestTokenize:
    def test_splits_on_word_boundaries(self):
        assert "hello" in _tokenize("hello world")

    def test_lowercases(self):
        tokens = _tokenize("Hello World")
        assert "hello" in tokens
        assert "world" in tokens

    def test_filters_short_words(self):
        tokens = _tokenize("a bb ccc dddd")
        assert "a" not in tokens
        assert "bb" not in tokens
        assert "ccc" in tokens
        assert "dddd" in tokens

    def test_handles_underscores(self):
        tokens = _tokenize("snake_case_name")
        assert "snake_case_name" in tokens

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_non_alpha_filtered(self):
        tokens = _tokenize("123 !! hello")
        assert "123" not in tokens
        assert "hello" in tokens


# ── _score_file ───────────────────────────────────────────────────────────────

class TestScoreFile:
    def test_zero_score_for_empty_keywords(self):
        score = _score_file("auth.py", "def login(): pass", [], set())
        assert score == 0.0

    def test_path_match_increases_score(self):
        score_with_path = _score_file(
            "auth/login.py", "pass", ["auth"], {"auth"}
        )
        score_no_path = _score_file(
            "utils/helper.py", "pass", ["auth"], {"auth"}
        )
        assert score_with_path > score_no_path

    def test_content_match_increases_score(self):
        content_with = "def authenticate(user): return token"
        content_without = "def render_page(): return html"
        s1 = _score_file("page.py", content_with, ["authenticate"], {"authenticate"})
        s2 = _score_file("page.py", content_without, ["authenticate"], {"authenticate"})
        assert s1 > s2

    def test_large_file_penalised(self):
        small_content = "def foo(): pass"
        large_content = ("x = 1\n" * 2000)  # many tokens
        s_small = _score_file("a.py", small_content, ["foo"], {"foo"})
        s_large = _score_file("a.py", large_content, ["foo"], {"foo"})
        # Large file gets penalty even if it has the same keyword once
        assert s_small >= s_large

    def test_repeated_keyword_in_content_gives_diminishing_returns(self):
        once = _score_file("f.py", "auth", ["auth"], {"auth"})
        six_times = _score_file("f.py", " ".join(["auth"] * 6), ["auth"], {"auth"})
        # Score should grow but not linearly — diminishing returns
        assert six_times > once
        # But not 6x
        assert six_times < once * 6


# ── rank_files ────────────────────────────────────────────────────────────────

class TestRankFiles:
    def _make_project(self, tmp_path: Path) -> Path:
        (tmp_path / "auth.py").write_text("def authenticate(user): return True\n")
        (tmp_path / "router.py").write_text("def route(path): return handler\n")
        (tmp_path / "utils.py").write_text("def format_date(d): return str(d)\n")
        return tmp_path

    def test_returns_list_of_dicts(self, tmp_path: Path):
        self._make_project(tmp_path)
        results = rank_files(tmp_path, "authentication login")
        assert isinstance(results, list)
        assert all(isinstance(r, dict) for r in results)

    def test_result_has_required_keys(self, tmp_path: Path):
        self._make_project(tmp_path)
        results = rank_files(tmp_path, "authentication")
        if results:
            keys = results[0].keys()
            assert "file" in keys
            assert "score" in keys
            assert "tokens" in keys
            assert "reason" in keys

    def test_auth_file_ranked_first_for_auth_query(self, tmp_path: Path):
        self._make_project(tmp_path)
        results = rank_files(tmp_path, "authenticate")
        assert results, "Expected at least one result"
        assert "auth" in results[0]["file"]

    def test_top_n_respected(self, tmp_path: Path):
        for i in range(10):
            (tmp_path / f"module{i}.py").write_text(f"def func{i}(): pass\n")
        results = rank_files(tmp_path, "func", top_n=3)
        assert len(results) <= 3

    def test_zero_score_files_excluded(self, tmp_path: Path):
        (tmp_path / "irrelevant.py").write_text("x = 42\n")
        results = rank_files(tmp_path, "authentication_xyzzy_nonexistent")
        assert len(results) == 0

    def test_claudeignore_respected(self, tmp_path: Path):
        (tmp_path / ".claudeignore").write_text("secret/\n")
        (tmp_path / "secret").mkdir()
        (tmp_path / "secret" / "keys.py").write_text("def get_key(): return 'abc'\n")
        (tmp_path / "main.py").write_text("def main(): pass\n")
        results = rank_files(tmp_path, "key get_key")
        files = [r["file"] for r in results]
        assert not any("secret" in f for f in files)

    def test_scores_sorted_descending(self, tmp_path: Path):
        self._make_project(tmp_path)
        results = rank_files(tmp_path, "authenticate route format")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_reason_contains_match_info(self, tmp_path: Path):
        (tmp_path / "payment.py").write_text("def charge_card(): pass\n")
        results = rank_files(tmp_path, "payment charge")
        if results:
            assert results[0]["reason"] != ""


# ── focus_context_tokens ──────────────────────────────────────────────────────

class TestFocusContextTokens:
    def test_sums_tokens(self):
        ranked = [
            {"file": "a.py", "score": 3.0, "tokens": 100, "reason": ""},
            {"file": "b.py", "score": 2.0, "tokens": 200, "reason": ""},
        ]
        assert focus_context_tokens(ranked) == 300

    def test_empty_list_returns_zero(self):
        assert focus_context_tokens([]) == 0
