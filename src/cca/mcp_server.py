"""MCP server that exposes cca tools to Claude directly."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    FastMCP = None  # type: ignore[assignment,misc]


def _require_mcp() -> None:
    if not _MCP_AVAILABLE:
        raise ImportError(
            "mcp package not installed. Run: pip install 'mcp[cli]'"
        )


def create_server() -> "FastMCP":  # type: ignore[return]
    _require_mcp()
    from cca.dead_code import find_unused_exports
    from cca.framework import detect_frameworks
    from cca.graph import build_graph, get_most_imported, find_cycles
    from cca.health import calculate_health
    from cca.parser import analyze_project, filter_source_files
    from cca.token_counter import count_all_tokens, count_project_tokens

    mcp = FastMCP("Token Slayer")  # type: ignore[call-arg]

    @mcp.tool()
    def analyze_project_tool(project_path: str) -> str:
        """Analyze a Python project: file count, lines, functions, complexity, frameworks."""
        root = Path(project_path)
        file_infos = analyze_project(root)
        frameworks = detect_frameworks(file_infos)
        return json.dumps({
            "files": len(file_infos),
            "total_lines": sum(f.lines for f in file_infos),
            "total_functions": sum(f.function_count for f in file_infos),
            "total_classes": sum(f.class_count for f in file_infos),
            "avg_complexity": round(
                sum(f.complexity for f in file_infos) / len(file_infos)
                if file_infos else 0,
                1,
            ),
            "frameworks": list(frameworks.values()),
        }, indent=2)

    @mcp.tool()
    def count_tokens_tool(project_path: str) -> str:
        """Count tokens before and after .claudeignore in a Python project."""
        root = Path(project_path)
        base = count_all_tokens(root)
        opt = count_project_tokens(root)
        b, o = base["total"], opt["total"]
        pct = (b - o) / b * 100 if b else 0.0
        return json.dumps({
            "baseline_tokens": b,
            "optimized_tokens": o,
            "savings_pct": round(pct, 1),
        }, indent=2)

    @mcp.tool()
    def health_score_tool(project_path: str) -> str:
        """Calculate composite health score (0-100) for a Python project."""
        root = Path(project_path)
        file_infos = analyze_project(root)
        graph = build_graph(file_infos, root)
        cycles = find_cycles(graph)
        src_infos = filter_source_files(file_infos)
        unused = find_unused_exports(src_infos, root)
        base = count_all_tokens(root)
        opt = count_project_tokens(root)
        b, o = base["total"], opt["total"]
        pct = (b - o) / b * 100 if b else 0.0
        score = calculate_health(src_infos, pct, unused, cycles)
        return json.dumps(score.to_dict(), indent=2)

    @mcp.tool()
    def find_cycles_tool(project_path: str) -> str:
        """Find circular import dependencies in a Python project."""
        root = Path(project_path)
        file_infos = analyze_project(root)
        graph = build_graph(file_infos, root)
        cycles = find_cycles(graph)
        return json.dumps({
            "cycle_count": len(cycles),
            "cycles": [" -> ".join(c + [c[0]]) for c in cycles[:10]],
        }, indent=2)

    @mcp.tool()
    def syntax_check_tool(project_path: str) -> str:
        """Find Python files with tree-sitter syntax errors before Claude edits them."""
        root = Path(project_path)
        file_infos = analyze_project(root)
        bad = [fi for fi in file_infos if fi.has_syntax_error]
        return json.dumps({
            "error_count": len(bad),
            "files": [str(fi.path.relative_to(root)) for fi in bad],
        }, indent=2)

    @mcp.tool()
    def diff_context_tool(project_path: str, pad: int = 3, staged_only: bool = False) -> str:
        """Get changed files and line ranges from git — read only what changed.

        Use this instead of re-reading whole files after a git pull or during
        an in-progress refactor. Returns {} if not a git repo or no changes.
        """
        from cca.diff_context import get_diff_context
        root = Path(project_path)
        changes = get_diff_context(root, pad=pad, staged_only=staged_only)
        return json.dumps(
            {f: [{"start": s, "end": e} for s, e in ranges] for f, ranges in changes.items()},
            indent=2,
        )

    @mcp.tool()
    def most_imported_tool(project_path: str) -> str:
        """Return the most-imported files in a Python project (high-impact files)."""
        root = Path(project_path)
        file_infos = analyze_project(root)
        graph = build_graph(file_infos, root)
        most_imported = get_most_imported(graph, n=10)
        return json.dumps(
            [{"file": p, "imported_by": c} for p, c in most_imported if c > 0],
            indent=2,
        )

    @mcp.tool()
    def snapshot_tool(project_path: str) -> str:
        """Get a compressed overview of the project (file tree + signatures).

        Call this FIRST at the start of every session before reading any files.
        Returns CONTEXT.md content if it exists, otherwise generates it on the fly.
        Typically reduces context by 80-90% vs reading all source files.
        """
        root = Path(project_path)
        context_md = root / "CONTEXT.md"
        if context_md.exists():
            return context_md.read_text(encoding="utf-8")
        from cca.snapshot import build_snapshot
        return build_snapshot(root)

    @mcp.tool()
    def focus_tool(project_path: str, query: str, top_n: int = 8, with_deps: bool = False) -> str:
        """Find the files most relevant to a task — read only these files.

        Use this BEFORE reading any source files. Describe what you're working
        on in `query` and only read the files this tool returns.

        Args:
            project_path: Root directory of the project.
            query: Task description, e.g. 'fix health score calculation'.
            top_n: Number of files to return (default 8).
            with_deps: also include each file's direct import neighbors
                (callers/callees) under a "related" key.
        """
        from cca.focus import rank_files, rank_files_with_context
        root = Path(project_path)
        ranked = (
            rank_files_with_context(root, query, top_n=top_n)
            if with_deps
            else rank_files(root, query, top_n=top_n)
        )
        return json.dumps(ranked, indent=2)

    @mcp.tool()
    def decisions_tool(project_path: str) -> str:
        """Read all recorded architectural decisions for the project.

        Call this after snapshot_tool to understand WHY the code is structured
        the way it is — before making any structural changes.
        Returns empty string if no decisions have been recorded yet.
        """
        decisions_path = Path(project_path) / "DECISIONS.md"
        if not decisions_path.exists():
            return "(No decisions recorded yet. Use `tslayer decision` to record them.)"
        return decisions_path.read_text(encoding="utf-8")

    @mcp.tool()
    def generate_config_tool(project_path: str) -> str:
        """Generate an optimised CLAUDE.md for a Python project."""
        from cca.config_gen import generate_claude_md
        from cca.dead_code import find_unused_exports
        from cca.framework import detect_frameworks
        from cca.graph import build_graph, get_most_imported
        from cca.token_counter import count_all_tokens, count_project_tokens
        root = Path(project_path)
        file_infos = analyze_project(root)
        graph = build_graph(file_infos, root)
        most_imported = get_most_imported(graph, n=10)
        unused = find_unused_exports(file_infos, root)
        base = count_all_tokens(root)
        opt = count_project_tokens(root)
        b, o = base["total"], opt["total"]
        pct = (b - o) / b * 100 if b else 0.0
        frameworks = detect_frameworks(file_infos)
        content = generate_claude_md(
            root=root,
            file_infos=file_infos,
            most_imported=most_imported,
            hot_files={},
            unused_exports=unused,
            token_savings_pct=pct,
            frameworks=frameworks,
        )
        out = root / "CLAUDE.md"
        out.write_text(content, encoding="utf-8")
        return json.dumps({"written": str(out), "token_savings_pct": round(pct, 1)}, indent=2)

    return mcp


def run_server() -> None:
    """Entry point: start the MCP stdio server."""
    server = create_server()
    server.run()
