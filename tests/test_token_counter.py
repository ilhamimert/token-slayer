"""Tests for cca.token_counter — token counting and project analysis."""
from __future__ import annotations

from pathlib import Path

import pytest

from cca.token_counter import count_tokens, count_all_tokens, count_project_tokens, CLAUDEIGNORE_DIRS


class TestCountTokens:
    def test_returns_positive_int(self):
        assert count_tokens("hello world") > 0

    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_longer_text_has_more_tokens(self):
        short = count_tokens("hello")
        long = count_tokens("hello " * 100)
        assert long > short

    def test_same_text_same_count(self):
        text = "def foo():\n    return 42\n"
        assert count_tokens(text) == count_tokens(text)

    def test_returns_int(self):
        assert isinstance(count_tokens("test"), int)

    def test_code_tokenizes(self):
        code = "from pathlib import Path\n\ndef analyze(root: Path) -> list:\n    return []\n"
        assert count_tokens(code) > 5


class TestCountAllTokens:
    def test_returns_dict_with_total_and_files(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        result = count_all_tokens(tmp_path)
        assert "total" in result
        assert "files" in result

    def test_total_equals_sum_of_files(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
        result = count_all_tokens(tmp_path)
        assert result["total"] == sum(result["files"].values())

    def test_always_skips_venv(self, tmp_path: Path):
        # _ALWAYS_SKIP ensures .venv is excluded even in the "baseline" count
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "pkg.py").write_text("LARGE = 'x' * 100\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

        result = count_all_tokens(tmp_path)
        paths = set(result["files"].keys())
        assert not any(".venv" in p for p in paths)
        assert any("main.py" in p for p in paths)

    def test_skips_binary_files(self, tmp_path: Path):
        (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "lib.dll").write_bytes(b"MZ")
        (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")
        result = count_all_tokens(tmp_path)
        paths = set(result["files"].keys())
        assert not any(p.endswith(".png") or p.endswith(".dll") for p in paths)

    def test_empty_dir(self, tmp_path: Path):
        result = count_all_tokens(tmp_path)
        assert result["total"] == 0
        assert result["files"] == {}


class TestCountProjectTokens:
    def test_skips_venv(self, tmp_path: Path):
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "huge.py").write_text("BIG = 'x' * 1000\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

        result = count_project_tokens(tmp_path)
        paths = set(result["files"].keys())
        assert not any(".venv" in p for p in paths)

    def test_skips_pycache(self, tmp_path: Path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.pyc").write_bytes(b"\x00fake")
        (tmp_path / "real.py").write_text("x = 1\n", encoding="utf-8")

        result = count_project_tokens(tmp_path)
        paths = set(result["files"].keys())
        assert not any("__pycache__" in p for p in paths)

    def test_savings_vs_all_tokens(self, tmp_path: Path):
        # .claudeignore excludes 'logs/' — optimized should be smaller than baseline
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "app.log").write_text("INFO: started\n" * 200, encoding="utf-8")
        (tmp_path / ".claudeignore").write_text("logs/\n")
        (tmp_path / "src.py").write_text("def foo(): pass\n", encoding="utf-8")

        baseline = count_all_tokens(tmp_path)["total"]
        optimized = count_project_tokens(tmp_path)["total"]
        assert baseline > optimized

    def test_extra_ignore_applied(self, tmp_path: Path):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "app.log").write_text("INFO: started\n" * 100, encoding="utf-8")
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

        result_with = count_project_tokens(tmp_path, extra_ignore={"logs"})
        result_without = count_project_tokens(tmp_path)
        assert result_with["total"] < result_without["total"]

    def test_total_is_int(self, sample_project: Path):
        assert isinstance(count_project_tokens(sample_project)["total"], int)


class TestClaudeignoreDirs:
    def test_contains_expected_dirs(self):
        for d in (".venv", "venv", "__pycache__", ".git", "node_modules"):
            assert d in CLAUDEIGNORE_DIRS
