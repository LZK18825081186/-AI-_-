# 闲鱼 AI 自动回复系统

基于 WebSocket 实时消息的闲鱼自动化管理平台，支持 AI 智能聊天（DeepSeek V4）、品类感知的回复风格切换、飞书多维表格库存管理、以及虚拟资料自动发货。

[![Docker](https://img.shields.io/badge/Docker-✓-blue)](https://www.docker.com)
[![Python](https://img.shields.io/badge/Python-3.11-green)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 目录

- [功能概览](#功能概览)
- [系统架构](#系统架构)
- [Docker 快速部署](#docker-快速部署)
- [详细部署指南（Windows）](#详细部署指南windows)
- [详细部署指南（Linux/Mac）](#详细部署指南linuxmac)
- [环境变量说明](#环境变量说明)
- [管理后台使用](#管理后台使用)
- [配置 AI 回复](#配置-ai-回复)
- [配置品类自动风格切换](#配置品类自动风格切换)
- [配置飞书知识库](#配置飞书知识库)
- [配置 CookieCloud 自动同步](#配置-cookiecloud-自动同步)
- [配置 Nginx 反向代理（可选）](#配置-nginx-反向代理可选)
- [常见运维操作](#常见运维操作)
- [故障排查](#故障排查)
- [项目结构](#项目结构)

---

## 功能概览

| 功能 | 说明 |
|------|------|
| 🤖 **AI 智能回复** | DeepSeek V4-Flash / V4-Pro 驱动，自动生成闲鱼回复 |
| 🎯 **品类感知风格** | cos服、数码、手办等品类自动匹配不同的话术风格 |
| 📊 **飞书知识库** | 多维表格管理库存，AI 实时读取商品信息 |
| 🖼️ **图片描述** | 本地 VL 模型自动生成实物图描述 |
| 💾 **虚拟资料自动发货** | 买家付款后自动发送网盘链接 |
| 🖥️ **Web 管理面板** | 可视化管理账号、AI 设置、关键词回复 |
| 🔔 **飞书通知** | 投诉/退款自动推送到飞书群 |
| 🍪 **Cookie 自动同步** | 配合 CookieCloud 插件，免手动粘贴 Cookie |
| 🐳 **Docker 一键部署** | Docker Compose 一键启动所有服务 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker 宿主机                           │
│                                                             │
│  ┌─────────────────────────┐  ┌─────────────────────────┐  │
│  │   xianyu-app (容器)      │  │  CookieCloud (容器)      │  │
│  │                         │  │  端口 8088              │  │
│  │  ┌───────────────────┐  │  │                         │  │
│  │  │ WebSocket 连接     │──┼──┼──► 闲鱼服务器             │  │
│  │  │ (XianyuAutoAsync)  │  │  │                         │  │
│  │  └───────────────────┘  │  │  ┌───────────────────┐   │  │
│  │  ┌───────────────────┐  │  │  │ CookieCloud 插件   │   │  │
│  │  │ AI 回复引擎        │  │  │  │ (Edge 浏览器)      │   │  │
│  │  │ (ai_reply_engine)  │  │  │  └───────────────────┘   │  │
│  │  └───────────────────┘  │  └─────────────────────────┘  │
│  │  ┌───────────────────┐  │                                │
│  │  │ Web 管理后端       │  │  ┌─────────────────────────┐  │
│  │  │ (reply_server)    │──┼──┼──► DeepSeek API            │  │
│  │  │ 端口 8080          │  │  │  (api.deepseek.com)       │  │
│  │  └───────────────────┘  │  └─────────────────────────┘  │
│  │  ┌───────────────────┐  │                                │
│  │  │ SQLite 数据库      │  │  ┌─────────────────────────┐  │
│  │  │ (商品/账号/设置)    │  │  │ 飞书多维表格              │  │
│  │  └───────────────────┘  │  │  (库存知识库)             │  │
│  └─────────────────────────┘  └─────────────────────────┘  │
│                                                             │
│  可选: Nginx (端口 80/443) 反向代理 + SSL                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Docker 快速部署

### 前提条件

| 软件 | 版本要求 | 获取方式 |
|------|---------|---------|
| Docker Desktop | 最新版 | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Edge / Chrome 浏览器 | 最新版 | 安装 CookieCloud 插件用 |
| 闲鱼卖家账号 | 已注册 | 至少一个在售商品的账号 |
| DeepSeek API Key | 有效 | [platform.deepseek.com](https://platform.deepseek.com) |

### 三步部署

```bash
# 1. 克隆项目
git clone https://github.com/LZK18825081186/-AI-_-
cd -AI-_-

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少填入：DEEPSEEK_API_KEY、ADMIN_PASSWORD

# 3. 启动
docker compose up -d
```

启动后访问 `http://localhost:8080`，使用 `.env` 中配置的管理员账号登录。

> **中国用户加速**：如果 Docker 拉取镜像慢，使用国内镜像源：
>
> ```bash
> docker compose -f docker-compose-cn.yml up -d
> ```

---

## 详细部署指南（Windows）

### 第一步：安装 Docker Desktop

1. 访问 [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) 下载 Docker Desktop
2. 安装完成后启动，等待状态栏显示 "Docker Desktop is running"
3. 打开 PowerShell 或 CMD，验证安装：
   ```powershell
   docker --version
   docker compose version
   ```

### 第二步：克隆项目

```powershell
# 进入你想存放项目的目录
cd D:\Projects

# 克隆
git clone https://github.com/LZK18825081186/-AI-_-
cd -AI-_-
```

> 如果没有安装 Git，可以：
> 1. 访问 [git-scm.com](https://git-scm.com) 下载安装
> 2. 或直接下载项目 ZIP 包解压

### 第三步：配置环境变量

```powershell
# 复制环境变量模板
copy .env.example .env
```

用记事本（或 VS Code）打开 `.env` 文件，至少填写以下内容：

```env
# ===== 必填项 =====

# DeepSeek API Key（用于 AI 回复）
# 前往 https://platform.deepseek.com 注册获取
DEEPSEEK_API_KEY=sk-你的完整API密钥

# 管理后台登录密码（请修改为强密码）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的登录密码
JWT_SECRET_KEY=生成一段随机字符串

# ===== 飞书多维表格（如需库存知识库）=====
# 不配置的话 AI 无法知道商品库存信息
FEISHU_BASE_TOKEN=你的飞书BaseToken
FEISHU_TABLE_ID=你的飞书TableID

# ===== CookieCloud（可选，推荐）=====
# 配置后可自动同步闲鱼 Cookie，无需手动粘贴
COOKIE_CLOUD_HOST=http://cookiecloud:8088
COOKIE_CLOUD_UUID=你的UUID
COOKIE_CLOUD_PASSWORD=你的CookieCloud密码
```

> **安全提示**：`.env` 文件包含敏感信息，已自动加入 `.gitignore`，**不会**被提交到 GitHub。

### 第四步：启动服务

```powershell
# 首次启动（需要拉取镜像，耗时约 5-10 分钟，取决于网络）
docker compose up -d

# 查看启动日志
docker compose logs -f

# 等待显示以下内容后即可：
# "管理后台: http://localhost:8080"
# "主程序启动完成，保持运行..."
```

### 第五步：登录管理后台

1. 打开浏览器访问 `http://localhost:8080`
2. 使用 `.env` 中配置的 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 登录
3. 进入「账号管理」页面，添加你的闲鱼卖家账号

---

## 详细部署指南（Linux/Mac）

```bash
# 克隆项目
git clone https://github.com/LZK18825081186/-AI-_-
cd -AI-_-

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key 等配置
vim .env

# 启动（如果需要 sudo 权限则加 sudo）
docker compose up -d

# 查看日志
docker compose logs -f
```

---

## 环境变量说明

### 核心配置

| 环境变量 | 必填 | 默认值 | 说明 |
|---------|------|--------|------|
| `DEEPSEEK_API_KEY` | ✅ | — | DeepSeek API 密钥 |
| `ADMIN_USERNAME` | ✅ | `admin` | 管理后台用户名 |
| `ADMIN_PASSWORD` | ✅ | `admin123` | 管理后台密码（**务必修改**） |
| `JWT_SECRET_KEY` | ✅ | — | JWT 签名密钥（生成随机字符串） |

### 飞书知识库

| 环境变量 | 必填 | 说明 |
|---------|------|------|
| `FEISHU_BASE_TOKEN` | 如用飞书 | 飞书多维表格的 Base Token |
| `FEISHU_TABLE_ID` | 如用飞书 | 飞书多维表格的 Table ID |

### CookieCloud

| 环境变量 | 必填 | 默认值 | 说明 |
|---------|------|--------|------|
| `COOKIE_CLOUD_HOST` | 如用 CookieCloud | `http://cookiecloud:8088` | CookieCloud 服务地址 |
| `COOKIE_CLOUD_UUID` | ✅ | — | CookieCloud 插件中的 UUID |
| `COOKIE_CLOUD_PASSWORD` | ✅ | — | CookieCloud 插件中的密码 |
| `COOKIE_CLOUD_REFRESH_SECONDS` | 否 | `1800` | Cookie 刷新间隔（秒） |
| `COOKIE_CLOUD_COOKIE_ID` | 否 | `default` | 默认同步到的账号 ID |

### 系统调优

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `LOG_LEVEL` | `INFO` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `TZ` | `Asia/Shanghai` | 时区 |
| `MEMORY_LIMIT` | `1G` | 容器内存上限 |
| `CPU_LIMIT` | `1.0` | 容器 CPU 核心数上限 |
| `WEB_PORT` | `8080` | Web 管理面板端口 |

---

## 管理后台使用

### 首次登录

访问 `http://localhost:8080`，使用 `.env` 中配置的账号密码登录。

### 添加闲鱼账号

1. 进入「账号管理」
2. 点击「添加账号」
3. 输入账号 ID 和 Cookie
   - 通过 CookieCloud 自动同步 → 无需手动操作
   - 手动添加 → 从浏览器开发者工具复制 Cookie 字符串

### AI 回复设置

进入「AI 回复设置」页面：

| 字段 | 推荐值 |
|------|--------|
| AI 模型 | `deepseek-v4-flash`（推荐）或 `deepseek-v4-pro` |
| API 地址 | `https://api.deepseek.com` |
| API Key | 你的 DeepSeek 密钥 |
| 回复风格 | 下方会详细讲解 |

### 砍价/折扣设置

| 字段 | 说明 |
|------|------|
| 最大优惠百分比 | 最多给买家优惠 % |
| 最大优惠金额 | 最多给买家优惠多少元 |
| 最大议价轮数 | 最多能砍几轮 |
| 砍价底价 | 低于此价格直接拒绝 |

---

## 配置 AI 回复

### 基础配置

系统启动后，在管理后台的「AI 回复设置」页面填入：

```yaml
API 地址: https://api.deepseek.com
API Key: sk-你的DeepSeek密钥
模型名称: deepseek-v4-flash   # 2026年4月起推荐 V4 系列
```

> **模型选择说明**：
> - `deepseek-v4-flash`：速度快，性价比高，推荐日常使用
> - `deepseek-v4-pro`：能力更强，适合复杂对话场景
> - `deepseek-chat` / `deepseek-reasoner`：旧模型名，2026年7月24日已停用

### 品类感知的回复风格

系统支持根据商品标题**自动识别品类**并切换回复风格。

现已内置以下品类风格：

| 品类 | 匹配关键词 | 风格特征 |
|------|-----------|---------|
| 👗 **cos服** | cos, cosplay, 汉服, JK, 洛丽塔, 角色... | 穿搭达人，懂尺码推荐，二次元用语 |
| 📱 **数码** | 手机, 电脑, 耳机, switch, ps5... | 技术范，参数控，价格透明 |
| 🎬 **手办/玩具** | 手办, 盲盒, 高达, 乐高, 潮玩... | 圈内人，品相党，稀缺话术 |
| 👟 **鞋包** | 包, 鞋, 运动鞋, 双肩... | 正品导向，成色论价 |
| 💻 **资料** | 资料, 题库, 笔记, 课程, PDF... | 虚拟资料专用，自动发货指引 |

**风格在管理后台的「AI 回复风格」文本框里配置**，格式为 JSON：

```json
{
  "general": "通用回复风格文本...",
  "clothing": {
    "general": "服装类一般咨询风格...",
    "price": "服装类砍价风格..."
  },
  "electronics": {
    "general": "数码类风格..."
  }
}
```

如果不提供品类定制风格，系统会自动使用 `general` 作为兜底。

---

## 配置飞书知识库

### 创建多维表格

在飞书「零花钱」群中创建一个多维表格，包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| 角色名称 | 文本 | 如：银狼 |
| 作品来源 | 文本 | 如：崩坏星穹铁道 |
| 码数 | 单选 | M / L / 均码 |
| 总库存 | 数字 | 共几套 |
| 已租出 | 数字 | 已租几套 |
| 状态 | 单选 | 可租 / 已租出 / 待归还 / 维护中 |
| 租期价格 | 文本 | 如：350元/3天 |
| 押金 | 数字 | 押金金额 |
| 日租金底价 | 数字 | 砍价底线 |
| 配件清单 | 文本 | 包含哪些配件 |
| 实物图 | 附件 | cos 服照片（支持多张） |
| 图片描述 | 文本 | AI 自动生成，只读 |
| 描述已审核 | 复选框 | 人工确认后买家可见 |

### 获取凭证

1. 打开飞书多维表格 → 右上角「...」→ 「高级权限」→ 复制 Base Token
2. 在表格 URL 中获取 Table ID（`/base/{BASE_TOKEN}/table/{TABLE_ID}`）
3. 填入 `.env`：
   ```env
   FEISHU_BASE_TOKEN=你的BaseToken
   FEISHU_TABLE_ID=你的TableID
   ```

---

## 配置 CookieCloud 自动同步

CookieCloud 是一款浏览器插件，能自动将闲鱼 Cookie 同步到服务器，**无需手动复制粘贴 Cookie**。

### 安装 CookieCloud 插件

**Edge 浏览器**：
1. 打开 Edge 扩展管理（`edge://extensions/`）
2. 开启「开发人员模式」
3. 访问 Chrome 网上应用店，搜索 "CookieCloud" 安装
4. 或访问 [CookieCloud GitHub](https://github.com/easychen/cookie-cloud) 手动安装

### 配置插件

1. 点击浏览器工具栏的 CookieCloud 图标
2. 设置：
   - **服务器地址**：`http://localhost:8088`（本地部署）或你的公网地址
   - **UUID**：自定义，如 `my-xianyu`
   - **密码**：设置一个强密码
3. 将 UUID 和密码填入 `.env` 文件

### 验证同步

1. 访问 [goofish.com](https://www.goofish.com) 登录闲鱼
2. 确保保持在登录状态
3. 系统日志中应显示 Cookie 同步成功的信息

---

## 配置 Nginx 反向代理（可选）

如需通过域名访问管理后台，或配置 HTTPS，可以使用自带的 Nginx 支持。

```bash
# 创建 Nginx 配置目录
mkdir -p nginx/ssl

# 将 SSL 证书放入 nginx/ssl/
# 编辑 nginx/nginx.conf 配置域名

# 启动（带上 with-nginx profile）
docker compose --profile with-nginx up -d
```

---

## 常见运维操作

### 查看日志

```bash
# 实时查看所有日志
docker compose logs -f

# 查看特定服务的日志
docker compose logs -f xianyu-app

# 查看最近 50 行
docker compose logs --tail=50
```

### 重启

```bash
# 重启所有服务
docker compose restart

# 仅重启应用
docker compose restart xianyu-app
```

### 更新

```bash
# 拉取最新代码
git pull

# 如果有新依赖或新镜像
docker compose build --no-cache
docker compose up -d
```

### 停止

```bash
# 停止所有服务
docker compose down

# 停止并删除数据卷（⚠️ 会清空数据库）
docker compose down -v
```

### 备份数据库

```bash
# 手动备份
docker exec xianyu-auto-reply cp /app/data/xianyu_data.db /app/backups/xianyu_data_$(date +%Y%m%d_%H%M%S).db

# 查看备份列表
docker exec xianyu-auto-reply ls -la /app/backups/
```

### 修改配置后续效

修改 `.env` 文件后需要重启才能生效：

```bash
# 方式一：仅重启容器
docker compose up -d

# 方式二：重建 + 重启（如果改了 Dockerfile）
docker compose up -d --build
```

---

## 故障排查

### 容器启动失败

```bash
# 查看详细错误
docker compose logs xianyu-app

# 常见原因：
# 1. 端口被占用 → 修改 .env 中的 WEB_PORT
# 2. 内存不足 → 降低 MEMORY_LIMIT
# 3. 数据库损坏 → 删除 data/ 目录后重启
```

### AI 回复返回 "Not Found"

1. 确认 DeepSeek API Key 有效
2. 检查 API 地址：应为 `https://api.deepseek.com`
3. 检查模型名：2026年7月起应使用 `deepseek-v4-flash` 或 `deepseek-v4-pro`
4. 在管理后台点「测试 AI 回复」验证

### Cookie 过期

```bash
# 检查当前 Cookie 状态
docker compose logs xianyu-app | grep -i "cookie\|token"

# 重新登录闲鱼并等待 CookieCloud 同步
# 或手动在管理后台更新 Cookie
```

### 容器健康检查失败

```bash
# 查看健康检查状态
docker inspect --format='{{json .State.Health}}' xianyu-auto-reply

# 可能原因：数据库损坏、端口冲突、内存不足
```

### 其他常见问题

| 问题 | 解决方案 |
|------|---------|
| Docker 拉取镜像慢 | 使用 `docker-compose-cn.yml`（国内镜像源） |
| 管理后台无法访问 | 检查防火墙是否放行 8080 端口 |
| AI 回复全是默认话术 | 检查是否已开启 AI 自动回复开关 |
| 飞书数据未同步 | 检查 FEISHU_BASE_TOKEN 和 TABLE_ID 是否正确 |
| 中文乱码 | 确保终端/Shell 支持 UTF-8 |

---

## 项目结构

```
.
├── Start.py                     # 启动入口
├── Dockerfile                   # Docker 构建文件
├── docker-compose.yml           # Docker Compose 编排
├── docker-compose-cn.yml        # 国内镜像源版
├── entrypoint.sh                # 容器启动脚本
├── requirements.txt             # Python 依赖
├── .env.example                 # 环境变量模板
├── .gitignore                   # Git 忽略规则
│
├── reply_server.py              # Web 管理后端 API
├── ai_reply_engine.py           # AI 回复引擎 + 品类风格切换
├── cookie_manager.py            # Cookie 管理
├── db_manager.py                # SQLite 数据库管理
├── ai_conversation_service.py   # AI 对话历史管理
├── XianyuAutoAsync.py           # 核心：WebSocket 闲鱼客户端
├── global_config.yml            # 全局默认配置
│
├── static/                      # 前端静态文件
│   ├── index.html               # 管理后台主页面
│   ├── login.html               # 登录页面
│   ├── css/                     # 样式文件
│   └── js/
│       └── app.js               # 前端主要逻辑
│
├── utils/                       # 工具模块
│   ├── qr_login.py              # 扫码登录
│   ├── image_utils.py           # 图片处理
│   ├── xianyu_utils.py          # 闲鱼工具函数
│   └── message_handler.py       # 消息处理
│
├── data/                        # 运行时数据（不提交 Git）
│   └── xianyu_data.db           # SQLite 数据库
│
├── logs/                        # 运行日志（不提交 Git）
├── backups/                     # 数据库备份（不提交 Git）
│
└── nginx/                       # Nginx 反向代理（可选）
    └── nginx.conf
```

## License

MIT

---

*更多问题请提交 [GitHub Issues](https://github.com/LZK18825081186/-AI-_-/issues)*
