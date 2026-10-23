@echo off
setlocal
call "%~dp0scripts\install-runtime.cmd" %*
exit /b %errorlevel%
