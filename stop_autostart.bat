@echo off
cd /d "%~dp0"
net session >nul 2>nul || (echo [X] Run as administrator. & pause & exit /b 1)
schtasks /end /tn "VENUES Website" >nul 2>nul
schtasks /delete /f /tn "VENUES Website" >nul 2>nul
schtasks /delete /f /tn "VENUES Hourly Backup" >nul 2>nul
schtasks /delete /f /tn "VENUES Failover Watchdog" >nul 2>nul
echo Stopped and removed the VENUES tasks.
pause
