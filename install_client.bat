@echo off
REM install_client.bat — double-click wrapper around install_client.ps1.
REM
REM Forwards every argument through to the PowerShell script and
REM bypasses Execution Policy for this one run (per-process, no
REM permanent change to the user's security posture). Lets operators
REM who don't know PowerShell just double-click the .bat file or run:
REM
REM   install_client.bat
REM   install_client.bat -Server http://lab-box:5050
REM   install_client.bat -Upgrade

setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%SCRIPT_DIR%install_client.ps1" %*
set EXITCODE=%ERRORLEVEL%

REM Keep the window open if launched from Explorer (double-click)
REM so operators can read the output before it vanishes.
echo.
if %EXITCODE% neq 0 (
    echo Install exited with error code %EXITCODE%.
) else (
    echo Install finished. You can close this window.
)
pause >nul
exit /b %EXITCODE%
