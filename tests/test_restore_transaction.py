"""恢复失败必须保留原数据，包括未 checkpoint 的 SQLite WAL。"""

from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pytest

from deepcat.core import data_manager as dm


@pytest.mark.parametrize("failed_name", ["settings.json", "data/todo.db", "model_catalog.json"])
def test_restore_rolls_back_every_file_and_sidecar(tmp_path, monkeypatch, failed_name):
    root = tmp_path / "app"
    (root / "data").mkdir(parents=True)
    previous = {
        "settings.json": b"old-settings",
        "data/todo.db": b"old-db",
        "data/todo.db-wal": b"old-wal",
        "data/todo.db-shm": b"old-shm",
        "model_catalog.json": b"old-models",
    }
    for name, content in previous.items():
        (root / name).write_bytes(content)
    archive = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("data/clipboard_images/new.png", b"new-image")
        for name in ("settings.json", "data/todo.db", "model_catalog.json"):
            stream.writestr(name, b"new-data")
    original = dm._replace_from_staging

    def replace(staging, app_dir, name):
        if name == failed_name:
            raise PermissionError("模拟目标文件占用")
        original(staging, app_dir, name)

    monkeypatch.setattr(dm, "get_app_dir", lambda: root)
    monkeypatch.setattr(dm, "_replace_from_staging", replace)
    ok, _ = dm.restore_data(archive)
    assert not ok
    assert {name: (root / name).read_bytes() for name in previous} == previous
    assert not (root / "data/clipboard_images/new.png").exists()


def test_corrupt_zip_does_not_remove_existing_wal_or_close_stores(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    wal = tmp_path / "data/todo.db-wal"
    wal.write_bytes(b"committed-data")
    archive = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as stream:
        stream.writestr("data/todo.db", b"original-archive-payload")
    archive.write_bytes(archive.read_bytes().replace(b"original-archive-payload", b"tampered-archive-payload"))
    monkeypatch.setattr(dm, "get_app_dir", lambda: tmp_path)
    monkeypatch.setattr(dm, "_close_restore_sources", lambda *args: pytest.fail("坏备份不能关闭现有资源"))
    ok, _ = dm.restore_data(archive)
    assert not ok
    assert wal.read_bytes() == b"committed-data"


def test_failed_restore_preserves_real_committed_wal(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data/todo.db"
    script = (
        "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
        "c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA wal_autocheckpoint=0'); "
        "c.execute('CREATE TABLE items(value TEXT)'); c.execute(\"INSERT INTO items VALUES ('committed')\"); "
        "c.commit(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", script, str(db_path)], check=True, timeout=10)
    assert Path(str(db_path) + "-wal").exists()
    archive = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("data/todo.db", b"replacement")
    monkeypatch.setattr(dm, "get_app_dir", lambda: tmp_path)

    def fail(*args):
        raise PermissionError("模拟替换失败")

    monkeypatch.setattr(dm, "_replace_from_staging", fail)
    assert not dm.restore_data(archive)[0]
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT value FROM items").fetchone()[0] == "committed"
    finally:
        connection.close()


def test_rollback_failure_retains_recovery_files(tmp_path, monkeypatch):
    from deepcat.core.restore_transaction import replace_restored_files

    (tmp_path / "settings.json").write_bytes(b"old")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "settings.json").write_bytes(b"new")
    original = Path.replace

    def replace(path, target):
        if "deepcat_restore_rollback_" in str(path):
            raise PermissionError("模拟回滚目标锁定")
        return original(path, target)

    def fail(*args):
        raise PermissionError("模拟安装失败")

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(RuntimeError, match="原文件保留"):
        replace_restored_files(staging, tmp_path, ["settings.json"], fail)
    backups = list(tmp_path.glob("deepcat_restore_rollback_*/settings.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"old"
