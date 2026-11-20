@echo off
rem Started by Windows Task Scheduler. No window stays open; output goes to out\scheduled_log.txt.
setlocal
cd /d "%~dp0.."
call "%~dp0find_python.bat"
if not defined PY exit /b 1
if not exist out mkdir out
echo ==== %date% %time% ====>> out\scheduled_log.txt
%PY% ai_digest.py >> out\scheduled_log.txt 2>&1
