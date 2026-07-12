"""Tests for cca.diff_context — line-level git diff context."""
from __future__ import annotations

from pathlib import Path

import pytest

from cca.diff_context import (
    _pad_ranges,
    _parse_hunks,
    _parse_unified_diff,
    get_diff_context,
)


# ── _parse_hunks ─────────────────────────────────────────────────────────────

class TestParseHunks:
    def test_single_hunk(self):
        text = "@@ -1,3 +1,5 @@\n"
        assert _parse_hunks(text) == [(1, 5)]

    def test_multiple_hunks(self):
        text = "@@ -1,3 +1,5 @@\n@@ -20,2 +23,1 @@\n"
        assert _parse_hunks(text) == [(1, 5), (23, 23)]

    def test_hunk_without_comma_length_is_one_line(self):
        text = "@@ -1 +1 @@\n"
        assert _parse_hunks(text) == [(1, 1)]

    def test_empty_text_returns_empty(self):
        assert _parse_hunks("") == []

    def test_non_hunk_lines_ignored(self):
        text = "diff --git a/x.py b/x.py\n+added line\n@@ -5,0 +6,1 @@\n"
        assert _parse_hunks(text) == [(6, 6)]


# ── _pad_ranges ──────────────────────────────────────────────────────────────

class TestPadRanges:
    def test_single_range_padded(self):
        assert _pad_ranges([(10, 12)], pad=3) == [(7, 15)]

    def test_overlapping_ranges_merge(self):
        assert _pad_ranges([(10, 12), (14, 16)], pad=2) == [(8, 18)]

    def test_non_overlapping_ranges_stay_separate(self):
        result = _pad_ranges([(10, 12), (50, 52)], pad=1)
        assert result == [(9, 13), (49, 53)]

    def test_range_near_line_1_does_not_go_negative(self):
        assert _pad_ranges([(2, 3)], pad=5) == [(1, 8)]

    def test_empty_ranges_returns_empty(self):
        assert _pad_ranges([], pad=3) == []


# ── _parse_unified_diff ────────────────────────────────────────────────────

class TestParseUnifiedDiff:
    def test_single_file_single_hunk(self):
        raw = (
            "diff --git a/app/utils.py b/app/utils.py\n"
            "index abc..def 100644\n"
            "--- a/app/utils.py\n"
            "+++ b/app/utils.py\n"
            "@@ -1,2 +1,3 @@\n"
            "+new line\n"
        )
        result = _parse_unified_diff(raw, pad=0)
        assert result == {"app/utils.py": [(1, 3)]}

    def test_non_py_file_excluded(self):
        raw = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1,1 +1,2 @@\n"
        )
        assert _parse_unified_diff(raw, pad=0) == {}

    def test_multiple_files(self):
        raw = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -1,1 +1,2 @@\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n+++ b/b.py\n"
            "@@ -5,0 +6,1 @@\n"
        )
        result = _parse_unified_diff(raw, pad=0)
        assert result == {"a.py": [(1, 2)], "b.py": [(6, 6)]}

    def test_empty_diff_returns_empty(self):
        assert _parse_unified_diff("", pad=3) == {}


# ── get_diff_context ─────────────────────────────────────────────────────────

class TestGetDiffContext:
    def test_returns_empty_for_non_git_repo(self, tmp_path: Path):
        assert get_diff_context(tmp_path) == {}

    def test_returns_empty_when_no_changes(self, git_project: Path):
        assert get_diff_context(git_project) == {}

    def test_detects_uncommitted_change(self, dirty_git_project: Path):
        result = get_diff_context(dirty_git_project)
        assert "app/utils.py" in result
        assert result["app/utils.py"]

    def test_pad_expands_range(self, dirty_git_project: Path):
        tight = get_diff_context(dirty_git_project, pad=0)
        padded = get_diff_context(dirty_git_project, pad=10)
        tight_span = tight["app/utils.py"][0]
        padded_span = padded["app/utils.py"][0]
        assert (padded_span[1] - padded_span[0]) >= (tight_span[1] - tight_span[0])

    def test_non_py_files_excluded(self, dirty_git_project: Path):
        (dirty_git_project / "notes.md").write_text("# notes\nsomething new\n", encoding="utf-8")
        result = get_diff_context(dirty_git_project)
        assert "notes.md" not in result

    def test_staged_only_detects_staged_change(self, git_project: Path):
        from git import Repo
        repo = Repo(git_project)
        (git_project / "app" / "utils.py").write_text(
            "def helper():\n    return 100\n\ndef unused_function():\n    return None\n",
            encoding="utf-8",
        )
        repo.index.add(["app/utils.py"])
        result = get_diff_context(git_project, staged_only=True)
        assert "app/utils.py" in result
