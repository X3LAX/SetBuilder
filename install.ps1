# SetBuilder installer for Windows. Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [-WithClaude]
param([switch]$WithClaude)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $env:LOCALAPPDATA "SetBuilder"

# Python 3.11+
$ok = $false
try { & py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; $ok = ($LASTEXITCODE -eq 0) } catch {}
if (-not $ok) { Write-Error "Python 3.11+ introuvable. Installe-le depuis https://www.python.org/downloads/ (cocher 'py launcher')."; exit 1 }

New-Item -ItemType Directory -Force -Path $dest | Out-Null
$venv = Join-Path $dest "venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { & py -3 -m venv $venv }

# Copy the app
$appDir = Join-Path $dest "app"
if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
Copy-Item -Recurse -Force (Join-Path $here "app") $appDir

& (Join-Path $venv "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $venv "Scripts\python.exe") -m pip install -r (Join-Path $here "requirements.txt")

# ffmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) { winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements }
    else { Write-Warning "ffmpeg introuvable : installe-le (https://ffmpeg.org/download.html) puis relance." }
}

# Shortcuts
$pyw = Join-Path $venv "Scripts\pythonw.exe"
$shell = New-Object -ComObject WScript.Shell
$targets = @((Join-Path ([Environment]::GetFolderPath("Programs")) "SetBuilder.lnk"),
             (Join-Path ([Environment]::GetFolderPath("Desktop")) "SetBuilder.lnk"))
foreach ($t in $targets) {
    $l = $shell.CreateShortcut($t)
    $l.TargetPath = $pyw
    $l.Arguments = "`"$(Join-Path $appDir 'main.py')`""
    $l.WorkingDirectory = $appDir
    $l.Save()
}

if ($WithClaude) {
    if (Get-Command npm -ErrorAction SilentlyContinue) { npm install -g @anthropic-ai/claude-code }
    else { Write-Warning "npm introuvable. Installe Node.js (https://nodejs.org) ou suis https://docs.claude.com/en/docs/claude-code/setup" }
}
Write-Host "SetBuilder installe : menu Demarrer > SetBuilder"
