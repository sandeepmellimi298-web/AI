@echo off
setlocal
title Bank Jobs Alert
cd /d "%~dp0"
set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run
python -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set PY="%%~fD\python.exe"
if not defined PY goto nopython
:run
if "%~1"=="" goto default
%PY% bank_jobs.py %*
goto done
:default
%PY% bank_jobs.py --open
goto done
:nopython
echo.
echo Python 3 was not found on this computer.
echo Install it from https://www.python.org/downloads/ and run this file again.
:done
echo.
pause
