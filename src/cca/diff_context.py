"""Line-level git diff context — changed files + line ranges, not whole files."""
from __future__ import annotations

import re
from pathlib import Path

try:
    from git import Repo
    _HAS_GIT = True
except ImportError:
    _HAS_GIT = False

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def _parse_hunks(patch_text: str) -> list[tuple[int, int]]:
    """Parse unified diff hunk headers into (new_start, new_end) line ranges."""
    ranges: list[tuple[int, int]] = []
    for line in patch_text.splitlines():
        m = _HUNK_RE.match(line)
        if not m:
            continue
        start = int(m.group(3))
        length = int(m.group(4)) if m.group(4) else 1
        ranges.append((start, start + max(length, 1) - 1))
    return ranges


def _pad_ranges(ranges: list[tuple[int, int]], pad: int) -> list[tuple[int, int]]:
    """Expand each range by *pad* lines and merge overlapping ranges."""
    if not ranges:
        return []
    padded = [(max(1, s - pad), e + pad) for s, e in ranges]
    padded.sort()
    merged = [padded[0]]
    for s, e in padded[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e + 1:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged


def _parse_unified_diff(raw: str, pad: int) -> dict[str, list[tuple[int, int]]]:
    """Split a multi-file unified diff into per-file, padded line ranges."""
    result: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    hunk_lines: list[str] = []

    def _flush() -> None:
        if current_file and current_file.endswith(".py") and hunk_lines:
            ranges = _pad_ranges(_parse_hunks("\n".join(hunk_lines)), pad)
            if ranges:
                result[current_file] = ranges

    for line in raw.splitlines():
        m = _FILE_HEADER_RE.match(line)
        if m:
            _flush()
            current_file = m.group(2)
            hunk_lines = []
            continue
        if line.startswith("@@"):
            hunk_lines.append(line)

    _flush()
    return result


def get_diff_context(
    repo_path: Path,
    pad: int = 3,
    staged_only: bool = False,
) -> dict[str, list[tuple[int, int]]]:
    """Return {relative_path: [(start_line, end_line), ...]} for changed .py files.

    Diffs the working tree against HEAD by default (uncommitted changes,
    staged + unstaged combined). staged_only=True diffs only the index
    against HEAD. Returns {} if not a git repo or on any git error.
    """
    if not _HAS_GIT:
        return {}
    try:
        repo = Repo(repo_path, search_parent_directories=True)
    except Exception:
        return {}
    try:
        if staged_only:
            raw = repo.git.diff("--cached", "HEAD", "--unified=0")
        else:
            raw = repo.git.diff("HEAD", "--unified=0")
    except Exception:
        return {}
    return _parse_unified_diff(raw, pad)
