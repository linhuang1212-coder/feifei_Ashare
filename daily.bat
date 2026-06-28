@echo off
set PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set REPO=C:\Users\Administrator\feifei_Ashare
set PYTHONUTF8=1
cd /d "%REPO%"
if not exist "%REPO%\logs" mkdir "%REPO%\logs"
"%PY%" run.py daily --config "%REPO%\config.yaml" >> "%REPO%\logs\daily.log" 2>&1
