"""Feishu inventory and review bridge for local and Docker deployments.

Two authentication modes are supported:
- bot: calls Feishu OpenAPI with a self-built app tenant access token.
- user_cli: reuses an existing lark-cli user session for Windows compatibility.

Every inventory request verifies that one configured group message still points to
one configured Base before returning live records. Description submissions may
write only the draft field and always reset the review checkbox to false.
"""

import argparse
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(PROJECT_DIR / ".env.bridge", override=True)

CHAT_ID = os.environ.get("FEISHU_INVENTORY_CHAT_ID", "").strip()
SOURCE_MESSAGE_ID = os.environ.get("FEISHU_INVENTORY_SOURCE_MESSAGE_ID", "").strip()
BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "").strip()
TABLE_ID = os.environ.get("FEISHU_TABLE_ID", "").strip()
BRIDGE_TOKEN = os.environ.get("FEISHU_INVENTORY_BRIDGE_TOKEN", "").strip()
AUTH_MODE = os.environ.get("FEISHU_AUTH_MODE", "user_cli").strip().lower()
APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
OPEN_API_BASE_URL = os.environ.get(
    "FEISHU_OPEN_API_BASE_URL", "https://open.feishu.cn"
).rstrip("/")
REQUEST_TIMEOUT = int(os.environ.get("FEISHU_REQUEST_TIMEOUT", "20"))
MAX_MEDIA_BYTES = int(os.environ.get("FEISHU_MAX_MEDIA_BYTES", str(50 * 1024 * 1024)))
MAX_DESCRIPTION_BYTES = int(os.environ.get("FEISHU_MAX_DESCRIPTION_BYTES", "12000"))
LARK_CLI = os.environ.get("LARK_CLI_PATH", "").strip()
LARK_NODE = os.environ.get("LARK_NODE_PATH", "").strip()
LARK_CLI_SCRIPT = os.environ.get("LARK_CLI_SCRIPT", "").strip()

cli_root = Path.home() / ".workbuddy" / "binaries" / "node" / "cli-connector-packages"
if not LARK_CLI_SCRIPT:
    default_script = cli_root / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
    if default_script.exists():
        LARK_CLI_SCRIPT = str(default_script)
if not LARK_NODE:
    version_root = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    managed_nodes = sorted(
        [*version_root.glob("*/node.exe"), *version_root.glob("*/bin/node")],
        reverse=True,
    )
    if managed_nodes:
        LARK_NODE = str(managed_nodes[0])
    else:
        LARK_NODE = shutil.which("node.exe") or shutil.which("node") or ""
if not LARK_CLI:
    LARK_CLI = (
        shutil.which("lark-cli.exe")
        or shutil.which("lark-cli.cmd")
        or shutil.which("lark-cli")
        or "lark-cli"
    )

BASE_URL_PATTERN = re.compile(r"https://[^\s\"']+/base/([A-Za-z0-9]+)")
SENSITIVE_VALUE_PATTERN = re.compile(
    r'(?i)(access[_-]?token|refresh[_-]?token|app[_-]?secret|authorization|bearer)'
    r'(["\s:=]+)([^"\s,}]+)'
)
LOG_DIR = PROJECT_DIR / "logs"
LOG_PATH = LOG_DIR / "feishu_inventory_bridge.log"
LOGGER = logging.getLogger("feishu_inventory_bridge")
CLI_LOCK = threading.Lock()
TOKEN_LOCK = threading.Lock()
TOKEN_CACHE = {"value": "", "expires_at": 0.0}
PROJECT_FIELDS = [
    "角色名称",
    "作品来源",
    "码数",
    "总库存",
    "已租出",
    "状态",
    "租期价格",
    "押金",
    "配件清单",
    "预计归还日期",
    "备注",
    "实物图",
    "图片描述",
    "描述已审核",
]


class BridgeError(RuntimeError):
    pass


def _configure_logging() -> None:
    if LOGGER.handlers:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)
    LOGGER.propagate = False


def _redact(text: str) -> str:
    redacted = text
    for value in (BRIDGE_TOKEN, BASE_TOKEN, APP_ID, APP_SECRET, TOKEN_CACHE["value"]):
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return SENSITIVE_VALUE_PATTERN.sub(r"\1\2[REDACTED]", redacted)


def _describe_cli_error(payload: dict, returncode: int) -> str:
    error = payload.get("error") or {}
    parts = [f"lark-cli exit {returncode}"]
    for key in ("type", "subtype", "code", "message", "hint"):
        value = error.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    missing_scopes = error.get("missing_scopes")
    if missing_scopes:
        parts.append(f"missing_scopes={missing_scopes}")
    return _redact("; ".join(parts))


