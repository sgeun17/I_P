@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run 01_setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 chunk_retriever.py --input examples\docx_input_legacy.json --compat-input --output reports\chunk_search_result.json
if errorlevel 1 (
  echo Search failed. Read reports\chunk_search_result.json for details.
  pause
  exit /b 1
)
echo Done. Open reports\chunk_search_result.json.
pause
