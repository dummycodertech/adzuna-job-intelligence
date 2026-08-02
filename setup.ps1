# setup.ps1 — Adzuna Job Intelligence Pipeline
# =============================================
# One-time local setup for Windows (PowerShell).
# Run from the project root: .\setup.ps1

Write-Host "`nAdzuna Job Intelligence — Local Setup" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# 1. Create virtual environment
if (-not (Test-Path ".venv")) {
    Write-Host "`n[1/4] Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
} else {
    Write-Host "`n[1/4] Virtual environment already exists, skipping." -ForegroundColor Green
}

# 2. Install dependencies
Write-Host "`n[2/4] Installing dependencies..." -ForegroundColor Yellow
& ".venv\Scripts\pip.exe" install -r requirements.txt --quiet

# 3. Create .env from .env.example if it doesn't exist
if (-not (Test-Path ".env")) {
    Write-Host "`n[3/4] Creating .env from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "      Open .env and fill in your ADZUNA_APP_ID and ADZUNA_APP_KEY." -ForegroundColor Magenta
    Write-Host "      Get credentials at: https://developer.adzuna.com/" -ForegroundColor Magenta
} else {
    Write-Host "`n[3/4] .env already exists, skipping." -ForegroundColor Green
}

# 4. Run dry-run to validate wiring
Write-Host "`n[4/4] Running dry-run to validate pipeline wiring..." -ForegroundColor Yellow
& ".venv\Scripts\python.exe" scripts/ingest.py --dry-run

Write-Host "`n======================================" -ForegroundColor Cyan
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Edit .env with your Adzuna credentials" -ForegroundColor White
Write-Host "  2. Run:  .venv\Scripts\python.exe scripts/ingest.py" -ForegroundColor White
Write-Host "  3. Run:  .venv\Scripts\python.exe scripts/validate.py" -ForegroundColor White
Write-Host "  4. Run:  .venv\Scripts\python.exe scripts/export_csv.py" -ForegroundColor White
Write-Host ""
