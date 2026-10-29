# One-time setup for a tester machine: Python venv, dependencies, Playwright browser,
# .env from template, and a "Start AutomateNY" desktop shortcut. Safe to re-run.
param(
    [switch]$NoShortcut = $false
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "========================================="
Write-Host " AutomateNY Setup "
Write-Host "========================================="

# 1. Python 3.12+
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python not found on PATH. Install Python 3.12+ from python.org and re-run."
    exit 1
}
$version = & python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
if ([version]$version -lt [version]"3.12") {
    Write-Error "Python $version found; 3.12+ required."
    exit 1
}

# 2. Virtual environment
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "`n[1/4] Creating .venv (Python $version)..."
    & python -m venv .venv
    if (-not $?) { Write-Error "venv creation failed."; exit 1 }
} else {
    Write-Host "`n[1/4] .venv already exists."
}
$venvPython = ".\.venv\Scripts\python.exe"

# 3. Dependencies + browser
Write-Host "`n[2/4] Installing Python packages..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements-web.txt --quiet
if (-not $?) { Write-Error "pip install failed."; exit 1 }

Write-Host "`n[3/4] Installing Playwright Chromium..."
& $venvPython -m playwright install chromium
if (-not $?) { Write-Error "playwright install failed."; exit 1 }

# 4. .env (never overwrite an existing one - it may hold credentials)
if (-not (Test-Path ".\.env")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "`n[4/4] Created .env from .env.example. Add credentials there if your systems need login."
} else {
    Write-Host "`n[4/4] .env already exists - left unchanged."
}

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $desktop "Start AutomateNY.lnk"))
    $shortcut.TargetPath = Join-Path $PSScriptRoot "start.cmd"
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Save()
    Write-Host "Desktop shortcut 'Start AutomateNY' created."
}

Write-Host "`n========================================="
Write-Host " Setup complete. Start with start.cmd (or the desktop shortcut)."
Write-Host " The app opens at http://127.0.0.1:8080"
Write-Host "========================================="
