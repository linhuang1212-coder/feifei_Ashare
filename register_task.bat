@echo off
schtasks /Create /TN "feifei-daily" /TR "C:\Users\Administrator\feifei_Ashare\daily.bat" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /F
echo.
if %errorlevel%==0 (echo [OK] feifei-daily registered: runs weekdays 18:00.) else (echo [FAIL] Please run this file as Administrator.)
echo Test once : schtasks /Run /TN feifei-daily
echo Query     : schtasks /Query /TN feifei-daily
echo Delete    : schtasks /Delete /TN feifei-daily /F
pause
