"""Detect symbols that are defined but never used elsewhere in the project."""
from __future__ import annotations

import re
from pathlib import Path

from cca.parser import FileInfo

_SKIP_NAMES = {
    "__init__", "__repr__", "__str__", "__eq__", "__hash__",
    "__len__", "__iter__", "__next__", "__enter__", "__exit__",
    "__call__", "__getitem__", "__setitem__", "__contains__",
    # Common closure/helper names that appear inside other functions
    "main", "visit", "callback", "handler",
}

# Directory patterns to exclude from dead-code analysis
_SKIP_DIRS = {"tests", "test", "test-project"}

# Decorators that register functions via frameworks or mark Python built-ins.
# These methods are never "imported" by other modules — they're accessed via
# instance attribute access or framework discovery.
_FRAMEWORK_DECORATOR_RE = re.compile(
    r"@(?:app|router|blueprint|server|mcp|pytest|fixture"  # framework registration
    r"|property|staticmethod|classmethod|abstractmethod"    # Python built-ins
    r")\b",
    re.IGNORECASE,
)


def _has_framework_decorator(source: str, sym: str) -> bool:
    """Return True if *sym*'s def is preceded by a framework decorator."""
    lines = source.splitlines()
    def_re = re.compile(rf"^\s*(?:async\s+)?def\s+{re.escape(sym)}\s*[:(]")
    for i, line in enumerate(lines):
        if def_re.match(line):
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("@"):
                if _FRAMEWORK_DECORATOR_RE.search(lines[j]):
                    return True
                j -= 1
    return False


def find_unused_exports(file_infos: list[FileInfo], root: Path) -> dict[str, list[str]]:
    """
    Return {relative_path: [symbol, ...]} for functions/classes that appear
    to be defined but never referenced in any other file.

    Uses simple text search — dynamic access and __all__ are not detected.
    Excludes test files and framework-registered entry points.
    """
    source_texts: dict[str, str] = {}
    info_by_rel: dict[str, FileInfo] = {}
    for info in file_infos:
        rel = str(info.path.relative_to(root)).replace("\\", "/")
        # Skip test directories — pytest finds tests dynamically, not via import
        if any(part in _SKIP_DIRS for part in info.path.parts):
            continue
        try:
            source_texts[rel] = info.path.read_text(encoding="utf-8")
        except Exception:
            source_texts[rel] = ""
        info_by_rel[rel] = info

    result: dict[str, list[str]] = {}
    for rel, src in source_texts.items():
        info = info_by_rel.get(rel)
        if info is None:
            continue

        symbols = [
            s for s in (info.functions + info.classes)
            if not s.startswith("_") and s not in _SKIP_NAMES
        ]

        unused = []
        for sym in symbols:
            # Skip if registered via framework decorator
            if _has_framework_decorator(src, sym):
                continue
            # Skip if used internally (appears more than once in own file:
            # once for def/class, any extra occurrence is a real usage)
            if src.count(sym) > 1:
                continue
            # Skip if referenced in any other source file
            if any(sym in text for other_rel, text in source_texts.items() if other_rel != rel):
                continue
            unused.append(sym)

        if unused:
            result[rel] = unused

    return result
