import ast
import re
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "reply_server.py").read_text(encoding="utf-8")
LOGIN_HTML = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
AI_ENGINE = (ROOT / "ai_reply_engine.py").read_text(encoding="utf-8")
XIANYU_AUTO = (ROOT / "XianyuAutoAsync.py").read_text(encoding="utf-8")
SECURE_CONFIRM = (ROOT / "secure_confirm_decrypted.py").read_text(encoding="utf-8")
SECURE_FREESHIPPING = (ROOT / "secure_freeshipping_decrypted.py").read_text(encoding="utf-8")
QR_LOGIN = (ROOT / "utils" / "qr_login.py").read_text(encoding="utf-8")
ITEM_SEARCH = (ROOT / "utils" / "item_search.py").read_text(encoding="utf-8")
DB_MANAGER = (ROOT / "db_manager.py").read_text(encoding="utf-8")


def function_source(name: str) -> str:
    match = re.search(
        rf"^(?:async )?def {re.escape(name)}\b[\s\S]*?(?=^(?:async )?def |^@app\.|\Z)",
        SERVER,
        re.MULTILINE,
    )
    if not match:
        raise AssertionError(f"function not found: {name}")
    return match.group(0)


class WebSecurityRegressionTests(unittest.TestCase):
    def test_admin_only_endpoints_use_admin_dependency(self):
        for name in (
            "get_logs",
            "get_log_stats",
            "clear_logs",
            "reload_cache",
            "debug_keywords_table_info",
            "save_default_ai_reply_settings",
        ):
            self.assertIn("Depends(require_admin)", function_source(name), name)

    def test_cross_tenant_mutations_check_ownership(self):
        cookie_guarded = (
            "delete_account_notifications",
            "test_ai_reply",
            "get_all_items_from_account",
            "get_items_by_page",
            "update_item_multi_spec",
            "update_item_multi_quantity_delivery",
        )
        for name in cookie_guarded:
            self.assertIn("require_user_cookie(", function_source(name), name)

        for name in (
            "get_notification_channel",
            "update_notification_channel",
            "delete_notification_channel",
            "set_message_notification",
        ):
            self.assertIn("require_user_channel(", function_source(name), name)

        self.assertIn("get_card_by_id(card_id, current_user['user_id'])", function_source("delete_card"))
        self.assertIn("删除列表包含无权限账号", function_source("batch_delete_items"))

    def test_pending_reviews_are_scoped_to_owned_cookies(self):
        self.assertIn("cookie_id IN", function_source("get_pending_reviews"))
        self.assertIn("cookie_id IN", function_source("get_pending_count"))
        self.assertIn("row[0] not in user_cookies", function_source("resolve_pending_review"))

    def test_captcha_grant_is_required_and_one_time(self):
        self.assertIn("_grant_captcha_session(request.session_id)", function_source("verify_captcha"))
        self.assertIn("_consume_captcha_session(request.session_id)", function_source("send_verification_code"))
        self.assertIn("_captcha_grants.pop(session_id, None)", function_source("_consume_captcha_session"))

    def test_login_rate_limit_is_enforced(self):
        source = function_source("login")
        self.assertIn("_is_login_rate_limited(identity)", source)
        self.assertIn("_record_login_failure(identity)", source)
        self.assertIn("_clear_login_failures(identity)", source)
        self.assertIn("status_code=429", source)

    def test_login_page_has_safe_session_and_redirect(self):
        self.assertIn("window.crypto.randomUUID", LOGIN_HTML)
        self.assertIn("redirectUrl.startsWith('/')", LOGIN_HTML)
        self.assertIn("!redirectUrl.startsWith('//')", LOGIN_HTML)
        self.assertNotIn("captchaStatus.innerHTML", LOGIN_HTML)
        self.assertNotIn("codeStatus.innerHTML", LOGIN_HTML)

    def test_sensitive_prompt_and_reply_are_not_logged(self):
        self.assertNotIn("发送的prompt: {prompt}", AI_ENGINE)
        self.assertNotIn("请求数据: {json.dumps(data", AI_ENGINE)
        self.assertNotIn("AI回复生成成功 (账号: {cookie_id}): {reply}", AI_ENGINE)

    def test_production_call_chain_does_not_log_sensitive_payloads(self):
        forbidden_by_source = {
            "secure_confirm": (
                ("使用cookies中的_m_h5_tk token: {token}", SECURE_CONFIRM),
                ("自动确认发货响应: {res_json}", SECURE_CONFIRM),
            ),
            "secure_freeshipping": (
                ("自动免拼发货响应: {res_json}", SECURE_FREESHIPPING),
            ),
            "xianyu_auto": (
                ("完整消息结构: {message}", XIANYU_AUTO),
                ("指定商品回复内容: {formatted_reply}", XIANYU_AUTO),
                ("使用默认回复: {formatted_reply}", XIANYU_AUTO),
                ("AI回复生成成功: {reply}", XIANYU_AUTO),
                ("响应内容: {response_text}", XIANYU_AUTO),
                ("POST请求参数: {json.dumps(params", XIANYU_AUTO),
                ("{cookie['value'][:50]}", XIANYU_AUTO),
                ("CDN URL: {cdn_url}", XIANYU_AUTO),
                ("原始消息: {message_data}", XIANYU_AUTO),
            ),
            "ai_engine": (
                ("{response.status_code} - {response.text}", AI_ENGINE),
                ("请求URL: {e.response.url}", AI_ENGINE),
                ("请求URL: {e.request.url}", AI_ENGINE),
                ("{message[:30]}", AI_ENGINE),
                ("-> {cdn_url}", AI_ENGINE),
            ),
        }
        for source_name, checks in forbidden_by_source.items():
            for pattern, source in checks:
                self.assertNotIn(pattern, source, f"{source_name}: {pattern}")

    def test_qr_sessions_are_owner_bound_and_one_time(self):
        generate = function_source("generate_qr_code")
        check = function_source("check_qr_code_status")
        self.assertIn("generate_qr_code(current_user['user_id'])", generate)
        self.assertIn("get_session_status(\n                session_id, current_user['user_id']", check)
        self.assertIn("consume_session_cookies", check)
        self.assertIn("owner_user_id", QR_LOGIN)
        self.assertIn("self.destroy_session(session_id)", QR_LOGIN)
        self.assertNotIn("result['cookies']", QR_LOGIN)
        self.assertNotIn("'生成二维码失败: {str(e)}'", generate)
        self.assertNotIn("'生成二维码失败: {str(e)}'", QR_LOGIN)

    def test_qr_session_runtime_owner_isolation_and_one_time_consumption(self):
        from utils.qr_login import QRLoginManager, QRLoginSession

        manager = QRLoginManager()
        session = QRLoginSession("session-1", owner_user_id=101)
        session.status = "success"
        session.cookies = {"sid": "secret"}
        session.unb = "account-1"
        manager.sessions[session.session_id] = session

        self.assertEqual(
            manager.get_session_status(session.session_id, owner_user_id=202),
            {"status": "not_found"},
        )
        owner_status = manager.get_session_status(session.session_id, owner_user_id=101)
        self.assertNotIn("cookies", owner_status)
        self.assertNotIn("unb", owner_status)
        self.assertIsNone(manager.consume_session_cookies(session.session_id, owner_user_id=202))

        consumed = manager.consume_session_cookies(session.session_id, owner_user_id=101)
        self.assertEqual(consumed, {"cookies": "sid=secret", "unb": "account-1"})
        self.assertNotIn(session.session_id, manager.sessions)
        self.assertEqual(session.cookies, {})
        self.assertIsNone(session.unb)
        self.assertIsNone(manager.consume_session_cookies(session.session_id, owner_user_id=101))

    def test_qr_session_cookie_consumption_is_atomic_across_threads(self):
        from utils.qr_login import QRLoginManager, QRLoginSession

        manager = QRLoginManager()
        session = QRLoginSession("session-concurrent", owner_user_id=101)
        session.status = "success"
        session.cookies = {"sid": "secret", "token": "sensitive"}
        session.params = {"ck": "sensitive-param"}
        session.unb = "account-1"
        session.qr_content = "sensitive-qr"
        session.qr_code_url = "data:image/png;base64,sensitive"
        session.verification_url = "https://example.invalid/sensitive"
        with manager._sessions_lock:
            manager.sessions[session.session_id] = session

        barrier = threading.Barrier(3)
        results = []

        def consume():
            barrier.wait()
            results.append(
                manager.consume_session_cookies(session.session_id, owner_user_id=101)
            )

        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        successes = [result for result in results if result is not None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(
            successes[0],
            {"cookies": "sid=secret; token=sensitive", "unb": "account-1"},
        )
        self.assertEqual(results.count(None), 1)
        with manager._sessions_lock:
            self.assertNotIn(session.session_id, manager.sessions)
        self.assertEqual(session.cookies, {})
        self.assertEqual(session.params, {})
        self.assertIsNone(session.unb)
        self.assertIsNone(session.qr_content)
        self.assertIsNone(session.qr_code_url)
        self.assertIsNone(session.verification_url)

    def test_item_search_requires_authentication_and_user_cookie_scope(self):
        for name in ("search_items", "search_multiple_pages"):
            source = function_source(name)
            self.assertIn("Depends(get_current_user)", source)
            self.assertIn("_consume_item_search_quota(user_id)", source)
            self.assertIn("_item_search_semaphore.acquire()", source)
            self.assertIn("user_id=user_id", source)
            self.assertNotIn('response_data["error"] = has_error', source)
        self.assertIn("db_manager.get_all_cookies(user_id)", ITEM_SEARCH)
        self.assertNotIn("db_manager.get_all_cookies()", ITEM_SEARCH)

    def test_database_init_preserves_original_connection_error(self):
        from db_manager import DBManager

        manager = DBManager.__new__(DBManager)
        manager.db_path = "/nonexistent/readonly/xianyu.db"
        manager.conn = None
        with mock.patch(
            "db_manager.sqlite3.connect",
            side_effect=sqlite3.OperationalError("unable to open database file"),
        ):
            with self.assertRaisesRegex(
                sqlite3.OperationalError, "unable to open database file"
            ):
                manager.init_db()

    def test_database_backup_is_consistent_and_restore_revokes_sessions(self):
        download_source = function_source("download_database_backup")
        restore_source = function_source("upload_database_backup")
        self.assertIn("db_manager.create_online_backup(snapshot_path)", download_source)
        self.assertIn("BackgroundTask(os.remove, snapshot_path)", download_source)
        self.assertNotIn("path=db_file_path", download_source)
        self.assertIn("db_manager.create_online_backup(backup_current_path)", restore_source)
        self.assertIn("db_manager.stage_online_restore(temp_file_path, restore_staging_path)", restore_source)
        self.assertIn("PRAGMA integrity_check", restore_source)
        self.assertIn("db_manager.delete_all_session_tokens()", restore_source)
        self.assertIn("SESSION_TOKENS.clear()", restore_source)
        self.assertIn("upload_chunk_size = 1024 * 1024", restore_source)
        self.assertIn("upload_size_limit = 100 * 1024 * 1024", restore_source)
        self.assertIn("await backup_file.read(upload_chunk_size)", restore_source)
        self.assertNotIn("await backup_file.read()", restore_source)
        self.assertIn("if upload_size > upload_size_limit", restore_source)
        self.assertIn("if upload_size == 0", restore_source)
        self.assertEqual(restore_source.count("tempfile.mkstemp("), 3)
        self.assertEqual(restore_source.count("dir=db_dir"), 3)
        self.assertIn("finally:\n        for path in (temp_file_path, restore_staging_path, rollback_staging_path)", restore_source)
        rollback_replace = restore_source.index("os.replace(rollback_staging_path, current_db_path)")
        rollback_init = restore_source.index("db_manager.__init__(current_db_path)", rollback_replace)
        self.assertGreater(rollback_init, rollback_replace)
        self.assertNotIn("detail=str(e)", download_source)
        self.assertNotIn("detail=str(e)", restore_source)
        self.assertIn("self.get_connection().backup(target)", DB_MANAGER)
        self.assertIn("source_conn.backup(target)", DB_MANAGER)
        self.assertIn("DELETE FROM session_tokens", DB_MANAGER)

    def test_dynamic_content_uses_dom_events_and_validated_urls(self):
        self.assertIn("body.textContent = String(message)", APP_JS)
        self.assertIn("escapeHtml(String(item.item_title", APP_JS)
        self.assertIn("escapeHtml(String(item.item_detail", APP_JS)
        self.assertIn("copy-cookie-btn').addEventListener('click'", APP_JS)
        self.assertIn("copyCookie(cookieId, cookieValue)", APP_JS)
        self.assertIn("getSafeVerificationUrl(data && data.verification_url)", APP_JS)
        self.assertIn("getSafeItemUrl(item.item_url || item.url)", APP_JS)
        self.assertNotRegex(APP_JS, r'on(?:click|change|error)\s*=\s*["\']')
        self.assertNotRegex(APP_JS, r'(?:src|href)\s*=\s*["\'][^"\']*\$\{')

    def test_delivery_statuses_are_visible_and_safely_rendered_in_order_ui(self):
        status_classes = {
            "reserved": "bg-info text-white",
            "committed": "bg-success text-white",
            "rolled_back": "bg-secondary text-white",
            "dispatching": "bg-warning text-dark",
            "confirmed": "bg-success text-white",
            "ambiguous": "bg-danger text-white",
            "manual_required": "bg-danger text-white",
            "delivery_inventory_commit_pending": "bg-warning text-dark",
            "delivery_dispatch_ambiguous": "bg-danger text-white",
            "delivery_manual_required": "bg-danger text-white",
            "delivery_failed": "bg-danger text-white",
        }
        status_texts = {
            "reserved": "库存已预留",
            "committed": "库存已提交",
            "rolled_back": "库存预留已回滚",
            "dispatching": "正在发送",
            "confirmed": "发送已确认",
            "ambiguous": "发送结果不明确",
            "manual_required": "需人工处理",
            "delivery_inventory_commit_pending": "已发送，库存待提交",
            "delivery_dispatch_ambiguous": "发送结果不明确，需人工处理",
            "delivery_manual_required": "不支持原子自动发货，需人工处理",
            "delivery_failed": "发货失败",
        }
        for status, css_class in status_classes.items():
            self.assertIn(f"'{status}': '{css_class}'", APP_JS, status)
        for status, text in status_texts.items():
            self.assertIn(f"'{status}': '{text}'", APP_JS, status)

        self.assertIn("${escapeHtml(statusText)}", APP_JS)
        self.assertIn("${escapeHtml(getOrderStatusText(order.order_status))}", APP_JS)

    def test_exception_details_do_not_reach_client_response_sinks(self):
        tree = ast.parse(SERVER)
        violations = []

        def contains_exception_value(node, exception_names):
            return any(
                isinstance(child, ast.Name) and child.id in exception_names
                for child in ast.walk(node)
            )

        def response_values(node):
            if isinstance(node, ast.Return) and node.value:
                return [node.value]
            if isinstance(node, ast.Call):
                call_name = node.func.id if isinstance(node.func, ast.Name) else None
                response_keywords = {"detail"} if call_name == "HTTPException" else {
                    "content",
                    "message",
                    "error",
                }
                return [
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg in response_keywords
                ]
            if isinstance(node, ast.Dict):
                return [
                    value
                    for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant)
                    and key.value in {"detail", "message", "error"}
                ]
            return []

        for handler in (
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
        ):
            exception_names = {handler.name} if handler.name else set()
            changed = True
            while changed:
                changed = False
                for node in ast.walk(handler):
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        value = node.value
                        if value and contains_exception_value(value, exception_names):
                            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                            aliases = {
                                target.id for target in targets if isinstance(target, ast.Name)
                            }
                            if not aliases.issubset(exception_names):
                                exception_names.update(aliases)
                                changed = True
            for node in ast.walk(handler):
                for value in response_values(node):
                    if contains_exception_value(value, exception_names):
                        violations.append((node.lineno, ast.unparse(value)))
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value in {"message", "error"}
                            and contains_exception_value(node.value, exception_names)
                        ):
                            violations.append((node.lineno, ast.unparse(node.value)))

        self.assertEqual([], violations)

    def test_business_http_exceptions_are_not_swallowed(self):
        tree = ast.parse(SERVER)
        protected_calls = {
            "require_admin",
            "require_user_channel",
            "require_user_cookie",
            "_consume_item_search_quota",
            "_normalize_search_keyword",
        }
        violations = []

        for try_node in (
            node for node in ast.walk(tree) if isinstance(node, ast.Try)
        ):
            calls = {
                node.func.id
                for statement in try_node.body
                for node in ast.walk(statement)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in protected_calls
            }
            if not calls:
                continue

            catches_exception = any(
                handler.type is None
                or isinstance(handler.type, ast.Name)
                and handler.type.id == "Exception"
                for handler in try_node.handlers
            )
            preserves_http_exception = any(
                isinstance(handler.type, ast.Name)
                and handler.type.id == "HTTPException"
                and any(
                    isinstance(node, ast.Raise) and node.exc is None
                    for node in ast.walk(handler)
                )
                for handler in try_node.handlers
            )
            if catches_exception and not preserves_http_exception:
                violations.append((try_node.lineno, sorted(calls)))

        self.assertEqual([], violations)

    def test_security_boundary_changes_revoke_sessions_and_runtime_instances(self):
        self.assertIn("def _revoke_user_sessions(user_id: int)", SERVER)
        self.assertIn("db_manager.delete_session_tokens_by_user(user_id)", SERVER)
        self.assertIn("user = db_manager.get_user_by_id(token_data['user_id'])", SERVER)
        self.assertIn("if not user or not user.get('is_active')", SERVER)
        self.assertIn("if not _revoke_user_sessions(admin_user['user_id'])", SERVER)
        self.assertIn("if not _revoke_user_sessions(user_id)", SERVER)
        self.assertIn("await _shutdown_all_xianyu_instances()", SERVER)
        xianyu_source = (ROOT / "XianyuAutoAsync.py").read_text(encoding="utf-8")
        self.assertIn("async def shutdown(self)", xianyu_source)
        self.assertIn("await asyncio.wait_for(websocket.close(), timeout=5)", xianyu_source)
        self.assertIn("self._unregister_instance()", xianyu_source)


if __name__ == "__main__":
    unittest.main()
