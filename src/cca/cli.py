from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from cca import __version__
from cca import reporter
from cca.config_gen import generate_claude_md, CLAUDEIGNORE_PATTERNS
from cca.dead_code import find_unused_exports
from cca.git_analyzer import get_hot_files, is_git_repo
from cca.graph import build_graph, get_in_degrees, get_most_imported
from cca.parser import analyze_project
from cca.token_counter import count_all_tokens, count_project_tokens

app = typer.Typer(
    name="cca",
    help="Claude Context Analyzer — analyze Python projects, reduce token waste.",
    no_args_is_help=True,
)
console = Console()


def _require_dir(path: Path) -> None:
    if not path.exists():
        console.print(f"[red]Error:[/red] '{path}' does not exist.")
        raise typer.Exit(1)
    if not path.is_dir():
        console.print(f"[red]Error:[/red] '{path}' is not a directory.")
        raise typer.Exit(1)


@app.command("analyze")
def analyze(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    dead_code: bool = typer.Option(False, "--dead-code", "-d", help="Show potential dead code"),
    tokens: bool = typer.Option(False, "--tokens", "-t", help="Show token budget estimate"),
):
    """Analyze a Python project: file stats, dependencies, hot zones."""
    _require_dir(path)
    console.print(f"\n[bold green]Analyzing:[/bold green] {path.resolve()}\n")

    with console.status("[cyan]Parsing Python files...[/cyan]"):
        file_infos = analyze_project(path)

    if not file_infos:
        console.print("[yellow]No Python files found.[/yellow]")
        raise typer.Exit(0)

    with console.status("[cyan]Building dependency graph...[/cyan]"):
        graph = build_graph(file_infos, path)
        in_degrees = get_in_degrees(graph)
        most_imported = get_most_imported(graph, n=10)

    hot_files: dict[str, int] = {}
    if is_git_repo(path):
        with console.status("[cyan]Scanning git history...[/cyan]"):
            hot_files = get_hot_files(path)

    reporter.print_analysis_table(file_infos, path, in_degrees, hot_files)
    reporter.print_dependency_summary(most_imported)

    if hot_files:
        console.print("[dim]  HOT = changed in recent git history[/dim]\n")

    if dead_code:
        with console.status("[cyan]Detecting dead code...[/cyan]"):
            unused = find_unused_exports(file_infos, path)
        reporter.print_unused(unused)

    if tokens:
        with console.status("[cyan]Counting tokens...[/cyan]"):
            base = count_all_tokens(path)
            opt = count_project_tokens(path)
            b, o = base["total"], opt["total"]
            pct = (b - o) / b * 100 if b else 0.0
        reporter.print_token_report(b, o, pct)


@app.command("generate-config")
def generate_config(
    path: Path = typer.Argument(..., help="Path to the Python project"),
    output: Path = typer.Option(None, "--output", "-o", help="Output path (default: <path>/CLAUDE.md)"),
):
    """Generate an optimized CLAUDE.md for the project."""
    _require_dir(path)
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
        base = count_all_tokens(path)
        opt = count_project_tokens(path)
        b, o = base["total"], opt["total"]
        pct = (b - o) / b * 100 if b else 0.0

    content = generate_claude_md(
        root=path,
        file_infos=file_infos,
        most_imported=most_imported,
        hot_files=hot_files,
        unused_exports=unused,
        token_savings_pct=pct,
    )

    out = output or (path / "CLAUDE.md")
    out.write_text(content, encoding="utf-8")
    console.print(f"[bold green]✓[/bold green] Written: {out}")
    reporter.print_token_report(b, o, pct)


@app.command("version")
def version_cmd():
    """Show version."""
    console.print(f"cca v{__version__}")


if __name__ == "__main__":
    app()
