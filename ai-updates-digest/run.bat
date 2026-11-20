@echo off
setlocal
title AI Updates Digest
cd /d "%~dp0"
call "%~dp0helpers\find_python.bat"
if not defined PY goto nopython
if "%~1"=="" goto default
%PY% ai_digest.py %*
goto done
:default
%PY% ai_digest.py --open
goto done
:nopython
echo.
echo Python 3 was not found on this computer.
echo Install it from https://www.python.org/downloads/ and run this file again.
:done
echo.
pause
