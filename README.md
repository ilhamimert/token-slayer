# Token Slayer

**Slash your Claude Code token usage.** Token Slayer analyzes your codebase locally — file structure, dependencies, git history, dead code, token distribution — and feeds Claude Code exactly the context it needs, instead of your entire project.

🇹🇷 [Türkçe README](README.tr.md)

> **No API key. No subscription. No billing.** Token Slayer never calls any LLM itself — it's a local static-analysis tool that runs entirely on your machine and hands Claude Code (which you already have) a compressed, relevant view of your project via [MCP](https://modelcontextprotocol.io/).

---

## Table of Contents

- [What It Does](#what-it-does)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [MCP Tools (for Claude Code)](#mcp-tools-for-claude-code)
- [How It Works](#how-it-works)
- [FAQ](#faq)
- [Development](#development)
- [License](#license)

---

## What It Does

Claude Code is at its best when it reads only the files relevant to your task. Left alone, it tends to read too much — whole files, whole directories — burning tokens on content it never needed. Token Slayer closes that gap:

| Problem | Token Slayer's answer |
|---|---|
| Claude reads your whole project to understand it | `snapshot` — a compressed `CONTEXT.md` (file tree + function/class signatures, no bodies) |
| Claude reads files that aren't relevant to your current task | `focus` — ranks files by relevance to a task description; `--with-deps` adds their import graph |
| Claude re-reads whole files after a small edit | `diff-context` — returns only the changed line ranges (+padding) from git |
| You don't know if `.claudeignore` is doing its job | `slim` / `tokens` — token budget analysis, before/after `.claudeignore` |
| Claude burns tokens on a file with a syntax error | `audit` — flags syntax errors, circular imports, and stale docs before you hand code to Claude |
| Your `CLAUDE.md` goes stale | `generate-config` — regenerates it from a fresh analysis |
| You want a single score for "is this project token-optimized?" | `score` — composite 0–100 health score |

Everything runs **locally**. No network calls except to your own git repository.

## Installation

### Option A — Global install via pipx (recommended)

Works from any project, on any machine, once installed:

```bash
pip install pipx
pipx install "token-slayer[mcp] @ git+https://github.com/ilhamimert/token-slayer.git"
```

### Option B — Local development install

```bash
git clone https://github.com/ilhamimert/token-slayer.git
cd token-slayer

# Windows
.\setup.ps1

# macOS / Linux
./setup.sh
```

Both setup scripts create a `.venv`, install all dependencies, and — if `pipx` is available — also install `tslayer` globally so it works from any project directory afterward.

## Quick Start

```bash
cd path/to/your-project
tslayer init                 # writes .mcp.json — one command, any project
```

Then, in your project, reload the Claude Code window (`Ctrl+Shift+P` → `Developer: Reload Window` in VS Code) so it picks up the new MCP server. From then on, Claude Code automatically has access to all of Token Slayer's tools when working in that project.

Try the CLI directly too:

```bash
tslayer score .              # composite health score (0-100)
tslayer audit .              # syntax errors, circular imports, stale CLAUDE.md
tslayer focus . "add Redis caching" --with-deps
```

## Command Reference

| Command | What it does |
|---|---|
| `tslayer init [path]` | Writes `.mcp.json` so Claude Code auto-loads Token Slayer for this project. |
| `tslayer analyze <path>` | File stats, dependency graph, hot files. Flags: `--quality`, `--cycles`, `--dead-code`, `--tokens`, `--chart`, `--multilang`, `--json`. |
| `tslayer score <path>` | Composite 0–100 health score (token savings, type coverage, complexity, dead code, circular imports). |
| `tslayer audit <path>` | CI-friendly check: is `CLAUDE.md` current? Any syntax errors? Circular imports? Token budget too large? Exits non-zero on failure. |
| `tslayer generate-config <path>` | Generates an optimized `CLAUDE.md` (project overview, hot files, dead code, recommended `.claudeignore`). |
| `tslayer tokens <path>` | Token budget report with a visual before/after `.claudeignore` chart. |
| `tslayer snapshot <path>` | Generates `CONTEXT.md` — file tree + function/class signatures, no bodies. Typically 80–90% smaller than reading every file. |
| `tslayer focus <path> "<task>"` | Ranks files by relevance to a task description. `--with-deps` adds each file's direct import neighbors. |
| `tslayer diff-context <path>` | Returns changed files + changed line ranges (with padding) from git — read only what changed, not whole files. `--staged`, `--pad N`. |
| `tslayer slim <path>` | Suggests `.claudeignore` patterns to hit a token budget. `--apply` writes them. |
| `tslayer sessions` | Live token usage for active Claude Code sessions. `--watch` for a live-refreshing view. |
| `tslayer checkpoint <path>` | Compresses current progress into a fresh-start prompt (`CHECKPOINT.md`) for a new conversation. |
| `tslayer decision "<text>"` | Records an architectural decision in `DECISIONS.md`, so future Claude sessions know *why* the code is structured a certain way. |
| `tslayer init-hooks <path>` | Installs a git pre-commit hook that runs `tslayer audit` before every commit. |
| `tslayer mcp` | Starts the MCP stdio server (this is what `.mcp.json` launches automatically — you don't need to run it by hand). |

Most commands support `--json` for machine-readable output.

## MCP Tools (for Claude Code)

Once `.mcp.json` is registered (via `tslayer init`), Claude Code can call these directly:

| Tool | Purpose |
|---|---|
| `snapshot_tool` | Compressed project overview — call this first, before reading any files. |
| `decisions_tool` | Read recorded architectural decisions — call before restructuring anything. |
| `focus_tool` | Task-relevant file ranking, optionally with import-graph context (`with_deps`). |
| `diff_context_tool` | Changed line ranges from git. |
| `syntax_check_tool` | Files with syntax errors, detected before Claude edits them. |
| `health_score_tool` | Composite project health score. |
| `analyze_project_tool` | File/function/class counts, complexity, detected frameworks. |
| `count_tokens_tool` | Token counts before/after `.claudeignore`. |
| `find_cycles_tool` | Circular import dependencies. |
| `most_imported_tool` | Highest-impact (most-imported) files. |
| `generate_config_tool` | Generate `CLAUDE.md` on demand. |

## How It Works

- **Parsing**: every `.py` file is parsed with [tree-sitter](https://tree-sitter.github.io/) — imports, functions, classes, cyclomatic complexity, and syntax errors, all from a real AST (not regex).
- **Dependency graph**: built with [networkx](https://networkx.org/) from resolved imports, used for circular-dependency detection and impact analysis.
- **Health score**: a weighted composite —

  | Component | Weight |
  |---|---|
  | Token savings (`.claudeignore` effectiveness) | 30% |
  | Type coverage (non-test functions) | 25% |
  | Complexity (branch density per function) | 20% |
  | Dead code (unused exports) | 15% |
  | Circular dependencies | 10% |

- **Caching**: parsed file data is cached (`.cca_cache.json`) and invalidated by mtime/size, so repeated runs on a large project are fast.

## FAQ

**Does this need an API key or subscription?**
No. Token Slayer never calls any LLM — it's a local static-analysis tool. Claude Code (which you already have) calls *it*, not the other way around.

**Will this work on a fresh machine / for someone else?**
Yes — `pipx install "token-slayer[mcp] @ git+https://github.com/ilhamimert/token-slayer.git"` once per machine, then `tslayer init` once per project. See [Installation](#installation).

**What languages does it analyze?**
Full support for Python. Basic (regex-based) support for TypeScript and Go via `analyze --multilang`.

**Where does it store project-specific data?**
`.cca_cache.json` (parse cache), `CLAUDE.md`, `CONTEXT.md`, `CHECKPOINT.md`, `DECISIONS.md` — all plain files in your project root, all safe to commit or `.gitignore` as you prefer.

## Development

```bash
git clone https://github.com/ilhamimert/token-slayer.git
cd token-slayer
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,mcp]"   # Windows
# .venv/bin/pip install -e ".[dev,mcp]"     # macOS/Linux

pytest -q
```

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright © 2026 [ilhamimert](https://github.com/ilhamimert).
