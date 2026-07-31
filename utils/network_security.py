import ipaddress
import socket
import ssl
import smtplib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver


class NetworkSecurityError(ValueError):
    pass


_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".home.arpa")
_BLOCKED_HEADERS = {
    "host",
    "proxy-authorization",
    "proxy-connection",
    "forwarded",
    "via",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-proto",
    "x-original-url",
    "x-rewrite-url",
    "connection",
    "transfer-encoding",
    "content-length",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
    "expect",
}
_FORBIDDEN_HEADER_CHARS = frozenset("\r\n")
_DANGEROUS_PORTS = {
    0, 1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 53, 69,
    79, 110, 111, 119, 123, 135, 137, 138, 139, 143, 161, 162, 389, 445,
    465, 512, 513, 514, 515, 587, 631, 873, 993, 995, 1080, 1433, 1521,
    2049, 2375, 2376, 3000, 3306, 3389, 5432, 5601, 5672, 5900, 5984,
    6379, 6443, 8086, 8500, 9042, 9092, 9200, 9300, 11211, 15672, 27017,
}


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def _validate_hostname(hostname: str) -> str:
    hostname = hostname.rstrip(".").lower()
    if not hostname:
        raise NetworkSecurityError("目标 URL 缺少主机名")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if hostname == "localhost" or "." not in hostname:
            raise NetworkSecurityError("不允许访问本地主机或单标签主机")
    if hostname.endswith(_BLOCKED_HOST_SUFFIXES):
        raise NetworkSecurityError("不允许访问本地域名")
    return hostname


def _resolve_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkSecurityError("目标主机 DNS 解析失败") from exc

    addresses = []
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise NetworkSecurityError("DNS 返回了无效 IP 地址") from exc
        if not ip.is_global:
            raise NetworkSecurityError("目标主机解析到非公网 IP 地址")
        normalized = str(ip)
        if normalized not in addresses:
            addresses.append(normalized)

    if not addresses:
        raise NetworkSecurityError("目标主机未解析到有效公网 IP 地址")
    return tuple(addresses)


def validate_public_url(url: str) -> ResolvedTarget:
    if not isinstance(url, str) or not url.strip():
        raise NetworkSecurityError("目标 URL 不能为空")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise NetworkSecurityError("目标 URL 格式无效") from exc

    if parsed.scheme.lower() not in ("http", "https"):
        raise NetworkSecurityError("仅允许 http 或 https URL")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkSecurityError("目标 URL 不允许包含用户信息")
    hostname = _validate_hostname(parsed.hostname or "")
    port = port or (443 if parsed.scheme.lower() == "https" else 80)
    if port <= 0 or port > 65535 or port in _DANGEROUS_PORTS:
        raise NetworkSecurityError("目标 URL 使用了危险端口")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        addresses = _resolve_public_addresses(hostname, port)
    else:
        if not literal.is_global:
            raise NetworkSecurityError("不允许访问非公网 IP 地址")
        addresses = (str(literal),)

    return ResolvedTarget(url=url.strip(), hostname=hostname, port=port, addresses=addresses)


def validate_outbound_headers(headers: Optional[Mapping[str, str]]) -> dict[str, str]:
    clean_headers = dict(headers or {})
    for name, value in list(clean_headers.items()):
        name_str = str(name).strip()
        value_str = str(value) if value is not None else ""
        if any(ch in name_str for ch in _FORBIDDEN_HEADER_CHARS):
            raise NetworkSecurityError(f"请求头名称包含禁止字符: {name_str[:80]}")
        if any(ch in value_str for ch in _FORBIDDEN_HEADER_CHARS):
            raise NetworkSecurityError(f"请求头值包含禁止字符: {value_str[:80]}")
        normalized = name_str.lower()
        if normalized in _BLOCKED_HEADERS or normalized.startswith("x-forwarded-"):
            raise NetworkSecurityError(f"不允许设置路由或逐跳请求头: {name}")
    return clean_headers


