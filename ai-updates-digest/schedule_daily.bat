@echo off
setlocal
title AI Updates Digest - daily schedule
schtasks /Create /F /SC DAILY /ST 08:30 /TN "AI Updates Digest Local" /TR "\"%~dp0helpers\run_scheduled.bat\""
if errorlevel 1 goto failed
echo.
echo Done. Windows will run the digest every day at 08:30 while you are signed in.
echo The computer must be switched on at that time.
echo To stop it, double-click unschedule.bat.
goto end
:failed
echo.
echo The daily schedule could not be created. Read the message above.
:end
echo.
pause
