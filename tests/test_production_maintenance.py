import io
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import production_maintenance as maintenance


REQUIRED_TABLES = (
    "users",
    "cookies",
    "keywords",
    "ai_reply_settings",
    "orders",
    "item_info",
)


def create_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        for table in REQUIRED_TABLES:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.execute("INSERT INTO users (id) VALUES (1)")
        connection.commit()


class ProductionMaintenanceTests(unittest.TestCase):
    def test_backup_and_restore_stage_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            database = root_path / "data" / "xianyu_data.db"
            uploads = root_path / "uploads"
            backups = root_path / "backups"
            database.parent.mkdir()
            uploads.mkdir()
            (uploads / "asset.txt").write_text("asset", encoding="utf-8")
            create_database(database)

            with mock.patch.multiple(
                maintenance,
                DB_PATH=database,
                UPLOAD_DIR=uploads,
                BACKUP_DIR=backups,
                HEALTH_FILE=backups / ".maintenance-health.json",
                MIN_FREE_BYTES=0,
            ):
                maintenance.initialize_health_state()
                archive = maintenance.backup_database()
                self.assertIsNotNone(archive)
                self.assertTrue(archive.is_file())

                staging = root_path / "restore-stage"
                maintenance.stage_restore_archive(archive, staging)
                restored_database = staging / maintenance.DB_ARCHIVE_PATH
                maintenance.validate_restore_database(restored_database)
                self.assertEqual(
                    (staging / maintenance.UPLOAD_ARCHIVE_ROOT / "asset.txt").read_text(
                        encoding="utf-8"
                    ),
                    "asset",
                )

    def test_backup_skips_when_disk_safety_threshold_would_be_breached(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            database = root_path / "xianyu.db"
            uploads = root_path / "uploads"
            backups = root_path / "backups"
            uploads.mkdir()
            create_database(database)

            with mock.patch.multiple(
                maintenance,
                DB_PATH=database,
                UPLOAD_DIR=uploads,
                BACKUP_DIR=backups,
                HEALTH_FILE=backups / ".maintenance-health.json",
            ), mock.patch.object(maintenance, "check_disk", return_value=False):
                maintenance.initialize_health_state()
                self.assertIsNone(maintenance.backup_database())
                state = maintenance._read_health_state()
                self.assertEqual(state["consecutive_failures"], 1)
                self.assertIsNone(state["last_success_at"])

    def test_run_loop_retries_failed_initial_backup_quickly(self):
        class StopLoop(Exception):
            pass

        with mock.patch.object(maintenance, "initialize_health_state"), mock.patch.object(
            maintenance, "backup_database", return_value=None
        ), mock.patch.object(
            maintenance.time, "monotonic", side_effect=[100.0, 100.0]
        ), mock.patch.object(
            maintenance.time, "sleep", side_effect=StopLoop
        ) as sleep_mock, mock.patch.multiple(
            maintenance,
            BACKUP_INTERVAL=86400,
            BACKUP_RETRY_INTERVAL=60,
            CHECK_INTERVAL=3600,
        ):
            with self.assertRaises(StopLoop):
                maintenance.run_loop()

        sleep_mock.assert_called_once_with(60)

    def test_health_fails_after_consecutive_backup_failures(self):
        with tempfile.TemporaryDirectory() as root:
            backups = Path(root) / "backups"
            with mock.patch.multiple(
                maintenance,
                BACKUP_DIR=backups,
                HEALTH_FILE=backups / ".maintenance-health.json",
                FAILURE_THRESHOLD=2,
            ):
                maintenance.initialize_health_state()
                maintenance.record_backup_result(False)
                self.assertTrue(maintenance.check_backup_health())
                maintenance.record_backup_result(False)
                self.assertFalse(maintenance.check_backup_health())

    def test_restore_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            archive_path = root_path / "malicious.tar.gz"
            payload = b"escape"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("../escape.txt")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))

            destination = root_path / "restore-stage"
            with self.assertRaisesRegex(ValueError, "unsafe archive member"):
                maintenance.stage_restore_archive(archive_path, destination)
            self.assertFalse((root_path / "escape.txt").exists())
            self.assertFalse(destination.exists())

    def test_restore_rejects_database_missing_required_tables(self):
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / "incomplete.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
                connection.commit()

            with self.assertRaisesRegex(ValueError, "missing required tables"):
                maintenance.validate_restore_database(database)


if __name__ == "__main__":
    unittest.main()
