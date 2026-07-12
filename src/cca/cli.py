from __future__ import annotations

import datetime
import json
import shutil
import stat
import sys
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
from cca.parser import analyze_project, filter_source_files
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


def _resolve_tslayer_command() -> str:
    """Find how to invoke `tslayer` from any shell — prefer PATH lookup
    (portable across machines/projects when installed via pipx), fall back
    to this running process's own executable path."""
    on_path = shutil.which("tslayer")
    return on_path or sys.argv[0]


@app.command("init")
def init_cmd(
    path: Path = typer.Argument(Path("."), help="Project directory to activate Token Slayer in"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing .mcp.json"),
):
    """One-command setup: register Token Slayer as an MCP server for this project.

    Writes .mcp.json so Claude Code auto-loads Token Slayer's tools
    (snapshot, focus, audit, diff-context, etc.) the next time this
    project opens. Run this once per project, then reload the Claude
    Code window.

    Example:
      cd my-project
      tslayer init
    """
    path = _require_dir(path)
    mcp_json_path = path / ".mcp.json"
    if mcp_json_path.exists() and not force:
        console.print(
            f"[yellow].mcp.json already exists:[/yellow] {mcp_json_path}\n"
            "[dim]Use --force to overwrite.[/dim]"
        )
        raise typer.Exit(1)

    tslayer_command = _resolve_tslayer_command()
    config = {
        "mcpServers": {
            "tslayer": {
                "command": tslayer_command,
                "args": ["mcp"],
                "type": "stdio",
            }
        }
    }
    mcp_json_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    console.print(f"[bold green]v[/bold green] .mcp.json written: {mcp_json_path}")
    console.print(f"  [dim]command: {tslayer_command}[/dim]")
    console.print(
        "\n[bold cyan]Next step:[/bold cyan] reload the Claude Code window "
        "(Ctrl+Shift+P -> Developer: Reload Window) — Token Slayer's tools "
        "will then load automatically."
    )


def _analyze_json_result(
    path: Path,
    file_infos: list,
    extra_langs: set[str],
    frameworks: dict,
    most_imported: list,
    hot_files: dict[str, int],
    graph,
    *,
    tokens: bool,
    cycles: bool,
    quality: bool,
) -> dict:
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
        result["tokens"] = _analyze_token_summary(path)
    if cycles:
        cycle_list = find_cycles(graph)
        result["cycles"] = [" -> ".join(c + [c[0]]) for c in cycle_list]
    if quality:
        result["quality"] = _analyze_quality_summary(file_infos)
    return result


def _analyze_token_summary(path: Path) -> dict:
    with console.status("[cyan]Counting tokens...[/cyan]"):
        base_data = count_all_tokens(path)
        opt_data = count_project_tokens(path)
        b, o = base_data["total"], opt_data["total"]
        pct = (b - o) / b * 100 if b else 0.0
    return {"baseline": b, "optimized": o, "savings_pct": round(pct, 1)}


def _analyze_quality_summary(file_infos: list) -> dict:
    return {
        "avg_complexity": round(
            sum(f.complexity for f in file_infos) / len(file_infos), 1
        ) if file_infos else 0,
        "type_coverage_pct": round(
            sum(f.typed_functions for f in file_infos) /
            max(sum(f.function_count for f in file_infos), 1) * 100, 1
        ),
    }


def _print_analyze_console(
    path: Path,
    file_infos: list,
    in_degrees: dict,
    hot_files: dict[str, int],
    most_imported: list,
    frameworks: dict,
    extra_langs: set[str],
    graph,
    *,
    quality: bool,
    cycles: bool,
    dead_code: bool,
    tokens: bool,
    chart: bool,
) -> None:
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
        _print_cycles(path, graph)

    if dead_code:
        _print_dead_code(path, file_infos)

    if tokens or chart:
        _print_token_section(path, tokens=tokens, chart=chart)


def _print_cycles(path: Path, graph) -> None:
    with console.status("[cyan]Checking for circular dependencies...[/cyan]"):
        cycle_list = find_cycles(graph)
    reporter.print_cycles_warning(cycle_list)


def _print_dead_code(path: Path, file_infos: list) -> None:
    with console.status("[cyan]Detecting dead code...[/cyan]"):
        unused = find_unused_exports(file_infos, path)
    reporter.print_unused(unused)


def _print_token_section(path: Path, *, tokens: bool, chart: bool) -> None:
    with console.status("[cyan]Counting tokens...[/cyan]"):
        base_data = count_all_tokens(path)
        opt_data = count_project_tokens(path)
        b, o = base_data["total"], opt_data["total"]
        pct = (b - o) / b * 100 if b else 0.0

    if tokens:
        reporter.print_token_report(b, o, pct)

    if chart:
        reporter.print_token_comparison_chart(base_data["files"], opt_data["files"])


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
        result = _analyze_json_result(
            path, file_infos, extra_langs, frameworks, most_imported, hot_files, graph,
            tokens=tokens, cycles=cycles, quality=quality,
        )
        typer.echo(json.dumps(result, indent=2))
        return

    _print_analyze_console(
        path, file_infos, in_degrees, hot_files, most_imported, frameworks, extra_langs, graph,
        quality=quality, cycles=cycles, dead_code=dead_code, tokens=tokens, chart=chart,
    )


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

    src_infos = filter_source_files(file_infos)

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


def _audit_claude_md_freshness(claude_md: Path, path: Path) -> str | None:
    md_mtime = claude_md.stat().st_mtime
    newest_py = max(
        (f.stat().st_mtime for f in path.rglob("*.py")
         if not any(p in {"venv", ".venv", "__pycache__"} for p in f.parts)),
        default=0,
    )
    if newest_py <= md_mtime:
        return None
    delta = (
        datetime.datetime.fromtimestamp(newest_py)
        - datetime.datetime.fromtimestamp(md_mtime)
    )
    return (
        f"CLAUDE.md eskidi -- "
        f"en son .py degisikliginden {int(delta.total_seconds() // 60)} dakika once uretilmis"
    )


def _audit_token_budget(path: Path) -> tuple[str, str | None]:
    with console.status("[cyan]Counting tokens...[/cyan]"):
        base_data = count_all_tokens(path)
        opt_data = count_project_tokens(path)
        b, o = base_data["total"], opt_data["total"]
        pct = (b - o) / b * 100 if b else 0.0

    ok_message = f"Token tasarrufu: {pct:.1f}%  ({b:,} -> {o:,})"
    if o > 100_000:
        return ok_message, f"Optimize edilmis proje cok buyuk: {o:,} token (>100k)"
    if o > 60_000:
        return ok_message, f"Buyuk proje: {o:,} token -- .claudeignore genisletmeyi dusunun"
    return ok_message, None


def _audit_claude_md_section(claude_md: Path, path: Path) -> tuple[list[str], list[str]]:
    ok: list[str] = ["CLAUDE.md mevcut"]
    issues: list[str] = []

    freshness_issue = _audit_claude_md_freshness(claude_md, path)
    if freshness_issue:
        issues.append(freshness_issue)
    else:
        ok.append("CLAUDE.md guncel")

    token_ok, token_issue = _audit_token_budget(path)
    ok.append(token_ok)
    if token_issue:
        issues.append(token_issue)

    return ok, issues


def _audit_cycles(path: Path) -> str | None:
    with console.status("[cyan]Checking circular dependencies...[/cyan]"):
        file_infos = analyze_project(path)
        graph = build_graph(file_infos, path)
        cycle_list = find_cycles(graph)
    if not cycle_list:
        return None
    return f"{len(cycle_list)} circular dependency bulundu -- cca analyze --cycles ile detay"


def _audit_syntax_errors(path: Path) -> str | None:
    with console.status("[cyan]Checking syntax errors...[/cyan]"):
        file_infos = analyze_project(path)
    bad = [fi for fi in file_infos if fi.has_syntax_error]
    if not bad:
        return None
    names = ", ".join(str(fi.path.relative_to(path)) for fi in bad[:5])
    more = f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""
    return f"{len(bad)} dosyada sozdizimi hatasi bulundu: {names}{more}"


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
    if not claude_md.exists():
        ok: list[str] = []
        issues: list[str] = ["CLAUDE.md bulunamadi -- run: tslayer generate-config <path>"]
    else:
        ok, issues = _audit_claude_md_section(claude_md, path)

    cycle_issue = _audit_cycles(path)
    if cycle_issue:
        issues.append(cycle_issue)
    else:
        ok.append("Circular dependency yok")

    syntax_issue = _audit_syntax_errors(path)
    if syntax_issue:
        issues.append(syntax_issue)
    else:
        ok.append("Sozdizimi hatasi yok")

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
    with_deps: bool = typer.Option(False, "--with-deps", help="Include direct import neighbors for each ranked file"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Find the files most relevant to a task — minimize what Claude reads.

    Instead of loading the whole project, Claude reads only the files
    this command identifies. Typically reduces context by 80-95%.

    Example:
      tslayer focus . "add Redis caching to the proxy"
      tslayer focus . "fix the token counter bug"
      tslayer focus . "fix the token counter bug" --with-deps
    """
    path = _require_dir(path)

    from cca.focus import rank_files, rank_files_with_context, focus_context_tokens

    with console.status(f"[cyan]Ranking files for: {query!r}[/cyan]"):
        ranked = (
            rank_files_with_context(path, query, top_n=top)
            if with_deps
            else rank_files(path, query, top_n=top)
        )

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
    if with_deps:
        table.add_column("Related", style="dim")

    for i, r in enumerate(ranked, 1):
        row = [
            str(i),
            r["file"],
            str(r["score"]),
            f"{r['tokens']:,}",
            r["reason"],
        ]
        if with_deps:
            related = r.get("related", [])
            imports = sum(1 for x in related if x["relation"] == "imports")
            imported_by = sum(1 for x in related if x["relation"] == "imported_by")
            row.append(f"{imports} imports, {imported_by} imported_by")
        table.add_row(*row)

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


@app.command("diff-context")
def diff_context_cmd(
    path: Path = typer.Argument(..., help="Path to the git project"),
    pad: int = typer.Option(3, "--pad", "-p", help="Extra lines of context around each change"),
    staged: bool = typer.Option(False, "--staged", help="Only diff staged changes vs HEAD"),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Show changed files and line ranges from git — read only what changed.

    Instead of loading entire files after a git pull or mid-refactor, Claude
    reads only the changed line ranges (+padding) this command reports.

    Example:
      tslayer diff-context .
      tslayer diff-context . --staged --pad 5
    """
    path = _require_dir(path)
    if not is_git_repo(path):
        console.print("[yellow]Not a git repository.[/yellow]")
        raise typer.Exit(0)

    from cca.diff_context import get_diff_context

    with console.status("[cyan]Reading git diff...[/cyan]"):
        changes = get_diff_context(path, pad=pad, staged_only=staged)

    if output_json:
        typer.echo(json.dumps(
            {f: [{"start": s, "end": e} for s, e in ranges] for f, ranges in changes.items()},
            indent=2,
        ))
        return

    if not changes:
        console.print("[green]No changes detected.[/green]")
        raise typer.Exit(0)

    console.print(f"\n[bold green]Changed files in:[/bold green] {path.resolve()}\n")
    from rich.table import Table
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("File", style="cyan")
    table.add_column("Line ranges", style="yellow")
    for f, ranges in changes.items():
        table.add_row(f, ", ".join(f"{s}-{e}" for s, e in ranges))
    console.print(table)


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


@app.command("checkpoint")
def checkpoint_cmd(
    path: Path = typer.Argument(..., help="Path to the project"),
    next_task: str = typer.Option("", "--next", "-n", help="What to do next in the new session"),
    output: Path = typer.Option(None, "--output", "-o", help="Output path (default: <path>/CHECKPOINT.md)"),
):
    """Compress current progress into a fresh-start prompt for a new conversation.

    When your conversation history is getting long, run this command.
    It generates CHECKPOINT.md — paste it into a new Claude Code session
    to continue with 90% fewer tokens than carrying the full history.

    Example:
      tslayer checkpoint . --next "add user authentication module"
    """
    path = _require_dir(path)

    from cca.snapshot import build_snapshot, snapshot_token_stats

    with console.status("[cyan]Building checkpoint...[/cyan]"):
        snapshot_text = build_snapshot(path)
        stats = snapshot_token_stats(path, snapshot_text)

    project_name = path.resolve().name
    snap_tokens = stats["snapshot_tokens"]
    full_tokens = stats["with_claudeignore_tokens"]

    next_section = next_task.strip() if next_task.strip() else "_(describe what to do next)_"

    handoff = "\n".join([
        f"# {project_name} — Checkpoint",
        "",
        "> Generated by `tslayer checkpoint`. Paste this into a new Claude Code conversation.",
        "> Do NOT read source files — work only from the signatures below unless you need a specific file.",
        "",
        "## Project overview",
        "",
        snapshot_text,
        "## Next task",
        "",
        next_section,
        "",
    ])

    out = output or (path / "CHECKPOINT.md")
    out.write_text(handoff, encoding="utf-8")

    from cca.token_counter import count_tokens as _count_tokens
    cp_tokens = _count_tokens(handoff)

    console.print(f"\n[bold green]v[/bold green] Written: {out}\n")
    console.print(f"  Checkpoint      : [green]{cp_tokens:,}[/green] tokens")
    console.print(f"  Full project    : [red]{full_tokens:,}[/red] tokens")
    console.print(
        f"  [bold green]Savings vs full context: "
        f"{(1 - cp_tokens / full_tokens) * 100:.0f}%[/bold green]\n"
    )
    console.print("[bold cyan]Next steps:[/bold cyan]")
    console.print("  1. Open a [bold]new[/bold] Claude Code conversation")
    console.print(f"  2. Say: [italic]'Read CHECKPOINT.md and continue from there'[/italic]")
    console.print(f"  [dim]File: {out}[/dim]\n")


@app.command("decision")
def decision_cmd(
    text: str = typer.Argument(None, help="Decision to record"),
    path: Path = typer.Option(Path("."), "--path", "-p", help="Project root"),
    tag: str = typer.Option("architecture", "--tag", "-t", help="Tag: architecture | bugfix | design | refactor"),
    list_all: bool = typer.Option(False, "--list", "-l", help="List all recorded decisions"),
):
    """Record WHY the code is structured a certain way.

    Saves to DECISIONS.md. Claude reads this at every session start
    so it never second-guesses or silently reverts past choices.

    Examples:
      tslayer decision "complexity per-function — per-file penalises large files unfairly"
      tslayer decision "dead code skips @property — they are framework entry points" --tag bugfix
      tslayer decision --list
    """
    path = path.resolve()
    decisions_path = path / "DECISIONS.md"

    if list_all:
        if not decisions_path.exists():
            console.print("[yellow]No decisions recorded yet.[/yellow]")
            console.print("[dim]Run: tslayer decision \"your decision\"[/dim]")
            raise typer.Exit(0)
        console.print(decisions_path.read_text(encoding="utf-8"))
        raise typer.Exit(0)

    if not text:
        console.print("[red]Provide a decision:[/red] tslayer decision \"why you did X\"")
        raise typer.Exit(1)

    import datetime
    today = datetime.date.today().isoformat()

    entry = f"\n### {today} [{tag}]\n{text.strip()}\n"

    if not decisions_path.exists():
        decisions_path.write_text(
            "# Architecture Decisions\n\n"
            "> Managed by `tslayer decision`.\n"
            "> Claude reads this to understand WHY the code is structured this way.\n"
            "> Never remove entries — add a new one if a decision changes.\n",
            encoding="utf-8",
        )

    with decisions_path.open("a", encoding="utf-8") as f:
        f.write(entry)

    console.print(f"\n[bold green]v[/bold green] Recorded in {decisions_path.name}\n")
    console.print(f"  [cyan][{tag}][/cyan] {text.strip()}\n")
    console.print(
        "[dim]Claude will read this at every session start. "
        "Add DECISIONS.md to your CLAUDE.md if not already there.[/dim]"
    )


@app.command("version")
def version_cmd():
    """Show version."""
    console.print(f"cca v{__version__}")


if __name__ == "__main__":
    app()
