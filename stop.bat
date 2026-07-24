@echo off
chcp 65001 >nul
title 停止闲鱼AI客服

cd /d "%~dp0"

echo ================================
echo  正在停止闲鱼AI客服...
echo ================================
echo.

docker-compose down 2>nul
if %ERRORLEVEL% EQU 0 (
    echo ✅ 已停止所有服务
) else (
    echo ⚠️  Docker可能未运行，无需停止
)

echo.
pause
