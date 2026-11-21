@echo off
setlocal
title Gold Rate Alert
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
%PY% gold_rate.py %*
goto done
:default
%PY% gold_rate.py --open
goto done
:nopython
echo.
echo Python 3 was not found on this computer.
echo Install it from https://www.python.org/downloads/ and run this file again.
:done
echo.
pause