def _validate_config() -> None:
    if AUTH_MODE not in {"bot", "user_cli"}:
        raise BridgeError("FEISHU_AUTH_MODE must be bot or user_cli")
    required = [
        ("FEISHU_INVENTORY_CHAT_ID", CHAT_ID),
        ("FEISHU_INVENTORY_SOURCE_MESSAGE_ID", SOURCE_MESSAGE_ID),
        ("FEISHU_BASE_TOKEN", BASE_TOKEN),
        ("FEISHU_TABLE_ID", TABLE_ID),
        ("FEISHU_INVENTORY_BRIDGE_TOKEN", BRIDGE_TOKEN),
    ]
    if AUTH_MODE == "bot":
        required.extend(
            [
                ("FEISHU_APP_ID", APP_ID),
                ("FEISHU_APP_SECRET", APP_SECRET),
            ]
        )
    missing = [name for name, value in required if not value]
    if missing:
        raise BridgeError("missing configuration: " + ", ".join(missing))


def _run_lark(args: list[str], timeout: int = 30) -> dict:
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    action = " ".join(args[:2]) if len(args) >= 2 else "lark-cli"
    LOGGER.info("running %s as user", action)
    command = (
        [LARK_NODE, LARK_CLI_SCRIPT, *args]
        if LARK_NODE and LARK_CLI_SCRIPT
        else [LARK_CLI, *args]
    )
    try:
        with CLI_LOCK:
            with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
                mode="w+b"
            ) as stderr_file:
                result = subprocess.run(
                    command,
                    cwd=PROJECT_DIR,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout,
                    env=env,
                    check=False,
                )
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout = stdout_file.read().decode("utf-8", errors="replace").strip()
                stderr = stderr_file.read().decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.error("failed to run %s: %s", action, _redact(str(exc)))
        raise
    output = stdout or stderr
    if not output:
        message = f"lark-cli returned no output (exit {result.returncode})"
        LOGGER.error("%s failed: %s", action, message)
        raise BridgeError(message)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        preview = _redact(output[:500].replace("\r", " ").replace("\n", " "))
        LOGGER.error(
            "%s returned invalid JSON (exit %s): %s",
            action,
            result.returncode,
            preview,
        )
        raise BridgeError("lark-cli returned invalid JSON") from exc
    if result.returncode != 0 or payload.get("ok") is not True:
        message = _describe_cli_error(payload, result.returncode)
        LOGGER.error("%s failed: %s", action, message)
        raise BridgeError(message)
    LOGGER.info("%s succeeded", action)
    return payload


