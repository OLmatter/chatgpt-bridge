@echo off
REM 一键重启 ChatGPT Bridge 服务(杀掉所有旧 python,只启动一个)
title ChatGPT Bridge Restart

echo [1] 杀掉所有 python 进程...
taskkill /F /IM python3.12.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2] 确认端口释放...
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul && (
    echo    端口仍被占用,等5秒...
    timeout /t 5 /nobreak >nul
) || echo    端口已释放

echo [3] 启动服务...
cd /d "C:\Users\520hh\Desktop\项目\kaggle\The 2026 NeuroGolf Championship\chatgpt_webui"
start "ChatGPT Bridge" python run_all.py
timeout /t 2 /nobreak >nul

echo [4] 启动监控...
start "Bridge Monitor" python monitor.py

echo.
echo 完成!服务+监控已启动。
echo 端口监听数(应为1):
netstat -ano | findstr ":5000" | findstr /c:"LISTENING"
echo.
pause
