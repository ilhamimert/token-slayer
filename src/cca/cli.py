from __future__ import annotations

import datetime
import json
import stat
from pathlib import Path

import typer
from rich.console import Console

from cca import __version__
from cca import reporter
from cca.config_gen import generate_claude_md, CLAUDEIGNORE_PATTERNS
from cca.dead_code import find_unused_exports
from cca.framework import detect_frameworks
from cca.git_analyzer import get_hot_files, is_git_repo
from cca.graph import build_graph, get_in_degrees, get_most_imported, find_cycles
from cca.health import calculate_health
from cca.lang import analyze_extra_files, detect_extra_languages
from cca.parser import analyze_project
from cca.token_counter import count_all_tokens, count_project_tokens

app = typer.Typer(
    name="tslayer",
    help="Token Slayer -- slash your Claude token usage. Analyze projects, generate CLAUDE.md.",
    no_args_is_help=True,
)
console = Console()


def _require_dir(path: Path) -> Path:
    path = path.resolve()
    if not path.exists():
        console.print(f"[red]Error:[/red] '{path}' does not exist.")
        raise typer.Exit(1)
    if not path.is_dir():
        console.print(f"[red]Error:[/red] '{path}' is not a directory.")
        raise typer.Exit(1)
    return path


@app.command("analyze")
def analyze(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    dead_code: bool = typer.Option(False, "--dead-code", "-d", help="Show potential dead code"),
    tokens: bool = typer.Option(False, "--tokens", "-t", help="Show token budget estimate"),
    chart: bool = typer.Option(False, "--chart", "-c", help="Show visual token distribution chart"),
    quality: bool = typer.Option(False, "--quality", "-q", help="Show code quality metrics (complexity, type coverage)"),
    cycles: bool = typer.Option(False, "--cycles", help="Show circular dependency check"),
    multilang: bool = typer.Option(False, "--multilang", "-m", help="Include TypeScript/Go files"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Analyze a Python project: file stats, dependencies, hot zones."""
    path = _require_dir(path)
    if not output_json:
        console.print(f"\n[bold green]Analyzing:[/bold green] {path.resolve()}\n")

    with console.status("[cyan]Parsing Python files...[/cyan]"):
        file_infos = analyze_project(path)

    extra_langs: set[str] = set()
    if multilang:
        extra_langs = detect_extra_languages(path)
        if extra_langs:
            with console.status(f"[cyan]Parsing {', '.join(extra_langs)} files...[/cyan]"):
                file_infos += analyze_extra_files(path)

    if not file_infos:
        if output_json:
            typer.echo(json.dumps({"error": "No source files found"}))
        else:
            console.print("[yellow]No source files found.[/yellow]")
        raise typer.Exit(0)

    with console.status("[cyan]Building dependency graph...[/cyan]"):
        graph = build_graph(file_infos, path)
        in_degrees = get_in_degrees(graph)
        most_imported = get_most_imported(graph, n=10)

    hot_files: dict[str, int] = {}
    if is_git_repo(path):
        with console.status("[cyan]Scanning git history...[/cyan]"):
            hot_files = get_hot_files(path)

    frameworks = detect_frameworks(file_infos)

    if output_json:
        result: dict = {
            "project": str(path.resolve()),
            "languages": ["python"] + sorted(extra_langs),
            "files": len(file_infos),
            "total_lines": sum(f.lines for f in file_infos),
            "total_functions": sum(f.function_count for f in file_infos),
            "total_classes": sum(f.class_count for f in file_infos),
            "frameworks": list(frameworks.values()),
            "most_imported": [{"file": p, "count": c} for p, c in most_imported if c > 0],
            "hot_files": hot_files,
        }
        if tokens:
            with console.status("[cyan]Counting tokens...[/cyan]"):
                base_data = count_all_tokens(path)
                opt_data = count_project_tokens(path)
                b, o = base_data["total"], opt_data["total"]
                pct = (b - o) / b * 100 if b else 0.0
            result["tokens"] = {"baseline": b, "optimized": o, "savings_pct": round(pct, 1)}
        if cycles:
            cycle_list = find_cycles(graph)
            result["cycles"] = [" -> ".join(c + [c[0]]) for c in cycle_list]
        if quality:
            result["quality"] = {
                "avg_complexity": round(
                    sum(f.complexity for f in file_infos) / len(file_infos), 1
                ) if file_infos else 0,
                "type_coverage_pct": round(
                    sum(f.typed_functions for f in file_infos) /
                    max(sum(f.function_count for f in file_infos), 1) * 100, 1
                ),
            }
        typer.echo(json.dumps(result, indent=2))
        return

    if frameworks:
        reporter.print_frameworks(frameworks)

    if extra_langs:
        console.print(f"[dim]  Extra languages detected: {', '.join(sorted(extra_langs))}[/dim]\n")

    reporter.print_analysis_table(file_infos, path, in_degrees, hot_files)
    reporter.print_dependency_summary(most_imported)

    if hot_files:
        console.print("[dim]  HOT = changed in recent git history[/dim]\n")

    if quality:
        reporter.print_quality_table(file_infos, path)

    if cycles:
        with console.status("[cyan]Checking for circular dependencies...[/cyan]"):
            cycle_list = find_cycles(graph)
        reporter.print_cycles_warning(cycle_list)

    if dead_code:
        with console.status("[cyan]Detecting dead code...[/cyan]"):
            unused = find_unused_exports(file_infos, path)
        reporter.print_unused(unused)

    if tokens or chart:
        with console.status("[cyan]Counting tokens...[/cyan]"):
            base_data = count_all_tokens(path)
            opt_data = count_project_tokens(path)
            b, o = base_data["total"], opt_data["total"]
            pct = (b - o) / b * 100 if b else 0.0

        if tokens:
            reporter.print_token_report(b, o, pct)

        if chart:
            reporter.print_token_comparison_chart(base_data["files"], opt_data["files"])


@app.command("score")
def score(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Calculate a composite health score (0-100) for the project."""
    path = _require_dir(path)
    if not output_json:
        console.print(f"\n[bold green]Scoring:[/bold green] {path.resolve()}\n")

    with console.status("[cyan]Analyzing project...[/cyan]"):
        file_infos = analyze_project(path)
        graph = build_graph(file_infos, path)
        cycle_list = find_cycles(graph)

    # Exclude test files from type-coverage and dead-code metrics:
    # test methods never have return annotations (that's normal), and pytest
    # discovers tests dynamically rather than via import.
    _TEST_DIRS = {"tests", "test", "test-project"}
    src_infos = [
        fi for fi in file_infos
        if not any(part in _TEST_DIRS for part in fi.path.parts)
    ]

    with console.status("[cyan]Detecting dead code...[/cyan]"):
        unused = find_unused_exports(src_infos, path)

    with console.status("[cyan]Counting tokens...[/cyan]"):
        base_data = count_all_tokens(path)
        opt_data = count_project_tokens(path)
        b, o = base_data["total"], opt_data["total"]
        pct = (b - o) / b * 100 if b else 0.0

    health = calculate_health(src_infos, pct, unused, cycle_list)

    if output_json:
        typer.echo(json.dumps(health.to_dict(), indent=2))
        return

    reporter.print_health_score(health)


@app.command("generate-config")
def generate_config(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    output: Path = typer.Option(None, "--output", "-o", help="Output path (default: <path>/CLAUDE.md)"),
    chart: bool = typer.Option(False, "--chart", "-c", help="Show visual token distribution chart"),
):
    """Generate an optimized CLAUDE.md for the project."""
    path = _require_dir(path)
    console.print(f"\n[bold green]Generating CLAUDE.md for:[/bold green] {path.resolve()}\n")

    with console.status("[cyan]Analyzing project...[/cyan]"):
        file_infos = analyze_project(path)
        graph = build_graph(file_infos, path)
        most_imported = get_most_imported(graph, n=10)

    hot_files: dict[str, int] = {}
    if is_git_repo(path):
        with console.status("[cyan]Scanning git history...[/cyan]"):
            hot_files = get_hot_files(path)

    with console.status("[cyan]Detecting dead code...[/cyan]"):
        unused = find_unused_exports(file_infos, path)

    with console.status("[cyan]Counting tokens...[/cyan]"):
        base_data = count_all_tokens(path)
        opt_data = count_project_tokens(path)
        b, o = base_data["total"], opt_data["total"]
        pct = (b - o) / b * 100 if b else 0.0

    frameworks = detect_frameworks(file_infos)

    content = generate_claude_md(
        root=path,
        file_infos=file_infos,
        most_imported=most_imported,
        hot_files=hot_files,
        unused_exports=unused,
        token_savings_pct=pct,
        frameworks=frameworks,
    )

    out = output or (path / "CLAUDE.md")
    out.write_text(content, encoding="utf-8")
    console.print(f"[bold green]v[/bold green] Written: {out}")
    reporter.print_token_report(b, o, pct)

    if frameworks:
        reporter.print_frameworks(frameworks)

    if chart:
        reporter.print_token_comparison_chart(base_data["files"], opt_data["files"])


@app.command("tokens")
def tokens_cmd(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    chart: bool = typer.Option(True, "--chart/--no-chart", help="Show bar chart (default: on)"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Show detailed token budget and visual distribution chart."""
    path = _require_dir(path)
    if not output_json:
        console.print(f"\n[bold green]Token analysis:[/bold green] {path.resolve()}\n")

    with console.status("[cyan]Counting tokens...[/cyan]"):
        base_data = count_all_tokens(path)
        opt_data = count_project_tokens(path)
        b, o = base_data["total"], opt_data["total"]
        pct = (b - o) / b * 100 if b else 0.0

    if output_json:
        typer.echo(json.dumps({
            "baseline_tokens": b,
            "optimized_tokens": o,
            "savings_pct": round(pct, 1),
            "files": {
                "baseline": base_data["files"],
                "optimized": opt_data["files"],
            },
        }, indent=2))
        return

    reporter.print_token_report(b, o, pct)

    if chart:
        reporter.print_token_comparison_chart(base_data["files"], opt_data["files"])


@app.command("audit")
def audit(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Check if CLAUDE.md exists and is up to date with the project."""
    path = _require_dir(path)
    if not output_json:
        console.print(f"\n[bold green]Auditing:[/bold green] {path.resolve()}\n")

    claude_md = path / "CLAUDE.md"
    issues: list[str] = []
    ok: list[str] = []

    if not claude_md.exists():
        issues.append("CLAUDE.md bulunamadi -- run: tslayer generate-config <path>")
    else:
        ok.append("CLAUDE.md mevcut")

        md_mtime = claude_md.stat().st_mtime
        newest_py = max(
            (f.stat().st_mtime for f in path.rglob("*.py")
             if not any(p in {"venv", ".venv", "__pycache__"} for p in f.parts)),
            default=0,
        )
        if newest_py > md_mtime:
            delta = (
                datetime.datetime.fromtimestamp(newest_py)
                - datetime.datetime.fromtimestamp(md_mtime)
            )
            issues.append(
                f"CLAUDE.md eskidi -- "
                f"en son .py degisikliginden {int(delta.total_seconds() // 60)} dakika once uretilmis"
            )
        else:
            ok.append("CLAUDE.md guncel")

        with console.status("[cyan]Counting tokens...[/cyan]"):
            base_data = count_all_tokens(path)
            opt_data = count_project_tokens(path)
            b, o = base_data["total"], opt_data["total"]
            pct = (b - o) / b * 100 if b else 0.0

        ok.append(f"Token tasarrufu: {pct:.1f}%  ({b:,} -> {o:,})")

        if o > 100_000:
            issues.append(f"Optimize edilmis proje cok buyuk: {o:,} token (>100k)")
        elif o > 60_000:
            issues.append(f"Buyuk proje: {o:,} token -- .claudeignore genisletmeyi dusunun")

    with console.status("[cyan]Checking circular dependencies...[/cyan]"):
        file_infos = analyze_project(path)
        graph = build_graph(file_infos, path)
        cycle_list = find_cycles(graph)

    if cycle_list:
        issues.append(f"{len(cycle_list)} circular dependency bulundu -- cca analyze --cycles ile detay")
    else:
        ok.append("Circular dependency yok")

    if output_json:
        typer.echo(json.dumps({
            "ok": ok,
            "issues": issues,
            "passed": len(issues) == 0,
        }, indent=2))
        raise typer.Exit(0 if not issues else 1)

    for item in ok:
        console.print(f"  [bold]OK[/bold]   [green]{item}[/green]")
    for item in issues:
        console.print(f"  [bold]!!![/bold]  [red]{item}[/red]")

    console.print()
    if issues:
        console.print(f"[bold red]{len(issues)} sorun bulundu.[/bold red]")
        raise typer.Exit(1)
    else:
        console.print("[bold green]Her sey yolunda.[/bold green]")


@app.command("init-hooks")
def init_hooks(
    path: Path = typer.Argument(..., help="Path to the git project root"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing hook"),
):
    """Install a git pre-commit hook that runs tslayer audit before every commit."""
    path = _require_dir(path)
    git_dir = path / ".git"
    if not git_dir.is_dir():
        console.print(f"[red]No .git directory in {path}[/red]")
        console.print("[dim]Run git init first.[/dim]")
        raise typer.Exit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"

    if hook_path.exists() and not force:
        console.print(
            f"[yellow]Pre-commit hook already exists:[/yellow] {hook_path}\n"
            "[dim]Use --force to overwrite.[/dim]"
        )
        raise typer.Exit(1)

    project_abs = path.resolve().as_posix()
    hook_content = (
        "#!/bin/sh\n"
        "# Generated by cca (claude-context-analyzer)\n"
        f'echo "[tslayer] Auditing project..."\n'
        f'tslayer audit "{project_abs}"\n'
        "STATUS=$?\n"
        "if [ $STATUS -ne 0 ]; then\n"
        f'  echo "[cca] Fix issues or regenerate: tslayer generate-config \\"{project_abs}\\""\n'
        "  exit 1\n"
        "fi\n"
    )
    hook_path.write_text(hook_content, encoding="utf-8")
    current = hook_path.stat().st_mode
    hook_path.chmod(current | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    console.print(f"[bold green]v[/bold green] Pre-commit hook installed: {hook_path}")
    console.print(f"[dim]Every commit will now run: tslayer audit {project_abs}[/dim]")


@app.command("sessions")
def sessions_cmd(
    watch: bool = typer.Option(False, "--watch", "-w", help="Live-refresh every 2s (Ctrl+C to stop)"),
    hours: int = typer.Option(24, "--hours", "-h", help="Show sessions active in the last N hours"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max sessions to show"),
    session_a: str = typer.Option(None, "--a", help="Session ID prefix to pin as slot A"),
    session_b: str = typer.Option(None, "--b", help="Session ID prefix to pin as slot B"),
):
    """Show real-time token usage for active Claude Code sessions.

    Two sessions are shown side-by-side with a live diff when exactly 2 are active.
    Use --a / --b to pin specific session IDs for comparison.
    """
    from cca.session_monitor import get_recent_sessions, run_live, _build_renderable

    all_sessions = get_recent_sessions(hours=hours)

    if not all_sessions:
        console.print(f"[yellow]No active Claude Code sessions in the last {hours}h.[/yellow]")
        raise typer.Exit(0)

    if session_a and session_b:
        def _find(prefix: str) -> dict | None:
            for s in all_sessions:
                if s["short_id"].startswith(prefix) or s["session_id"].startswith(prefix):
                    return s
            return None
        sa, sb = _find(session_a), _find(session_b)
        if not sa:
            console.print(f"[red]Session not found:[/red] {session_a}")
            raise typer.Exit(1)
        if not sb:
            console.print(f"[red]Session not found:[/red] {session_b}")
            raise typer.Exit(1)
        sessions = [sa, sb]
    else:
        sessions = all_sessions[:limit]

    if watch:
        console.print(
            f"[bold green]Watching {len(sessions)} session(s)[/bold green] "
            "[dim](Ctrl+C to stop)[/dim]\n"
        )
        try:
            run_live(sessions, hours=hours)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")
    else:
        from rich.console import Group
        console.print(_build_renderable(sessions))


@app.command("mcp")
def mcp_cmd():
    """Start the MCP stdio server so Claude can call cca tools directly."""
    try:
        from cca.mcp_server import run_server
        console.print(
            "[bold green]Starting MCP server...[/bold green]\n"
            "[dim]Add to Claude Desktop config:\n"
            '  "tslayer": {"command": "tslayer", "args": ["mcp"]}[/dim]\n'
        )
        run_server()
    except ImportError as e:
        console.print(f"[red]MCP not available:[/red] {e}")
        console.print("[dim]Install: pip install 'mcp[cli]'[/dim]")
        raise typer.Exit(1)


@app.command("snapshot")
def snapshot_cmd(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    output: Path = typer.Option(None, "--output", "-o", help="Output path (default: <path>/CONTEXT.md)"),
    show_stats: bool = typer.Option(True, "--stats/--no-stats", help="Show token reduction stats"),
):
    """Generate CONTEXT.md — a compressed project snapshot for Claude.

    Replaces loading 50K tokens of source files with a single ~3K token
    file containing: file tree + function/class signatures + config files.

    Add 'CONTEXT.md' to .claudeignore of other projects that import this one.
    Tell Claude: 'Read CONTEXT.md first, then ask me which files you need.'
    """
    path = _require_dir(path)
    console.print(f"\n[bold green]Generating snapshot for:[/bold green] {path.resolve()}\n")

    from cca.snapshot import build_snapshot, snapshot_token_stats

    with console.status("[cyan]Building snapshot...[/cyan]"):
        text = build_snapshot(path)

    out = output or (path / "CONTEXT.md")
    out.write_text(text, encoding="utf-8")
    console.print(f"[bold green]v[/bold green] Written: {out}")

    # Auto-add CONTEXT.md to .claudeignore so it doesn't bloat the project context
    ci_path = path / ".claudeignore"
    existing = ci_path.read_text(encoding="utf-8") if ci_path.exists() else ""
    if "CONTEXT.md" not in existing:
        with ci_path.open("a", encoding="utf-8") as f:
            f.write("\n# Generated snapshot — Claude reads this directly, exclude from token count\nCONTEXT.md\n")
        console.print("[dim]  Added CONTEXT.md to .claudeignore[/dim]")

    if show_stats:
        with console.status("[cyan]Counting tokens...[/cyan]"):
            stats = snapshot_token_stats(path, text)
        w = stats["with_claudeignore_tokens"]
        s = stats["snapshot_tokens"]
        r = stats["reduction_pct"]
        console.print(f"\n  Project (.claudeignore) : [yellow]{w:>8,}[/yellow] tokens")
        console.print(f"  Snapshot (CONTEXT.md)   : [green]{s:>8,}[/green] tokens")
        console.print(f"\n  [bold green]Context reduction : {r:.1f}%[/bold green]")
    console.print(
        "\n[dim]Tip: Tell Claude 'Read CONTEXT.md first, "
        "then ask me which files you need.'[/dim]"
    )


@app.command("focus")
def focus_cmd(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    query: str = typer.Argument(..., help="Task description, e.g. 'add Redis caching'"),
    top: int = typer.Option(8, "--top", "-n", help="Number of files to return"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Find the files most relevant to a task — minimize what Claude reads.

    Instead of loading the whole project, Claude reads only the files
    this command identifies. Typically reduces context by 80-95%.

    Example:
      tslayer focus . "add Redis caching to the proxy"
      tslayer focus . "fix the token counter bug"
    """
    path = _require_dir(path)

    from cca.focus import rank_files, focus_context_tokens

    with console.status(f"[cyan]Ranking files for: {query!r}[/cyan]"):
        ranked = rank_files(path, query, top_n=top)

    if output_json:
        import json as _json
        typer.echo(_json.dumps(ranked, indent=2))
        return

    if not ranked:
        console.print("[yellow]No relevant files found for that query.[/yellow]")
        raise typer.Exit(0)

    console.print(f"\n[bold green]Top {len(ranked)} files for:[/bold green] [italic]{query}[/italic]\n")

    from rich.table import Table
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("#", style="dim", width=3)
    table.add_column("File", style="cyan", no_wrap=False)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Tokens", justify="right", width=7)
    table.add_column("Matched on", style="dim")

    for i, r in enumerate(ranked, 1):
        table.add_row(
            str(i),
            r["file"],
            str(r["score"]),
            f"{r['tokens']:,}",
            r["reason"],
        )

    console.print(table)

    total = focus_context_tokens(ranked)
    with console.status("[cyan]Counting full project tokens...[/cyan]"):
        full = count_project_tokens(path)["total"]

    saved = full - total
    pct = saved / full * 100 if full else 0.0
    console.print(
        f"\n  Focused context : [green]{total:>8,}[/green] tokens  ({len(ranked)} files)\n"
        f"  Full project    : [red]{full:>8,}[/red] tokens\n"
        f"  [bold green]Context reduction: {pct:.1f}% ({saved:,} tokens saved)[/bold green]\n"
    )


@app.command("slim")
def slim_cmd(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    budget: int = typer.Option(20_000, "--budget", "-b", help="Target token budget"),
    apply: bool = typer.Option(False, "--apply", help="Write suggestions to .claudeignore"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Find what to add to .claudeignore to stay under a token budget.

    Analyzes your project's token distribution and suggests patterns
    that give the most savings per line added to .claudeignore.

    Use --apply to automatically write the suggestions.
    """
    path = _require_dir(path)

    from cca.slim import suggest_patterns, apply_patterns, analyze_token_distribution
    from cca.token_counter import count_project_tokens

    with console.status("[cyan]Analyzing token distribution...[/cyan]"):
        suggestions = suggest_patterns(path, budget=budget)
        current = count_project_tokens(path)["total"]

    if output_json:
        import json as _json
        typer.echo(_json.dumps({
            "current_tokens": current,
            "budget": budget,
            "over_budget": current > budget,
            "suggestions": suggestions,
        }, indent=2))
        return

    console.print(
        f"\n[bold green]Slim analysis for:[/bold green] {path.resolve()}\n"
        f"  Current context : [{'red' if current > budget else 'green'}]{current:,}[/] tokens\n"
        f"  Budget target   : {budget:,} tokens\n"
    )

    if not suggestions:
        console.print("[green]No significant savings found — your .claudeignore looks good.[/green]")
        raise typer.Exit(0)

    from rich.table import Table
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("Pattern", style="cyan")
    table.add_column("Saves", justify="right", width=9)
    table.add_column("Files", justify="right", width=6)
    table.add_column("Reason", style="dim")

    cumulative = 0
    for s in suggestions:
        cumulative += s["saves_tokens"]
        table.add_row(
            s["pattern"],
            f"{s['saves_tokens']:,}",
            str(s["files_affected"]),
            s["reason"],
        )

    console.print(table)

    potential = min(cumulative, current)
    after = current - potential
    console.print(
        f"\n  After applying all : ~[green]{after:,}[/green] tokens "
        f"([bold green]{potential / current * 100:.1f}% reduction[/bold green])\n"
    )

    if apply:
        patterns = [s["pattern"] for s in suggestions]
        added = apply_patterns(path, patterns)
        if added:
            console.print(f"[bold green]v[/bold green] Added {added} patterns to .claudeignore")
        else:
            console.print("[dim]All patterns already in .claudeignore[/dim]")
    else:
        console.print("[dim]Run with --apply to write these to .claudeignore[/dim]")


@app.command("version")
def version_cmd():
    """Show version."""
    console.print(f"cca v{__version__}")


if __name__ == "__main__":
    app()