def _request_json(method: str, path: str, **kwargs) -> dict:
    url = f"{OPEN_API_BASE_URL}{path}"
    try:
        response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise BridgeError(f"Feishu OpenAPI request failed: {type(exc).__name__}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise BridgeError(f"Feishu OpenAPI returned non-JSON HTTP {response.status_code}") from exc
    if response.status_code >= 400 or payload.get("code") != 0:
        code = payload.get("code", "unknown")
        message = _redact(str(payload.get("msg") or "request failed"))
        raise BridgeError(
            f"Feishu OpenAPI {method} {path.split('?')[0]} failed: "
            f"HTTP {response.status_code}, code={code}, msg={message}"
        )
    return payload


def _get_tenant_access_token() -> str:
    now = time.monotonic()
    with TOKEN_LOCK:
        if TOKEN_CACHE["value"] and TOKEN_CACHE["expires_at"] - now > 300:
            return TOKEN_CACHE["value"]
        payload = _request_json(
            "POST",
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        token = str(payload.get("tenant_access_token") or "")
        expires_in = int(payload.get("expire") or 0)
        if not token or expires_in <= 0:
            raise BridgeError("Feishu tenant access token response is incomplete")
        TOKEN_CACHE["value"] = token
        TOKEN_CACHE["expires_at"] = now + expires_in
        LOGGER.info("tenant access token refreshed; expires_in=%s", expires_in)
        return token


def _bot_headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_tenant_access_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _extract_message_content(message: dict) -> str:
    body = message.get("body") or {}
    content = body.get("content")
    if content is None:
        content = message.get("content")
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return content
        return json.dumps(decoded, ensure_ascii=False)
    return json.dumps(content or {}, ensure_ascii=False)


def _verify_group_source_cli() -> dict:
    payload = _run_lark(
        [
            "im",
            "+messages-mget",
            "--as",
            "user",
            "--message-ids",
            SOURCE_MESSAGE_ID,
            "--no-reactions",
            "--format",
            "json",
        ]
    )
    messages = (payload.get("data") or {}).get("messages") or []
    message = next(
        (item for item in messages if item.get("message_id") == SOURCE_MESSAGE_ID),
        None,
    )
    if not message:
        raise BridgeError("configured inventory source message is not accessible")
    return _validate_source_message(message)


def _verify_group_source_bot() -> dict:
    message_id = quote(SOURCE_MESSAGE_ID, safe="")
    payload = _request_json(
        "GET",
        f"/open-apis/im/v1/messages/{message_id}",
        headers=_bot_headers(),
        params={"user_id_type": "open_id"},
    )
    messages = (payload.get("data") or {}).get("items") or []
    message = next(
        (item for item in messages if item.get("message_id") == SOURCE_MESSAGE_ID),
        None,
    )
    if not message:
        raise BridgeError("configured inventory source message is not accessible")
    return _validate_source_message(message)


def _validate_source_message(message: dict) -> dict:
    if message.get("chat_id") != CHAT_ID:
        raise BridgeError("inventory source message is not in the configured chat")
    content = _extract_message_content(message)
    tokens = BASE_URL_PATTERN.findall(content)
    if BASE_TOKEN not in tokens:
        raise BridgeError("configured Base is no longer linked by the source message")
    return {
        "chat_id": CHAT_ID,
        "message_id": SOURCE_MESSAGE_ID,
        "message_create_time": message.get("create_time"),
        "base_token": BASE_TOKEN,
    }


def _fetch_records_cli() -> dict:
    args = [
        "base",
        "+record-list",
        "--as",
        "user",
        "--base-token",
        BASE_TOKEN,
        "--table-id",
        TABLE_ID,
        "--limit",
        "200",
    ]
    for field in PROJECT_FIELDS:
        args.extend(["--field-id", field])
    args.extend(["--format", "json"])
    payload = _run_lark(args)
    data = payload.get("data") or {}
    if data.get("has_more"):
        raise BridgeError("inventory table exceeds 200 records; pagination is required")
    fields = data.get("fields") or []
    rows = data.get("data") or []
    record_ids = data.get("record_id_list") or []
    records = []
    for row_index, row in enumerate(rows):
        record = {
            field: row[index] if index < len(row) else None
            for index, field in enumerate(fields)
        }
        if row_index < len(record_ids):
            record["_record_id"] = record_ids[row_index]
        records.append(record)
    return {"fields": fields, "records": records, "record_count": len(records)}


def _fetch_records_bot() -> dict:
    records = []
    page_token = ""
    while True:
        params = {"page_size": 500, "user_id_type": "open_id"}
        if page_token:
            params["page_token"] = page_token
        payload = _request_json(
            "POST",
            f"/open-apis/bitable/v1/apps/{quote(BASE_TOKEN, safe='')}"
            f"/tables/{quote(TABLE_ID, safe='')}/records/search",
            headers=_bot_headers(),
            params=params,
            json={"field_names": PROJECT_FIELDS, "automatic_fields": False},
        )
        data = payload.get("data") or {}
        for item in data.get("items") or []:
            record = dict(item.get("fields") or {})
            record["_record_id"] = item.get("record_id")
            records.append(record)
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "")
        if not page_token:
            raise BridgeError("Feishu record pagination did not return page_token")
    return {
        "fields": PROJECT_FIELDS,
        "records": records,
        "record_count": len(records),
    }


def get_inventory() -> dict:
    _validate_config()
    if AUTH_MODE == "bot":
        source = _verify_group_source_bot()
        inventory = _fetch_records_bot()
    else:
        source = _verify_group_source_cli()
        inventory = _fetch_records_cli()
    return {
        "ok": True,
        "source_verified": True,
        "auth_mode": AUTH_MODE,
        "source": source,
        **inventory,
    }


def _find_verified_records(record_ids: list[str]) -> list[str]:
    inventory = get_inventory()
    available = {
        str(record.get("_record_id") or "") for record in inventory["records"]
    }
    requested = list(dict.fromkeys(record_ids))
    if not requested or any(not record_id for record_id in requested):
        raise BridgeError("record_ids must contain at least one valid record ID")
    if any(record_id not in available for record_id in requested):
        raise BridgeError("description target is not present in verified inventory")
    return requested


def _update_description_bot(record_id: str, description: str) -> None:
    payload = _request_json(
        "PUT",
        f"/open-apis/bitable/v1/apps/{quote(BASE_TOKEN, safe='')}"
        f"/tables/{quote(TABLE_ID, safe='')}/records/{quote(record_id, safe='')}",
        headers=_bot_headers(),
        json={"fields": {"图片描述": description, "描述已审核": False}},
    )
    if not (payload.get("data") or {}).get("record"):
        raise BridgeError("Feishu description update response is incomplete")


def _update_description_cli(record_id: str, description: str) -> None:
    _run_lark(
        [
            "base",
            "+record-upsert",
            "--as",
            "user",
            "--base-token",
            BASE_TOKEN,
            "--table-id",
            TABLE_ID,
            "--record-id",
            record_id,
            "--json",
            json.dumps(
                {"图片描述": description, "描述已审核": False}, ensure_ascii=False
            ),
            "--format",
            "json",
        ]
    )


def submit_description(record_ids: list[str], description: str) -> dict:
    description = description.strip()
    if not description:
        raise BridgeError("description must not be empty")
    if len(description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        raise BridgeError("description exceeds configured size limit")
    verified_ids = _find_verified_records(record_ids)
    updated = []
    for record_id in verified_ids:
        if AUTH_MODE == "bot":
            _update_description_bot(record_id, description)
        else:
            _update_description_cli(record_id, description)
        updated.append(record_id)
    LOGGER.info(
        "submitted description for human review; records=%s; bytes=%s; review_reset=true",
        len(updated),
        len(description.encode("utf-8")),
    )
    return {
        "ok": True,
        "updated": len(updated),
        "record_ids": updated,
        "review_required": True,
    }


def _find_verified_attachment(file_token: str) -> dict:
    inventory = get_inventory()
    for record in inventory["records"]:
        attachments = record.get("实物图")
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if isinstance(attachment, dict) and hmac.compare_digest(
                str(attachment.get("file_token") or ""), file_token
            ):
                return attachment
    raise BridgeError("requested media is not present in verified inventory")


def _download_cli_media(file_token: str, attachment: dict) -> tuple[bytes, str, str]:
    media_dir = PROJECT_DIR / ".bridge_media_cache"
    media_dir.mkdir(exist_ok=True)
    filename = str(attachment.get("name") or "media.bin")
    output_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
    output_path = media_dir / output_name
    output_relative = str(output_path.relative_to(PROJECT_DIR))
    args = [
        "base",
        "+record-download-attachment",
        "--as",
        "user",
        "--base-token",
        BASE_TOKEN,
        "--table-id",
        TABLE_ID,
        "--file-token",
        file_token,
        "--output",
        output_relative,
        "--format",
        "json",
    ]
    try:
        _run_lark(args, timeout=max(30, REQUEST_TIMEOUT))
        if not output_path.exists():
            raise BridgeError("lark-cli did not create the requested media file")
        if output_path.stat().st_size > MAX_MEDIA_BYTES:
            raise BridgeError("requested media exceeds configured size limit")
        body = output_path.read_bytes()
        content_type = str(attachment.get("type") or "application/octet-stream")
        LOGGER.info("downloaded verified media with user_cli; bytes=%s", len(body))
        return body, content_type, filename
    finally:
        if output_path.exists():
            output_path.unlink()


def _download_bot_media(file_token: str, attachment: dict) -> tuple[bytes, str, str]:
    media_url = str(attachment.get("url") or "")
    if media_url.startswith(f"{OPEN_API_BASE_URL}/open-apis/"):
        path = media_url[len(OPEN_API_BASE_URL):]
        url = media_url
    else:
        path = f"/open-apis/drive/v1/medias/{quote(file_token, safe='')}/download"
        url = f"{OPEN_API_BASE_URL}{path}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {_get_tenant_access_token()}"},
            timeout=REQUEST_TIMEOUT,
            stream=True,
        )
    except requests.RequestException as exc:
        raise BridgeError(f"Feishu media request failed: {type(exc).__name__}") from exc
    if response.status_code not in (200, 206):
        raise BridgeError(f"Feishu media download failed: HTTP {response.status_code}")
    content_length = int(response.headers.get("Content-Length") or 0)
    if content_length > MAX_MEDIA_BYTES:
        raise BridgeError("requested media exceeds configured size limit")
    chunks = []
    total = 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_MEDIA_BYTES:
            raise BridgeError("requested media exceeds configured size limit")
        chunks.append(chunk)
    content_type = response.headers.get("Content-Type") or str(
        attachment.get("type") or "application/octet-stream"
    )
    filename = str(attachment.get("name") or "media.bin")
    LOGGER.info("downloaded verified media; bytes=%s; path=%s", total, path.split("?")[0])
    return b"".join(chunks), content_type, filename


