@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo {"ok":false,"error":{"code":"RUNTIME_MISSING","message":"Project Python environment is missing","details":{}}}
  exit /b 1
)
set "PYTHONDONTWRITEBYTECODE=1"
".venv\Scripts\python.exe" -B -m local_scrape --root "%~dp0." start --json
exit /b %errorlevel%
