@echo off
chcp 65001 >nul
title VENUES - Turn the booking system ON
net session >nul 2>nul || (echo [X] Right-click this file and choose "Run as administrator". & pause & exit /b 1)
schtasks /change /tn "VENUES Website" /enable >nul 2>nul || (echo [X] The startup task is missing - run 3_autostart.bat as administrator & pause & exit /b 1)
schtasks /run /tn "VENUES Website" >nul
echo.
echo   2.iajaward.org (bookings) is ON again (starts with Windows as before).
echo.
pause
