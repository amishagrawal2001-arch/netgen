# install_client.ps1 — Windows PowerShell client installer.
#
# Parallel to install_client.sh on Linux/macOS. Builds a per-user
# Python venv under %USERPROFILE%\.netgen-client, installs the wheel,
# and drops a Start Menu shortcut + Desktop launcher (.lnk) that
# points at the GUI.
#
# Usage from PowerShell:
#   .\install_client.ps1                                   # uses dist\*.whl
#   .\install_client.ps1 -Server http://lab-box:5050       # pre-set server URL
#   .\install_client.ps1 -Wheel C:\path\to\netgen.whl
#   .\install_client.ps1 -Upgrade                          # force rebuild venv
#
# Notes:
#   * Does NOT need admin. Per-user install.
#   * Requires Python 3.9+ on PATH. If missing, the script tells you
#     where to get it.
#   * If Execution Policy blocks the script: run PowerShell as the
#     same user and `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

[CmdletBinding()]
param(
    [string]$Server = "",
    [string]$Wheel = "",
    [switch]$Upgrade
)

$ErrorActionPreference = "Stop"

function Log    ($Msg) { Write-Host "[client] $Msg" -ForegroundColor Green }
function Warn   ($Msg) { Write-Host "[client] WARNING: $Msg" -ForegroundColor Yellow }
function Fail   ($Msg) { Write-Host "[client] ERROR: $Msg" -ForegroundColor Red; exit 1 }
function Info   ($Msg) { Write-Host "[client] $Msg" -ForegroundColor Cyan }

# ───────────────────────────────────────────────────────────────────
# Locate the wheel
# ───────────────────────────────────────────────────────────────────

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir  = Join-Path $env:USERPROFILE ".netgen-client"

if (-not $Wheel) {
    $candidates = @(
        Get-ChildItem -Path (Join-Path $RepoRoot "dist") -Filter "ostg_trafficgen-*-py3-none-any.whl" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        Get-ChildItem -Path $RepoRoot -Filter "ostg_trafficgen-*-py3-none-any.whl" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
    ) | Where-Object { $_ -ne $null } | Select-Object -First 1
    if ($candidates) { $Wheel = $candidates.FullName }
}
if (-not $Wheel -or -not (Test-Path $Wheel)) {
    Fail "No wheel found. Build one with 'python -m build --wheel', or pass -Wheel <path>."
}
Log "Using wheel: $Wheel"

# ───────────────────────────────────────────────────────────────────
# Python ≥ 3.9 check
# ───────────────────────────────────────────────────────────────────
#
# Windows-canonical locations: PATH (`python`, `py -3`), then the
# `py` launcher which knows about every installed version.
# `py -3.X` form is what `py --list` typically advertises.

$PythonBin = $null
foreach ($cmd in @("python", "python3", "py")) {
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
    Write-Host ""
    Write-Host "  Need Python >= 3.9 on PATH."                                -ForegroundColor Yellow
    Write-Host "  Download:  https://www.python.org/downloads/windows/"      -ForegroundColor Yellow
    Write-Host "  At install time, CHECK 'Add python.exe to PATH'."           -ForegroundColor Yellow
    Write-Host ""
    Fail "No suitable Python interpreter found."
}

# ───────────────────────────────────────────────────────────────────
# Venv setup
# ───────────────────────────────────────────────────────────────────

if ((Test-Path $VenvDir) -and -not $Upgrade) {
    Info "Reusing existing venv: $VenvDir  (pass -Upgrade to rebuild)"
} else {
    if (Test-Path $VenvDir) {
        Log "Removing existing venv for clean upgrade..."
        Remove-Item -Recurse -Force $VenvDir
    }
    Log "Creating venv: $VenvDir"
    & $PythonBin -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed." }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"
$VenvClient = Join-Path $VenvDir "Scripts\ostg-client.exe"

if (-not (Test-Path $VenvPython)) {
    Fail "venv Python missing — venv didn't bootstrap properly."
}

Log "Upgrading pip..."
& $VenvPython -m pip install --quiet --upgrade pip setuptools wheel

Log "Installing $Wheel..."
& $VenvPython -m pip install --force-reinstall "$Wheel"
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }

