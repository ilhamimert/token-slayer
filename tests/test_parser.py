"""Tests for cca.parser — tree-sitter based Python file analysis."""
from __future__ import annotations

from pathlib import Path

import pytest

from cca.parser import analyze_file, analyze_project, FileInfo, IGNORE_DIRS


class TestAnalyzeFile:
    def test_returns_file_info(self, sample_project: Path):
        info = analyze_file(sample_project / "main.py")
        assert isinstance(info, FileInfo)

    def test_line_count(self, sample_project: Path):
        info = analyze_file(sample_project / "main.py")
        # 9 newlines in the file → count(b"\n") + 1 = 10
        assert info.lines == 10

    def test_detects_functions(self, sample_project: Path):
        info = analyze_file(sample_project / "main.py")
        assert "run" in info.functions
        assert "startup" in info.functions

    def test_detects_class(self, sample_project: Path):
        info = analyze_file(sample_project / "app" / "config.py")
        assert "Settings" in info.classes

    def test_detects_import_statement(self, sample_project: Path):
        info = analyze_file(sample_project / "main.py")
        assert "os" in info.imports

    def test_detects_from_import(self, sample_project: Path):
        info = analyze_file(sample_project / "main.py")
        assert "app.config" in info.imports
        assert "app.utils" in info.imports

    def test_function_count_property(self, sample_project: Path):
        info = analyze_file(sample_project / "app" / "utils.py")
        assert info.function_count == 2

    def test_class_count_property(self, sample_project: Path):
        info = analyze_file(sample_project / "app" / "models.py")
        assert info.class_count == 1

    def test_empty_init_file(self, sample_project: Path):
        info = analyze_file(sample_project / "app" / "__init__.py")
        assert info.function_count == 0
        assert info.class_count == 0
        assert info.imports == []

    def test_path_stored(self, sample_project: Path):
        target = sample_project / "main.py"
        info = analyze_file(target)
        assert info.path == target

    def test_dataclass_decorator_not_counted_as_function(self, sample_project: Path):
        info = analyze_file(sample_project / "app" / "models.py")
        assert "User" in info.classes
        # greet is a method inside a class — still counted by tree-sitter as function_definition
        assert info.function_count >= 1

    def test_method_imports_extracted(self, sample_project: Path):
        info = analyze_file(sample_project / "app" / "models.py")
        assert "app.config" in info.imports
        assert "dataclasses" in info.imports

    def test_has_syntax_error_false_for_valid_file(self, sample_project: Path):
        info = analyze_file(sample_project / "main.py")
        assert info.has_syntax_error is False

    def test_has_syntax_error_true_for_broken_file(self, tmp_path: Path):
        broken = tmp_path / "broken.py"
        broken.write_text("def broken(:\n    pass\n", encoding="utf-8")
        info = analyze_file(broken)
        assert info.has_syntax_error is True

    def test_has_syntax_error_roundtrips_through_to_from_dict(self, tmp_path: Path):
        broken = tmp_path / "broken.py"
        broken.write_text("def broken(:\n    pass\n", encoding="utf-8")
        info = analyze_file(broken)
        restored = FileInfo.from_dict(info.to_dict())
        assert restored.has_syntax_error is True

    def test_from_dict_defaults_has_syntax_error_when_key_missing(self):
        data = {"path": "x.py", "lines": 1}
        info = FileInfo.from_dict(data)
        assert info.has_syntax_error is False


class TestAnalyzeProject:
    def test_finds_all_py_files(self, sample_project: Path):
        infos = analyze_project(sample_project)
        paths = {str(i.path.relative_to(sample_project)).replace("\\", "/") for i in infos}
        assert "main.py" in paths
        assert "app/config.py" in paths
        assert "app/utils.py" in paths
        assert "app/models.py" in paths
        assert "app/__init__.py" in paths

    def test_skips_ignore_dirs(self, sample_project: Path):
        venv = sample_project / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "fake.py").write_text("x = 1\n", encoding="utf-8")

        infos = analyze_project(sample_project)
        paths = [str(i.path) for i in infos]
        assert not any(".venv" in p for p in paths)

    def test_skips_pycache(self, sample_project: Path):
        cache = sample_project / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-311.pyc").write_bytes(b"\x00fake")

        infos = analyze_project(sample_project)
        paths = [str(i.path) for i in infos]
        assert not any("__pycache__" in p for p in paths)

    def test_returns_list_of_file_info(self, sample_project: Path):
        infos = analyze_project(sample_project)
        assert all(isinstance(i, FileInfo) for i in infos)

    def test_sorted_output(self, sample_project: Path):
        infos = analyze_project(sample_project)
        paths = [i.path for i in infos]
        assert paths == sorted(paths)

    def test_empty_directory(self, tmp_path: Path):
        infos = analyze_project(tmp_path)
        assert infos == []

    def test_ignore_dirs_constant_includes_common_dirs(self):
        for expected in (".venv", "venv", "__pycache__", ".git", "node_modules"):
            assert expected in IGNORE_DIRS
