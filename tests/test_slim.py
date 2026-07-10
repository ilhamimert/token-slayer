"""Tests for cca.slim — .claudeignore pattern suggestion."""
from __future__ import annotations

from pathlib import Path

import pytest

from cca.slim import analyze_token_distribution, apply_patterns, suggest_patterns


# ── analyze_token_distribution ────────────────────────────────────────────────

class TestAnalyzeTokenDistribution:
    def test_returns_list_of_dicts(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n")
        result = analyze_token_distribution(tmp_path)
        assert isinstance(result, list)
        assert all("file" in r and "tokens" in r for r in result)

    def test_sorted_descending_by_tokens(self, tmp_path: Path):
        (tmp_path / "small.py").write_text("x = 1\n")
        (tmp_path / "large.py").write_text("x = 1\n" * 200)
        result = analyze_token_distribution(tmp_path)
        tokens = [r["tokens"] for r in result]
        assert tokens == sorted(tokens, reverse=True)

    def test_skips_binary_files(self, tmp_path: Path):
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n")
        (tmp_path / "code.py").write_text("pass")
        result = analyze_token_distribution(tmp_path)
        assert not any(r["file"].endswith(".png") for r in result)

    def test_skips_claudeignore_dirs(self, tmp_path: Path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.pyc").write_bytes(b"\x00\x00")
        (tmp_path / "real.py").write_text("pass")
        result = analyze_token_distribution(tmp_path)
        assert not any("__pycache__" in r["file"] for r in result)

    def test_uses_posix_paths(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.py").write_text("x = 1")
        result = analyze_token_distribution(tmp_path)
        assert all("\\" not in r["file"] for r in result)


# ── suggest_patterns ──────────────────────────────────────────────────────────

class TestSuggestPatterns:
    def test_returns_list(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n")
        result = suggest_patterns(tmp_path)
        assert isinstance(result, list)

    def test_result_has_required_keys(self, tmp_path: Path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("# Guide\n" * 100)
        result = suggest_patterns(tmp_path, budget=0)
        if result:
            keys = result[0].keys()
            assert "pattern" in keys
            assert "saves_tokens" in keys
            assert "files_affected" in keys
            assert "reason" in keys

    def test_docs_dir_suggested(self, tmp_path: Path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("# Guide\n" * 200)
        result = suggest_patterns(tmp_path, budget=0)
        patterns = [r["pattern"] for r in result]
        assert any("docs" in p for p in patterns)

    def test_src_dir_never_suggested(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("def main(): pass\n" * 300)
        result = suggest_patterns(tmp_path, budget=0)
        patterns = [r["pattern"] for r in result]
        assert not any(p == "src/" for p in patterns)

    def test_source_files_never_suggested_individually(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "big_module.py").write_text("x = 1\n" * 5000)
        result = suggest_patterns(tmp_path, budget=0)
        patterns = [r["pattern"] for r in result]
        assert not any("src/" in p or "src\\" in p for p in patterns)

    def test_lock_files_suggested_as_extension(self, tmp_path: Path):
        (tmp_path / "package-lock.json").write_text("{}" * 300)
        (tmp_path / "yarn.lock").write_text("# lockfile\n" * 200)
        result = suggest_patterns(tmp_path, budget=0)
        patterns = [r["pattern"] for r in result]
        assert any(".lock" in p for p in patterns)

    def test_max_suggestions_respected(self, tmp_path: Path):
        for i in range(20):
            (tmp_path / f"docs{i}").mkdir()
            (tmp_path / f"docs{i}" / "guide.md").write_text("# Guide\n" * 100)
        result = suggest_patterns(tmp_path, max_suggestions=5)
        assert len(result) <= 5

    def test_already_claudeignored_patterns_not_suggested(self, tmp_path: Path):
        (tmp_path / ".claudeignore").write_text("docs/\n")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("# Guide\n" * 200)
        result = suggest_patterns(tmp_path, budget=0)
        patterns = [r["pattern"] for r in result]
        assert "docs/" not in patterns

    def test_saves_tokens_is_positive(self, tmp_path: Path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "large.md").write_text("# Doc\n" * 300)
        result = suggest_patterns(tmp_path, budget=0)
        assert all(r["saves_tokens"] > 0 for r in result)


# ── apply_patterns ────────────────────────────────────────────────────────────

class TestApplyPatterns:
    def test_creates_claudeignore_if_missing(self, tmp_path: Path):
        ci = tmp_path / ".claudeignore"
        assert not ci.exists()
        apply_patterns(tmp_path, ["build/"])
        assert ci.exists()

    def test_appends_new_patterns(self, tmp_path: Path):
        ci = tmp_path / ".claudeignore"
        ci.write_text("# existing\n.venv/\n")
        apply_patterns(tmp_path, ["build/", "dist/"])
        content = ci.read_text()
        assert "build/" in content
        assert "dist/" in content

    def test_returns_count_of_added_patterns(self, tmp_path: Path):
        n = apply_patterns(tmp_path, ["build/", "dist/"])
        assert n == 2

    def test_does_not_duplicate_existing_patterns(self, tmp_path: Path):
        ci = tmp_path / ".claudeignore"
        ci.write_text(".venv/\n")
        n = apply_patterns(tmp_path, [".venv/", "build/"])
        assert n == 1
        content = ci.read_text()
        assert content.count(".venv/") == 1

    def test_returns_zero_when_nothing_new(self, tmp_path: Path):
        ci = tmp_path / ".claudeignore"
        ci.write_text("build/\n")
        n = apply_patterns(tmp_path, ["build/"])
        assert n == 0

    def test_preserves_existing_content(self, tmp_path: Path):
        ci = tmp_path / ".claudeignore"
        ci.write_text("# My rules\n.venv/\n")
        apply_patterns(tmp_path, ["dist/"])
        content = ci.read_text()
        assert "# My rules" in content
        assert ".venv/" in content
