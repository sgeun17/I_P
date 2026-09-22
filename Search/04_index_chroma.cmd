@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run 01_setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import chromadb" >nul 2>&1
if errorlevel 1 (
  echo Run 03_check_chroma.cmd to install ChromaDB first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 chroma_index.py
if errorlevel 1 (
  echo Index verification failed. See the message above.
  pause
  exit /b 1
)
pause
