#!/usr/bin/env python3
"""
闲鱼扫码登录工具
基于API接口实现二维码生成和Cookie获取（参照myfish-main项目）
"""

import asyncio
import time
import uuid
import json
import re
from random import random
from typing import Optional, Dict, Any
import httpx
import qrcode
import qrcode.constants
from loguru import logger
import hashlib
import threading


def generate_headers():
    """生成请求头"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'Referer': 'https://passport.goofish.com/',
        'Origin': 'https://passport.goofish.com',
    }


class GetLoginParamsError(Exception):
    """获取登录参数错误"""


class GetLoginQRCodeError(Exception):
    """获取登录二维码失败"""


class NotLoginError(Exception):
    """未登录错误"""


class QRLoginSession:
    """二维码登录会话"""

    def __init__(self, session_id: str, owner_user_id: int):
        self.session_id = session_id
        self.owner_user_id = owner_user_id
        self.status = 'waiting'  # waiting, scanned, success, expired, cancelled, verification_required
        self.qr_code_url = None
        self.qr_content = None
        self.cookies = {}
        self.unb = None
        self.created_time = time.time()
        self.expire_time = 300  # 5分钟过期
        self.params = {}  # 存储登录参数
        self.verification_url = None  # 风控验证URL

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.created_time > self.expire_time

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'session_id': self.session_id,
            'status': self.status,
            'qr_code_url': self.qr_code_url,
            'created_time': self.created_time,
            'is_expired': self.is_expired()
        }


class QRLoginManager:
    """二维码登录管理器"""

    def __init__(self):
        self.sessions: Dict[str, QRLoginSession] = {}
        self._sessions_lock = threading.RLock()
        self.headers = generate_headers()
        self.host = "https://passport.goofish.com"
        self.api_mini_login = f"{self.host}/mini_login.htm"
        self.api_generate_qr = f"{self.host}/newlogin/qrcode/generate.do"
        self.api_scan_status = f"{self.host}/newlogin/qrcode/query.do"
        self.api_h5_tk = "https://h5api.m.goofish.com/h5/mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get/1.0/"
        
        # 配置代理（如果需要的话，取消注释并修改代理地址）
        # self.proxy = "http://127.0.0.1:7890"
        self.proxy = None
        
        # 配置超时时间
        self.timeout = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=60.0)

    def _cookie_marshal(self, cookies: dict) -> str:
        """将Cookie字典转换为字符串"""
        return "; ".join([f"{k}={v}" for k, v in cookies.items()])

    async def _get_mh5tk(self, session: QRLoginSession) -> dict:
        """获取m_h5_tk和m_h5_tk_enc"""
        data = {"bizScene": "home"}
        data_str = json.dumps(data, separators=(',', ':'))
        t = str(int(time.time() * 1000))
        app_key = "34839810"

        # 先发一次 GET 请求，获取 cookie 中的 m_h5_tk
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=self.proxy) as client:
            try:
                resp = await client.get(self.api_h5_tk, headers=self.headers)
                cookies = {k: v for k, v in resp.cookies.items()}
                session.cookies.update(cookies)

                m_h5_tk = cookies.get("m_h5_tk", "")
                token = m_h5_tk.split("_")[0] if "_" in m_h5_tk else ""

                # 生成签名
                sign_input = f"{token}&{t}&{app_key}&{data_str}"
                sign = hashlib.md5(sign_input.encode()).hexdigest()

                # 构造最终请求参数
                params = {
                    "jsv": "2.7.2",
                    "appKey": app_key,
                    "t": t,
                    "sign": sign,
                    "v": "1.0",
                    "type": "originaljson",
                    "dataType": "json",
                    "timeout": 20000,
                    "api": "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get",
                    "data": data_str,
                }

                # 发请求正式获取数据，确保 token 有效
                await client.post(self.api_h5_tk, params=params, headers=self.headers, cookies=session.cookies)

                return cookies
            except httpx.ConnectTimeout:
                logger.error("获取m_h5_tk时连接超时")
                raise
            except httpx.ReadTimeout:
                logger.error("获取m_h5_tk时读取超时")
                raise
            except httpx.ConnectError:
                logger.error("获取m_h5_tk时连接错误")
                raise

    async def _get_login_params(self, session: QRLoginSession) -> dict:
        """获取二维码登录时需要的表单参数"""
        params = {
            "lang": "zh_cn",
            "appName": "xianyu",
            "appEntrance": "web",
            "styleType": "vertical",
            "bizParams": "",
            "notLoadSsoView": False,
            "notKeepLogin": False,
            "isMobile": False,
            "qrCodeFirst": False,
            "stie": 77,
            "rnd": random(),
        }

        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
            try:
                resp = await client.get(
                    self.api_mini_login,
                    params=params,
                    cookies=session.cookies,
                    headers=self.headers,
                )

                # 正则匹配需要的json数据
                pattern = r"window\.viewData\s*=\s*(\{.*?\});"
                match = re.search(pattern, resp.text)
                if match:
                    json_string = match.group(1)
                    view_data = json.loads(json_string)
                    data = view_data.get("loginFormData")
                    if data:
                        data["umidTag"] = "SERVER"
                        session.params.update(data)
                        return data
                    else:
                        raise GetLoginParamsError("未找到loginFormData")
                else:
                    raise GetLoginParamsError("获取登录参数失败")
            except httpx.ConnectTimeout:
                logger.error("获取登录参数时连接超时")
                raise
            except httpx.ReadTimeout:
                logger.error("获取登录参数时读取超时")
                raise
            except httpx.ConnectError:
                logger.error("获取登录参数时连接错误")
                raise
    
    async def generate_qr_code(self, owner_user_id: int) -> Dict[str, Any]:
        """生成绑定到当前用户的二维码。"""
        try:
            # 创建新的会话
            session_id = str(uuid.uuid4())
            session = QRLoginSession(session_id, owner_user_id)

            # 1. 获取m_h5_tk
            await self._get_mh5tk(session)
            logger.info(f"获取m_h5_tk成功: {session_id}")

            # 2. 获取登录参数
            login_params = await self._get_login_params(session)
            logger.info(f"获取登录参数成功: {session_id}")

            # 3. 生成二维码
            async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
                resp = await client.get(
                    self.api_generate_qr,
                    params=login_params,
                    headers=self.headers
                )
                logger.debug(f"二维码接口响应状态: {resp.status_code}")

                try:
                    results = resp.json()
                except Exception:
                    logger.exception("二维码接口返回不是JSON")
                    raise GetLoginQRCodeError("二维码接口返回格式异常")

                if results.get("content", {}).get("success") == True:
                    # 更新会话参数
                    session.params.update({
                        "t": results["content"]["data"]["t"],
                        "ck": results["content"]["data"]["ck"],
                    })

                    # 获取二维码内容
                    qr_content = results["content"]["data"]["codeContent"]
                    session.qr_content = qr_content

                    # 生成二维码图片（base64格式）
                    qr = qrcode.QRCode(
                        version=5,
                        error_correction=qrcode.constants.ERROR_CORRECT_L,
                        box_size=10,
                        border=2,
                    )
                    qr.add_data(qr_content)
                    qr.make()

                    # 将二维码转换为base64
                    from io import BytesIO
                    import base64

                    qr_img = qr.make_image()
                    buffer = BytesIO()
                    qr_img.save(buffer, format='PNG')
                    qr_base64 = base64.b64encode(buffer.getvalue()).decode()
                    qr_data_url = f"data:image/png;base64,{qr_base64}"

                    session.qr_code_url = qr_data_url
                    session.status = 'waiting'

                    # 保存会话
                    with self._sessions_lock:
                        self.sessions[session_id] = session

                    # 启动状态检查任务
                    asyncio.create_task(self._monitor_qr_status(session_id))

                    logger.info(f"二维码生成成功: {session_id}")
                    return {
                        'success': True,
                        'session_id': session_id,
                        'qr_code_url': qr_data_url
                    }
                else:
                    raise GetLoginQRCodeError("获取登录二维码失败")

        except httpx.ConnectTimeout as e:
            logger.error(f"连接超时: {e}")
            return {'success': False, 'message': f'连接超时，请检查网络或尝试使用代理'}
        except httpx.ReadTimeout as e:
            logger.error(f"读取超时: {e}")
            return {'success': False, 'message': f'读取超时，服务器响应过慢'}
        except httpx.ConnectError as e:
            logger.error(f"连接错误: {e}")
            return {'success': False, 'message': f'连接错误，请检查网络或代理设置'}
        except Exception:
            logger.exception("二维码生成过程中发生异常")
            return {'success': False, 'message': '生成二维码失败'}
    
    async def _poll_qrcode_status(self, params: dict, cookies: dict) -> httpx.Response:
        """获取二维码扫描状态"""
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, proxy=self.proxy) as client:
            resp = await client.post(
                self.api_scan_status,
                data=params,
                cookies=cookies,
                headers=self.headers,
            )
            return resp

    async def _monitor_qr_status(self, session_id: str):
        """监控二维码状态"""
        try:
            with self._sessions_lock:
                session = self.sessions.get(session_id)
            if not session:
                return

            logger.info(f"开始监控二维码状态: {session_id}")

            # 监控登录状态
            max_wait_time = 300  # 5分钟
            start_time = time.time()

            while time.time() - start_time < max_wait_time:
                try:
                    with self._sessions_lock:
                        if self.sessions.get(session_id) is not session:
                            break
                        params = dict(session.params)
                        cookies = dict(session.cookies)

                    # 网络请求必须在会话锁外执行。
                    resp = await self._poll_qrcode_status(params, cookies)
                    response_data = resp.json().get("content", {}).get("data", {})
                    qrcode_status = response_data.get("qrCodeStatus")
                    should_break = False
                    log_message = None

                    with self._sessions_lock:
                        if self.sessions.get(session_id) is not session:
                            break

                        if qrcode_status == "CONFIRMED":
                            if response_data.get("iframeRedirect") is True:
                                session.status = 'verification_required'
                                session.verification_url = response_data.get("iframeRedirectUrl")
                                log_message = ("warning", f"账号被风控，需要手机验证: {session_id}")
                            else:
                                session.status = 'success'
                                for k, v in resp.cookies.items():
                                    session.cookies[k] = v
                                    if k == 'unb':
                                        session.unb = v
                                log_message = ("info", f"扫码登录成功: {session_id}")
                            should_break = True
                        elif qrcode_status == "EXPIRED":
                            session.status = 'expired'
                            log_message = ("info", f"二维码已过期: {session_id}")
                            should_break = True
                        elif qrcode_status == "SCANED":
                            if session.status == 'waiting':
                                session.status = 'scanned'
                                log_message = ("info", f"二维码已扫描，等待确认: {session_id}")
                        elif qrcode_status != "NEW":
                            session.status = 'cancelled'
                            log_message = ("info", f"用户取消登录: {session_id}")
                            should_break = True

                    if log_message:
                        getattr(logger, log_message[0])(log_message[1])
                    if should_break:
                        break
                    await asyncio.sleep(0.8)  # 每0.8秒检查一次

                except Exception as e:
                    logger.error(f"监控二维码状态异常: {e}")
                    await asyncio.sleep(2)

            # 超时处理
            timed_out = False
            with self._sessions_lock:
                if (
                    self.sessions.get(session_id) is session
                    and session.status not in ['success', 'expired', 'cancelled', 'verification_required']
                ):
                    session.status = 'expired'
                    timed_out = True
            if timed_out:
                logger.info(f"二维码监控超时，标记为过期: {session_id}")

        except Exception as e:
            logger.error(f"监控二维码状态失败: {e}")
            with self._sessions_lock:
                session = self.sessions.get(session_id)
                if session:
                    session.status = 'expired'
    
    def _get_owned_session(self, session_id: str, owner_user_id: int) -> Optional[QRLoginSession]:
        with self._sessions_lock:
            session = self.sessions.get(session_id)
            if not session or session.owner_user_id != owner_user_id:
                return None
            return session

    def get_session_status(self, session_id: str, owner_user_id: int) -> Dict[str, Any]:
        """获取当前用户拥有的会话状态，不返回 Cookie。"""
        with self._sessions_lock:
            session = self._get_owned_session(session_id, owner_user_id)
            if not session:
                return {'status': 'not_found'}

            if session.is_expired() and session.status != 'success':
                session.status = 'expired'

            result = {
                'status': session.status,
                'session_id': session_id
            }

            # 如果需要验证，返回验证URL
            if session.status == 'verification_required' and session.verification_url:
                result['verification_url'] = session.verification_url
                result['message'] = '账号被风控，需要手机验证'

            return result

    def cleanup_expired_sessions(self):
        """清理过期会话"""
        with self._sessions_lock:
            expired_sessions = [
                session_id
                for session_id, session in self.sessions.items()
                if session.is_expired()
            ]
            for session_id in expired_sessions:
                self._destroy_session_locked(session_id)

        for session_id in expired_sessions:
            logger.info(f"清理过期会话: {session_id}")

    def consume_session_cookies(self, session_id: str, owner_user_id: int) -> Optional[Dict[str, str]]:
        """一次性消费当前用户会话 Cookie，并立即销毁敏感会话。"""
        with self._sessions_lock:
            session = self._get_owned_session(session_id, owner_user_id)
            if not session or session.status != 'success':
                return None
            result = {
                'cookies': self._cookie_marshal(dict(session.cookies)),
                'unb': session.unb
            }
            self.destroy_session(session_id)
            return result

    def _destroy_session_locked(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            session.cookies.clear()
            session.params.clear()
            session.qr_content = None
            session.qr_code_url = None
            session.verification_url = None
            session.unb = None

    def destroy_session(self, session_id: str) -> None:
        with self._sessions_lock:
            self._destroy_session_locked(session_id)

# 全局二维码登录管理器实例
qr_login_manager = QRLoginManager()