class Handler(BaseHTTPRequestHandler):
    server_version = "FeishuInventoryBridge/2.0"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {BRIDGE_TOKEN}"
        return bool(BRIDGE_TOKEN) and hmac.compare_digest(supplied, expected)

    def _read_json_body(self) -> dict:
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise BridgeError("invalid Content-Length") from exc
        if content_length <= 0 or content_length > MAX_DESCRIPTION_BYTES + 64 * 1024:
            raise BridgeError("invalid request body size")
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise BridgeError("request body must be a JSON object")
        return payload

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if path != "/descriptions/pending":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            payload = self._read_json_body()
            record_ids = payload.get("record_ids")
            description = payload.get("description")
            if not isinstance(record_ids, list) or not all(
                isinstance(record_id, str) for record_id in record_ids
            ):
                raise BridgeError("record_ids must be a list of strings")
            if not isinstance(description, str):
                raise BridgeError("description must be a string")
            self._send_json(200, submit_description(record_ids, description))
        except BridgeError as exc:
            message = _redact(str(exc))
            LOGGER.warning("description submission rejected: %s", message)
            self._send_json(400, {"ok": False, "error": message})
        except Exception:
            LOGGER.error("description submission failed:\n%s", _redact(traceback.format_exc()))
            self._send_json(500, {"ok": False, "error": "internal error"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "configured": bool(BRIDGE_TOKEN),
                    "auth_mode": AUTH_MODE,
                    "credential_configured": (
                        bool(APP_ID and APP_SECRET) if AUTH_MODE == "bot" else True
                    ),
                },
            )
            return
        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        if path.startswith("/media-authorized/"):
            file_token = path.removeprefix("/media-authorized/").strip()
            if not file_token or "/" in file_token:
                self._send_json(400, {"ok": False, "error": "invalid media token"})
                return
            try:
                attachment = _find_verified_attachment(file_token)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "authorized": True,
                        "name": str(attachment.get("name") or ""),
                    },
                )
            except BridgeError as exc:
                message = _redact(str(exc))
                LOGGER.warning("media authorization unavailable: %s", message)
                self._send_json(503, {"ok": False, "authorized": False, "error": message})
            return
        if path.startswith("/media/"):
            file_token = path.removeprefix("/media/").strip()
            if not file_token or "/" in file_token:
                self._send_json(400, {"ok": False, "error": "invalid media token"})
                return
            try:
                attachment = _find_verified_attachment(file_token)
                if AUTH_MODE == "bot":
                    body, content_type, filename = _download_bot_media(file_token, attachment)
                else:
                    body, content_type, filename = _download_cli_media(file_token, attachment)
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f'attachment; filename="{quote(filename)}"')
                self.send_header("Cache-Control", "private, max-age=300")
                self.end_headers()
                self.wfile.write(body)
            except BridgeError as exc:
                message = _redact(str(exc))
                LOGGER.warning("media request unavailable: %s", message)
                self._send_json(503, {"ok": False, "error": message})
            return
        if path != "/inventory":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            self._send_json(200, get_inventory())
        except (BridgeError, subprocess.TimeoutExpired, OSError) as exc:
            message = _redact(str(exc))
            LOGGER.warning("inventory request unavailable: %s", message)
            self._send_json(
                503,
                {"ok": False, "source_verified": False, "error": message},
            )
        except Exception:
            LOGGER.error("inventory request failed:\n%s", _redact(traceback.format_exc()))
            self._send_json(
                500,
                {"ok": False, "source_verified": False, "error": "internal error"},
            )

    def log_message(self, format_string: str, *args) -> None:
        LOGGER.info(
            "%s %s",
            self.client_address[0],
            _redact(format_string % args),
        )


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host", default=os.environ.get("FEISHU_INVENTORY_BRIDGE_HOST", "0.0.0.0")
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FEISHU_INVENTORY_BRIDGE_PORT", "8765")),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    _validate_config()
    if args.check:
        result = get_inventory()
        print(
            json.dumps(
                {
                    "ok": result["ok"],
                    "source_verified": result["source_verified"],
                    "auth_mode": result["auth_mode"],
                    "record_count": result["record_count"],
                },
                ensure_ascii=False,
            )
        )
        return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    LOGGER.info("listening on %s:%s; auth_mode=%s", args.host, args.port, AUTH_MODE)
    server.serve_forever()


if __name__ == "__main__":
    main()
