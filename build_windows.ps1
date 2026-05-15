# build_windows.ps1 — Build a Windows .exe installer for the Netgen GUI.
#
# Produces `dist\Netgen-Client-<version>-windows.exe`, a single-file
# PyInstaller-bundled executable. Operators run the .exe directly —
# no Python install needed on the target machine.
#
# Requirements on the BUILD host:
#   * Windows 10 / 11 (PyInstaller is OS-native, can't cross-build)
#   * Python 3.9+ on PATH (we use it to run pyinstaller)
#   * Internet access (pip downloads PyInstaller + the wheel's deps)
#
# Usage:
#   .\build_windows.ps1                  # one-file .exe
#   .\build_windows.ps1 -Folder          # one-folder build (faster startup)
#
# What ends up in dist\ after a clean run:
#   * Netgen-Client-<version>-windows.exe              (~150 MB, single file)
#   * (with -Folder: Netgen-Client-<version>-windows\  containing many files)

[CmdletBinding()]
param(
    [switch]$Folder
)

$ErrorActionPreference = "Stop"

function Log    ($Msg) { Write-Host "[build] $Msg" -ForegroundColor Green }
function Warn   ($Msg) { Write-Host "[build] WARNING: $Msg" -ForegroundColor Yellow }
function Fail   ($Msg) { Write-Host "[build] ERROR: $Msg" -ForegroundColor Red; exit 1 }
function Info   ($Msg) { Write-Host "[build] $Msg" -ForegroundColor Cyan }

if (-not $IsWindows -and ($PSVersionTable.PSVersion.Major -ge 6)) {
    Fail "PyInstaller can't cross-build; run this on a Windows machine."
}

# ───────────────────────────────────────────────────────────────────
# Python detection
# ───────────────────────────────────────────────────────────────────

$PythonBin = $null
foreach ($cmd in @("python", "py")) {
    $exe = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $exe) { continue }
    $ver = & $exe.Source --version 2>&1
    if ($ver -match "Python (\d+)\.(\d+)") {
        $major = [int]$matches[1]; $minor = [int]$matches[2]
        if (($major -gt 3) -or ($major -eq 3 -and $minor -ge 9)) {
            $PythonBin = $exe.Source
            Info "Using Python: $PythonBin ($ver)"
            break
        }
    }
}
if (-not $PythonBin) {
    Fail "Need Python >= 3.9 on PATH. Install from python.org and check 'Add to PATH'."
}

# ───────────────────────────────────────────────────────────────────
# Build venv (isolated from the user's system Python)
# ───────────────────────────────────────────────────────────────────

$BuildVenv = ".build_windows_venv"

if (Test-Path $BuildVenv) {
    Log "Reusing existing build venv: $BuildVenv"
} else {
    Log "Creating build venv: $BuildVenv"
    & $PythonBin -m venv $BuildVenv
    if ($LASTEXITCODE -ne 0) { Fail "venv create failed." }
}

$Py  = Join-Path $BuildVenv "Scripts\python.exe"
$Pip = Join-Path $BuildVenv "Scripts\pip.exe"

Log "Installing build prerequisites..."
& $Py -m pip install --quiet --upgrade pip setuptools wheel
# PyInstaller + the runtime deps the spec lists as hidden imports.
& $Py -m pip install --quiet pyinstaller pillow
& $Py -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed — check requirements.txt is present." }

# ───────────────────────────────────────────────────────────────────
# Run PyInstaller
# ───────────────────────────────────────────────────────────────────

Log "Cleaning previous build artifacts..."
@("build", "dist\Netgen-Client-*-windows*", "*.spec.bak") |
    ForEach-Object { Get-Item -Path $_ -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }

# Spec mode: one-file (default) vs one-folder via env var the spec reads.
if ($Folder) {
    $env:NETGEN_ONEFILE = "0"
    Info "Building one-FOLDER distribution (faster startup)..."
} else {
    $env:NETGEN_ONEFILE = "1"
    Info "Building one-FILE .exe (single-file ship)..."
}

Log "Invoking PyInstaller..."
& $Py -m PyInstaller --clean --noconfirm ostg_client_windows.spec
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed." }

# ───────────────────────────────────────────────────────────────────
# Report the result
# ───────────────────────────────────────────────────────────────────

$Version = (Get-Content pyproject.toml | Select-String '^\s*version\s*=\s*"([^"]+)"').Matches.Groups[1].Value
if (-not $Version) { Fail "Could not read version from pyproject.toml." }

$ExePath    = "dist\Netgen-Client-${Version}-windows.exe"
$FolderPath = "dist\Netgen-Client-${Version}-windows"

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Windows build complete (Netgen $Version)"                           -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

if ($Folder) {
    if (Test-Path $FolderPath) {
        $Size = (Get-ChildItem -Recurse $FolderPath | Measure-Object Length -Sum).Sum
        $SizeMB = [math]::Round($Size / 1MB, 1)
        Write-Host "Folder build:   $FolderPath  (${SizeMB} MB)"
    }
} else {
    if (Test-Path $ExePath) {
        $Size = (Get-Item $ExePath).Length
        $SizeMB = [math]::Round($Size / 1MB, 1)
        Write-Host "Single-file build: $ExePath  (${SizeMB} MB)"
        Write-Host ""
        Write-Host "Ship $ExePath to your Windows customers — they run it directly,"
        Write-Host "no Python install needed."
    }
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  - Test launch:  .\$ExePath"
Write-Host "  - Attach to a GitHub release alongside the .whl + .dmg + .AppImage"
Write-Host ""
