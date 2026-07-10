"""Tests for cca.snapshot — context snapshot generation."""
from __future__ import annotations

from pathlib import Path

import pytest

from cca.snapshot import (
    _build_file_tree,
    _extract_signatures,
    _tree_lines,
    build_snapshot,
    snapshot_token_stats,
)


# ── _extract_signatures ───────────────────────────────────────────────────────

class TestExtractSignatures:
    def test_empty_source_returns_empty(self):
        assert _extract_signatures("") == []

    def test_extracts_def(self):
        src = "def foo(x: int) -> str:\n    return str(x)\n"
        sigs = _extract_signatures(src)
        assert any("def foo" in s for s in sigs)

    def test_extracts_async_def(self):
        src = "async def bar() -> None:\n    pass\n"
        sigs = _extract_signatures(src)
        assert any("async def bar" in s for s in sigs)

    def test_extracts_class(self):
        src = "class MyModel:\n    pass\n"
        sigs = _extract_signatures(src)
        assert any("class MyModel" in s for s in sigs)

    def test_indented_method_has_indent(self):
        src = "class Foo:\n    def bar(self):\n        pass\n"
        sigs = _extract_signatures(src)
        method = next((s for s in sigs if "def bar" in s), None)
        assert method is not None
        assert method.startswith("  ")  # indented inside class

    def test_no_body_lines(self):
        src = "def foo():\n    x = 1\n    return x\n"
        sigs = _extract_signatures(src)
        assert not any("return" in s for s in sigs)
        assert not any("x = 1" in s for s in sigs)

    def test_normalises_extra_whitespace(self):
        src = "def   foo(  x,   y  ):\n    pass\n"
        sigs = _extract_signatures(src)
        assert all("  " not in s.strip() for s in sigs)


# ── _build_file_tree ──────────────────────────────────────────────────────────

class TestBuildFileTree:
    def test_returns_relative_posix_paths(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("y = 2")
        paths = _build_file_tree(tmp_path, [])
        assert all("/" in p or p.count(".") >= 1 for p in paths)
        assert "a.py" in paths

    def test_skips_claudeignore_pattern(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "out.py").write_text("y = 2")
        paths = _build_file_tree(tmp_path, ["build/"])
        assert not any("build" in p for p in paths)

    def test_skips_binary_files(self, tmp_path: Path):
        (tmp_path / "img.png").write_bytes(b"\x89PNG")
        (tmp_path / "code.py").write_text("pass")
        paths = _build_file_tree(tmp_path, [])
        assert not any(p.endswith(".png") for p in paths)

    def test_backslashes_converted_to_forward(self, tmp_path: Path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.py").write_text("z = 3")
        paths = _build_file_tree(tmp_path, [])
        assert all("\\" not in p for p in paths)


# ── _tree_lines ───────────────────────────────────────────────────────────────

class TestTreeLines:
    def test_empty_produces_empty(self):
        assert _tree_lines([]) == []

    def test_flat_files_appear(self):
        lines = _tree_lines(["a.py", "b.py"])
        joined = "\n".join(lines)
        assert "a.py" in joined
        assert "b.py" in joined

    def test_directory_appears_before_file(self):
        lines = _tree_lines(["sub/c.py"])
        joined = "\n".join(lines)
        assert "sub/" in joined
        assert "c.py" in joined


# ── build_snapshot ────────────────────────────────────────────────────────────

class TestBuildSnapshot:
    def test_returns_string(self, tmp_path: Path):
        (tmp_path / "hello.py").write_text("def greet(): pass\n")
        result = build_snapshot(tmp_path)
        assert isinstance(result, str)

    def test_contains_file_tree_header(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("pass")
        result = build_snapshot(tmp_path)
        assert "## File Tree" in result

    def test_contains_signatures_header_for_py_files(self, tmp_path: Path):
        (tmp_path / "mod.py").write_text("def foo(): pass\n")
        result = build_snapshot(tmp_path)
        assert "## Signatures" in result

    def test_signatures_section_absent_when_no_py_files(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# Hello")
        result = build_snapshot(tmp_path)
        assert "## Signatures" not in result

    def test_small_toml_included_in_config_section(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[tool]\nname = 'x'\n")
        result = build_snapshot(tmp_path)
        assert "## Config & Docs" in result
        assert "pyproject.toml" in result

    def test_claudeignore_respected(self, tmp_path: Path):
        (tmp_path / ".claudeignore").write_text("secret/\n")
        (tmp_path / "secret").mkdir()
        (tmp_path / "secret" / "key.py").write_text("KEY = 'abc'")
        (tmp_path / "main.py").write_text("pass")
        result = build_snapshot(tmp_path)
        assert "secret" not in result or "key.py" not in result

    def test_project_name_in_header(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("pass")
        result = build_snapshot(tmp_path)
        assert tmp_path.name in result


# ── snapshot_token_stats ──────────────────────────────────────────────────────

class TestSnapshotTokenStats:
    def test_returns_expected_keys(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n")
        stats = snapshot_token_stats(tmp_path, "short snapshot")
        assert "with_claudeignore_tokens" in stats
        assert "snapshot_tokens" in stats
        assert "reduction_pct" in stats

    def test_snapshot_tokens_matches_input(self, tmp_path: Path):
        from cca.token_counter import count_tokens
        (tmp_path / "a.py").write_text("x = 1\n")
        text = "hello world this is a snapshot"
        stats = snapshot_token_stats(tmp_path, text)
        assert stats["snapshot_tokens"] == count_tokens(text)

    def test_reduction_pct_is_float(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n")
        stats = snapshot_token_stats(tmp_path, "tiny")
        assert isinstance(stats["reduction_pct"], float)
