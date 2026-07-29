"""
飞书表格同步模块
从飞书电子表格读取 cos 服库存信息，自动同步到知识库

支持两种方式：
1. lark-cli 子进程（优先，复用飞书连接器认证）
2. 飞书 Open API 直连（备用）

约定表格列（按顺序）:
A=角色名, B=作品, C=日租金, D=押金, E=尺码, F=成色,
G=包含部件(逗号分隔), H=不包含(逗号分隔),
I=清洗方式, J=发货方式, K=亮点(逗号分隔), L=状态, M=备注
"""

import asyncio
import csv
import io
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class FeishuSync:
    """飞书表格 → 知识库同步器"""

    def __init__(
        self,
        spreadsheet_url: str = "",
        sheet_name: str = "服装库存",
        sync_interval: int = 300,
    ):
        self.spreadsheet_url = spreadsheet_url
        self.sheet_name = sheet_name
        self.sync_interval = sync_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 回调：同步完成后的通知
        self.on_sync_complete = None

        # 上次同步的产品列表（用于检测变更）
        self._last_products: List[Dict[str, Any]] = []

    def is_configured(self) -> bool:
        """检查是否已配置飞书表格"""
        return bool(self.spreadsheet_url and self.sheet_name)

    async def sync(self) -> Optional[List[Dict[str, Any]]]:
        """执行一次同步，返回 products 列表"""
        if not self.is_configured():
            logger.debug("飞书表格未配置，跳过同步")
            return None

        logger.info(f"开始同步飞书表格: {self.sheet_name}")

        try:
            products = await self._sync_via_cli()
            if not products:
                products = await self._sync_via_api()

            if products:
                logger.info(f"飞书同步完成: {len(products)} 件服装")
                self._last_products = products
                return products
            else:
                logger.warning("飞书同步未获取到数据")
                return None

        except Exception as e:
            logger.error(f"飞书同步失败: {e}")
            return None

    async def _sync_via_cli(self) -> Optional[List[Dict[str, Any]]]:
        """通过 lark-cli 子进程同步"""
        try:
            # 使用 lark-cli 读取表格 CSV
            cmd = [
                "lark-cli", "sheets", "+csv-get",
                "--url", self.spreadsheet_url,
                "--sheet-name", self.sheet_name,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=15
            )

            if process.returncode != 0:
                logger.warning(f"lark-cli 返回错误: {stderr.decode()[:200]}")
                return None

            # 解析 CSV 输出
            csv_text = stdout.decode("utf-8")
            return self._parse_csv(csv_text)

        except asyncio.TimeoutError:
            logger.warning("lark-cli 超时，将尝试 API 方式")
            return None
        except FileNotFoundError:
            logger.debug("lark-cli 未安装，将使用 API 方式")
            return None
        except Exception as e:
            logger.warning(f"lark-cli 异常: {e}")
            return None

    async def _sync_via_api(self) -> Optional[List[Dict[str, Any]]]:
        """通过飞书 Open API 同步（备选）"""
        # 需要 APP_ID 和 APP_SECRET，暂不实现
        # 用户后续配置飞书表格时，优先使用 lark-cli
        logger.debug("飞书 API 同步尚未配置")
        return None

    def _parse_csv(self, csv_text: str) -> List[Dict[str, Any]]:
        """解析飞书表格 CSV 为 products 列表"""
        products = []
        reader = csv.reader(io.StringIO(csv_text))

        # 跳过标题行
        rows = list(reader)
        if len(rows) < 2:
            return products

        # 第一行是标题，从第二行开始是数据
        for row in rows[1:]:
            if len(row) < 5:  # 至少要有角色名、作品、租金、押金、尺码
                continue

            # 跳过状态为"已下架"或"停用"的行
            status = row[11] if len(row) > 11 else ""
            if status in ("已下架", "停用", "报废"):
                continue

            product = {
                "id": row[0].strip() if len(row) > 0 else "",       # 角色名作为 ID
                "name": row[0].strip() if len(row) > 0 else "",
                "role": row[0].strip() if len(row) > 0 else "",
                "series": row[1].strip() if len(row) > 1 else "",    # 作品
                "price": self._parse_number(row[2]) if len(row) > 2 else 0,   # 日租金
                "deposit": self._parse_number(row[3]) if len(row) > 3 else 0, # 押金
                "size": row[4].strip() if len(row) > 4 else "",      # 尺码
                "condition": row[5].strip() if len(row) > 5 else "",  # 成色
                "includes": self._parse_list(row[6]) if len(row) > 6 else [], # 包含
                "not_includes": self._parse_list(row[7]) if len(row) > 7 else [],
                "cleaning": row[8].strip() if len(row) > 8 else "",   # 清洗
                "ship_method": row[9].strip() if len(row) > 9 else "",# 发货
                "highlights": self._parse_list(row[10]) if len(row) > 10 else [],
                "status": status,
                "remark": row[12].strip() if len(row) > 12 else "",
                "faq": [
                    {
                        "question": "尺码合适吗",
                        "answer": f"{row[4].strip() if len(row)>4 else ''}码，适合{row[4].strip() if len(row)>4 else ''}的身材。不确定可以报身高体重三围我帮你看～"
                    },
                    {
                        "question": "包含什么",
                        "answer": f"包含：{row[6].strip() if len(row)>6 else '全套'}。不含{row[7].strip() if len(row)>7 else '假发和道具'}，可另租～"
                    },
                ],
            }
            products.append(product)

        return products

    @staticmethod
    def _parse_number(value: str) -> float:
        """从字符串解析数字"""
        import re
        value = value.strip().replace("¥", "").replace("元", "").replace(",", "")
        match = re.search(r"[\d.]+", value)
        return float(match.group()) if match else 0.0

    @staticmethod
    def _parse_list(value: str) -> List[str]:
        """从逗号分隔的字符串解析列表"""
        if not value or not value.strip():
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    async def start_auto_sync(self, callback=None):
        """启动定时同步"""
        if not self.is_configured():
            logger.info("飞书表格未配置，跳过自动同步")
            return

        self._running = True
        self.on_sync_complete = callback

        async def _loop():
            while self._running:
                products = await self.sync()
                if products and self.on_sync_complete:
                    await self.on_sync_complete(products)
                await asyncio.sleep(self.sync_interval)

        self._task = asyncio.create_task(_loop())
        logger.info(f"飞书自动同步已启动（间隔 {self.sync_interval}s）")

    async def stop(self):
        """停止定时同步"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("飞书同步已停止")
