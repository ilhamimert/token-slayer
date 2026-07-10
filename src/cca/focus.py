"""Task-focused file relevance ranking.

Given a natural language task description, scores every file in the project
by how likely it is to be relevant. Claude only reads the top-N files
instead of the whole project.

Algorithm: TF-IDF-inspired keyword matching (no ML, no external deps).
  1. Tokenize the query into keywords
  2. For each file, count keyword occurrences (path + content)
  3. Weight path matches 3x (file names are very strong signals)
  4. Normalize by file size to avoid large files always winning
  5. Return top-N sorted by score
"""
from __future__ import annotations

import re
from pathlib import Path

from cca.token_counter import (
    _is_binary,
    _is_ignored,
    _parse_claudeignore,
    count_tokens,
    CLAUDEIGNORE_DIRS,
)


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase words, filter noise."""
    return [w.lower() for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text) if len(w) >= 3]


def _score_file(
    rel: str,
    content: str,
    keywords: list[str],
    keyword_set: set[str],
) -> float:
    if not keywords:
        return 0.0

    path_tokens = set(_tokenize(rel))
    content_lower = content.lower()
    content_tokens = _tokenize(content)
    content_freq: dict[str, int] = {}
    for t in content_tokens:
        content_freq[t] = content_freq.get(t, 0) + 1

    score = 0.0
    for kw in keyword_set:
        # Path match is a strong signal (weight 3x)
        if any(kw in pt for pt in path_tokens):
            score += 3.0
        # Exact word match in content
        count = content_freq.get(kw, 0)
        if count:
            # Diminishing returns: log-like scaling
            score += 1.0 + min(count - 1, 4) * 0.2

    # Penalize very large files slightly (avoid monolith bias)
    size_penalty = max(1.0, len(content_tokens) / 500)
    return score / size_penalty


def rank_files(
    root: Path,
    query: str,
    top_n: int = 10,
) -> list[dict]:
    """Return top-N files most relevant to *query*, sorted by score.

    Returns
    -------
    list of dicts: {"file": str, "score": float, "tokens": int, "reason": str}
    """
    keywords = _tokenize(query)
    keyword_set = set(keywords)

    ci_patterns = _parse_claudeignore(root)
    results: list[dict] = []

    for f in root.rglob("*"):
        if not f.is_file() or _is_binary(f):
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        if ci_patterns:
            if _is_ignored(rel, ci_patterns):
                continue
        else:
            if any(part in CLAUDEIGNORE_DIRS for part in f.parts):
                continue

        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        score = _score_file(rel, content, keywords, keyword_set)
        if score <= 0:
            continue

        # Build human-readable reason
        matched_in_path = [kw for kw in keyword_set if kw in rel.lower()]
        matched_in_content = [kw for kw in keyword_set if kw in content.lower()]
        reasons: list[str] = []
        if matched_in_path:
            reasons.append(f"path:{','.join(matched_in_path)}")
        if matched_in_content:
            reasons.append(f"content:{','.join(matched_in_content[:5])}")

        results.append({
            "file": rel,
            "score": round(score, 2),
            "tokens": count_tokens(content),
            "reason": " | ".join(reasons),
        })

    results.sort(key=lambda x: -x["score"])
    return results[:top_n]


def focus_context_tokens(ranked: list[dict]) -> int:
    """Total tokens if Claude reads only the ranked files."""
    return sum(r["tokens"] for r in ranked)
