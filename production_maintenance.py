"""Small production maintenance worker for SQLite backups and disk alerts."""

import argparse
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

DB_PATH = Path(os.environ.get("DB_PATH", "/app/data/xianyu_data.db"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/app/static/uploads/images"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/backups"))
HEALTH_FILE = BACKUP_DIR / ".maintenance-health.json"
BACKUP_INTERVAL = max(3600, int(os.environ.get("BACKUP_INTERVAL_SECONDS", "86400")))
BACKUP_RETENTION_DAYS = max(1, int(os.environ.get("BACKUP_RETENTION_DAYS", "14")))
MIN_FREE_BYTES = max(0, int(os.environ.get("MIN_FREE_DISK_BYTES", str(4 * 1024**3))))
CHECK_INTERVAL = max(300, int(os.environ.get("DISK_CHECK_INTERVAL_SECONDS", "3600")))
BACKUP_RETRY_INTERVAL = max(30, int(os.environ.get("BACKUP_RETRY_INTERVAL_SECONDS", "60")))
HEALTH_GRACE = max(0, int(os.environ.get("BACKUP_HEALTH_GRACE_SECONDS", "900")))
FAILURE_THRESHOLD = max(1, int(os.environ.get("BACKUP_FAILURE_THRESHOLD", "3")))
MAX_RESTORE_BYTES = max(1, int(os.environ.get("MAX_RESTORE_BYTES", str(10 * 1024**3))))
RESTORE_REQUIRED_TABLES = tuple(
    item.strip()
    for item in os.environ.get(
        "RESTORE_REQUIRED_TABLES",
        "users,cookies,keywords,ai_reply_settings,orders,item_info",
    ).split(",")
    if item.strip()
)
BACKUP_PREFIX = "xianyu_data_"
BACKUP_SUFFIXES = (".db", ".tar.gz")
DB_ARCHIVE_PATH = "data/xianyu_data.db"
UPLOAD_ARCHIVE_ROOT = "static/uploads/images"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("production_maintenance")


def _read_health_state() -> dict:
    if not HEALTH_FILE.exists():
        return {}
    with HEALTH_FILE.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict):
        raise ValueError("maintenance health state must be a JSON object")
    return state


def _write_health_state(state: dict) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=BACKUP_DIR,
        prefix=".maintenance-health.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, HEALTH_FILE)
        try:
            directory_fd = os.open(BACKUP_DIR, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def initialize_health_state() -> None:
    try:
        state = _read_health_state()
    except (OSError, ValueError, json.JSONDecodeError):
        LOGGER.exception("discarding unreadable maintenance health state")
        state = {}
    state.setdefault("last_success_at", None)
    state.setdefault("last_attempt_at", None)
    state.setdefault("consecutive_failures", 0)
    state["process_started_at"] = time.time()
    _write_health_state(state)


def record_backup_result(success: bool) -> None:
    try:
        state = _read_health_state()
    except (OSError, ValueError, json.JSONDecodeError):
        LOGGER.exception("resetting unreadable maintenance health state")
        state = {}
    now = time.time()
    state.setdefault("process_started_at", now)
    state.setdefault("last_success_at", None)
    state["last_attempt_at"] = now
    if success:
        state["last_success_at"] = now
        state["consecutive_failures"] = 0
    else:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    _write_health_state(state)


def check_backup_health() -> bool:
    try:
        state = _read_health_state()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("maintenance health state is unreadable: %s", exc)
        return False
    if not state:
        LOGGER.error("maintenance health state has not been initialized")
        return False

    now = time.time()
    try:
        failures = int(state.get("consecutive_failures", 0))
        started_at = float(state["process_started_at"])
        last_success_value = state.get("last_success_at")
        last_success = float(last_success_value) if last_success_value is not None else None
    except (KeyError, TypeError, ValueError) as exc:
        LOGGER.error("maintenance health state is invalid: %s", exc)
        return False

    if failures >= FAILURE_THRESHOLD:
        LOGGER.error(
            "maintenance backup is unhealthy: %s consecutive failures (threshold %s)",
            failures,
            FAILURE_THRESHOLD,
        )
        return False
    if last_success is None:
        age = max(0.0, now - started_at)
        if age > HEALTH_GRACE:
            LOGGER.error("maintenance has no successful backup after %.0f seconds", age)
            return False
        LOGGER.info("maintenance is within the initial backup grace period")
        return True

    age = max(0.0, now - last_success)
    maximum_age = BACKUP_INTERVAL + HEALTH_GRACE
    if age > maximum_age:
        LOGGER.error(
            "last successful backup is %.0f seconds old (maximum %s)",
            age,
            maximum_age,
        )
        return False
    LOGGER.info("maintenance backup health passed; last success was %.0f seconds ago", age)
    return True


def check_disk(required_bytes: int = 0) -> bool:
    target = BACKUP_DIR if BACKUP_DIR.exists() else BACKUP_DIR.parent
    usage = shutil.disk_usage(target)
    free_gib = usage.free / 1024**3
    total_gib = usage.total / 1024**3
    required_free = MIN_FREE_BYTES + max(0, required_bytes)
    if usage.free < required_free:
        LOGGER.error(
            "low disk space: free=%.2f GiB, total=%.2f GiB, required=%.2f GiB",
            free_gib,
            total_gib,
            required_free / 1024**3,
        )
        return False
    LOGGER.info("disk check passed: free=%.2f GiB, total=%.2f GiB", free_gib, total_gib)
    return True


def prune_backups() -> int:
    if not BACKUP_DIR.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=BACKUP_RETENTION_DAYS)
    removed = 0
    for path in BACKUP_DIR.glob(f"{BACKUP_PREFIX}*"):
        if not path.name.endswith(BACKUP_SUFFIXES) or not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            path.unlink()
            removed += 1
    if removed:
        LOGGER.info("removed %s expired project backups", removed)
    return removed


def directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _create_backup_database() -> Path | None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        LOGGER.warning("database does not exist yet: %s", DB_PATH)
        return None
    estimated_size = DB_PATH.stat().st_size * 2 + directory_size(UPLOAD_DIR)
    if not check_disk(estimated_size):
        LOGGER.error("backup skipped because free disk cannot preserve the safety threshold")
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = BACKUP_DIR / f"{BACKUP_PREFIX}{timestamp}.tar.gz"
    temporary_archive = BACKUP_DIR / f".{destination.name}.tmp"
    temporary_database = BACKUP_DIR / f".{BACKUP_PREFIX}{timestamp}.db.tmp"
    try:
        with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary_database)) as target:
                source.backup(target)
                result = target.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RuntimeError("backup integrity_check failed")
        with tarfile.open(temporary_archive, "w:gz") as archive:
            archive.add(temporary_database, arcname=DB_ARCHIVE_PATH, recursive=False)
            if UPLOAD_DIR.is_dir():
                archive.add(UPLOAD_DIR, arcname=UPLOAD_ARCHIVE_ROOT)
        with tarfile.open(temporary_archive, "r:gz") as archive:
            if DB_ARCHIVE_PATH not in archive.getnames():
                raise RuntimeError("backup archive validation failed")
        temporary_archive.replace(destination)
        os.chmod(destination, 0o600)
        LOGGER.info("online database and upload backup completed: %s", destination.name)
        prune_backups()
        return destination
    finally:
        if temporary_database.exists():
            temporary_database.unlink()
        if temporary_archive.exists():
            temporary_archive.unlink()


def backup_database() -> Path | None:
    try:
        destination = _create_backup_database()
    except Exception:
        record_backup_result(False)
        raise
    record_backup_result(destination is not None)
    return destination


def _validated_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names: set[str] = set()
    total_size = 0
    database_count = 0
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive member: {name!r}")
        if name in names:
            raise ValueError(f"duplicate archive member: {name}")
        names.add(name)
        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not allowed: {name}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported archive member type: {name}")
        if name == DB_ARCHIVE_PATH:
            if not member.isfile():
                raise ValueError("database archive member must be a regular file")
            database_count += 1
        elif name != UPLOAD_ARCHIVE_ROOT and not name.startswith(f"{UPLOAD_ARCHIVE_ROOT}/"):
            raise ValueError(f"unexpected archive member prefix: {name}")
        total_size += max(0, member.size)
        if total_size > MAX_RESTORE_BYTES:
            raise ValueError("archive expands beyond MAX_RESTORE_BYTES")
    if database_count != 1:
        raise ValueError("archive must contain exactly one data/xianyu_data.db")
    return members


def _safe_destination(root: Path, member_name: str) -> Path:
    destination = root.joinpath(*PurePosixPath(member_name).parts)
    resolved_root = root.resolve()
    resolved_destination = destination.resolve()
    if resolved_destination != resolved_root and resolved_root not in resolved_destination.parents:
        raise ValueError(f"archive member escapes extraction directory: {member_name}")
    return destination


def validate_restore_database(database: Path) -> None:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError("restore database integrity_check failed")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    missing = sorted(set(RESTORE_REQUIRED_TABLES) - tables)
    if missing:
        raise ValueError(f"restore database is missing required tables: {', '.join(missing)}")


def stage_restore_archive(archive_path: Path, destination: Path) -> None:
    if not archive_path.is_file() or archive_path.is_symlink():
        raise ValueError("restore archive must be a regular, non-symlink file")
    if destination.exists():
        raise ValueError("restore staging destination already exists")
    destination.mkdir(parents=True, mode=0o700)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _validated_members(archive)
            for member in members:
                target = _safe_destination(destination, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read archive member: {member.name}")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, 0o600)
        validate_restore_database(destination / DB_ARCHIVE_PATH)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    LOGGER.info("restore archive validated and staged at %s", destination)


def run_loop() -> None:
    initialize_health_state()
    next_backup = 0.0
    while True:
        now = time.monotonic()
        if now >= next_backup:
            try:
                backup_succeeded = backup_database() is not None
            except Exception:
                LOGGER.exception("scheduled backup failed")
                backup_succeeded = False
            next_backup = now + (
                BACKUP_INTERVAL if backup_succeeded else BACKUP_RETRY_INTERVAL
            )
        else:
            check_disk()
        time.sleep(min(CHECK_INTERVAL, max(1, int(next_backup - time.monotonic()))))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("backup", "check-disk", "health", "restore-stage", "run"),
    )
    parser.add_argument("archive", nargs="?")
    parser.add_argument("destination", nargs="?")
    args = parser.parse_args()
    if args.command == "backup":
        raise SystemExit(0 if backup_database() is not None else 2)
    if args.command == "check-disk":
        raise SystemExit(0 if check_disk() else 2)
    if args.command == "health":
        raise SystemExit(0 if check_backup_health() else 2)
    if args.command == "restore-stage":
        if not args.archive or not args.destination:
            parser.error("restore-stage requires ARCHIVE and DESTINATION")
        stage_restore_archive(Path(args.archive), Path(args.destination))
        return
    run_loop()


if __name__ == "__main__":
    main()
