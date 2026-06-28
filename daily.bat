@echo off
chcp 65001 >nul
REM feifei_Ashare 收盘后日常:批量补鲜 + 打分/信号(供 Windows 计划任务调用)
REM 用全路径 Python(py 启动器在计划任务/SYSTEM 下可能找不到 3.12)
set PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set REPO=C:\Users\Administrator\feifei_Ashare
set PYTHONUTF8=1
cd /d "%REPO%"
if not exist "%REPO%\logs" mkdir "%REPO%\logs"
echo. >> "%REPO%\logs\daily.log"
"%PY%" run.py daily --config "%REPO%\config.yaml" >> "%REPO%\logs\daily.log" 2>&1
