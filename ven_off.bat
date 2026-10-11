@echo off
chcp 65001 >nul
title VENUES - Turn the booking system OFF
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
REM Stops 2.iajaward.org only - the award website (iajaward.org) keeps running
schtasks /end /tn "VENUES Website" >nul 2>nul
schtasks /change /tn "VENUES Website" /disable >nul 2>nul
echo.
echo   2.iajaward.org (bookings) is OFF (also after restarting the PC).
echo   iajaward.org is still running.
echo   To turn it back on: ven_on.bat (as administrator)
echo.
pause
