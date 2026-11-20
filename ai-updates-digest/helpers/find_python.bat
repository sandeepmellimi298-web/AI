@echo off
rem Sets PY to a command that starts Python 3. PY stays empty when none is found.
set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :eof
python -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :eof
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try_folder "%%~fD"
if defined PY goto :eof
for %%D in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3") do call :try_conda "%%~D"
goto :eof

:try_folder
if defined PY goto :eof
if not exist "%~1\python.exe" goto :eof
set "PY="%~1\python.exe""
goto :eof

:try_conda
if defined PY goto :eof
if not exist "%~1\python.exe" goto :eof
set "PY="%~1\python.exe""
set "PATH=%~1;%~1\Library\bin;%~1\Scripts;%PATH%"
goto :eof
