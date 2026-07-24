#!/bin/bash
# 闲鱼AI客服 — Linux/macOS 一键部署脚本
set -e

cd "$(dirname "$0")"

# 检测操作系统
OS_TYPE="linux"
[ "$(uname -s)" = "Darwin" ] && OS_TYPE="macos"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok() { echo -e "${GREEN}  ✅ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
err() { echo -e "${RED}[错误] $1${NC}"; exit 1; }

echo
echo "============================================="
echo "  闲鱼COS服租赁 AI客服 — 一键部署 ($OS_TYPE)"
echo "============================================="

# [1/6] 检查 Docker
echo; echo "[1/6] 检测 Docker..."
docker --version >/dev/null 2>&1 || err "请先安装 Docker: curl -fsSL https://get.docker.com | sh"
ok "Docker 已就绪"
docker info >/dev/null 2>&1 || err "Docker 未运行，请先启动 Docker 服务"
ok "Docker 正在运行"

# [2/6] 配置文件
echo; echo "[2/6] 检查配置文件..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    ok ".env 已创建"
    echo
    echo "  [重要] 请编辑 .env 填入必填项后重新运行本脚本:"
    echo "    nano .env"
    echo
    echo "  必填: DEEPSEEK_API_KEY / ADMIN_USERNAME / ADMIN_PASSWORD / JWT_SECRET_KEY"
    echo "  可选: LM_STUDIO_URL (远程图片理解模型地址)"
    exit 0
else
    ok ".env 已存在"
fi

# [3/6] 拉取镜像
echo; echo "[3/6] 拉取 Docker 镜像..."
docker-compose pull 2>/dev/null || warn "部分镜像拉取失败，将尝试构建"
ok "镜像就绪"

# [4/6] 启动 CookieCloud
echo; echo "[4/6] 启动内置 CookieCloud..."
docker-compose up -d cookiecloud 2>/dev/null && ok "CookieCloud 已启动 (端口 8088)" || warn "CookieCloud 启动失败"

# [5/6] 浏览器引导
echo; echo "[5/6] 浏览器引导..."
echo
echo "  ┌─────────────────────────────────────────┐"
echo "  │  请手动在浏览器中完成以下操作:            │"
echo "  │                                         │"
echo "  │  ① 安装 CookieCloud 浏览器插件          │"
echo "  │  ② 配置插件服务器: http://localhost:8088 │"
echo "  │  ③ 登录闲鱼 goofish.com                 │"
echo "  │                                         │"
echo "  └─────────────────────────────────────────┘"
echo
read -p "  完成后按 Enter 继续..." _

# [6/6] 启动全部服务
echo; echo "[6/6] 启动全部服务..."
docker-compose up -d 2>/dev/null && ok "全部服务已启动" || warn "部分服务启动失败"

# 开机自启
echo
STARTUP_MODE=$(sed -n 's/^STARTUP_MODE=//p' .env 2>/dev/null || echo "manual")
if [ "$STARTUP_MODE" = "auto" ]; then
    if [ "$OS_TYPE" = "macos" ]; then
        # macOS: 创建 LaunchAgent
        PLIST="$HOME/Library/LaunchAgents/com.xianyu.ai.plist"
        if [ ! -f "$PLIST" ]; then
            cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.xianyu.ai</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/docker-compose</string>
        <string>up</string>
        <string>-d</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$(pwd)</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
EOF
            launchctl load "$PLIST" 2>/dev/null
            ok "开机自启已安装 (launchd)"
        fi
    else
        # Linux: systemd
        SVC_FILE="/etc/systemd/system/xianyu-ai.service"
        if [ ! -f "$SVC_FILE" ]; then
            sudo tee "$SVC_FILE" > /dev/null <<EOF
[Unit]
Description=闲鱼AI客服
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$(pwd)
ExecStart=/usr/bin/docker-compose up -d
ExecStop=/usr/bin/docker-compose down
User=root

[Install]
WantedBy=multi-user.target
EOF
            sudo systemctl daemon-reload
            sudo systemctl enable xianyu-ai 2>/dev/null
            ok "开机自启已安装 (systemd)"
        fi
    fi
else
    warn "开机自启未启用 (STARTUP_MODE=manual)"
fi

echo
echo "============================================="
echo "  🎉 部署完成！"
echo "============================================="
echo
echo "  管理后台: http://localhost:8080"
echo "  停止系统: docker-compose down"
echo "  查看日志: docker-compose logs -f"
echo
