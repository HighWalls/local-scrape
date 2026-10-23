@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Runtime missing. Run setup.cmd first. 1>&2
  exit /b 1
)
set "PYTHONDONTWRITEBYTECODE=1"
"%~dp0.venv\Scripts\python.exe" -B "%~dp0scripts\run_task.py" %*
exit /b %errorlevel%
