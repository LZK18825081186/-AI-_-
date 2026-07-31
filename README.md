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
- [Linux Mint 生产部署（HP T730 / 30GB 硬盘）](#linux-mint-生产部署hp-t730--30gb-硬盘)
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
| 🖼️ **图片描述** | 阿里云百炼千问 VL 远程 API 生成实物图描述，不使用本地 VL 模型兜底 |
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

本节的 `.env.example` + `docker-compose.yml` 用于开发机和普通 Compose 部署，会在目标机本地构建镜像。原生 Linux 首次运行普通 Compose 时应执行 `./setup.sh`，由脚本为固定 UID/GID `10001:10001` 准备持久化和上传目录；不要直接运行裸 `docker compose up`。HP T730 / Linux Mint 生产环境不要使用本节命令，必须以 [Linux Mint 生产部署](#linux-mint-生产部署hp-t730--30gb-硬盘)中的 `.env.linux.example` + `docker-compose.linux.yml` + `docker-deploy.sh` 为准。

### 前提条件

| 软件 | 版本要求 | 获取方式 |
|------|---------|---------|
| Docker Desktop | 最新版 | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Edge / Chrome 浏览器 | 最新版 | 安装 CookieCloud 插件用 |
| 闲鱼卖家账号 | 已注册 | 至少一个在售商品的账号 |
| DeepSeek API Key | 有效 | 部署后在管理后台保存到默认或账号级 AI 设置 |

### 三步部署

```bash
# 1. 克隆项目
git clone https://github.com/LZK18825081186/-AI-_-
cd -AI-_-

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少替换管理员密码、内部 API 密钥及所用外部服务凭证

# 3. 启动
# Windows / macOS Docker Desktop：
docker compose up -d
# 原生 Linux 普通部署（无需依赖 Git 执行位）：
bash ./setup.sh
```

启动后访问 `http://localhost:8080`，使用 `.env` 中配置的管理员账号登录。原生 Linux 不应跳过 `setup.sh` 的 UID/GID 目录初始化。

> **中国用户加速**：如果 Docker 拉取镜像慢，Windows/macOS 可使用国内镜像源；原生 Linux 请先由 `setup.sh` 初始化目录权限，再用同一 Compose 文件启动：
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

# Linux 部署脚本的 DeepSeek 凭证校验项；仅设置此变量不会启用 AI 回复
DEEPSEEK_API_KEY=sk-你的完整API密钥

# 商品图片理解使用阿里云百炼千问远程 API
DASHSCOPE_API_KEY=sk-你的百炼API密钥

# 管理后台登录密码（请修改为强密码）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的登录密码
XIANYU_MESSAGE_API_KEY=至少32个随机字节

# ===== 飞书群聊库存（库存回答的唯一事实来源）=====
# 桥接服务先验证指定群消息仍指向该 Base，再读取实时记录
FEISHU_BASE_TOKEN=你的飞书BaseToken
FEISHU_TABLE_ID=你的飞书TableID
FEISHU_INVENTORY_CHAT_ID=发布库存链接的群ID
FEISHU_INVENTORY_SOURCE_MESSAGE_ID=发布库存链接的消息ID
FEISHU_INVENTORY_BRIDGE_TOKEN=高强度随机字符串
FEISHU_INVENTORY_BRIDGE_URL=http://host.docker.internal:8765

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

## Linux Mint 生产部署（HP T730 / 30GB 硬盘）

Linux 生产环境只以 `.env.linux.example`、`docker-compose.linux.yml` 和 `docker-deploy.sh` 为部署合同，采用三个容器，三者复用同一个预构建 `linux/amd64` 应用镜像层：

- `xianyu-app`：闲鱼 WebSocket、AI 回复和管理后台。
- `feishu-inventory-bridge`：以飞书企业自建应用身份验证群消息并只读 Base。
- `maintenance`：每天在线备份 SQLite 与上传图片，并检查剩余磁盘空间。

三个应用容器固定使用 `10001:10001` 非 root 身份，丢弃全部 Linux capabilities，限制 PID，并以只读根文件系统运行；仅持久化目录与受限 `tmpfs` 可写。CookieCloud 与 Nginx 默认不启动，分别通过 `cookiecloud`、`nginx` profile 按需启用。目标机不执行 Chromium 镜像构建，避免 Docker 构建缓存占满 30GB 硬盘。

生产路径固定为 `DB_PATH=/app/data/xianyu_data.db`、`AI_CACHE_DIR=/app/data`，主应用和维护容器必须共享同一 SQLite 文件。针对 T730，建议先采用以下保守起始值：单次请求超时 60 秒、失败后最多重试 1 次、压缩后单图最多 3 MiB、最长边 1600 像素、每个商品最多处理 4 张图。对应参数位于 `.env.linux.example` 的 `QWEN_*` 配置中；这些数值不是 T730 实机压测结论，部署后应根据内存、响应延迟和失败率实测调整，也不要直接用普通 `.env.example` 的开发机默认值替代。

### 一次性准备

1. 在飞书开放平台创建企业自建应用并启用机器人能力。
2. 为应用申请并发布以下权限：
   - `im:message:readonly`
   - `im:message.group_msg`
   - `base:record:retrieve`
   - 若需要从 Base 首次下载实物图，再授予 `bitable:app:readonly`。
3. 将机器人加入权威库存群。
4. 在库存 Base 右上角选择“更多”→“添加文档应用”，添加该应用并授予读取记录所需权限；若开启高级权限，按飞书要求授予应用可管理权限，否则查询可能返回空记录。
5. 在开发机或 CI 构建并发布预构建镜像：
   ```bash
   docker buildx build --platform linux/amd64 \
     -t REGISTRY/xianyu-auto-reply:VERSION --push .
   ```
   无镜像仓库时可导出：
   ```bash
   docker save REGISTRY/xianyu-auto-reply:VERSION \
     -o xianyu-auto-reply-linux-amd64.tar
   sha256sum xianyu-auto-reply-linux-amd64.tar
   ```

### 目标机部署

```bash
chmod +x docker-deploy.sh
sudo ./docker-deploy.sh init
# 编辑 .env，替换所有 CHANGE_ME 值
sudo ./docker-deploy.sh validate
sudo ./docker-deploy.sh deploy
```

如果使用离线镜像包，把 `xianyu-auto-reply-linux-amd64.tar` 放在项目根目录，并把开发机输出的摘要填入 `.env` 的 `IMAGE_ARCHIVE_SHA256`。脚本会先校验 SHA256，再执行 `docker load`，并确认归档实际提供与 `XIANYU_IMAGE` 完全一致的 tag 且镜像平台是 `linux/amd64`；没有归档时才拉取镜像。

生产运维命令统一通过 `sudo ./docker-deploy.sh ...` 运行，使私有持久化目录始终归 `10001:10001` 所有。部署脚本会验证 Docker Compose v2、x86_64 架构、至少 6GiB 空闲空间、目录所有权、必填部署项和 Compose 配置；部署或健康检查还会检查飞书群消息来源、实时 Base 记录、主应用健康状态以及磁盘阈值。任何飞书验证失败都会阻止库存事实回复，固定转人工。这些检查是脚本预期行为，仍需在实际目标机完成部署验收。

常用命令：

```bash
sudo ./docker-deploy.sh health
sudo ./docker-deploy.sh status
sudo ./docker-deploy.sh backup
sudo ./docker-deploy.sh restore backups/xianyu_data_YYYYMMDD_HHMMSS.tar.gz
sudo ./docker-deploy.sh logs xianyu-app
sudo ./docker-deploy.sh update
```

默认管理后台只绑定 `127.0.0.1:8080`。需要局域网访问时再将 `.env` 中 `WEB_BIND_ADDRESS` 改为服务器局域网地址，并同时配置主机防火墙；不建议直接暴露到公网。

首次容器启动后，仍需在管理后台录入闲鱼卖家账号 Cookie，并为默认设置或每个账号保存 DeepSeek API 地址、API Key、模型和“启用 AI”状态。`DEEPSEEK_API_KEY` 当前只是 Linux 部署脚本的必填校验项，不会自动写入用户级 AI 设置，因此仅填写 `.env` 不会启用 AI 回复。完成首次配置后，日常更新运行 `sudo ./docker-deploy.sh update`；更新前会备份数据库、上传图片和运行配置，并记录当前容器镜像 ID。新版本镜像准备、启动或健康检查失败时，脚本会尝试恢复旧镜像；这属于镜像回滚，与 `restore <archive>` 的数据恢复是两套独立流程。30GB 系统盘应保持至少 6GiB 可用，不要在目标机使用 `docker compose build`；确认更新稳定后可按需执行 `docker image prune`，不要使用会删除在用数据的 volume 清理命令。

---

## 环境变量说明

### 核心配置

| 环境变量 | 必填 | 默认值 | 说明 |
|---------|------|--------|------|
| `DEEPSEEK_API_KEY` | Linux 部署校验 | — | 部署脚本要求的 DeepSeek 凭证；AI 回复仍需在后台保存用户级设置 |
| `DASHSCOPE_API_KEY` | 图片理解 | — | 阿里云百炼千问 API 密钥 |
| `ADMIN_USERNAME` | 是 | 无 | 管理后台用户名，必须在 `.env` 中显式配置 |
| `ADMIN_PASSWORD` | 是 | 无 | 管理后台强密码，禁止使用默认值或占位值 |
| `XIANYU_MESSAGE_API_KEY` | 是 | — | 内部回复和外部发消息 API 共用密钥，至少 32 个随机字节 |

### 飞书知识库

| 环境变量 | 必填 | 说明 |
|---------|------|------|
| `FEISHU_AUTH_MODE` | 是 | Linux 生产使用 `bot`；Windows 兼容模式使用 `user_cli` |
| `FEISHU_APP_ID` | bot 模式 | 飞书企业自建应用 ID |
| `FEISHU_APP_SECRET` | bot 模式 | 飞书企业自建应用密钥 |
| `FEISHU_BASE_TOKEN` | 是 | 群消息所发布多维表格的 Base Token |
| `FEISHU_TABLE_ID` | 是 | 库存数据表的 Table ID |
| `FEISHU_INVENTORY_CHAT_ID` | 是 | 发布库存链接的群 ID |
| `FEISHU_INVENTORY_SOURCE_MESSAGE_ID` | 是 | 发布库存 Base 链接的消息 ID |
| `FEISHU_INVENTORY_BRIDGE_TOKEN` | 是 | 应用访问内部只读桥接的随机令牌，至少 32 字符 |
| `FEISHU_INVENTORY_BRIDGE_URL` | 否 | Linux Compose 内部地址为 `http://feishu-inventory-bridge:8765` |

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
| `MEMORY_LIMIT` | `1536m` | 主应用容器内存上限，可按开发机资源调整 |
| `CPU_LIMIT` | `1.50` | 主应用容器 CPU 核心数上限，可按开发机资源调整 |
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

系统启动后，必须在管理后台的「AI 回复设置」页面为默认设置或具体闲鱼账号保存以下内容，并打开 AI 回复开关。运行时从数据库读取这些用户级设置，不会从 `DEEPSEEK_API_KEY` 自动导入：

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

### 配置权威来源和只读桥接

1. 将库存 Base 链接发布到用于维护库存的飞书群，记录群 ID 和该条消息 ID。
2. 从链接中取得 Base Token，并在 Base URL 中取得 Table ID（`/base/{BASE_TOKEN}/table/{TABLE_ID}`）。
3. Linux 生产环境配置 `FEISHU_AUTH_MODE=bot`、飞书自建应用 `APP_ID/APP_SECRET`，并完成上一节列出的机器人入群、API scope 和 Base 文档应用授权。
4. Windows 兼容环境可继续配置 `FEISHU_AUTH_MODE=user_cli`，使用当前用户 `lark-cli auth login` 授权并在宿主机启动桥接。
5. 在 `.env` 填入以下核心配置：
   ```env
   FEISHU_AUTH_MODE=bot
   FEISHU_APP_ID=你的应用ID
   FEISHU_APP_SECRET=你的应用密钥
   FEISHU_BASE_TOKEN=你的BaseToken
   FEISHU_TABLE_ID=你的TableID
   FEISHU_INVENTORY_CHAT_ID=发布库存链接的群ID
   FEISHU_INVENTORY_SOURCE_MESSAGE_ID=发布库存链接的消息ID
   FEISHU_INVENTORY_BRIDGE_TOKEN=至少32字符的随机字符串
   FEISHU_INVENTORY_BRIDGE_URL=http://feishu-inventory-bridge:8765
   ```
6. `./docker-deploy.sh health` 会调用桥接的完整 `--check`，不仅检查端口，还会真实验证群消息和读取 Base 记录。

桥接会在每次库存和媒体请求前重新验证指定群消息及 Base 链接。验证、授权或 Base 读取失败时，库存问题固定转人工，不使用 `knowledge_base.txt` 或其他本地旧库存兜底。媒体代理也只允许下载当前已验证 Base 记录中实际存在的附件 Token。

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

如需通过域名访问管理后台，可以使用 Linux Compose 的 `nginx` profile。现有 `nginx/nginx.conf` 默认只启用 HTTP；放入证书本身不会自动启用 HTTPS，必须先把 HTTPS `server` 块取消注释、设置真实域名，并确认 `cert.pem` 和 `key.pem` 的路径匹配。

```bash
mkdir -p nginx/ssl
# 放入 nginx/ssl/cert.pem 与 nginx/ssl/key.pem，并编辑 nginx/nginx.conf
# 先检查展开配置，再启动正确的 nginx profile
docker compose --env-file .env -f docker-compose.linux.yml --profile nginx config --quiet
docker compose --env-file .env -f docker-compose.linux.yml --profile nginx up -d
```

仅绑定 `127.0.0.1` 时可继续使用 HTTP；任何公网登录流量都必须在可信反向代理上终止 HTTPS，防火墙只开放实际需要的 `80/443` 端口。

---

## 常见运维操作

以下直接使用 `docker compose` 的命令只适用于开发/普通 Compose；Linux 生产环境统一使用 `sudo ./docker-deploy.sh COMMAND`。

### 查看日志

```bash
# 开发/普通 Compose：实时查看所有日志
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

Linux 生产环境使用带备份、镜像记录、健康检查和自动回滚的更新命令：

```bash
sudo ./docker-deploy.sh update
```

开发环境仍可按原 Compose 流程更新；不要在 30GB 的 T730 生产系统盘上执行 `docker compose build --no-cache`。

### 停止

```bash
# 开发/普通 Compose
docker compose down

# Linux 生产（保留数据）
sudo ./docker-deploy.sh stop
```

不要使用 `docker compose down -v` 或手动删除 `data/`，这会破坏持久化数据。

### 备份数据库和上传图片

```bash
sudo ./docker-deploy.sh backup
sudo ls -lh backups/
```

该命令使用 SQLite 在线备份 API 并执行 `integrity_check`，随后生成包含 `data/xianyu_data.db` 与归档内 `static/uploads/images/` 的 `xianyu_data_*.tar.gz`；后者对应宿主机 `./uploads/images/`。脚本同时单独归档运行配置；复制正在写入的数据库文件不能替代此流程。

恢复使用生产脚本，不要手工解压覆盖：

```bash
sudo ./docker-deploy.sh restore backups/xianyu_data_YYYYMMDD_HHMMSS.tar.gz
```

脚本先创建当前数据的在线备份，再在隔离目录校验归档成员、大小、数据库 `integrity_check` 和必需表；校验通过后停服替换数据库与上传图片并运行健康检查。替换或健康检查失败时会尝试恢复原数据，成功后仍保留 `backups/pre_restore_*` 快照供人工复核。不能通过删除 `data/` 目录“修复”数据库。

### 修改配置后续效

修改 `.env` 文件后需要重建容器才能应用新环境变量：

```bash
# 开发/普通 Compose
docker compose up -d

# 开发机修改了 Dockerfile 时才本地构建
docker compose up -d --build

# Linux 生产使用预构建镜像，不在目标机构建
sudo ./docker-deploy.sh deploy
```

---

## 故障排查

### 容器启动失败

```bash
# 查看详细错误
docker compose logs xianyu-app

# 常见原因：
# 1. 端口被占用 → 修改 .env 中的 WEB_PORT
# 2. 资源不足 → 检查 Docker/宿主机资源与日志
# 3. 数据库异常 → 停止写入并保留 data/，从已验证备份执行受控恢复；不要删除 data/
```

### AI 回复返回 "Not Found"

1. 确认已在管理后台为默认设置或该账号保存 DeepSeek API Key，并打开 AI 回复开关
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
| 飞书数据未同步 | 检查桥接计划任务、`/health`、`logs/feishu_inventory_bridge.log`、群消息 ID 和 Base/Table ID |
| 中文乱码 | 确保终端/Shell 支持 UTF-8 |

---

## 项目结构

```
.
├── Start.py                     # 启动入口
├── Dockerfile                   # Docker 构建文件
├── docker-compose.yml           # 开发/普通 Compose 编排
├── docker-compose-cn.yml        # 普通 Compose 国内镜像源版
├── docker-compose.linux.yml     # HP T730 Linux 生产编排
├── docker-deploy.sh             # 生产部署、备份、更新与回滚入口
├── production_maintenance.py    # SQLite 在线备份、恢复校验和磁盘检查
├── feishu_inventory_bridge.py   # 飞书群消息与 Base 只读桥接
├── entrypoint.sh                # 容器启动脚本
├── requirements.txt             # Python 依赖
├── .env.example                 # 开发/普通 Compose 环境模板
├── .env.linux.example           # HP T730 Linux 生产环境模板
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
├── backups/                     # 数据库、上传图片和运行配置备份（不提交 Git）
│
└── nginx/                       # Nginx 反向代理（可选）
    └── nginx.conf
```

## License

MIT

---

*更多问题请提交 [GitHub Issues](https://github.com/LZK18825081186/-AI-_-/issues)*