if (-not (Test-Path $VenvClient)) {
    Fail "ostg-client.exe not found in venv after install — wheel may be missing the entry point."
}

# ───────────────────────────────────────────────────────────────────
# Shortcuts (Desktop + Start Menu)
# ───────────────────────────────────────────────────────────────────
#
# Windows shortcuts are .lnk files built via WScript.Shell COM. The
# `Arguments` field is where we pass `-s http://server:5050` so
# operators don't have to type it on every launch.

$LaunchArgs = ""
if ($Server) { $LaunchArgs = "-s $Server" }

function New-Shortcut {
    param([string]$LnkPath, [string]$Target, [string]$Args, [string]$Description, [string]$IconPath = $null)
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($LnkPath)
    $sc.TargetPath = $Target
    $sc.Arguments  = $Args
    $sc.WorkingDirectory = (Split-Path -Parent $Target)
    $sc.Description = $Description
    if ($IconPath -and (Test-Path $IconPath)) {
        $sc.IconLocation = $IconPath
    }
    $sc.Save()
}

# Find an icon if the wheel ships one. Windows wants .ico for best
# rendering; .png works in modern Windows but looks rough at small
# sizes. We accept either.
$IconPath = $null
foreach ($glob in @(
    (Join-Path $VenvDir "Lib\site-packages\resources\icons\ostg.ico"),
    (Join-Path $VenvDir "Lib\site-packages\resources\icons\ostg.png"),
    (Join-Path $VenvDir "Lib\site-packages\resources\icons\icon.png")
)) {
    if (Test-Path $glob) { $IconPath = $glob; break }
}

$Desktop  = [Environment]::GetFolderPath("Desktop")
$StartMenu = [Environment]::GetFolderPath("Programs")
$DesktopLnk  = Join-Path $Desktop "Netgen Client.lnk"
$StartLnk    = Join-Path $StartMenu "Netgen Client.lnk"

Log "Creating Desktop shortcut: $DesktopLnk"
New-Shortcut -LnkPath $DesktopLnk -Target $VenvClient -Args $LaunchArgs `
             -Description "Netgen GUI client" -IconPath $IconPath

Log "Creating Start Menu shortcut: $StartLnk"
New-Shortcut -LnkPath $StartLnk -Target $VenvClient -Args $LaunchArgs `
             -Description "Netgen GUI client" -IconPath $IconPath

# ───────────────────────────────────────────────────────────────────
# What now?
# ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Client install complete"                                            -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "Venv:    $VenvDir"
Write-Host "Wheel:   $Wheel"
Write-Host "Python:  $PythonBin"
if ($Server) { Write-Host "Server:  $Server" }
Write-Host ""
Write-Host "Launch the GUI:"
Write-Host "  - Double-click the Desktop shortcut 'Netgen Client'"
Write-Host "  - Or from Start Menu: 'Netgen Client'"
Write-Host "  - Or from PowerShell:"
Write-Host "      & '$VenvClient' $LaunchArgs" -ForegroundColor Cyan
Write-Host ""
Write-Host "Authentication:"
Write-Host "  If the server has auth enabled, set the token in your"
Write-Host "  user environment before launching:"
Write-Host "    setx NETGEN_AUTH_TOKEN <your-token>" -ForegroundColor Cyan
Write-Host "  (then close + reopen the shell so the var propagates)."
Write-Host ""
Write-Host "To uninstall:"
Write-Host "  Remove-Item -Recurse -Force '$VenvDir'"            -ForegroundColor Cyan
Write-Host "  Remove-Item '$DesktopLnk', '$StartLnk'"            -ForegroundColor Cyan
Write-Host ""