def validate_smtp_target(hostname: str, port: int, use_ssl: bool, use_starttls: bool) -> tuple[str, ...]:
    hostname = _validate_hostname(str(hostname or "").strip())
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise NetworkSecurityError("SMTP 端口无效") from exc

    if not ((port == 465 and use_ssl and not use_starttls) or
            (port == 587 and use_starttls and not use_ssl)):
        raise NetworkSecurityError("SMTP 仅允许 465+SSL 或 587+STARTTLS")
    return _resolve_public_addresses(hostname, port)


def send_pinned_smtp(
    hostname: str,
    port: int,
    use_ssl: bool,
    use_starttls: bool,
    username: str,
    password: str,
    from_addr: str,
    to_addrs: list[str],
    message_bytes: bytes,
    timeout: float = 15.0,
) -> None:
    """将已验证的邮件消息发送到预先解析的 IP 地址，阻止 SMTP DNS rebinding。

    此函数在执行 validate_smtp_target 后调用，直接连接到其返回的 IP，
    不再对 hostname 做新解析。对 465 端口先用 SSL 包裹裸 socket，
    再用 smtplib.SMTP 包装；587 端口用 starttls 升级。
    """
    addresses = validate_smtp_target(hostname, port, use_ssl, use_starttls)

    raw_sock: socket.socket | None = None
    last_err: Exception | None = None

    for address in addresses:
        try:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            raw_sock = socket.create_connection(
                (address, port), timeout=timeout, source_address=None
            )
            break
        except OSError as exc:
            last_err = exc
            raw_sock = None
            continue

    if raw_sock is None:
        raise NetworkSecurityError(
            f"无法连接到 SMTP 服务器已解析地址: {last_err}"
        ) from last_err

    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
            wrapped = ctx.wrap_socket(raw_sock, server_hostname=hostname)
            server = smtplib.SMTP()
            server.sock = wrapped
            server.file = wrapped.makefile("rb")
            server.ehlo()
        else:
            server = smtplib.SMTP()
            server.sock = raw_sock
            server.file = raw_sock.makefile("rb")
            server.ehlo()
            ctx = ssl.create_default_context()
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
            server.starttls(context=ctx)
            server.ehlo()

        server.login(username, password)
        server.sendmail(from_addr, to_addrs, message_bytes)
        server.quit()
    except Exception:
        try:
            server.quit()
        except Exception:
            pass
        raise


class PinnedPublicResolver(AbstractResolver):
    def __init__(self, target: ResolvedTarget):
        self._target = target

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        if host.rstrip(".").lower() != self._target.hostname or port != self._target.port:
            raise OSError("拒绝解析未经验证的目标")
        records = []
        for address in self._target.addresses:
            ip = ipaddress.ip_address(address)
            address_family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            if family not in (socket.AF_UNSPEC, address_family):
                continue
            records.append({
                "hostname": host,
                "host": address,
                "port": port,
                "family": address_family,
                "proto": 0,
                "flags": 0,
            })
        return records

    async def close(self):
        return None


async def create_isolated_session(
    target: ResolvedTarget,
    timeout: float = 10,
) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        resolver=PinnedPublicResolver(target),
        use_dns_cache=False,
    )
    return aiohttp.ClientSession(
        connector=connector,
        cookie_jar=aiohttp.DummyCookieJar(),
        timeout=aiohttp.ClientTimeout(total=float(timeout)),
        trust_env=False,
    )


@asynccontextmanager
async def secure_request(
    method: str,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 10,
    **kwargs,
):
    target = validate_public_url(url)
    safe_headers = validate_outbound_headers(headers)
    session = await create_isolated_session(target, timeout)
    try:
        async with session.request(
            method.upper(),
            target.url,
            headers=safe_headers,
            allow_redirects=False,
            **kwargs,
        ) as response:
            yield response
    finally:
        await session.close()
