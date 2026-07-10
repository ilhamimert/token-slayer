#!/usr/bin/env bash
# Token Slayer — Mac/Linux one-shot setup
# Usage: ./setup.sh

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; GRAY='\033[0;37m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[!!]${NC} $1"; }
fail() { echo -e "  ${RED}[X] ${NC} $1"; exit 1; }

echo ""
echo -e "  ${CYAN}Token Slayer — Setup${NC}"
echo -e "  ${CYAN}=====================${NC}"
echo ""

# ── 1. Python check ─────────────────────────────────────────────────────────
if command -v python3 &>/dev/null; then
    ok "$(python3 --version)"
    PY=python3
elif command -v python &>/dev/null; then
    ok "$(python --version)"
    PY=python
else
    fail "Python not found. Install Python 3.11+ first."
fi

# ── 2. Virtual environment ───────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo -e "  ${GRAY}Creating .venv...${NC}"
    $PY -m venv .venv
    ok ".venv created"
else
    ok ".venv already exists"
fi

# ── 3. Activate ─────────────────────────────────────────────────────────────
# shellcheck disable=SC1091
source .venv/bin/activate

# ── 4. Upgrade pip ──────────────────────────────────────────────────────────
echo -e "  ${GRAY}Upgrading pip...${NC}"
pip install --upgrade pip --quiet

# ── 5. Install all extras ────────────────────────────────────────────────────
echo -e "  ${GRAY}Installing dependencies (this may take a minute)...${NC}"
pip install -e ".[dev,mcp]" --quiet
ok "All dependencies installed"

# ── 6. .env setup ───────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn ".env created from .env.example — open it and add your API keys!"
else
    ok ".env already exists"
fi

# ── 7. Smoke test ───────────────────────────────────────────────────────────
echo ""
echo -e "  ${GRAY}Verifying...${NC}"
tslayer --help 2>&1 | head -3 | sed 's/^/    /'

echo ""
echo -e "  ${GREEN}✓ Token Slayer is ready!${NC}"
echo ""
echo -e "  ${CYAN}Next steps:${NC}"
echo "    1. Edit .env  →  add ANTHROPIC_API_KEY (and/or OPENAI_API_KEY)"
echo "    2. Activate venv:  source .venv/bin/activate"
echo "    3. Try it:  tslayer score ."
echo "    4. Start proxy:  tslayer serve"
echo ""
