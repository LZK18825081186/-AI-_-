@echo off
chcp 65001 >nul
title 闲鱼AI客服 — 一键部署

cd /d "%~dp0"

:: ============================================
:: 第一阶段：环境检测
:: ============================================
echo.
echo =============================================
echo   闲鱼COS服租赁 AI客服 — 一键部署
echo =============================================
echo.
echo [1/6] 检测 Docker Desktop...
docker --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未检测到 Docker Desktop！
    echo.
    echo 请先安装 Docker Desktop:
    echo   https://www.docker.com/products/docker-desktop/
    echo.
    pause
    exit /b 1
)
echo   ✅ Docker 已就绪

:: 检查 docker 是否正在运行
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [错误] Docker Desktop 未运行！请先启动 Docker Desktop
    pause
    exit /b 1
)
echo   ✅ Docker 正在运行

:: ============================================
:: 第二阶段：配置文件
:: ============================================
echo.
echo [2/6] 检查配置文件...

:: 如果 .env 不存在，从模板创建
if not exist ".env" (
    echo   未找到 .env，从模板创建...
    copy .env.example .env >nul 2>&1
    echo   ✅ .env 已创建，请编辑填入 API Key:
    echo.
    echo [重要] 请填入以下必填项后保存关闭:
    echo   - DEEPSEEK_API_KEY    （DeepSeek API 密钥）
    echo   - ADMIN_USERNAME       （管理后台用户名）
    echo   - ADMIN_PASSWORD       （管理后台密码）
    echo   - JWT_SECRET_KEY       （随机密钥）
    echo.
    echo   [可选] 如需远程图片理解模型:
    echo   - LM_STUDIO_URL        （如 http://192.168.1.100:1234）
    echo.
    start notepad .env
    echo   等待编辑完成...（关闭记事本后继续）
    echo.
    :: 等待用户关闭记事本
    :wait_notepad
    tasklist /FI "IMAGENAME eq notepad.exe" 2>NUL | find /I "notepad.exe" >NUL
    if %ERRORLEVEL% EQU 0 (
        timeout /t 1 >nul
        goto wait_notepad
    )
    echo   ✅ .env 已保存
) else (
    echo   ✅ .env 已存在
)

:: ============================================
:: 第三阶段：拉取镜像
:: ============================================
echo.
echo [3/6] 拉取 Docker 镜像...
echo   （首次需要几分钟，请耐心等待）

docker-compose pull 2>&1 | findstr /V "Pulling"
if %ERRORLEVEL% NEQ 0 (
    echo   ⚠️  部分镜像拉取失败，将尝试构建
)
echo   ✅ 镜像就绪

:: ============================================
:: 第四阶段：启动 CookieCloud
:: ============================================
echo.
echo [4/6] 启动内置 CookieCloud 服务...
docker-compose up -d cookiecloud 2>&1 | findstr /V "Creating"
if %ERRORLEVEL% NEQ 0 (
    echo   ⚠️  CookieCloud 启动可能失败
) else (
    echo   ✅ CookieCloud 已启动 (端口 8088)
)

:: ============================================
:: 第五阶段：浏览器引导
:: ============================================
echo.
echo [5/6] 浏览器引导设置...
echo.
echo   即将打开浏览器，请完成以下操作:
echo     1. 安装 CookieCloud Edge 浏览器插件
echo     2. 登录闲鱼 (goofish.com)
echo     3. 完成飞书 CLI 授权
echo.

:: 检查 Edge 浏览器
where msedge >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo   ⚠️  未找到 Edge，尝试安装...
    winget install Microsoft.Edge --accept-package-agreements --accept-source-agreements >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo   [警告] 自动安装 Edge 失败，请手动安装
    )
)

echo.
echo ┌─────────────────────────────────────────┐
echo │                                         │
echo │  现在将在 Edge 中打开以下页面:           │
echo │                                         │
echo │  ① CookieCloud 插件安装页               │
echo │     https://microsoftedge.microsoft.com/  │
echo │     addons/detail/cookiecloud/            │
echo │     （请点击"获取"安装插件）             │
echo │                                         │
echo │  ② 闲鱼登录页                           │
echo │     https://www.goofish.com/              │
echo │     （请用手机闲鱼扫码登录）             │
echo │                                         │
echo └─────────────────────────────────────────┘
echo.
echo   按任意键打开浏览器...
pause >nul

:: 打开 CookieCloud 插件页
start msedge "https://microsoftedge.microsoft.com/addons/detail/cookiecloud/obkehbkfefgbielahbennmkninplmffm" >nul 2>&1

:: 等待 2 秒
timeout /t 2 >nul

:: 打开闲鱼
start msedge "https://www.goofish.com/" >nul 2>&1

echo.
echo   请在浏览器中:
echo   1. 安装 CookieCloud 插件
echo   2. 在 CookieCloud 插件中配置:
echo      - 服务器: http://localhost:8088
echo      - UUID 和密码: 见 .env 文件
echo   3. 登录闲鱼
echo.
echo   完成后按任意键继续...
pause >nul

:: ============================================
:: 第六阶段：启动全部服务
:: ============================================
echo.
echo [6/6] 启动全部服务...
docker-compose up -d 2>&1 | findstr /V "Creating"
if %ERRORLEVEL% NEQ 0 (
    echo   ⚠️  部分服务启动失败
) else (
    echo   ✅ 全部服务已启动
)

:: ============================================
:: 完成
:: ============================================
echo.
echo =============================================
echo   🎉 部署完成！
echo =============================================
echo.
echo   管理后台: http://localhost:8080
echo   停止系统: docker compose down
echo   查看日志: docker compose logs -f
echo.
echo   用户名和密码见 .env 文件
echo.
pause
