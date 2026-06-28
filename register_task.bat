@echo off
chcp 65001 >nul
REM 右键本文件 →「以管理员身份运行」,即可注册"收盘后日常"计划任务。
REM 工作日 18:00 跑 daily.bat(补鲜 + 打分/信号);仅当前用户登录时运行。
schtasks /Create /TN "feifei-daily" /TR "C:\Users\Administrator\feifei_Ashare\daily.bat" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /F
echo.
if %errorlevel%==0 (
  echo [OK] 已注册 feifei-daily,工作日 18:00 运行。
  echo 查询: schtasks /Query /TN feifei-daily
  echo 手动测试一次: schtasks /Run /TN feifei-daily
  echo 删除: schtasks /Delete /TN feifei-daily /F
) else (
  echo [失败] 请确认本窗口是"管理员"权限。
)
pause
