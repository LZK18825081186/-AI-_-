import asyncio
import importlib
import os
import sqlite3
import tarfile
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


class Response:
    def __init__(self, payload, status=200, body=b"", headers=None):
        self._payload = payload
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._payload

    def iter_content(self, _chunk_size):
        yield self._body


class LinuxProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(
            {
                "FEISHU_AUTH_MODE": "bot",
                "FEISHU_APP_ID": "cli_test_app",
                "FEISHU_APP_SECRET": "test_secret_value_123456",
                "FEISHU_BASE_TOKEN": "baseToken123",
                "FEISHU_TABLE_ID": "tblTest123",
                "FEISHU_INVENTORY_CHAT_ID": "oc_test_chat",
                "FEISHU_INVENTORY_SOURCE_MESSAGE_ID": "om_test_message",
                "FEISHU_INVENTORY_BRIDGE_TOKEN": "b" * 64,
                "ADMIN_USERNAME": "test-admin",
                "ADMIN_PASSWORD": "Test-Only-Admin-Password-2026!",
            }
        )
        import feishu_inventory_bridge

        cls.bridge = importlib.reload(feishu_inventory_bridge)

    def fake_request(self, method, url, **_kwargs):
        if url.endswith("/auth/v3/tenant_access_token/internal"):
            return Response(
                {"code": 0, "msg": "ok", "tenant_access_token": "t-test", "expire": 7200}
            )
        if "/im/v1/messages/" in url:
            return Response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "message_id": "om_test_message",
                                "chat_id": "oc_test_chat",
                                "create_time": "123",
                                "body": {
                                    "content": '{"text":"https://example.feishu.cn/base/baseToken123"}'
                                },
                            }
                        ]
                    },
                }
            )
        if "/bitable/v1/apps/" in url:
            return Response(
                {
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "items": [
                            {
                                "record_id": "rec1",
                                "fields": {
                                    "角色名称": "银狼",
                                    "状态": "可租",
                                    "实物图": [
                                        {
                                            "file_token": "file1",
                                            "name": "silver.png",
                                            "type": "image/png",
                                        }
                                    ],
                                },
                            }
                        ],
                        "has_more": False,
                        "total": 1,
                    },
                }
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    def test_bot_inventory_and_media_whitelist(self):
        with patch.object(self.bridge.requests, "request", side_effect=self.fake_request):
            result = self.bridge.get_inventory()
            self.assertTrue(result["source_verified"])
            self.assertEqual(result["auth_mode"], "bot")
            self.assertEqual(result["record_count"], 1)
            self.assertEqual(result["records"][0]["_record_id"], "rec1")
            attachment = self.bridge._find_verified_attachment("file1")
            self.assertEqual(attachment["name"], "silver.png")
            with self.assertRaises(self.bridge.BridgeError):
                self.bridge._find_verified_attachment("not-in-base")

    def test_media_authorization_fails_closed(self):
        async def run_check():
            from XianyuAutoAsync import XianyuLive

            client = object.__new__(XianyuLive)
            with patch("XianyuAutoAsync.requests.get") as request_get:
                request_get.return_value.status_code = 503
                return await client._is_verified_media_authorized("file1")

        self.assertFalse(asyncio.run(run_check()))

    def test_online_backup_and_retention(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            db_path = root_path / "data.db"
            backup_dir = root_path / "backups"
            upload_dir = root_path / "uploads"
            upload_dir.mkdir()
            (upload_dir / "sample.jpg").write_bytes(b"image")
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("CREATE TABLE sample (value TEXT)")
                connection.execute("INSERT INTO sample VALUES ('ok')")
                connection.commit()
            os.environ.update(
                {
                    "DB_PATH": str(db_path),
                    "UPLOAD_DIR": str(upload_dir),
                    "BACKUP_DIR": str(backup_dir),
                    "MIN_FREE_DISK_BYTES": "0",
                }
            )
            import production_maintenance

            maintenance = importlib.reload(production_maintenance)
            destination = maintenance.backup_database()
            self.assertIsNotNone(destination)
            extracted_db = root_path / "extracted.db"
            with tarfile.open(destination, "r:gz") as archive:
                self.assertIn("data/xianyu_data.db", archive.getnames())
                self.assertIn("static/uploads/images/sample.jpg", archive.getnames())
                source = archive.extractfile("data/xianyu_data.db")
                self.assertIsNotNone(source)
                extracted_db.write_bytes(source.read())
            with closing(sqlite3.connect(extracted_db)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "ok")

    def test_backup_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ.update(
                {
                    "DB_PATH": str(Path(root) / "missing.db"),
                    "BACKUP_DIR": str(Path(root) / "backups"),
                    "MIN_FREE_DISK_BYTES": "0",
                }
            )
            import production_maintenance

            maintenance = importlib.reload(production_maintenance)
            self.assertIsNone(maintenance.backup_database())

    def test_inventory_loader_keeps_source_metadata_separate(self):
        import threading
        import ai_reply_engine

        engine = object.__new__(ai_reply_engine.AIReplyEngine)
        engine.inventory_lock = threading.RLock()
        engine.inventory_records = []
        engine.inventory_source = {}
        engine.inventory_loaded_at = 0
        engine._fetch_group_inventory = lambda: {
            "records": [
                {
                    "角色名称": "银狼",
                    "作品来源": "崩坏：星穹铁道",
                    "码数": "M",
                    "总库存": 2,
                    "已租出": 0,
                    "状态": "可租",
                }
            ],
            "source": {"message_id": "om_verified"},
        }
        engine._sync_media_from_verified_records = lambda: None
        result = engine._load_knowledge_base(force_refresh=True)
        self.assertIn("崩坏：星穹铁道-银狼", result)
        self.assertEqual(engine.inventory_source["message_id"], "om_verified")

    def test_inventory_fact_is_answered_before_ai_enabled_check(self):
        import threading
        import ai_reply_engine

        engine = object.__new__(ai_reply_engine.AIReplyEngine)
        engine.inventory_lock = threading.RLock()
        engine.inventory_records = []
        engine._load_knowledge_base = lambda force_refresh=False: setattr(
            engine,
            "inventory_records",
            [{"角色名称": "银狼", "码数": "M", "总库存": 1, "已租出": 0, "状态": "可租"}],
        )
        engine.save_conversation = lambda *_args, **_kwargs: None
        engine.is_ai_enabled = lambda *_args, **_kwargs: False
        reply = engine.generate_reply(
            "有货吗", {"title": "银狼cos服"}, "chat", "cookie", "user", "item"
        )
        self.assertIn("银狼", reply)
        self.assertIn("可租1套", reply)

    def test_inventory_reply_uses_item_title(self):
        import ai_reply_engine

        engine = object.__new__(ai_reply_engine.AIReplyEngine)
        import threading

        engine.inventory_lock = threading.RLock()
        engine.inventory_records = [
            {
                "角色名称": "银狼",
                "码数": "M",
                "总库存": 2,
                "已租出": 0,
                "状态": "可租",
                "租期价格": "350元/3天",
                "押金": 150,
                "配件清单": "服装本体",
            },
            {
                "角色名称": "卡芙卡",
                "码数": "L",
                "总库存": 3,
                "已租出": 0,
                "状态": "可租",
                "租期价格": "420元/3天",
                "押金": 200,
                "配件清单": "服装本体",
            },
        ]
        reply = engine._build_verified_inventory_reply("多少钱？", "崩铁银狼cos服")
        self.assertIn("银狼", reply)
        self.assertIn("350元/3天", reply)
        self.assertNotIn("卡芙卡", reply)

    def test_batch_delivery_reservation_commit_and_rollback(self):
        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            manager = DBManager(str(Path(root) / "delivery.db"))
            try:
                with manager.lock:
                    cursor = manager.conn.cursor()
                    cursor.execute(
                        "INSERT INTO cards (name, type, data_content, enabled, user_id) "
                        "VALUES (?, 'data', ?, 1, 1)",
                        ("资料包", "链接A 提取码A\n链接B 提取码B"),
                    )
                    card_id = cursor.lastrowid
                    manager.conn.commit()

                first = manager.reserve_batch_data(card_id, "order-1", 0, cookie_id="test")
                self.assertEqual(first, "链接A 提取码A")
                self.assertEqual(manager.reserve_batch_data(card_id, "order-1", 0, cookie_id="test"), first)
                remaining = manager.conn.execute(
                    "SELECT data_content FROM cards WHERE id = ?", (card_id,)
                ).fetchone()[0]
                self.assertEqual(remaining, "链接B 提取码B")

                self.assertTrue(manager.rollback_reserved_data("order-1", 0, cookie_id="test"))
                restored = manager.conn.execute(
                    "SELECT data_content FROM cards WHERE id = ?", (card_id,)
                ).fetchone()[0]
                self.assertEqual(restored, "链接A 提取码A\n链接B 提取码B")

                self.assertEqual(manager.reserve_batch_data(card_id, "order-1", 0, cookie_id="test"), first)
                second = manager.reserve_batch_data(card_id, "order-1", 1, cookie_id="test")
                self.assertEqual(second, "链接B 提取码B")
                self.assertTrue(manager.commit_reserved_units("order-1", [0, 1], cookie_id="test"))
                self.assertTrue(manager.commit_reserved_units("order-1", [0, 1], cookie_id="test"))
                self.assertFalse(manager.rollback_reserved_data("order-1", 0, cookie_id="test"))
                self.assertFalse(manager.rollback_reserved_data("order-1", 1, cookie_id="test"))

                self.assertTrue(
                    manager.set_delivery_dispatch_status("order-1", "dispatching", cookie_id="test")
                )
                self.assertEqual(
                    manager.get_delivery_dispatch("order-1", cookie_id="test")["status"], "dispatching"
                )
                self.assertTrue(
                    manager.set_delivery_dispatch_status("order-1", "confirmed", cookie_id="test")
                )
                dispatch = manager.get_delivery_dispatch("order-1", cookie_id="test")
                self.assertEqual(dispatch["status"], "confirmed")
                self.assertIsNone(dispatch["last_error"])
                self.assertTrue(
                    manager.set_delivery_dispatch_status(
                        "order-2",
                        "manual_required",
                        "多件非资料商品转人工",
                        cookie_id="test",
                    )
                )
                manual_dispatch = manager.get_delivery_dispatch("order-2", cookie_id="test")
                self.assertEqual(manual_dispatch["status"], "manual_required")
                self.assertEqual(manual_dispatch["last_error"], "多件非资料商品转人工")
            finally:
                manager.close()

    def test_v15_dispatch_schema_migrates_without_losing_existing_rows(self):
        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "dispatch-v15.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute('''
                    CREATE TABLE system_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        description TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                connection.execute(
                    "INSERT INTO system_settings (key, value) VALUES ('db_version', '1.5')"
                )
                connection.execute('''
                    CREATE TABLE delivery_dispatches (
                        cookie_id TEXT NOT NULL,
                        order_id TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'dispatching'
                            CHECK (status IN ('dispatching', 'confirmed', 'ambiguous')),
                        last_error TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (cookie_id, order_id)
                    )
                ''')
                connection.execute(
                    "INSERT INTO delivery_dispatches "
                    "(cookie_id, order_id, status, last_error, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "cookie-old",
                        "order-old",
                        "confirmed",
                        "legacy-error-marker",
                        "2026-01-02 03:04:05",
                        "2026-02-03 04:05:06",
                    ),
                )
                connection.commit()

            manager = DBManager(str(db_path))
            try:
                self.assertEqual(manager.get_system_setting("db_version"), "1.6")
                migrated_dispatch = manager.get_delivery_dispatch(
                    "order-old", cookie_id="cookie-old"
                )
                self.assertEqual(migrated_dispatch["status"], "confirmed")
                self.assertEqual(migrated_dispatch["last_error"], "legacy-error-marker")
                self.assertEqual(migrated_dispatch["created_at"], "2026-01-02 03:04:05")
                self.assertEqual(migrated_dispatch["updated_at"], "2026-02-03 04:05:06")
                self.assertTrue(
                    manager.set_delivery_dispatch_status(
                        "order-manual", "manual_required", cookie_id="cookie-old"
                    )
                )
                self.assertEqual(
                    manager.get_delivery_dispatch(
                        "order-manual", cookie_id="cookie-old"
                    )["status"],
                    "manual_required",
                )
            finally:
                manager.close()

            reopened = DBManager(str(db_path))
            try:
                self.assertEqual(reopened.get_system_setting("db_version"), "1.6")
                self.assertEqual(
                    reopened.get_delivery_dispatch(
                        "order-old", cookie_id="cookie-old"
                    )["last_error"],
                    "legacy-error-marker",
                )
                self.assertEqual(
                    reopened.get_delivery_dispatch(
                        "order-manual", cookie_id="cookie-old"
                    )["status"],
                    "manual_required",
                )
            finally:
                reopened.close()

    def test_public_template_admin_password_is_rejected(self):
        from db_manager import DBManager

        real_getenv = os.getenv

        def fake_getenv(key, default=None):
            overrides = {
                "DOCKER_ENV": "true",
                "ADMIN_USERNAME": "admin",
                "ADMIN_PASSWORD": "your_secure_password_here",
            }
            return overrides.get(key, real_getenv(key, default))

        with tempfile.TemporaryDirectory() as root, patch(
            "db_manager.os.getenv", side_effect=fake_getenv
        ):
            with self.assertRaisesRegex(RuntimeError, "must not use a default"):
                DBManager(str(Path(root) / "weak-admin.db"))
            self.assertTrue((Path(root) / "weak-admin.db").exists())

    def test_media_cache_uses_configured_writable_directory(self):
        from XianyuAutoAsync import XianyuLive

        real_getenv = os.getenv

        def fake_getenv(key, default=None):
            if key == "AI_CACHE_DIR":
                return cache_root
            return real_getenv(key, default)

        with tempfile.TemporaryDirectory() as cache_root, patch(
            "XianyuAutoAsync.os.getenv", side_effect=fake_getenv
        ):
            cache_dir = Path(XianyuLive._media_cache_dir())
            self.assertEqual(cache_dir, Path(cache_root) / ".xianyu_img_cache")
            probe = cache_dir / "write-probe"
            probe.write_bytes(b"ok")
            self.assertEqual(probe.read_bytes(), b"ok")

    def test_database_cleanup_failures_preserve_original_initialization_error(self):
        from db_manager import DBManager

        class FailingConnection:
            def rollback(self):
                raise RuntimeError("rollback failed")

            def close(self):
                raise RuntimeError("close failed")

        manager = object.__new__(DBManager)
        manager.db_path = ":memory:"
        manager.conn = FailingConnection()
        with patch("db_manager.sqlite3.connect", side_effect=RuntimeError("original init error")):
            with self.assertRaisesRegex(RuntimeError, "original init error"):
                manager.init_db()
        self.assertIsNone(manager.conn)

    def test_runtime_upload_directories_are_ignored_but_placeholders_are_tracked(self):
        gitignore = Path(__file__).with_name(".gitignore").read_text(encoding="utf-8")
        self.assertIn("static/uploads/images/*", gitignore)
        self.assertIn("!static/uploads/images/.gitkeep", gitignore)
        self.assertIn("uploads/images/*", gitignore)
        self.assertIn("!uploads/images/.gitkeep", gitignore)
        self.assertTrue(Path("static/uploads/images/.gitkeep").exists())
        self.assertTrue(Path("uploads/images/.gitkeep").exists())

    def test_linux_deploy_scripts_wait_for_health_and_arm_upload_rollback_early(self):
        setup_script = Path(__file__).with_name("setup.sh").read_text(encoding="utf-8")
        production_script = Path(__file__).with_name("docker-deploy.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("docker compose up -d --wait --wait-timeout 180", setup_script)
        move_live = production_script.index(
            'mv "${PROJECT_DIR}/uploads/images" "${snapshot}/live-images"'
        )
        arm_rollback = production_script.index("uploads_swapped=1", move_live)
        install_new = production_script.index(
            'mv "${new_uploads}" "${PROJECT_DIR}/uploads/images"', move_live
        )
        self.assertLess(move_live, arm_rollback)
        self.assertLess(arm_rollback, install_new)

    def test_windows_deploy_script_uses_valid_compose_names_and_delayed_errorlevel(self):
        script = Path(__file__).with_name("docker-deploy.bat").read_text(encoding="utf-8")
        self.assertIn("set COMPOSE_FILE=docker-compose.yml", script)
        self.assertIn("docker compose -f docker-compose-cn.yml build --no-cache", script)
        self.assertNotIn("docker compose -f docker compose-cn.yml", script)
        self.assertIn("if !errorlevel! neq 0 (", script)
        self.assertIn("docker compose up -d --wait --wait-timeout 180", script)
        self.assertIn("echo %SUCCESS_PREFIX% 镜像构建完成\nexit /b 0", script)

    def test_production_runtime_backup_uses_selected_environment_file(self):
        script = Path(__file__).with_name("docker-deploy.sh").read_text(encoding="utf-8")
        self.assertIn('cp -- "${ENV_FILE}" "${staging_dir}/${env_archive_name}"', script)
        self.assertNotIn('local files=(".env" "global_config.yml")', script)

    def test_auto_delivery_reservation_lookup_is_account_scoped(self):
        source = Path(__file__).with_name("XianyuAutoAsync.py").read_text(encoding="utf-8")
        expected = (
            "db_manager.get_delivery_reservation(\n"
            "                            order_id, unit_index, cookie_id=self.cookie_id\n"
            "                        )"
        )
        self.assertIn(expected, source)

    def test_session_revocation_is_scoped_to_target_user(self):
        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            manager = DBManager(str(Path(root) / "sessions.db"))
            try:
                self.assertTrue(manager.create_user("session-a", "a@example.com", "password-a"))
                self.assertTrue(manager.create_user("session-b", "b@example.com", "password-b"))
                user_a = manager.get_user_by_username("session-a")["id"]
                user_b = manager.get_user_by_username("session-b")["id"]
                self.assertTrue(manager.save_session_token("token-a", user_a, "session-a"))
                self.assertTrue(manager.save_session_token("token-b", user_b, "session-b"))
                self.assertTrue(manager.delete_session_tokens_by_user(user_a))
                self.assertIsNone(manager.get_session_token("token-a"))
                self.assertIsNotNone(manager.get_session_token("token-b"))
            finally:
                manager.close()

    def test_user_backup_import_allowlist_rejects_extra_tables_and_rewrites_owner(self):
        import copy

        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            manager = DBManager(str(Path(root) / "user-backup.db"))
            try:
                self.assertTrue(
                    manager.create_user("backup-source", "source@example.com", "Password123!")
                )
                self.assertTrue(
                    manager.create_user("backup-target", "target@example.com", "Password123!")
                )
                source_id = manager.get_user_by_username("backup-source")["id"]
                target_id = manager.get_user_by_username("backup-target")["id"]
                self.assertTrue(manager.save_cookie("source-cookie", "source-value", source_id))
                self.assertTrue(manager.save_cookie("target-old", "target-value", target_id))

                backup = manager.export_backup(target_id)
                allowed_user_tables = {
                    "cookies",
                    "keywords",
                    "cookie_status",
                    "default_replies",
                    "message_notifications",
                    "item_info",
                    "ai_reply_settings",
                    "ai_conversations",
                }
                self.assertLessEqual(set(backup["data"]), allowed_user_tables)

                cookie_table = backup["data"]["cookies"]
                id_index = cookie_table["columns"].index("id")
                owner_index = cookie_table["columns"].index("user_id")
                cookie_table["rows"][0][id_index] = "target-imported"
                cookie_table["rows"][0][owner_index] = source_id
                for table_name, table in backup["data"].items():
                    if table_name == "cookies" or "cookie_id" not in table["columns"]:
                        continue
                    cookie_index = table["columns"].index("cookie_id")
                    for row in table["rows"]:
                        row[cookie_index] = "target-imported"

                rejected_backup = copy.deepcopy(backup)
                rejected_backup["data"]["cards"] = {"columns": ["id"], "rows": []}
                self.assertFalse(manager.import_backup(rejected_backup, target_id))
                self.assertEqual(manager.get_cookie_details("target-old")["user_id"], target_id)

                self.assertTrue(manager.import_backup(backup, target_id))
                self.assertIsNone(manager.get_cookie_details("target-old"))
                self.assertEqual(
                    manager.get_cookie_details("target-imported")["user_id"], target_id
                )
                self.assertEqual(
                    manager.get_cookie_details("source-cookie")["user_id"], source_id
                )
            finally:
                manager.close()

    def test_cards_and_delivery_rules_are_isolated_across_tenants(self):
        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            manager = DBManager(str(Path(root) / "tenant-delivery.db"))
            try:
                self.assertTrue(manager.create_user("tenant-one", "one@example.com", "Password123!"))
                self.assertTrue(manager.create_user("tenant-two", "two@example.com", "Password123!"))
                tenant_one = manager.get_user_by_username("tenant-one")["id"]
                tenant_two = manager.get_user_by_username("tenant-two")["id"]
                card_one = manager.create_card(
                    "共享名称", "text", text_content="租户一内容", user_id=tenant_one
                )
                card_two = manager.create_card(
                    "共享名称", "text", text_content="租户二内容", user_id=tenant_two
                )
                rule_one = manager.create_delivery_rule(
                    "星穹铁道", card_one, user_id=tenant_one
                )
                rule_two = manager.create_delivery_rule(
                    "星穹铁道", card_two, user_id=tenant_two
                )
                cross_tenant_rule = manager.create_delivery_rule(
                    "越权规则", card_two, user_id=tenant_one
                )

                self.assertEqual(
                    {card["id"] for card in manager.get_all_cards(tenant_one)}, {card_one}
                )
                self.assertEqual(
                    {card["id"] for card in manager.get_all_cards(tenant_two)}, {card_two}
                )
                self.assertIsNone(manager.get_card_by_id(card_two, tenant_one))
                self.assertIsNone(manager.get_card_by_id(card_one, tenant_two))
                self.assertEqual(
                    {rule["id"] for rule in manager.get_all_delivery_rules(tenant_one)},
                    {rule_one, cross_tenant_rule},
                )
                self.assertEqual(
                    {rule["id"] for rule in manager.get_all_delivery_rules(tenant_two)},
                    {rule_two},
                )
                self.assertIsNone(manager.get_delivery_rule_by_id(rule_two, tenant_one))
                self.assertIsNone(manager.get_delivery_rule_by_id(rule_one, tenant_two))
                self.assertEqual(
                    [rule["id"] for rule in manager.get_delivery_rules_by_keyword(
                        "星穹铁道角色资料", tenant_one
                    )],
                    [rule_one],
                )
                self.assertEqual(
                    manager.get_delivery_rules_by_keyword("越权规则", tenant_one), []
                )
            finally:
                manager.close()

    def _run_delivery_dispatch_scenario(
        self,
        send_error=None,
        commit_result=True,
        card_type="data",
        quantity=1,
        existing_dispatch=None,
        delivery_content=None,
        existing_order_status=None,
    ):
        from collections import defaultdict

        import db_manager as db_module
        from XianyuAutoAsync import XianyuLive

        events = []
        written_orders = []

        class FakeDB:
            def get_item_info(self, _cookie_id, _item_id):
                return {"item_id": "item-1"}

            def get_order_by_id(self, _order_id, cookie_id=""):
                if existing_order_status:
                    return {"order_status": existing_order_status}
                return None

            def get_delivery_dispatch(self, _order_id, cookie_id=""):
                return existing_dispatch

            def get_item_multi_quantity_delivery_status(self, _cookie_id, _item_id):
                return quantity > 1

            def set_delivery_dispatch_status(self, _order_id, status, error=None, cookie_id=""):
                events.append(f"dispatch:{status}")
                return True

            def commit_reserved_units(self, _order_id, _indexes, cookie_id=""):
                events.append("inventory:commit")
                return commit_result

            def get_delivery_reservations(self, _order_id, cookie_id=""):
                return [{"unit_index": 0, "status": "reserved"}]

            def rollback_reserved_data(self, _order_id, _unit_index, cookie_id=""):
                events.append("inventory:rollback")
                return True

            def increment_delivery_times(self, _rule_id):
                events.append("delivery-count:increment")
                return True

            def insert_or_update_order(self, **order):
                written_orders.append(order)
                events.append(f"order:{order.get('order_status')}")
                return True

        async def run_scenario():
            client = object.__new__(XianyuLive)
            client.cookie_id = "cookie-1"
            client._extract_order_id = lambda _message: "order-1"
            client.is_lock_held = lambda _key: False
            client.can_auto_delivery = lambda _order_id: True
            client._order_locks = defaultdict(asyncio.Lock)
            client._lock_usage_times = {}
            client._lock_hold_info = {}
            client.confirmed_orders = {}
            client.order_confirm_cooldown = 600
            client.last_delivery_time = {}
            client._safe_str = lambda error: str(error)
            client.is_auto_confirm_enabled = lambda: True
            client.mark_delivery_sent = lambda order_id: events.append(f"sent:{order_id}")

            async def fetch_order_detail(*_args, **_kwargs):
                return {"quantity": quantity}

            async def prepare_delivery(*_args, **kwargs):
                unit_index = kwargs.get("unit_index", 0)
                events.append("inventory:reserved")
                return {
                    "content": delivery_content or (
                        "__IMAGE_SEND__7|https://img.alicdn.com/test.jpg"
                        if card_type == "image"
                        else "下载地址和提取码"
                    ),
                    "rule_id": 7,
                    "card_type": card_type,
                    "unit_index": unit_index,
                    "reservation_status": "reserved" if card_type == "data" else None,
                }

            async def send_message(*args, **kwargs):
                content = args[3]
                if len(content) > 100 and not kwargs.get("allow_truncate", True):
                    raise ValueError(
                        f"交付消息长度 {len(content)} 超过闲鱼单条消息上限 100"
                    )
                events.append("message:send")
                if send_error:
                    raise send_error
                return {"code": 200}

            async def send_image(*_args, **_kwargs):
                events.append("image:send")
                if send_error:
                    raise send_error
                return {"code": 200}

            async def confirm_platform(*_args, **_kwargs):
                events.append("platform:confirm")
                return {"success": True}

            async def notify(*_args, **_kwargs):
                events.append("notification")

            async def release_lock(*_args, **_kwargs):
                return None

            client.fetch_order_detail_info = fetch_order_detail
            client._auto_delivery = prepare_delivery
            client.send_msg = send_message
            client.send_image_msg = send_image
            client.auto_confirm = confirm_platform
            client.send_delivery_failure_notification = notify
            client._delayed_lock_release = release_lock

            with patch.object(db_module, "db_manager", FakeDB()):
                await client._handle_auto_delivery(
                    object(), {}, "买家", "buyer-1", "item-1", "chat-1", "now"
                )
                await asyncio.sleep(0)

        asyncio.run(run_scenario())
        return events, written_orders

    def test_manual_required_dispatch_keeps_manual_status_after_replay(self):
        events, written_orders = self._run_delivery_dispatch_scenario(
            existing_dispatch={"status": "manual_required"}
        )

        self.assertNotIn("inventory:reserved", events)
        self.assertNotIn("message:send", events)
        self.assertNotIn("platform:confirm", events)
        self.assertEqual(written_orders[-1]["order_status"], "delivery_manual_required")

    def test_inventory_commit_pending_replay_retries_commit_without_resending(self):
        events, written_orders = self._run_delivery_dispatch_scenario(
            existing_dispatch={"status": "confirmed"},
            existing_order_status="delivery_inventory_commit_pending",
        )

        self.assertIn("inventory:commit", events)
        self.assertNotIn("message:send", events)
        self.assertNotIn("platform:confirm", events)
        self.assertEqual(written_orders[-1]["order_status"], "delivered_pending_confirmation")

    def test_unconfirmed_dispatch_is_ambiguous_and_inventory_stays_reserved(self):
        events, written_orders = self._run_delivery_dispatch_scenario(
            send_error=TimeoutError("ack timeout")
        )

        self.assertIn("dispatch:ambiguous", events)
        self.assertNotIn("inventory:commit", events)
        self.assertNotIn("platform:confirm", events)
        self.assertEqual(written_orders[-1]["order_status"], "delivery_dispatch_ambiguous")

    def test_oversized_delivery_fails_before_send_and_rolls_back_inventory(self):
        events, written_orders = self._run_delivery_dispatch_scenario(
            delivery_content="x" * 101
        )

        self.assertNotIn("message:send", events)
        self.assertIn("inventory:rollback", events)
        self.assertIn("dispatch:manual_required", events)
        self.assertNotIn("platform:confirm", events)
        self.assertEqual(written_orders[-1]["order_status"], "delivery_manual_required")

    def test_confirmed_send_with_failed_inventory_commit_is_pending(self):
        events, written_orders = self._run_delivery_dispatch_scenario(commit_result=False)

        self.assertLess(events.index("message:send"), events.index("dispatch:confirmed"))
        self.assertLess(events.index("dispatch:confirmed"), events.index("inventory:commit"))
        self.assertNotIn("platform:confirm", events)
        self.assertEqual(
            written_orders[-1]["order_status"], "delivery_inventory_commit_pending"
        )

    def test_platform_confirmation_follows_send_ack_and_inventory_commit(self):
        events, written_orders = self._run_delivery_dispatch_scenario()

        self.assertLess(events.index("message:send"), events.index("dispatch:confirmed"))
        self.assertLess(events.index("dispatch:confirmed"), events.index("inventory:commit"))
        self.assertLess(events.index("inventory:commit"), events.index("platform:confirm"))
        self.assertEqual(written_orders[-1]["order_status"], "delivered")

    def test_multi_image_delivery_is_fail_closed_without_partial_send(self):
        events, written_orders = self._run_delivery_dispatch_scenario(
            card_type="image", quantity=2
        )

        self.assertNotIn("image:send", events)
        self.assertNotIn("platform:confirm", events)
        self.assertIn("dispatch:manual_required", events)
        self.assertEqual(written_orders[-1]["order_status"], "delivery_manual_required")

    def test_single_image_ack_is_persisted_before_platform_confirmation(self):
        events, written_orders = self._run_delivery_dispatch_scenario(card_type="image")

        self.assertLess(events.index("image:send"), events.index("dispatch:confirmed"))
        self.assertLess(events.index("dispatch:confirmed"), events.index("platform:confirm"))
        self.assertEqual(written_orders[-1]["order_status"], "delivered")

    def test_single_image_send_error_is_ambiguous_and_not_retried(self):
        events, written_orders = self._run_delivery_dispatch_scenario(
            send_error=TimeoutError("image ack timeout"), card_type="image"
        )

        self.assertIn("dispatch:ambiguous", events)
        self.assertNotIn("platform:confirm", events)
        self.assertEqual(written_orders[-1]["order_status"], "delivery_dispatch_ambiguous")

    def test_delete_card_keeps_other_account_dispatch_with_same_order_id(self):
        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            manager = DBManager(str(Path(root) / "delete-card-scope.db"))
            try:
                with manager.lock:
                    cursor = manager.conn.cursor()
                    cursor.execute(
                        "INSERT INTO cards (name, type, data_content, enabled, user_id) "
                        "VALUES (?, 'data', ?, 1, 1)",
                        ("账号一资料", "内容一"),
                    )
                    card_one = cursor.lastrowid
                    cursor.execute(
                        "INSERT INTO cards (name, type, data_content, enabled, user_id) "
                        "VALUES (?, 'data', ?, 1, 1)",
                        ("账号二资料", "内容二"),
                    )
                    card_two = cursor.lastrowid
                    manager.conn.commit()

                manager.reserve_batch_data(card_one, "shared-order", 0, cookie_id="cookie-one")
                manager.reserve_batch_data(card_two, "shared-order", 0, cookie_id="cookie-two")
                manager.set_delivery_dispatch_status(
                    "shared-order", "confirmed", cookie_id="cookie-one"
                )
                manager.set_delivery_dispatch_status(
                    "shared-order", "confirmed", cookie_id="cookie-two"
                )

                self.assertTrue(manager.delete_card(card_one))
                self.assertIsNone(
                    manager.get_delivery_dispatch("shared-order", cookie_id="cookie-one")
                )
                self.assertEqual(
                    manager.get_delivery_dispatch("shared-order", cookie_id="cookie-two")["status"],
                    "confirmed",
                )
            finally:
                manager.close()

    def test_delete_user_keeps_other_account_dispatch_with_same_order_id(self):
        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            manager = DBManager(str(Path(root) / "delete-user-scope.db"))
            try:
                manager.create_user("delete-scope-one", "scope-one@example.com", "Password123!")
                manager.create_user("delete-scope-two", "scope-two@example.com", "Password123!")
                user_one = manager.get_user_by_username("delete-scope-one")["id"]
                user_two = manager.get_user_by_username("delete-scope-two")["id"]
                card_one = manager.create_card(
                    "待删账号资料", "data", data_content="内容一", user_id=user_one
                )
                card_two = manager.create_card(
                    "保留账号资料", "data", data_content="内容二", user_id=user_two
                )
                manager.reserve_batch_data(
                    card_one, "shared-user-order", 0, cookie_id="cookie-user-one"
                )
                manager.reserve_batch_data(
                    card_two, "shared-user-order", 0, cookie_id="cookie-user-two"
                )
                manager.set_delivery_dispatch_status(
                    "shared-user-order", "confirmed", cookie_id="cookie-user-one"
                )
                manager.set_delivery_dispatch_status(
                    "shared-user-order", "confirmed", cookie_id="cookie-user-two"
                )

                self.assertTrue(manager.delete_user_and_data(user_one))
                self.assertIsNone(
                    manager.get_delivery_dispatch(
                        "shared-user-order", cookie_id="cookie-user-one"
                    )
                )
                self.assertEqual(
                    manager.get_delivery_dispatch(
                        "shared-user-order", cookie_id="cookie-user-two"
                    )["status"],
                    "confirmed",
                )
            finally:
                manager.close()

    def test_delete_user_cleans_delivery_reservations_before_cards(self):
        from db_manager import DBManager

        with tempfile.TemporaryDirectory() as root:
            manager = DBManager(str(Path(root) / "delete-user.db"))
            try:
                self.assertTrue(manager.create_user("delete-me", "delete@example.com", "Password123!"))
                user_id = manager.get_user_by_username("delete-me")["id"]
                with manager.lock:
                    cursor = manager.conn.cursor()
                    cursor.execute(
                        "INSERT INTO cookies (id, value, user_id) VALUES (?, ?, ?)",
                        ("delete-cookie", "cookie-value", user_id),
                    )
                    cursor.execute(
                        "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
                        ("delete-cookie", "测试", "回复"),
                    )
                    cursor.execute(
                        "INSERT INTO cards (name, type, data_content, enabled, user_id) "
                        "VALUES (?, 'data', ?, 1, ?)",
                        ("待删除资料", "链接C 提取码C", user_id),
                    )
                    card_id = cursor.lastrowid
                    manager.conn.commit()
                self.assertEqual(
                    manager.reserve_batch_data(card_id, "delete-order", 0, cookie_id="test"),
                    "链接C 提取码C",
                )

                self.assertTrue(manager.delete_user_and_data(user_id))
                self.assertIsNone(manager.get_user_by_id(user_id))
                self.assertEqual(
                    manager.conn.execute(
                        "SELECT COUNT(*) FROM delivery_reservations WHERE card_id = ?",
                        (card_id,),
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    manager.conn.execute(
                        "SELECT COUNT(*) FROM keywords WHERE cookie_id = 'delete-cookie'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                manager.close()

    def test_internal_reply_api_requires_explicit_high_entropy_key(self):
        import reply_server
        from fastapi import HTTPException

        request = reply_server.RequestModel(
            cookie_id="test-cookie",
            msg_time="2026-07-31 08:00:00",
            user_url="https://example.invalid/user",
            send_user_id="buyer",
            send_user_name="买家",
            item_id="item-1",
            send_message="你好",
            chat_id="chat-1",
        )
        key = "a" * 64

        async def run_check():
            with patch.object(reply_server, "EXTERNAL_MESSAGE_API_KEY", key), patch.object(
                reply_server, "match_reply", return_value="已收到"
            ):
                with self.assertRaises(HTTPException) as rejected:
                    await reply_server.xianyu_reply(request, x_api_key="")
                accepted = await reply_server.xianyu_reply(request, x_api_key=key)
                return rejected.exception.status_code, accepted

        status_code, accepted = asyncio.run(run_check())
        self.assertEqual(status_code, 401)
        self.assertEqual(accepted["data"]["send_msg"], "已收到")

    def test_auto_delivery_requires_trusted_platform_state(self):
        from XianyuAutoAsync import XianyuLive

        client = object.__new__(XianyuLive)
        reminder = "[我已付款，等待你发货]"
        forged_buyer_message = {
            "1": {"10": {"reminderContent": reminder, "senderUserId": "buyer"}}
        }
        trusted_platform_message = {
            "1": {"10": {"reminderContent": reminder, "senderUserId": "buyer"}},
            "3": {"redReminder": "等待卖家发货"},
        }

        self.assertTrue(client._is_auto_delivery_trigger(reminder))
        self.assertFalse(
            client._is_trusted_auto_delivery_event(forged_buyer_message, reminder)
        )
        self.assertTrue(
            client._is_trusted_auto_delivery_event(trusted_platform_message, reminder)
        )

    def test_websocket_send_requires_matching_server_ack(self):
        async def run_check():
            import collections
            import json
            from XianyuAutoAsync import XianyuLive

            client = object.__new__(XianyuLive)
            client.cookie_id = "test"
            client.myid = "seller"
            client._pending_ws_requests = {}
            client._pending_ws_lock = asyncio.Lock()
            client._recent_bot_messages = collections.OrderedDict()
            client._recent_bot_message_ttl = 120

            class FakeWebSocket:
                async def send(self, raw):
                    payload = json.loads(raw)
                    await client._resolve_pending_ws_request(
                        {"code": 200, "headers": {"mid": payload["headers"]["mid"]}}
                    )

            response = await client.send_msg(
                FakeWebSocket(), "chat", "buyer", "下载链接和提取码"
            )
            return response, client._is_recent_bot_message("chat", "下载链接和提取码")

        response, remembered = asyncio.run(run_check())
        self.assertEqual(response["code"], 200)
        self.assertTrue(remembered)

    def test_sync_work_is_offloaded_from_event_loop(self):
        async def run_check():
            started = time.monotonic()
            sync_result, tick = await asyncio.gather(
                asyncio.to_thread(lambda: (time.sleep(0.08), "done")[1]),
                asyncio.sleep(0.01, result="tick"),
            )
            return sync_result, tick, time.monotonic() - started

        sync_result, tick, elapsed = asyncio.run(run_check())
        self.assertEqual((sync_result, tick), ("done", "tick"))
        self.assertLess(elapsed, 0.2)


if __name__ == "__main__":
    unittest.main()
