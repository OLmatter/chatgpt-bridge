@echo off
chcp 65001 >nul
title ChatGPT Bridge 启动器
echo ========================================
echo   ChatGPT Bridge 一键启动
echo ========================================
echo.
echo [1/3] 杀掉旧进程(避免端口冲突)...
taskkill /F /IM python3.12.exe >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/3] 启动后端服务...
cd /d "C:\Users\520hh\Desktop\项目\kaggle\The 2026 NeuroGolf Championship\chatgpt_webui"
start "Bridge Backend" /min python run_all.py
timeout /t 3 /nobreak >nul

echo [3/3] 启动监控窗口...
start "Bridge Monitor" python monitor.py

echo.
echo ========================================
echo 完成!
echo   后端: http://127.0.0.1:5000 (最小化运行)
echo   监控: 桌面上的小窗口
echo ========================================
echo.
echo 端口监听数(应为1):
netstat -ano | findstr ":5000" | findstr /c:"LISTENING"
echo.
echo 这个窗口可以关了,后端和监控会继续运行。
pause
