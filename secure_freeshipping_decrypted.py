import asyncio
import time
from loguru import logger
from utils.xianyu_utils import trans_cookies, generate_sign


class SecureFreeshipping:
    def __init__(self, session, cookies_str, cookie_id):
        self.session = session
        self.cookies_str = cookies_str
        self.cookie_id = cookie_id
        self.cookies = trans_cookies(cookies_str) if cookies_str else {}
        
        # 这些属性将由主类传递
        self.current_token = None
        self.last_token_refresh_time = None
        self.token_refresh_interval = None

    def _safe_str(self, obj):
        """仅返回异常类型，避免异常文本携带请求参数或响应正文。"""
        return type(obj).__name__

    @staticmethod
    def _mask_id(value):
        text = str(value or "")
        if len(text) <= 4:
            return "***"
        return f"{text[:2]}***{text[-2:]}"

    async def update_config_cookies(self):
        """更新数据库中的cookies"""
        try:
            from db_manager import db_manager
            
            # 更新数据库中的Cookie
            db_manager.update_config_cookies(self.cookie_id, self.cookies_str)
            logger.debug(f"【{self.cookie_id}】Cookie已更新到数据库")
            
        except Exception as e:
            logger.error(f"【{self.cookie_id}】更新Cookie到数据库失败: {self._safe_str(e)}")

    async def auto_freeshipping(self, order_id, item_id, buyer_id, retry_count=0):
        """自动免拼发货 - 加密版本"""
        if retry_count >= 4:  # 最多重试3次
            logger.error("免拼发货发货失败，重试次数过多")
            return {"error": "免拼发货发货失败，重试次数过多"}

        # 确保session已创建
        if not self.session:
            raise Exception("Session未创建")

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.idle.groupon.activity.seller.freeshipping',
            'sessionOption': 'AutoLoginOnly',
        }

        data_val = '{"bizOrderId":"' + order_id + '", "itemId":' + item_id + ',"buyerId":' + buyer_id + '}'
        data = {
            'data': data_val,
        }
        
        logger.info(
            f"【{self.cookie_id}】准备免拼发货: "
            f"order_id={self._mask_id(order_id)}, item_id={self._mask_id(item_id)}"
        )

        # 始终从最新的cookies中获取_m_h5_tk token（刷新后cookies会被更新）
        token = trans_cookies(self.cookies_str).get('_m_h5_tk', '').split('_')[0] if trans_cookies(self.cookies_str).get('_m_h5_tk') else ''

        if token:
            logger.debug("Cookie 签名 Token 可用")
        else:
            logger.warning("Cookie 中没有找到签名 Token")

        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign

        try:
            logger.info(f"【{self.cookie_id}】开始自动免拼发货，订单ID: {self._mask_id(order_id)}")
            async with self.session.post(
                'https://h5api.m.goofish.com/h5/mtop.idle.groupon.activity.seller.freeshipping/1.0/',
                params=params,
                data=data
            ) as response:
                res_json = await response.json()

                # 检查并更新Cookie
                if 'set-cookie' in response.headers:
                    new_cookies = {}
                    for cookie in response.headers.getall('set-cookie', []):
                        if '=' in cookie:
                            name, value = cookie.split(';')[0].split('=', 1)
                            new_cookies[name.strip()] = value.strip()
                    
                    # 更新cookies
                    if new_cookies:
                        self.cookies.update(new_cookies)
                        # 生成新的cookie字符串
                        self.cookies_str = '; '.join([f"{k}={v}" for k, v in self.cookies.items()])
                        # 更新数据库中的Cookie
                        await self.update_config_cookies()
                        logger.debug("已更新Cookie到数据库")

                ret_code = res_json.get('ret', ['UNKNOWN'])[0] if res_json.get('ret') else 'UNKNOWN'
                logger.info(
                    f"【{self.cookie_id}】自动免拼发货响应已解析: "
                    f"http_status={response.status}, success={ret_code == 'SUCCESS::调用成功'}"
                )
                
                # 检查响应结果
                if res_json.get('ret') and res_json['ret'][0] == 'SUCCESS::调用成功':
                    logger.info(f"【{self.cookie_id}】✅ 自动免拼发货成功，订单ID: {self._mask_id(order_id)}")
                    return {"success": True, "order_id": order_id}
                else:
                    logger.warning(f"【{self.cookie_id}】❌ 自动免拼发货失败: api_ret_present={bool(res_json.get('ret'))}")
                    
                    return await self.auto_freeshipping(order_id, item_id, buyer_id, retry_count + 1)
                    

        except Exception as e:
            logger.error(f"【{self.cookie_id}】自动免拼发货API请求异常: {self._safe_str(e)}")
            await asyncio.sleep(0.5)
            
            # 网络异常也进行重试
            if retry_count < 2:
                logger.info(f"【{self.cookie_id}】网络异常，准备重试...")
                return await self.auto_freeshipping(order_id, item_id, buyer_id, retry_count + 1)
            
            return {"error": f"网络异常: {self._safe_str(e)}", "order_id": order_id}
