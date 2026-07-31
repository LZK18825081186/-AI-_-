import base64
import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

import ai_reply_engine as engine_module
import feishu_inventory_bridge as bridge


class QwenVisionTests(unittest.TestCase):
    def test_prepare_qwen_image_limits_dimension_and_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "large.png"
            Image.new("RGB", (3200, 2400), "red").save(image_path)
            with mock.patch.object(engine_module, "QWEN_MAX_IMAGE_DIMENSION", 1024), mock.patch.object(
                engine_module, "QWEN_MAX_IMAGE_BYTES", 200_000
            ):
                mime, encoded = engine_module.AIReplyEngine._prepare_qwen_image(
                    str(image_path)
                )
            body = base64.b64decode(encoded)
            self.assertEqual(mime, "image/jpeg")
            self.assertLessEqual(len(body), 200_000)
            with Image.open(BytesIO(body)) as prepared:
                self.assertLessEqual(max(prepared.size), 1024)

    def test_qwen_retry_is_finite(self):
        attempts = []

        def operation():
            attempts.append(1)
            raise TimeoutError("timeout")

        with mock.patch.object(engine_module, "QWEN_MAX_RETRIES", 2), mock.patch.object(
            engine_module.time, "sleep"
        ):
            with self.assertRaises(TimeoutError):
                engine_module.AIReplyEngine._call_qwen_with_retry(operation, "测试")
        self.assertEqual(len(attempts), 3)


class ReviewCacheTests(unittest.TestCase):
    def test_verified_sync_atomically_removes_revoked_and_deleted_descriptions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = engine_module.AIReplyEngine.__new__(engine_module.AIReplyEngine)
            engine.cache_lock = threading.RLock()
            engine.item_images = {
                "撤审商品": [{"file_token": "old", "cdn_url": "cdn"}],
                "已删除商品": [{"file_token": "gone", "cdn_url": "cdn"}],
            }
            engine.image_descriptions = {
                "撤审商品": [{"description": "旧事实", "reviewed": True}],
                "已删除商品": [{"description": "旧事实", "reviewed": True}],
            }
            engine.image_cdn_cache_path = str(Path(temp_dir) / "media.json")
            engine.image_descriptions_path = str(Path(temp_dir) / "descriptions.json")
            engine.inventory_records = [
                {
                    "_record_id": "rec-revoked",
                    "角色名称": "撤审商品",
                    "实物图": [{"file_token": "new", "name": "new.jpg"}],
                    "图片描述": "未审核草稿",
                    "描述已审核": False,
                },
                {
                    "_record_id": "rec-reviewed",
                    "角色名称": "已审商品",
                    "实物图": [{"file_token": "ok", "name": "ok.jpg"}],
                    "图片描述": "人工确认事实",
                    "描述已审核": True,
                },
            ]

            engine._sync_media_from_verified_records()

            self.assertNotIn("撤审商品", engine.image_descriptions)
            self.assertNotIn("已删除商品", engine.image_descriptions)
            self.assertTrue(engine.image_descriptions["已审商品"][0]["reviewed"])
            self.assertNotIn("已删除商品", engine.item_images)
            on_disk = json.loads(Path(engine.image_descriptions_path).read_text("utf-8"))
            self.assertEqual(set(on_disk), {"已审商品"})
            self.assertFalse(list(Path(temp_dir).glob(".cache-*.tmp")))

    def test_partial_multi_record_review_does_not_become_customer_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = engine_module.AIReplyEngine.__new__(engine_module.AIReplyEngine)
            engine.cache_lock = threading.RLock()
            engine.item_images = {}
            engine.image_descriptions = {}
            engine.image_cdn_cache_path = str(Path(temp_dir) / "media.json")
            engine.image_descriptions_path = str(Path(temp_dir) / "descriptions.json")
            engine.inventory_records = [
                {
                    "_record_id": "rec1",
                    "角色名称": "多码商品",
                    "图片描述": "同一草稿",
                    "描述已审核": True,
                },
                {
                    "_record_id": "rec2",
                    "角色名称": "多码商品",
                    "图片描述": "同一草稿",
                    "描述已审核": False,
                },
            ]

            engine._sync_media_from_verified_records()

            self.assertNotIn("多码商品", engine.image_descriptions)


class FeishuReviewBridgeTests(unittest.TestCase):
    def test_bot_update_forces_review_false(self):
        captured = {}

        def request_json(method, path, **kwargs):
            captured.update(method=method, path=path, kwargs=kwargs)
            return {"code": 0, "data": {"record": {"record_id": "rec1"}}}

        with mock.patch.object(bridge, "_request_json", side_effect=request_json), mock.patch.object(
            bridge, "_bot_headers", return_value={"Authorization": "Bearer token"}
        ):
            bridge._update_description_bot("rec1", "待审草稿")

        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(
            captured["kwargs"]["json"]["fields"],
            {"图片描述": "待审草稿", "描述已审核": False},
        )

    def test_submit_rejects_unverified_record(self):
        inventory = {"records": [{"_record_id": "rec1"}]}
        with mock.patch.object(bridge, "get_inventory", return_value=inventory):
            with self.assertRaisesRegex(bridge.BridgeError, "not present"):
                bridge.submit_description(["rec-other"], "draft")

    def test_submit_updates_only_verified_records(self):
        inventory = {"records": [{"_record_id": "rec1"}, {"_record_id": "rec2"}]}
        with mock.patch.object(bridge, "get_inventory", return_value=inventory), mock.patch.object(
            bridge, "AUTH_MODE", "bot"
        ), mock.patch.object(bridge, "_update_description_bot") as update:
            result = bridge.submit_description(["rec1", "rec2"], "draft")
        self.assertEqual(result["updated"], 2)
        self.assertTrue(result["review_required"])
        self.assertEqual(update.call_count, 2)


if __name__ == "__main__":
    unittest.main()
