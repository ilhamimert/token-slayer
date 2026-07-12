"""Tests for cca.cache — incremental parse cache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cca.cache import (
    _file_sig,
    get_cached,
    invalidate,
    load_cache,
    save_cache,
    set_cached,
)
from cca.parser import FileInfo


def _sample_info(path: Path) -> dict:
    return FileInfo(path=path, lines=10, functions=["foo"], imports=["os"]).to_dict()


class TestLoadSaveCache:
    def test_load_missing_returns_empty(self, tmp_path: Path):
        cache = load_cache(tmp_path)
        assert cache == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        cache: dict = {}
        py = tmp_path / "a.py"
        py.write_text("x = 1\n", encoding="utf-8")
        set_cached(cache, py, tmp_path, _sample_info(py))
        save_cache(tmp_path, cache)

        loaded = load_cache(tmp_path)
        assert "a.py" in loaded

    def test_load_corrupt_returns_empty(self, tmp_path: Path):
        (tmp_path / ".cca_cache.json").write_text("not json", encoding="utf-8")
        assert load_cache(tmp_path) == {}


class TestGetSetCached:
    def test_set_then_get_returns_data(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text("def f(): pass\n", encoding="utf-8")
        cache: dict = {}
        data = _sample_info(py)
        set_cached(cache, py, tmp_path, data)
        result = get_cached(cache, py, tmp_path)
        assert result == data

    def test_modified_file_invalidates_cache(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text("x = 1\n", encoding="utf-8")
        cache: dict = {}
        set_cached(cache, py, tmp_path, _sample_info(py))
        # Write noticeably more content so st_size changes regardless of mtime precision
        py.write_text("x = 2\n" + "# padding\n" * 10, encoding="utf-8")
        result = get_cached(cache, py, tmp_path)
        assert result is None

    def test_unknown_file_returns_none(self, tmp_path: Path):
        py = tmp_path / "nonexistent.py"
        py.write_text("", encoding="utf-8")
        assert get_cached({}, py, tmp_path) is None


class TestInvalidate:
    def test_removes_entry(self, tmp_path: Path):
        py = tmp_path / "a.py"
        py.write_text("", encoding="utf-8")
        cache: dict = {}
        set_cached(cache, py, tmp_path, _sample_info(py))
        assert get_cached(cache, py, tmp_path) is not None
        invalidate(cache, py, tmp_path)
        assert get_cached(cache, py, tmp_path) is None

    def test_invalidate_missing_key_safe(self, tmp_path: Path):
        py = tmp_path / "ghost.py"
        py.write_text("", encoding="utf-8")
        cache: dict = {}
        invalidate(cache, py, tmp_path)  # should not raise


class TestAnalyzeProjectCache:
    def test_second_run_uses_cache(self, tmp_path: Path):
        (tmp_path / "mod.py").write_text("def f(): pass\n", encoding="utf-8")
        from cca.parser import analyze_project
        infos1 = analyze_project(tmp_path, use_cache=True)
        infos2 = analyze_project(tmp_path, use_cache=True)
        assert len(infos1) == len(infos2)
        assert infos1[0].functions == infos2[0].functions

    def test_cache_disabled_still_works(self, tmp_path: Path):
        (tmp_path / "mod.py").write_text("def g(): pass\n", encoding="utf-8")
        from cca.parser import analyze_project
        infos = analyze_project(tmp_path, use_cache=False)
        assert len(infos) == 1
        assert "g" in infos[0].functions

    def test_stale_cache_entry_missing_has_syntax_error_key_defaults_false(self, tmp_path: Path):
        py = tmp_path / "mod.py"
        py.write_text("def f(): pass\n", encoding="utf-8")
        cache: dict = {}
        data = _sample_info(py)
        del data["has_syntax_error"]  # simulate a cache entry written before this field existed
        set_cached(cache, py, tmp_path, data)
        result = get_cached(cache, py, tmp_path)
        assert FileInfo.from_dict(result).has_syntax_error is False
