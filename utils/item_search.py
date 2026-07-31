#!/usr/bin/env python3
"""
闲鱼商品搜索模块
基于 Playwright 实现真实的闲鱼商品搜索功能
"""

import asyncio
import json
import time
import sys
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from loguru import logger

# 修复Docker环境中的asyncio事件循环策略问题
if sys.platform.startswith('linux') or os.getenv('DOCKER_ENV'):
    try:
        # 在Linux/Docker环境中设置事件循环策略
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    except Exception as e:
        logger.warning(f"设置事件循环策略失败: {e}")

# 确保在Docker环境中使用正确的事件循环
if os.getenv('DOCKER_ENV'):
    try:
        # 强制使用SelectorEventLoop（在Docker中更稳定）
        if hasattr(asyncio, 'SelectorEventLoop'):
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
    except Exception as e:
        logger.warning(f"设置SelectorEventLoop失败: {e}")

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright 未安装，将使用模拟数据")


class XianyuSearcher:
    """闲鱼商品搜索器 - 基于 Playwright"""

    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.api_responses = []

    async def safe_get(self, data, *keys, default="暂无"):
        """安全获取嵌套字典值"""
        for key in keys:
            try:
                data = data[key]
            except (KeyError, TypeError, IndexError):
                return default
        return data

    async def get_first_valid_cookie(self, user_id: int):
        """仅从当前用户的账号中获取第一个有效 Cookie。"""
        try:
            from db_manager import db_manager

            cookies = db_manager.get_all_cookies(user_id)

            # 找到第一个有效的cookie（长度大于50的认为是有效的）
            for cookie_id, cookie_value in cookies.items():
                if len(cookie_value) > 50:
                    logger.info(f"找到有效cookie: {cookie_id}")
                    return {
                        'id': cookie_id,
                        'value': cookie_value
                    }

            return None

        except Exception as e:
            logger.error(f"获取cookie失败: {str(e)}")
            return None

    async def set_browser_cookies(self, cookie_value: str):
        """设置浏览器cookies"""
        try:
            if not cookie_value:
                return False

            # 解析cookie字符串
            cookies = []
            for cookie_pair in cookie_value.split(';'):
                cookie_pair = cookie_pair.strip()
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })

            # 设置cookies到浏览器
            await self.context.add_cookies(cookies)
            logger.info(f"成功设置 {len(cookies)} 个cookies到浏览器")
            return True

        except Exception as e:
            logger.error(f"设置浏览器cookies失败: {str(e)}")
            return False

    async def init_browser(self):
        """初始化浏览器"""
        if not PLAYWRIGHT_AVAILABLE:
            raise Exception("Playwright 未安装，无法使用真实搜索功能")

        if not self.browser:
            playwright = await async_playwright().start()
            logger.info("正在启动浏览器...")
            # 简化的浏览器启动参数，避免冲突
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--no-first-run',
                '--disable-extensions',
                '--disable-default-apps',
                '--no-default-browser-check'
            ]

            # 只在确实是Docker环境时添加额外参数
            if os.getenv('DOCKER_ENV') == 'true':
                browser_args.extend([
                    '--disable-gpu',
                    '--single-process'
                ])

            logger.info("正在启动浏览器...")
            self.browser = await playwright.chromium.launch(
                headless=True,  # 无头模式，后台运行
                args=browser_args
            )

            logger.info("浏览器启动成功，创建上下文...")
            # 简化上下文创建，减少可能的问题
            self.context = await self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720}
            )

            logger.info("创建页面...")
            self.page = await self.context.new_page()

            logger.info("浏览器初始化完成")

    async def close_browser(self):
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.context = None
            self.page = None
    
    async def search_items(self, keyword: str, user_id: int, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """
        搜索闲鱼商品 - 使用 Playwright 获取真实数据

        Args:
            keyword: 搜索关键词
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            搜索结果字典，包含items列表和总数
        """
        try:
            if not PLAYWRIGHT_AVAILABLE:
                logger.error("Playwright 不可用，无法获取真实数据")
                return {
                    'items': [],
                    'total': 0,
                    'error': 'Playwright 不可用，无法获取真实数据'
                }

            logger.info(f"使用 Playwright 搜索闲鱼商品: 关键词='{keyword}', 页码={page}, 每页={page_size}")

            await self.init_browser()

            # 清空之前的API响应
            self.api_responses = []
            data_list = []

            # 设置API响应监听器
            async def on_response(response):
                """处理API响应，解析数据"""
                if "h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search" in response.url:
                    try:
                        # 检查响应状态
                        if response.status != 200:
                            logger.warning(f"API响应状态异常: {response.status}")
                            return

                        # 安全地获取响应内容
                        try:
                            result_json = await response.json()
                        except Exception as json_error:
                            logger.warning(f"无法解析响应JSON: {str(json_error)}")
                            return

                        self.api_responses.append(result_json)
                        logger.info(f"捕获到API响应，URL: {response.url}")

                        items = result_json.get("data", {}).get("resultList", [])
                        logger.info(f"从API获取到 {len(items)} 条原始数据")

                        for item in items:
                            try:
                                parsed_item = await self._parse_real_item(item)
                                if parsed_item:
                                    data_list.append(parsed_item)
                            except Exception as parse_error:
                                logger.warning(f"解析单个商品失败: {str(parse_error)}")
                                continue

                    except Exception as e:
                        logger.warning(f"响应处理异常: {str(e)}")

            try:
                # 获取并设置cookies进行登录
                logger.info("正在获取有效的cookies账户...")
                cookie_data = await self.get_first_valid_cookie(user_id)
                if not cookie_data:
                    raise Exception("未找到有效的cookies账户，请先在Cookie管理中添加有效的闲鱼账户")

                logger.info(f"使用账户: {cookie_data.get('id', 'unknown')}")

                logger.info("正在访问闲鱼首页...")
                await self.page.goto("https://www.goofish.com", timeout=30000)

                # 设置cookies进行登录
                logger.info("正在设置cookies进行登录...")
                cookie_success = await self.set_browser_cookies(cookie_data.get('value', ''))
                if not cookie_success:
                    logger.warning("设置cookies失败，将以未登录状态继续")
                else:
                    logger.info("✅ cookies设置成功，已登录")
                    # 刷新页面以应用cookies
                    await self.page.reload()
                    await asyncio.sleep(2)

                await self.page.wait_for_load_state("networkidle", timeout=10000)

                logger.info(f"正在搜索关键词: {keyword}")
                await self.page.fill('input[class*="search-input"]', keyword)

                # 注册响应监听
                self.page.on("response", on_response)

                await self.page.click('button[type="submit"]')
                await self.page.wait_for_load_state("networkidle", timeout=15000)

                # 等待第一页API响应
                logger.info("等待第一页API响应...")
                await asyncio.sleep(5)

                # 尝试处理弹窗
                try:
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(1)
                except:
                    pass

                # 等待更多数据
                await asyncio.sleep(3)

                first_page_count = len(data_list)
                logger.info(f"第1页完成，获取到 {first_page_count} 条数据")

                # 如果需要获取指定页数据，实现翻页逻辑
                if page > 1:
                    # 清空之前的数据，只保留目标页的数据
                    data_list.clear()
                    await self._navigate_to_page(page)

                # 根据"人想要"数量进行倒序排列
                data_list.sort(key=lambda x: x.get('want_count', 0), reverse=True)

                total_count = len(data_list)
                logger.info(f"搜索完成，总共获取到 {total_count} 条真实数据，已按想要人数排序")

                return {
                    'items': data_list,
                    'total': total_count,
                    'is_real_data': True,
                    'source': 'playwright'
                }

            finally:
                await self.close_browser()

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Playwright 搜索失败: {error_msg}")

            # 检查是否是浏览器安装问题
            if "Executable doesn't exist" in error_msg or "playwright install" in error_msg:
                error_msg = "浏览器未安装。请在Docker容器中运行: playwright install chromium"
            elif "BrowserType.launch" in error_msg:
                error_msg = "浏览器启动失败。请确保Docker容器有足够的权限和资源"

            # 如果 Playwright 失败，返回错误信息
            return {
                'items': [],
                'total': 0,
                'error': f'搜索失败: {error_msg}'
            }

    async def _get_fallback_data(self, keyword: str, page: int, page_size: int) -> Dict[str, Any]:
        """获取备选数据（模拟数据）"""
        logger.info(f"使用备选数据: 关键词='{keyword}', 页码={page}, 每页={page_size}")

        # 模拟搜索延迟
        await asyncio.sleep(0.5)

        # 生成模拟数据
        mock_items = []
        start_index = (page - 1) * page_size

        for i in range(page_size):
            item_index = start_index + i + 1
            mock_items.append({
                'item_id': f'mock_{keyword}_{item_index}',
                'title': f'{keyword}相关商品 #{item_index} [模拟数据]',
                'price': f'{100 + item_index * 10}',
                'seller_name': f'卖家{item_index}',
                'item_url': f'https://www.goofish.com/item?id=mock_{keyword}_{item_index}',
                'publish_time': '2025-07-28',
                'tags': [f'标签{i+1}', f'分类{i+1}'],
                'main_image': f'https://via.placeholder.com/200x200?text={keyword}商品{item_index}',
                'raw_data': {
                    'mock': True,
                    'keyword': keyword,
                    'index': item_index
                }
            })

        # 模拟总数
        total_items = 100 + hash(keyword) % 500

        logger.info(f"备选数据生成完成: 找到{len(mock_items)}个商品，总计{total_items}个")

        return {
            'items': mock_items,
            'total': total_items,
            'is_fallback': True
        }

    async def _parse_real_item(self, item_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """解析真实的闲鱼商品数据"""
        try:
            main_data = await self.safe_get(item_data, "data", "item", "main", "exContent", default={})
            click_params = await self.safe_get(item_data, "data", "item", "main", "clickParam", "args", default={})

            # 解析商品信息
            title = await self.safe_get(main_data, "title", default="未知标题")

            # 价格处理
            price_parts = await self.safe_get(main_data, "price", default=[])
            price = "价格异常"
            if isinstance(price_parts, list):
                price = "".join([str(p.get("text", "")) for p in price_parts if isinstance(p, dict)])
                price = price.replace("当前价", "").strip()

                # 统一价格格式处理
                if price and price != "价格异常":
                    # 先移除所有¥符号，避免重复
                    clean_price = price.replace('¥', '').strip()

                    # 处理万单位的价格
                    if "万" in clean_price:
                        try:
                            numeric_price = clean_price.replace('万', '').strip()
                            price_value = float(numeric_price) * 10000
                            price = f"¥{price_value:.0f}"
                        except:
                            price = f"¥{clean_price}"  # 如果转换失败，保持原样但确保有¥符号
                    else:
                        # 普通价格，确保有¥符号
                        if clean_price and (clean_price[0].isdigit() or clean_price.replace('.', '').isdigit()):
                            price = f"¥{clean_price}"
                        else:
                            price = clean_price if clean_price else "价格异常"

            # 只提取"想要人数"标签
            fish_tags_content = ""
            fish_tags = await self.safe_get(main_data, "fishTags", default={})

            # 遍历所有类型的标签 (r2, r3, r4等)
            for tag_type, tag_data in fish_tags.items():
                if isinstance(tag_data, dict) and "tagList" in tag_data:
                    tag_list = tag_data.get("tagList", [])
                    for tag_item in tag_list:
                        if isinstance(tag_item, dict) and "data" in tag_item:
                            content = tag_item["data"].get("content", "")
                            # 只保留包含"人想要"的标签
                            if content and "人想要" in content:
                                fish_tags_content = content
                                break
                    if fish_tags_content:  # 找到后就退出
                        break

            # 其他字段解析
            area = await self.safe_get(main_data, "area", default="地区未知")
            seller = await self.safe_get(main_data, "userNickName", default="匿名卖家")
            raw_link = await self.safe_get(item_data, "data", "item", "main", "targetUrl", default="")
            image_url = await self.safe_get(main_data, "picUrl", default="")

            # 获取商品ID
            item_id = await self.safe_get(click_params, "item_id", default="未知ID")

            # 处理发布时间
            publish_time = "未知时间"
            publish_timestamp = click_params.get("publishTime", "")
            if publish_timestamp and publish_timestamp.isdigit():
                try:
                    publish_time = datetime.fromtimestamp(
                        int(publish_timestamp)/1000
                    ).strftime("%Y-%m-%d %H:%M")
                except:
                    pass

            # 提取"人想要"的数字用于排序
            want_count = self._extract_want_count(fish_tags_content)

            return {
                "item_id": item_id,
                "title": title,
                "price": price,
                "seller_name": seller,
                "item_url": raw_link.replace("fleamarket://", "https://www.goofish.com/"),
                "main_image": f"https:{image_url}" if image_url and not image_url.startswith("http") else image_url,
                "publish_time": publish_time,
                "tags": [fish_tags_content] if fish_tags_content else [],
                "area": area,
                "want_count": want_count,  # 添加想要人数用于排序
                "raw_data": item_data
            }

        except Exception as e:
            logger.warning(f"解析真实商品数据失败: {str(e)}")
            return None

    def _extract_want_count(self, tags_content: str) -> int:
        """从标签内容中提取"人想要"的数字"""
        try:
            if not tags_content or "人想要" not in tags_content:
                return 0

            # 使用正则表达式提取数字
            import re
            # 匹配类似 "123人想要" 或 "1.2万人想要" 的格式
            pattern = r'(\d+(?:\.\d+)?(?:万)?)\s*人想要'
            match = re.search(pattern, tags_content)

            if match:
                number_str = match.group(1)
                if '万' in number_str:
                    # 处理万单位
                    number = float(number_str.replace('万', '')) * 10000
                    return int(number)
                else:
                    return int(float(number_str))

            return 0
        except Exception as e:
            logger.warning(f"提取想要人数失败: {str(e)}")
            return 0

    async def _navigate_to_page(self, target_page: int):
        """导航到指定页面"""
        try:
            logger.info(f"正在导航到第 {target_page} 页...")

            # 等待页面稳定
            await asyncio.sleep(2)

            # 查找并点击下一页按钮
            next_button_selectors = [
                '.search-page-tiny-arrow-right--oXVFaRao',  # 用户找到的正确选择器
                '[class*="search-page-tiny-arrow-right"]',  # 更通用的版本
                'button[aria-label="下一页"]',
                'button:has-text("下一页")',
                'a:has-text("下一页")',
                '.ant-pagination-next',
                'li.ant-pagination-next a',
                'a[aria-label="下一页"]',
                '[class*="next"]',
                '[class*="pagination-next"]',
                'button[title="下一页"]',
                'a[title="下一页"]'
            ]

            # 从第2页开始点击
            for current_page in range(2, target_page + 1):
                logger.info(f"正在点击到第 {current_page} 页...")

                next_button_found = False
                for selector in next_button_selectors:
                    try:
                        next_button = self.page.locator(selector).first

                        if await next_button.is_visible(timeout=3000):
                            # 检查按钮是否可点击（不是禁用状态）
                            is_disabled = await next_button.get_attribute("disabled")
                            has_disabled_class = await next_button.evaluate("el => el.classList.contains('ant-pagination-disabled') || el.classList.contains('disabled')")

                            if not is_disabled and not has_disabled_class:
                                logger.info(f"找到下一页按钮，正在点击...")

                                # 滚动到按钮位置
                                await next_button.scroll_into_view_if_needed()
                                await asyncio.sleep(1)

                                # 点击下一页
                                await next_button.click()
                                await self.page.wait_for_load_state("networkidle", timeout=15000)

                                # 等待新数据加载
                                await asyncio.sleep(5)

                                logger.info(f"成功导航到第 {current_page} 页")
                                next_button_found = True
                                break

                    except Exception as e:
                        continue

                if not next_button_found:
                    logger.warning(f"无法找到下一页按钮，停止在第 {current_page-1} 页")
                    break

        except Exception as e:
            logger.error(f"导航到第 {target_page} 页失败: {str(e)}")

    async def search_multiple_pages(self, keyword: str, user_id: int, total_pages: int = 1) -> Dict[str, Any]:
        """
        搜索多页闲鱼商品

        Args:
            keyword: 搜索关键词
            total_pages: 总页数

        Returns:
            搜索结果字典，包含所有页面的items列表和总数
        """
        browser_initialized = False
        try:
            if not PLAYWRIGHT_AVAILABLE:
                logger.error("Playwright 不可用，无法获取真实数据")
                return {
                    'items': [],
                    'total': 0,
                    'error': 'Playwright 不可用，无法获取真实数据'
                }

            logger.info(f"使用 Playwright 搜索多页闲鱼商品: 关键词='{keyword}', 总页数={total_pages}")

            # 确保浏览器初始化
            await self.init_browser()
            browser_initialized = True

            # 验证浏览器状态
            if not self.browser or not self.page:
                raise Exception("浏览器初始化失败")

            logger.info("浏览器初始化成功，开始搜索...")

            # 清空之前的API响应
            self.api_responses = []
            all_data_list = []

            # 设置API响应监听器
            async def on_response(response):
                """处理API响应，解析数据"""
                if "h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search" in response.url:
                    try:
                        # 检查响应状态
                        if response.status != 200:
                            logger.warning(f"API响应状态异常: {response.status}")
                            return

                        # 安全地获取响应内容
                        try:
                            result_json = await response.json()
                        except Exception as json_error:
                            logger.warning(f"无法解析响应JSON: {str(json_error)}")
                            return

                        self.api_responses.append(result_json)
                        logger.info(f"捕获到API响应，URL: {response.url}")

                        items = result_json.get("data", {}).get("resultList", [])
                        logger.info(f"从API获取到 {len(items)} 条原始数据")

                        for item in items:
                            try:
                                parsed_item = await self._parse_real_item(item)
                                if parsed_item:
                                    all_data_list.append(parsed_item)
                            except Exception as parse_error:
                                logger.warning(f"解析单个商品失败: {str(parse_error)}")
                                continue

                    except Exception as e:
                        logger.warning(f"响应处理异常: {str(e)}")

            try:
                # 检查浏览器状态
                if not self.page or self.page.is_closed():
                    raise Exception("页面已关闭或不可用")

                # 获取并设置cookies进行登录
                logger.info("正在获取有效的cookies账户...")
                cookie_data = await self.get_first_valid_cookie(user_id)
                if not cookie_data:
                    raise Exception("未找到有效的cookies账户，请先在Cookie管理中添加有效的闲鱼账户")

                logger.info(f"使用账户: {cookie_data.get('id', 'unknown')}")

                logger.info("正在访问闲鱼首页...")
                await self.page.goto("https://www.goofish.com", timeout=30000)

                # 设置cookies进行登录
                logger.info("正在设置cookies进行登录...")
                cookie_success = await self.set_browser_cookies(cookie_data.get('value', ''))
                if not cookie_success:
                    logger.warning("设置cookies失败，将以未登录状态继续")
                else:
                    logger.info("✅ cookies设置成功，已登录")
                    # 刷新页面以应用cookies
                    await self.page.reload()
                    await asyncio.sleep(2)

                # 再次检查页面状态
                if self.page.is_closed():
                    raise Exception("页面在导航后被关闭")

                logger.info("等待页面加载完成...")
                await self.page.wait_for_load_state("networkidle", timeout=15000)

                # 等待页面稳定
                logger.info("等待页面稳定...")
                await asyncio.sleep(3)  # 增加等待时间

                # 再次检查页面状态
                if self.page.is_closed():
                    raise Exception("页面在等待加载后被关闭")

                # 获取页面标题和URL用于调试
                page_title = await self.page.title()
                page_url = self.page.url
                logger.info(f"当前页面标题: {page_title}")
                logger.info(f"当前页面URL: {page_url}")

                logger.info(f"正在搜索关键词: {keyword}")

                # 尝试多种搜索框选择器
                search_selectors = [
                    'input[class*="search-input"]',
                    'input[placeholder*="搜索"]',
                    'input[type="text"]',
                    '.search-input',
                    '#search-input'
                ]

                search_input = None
                for selector in search_selectors:
                    try:
                        logger.info(f"尝试查找搜索框，选择器: {selector}")
                        search_input = await self.page.wait_for_selector(selector, timeout=5000)
                        if search_input:
                            logger.info(f"✅ 找到搜索框，使用选择器: {selector}")
                            break
                    except Exception as e:
                        logger.info(f"❌ 选择器 {selector} 未找到搜索框: {str(e)}")
                        continue

                if not search_input:
                    raise Exception("未找到搜索框元素")

                # 检查页面状态
                if self.page.is_closed():
                    raise Exception("页面在查找搜索框后被关闭")

                await search_input.fill(keyword)
                logger.info(f"✅ 搜索关键词 '{keyword}' 已填入搜索框")

                # 注册响应监听
                self.page.on("response", on_response)

                logger.info("🖱️ 准备点击搜索按钮...")
                await self.page.click('button[type="submit"]')
                logger.info("✅ 搜索按钮已点击")
                await self.page.wait_for_load_state("networkidle", timeout=15000)

                # 等待第一页API响应
                logger.info("等待第一页API响应...")
                await asyncio.sleep(10)  # 增加等待时间

                # 尝试处理弹窗
                try:
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(1)
                except:
                    pass

                # 等待更多数据
                await asyncio.sleep(3)

                first_page_count = len(all_data_list)
                logger.info(f"第1页完成，获取到 {first_page_count} 条数据")

                # 如果需要获取更多页数据
                if total_pages > 1:
                    for page_num in range(2, total_pages + 1):
                        logger.info(f"正在获取第 {page_num} 页数据...")

                        # 等待页面稳定
                        await asyncio.sleep(2)

                        # 查找并点击下一页按钮
                        next_button_found = False
                        next_button_selectors = [
                            '.search-page-tiny-arrow-right--oXVFaRao',
                            '[class*="search-page-tiny-arrow-right"]',
                            'button[aria-label="下一页"]',
                            'button:has-text("下一页")',
                            'a:has-text("下一页")',
                            '.ant-pagination-next',
                            'li.ant-pagination-next a',
                            'a[aria-label="下一页"]'
                        ]

                        for selector in next_button_selectors:
                            try:
                                next_button = self.page.locator(selector).first

                                if await next_button.is_visible(timeout=3000):
                                    # 检查按钮是否可点击
                                    is_disabled = await next_button.get_attribute("disabled")
                                    has_disabled_class = await next_button.evaluate("el => el.classList.contains('ant-pagination-disabled') || el.classList.contains('disabled')")

                                    if not is_disabled and not has_disabled_class:
                                        logger.info(f"找到下一页按钮，正在点击到第 {page_num} 页...")

                                        # 记录点击前的数据量
                                        before_click_count = len(all_data_list)

                                        # 滚动到按钮位置并点击
                                        await next_button.scroll_into_view_if_needed()
                                        await asyncio.sleep(1)
                                        await next_button.click()
                                        await self.page.wait_for_load_state("networkidle", timeout=15000)

                                        # 等待新数据加载
                                        await asyncio.sleep(5)

                                        # 检查是否有新数据
                                        after_click_count = len(all_data_list)
                                        new_items = after_click_count - before_click_count

                                        if new_items > 0:
                                            logger.info(f"第 {page_num} 页成功，新增 {new_items} 条数据")
                                            next_button_found = True
                                            break
                                        else:
                                            logger.warning(f"第 {page_num} 页点击后没有新数据，可能已到最后一页")
                                            next_button_found = False
                                            break

                            except Exception as e:
                                continue

                        if not next_button_found:
                            logger.warning(f"无法获取第 {page_num} 页数据，停止在第 {page_num-1} 页")
                            break

                # 根据"人想要"数量进行倒序排列
                all_data_list.sort(key=lambda x: x.get('want_count', 0), reverse=True)

                total_count = len(all_data_list)
                logger.info(f"多页搜索完成，总共获取到 {total_count} 条真实数据，已按想要人数排序")

                return {
                    'items': all_data_list,
                    'total': total_count,
                    'is_real_data': True,
                    'source': 'playwright'
                }

            finally:
                # 确保浏览器被正确关闭
                if browser_initialized:
                    try:
                        await self.close_browser()
                        logger.info("浏览器已安全关闭")
                    except Exception as close_error:
                        logger.warning(f"关闭浏览器时出错: {str(close_error)}")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Playwright 多页搜索失败: {error_msg}")

            # 检查是否是浏览器相关问题
            if "Executable doesn't exist" in error_msg or "playwright install" in error_msg:
                error_msg = "浏览器未安装。请在Docker容器中运行: playwright install chromium"
            elif "BrowserType.launch" in error_msg:
                error_msg = "浏览器启动失败。请确保Docker容器有足够的权限和资源"
            elif "Target page, context or browser has been closed" in error_msg:
                error_msg = "浏览器页面被意外关闭。这可能是由于网站反爬虫检测或系统资源限制导致的"
            elif "Page.goto" in error_msg and "closed" in error_msg:
                error_msg = "页面导航失败，浏览器连接已断开"
            elif "Timeout" in error_msg and "exceeded" in error_msg:
                error_msg = "页面加载超时。网络连接可能不稳定或网站响应缓慢"

            # 如果 Playwright 失败，返回错误信息
            return {
                'items': [],
                'total': 0,
                'error': f'多页搜索失败: {error_msg}'
            }

    async def _get_multiple_fallback_data(self, keyword: str, total_pages: int) -> Dict[str, Any]:
        """获取多页备选数据（模拟数据）"""
        logger.info(f"使用多页备选数据: 关键词='{keyword}', 总页数={total_pages}")

        # 模拟搜索延迟
        await asyncio.sleep(1)

        # 生成多页模拟数据
        all_mock_items = []

        for page in range(1, total_pages + 1):
            page_size = 20  # 每页20条
            start_index = (page - 1) * page_size

            for i in range(page_size):
                item_index = start_index + i + 1
                all_mock_items.append({
                    'item_id': f'mock_{keyword}_{item_index}',
                    'title': f'{keyword}相关商品 #{item_index} [模拟数据-第{page}页]',
                    'price': f'{100 + item_index * 10}',
                    'seller_name': f'卖家{item_index}',
                    'item_url': f'https://www.goofish.com/item?id=mock_{keyword}_{item_index}',
                    'publish_time': '2025-07-28',
                    'tags': [f'标签{i+1}', f'分类{i+1}'],
                    'main_image': f'https://via.placeholder.com/200x200?text={keyword}商品{item_index}',
                    'raw_data': {
                        'mock': True,
                        'keyword': keyword,
                        'index': item_index,
                        'page': page
                    }
                })

        total_count = len(all_mock_items)
        logger.info(f"多页备选数据生成完成: 找到{total_count}个商品，共{total_pages}页")

        return {
            'items': all_mock_items,
            'total': total_count,
            'is_fallback': True
        }


# 搜索器工具函数

async def search_xianyu_items(keyword: str, user_id: int, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """
    搜索闲鱼商品的便捷函数，带重试机制

    Args:
        keyword: 搜索关键词
        page: 页码
        page_size: 每页数量

    Returns:
        搜索结果
    """
    max_retries = 2
    retry_delay = 5  # 秒，增加重试间隔

    for attempt in range(max_retries + 1):
        searcher = None
        try:
            # 每次搜索都创建新的搜索器实例，避免浏览器状态混乱
            searcher = XianyuSearcher()

            logger.info(f"开始单页搜索，尝试次数: {attempt + 1}/{max_retries + 1}")
            result = await searcher.search_items(keyword, user_id, page, page_size)

            # 如果成功获取到数据，直接返回
            if result.get('items') or not result.get('error'):
                logger.info(f"单页搜索成功，获取到 {len(result.get('items', []))} 条数据")
                return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"搜索商品失败 (尝试 {attempt + 1}/{max_retries + 1}): {error_msg}")

            # 如果是最后一次尝试，返回错误
            if attempt == max_retries:
                return {
                    'items': [],
                    'total': 0,
                    'error': f"搜索失败，已重试 {max_retries} 次: {error_msg}"
                }

            # 等待后重试
            logger.info(f"等待 {retry_delay} 秒后重试...")
            await asyncio.sleep(retry_delay)

        finally:
            # 确保搜索器被正确关闭
            if searcher:
                try:
                    await searcher.close_browser()
                except Exception as close_error:
                    logger.warning(f"关闭搜索器时出错: {str(close_error)}")

    # 理论上不会到达这里
    return {
        'items': [],
        'total': 0,
        'error': "未知错误"
    }


async def search_multiple_pages_xianyu(keyword: str, user_id: int, total_pages: int = 1) -> Dict[str, Any]:
    """
    搜索多页闲鱼商品的便捷函数，带重试机制

    Args:
        keyword: 搜索关键词
        total_pages: 总页数

    Returns:
        搜索结果
    """
    max_retries = 2
    retry_delay = 5  # 秒，增加重试间隔

    for attempt in range(max_retries + 1):
        searcher = None
        try:
            # 每次搜索都创建新的搜索器实例，避免浏览器状态混乱
            searcher = XianyuSearcher()

            logger.info(f"开始多页搜索，尝试次数: {attempt + 1}/{max_retries + 1}")
            result = await searcher.search_multiple_pages(keyword, user_id, total_pages)

            # 如果成功获取到数据，直接返回
            if result.get('items') or not result.get('error'):
                logger.info(f"多页搜索成功，获取到 {len(result.get('items', []))} 条数据")
                return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"多页搜索商品失败 (尝试 {attempt + 1}/{max_retries + 1}): {error_msg}")

            # 如果是最后一次尝试，返回错误
            if attempt == max_retries:
                return {
                    'items': [],
                    'total': 0,
                    'error': f"搜索失败，已重试 {max_retries} 次: {error_msg}"
                }

            # 等待后重试
            logger.info(f"等待 {retry_delay} 秒后重试...")
            await asyncio.sleep(retry_delay)

        finally:
            # 确保搜索器被正确关闭
            if searcher:
                try:
                    await searcher.close_browser()
                except Exception as close_error:
                    logger.warning(f"关闭搜索器时出错: {str(close_error)}")

    # 理论上不会到达这里
    return {
        'items': [],
        'total': 0,
        'error': "未知错误"
    }



