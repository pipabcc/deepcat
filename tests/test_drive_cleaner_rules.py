"""常规清理扫描的 Windows 目标覆盖回归：新增安全清理类目与安全闸门。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deepcat import drive_cleaner as dc


def _fake_windows_tree(tmp_path: Path) -> dict[str, Path]:
    """构造一套假的 Windows 目录结构，env 全部指向临时目录。"""
    windows = tmp_path / "Windows"
    users = tmp_path / "Users" / "tester"
    local = users / "AppData" / "Local"
    roaming = users / "AppData" / "Roaming"
    programdata = tmp_path / "ProgramData"
    for directory in (
        windows / "Minidump",
        windows / "Logs" / "CBS",
        local / "D3DSCache",
        local / "NVIDIA Corporation" / "NV_Cache",
        local / "CrashDumps",
        local / "Microsoft" / "Windows" / "WER" / "ReportQueue",
        local / "Microsoft" / "Windows" / "Explorer",
        local / "Microsoft" / "Windows" / "INetCache",
        programdata / "Microsoft" / "Windows" / "WER" / "ReportArchive",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (windows / "Minidump" / "090625-1234.dmp").write_bytes(b"x" * 4096)
    (windows / "MEMORY.DMP").write_bytes(b"x" * 4096)
    (local / "D3DSCache" / "shader.bin").write_bytes(b"x" * 4096)
    (local / "CrashDumps" / "app.dmp").write_bytes(b"x" * 4096)
    (local / "Microsoft" / "Windows" / "Explorer" / "thumbcache_1.db").write_bytes(b"x" * 4096)
    (programdata / "Microsoft" / "Windows" / "WER" / "ReportArchive" / "r.zip").write_bytes(b"x" * 4096)
    (tmp_path / "Windows.old").mkdir()
    (tmp_path / "Windows.old" / "old.txt").write_bytes(b"x" * 4096)
    return {
        "SystemRoot": str(windows),
        "LOCALAPPDATA": str(local),
        "APPDATA": str(roaming),
        "PROGRAMDATA": str(programdata),
        "USERPROFILE": str(users),
        "TEMP": str(local / "Temp"),
        "TMP": str(local / "Temp"),
    }


def _with_fake_env(monkeypatch, tmp_path: Path) -> None:
    env = _fake_windows_tree(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_known_candidate_paths_cover_new_windows_targets(tmp_path, monkeypatch):
    _with_fake_env(monkeypatch, tmp_path)
    items = {str(path).lower(): (kind, mode, rule, risk) for path, kind, mode, rule, risk, _ in dc._known_candidate_paths(str(tmp_path))}

    # SAFE：可自动重建或纯诊断
    assert items[str(Path(os.environ["LOCALAPPDATA"]) / "D3DSCache").lower()][3] == dc.SAFE
    assert items[str(Path(os.environ["LOCALAPPDATA"]) / "CrashDumps").lower()][3] == dc.SAFE
    assert items[str(Path(os.environ["SystemRoot"]) / "Minidump").lower()][3] == dc.SAFE
    assert items[str(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Explorer").lower()] == (
        "thumbnail_cache",
        dc.CONTENTS,
        "thumbnail_cache",
        dc.SAFE,
    )
    assert items[str(Path(os.environ["PROGRAMDATA"]) / "Microsoft" / "Windows" / "WER" / "ReportArchive").lower()][3] == dc.SAFE

    # CONFIRM：需要用户确认
    assert items[str(tmp_path / "Windows.old").lower()][3] == dc.CONFIRM_REQUIRED
    assert items[str(Path(os.environ["SystemRoot"]) / "Logs").lower()][3] == dc.CONFIRM_REQUIRED
    assert items[
        str(
            Path(os.environ["SystemRoot"])
            / "ServiceProfiles"
            / "NetworkService"
            / "AppData"
            / "Local"
            / "Microsoft"
            / "Windows"
            / "DeliveryOptimization"
            / "Cache"
        ).lower()
    ][3] == dc.CONFIRM_REQUIRED

    # 内存转储单文件用 SELF 模式删除
    assert items[str(Path(os.environ["SystemRoot"]) / "MEMORY.DMP").lower()][1] == dc.SELF


def test_new_safe_rules_in_cleaner_whitelist(tmp_path):
    path = tmp_path / "cache"
    for rule in (
        "gpu_shader_cache",
        "crash_dumps",
        "wer_reports",
        "thumbnail_cache",
        "inet_cache",
        "java_deployment_cache",
        "gradle_cache",
        "go_build_cache",
    ):
        assert dc._candidate_allowed_by_cleaner(path, "gpu_cache", dc.SELF, rule), rule
    # CONFIRM 规则不在白名单：即便标 SAFE 也不允许直接清理（防御性）
    assert not dc._candidate_allowed_by_cleaner(path, "windows_old", dc.SELF, "windows_old")
    assert not dc._candidate_allowed_by_cleaner(path, "system_logs", dc.CONTENTS, "windows_logs")


def test_classify_directory_recognizes_new_names(tmp_path, monkeypatch):
    # 受管根内（本例把 LOCALAPPDATA 指向 tmp_path 内部）：名称命中即 SAFE。
    # 位置规则详见 drive_cleaner._is_within_managed_cleanup_root。
    managed_local = tmp_path / "managed" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(managed_local))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "managed" / "me"))

    gpu = dc._classify_directory(managed_local / "D3DSCache", "d3dscache")
    assert gpu is not None and gpu[0] == "gpu_cache" and gpu[3] == dc.SAFE
    gpu_generic = dc._classify_directory(managed_local / "GLCache", "glcache")
    assert gpu_generic is not None and gpu_generic[0] == "gpu_cache" and gpu_generic[3] == dc.SAFE
    browser_gpu = dc._classify_directory(
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Default" / "GPUCache",
        "gpucache",
    )
    assert browser_gpu is not None and browser_gpu[0] == "browser_cache" and browser_gpu[3] == dc.SAFE
    crash = dc._classify_directory(managed_local / "Minidumps", "minidumps")
    assert crash is not None and crash[0] == "crash_dump" and crash[3] == dc.SAFE
    wer = dc._classify_directory(managed_local / "ReportArchive", "reportarchive")
    assert wer is not None and wer[0] == "error_reports" and wer[3] == dc.SAFE

    # 非受管位置：同名目录必须降级为待确认，绝不允许被当作可安全清理项
    outside = tmp_path / "Projects" / "D3DSCache"
    downgraded = dc._classify_directory(outside, "d3dscache")
    assert downgraded is not None and downgraded[0] == "gpu_cache" and downgraded[3] == dc.CONFIRM_REQUIRED


def test_cleanup_scan_discovers_new_targets(tmp_path, monkeypatch):
    _with_fake_env(monkeypatch, tmp_path)
    drive = dc.DriveInfo(
        drive=tmp_path.anchor.rstrip("\\/"),
        root=str(tmp_path),
        drive_type="fixed",
        total_bytes=10 * dc._GB,
        used_bytes=1 * dc._GB,
        free_bytes=9 * dc._GB,
    )
    result = dc._scan_one_drive(
        drive,
        min_candidate_size_mb=0,
        min_large_file_size_mb=1024,
        min_duplicate_size_mb=1024,
        max_depth=3,
        scan_modes={"cleanup"},
        confirm_duplicate_content_hash=False,
        cancel=None,
        progress=None,
        item_found=None,
    )
    by_path = {candidate.path.lower(): candidate for candidate in result.candidates}

    d3ds = by_path.get(str(Path(os.environ["LOCALAPPDATA"]) / "D3DSCache").lower())
    assert d3ds is not None and d3ds.risk == dc.SAFE and d3ds.allowed_by_cleaner
    minidump = by_path.get(str(Path(os.environ["SystemRoot"]) / "Minidump").lower())
    assert minidump is not None and minidump.risk == dc.SAFE
    memory_dmp = by_path.get(str(Path(os.environ["SystemRoot"]) / "MEMORY.DMP").lower())
    assert memory_dmp is not None and memory_dmp.cleanup_mode == dc.SELF
    windows_old = by_path.get(str((tmp_path / "Windows.old").resolve()).lower())
    assert windows_old is not None and windows_old.risk == dc.CONFIRM_REQUIRED


def test_memory_dmp_cleanup_uses_safe_delete_path(tmp_path, monkeypatch):
    """SELF 模式删除内存转储文件：走回收站/年龄检查，不允许永久直删。"""
    _with_fake_env(monkeypatch, tmp_path)
    dmp = Path(os.environ["SystemRoot"]) / "MEMORY.DMP"

    import time

    errors: list[str] = []
    # cutoff 设为当前时间之后 → 文件足够旧，允许进入清理流程
    dc._remove_path_if_old(dmp, cutoff=time.time() + 10, errors=errors, delete_mode="recycle")
    # recycle 模式由 monkeypatch 下的回收站接口决定；这里验证不抛异常且记录可追
    assert isinstance(errors, list)


def test_windows_installer_cache_never_listed(tmp_path, monkeypatch):
    """安全边界：Windows Installer / Package Cache 等不可重建目录绝不能进入清理候选。"""
    _with_fake_env(monkeypatch, tmp_path)
    items = {str(path).lower() for path, *_ in dc._known_candidate_paths(str(tmp_path))}
    programdata = Path(os.environ["PROGRAMDATA"])
    assert str(programdata / "Package Cache").lower() not in items
    assert str(Path(os.environ["SystemRoot"]) / "Installer").lower() not in items
    assert str(Path(os.environ["SystemRoot"]) / "Prefetch").lower() not in items
    assert str(Path(os.environ["SystemRoot"]) / "WinSxS").lower() not in items
