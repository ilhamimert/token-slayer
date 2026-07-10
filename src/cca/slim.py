"""Auto-generate .claudeignore additions from token analysis.

Analyzes which files/directories eat the most context tokens and
suggests (or applies) new .claudeignore patterns to eliminate them.
"""
from __future__ import annotations

from pathlib import Path

from cca.token_counter import (
    _is_binary,
    _is_ignored,
    _parse_claudeignore,
    count_tokens,
    CLAUDEIGNORE_DIRS,
)


def analyze_token_distribution(root: Path) -> list[dict]:
    """Return all readable files with their token counts, sorted descending."""
    results: list[dict] = []
    for f in root.rglob("*"):
        if not f.is_file() or _is_binary(f):
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        if any(part in CLAUDEIGNORE_DIRS for part in f.parts):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            results.append({"file": rel, "tokens": count_tokens(content)})
        except Exception:
            pass
    results.sort(key=lambda x: -x["tokens"])
    return results


def suggest_patterns(
    root: Path,
    budget: int = 20_000,
    max_suggestions: int = 10,
) -> list[dict]:
    """Suggest .claudeignore patterns that bring total tokens under *budget*.

    Returns
    -------
    list of dicts:
        {"pattern": str, "saves_tokens": int, "files_affected": int, "reason": str}
    """
    ci_patterns = _parse_claudeignore(root)
    all_files = analyze_token_distribution(root)
    total = sum(f["tokens"] for f in all_files)

    # Already filtered files (respecting existing .claudeignore)
    filtered_files = [
        f for f in all_files
        if not (ci_patterns and _is_ignored(f["file"], ci_patterns))
    ]

    # Group by top-level directory and file extension
    dir_tokens: dict[str, int] = {}
    dir_files: dict[str, int] = {}
    ext_tokens: dict[str, int] = {}
    ext_files: dict[str, int] = {}

    for entry in filtered_files:
        parts = entry["file"].split("/")
        top = parts[0] if len(parts) > 1 else ""
        if top:
            dir_tokens[top] = dir_tokens.get(top, 0) + entry["tokens"]
            dir_files[top] = dir_files.get(top, 0) + 1

        ext = "." + entry["file"].rsplit(".", 1)[-1] if "." in entry["file"] else ""
        if ext and ext not in {".py", ".ts", ".js", ".go", ".rs", ".md"}:
            ext_tokens[ext] = ext_tokens.get(ext, 0) + entry["tokens"]
            ext_files[ext] = ext_files.get(ext, 0) + 1

    candidates: list[dict] = []

    # High-value directory patterns
    SKIP_DIRS = {"src", "app", "lib", "core", "tests", "test"}
    for d, tok in sorted(dir_tokens.items(), key=lambda x: -x[1]):
        if d.startswith("."):
            continue
        if d in SKIP_DIRS:
            continue
        if tok < 500:
            break
        pattern = f"{d}/"
        if pattern not in ci_patterns:
            reason = _classify_dir(d)
            candidates.append({
                "pattern": pattern,
                "saves_tokens": tok,
                "files_affected": dir_files[d],
                "reason": reason,
            })

    # High-value extension patterns
    NOISY_EXTS = {".lock", ".log", ".json", ".txt", ".csv", ".svg", ".html", ".css"}
    for ext, tok in sorted(ext_tokens.items(), key=lambda x: -x[1]):
        if tok < 300 or ext not in NOISY_EXTS:
            continue
        pattern = f"*{ext}"
        if pattern not in ci_patterns:
            candidates.append({
                "pattern": pattern,
                "saves_tokens": tok,
                "files_affected": ext_files[ext],
                "reason": f"{ext} files rarely useful in context",
            })

    # Individual large files — only non-source files (never suggest excluding src/)
    SOURCE_PREFIXES = ("src/", "lib/", "app/", "core/")
    for entry in filtered_files[:20]:
        if entry["tokens"] > 3_000:
            p = entry["file"]
            if any(p.startswith(pfx) for pfx in SOURCE_PREFIXES):
                continue
            if not any(c["pattern"] in p for c in candidates):
                candidates.append({
                    "pattern": p,
                    "saves_tokens": entry["tokens"],
                    "files_affected": 1,
                    "reason": f"Large file ({entry['tokens']:,} tokens)",
                })

    # Sort by token savings and return top-N
    candidates.sort(key=lambda x: -x["saves_tokens"])
    return candidates[:max_suggestions]


def apply_patterns(root: Path, patterns: list[str]) -> int:
    """Append *patterns* to .claudeignore. Returns number of patterns added."""
    ci_path = root / ".claudeignore"
    existing = set(_parse_claudeignore(root))
    new_patterns = [p for p in patterns if p not in existing]
    if not new_patterns:
        return 0
    with ci_path.open("a", encoding="utf-8") as f:
        f.write("\n# Added by tslayer slim\n")
        for p in new_patterns:
            f.write(p + "\n")
    return len(new_patterns)


def _classify_dir(name: str) -> str:
    n = name.lower()
    if n in {"docs", "doc", "documentation"}:
        return "Documentation rarely needed during coding"
    if n in {"examples", "example", "samples", "demo", "demos"}:
        return "Example files add noise without context value"
    if n in {"migrations", "alembic"}:
        return "DB migration history not needed in context"
    if n in {"static", "assets", "public", "media"}:
        return "Static assets are not source code"
    if n in {"coverage", "htmlcov", "reports"}:
        return "Test reports are generated output"
    if n in {"vendor", "third_party", "third-party"}:
        return "Vendored dependencies should be excluded"
    if n in {"node_modules", "bower_components"}:
        return "JS dependency tree — exclude entirely"
    if n in {"dist", "build", "out", "output"}:
        return "Build output is not source"
    if n in {"logs", "log"}:
        return "Log files are runtime output"
    return f"'{name}/' directory — check if needed in context"
