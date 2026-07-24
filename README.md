# 闲鱼 COS 服租赁 AI 自动回复系统

基于 WebSocket 实时消息的闲鱼自动回复机器人，支持 DeepSeek AI 智能问答 + 飞书多维表格库存知识库。

## 功能

- 🤖 **AI 智能回复**：DeepSeek V4 Flash 驱动，根据买家问题自动生成回复
- 📊 **飞书库存知识库**：客服在飞书表格里改库存，AI 立刻知道
- 🔔 **飞书通知**：重要消息（投诉/退款等）自动推送到飞书
- 🖥️ **Web 管理面板**：可视化配置关键词回复、AI 设置等
- 🐳 **Docker 部署**：一键启动，无需安装 Python 环境

## 快速开始（Docker）

### 前提条件
- 安装 [Docker](https://docs.docker.com/get-docker/) 和 Docker Compose
- 一个闲鱼卖家账号
-（可选）CookieCloud 服务用于自动同步 Cookie

### 1. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，至少设置以下变量：

```env
# 必填：管理后台账号密码
ADMIN_USERNAME=your_username
ADMIN_PASSWORD=your_secure_password
JWT_SECRET_KEY=your_random_secret_key

# 可选：CookieCloud（自动同步闲鱼Cookie）
COOKIE_CLOUD_HOST=http://your-cookiecloud-server:8088
COOKIE_CLOUD_UUID=your_uuid
COOKIE_CLOUD_PASSWORD=your_password
```

### 2. 启动

```bash
docker-compose up -d
```

### 3. 访问管理面板

浏览器打开 `http://localhost:8080`，用你设置的账号密码登录。

## 手动配置（不用 CookieCloud）

如果不使用 CookieCloud，可以手动导入 Cookie：

1. 登录管理面板
2. 进入「Cookie 管理」→「添加 Cookie」
3. 从浏览器开发者工具复制闲鱼 Cookie 粘贴进去

## 配置 AI 回复

1. 登录管理面板，进入「AI 回复设置」
2. 填写 API Key 和 Base URL（如 DeepSeek: `https://api.deepseek.com/v1`）
3. 选择模型（推荐 `deepseek-chat`）
4. 开启 AI 回复

## 配置知识库

### 方式一：飞书多维表格（推荐）

在飞书中创建一个多维表格存放库存数据，格式参考：

| 角色名称 | 作品来源 | 码数 | 总库存 | 已租出 | 状态 | 租期价格 | 押金 |
|---------|---------|------|--------|--------|------|---------|------|
| 雷电将军 | 原神 | M | 2 | 0 | 可租 | 400元/3天 | 200 |

然后修改 `ai_reply_engine.py` 中的 `_load_knowledge_base()` 方法，将 `base_token` 和 `table_id` 改为你的表格 ID。

### 方式二：本地文件

直接编辑项目根目录的 `knowledge_base.txt`，AI 会自动读取。

## 项目结构

```
xianyu-direct/
├── Start.py              # 启动入口
├── XianyuAutoAsync.py    # 主逻辑（WebSocket + 消息处理）
├── ai_reply_engine.py    # AI 回复引擎
├── reply_server.py       # Web 管理面板 API
├── db_manager.py         # 数据库管理
├── cookie_manager.py     # Cookie 管理
├── global_config.yml     # 全局配置
├── Dockerfile            # Docker 镜像
├── docker-compose.yml    # Docker 编排
└── utils/                # 工具模块
```

## 注意事项

- 系统使用无头浏览器自动维护 Cookie，需要 Chromium 浏览器
- 长时间不登录闲鱼可能导致 Cookie 过期，需要重新导入
- AI 回复仅在关键词匹配失败时触发（优先级：关键词 > AI > 默认回复）
- 如果你手动回复了某个买家，AI 会暂停对该买家的自动回复 10 分钟
