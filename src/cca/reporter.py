"""Rich-based terminal output."""
from __future__ import annotations

import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cca.parser import FileInfo

# Force UTF-8 on Windows to handle all Rich output correctly
console = Console(highlight=False)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    console = Console(file=sys.stdout, highlight=False)


def print_analysis_table(
    file_infos: list[FileInfo],
    root: Path,
    in_degrees: dict[str, int],
    hot_files: dict[str, int],
) -> None:
    table = Table(
        title=f"Project Analysis - {root.name}",
        box=box.SIMPLE_HEAD,
        header_style="bold cyan",
        show_lines=False,
    )
    table.add_column("File", style="white", no_wrap=True, min_width=30)
    table.add_column("Lines", justify="right", style="yellow")
    table.add_column("Funcs", justify="right", style="green")
    table.add_column("Classes", justify="right", style="blue")
    table.add_column("Imports", justify="right", style="dim")
    table.add_column("Imported by", justify="right", style="bold red")
    table.add_column("Hot", justify="center", style="red")

    sorted_infos = sorted(
        file_infos,
        key=lambda f: in_degrees.get(str(f.path.relative_to(root)), 0),
        reverse=True,
    )

    for info in sorted_infos:
        rel = str(info.path.relative_to(root))
        imported_by = in_degrees.get(rel, 0)
        table.add_row(
            rel,
            str(info.lines),
            str(info.function_count),
            str(info.class_count),
            str(len(info.imports)),
            str(imported_by) if imported_by else "-",
            "HOT" if rel in hot_files else "",
        )

    console.print(table)


def print_dependency_summary(most_imported: list[tuple[str, int]]) -> None:
    top = [(p, c) for p, c in most_imported if c > 0][:5]
    if not top:
        return
    body = "\n".join(
        f"  [cyan]{p}[/cyan]  <-  imported by [bold]{c}[/bold] file{'s' if c != 1 else ''}"
        for p, c in top
    )
    console.print(Panel(body, title="[bold]Most Imported Files[/bold]", border_style="cyan"))


def print_token_report(baseline: int, optimized: int, savings_pct: float) -> None:
    console.print(Panel(
        f"  Full project (no ignore):  [yellow]{baseline:>10,}[/yellow] tokens\n"
        f"  With .claudeignore:        [green]{optimized:>10,}[/green] tokens\n"
        f"  Estimated savings:         [bold green]{savings_pct:>9.1f}%[/bold green]",
        title="[bold]Token Budget Estimate[/bold]",
        subtitle="[dim]Approx. via cl100k_base (~Claude tokenizer, +-10%)[/dim]",
        border_style="green",
    ))


def print_unused(unused: dict[str, list[str]]) -> None:
    if not unused:
        console.print(Panel("[green]No obvious dead code detected.[/green]", border_style="green"))
        return
    lines = [
        f"  [yellow]{path}[/yellow]  ->  {', '.join(syms)}"
        for path, syms in list(unused.items())[:12]
    ]
    console.print(Panel(
        "\n".join(lines),
        title="[bold yellow]Possible Dead Code[/bold yellow]",
        subtitle="[dim]Verify before removing - dynamic access not detected[/dim]",
        border_style="yellow",
    ))
