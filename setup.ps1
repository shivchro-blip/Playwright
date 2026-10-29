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
# requirements-lock.txt pins every package (with hashes) so all machines get the same tested versions
& $venvPython -m pip install --require-hashes -r requirements-lock.txt --quiet
if (-not $?) { Write-Error "pip install failed (requirements-lock.txt)."; exit 1 }

Write-Host "`n[3/4] Installing Playwright Chromium..."
& $venvPython -m playwright install chromium
if (-not $?) {
    # "unable to get local issuer certificate": a company proxy re-signs HTTPS traffic with its own root
    # certificate, which Windows trusts (so browsers work) but Node does not. Retry using the Windows store.
    Write-Host "`nDownload failed. Retrying with the Windows certificate store (NODE_OPTIONS=--use-system-ca)..."
    $env:NODE_OPTIONS = "--use-system-ca"
    & $venvPython -m playwright install chromium
    $ok = $?
    Remove-Item Env:\NODE_OPTIONS -ErrorAction SilentlyContinue
    if (-not $ok) {
        Write-Host @"

Still failing. If the error mentions a certificate (UNABLE_TO_GET_ISSUER_CERT_LOCALLY), your network inspects
HTTPS traffic. Ask IT for the company root certificate as a .pem/.crt file, then run:

  `$env:NODE_EXTRA_CA_CERTS = "C:\path\to\company-root.pem"
  .\setup.ps1

If the error is a timeout or "connection refused", the download host cdn.playwright.dev may be blocked:
ask IT to allow it, or set `$env:HTTPS_PROXY = "http://proxy-host:port" and re-run.
(Do not set NODE_TLS_REJECT_UNAUTHORIZED=0: it turns certificate checking off for everything.)
"@
        Write-Error "playwright install failed."
        exit 1
    }
}

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
