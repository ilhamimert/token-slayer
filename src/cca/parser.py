"""Parse Python source files using tree-sitter."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_python as tspython

_LANGUAGE = Language(tspython.language())
_PARSER = Parser(_LANGUAGE)

IGNORE_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    "dist", "build", ".egg-info", ".pytest_cache", ".mypy_cache",
}

_TEST_DIRS = {"tests", "test", "test-project"}

_COMPLEXITY_NODES = {
    "if_statement", "elif_clause",
    "for_statement", "while_statement",
    "except_clause", "conditional_expression",
    "boolean_operator",
}


@dataclass
class FileInfo:
    path: Path
    lines: int
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    complexity: int = 0       # cyclomatic: branch node count
    typed_functions: int = 0  # functions with return type annotation
    language: str = "python"
    has_syntax_error: bool = False

    @property
    def function_count(self) -> int:
        return len(self.functions)

    @property
    def class_count(self) -> int:
        return len(self.classes)

    @property
    def type_coverage(self) -> float:
        if not self.functions:
            return 0.0
        return self.typed_functions / len(self.functions) * 100

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "lines": self.lines,
            "functions": self.functions,
            "classes": self.classes,
            "imports": self.imports,
            "complexity": self.complexity,
            "typed_functions": self.typed_functions,
            "language": self.language,
            "has_syntax_error": self.has_syntax_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FileInfo":
        return cls(
            path=Path(data["path"]),
            lines=data["lines"],
            functions=data.get("functions", []),
            classes=data.get("classes", []),
            imports=data.get("imports", []),
            complexity=data.get("complexity", 0),
            typed_functions=data.get("typed_functions", 0),
            language=data.get("language", "python"),
            # Stale cache entries predating this field default to False
            # until the file is re-analyzed.
            has_syntax_error=data.get("has_syntax_error", False),
        )


def _walk(node, visitor):
    visitor(node)
    for child in node.children:
        _walk(child, visitor)


def _extract_imports(root_node, source: bytes) -> list[str]:
    results: list[str] = []

    def visit(node):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    results.append(source[child.start_byte:child.end_byte].decode())
                elif child.type == "aliased_import":
                    for sub in child.children:
                        if sub.type == "dotted_name":
                            results.append(source[sub.start_byte:sub.end_byte].decode())
                            break
        elif node.type == "import_from_statement":
            for child in node.children:
                if child.type == "relative_import":
                    break
                if child.type == "dotted_name":
                    results.append(source[child.start_byte:child.end_byte].decode())
                    break

    _walk(root_node, visit)
    return results


def _extract_definitions(root_node, source: bytes) -> tuple[list[str], list[str]]:
    functions: list[str] = []
    classes: list[str] = []

    def visit(node):
        if node.type == "function_definition":
            for child in node.children:
                if child.type == "identifier":
                    functions.append(source[child.start_byte:child.end_byte].decode())
                    break
        elif node.type == "class_definition":
            for child in node.children:
                if child.type == "identifier":
                    classes.append(source[child.start_byte:child.end_byte].decode())
                    break

    _walk(root_node, visit)
    return functions, classes


def _calc_complexity(root_node) -> int:
    """Count branch/decision nodes for cyclomatic complexity."""
    count = 0

    def visit(node):
        nonlocal count
        if node.type in _COMPLEXITY_NODES:
            count += 1

    _walk(root_node, visit)
    return count


def _calc_typed_functions(root_node) -> int:
    """Count functions that have a return type annotation (-> Type)."""
    count = 0

    def visit(node):
        nonlocal count
        if node.type == "function_definition":
            if node.child_by_field_name("return_type") is not None:
                count += 1

    _walk(root_node, visit)
    return count


def analyze_file(path: Path) -> FileInfo:
    source = path.read_bytes()
    tree = _PARSER.parse(source)
    lines = source.count(b"\n") + 1
    imports = _extract_imports(tree.root_node, source)
    functions, classes = _extract_definitions(tree.root_node, source)
    complexity = _calc_complexity(tree.root_node)
    typed_functions = _calc_typed_functions(tree.root_node)
    return FileInfo(
        path=path,
        lines=lines,
        imports=imports,
        functions=functions,
        classes=classes,
        complexity=complexity,
        typed_functions=typed_functions,
        has_syntax_error=tree.root_node.has_error,
    )


def filter_source_files(file_infos: list[FileInfo]) -> list[FileInfo]:
    """Exclude test files from quality metrics.

    Test functions never carry return annotations (that's normal), and
    pytest discovers tests dynamically rather than via import, so they
    skew type-coverage and complexity metrics if included.
    """
    return [
        fi for fi in file_infos
        if not any(part in _TEST_DIRS for part in fi.path.parts)
    ]


def analyze_project(root: Path, use_cache: bool = True) -> list[FileInfo]:
    from cca.cache import load_cache, save_cache, get_cached, set_cached
    root = root.resolve()
    cache = load_cache(root) if use_cache else {}
    results: list[FileInfo] = []
    dirty = False
    for py_file in sorted(root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in py_file.parts):
            continue
        try:
            cached_data = get_cached(cache, py_file, root) if use_cache else None
            if cached_data is not None:
                info = FileInfo.from_dict(cached_data)
                # Ensure path is always absolute regardless of how it was cached
                if not info.path.is_absolute():
                    info = FileInfo(
                        path=root / info.path,
                        lines=info.lines,
                        functions=info.functions,
                        classes=info.classes,
                        imports=info.imports,
                        complexity=info.complexity,
                        typed_functions=info.typed_functions,
                        language=info.language,
                        has_syntax_error=info.has_syntax_error,
                    )
                results.append(info)
            else:
                info = analyze_file(py_file)
                if use_cache:
                    set_cached(cache, py_file, root, info.to_dict())
                    dirty = True
                results.append(info)
        except Exception:
            pass
    if dirty and use_cache:
        save_cache(root, cache)
    return results
