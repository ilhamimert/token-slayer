"""Token counting using tiktoken as a Claude tokenizer approximation."""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import tiktoken

# cl100k_base is the closest public approximation; real Claude counts may differ +-10%
_ENC = tiktoken.get_encoding("cl100k_base")

# Fallback dirs when no .claudeignore exists
CLAUDEIGNORE_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    "dist", "build", "egg-info", ".pytest_cache", ".mypy_cache",
}

_BINARY_SUFFIXES = {
    ".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".zip", ".gz", ".tar", ".whl", ".db", ".sqlite",
}


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def _is_binary(path: Path) -> bool:
    return path.suffix.lower() in _BINARY_SUFFIXES


def _parse_claudeignore(root: Path) -> list[str]:
    """Read .claudeignore and return non-comment, non-empty patterns."""
    ci = root / ".claudeignore"
    if not ci.exists():
        return []
    patterns: list[str] = []
    for line in ci.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _is_ignored(rel: str, patterns: list[str]) -> bool:
    """Return True if *rel* (POSIX relative path) matches any .claudeignore pattern."""
    rel_posix = rel.replace("\\", "/")
    parts = rel_posix.split("/")
    for pat in patterns:
        pat = pat.rstrip("/")
        # directory component match (e.g. ".venv" or "build")
        if pat in parts:
            return True
        # glob match against full path (e.g. "*.pyc", "*.egg-info")
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        # glob match against filename only
        if fnmatch.fnmatch(parts[-1], pat):
            return True
        # suffix-style dir match (e.g. "*.egg-info/")
        if any(fnmatch.fnmatch(p, pat) for p in parts):
            return True
    return False


_ALWAYS_SKIP = {".venv", "venv", "env", ".git", "node_modules", "__pycache__"}


def count_all_tokens(root: Path) -> dict:
    """Baseline: count readable text files, skipping venv/.git (always noise)."""
    root = root.resolve()
    files: dict[str, int] = {}
    for f in root.rglob("*"):
        if not f.is_file() or _is_binary(f):
            continue
        if any(part in _ALWAYS_SKIP for part in f.relative_to(root).parts):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            files[str(f.relative_to(root))] = count_tokens(text)
        except Exception:
            pass
    return {"total": sum(files.values()), "files": files}


def count_project_tokens(root: Path, extra_ignore: set[str] | None = None) -> dict:
    """Optimized: apply .claudeignore patterns (falls back to hardcoded dirs)."""
    ci_patterns = _parse_claudeignore(root)
    use_patterns = bool(ci_patterns)

    # Merge fallback dirs with any caller-supplied extras
    fallback_dirs = CLAUDEIGNORE_DIRS | (extra_ignore or set())

    files: dict[str, int] = {}
    for f in root.rglob("*"):
        if not f.is_file() or _is_binary(f):
            continue
        rel = str(f.relative_to(root))
        if use_patterns:
            if _is_ignored(rel, ci_patterns):
                continue
        else:
            if any(part in fallback_dirs for part in f.parts):
                continue
            if any(part.endswith(".egg-info") for part in f.parts):
                continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            files[rel] = count_tokens(text)
        except Exception:
            pass
    return {"total": sum(files.values()), "files": files}
