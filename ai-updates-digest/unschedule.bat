@echo off
title AI Updates Digest - remove daily schedule
schtasks /Delete /F /TN "AI Updates Digest Local"
echo.
pause
