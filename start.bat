@echo off
chcp 65001 >nul
title 闲鱼AI客服

cd /d "%~dp0"

echo ================================
echo  闲鱼AI自动客服 - 正在启动...
echo ================================
echo.
echo 启动后可访问管理后台: http://localhost:8080
echo 关闭此窗口将停止服务
echo.

python Start.py

pause
