#!/bin/bash
# sync_feishu.sh — 从飞书多维表格同步库存到知识库
# 用法: ./sync_feishu.sh           # 当前目录运行（开发环境）
#      DOCKER_CONTAINER=xianyu-auto-reply ./sync_feishu.sh  # 指定容器

set -e

FEISHU_BASE_TOKEN="${FEISHU_BASE_TOKEN:-SwZJb2oCPaEkTGs1nKtcLUKTnyd}"
FEISHU_TABLE_ID="${FEISHU_TABLE_ID:-tblizop7uMcAG0TF}"
DOCKER_CONTAINER="${DOCKER_CONTAINER:-xianyu-auto-reply}"
KB_FILE="/tmp/knowledge_base_live.txt"

echo "=== 飞书库存同步 $(date) ==="

# 1. 用 lark-cli 拉取飞书表格数据
echo "正在从飞书拉取数据..."
RAW_DATA=$(unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null; lark-cli base +record-list \
  --base-token "$FEISHU_BASE_TOKEN" \
  --table-id "$FEISHU_TABLE_ID" \
  --as user --limit 50 2>/dev/null)

if [ -z "$RAW_DATA" ]; then
    echo "❌ 飞书拉取失败（lark-cli 未认证或网络问题），跳过"
    exit 0
fi

# 2. 用 Python 解析表格数据生成知识库
python3 -c "
import re, sys

data = sys.stdin.read()

# 解析lark-cli输出格式
lines = data.strip().split('\n')
header_found = False
records = []

for line in lines:
    line = line.strip()
    if not line or line.startswith('| ---') or 'Meta:' in line:
        continue
    if '| 角色名称 |' in line:
        header_found = True
        continue
    if not header_found:
        continue
    cells = [c.strip() for c in line.split('|')[1:-1]]
    if len(cells) < 15:
        continue
    records.append(cells)

# 生成知识库文本
kb = '===== COS服库存知识库 (自动同步) =====\n'
stats = {'可租': 0, '已租出': 0, '待归还': 0}

for r in records:
    if len(r) < 15:
        continue
    name = r[1]  # 角色名称
    status_raw = r[2]  # 状态
    parts = r[3]  # 配件清单  
    price = r[5]  # 租期价格
    bottom = r[6]  # 日租金底价  
    size = r[7]    # 码数
    work = r[10]   # 作品来源
    total = r[11]  # 总库存
    rented = r[12]  # 已租出
    deposit = r[14]  # 押金
    
    status = status_raw.replace('[\"', '').replace('\"]', '').replace('[', '').replace(']', '')
    if '可租' in status: stats['可租'] += 1
    elif '已租出' in status: stats['已租出'] += 1
    elif '待归还' in status: stats['待归还'] += 1
    
    kb += f'{work} | {name} | {size}码 | {price} | 押金{deposit}元 | 库存{total}/已租{rented}'
    if '可租' in status: kb += ' | ★可租'
    elif '已租出' in status: kb += ' | 已租出'
    elif '待归还' in status: kb += ' | 待归还'
    if parts: kb += f' | 配件:{parts[:50]}'
    kb += '\n'

kb += f'\n可租{stats[\"可租\"]}款, 已租出{stats[\"已租出\"]}款, 待归还{stats[\"待归还\"]}款'

with open('$KB_FILE', 'w', encoding='utf-8') as f:
    f.write(kb)
" <<< "$RAW_DATA"

if [ ! -s "$KB_FILE" ]; then
    echo "❌ 知识库生成失败"
    exit 1
fi

KB_SIZE=$(wc -c < "$KB_FILE")
echo "✅ 知识库已生成 (${KB_SIZE}字节)"

# 3. 推送到Docker容器
if docker ps --format '{{.Names}}' | grep -q "^${DOCKER_CONTAINER}$"; then
    docker cp "$KB_FILE" "${DOCKER_CONTAINER}:/app/knowledge_base.txt" && \
      echo "✅ 已推送到容器 ${DOCKER_CONTAINER}" || \
      echo "❌ 推送到容器失败"
else
    # 无Docker: 复制到本地项目目录
    cp "$KB_FILE" ./knowledge_base.txt 2>/dev/null && \
      echo "✅ 已保存到项目目录" || true
fi

rm -f "$KB_FILE"
echo "=== 同步完成 ==="
