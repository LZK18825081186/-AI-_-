# 闲鱼 COS 服租赁 AI 自动回复系统

基于 WebSocket 实时消息的闲鱼自动回复机器人，支持 DeepSeek AI 智能问答 + 飞书多维表格库存知识库 + 本地 VL 模型自动生成图片描述。

## 功能

- 🤖 **AI 智能回复**：DeepSeek / DashScope 驱动，根据买家问题自动生成回复
- 📊 **飞书库存知识库**：多维表格改库存，AI 立刻知道
- 🖼️ **图片自动描述**：Qwen3.6-35B-A3B 本地 VL 模型识别实物图，DeepSeek 汇总为详细描述
- 🔔 **飞书通知**：投诉/退款等自动推送到飞书
- 🖥️ **Web 管理面板**：可视化配置关键词回复、AI 设置
- 🐳 **Docker 一键部署**：含内置 CookieCloud 服务

## 快速开始（Docker）

### 前提条件

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Win/Mac）或 Docker Engine（Linux）
- Edge 浏览器（装 CookieCloud 插件用）
- 一个闲鱼卖家账号

### 1. 克隆项目

```bash
git clone https://github.com/LZK18825081186/-AI-_-
cd -AI-_-
```

### 2. 编辑配置

复制并编辑环境变量文件：

```bash
cp .env.example .env
```

至少填入以下内容：

```env
# 飞书多维表格（库存数据）
FEISHU_BASE_TOKEN=你的飞书BaseToken
FEISHU_TABLE_ID=你的飞书TableID

# DeepSeek API（AI 回复 + 图片汇总）
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥

# 管理后台
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的密码
JWT_SECRET_KEY=随机字符串

# 远程图片理解模型（可选，默认本机 LM Studio）
# LM_STUDIO_URL=http://192.168.1.100:1234
```

### 3. 一键部署

**Windows**：双击 `setup.bat`

**Linux/Mac**：

```bash
chmod +x setup.sh && ./setup.sh
```

脚本会自动：
- 拉取 Docker 镜像
- 启动内置 CookieCloud 服务
- 打开浏览器引导安装 CookieCloud 插件
- 等待你登录闲鱼
- 启动全部服务

### 4. 登录管理后台

打开 `http://localhost:8080`，用 `.env` 中设置的用户名密码登录。

## 手动启动（不依赖 Docker）

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量后启动
FEISHU_BASE_TOKEN=xxx FEISHU_TABLE_ID=xxx \
DEEPSEEK_API_KEY=sk-xxx \
COOKIE_CLOUD_HOST=http://localhost:8088 \
COOKIE_CLOUD_UUID=xxx COOKIE_CLOUD_PASSWORD=xxx \
python Start.py
```

## 图片描述生成

上传 cos 服实测图到飞书多维表格的「实物图」字段后，系统自动：

```
多张照片 → Qwen3.6-35B-A3B 分批视觉推理（2张/批）
    → DeepSeek 汇总为结构化质检报告
    → 写入「图片描述」字段
    → 勾选「描述已审核」后买家可见
```

### 远程 LM Studio

如果本地显卡不够（12GB 显存跑 22GB 模型会 CPU offload），可以在另一台电脑运行 LM Studio，然后在 `.env` 中指定：

```env
LM_STUDIO_URL=http://192.168.1.100:1234
```

要求远程机器 LM Studio 设置中绑定 `0.0.0.0` 并放行 1234 端口。

## 配置 AI 回复

登录管理后台 → 「AI 回复设置」：

- API 地址：`https://api.deepseek.com/v1`
- API Key：你的 DeepSeek 密钥
- 模型：`deepseek-chat`

## 配置飞书知识库

在飞书「零花钱」群中创建一个多维表格，包含以下字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| 角色名称 | 文本 | 如：银狼 |
| 作品来源 | 文本 | 如：崩坏星穹铁道 |
| 码数 | 单选 | M / L / 均码 |
| 总库存 | 数字 | 共几套 |
| 已租出 | 数字 | 已租几套 |
| 状态 | 单选 | 可租/已租出/待归还/维护中 |
| 租期价格 | 文本 | 如：350元/3天 |
| 押金 | 数字 | 押金金额 |
| 日租金底价 | 数字 | 砍价底线 |
| 配件清单 | 文本 | 包含哪些配件 |
| 实物图 | 附件 | cos 服照片（支持多张） |
| 图片描述 | 文本 | AI 自动生成，只读 |
| 描述已审核 | 复选框 | 人工确认后买家可见 |

## 项目结构

```
xianyu-direct/
├── Start.py              # 启动入口
├── XianyuAutoAsync.py    # 主逻辑（WebSocket + 消息处理）
├── ai_reply_engine.py    # AI 回复引擎 + VL 图片描述
├── reply_server.py       # Web 管理面板 API
├── db_manager.py         # 数据库管理
├── cookie_manager.py     # Cookie 管理
├── global_config.yml     # 全局配置
├── docker-compose.yml    # Docker 编排
├── setup.bat / setup.sh  # 一键部署脚本
├── stop.bat              # 停止脚本
└── utils/                # 工具模块
```

## 验证

```bash
# 停止系统
./stop.bat  # Windows
docker-compose down  # Linux

# 查看日志
docker-compose logs -f

# 重启
docker-compose up -d
```

## 注意事项

- 使用 CookieCloud 自动同步 Cookie，需保持 Edge 浏览器登录闲鱼
- 长时间不登录闲鱼可能导致 Cookie 过期
- AI 回复优先级：关键词匹配 > AI 生成 > 默认兜底
- 手动回复某个买家后，AI 自动暂停对该买家回复 10 分钟
- `token.txt` 和 `xianyu_data.db` 包含敏感信息，已列入 `.gitignore`
- 飞书 `base_token` 和 `table_id` 必须通过 `.env` 环境变量配置，不可写死
