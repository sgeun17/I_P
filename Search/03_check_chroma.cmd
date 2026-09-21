@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run 01_setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import chromadb; from importlib.metadata import version; raise SystemExit(0 if version('chromadb') == '1.5.9' else 1)" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements-chroma.txt --cache-dir .cache/pip --disable-pip-version-check
  if errorlevel 1 (
    echo Installation failed. Check internet access and retry.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -X utf8 chroma_sample.py
if errorlevel 1 (
  echo Check failed. See the message above.
  pause
  exit /b 1
)
pause
