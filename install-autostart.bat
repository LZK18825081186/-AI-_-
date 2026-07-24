@echo off
chcp 65001 >nul
title 安装开机启动

cd /d "%~dp0"

echo ================================
echo  安装闲鱼AI客服开机自启
echo ================================
echo.

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_BAT=%~dp0start.bat"
set "VBS_FILE=%TEMP%\xianyu_launcher.vbs"

REM 创建隐藏窗口的VBS启动器
echo Set WshShell = CreateObject("WScript.Shell") > "%VBS_FILE%"
echo WshShell.Run """%TARGET_BAT%""", 0, False >> "%VBS_FILE%"

REM 复制VBS到启动文件夹
copy /Y "%VBS_FILE%" "%STARTUP_DIR%\闲鱼AI客服.vbs" >nul 2>&1

if %ERRORLEVEL% EQU 0 (
    echo ✅ 开机启动已安装！
    echo.
    echo 下次开机时，闲鱼AI客服会自动在后台启动
    echo VBS文件位置: %STARTUP_DIR%\闲鱼AI客服.vbs
) else (
    echo ❌ 安装失败，请以管理员身份运行此脚本
)

echo.
echo 如需取消，请运行: uninstall-autostart.bat
echo.
pause
