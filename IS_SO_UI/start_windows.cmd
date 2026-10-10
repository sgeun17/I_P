@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python start_local.py
) else (
    py -3 start_local.py
)
pause
