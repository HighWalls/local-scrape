@echo off
setlocal
set "SELECTED_PYTHON="
if defined LOCAL_SCRAPE_PYTHON (
  call :try_python "%LOCAL_SCRAPE_PYTHON%"
  if defined SELECTED_PYTHON goto install
  goto missing
)
call :try_python "%~dp0..\.venv\Scripts\python.exe"
if defined SELECTED_PYTHON goto install
for %%V in (3.14 3.13) do (
  for /f "delims=" %%P in ('py -%%V -I -c "import sys; print(sys.executable)" 2^>nul') do call :try_python "%%P"
)
if defined SELECTED_PYTHON goto install
for %%P in (python.exe python3.exe python3.14.exe python3.13.exe) do call :try_python "%%P"
if defined SELECTED_PYTHON goto install
for %%K in (HKCU HKLM) do for %%V in (3.14 3.13) do for %%R in (64 32) do (
  for /f "tokens=2,*" %%A in ('reg query "%%K\Software\Python\PythonCore\%%V\InstallPath" /v ExecutablePath /reg:%%R 2^>nul') do call :try_python "%%B"
)
if defined SELECTED_PYTHON goto install
:missing
echo Install Python 3.13 or 3.14 from python.org, then rerun setup.cmd. 1>&2
echo A supported Python launcher, PATH entry, or standard registry installation is required. 1>&2
echo Optionally set LOCAL_SCRAPE_PYTHON to the full interpreter path. 1>&2
exit /b 1

:try_python
if defined SELECTED_PYTHON exit /b 0
"%~1" -I -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,13),(3,14)) else 1)" >nul 2>&1
if not errorlevel 1 set "SELECTED_PYTHON=%~1"
exit /b 0

:install
set "PYTHONDONTWRITEBYTECODE=1"
"%SELECTED_PYTHON%" -B "%~dp0bootstrap_runtime.py" --install %*
exit /b %errorlevel%
