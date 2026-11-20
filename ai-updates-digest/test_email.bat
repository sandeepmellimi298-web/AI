@echo off
setlocal
title AI Updates Digest - test email
cd /d "%~dp0"
call "%~dp0helpers\find_python.bat"
if not defined PY goto nopython
%PY% ai_digest.py --test-email
goto done
:nopython
echo.
echo Python 3 was not found on this computer.
echo Install it from https://www.python.org/downloads/ and run this file again.
:done
echo.
pause
