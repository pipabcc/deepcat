"""数据清理磁盘阶段拆分与共享 PromptStore 的回归。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_clear_data_disk_heavy_clears_clipboard_images(tmp_path):
    from deepcat.core import data_manager as dm

    img_dir = tmp_path / "data" / "clipboard_images"
    img_dir.mkdir(parents=True)
    (img_dir / "a.png").write_bytes(b"x" * 32)

    ok, msg = dm.clear_data_disk_heavy("clipboard", tmp_path)

    assert ok
    assert img_dir.exists()
    assert list(img_dir.iterdir()) == []


def test_clear_data_disk_heavy_clears_logs(tmp_path):
    from deepcat.core import data_manager as dm

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("log", encoding="utf-8")

    ok, msg = dm.clear_data_disk_heavy("logs", tmp_path)

    assert ok
    assert list(logs.iterdir()) == []
    assert "1" in msg


def test_clear_data_disk_heavy_rejects_unknown_type(tmp_path):
    from deepcat.core import data_manager as dm

    ok, msg = dm.clear_data_disk_heavy("settings", tmp_path)
    assert not ok


def test_clear_data_class_clipboard_can_skip_disk_phase(tmp_path, monkeypatch):
    from deepcat.core import data_manager as dm

    monkeypatch.setattr(dm, "get_app_dir", lambda: tmp_path)

    img_dir = tmp_path / "data" / "clipboard_images"
    img_dir.mkdir(parents=True)
    (img_dir / "a.png").write_bytes(b"x" * 32)
    db_path = tmp_path / "data" / "clipboard_history.db"
    db_path.write_bytes(b"sqlite")

    class _FakeWindow:
        _clipboard_history_page = None

    ok, msg = dm.clear_data_class("clipboard", _FakeWindow(), clear_disk_heavy=False)

    assert ok
    assert not db_path.exists()  # 主线程数据库部分完成
    assert (img_dir / "a.png").exists()  # 磁盘阶段被跳过，图片仍在

    ok2, msg2 = dm.clear_data_disk_heavy("clipboard", tmp_path)
    assert ok2
    assert list(img_dir.iterdir()) == []


def test_load_settings_reuses_shared_prompt_store(monkeypatch, tmp_path):
    import deepcat.prompt_store as prompt_store_module
    import deepcat.settings_store as ss

    monkeypatch.setattr(ss, "get_app_dir", lambda: tmp_path)

    def _fake_prompt_db_path():
        d = tmp_path / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d / "prompt.db"

    monkeypatch.setattr(prompt_store_module, "_get_db_path", _fake_prompt_db_path)
    ss._reset_shared_prompt_store()
    try:
        ss.save_settings(ss._defaults())  # 先落盘 settings.json，避免 load 走无文件早退分支
        first = ss.load_settings()
        store_after_first = ss._shared_prompt_store
        assert store_after_first is not None

        second = ss.load_settings()
        assert ss._shared_prompt_store is store_after_first  # 连接被复用
        assert first is not second  # 设置对象各自构建

        # 外部独立连接写入提示词后，下一次 load_settings 立即可见（共享连接不做陈旧缓存）
        from deepcat.prompt_store import PromptStore

        PromptStore(db_path=_fake_prompt_db_path()).save_prompt("review_key", "review_value")
        third = ss.load_settings()
        translator_ui = third.ui.get("translator") if isinstance(third.ui, dict) else None
        assert isinstance(translator_ui, dict)
        assert translator_ui.get("review_key") == "review_value"
    finally:
        ss._reset_shared_prompt_store()


def test_load_settings_survives_prompt_store_failure(monkeypatch, tmp_path):
    import deepcat.settings_store as ss

    monkeypatch.setattr(ss, "get_app_dir", lambda: tmp_path)

    calls: list[str] = []

    class _BrokenStore:
        def __init__(self) -> None:
            raise RuntimeError("prompt db unavailable")

    import deepcat.prompt_store as prompt_store_module

    monkeypatch.setattr(prompt_store_module, "PromptStore", _BrokenStore)
    ss._reset_shared_prompt_store()
    try:
        ss.save_settings(ss._defaults())
        with pytest.MonkeyPatch.context() as patch_ctx:
            patch_ctx.setattr(ss.logger, "error", lambda *a, **k: calls.append(str(a)))
            loaded = ss.load_settings()

        assert isinstance(loaded, ss.AppSettings)
        assert len(calls) == 1
        assert "prompt db unavailable" in calls[0]
        # 失败后共享实例被重置，下次调用重建
        assert ss._shared_prompt_store is None
    finally:
        ss._reset_shared_prompt_store()
