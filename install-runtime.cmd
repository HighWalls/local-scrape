@echo off
setlocal
call "%~dp0setup.cmd" %*
exit /b %errorlevel%
