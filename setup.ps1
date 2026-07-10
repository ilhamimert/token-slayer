# Token Slayer — Windows one-shot setup
# Usage (PowerShell):  .\setup.ps1
# Usage (CMD/anywhere): powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"

function Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  [X]  $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  Token Slayer — Setup" -ForegroundColor Cyan
Write-Host "  =====================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Python check ─────────────────────────────────────────────────────────
try {
    $pyVer = python --version 2>&1
    Ok $pyVer
} catch {
    Fail "Python not found. Install Python 3.11+ from https://python.org and re-run."
}

# ── 2. Virtual environment ───────────────────────────────────────────────────
if (-not (Test-Path ".venv")) {
    Write-Host "  Creating .venv..." -ForegroundColor Gray
    python -m venv .venv
    Ok ".venv created"
} else {
    Ok ".venv already exists"
}

# ── 3. Activate ─────────────────────────────────────────────────────────────
& .\.venv\Scripts\Activate.ps1

# ── 4. Upgrade pip ──────────────────────────────────────────────────────────
Write-Host "  Upgrading pip..." -ForegroundColor Gray
python -m pip install --upgrade pip --quiet

# ── 5. Install all extras ────────────────────────────────────────────────────
Write-Host "  Installing dependencies (this may take a minute)..." -ForegroundColor Gray
pip install -e ".[dev,mcp]" --quiet
Ok "All dependencies installed"

# ── 6. .env setup ───────────────────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Warn ".env created from .env.example — open it and add your API keys!"
} else {
    Ok ".env already exists"
}

# ── 7. Smoke test ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Verifying..." -ForegroundColor Gray
$help = tslayer --help 2>&1 | Select-Object -First 3
$help | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  ✓ Token Slayer is ready!" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. Edit .env  →  add ANTHROPIC_API_KEY (and/or OPENAI_API_KEY)"
Write-Host "    2. Activate venv:  .\.venv\Scripts\Activate.ps1"
Write-Host "    3. Try it:  tslayer score ."
Write-Host "    4. Start proxy:  tslayer serve"
Write-Host ""
