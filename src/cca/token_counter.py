"""Token counting using tiktoken as a Claude tokenizer approximation."""
from __future__ import annotations

from pathlib import Path

import tiktoken

# cl100k_base is the closest public approximation; real Claude counts may differ +-10%
_ENC = tiktoken.get_encoding("cl100k_base")

# Dirs skipped in the "optimized" scenario (what .claudeignore would cover)
CLAUDEIGNORE_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    "dist", "build", "egg-info", ".pytest_cache", ".mypy_cache",
}

# Always skip binary files in both modes
_BINARY_SUFFIXES = {".pyc", ".pyo", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg", ".gif", ".zip", ".gz"}


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def _is_binary(path: Path) -> bool:
    return path.suffix in _BINARY_SUFFIXES


def count_all_tokens(root: Path) -> dict:
    """Naive baseline: count every readable text file (simulates no .claudeignore)."""
    files: dict[str, int] = {}
    for f in root.rglob("*"):
        if not f.is_file() or _is_binary(f):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            files[str(f.relative_to(root))] = count_tokens(text)
        except Exception:
            pass
    return {"total": sum(files.values()), "files": files}


def count_project_tokens(root: Path, extra_ignore: set[str] | None = None) -> dict:
    """Optimized: skip common non-source dirs (simulates .claudeignore in effect)."""
    ignore_dirs = CLAUDEIGNORE_DIRS | (extra_ignore or set())
    files: dict[str, int] = {}
    for f in root.rglob("*"):
        if not f.is_file() or _is_binary(f):
            continue
        if any(part in ignore_dirs for part in f.parts):
            continue
        if any(part.endswith(".egg-info") for part in f.parts):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            files[str(f.relative_to(root))] = count_tokens(text)
        except Exception:
            pass
    return {"total": sum(files.values()), "files": files}
