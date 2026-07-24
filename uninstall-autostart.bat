@echo off
chcp 65001 >nul
title 卸载开机启动

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo ================================
echo  卸载闲鱼AI客服开机自启
echo ================================
echo.

if exist "%STARTUP_DIR%\闲鱼AI客服.vbs" (
    del /F /Q "%STARTUP_DIR%\闲鱼AI客服.vbs" >nul 2>&1
    echo ✅ 开机启动已卸载！
    echo.
    echo 下次开机时，闲鱼AI客服不会再自动启动
) else (
    echo ⚠️  未找到开机启动项，可能已经卸载或从未安装
)

echo.
echo 如需重新启用，请运行: install-autostart.bat
echo.
pause
