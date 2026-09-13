import os
from pathlib import Path
import time

from deepcat.drive_cleaner import (
    CONFIRM_REQUIRED,
    CONTENTS,
    SAFE,
    CleanupCandidate,
    SoftwareEntry,
    cleanup_items,
    scan_drives,
    uninstall_software,
)
from deepcat import drive_cleaner


def _write_bytes(path: Path, size: int, fill: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fill * size)
    # 保持文件属于“近期”，同时避开 Windows 文件时间戳略领先系统时钟的边界。
    timestamp = time.time() - 1.0
    os.utime(path, (timestamp, timestamp))


def test_scan_finds_cleanup_large_and_duplicate_files(tmp_path, monkeypatch):
    monkeypatch.setattr(drive_cleaner, "_is_under_git_repo", lambda path, max_depth=4: False)
    temp_dir = tmp_path / "temp"
    _write_bytes(temp_dir / "old.tmp", 1024 * 1024 + 10)
    _write_bytes(tmp_path / "large.bin", 2 * 1024 * 1024, b"a")
    _write_bytes(tmp_path / "copy1.bin", 1024 * 1024, b"b")
    _write_bytes(tmp_path / "copy2.bin", 1024 * 1024, b"b")

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=1,
        min_duplicate_size_mb=1,
        max_depth=3,
    )

    result = report.drives[0]
    # 位置规则（P0 修复）：tmp_path 位于工程盘 D:\ 下、不在受管清理根内，
    # 因此名为 temp 的目录只作为"待确认"候选项出现，不再自动判为 SAFE。
    # 受管根内仍判 SAFE，见 test_managed_temp_root_still_safe。
    assert any(candidate.kind == "temp" and candidate.risk == CONFIRM_REQUIRED for candidate in result.candidates)
    assert any(entry.path.endswith("large.bin") for entry in result.large_files)
    assert any(set(Path(p).name for p in group.paths) == {"copy1.bin", "copy2.bin"} for group in result.duplicate_groups)


def test_cleanup_dry_run_keeps_files(tmp_path):
    target = tmp_path / "temp"
    _write_bytes(target / "a.tmp", 128)
    candidate = CleanupCandidate(
        id="cand_1",
        path=str(target),
        size_bytes=128,
        kind="temp",
        risk=SAFE,
        cleanup_mode=CONTENTS,
        source_rule="named_temp_dir",
        last_write_time="",
        allowed_by_cleaner=True,
        reason="test",
    )

    report = cleanup_items([candidate], ["cand_1"], dry_run=True, confirmed_by_user=True, safe_age_hours=0)

    assert report.items[0].status == "DryRun"
    assert (target / "a.tmp").exists()


def test_cleanup_contents_removes_only_children(tmp_path):
    target = tmp_path / "temp"
    _write_bytes(target / "a.tmp", 128)
    candidate = CleanupCandidate(
        id="cand_1",
        path=str(target),
        size_bytes=128,
        kind="temp",
        risk=SAFE,
        cleanup_mode=CONTENTS,
        source_rule="named_temp_dir",
        last_write_time="",
        allowed_by_cleaner=True,
        reason="test",
    )

    report = cleanup_items([candidate], ["cand_1"], dry_run=False, confirmed_by_user=True, safe_age_hours=0)

    assert report.items[0].status == "Success"
    assert target.exists()
    assert not (target / "a.tmp").exists()


def test_cleanup_removes_recent_files_when_age_threshold_zero(tmp_path):
    target = tmp_path / "temp"
    _write_bytes(target / "recent.tmp", 128)
    candidate = CleanupCandidate(
        id="cand_1",
        path=str(target),
        size_bytes=128,
        kind="temp",
        risk=SAFE,
        cleanup_mode=CONTENTS,
        source_rule="named_temp_dir",
        last_write_time="",
        allowed_by_cleaner=True,
        reason="test",
    )

    report = cleanup_items([candidate], ["cand_1"], dry_run=False, confirmed_by_user=True, safe_age_hours=0)

    assert report.items[0].status == "Success"
    assert not (target / "recent.tmp").exists()


def test_cleanup_confirm_required_requires_explicit_allow(tmp_path, monkeypatch):
    monkeypatch.setattr(drive_cleaner, "_is_under_git_repo", lambda path, max_depth=4: False)
    target = tmp_path / "Downloads"
    _write_bytes(target / "maybe-user-file.tmp", 128)
    candidate = CleanupCandidate(
        id="cand_1",
        path=str(target),
        size_bytes=128,
        kind="downloads",
        risk=CONFIRM_REQUIRED,
        cleanup_mode=CONTENTS,
        source_rule="downloads_folder",
        last_write_time="",
        allowed_by_cleaner=False,
        reason="test",
    )

    rejected = cleanup_items([candidate], ["cand_1"], dry_run=False, confirmed_by_user=True, safe_age_hours=0)
    accepted = cleanup_items(
        [candidate],
        ["cand_1"],
        dry_run=False,
        confirmed_by_user=True,
        safe_age_hours=0,
        allow_confirm_required=True,
    )

    assert rejected.items[0].status == "Rejected"
    assert accepted.items[0].status == "Success"
    assert not (target / "maybe-user-file.tmp").exists()


def test_cleanup_rejects_unconfirmed_task(tmp_path):
    target = tmp_path / "temp"
    _write_bytes(target / "a.tmp", 128)
    candidate = CleanupCandidate(
        id="cand_1",
        path=str(target),
        size_bytes=128,
        kind="temp",
        risk=SAFE,
        cleanup_mode=CONTENTS,
        source_rule="named_temp_dir",
        last_write_time="",
        allowed_by_cleaner=True,
        reason="test",
    )

    report = cleanup_items([candidate], ["cand_1"], dry_run=False, confirmed_by_user=False, safe_age_hours=0)

    assert report.items == []
    assert (target / "a.tmp").exists()


def test_recycle_error_message_uses_actionable_chinese_text():
    assert "占用" in drive_cleaner.recycle_error_message(32)
    assert "访问被拒绝" in drive_cleaner.recycle_error_message(120)
    assert "无法处理" in drive_cleaner.recycle_error_message(124)
    assert "999" in drive_cleaner.recycle_error_message(999)


def test_move_paths_to_recycle_bin_processes_fixed_size_batches(tmp_path, monkeypatch):
    paths = []
    for index in range(5):
        path = tmp_path / f"item-{index}.tmp"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    batches = []
    progress = []

    def fake_execute(batch):
        batches.append([path.name for path in batch])
        for path in batch:
            path.unlink()
        return 0, False

    monkeypatch.setattr(drive_cleaner, "_execute_recycle_batch", fake_execute)
    errors = []
    ok = drive_cleaner._move_paths_to_recycle_bin(
        paths,
        errors,
        batch_size=2,
        progress=lambda done, total, path: progress.append((done, total, path)),
    )

    assert ok is True
    assert errors == []
    assert batches == [["item-0.tmp", "item-1.tmp"], ["item-2.tmp", "item-3.tmp"], ["item-4.tmp"]]
    assert [row[:2] for row in progress] == [(2, 5), (4, 5), (5, 5)]


def test_move_paths_to_recycle_bin_reports_only_remaining_failures(tmp_path, monkeypatch):
    moved = tmp_path / "moved.tmp"
    locked = tmp_path / "locked.tmp"
    moved.write_text("x", encoding="utf-8")
    locked.write_text("x", encoding="utf-8")

    def fake_execute(batch):
        batch[0].unlink()
        return 32, False

    monkeypatch.setattr(drive_cleaner, "_execute_recycle_batch", fake_execute)
    errors = []
    ok = drive_cleaner._move_paths_to_recycle_bin([moved, locked], errors, batch_size=2)

    assert ok is False
    assert len(errors) == 1
    assert str(locked) in errors[0]
    assert "文件正在被其他程序占用" in errors[0]


def test_move_paths_to_recycle_bin_checks_cancel_between_batches(tmp_path, monkeypatch):
    paths = []
    for index in range(4):
        path = tmp_path / f"cancel-{index}.tmp"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    cancelled = False
    calls = []

    def fake_execute(batch):
        calls.append([path.name for path in batch])
        for path in batch:
            path.unlink()
        return 0, False

    def on_progress(_done, _total, _path):
        nonlocal cancelled
        cancelled = True

    monkeypatch.setattr(drive_cleaner, "_execute_recycle_batch", fake_execute)
    errors = []
    ok = drive_cleaner._move_paths_to_recycle_bin(
        paths,
        errors,
        cancel=lambda: cancelled,
        progress=on_progress,
        batch_size=2,
    )

    assert ok is False
    assert calls == [["cancel-0.tmp", "cancel-1.tmp"]]
    assert paths[2].exists()


def test_scan_does_not_whitelist_project_cache_inside_git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    cache_dir = tmp_path / "cache"
    _write_bytes(cache_dir / "artifact.bin", 1024 * 1024 + 1)

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=10,
        min_duplicate_size_mb=10,
        max_depth=2,
    )

    candidates = report.drives[0].candidates
    assert any(candidate.path == str(cache_dir) and not candidate.allowed_by_cleaner for candidate in candidates)


def test_scan_modes_do_not_run_unselected_scanners(tmp_path):
    temp_dir = tmp_path / "temp"
    _write_bytes(temp_dir / "old.tmp", 1024 * 1024 + 10)
    _write_bytes(tmp_path / "huge.bin", 2 * 1024 * 1024, b"h")
    _write_bytes(tmp_path / "dup1.bin", 1024 * 1024, b"d")
    _write_bytes(tmp_path / "dup2.bin", 1024 * 1024, b"d")

    cleanup_report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=1,
        min_duplicate_size_mb=1,
        max_depth=3,
        scan_modes=["cleanup"],
    )
    cleanup_result = cleanup_report.drives[0]

    assert cleanup_result.candidates
    assert cleanup_result.large_files == []
    assert cleanup_result.duplicate_groups == []

    large_report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=1,
        min_duplicate_size_mb=1,
        max_depth=3,
        scan_modes=["large_files"],
    )
    large_result = large_report.drives[0]

    assert large_result.candidates == []
    assert any(entry.path.endswith("huge.bin") for entry in large_result.large_files)
    assert large_result.duplicate_groups == []


def test_large_file_scan_is_not_limited_by_depth(tmp_path):
    deep_dir = tmp_path / "a" / "b" / "c" / "d"
    deep_file = deep_dir / "deep-large.bin"
    _write_bytes(deep_file, 1024 * 1024 + 1, b"l")

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=100,
        min_large_file_size_mb=1,
        min_duplicate_size_mb=100,
        max_depth=2,
        scan_modes=["large_files"],
    )

    assert any(entry.path == str(deep_file) for entry in report.drives[0].large_files)


def test_large_file_scan_includes_development_directories(tmp_path):
    node_module_file = tmp_path / "project" / "node_modules" / "pkg" / "large-cache.bin"
    venv_file = tmp_path / "project" / ".venv" / "Lib" / "large-wheel.bin"
    _write_bytes(node_module_file, 1024 * 1024 + 1, b"n")
    _write_bytes(venv_file, 1024 * 1024 + 1, b"v")

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=100,
        min_large_file_size_mb=1,
        min_duplicate_size_mb=100,
        max_depth=3,
        scan_modes=["large_files"],
    )

    paths = {entry.path for entry in report.drives[0].large_files}
    assert str(node_module_file) in paths
    assert str(venv_file) in paths


def test_space_scan_accumulates_nested_directory_sizes(tmp_path):
    heavy_dir = tmp_path / "ProgramData"
    nested_dir = heavy_dir / "Cache"
    deeper_dir = nested_dir / "Deep"
    _write_bytes(heavy_dir / "a.bin", 1024)
    _write_bytes(nested_dir / "b.bin", 2048)
    _write_bytes(deeper_dir / "c.bin", 4096)
    events = []

    report = scan_drives(
        [str(tmp_path)],
        scan_modes=["space"],
        max_depth=2,
        progress=lambda message: events.append(message),
    )

    result = report.drives[0]
    by_path = {entry.path: entry for entry in result.large_dirs}
    assert by_path[str(heavy_dir)].size_bytes == 1024 + 2048 + 4096
    assert by_path[str(nested_dir)].size_bytes == 2048 + 4096
    assert str(deeper_dir) not in by_path
    assert result.space_total_size_bytes == 1024 + 2048 + 4096
    assert result.space_total_dirs == 2
    assert result.space_displayed_dirs == 2
    assert result.space_result_truncated is False
    assert result.space_scanned_files == 3
    assert "完整统计" in by_path[str(heavy_dir)].type_description
    assert any("正在统计目录体积" in message for message in events)


def test_space_scan_streams_preview_rows(tmp_path):
    visible_dir = tmp_path / "visible"
    _write_bytes(visible_dir / "file.bin", 1024, b"s")
    events = []

    scan_drives(
        [str(tmp_path)],
        scan_modes=["space"],
        max_depth=2,
        item_found=lambda kind, item: events.append((kind, item.path)),
    )

    assert ("large_dir_preview", str(visible_dir)) in events


def test_space_scan_counts_windows_winsxs(tmp_path):
    windows_dir = tmp_path / "Windows"
    winsxs_dir = windows_dir / "WinSxS"
    _write_bytes(winsxs_dir / "payload.bin", 4096)

    report = scan_drives([str(tmp_path)], scan_modes=["space"], max_depth=2)

    by_path = {entry.path: entry for entry in report.drives[0].large_dirs}
    assert by_path[str(windows_dir)].size_bytes == 4096
    assert by_path[str(winsxs_dir)].size_bytes == 4096


def test_scan_streams_found_items(tmp_path):
    _write_bytes(tmp_path / "huge.bin", 2 * 1024 * 1024, b"h")
    events = []

    scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=1,
        min_duplicate_size_mb=1,
        max_depth=2,
        scan_modes=["large_files"],
        progress=lambda message: events.append(("progress", message)),
        item_found=lambda kind, item: events.append((kind, item.path)),
    )

    assert any(kind == "progress" and "正在扫描" in message for kind, message in events)
    assert ("large_file", str(tmp_path / "huge.bin")) in events


def test_duplicate_scan_streams_size_preview_groups(tmp_path):
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    _write_bytes(first, 1024 * 1024, b"a")
    _write_bytes(second, 1024 * 1024, b"b")
    events = []

    scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=100,
        min_large_file_size_mb=100,
        min_duplicate_size_mb=1,
        max_depth=2,
        scan_modes=["duplicates"],
        item_found=lambda kind, item: events.append((kind, list(getattr(item, "paths", []) or []))),
    )

    assert any(kind == "duplicate_group_preview" and {str(first), str(second)} == set(paths) for kind, paths in events)


def test_duplicate_scan_skips_legacy_alias_paths(tmp_path):
    _write_bytes(tmp_path / "real_a.bin", 1024 * 1024, b"d")
    _write_bytes(tmp_path / "real_b.bin", 1024 * 1024, b"d")
    legacy = tmp_path / "Documents and Settings" / "Default User" / "Local Settings" / "Application Data"
    _write_bytes(legacy / "legacy_alias.bin", 1024 * 1024, b"d")

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=100,
        min_large_file_size_mb=100,
        min_duplicate_size_mb=1,
        max_depth=8,
        scan_modes=["duplicates"],
    )

    groups = report.drives[0].duplicate_groups
    assert len(groups) == 1
    assert {Path(path).name for path in groups[0].paths} == {"real_a.bin", "real_b.bin"}


def test_cleanup_mode_on_drive_root_uses_targeted_discovery(tmp_path, monkeypatch):
    local_app = tmp_path / "Users" / "me" / "AppData" / "Local"
    targeted_cache = local_app / "Cache"
    unrelated_cache = tmp_path / "Projects" / "HugeApp" / "Cache"
    _write_bytes(targeted_cache / "blob.bin", 1024 * 1024 + 1)
    _write_bytes(unrelated_cache / "blob.bin", 1024 * 1024 + 1)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Users" / "me" / "AppData" / "Roaming"))
    monkeypatch.setenv("TEMP", str(local_app / "Temp"))
    monkeypatch.setenv("TMP", str(local_app / "Temp"))
    monkeypatch.setattr("deepcat.drive_cleaner._is_drive_root_path", lambda _path: True)

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=100,
        min_duplicate_size_mb=100,
        max_depth=7,
        scan_modes=["cleanup"],
    )

    paths = {Path(candidate.path) for candidate in report.drives[0].candidates}
    assert targeted_cache in paths
    assert unrelated_cache not in paths


def test_cleanup_mode_on_non_system_drive_scans_common_root_dirs(tmp_path, monkeypatch):
    temp_dir = tmp_path / "Temp"
    cache_dir = tmp_path / "Cache"
    _write_bytes(temp_dir / "old.tmp", 1024 * 1024 + 1)
    _write_bytes(cache_dir / "blob.bin", 1024 * 1024 + 1)
    monkeypatch.setattr("deepcat.drive_cleaner._is_drive_root_path", lambda _path: True)
    monkeypatch.setattr("deepcat.drive_cleaner._is_non_system_drive_root", lambda _path: True)

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=100,
        min_duplicate_size_mb=100,
        max_depth=4,
        scan_modes=["cleanup"],
    )

    paths = {Path(candidate.path) for candidate in report.drives[0].candidates}
    assert temp_dir in paths
    assert cache_dir in paths


def test_cleanup_mode_loads_external_rule_cache_paths(tmp_path, monkeypatch):
    appdata = tmp_path / "Users" / "me" / "AppData" / "Roaming"
    vscode_cache = appdata / "Code - Insiders" / "Cache"
    _write_bytes(vscode_cache / "blob.bin", 1024 * 1024 + 1)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Users" / "me" / "AppData" / "Local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Users" / "me"))

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=100,
        min_duplicate_size_mb=100,
        max_depth=4,
        scan_modes=["cleanup"],
    )

    matches = [candidate for candidate in report.drives[0].candidates if Path(candidate.path) == vscode_cache]
    assert matches
    assert matches[0].risk == SAFE
    assert matches[0].allowed_by_cleaner is True
    assert matches[0].source_rule.startswith("external_rule:")


def test_external_backup_rule_requires_confirmation(tmp_path, monkeypatch):
    appdata = tmp_path / "Users" / "me" / "AppData" / "Roaming"
    backup_file = appdata / "aMule" / "logfile.bak"
    _write_bytes(backup_file, 1024 * 1024 + 1)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Users" / "me" / "AppData" / "Local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Users" / "me"))

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=100,
        min_duplicate_size_mb=100,
        max_depth=4,
        scan_modes=["cleanup"],
    )

    matches = [candidate for candidate in report.drives[0].candidates if Path(candidate.path) == backup_file]
    assert matches
    assert matches[0].risk == CONFIRM_REQUIRED
    assert matches[0].allowed_by_cleaner is False


def test_software_registry_values_classify_user_app(tmp_path):
    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "Example App",
            "DisplayVersion": "1.2.3",
            "Publisher": "Example",
            "InstallLocation": str(tmp_path / "Example App"),
            "UninstallString": "example-uninstall.exe",
            "EstimatedSize": 2048,
        },
        root_name="HKCU",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Example",
        registry_view="user",
    )

    assert entry is not None
    assert entry.software_type == "用户"
    assert entry.is_system is False
    assert entry.size_bytes == 2048 * 1024
    assert entry.uninstall_command == "example-uninstall.exe"


def test_software_install_location_falls_back_to_display_icon_and_uninstall_command(tmp_path, monkeypatch):
    app_dir = tmp_path / "Program Files" / "Example App"
    icon_file = app_dir / "Example.exe"
    uninstall_file = app_dir / "unins000.exe"
    _write_bytes(icon_file, 1)
    _write_bytes(uninstall_file, 1)
    monkeypatch.setattr(drive_cleaner, "_find_software_install_locations", lambda _name, _publisher="": [])

    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "Example App",
            "Publisher": "Example",
            "DisplayIcon": f'"{icon_file}",0',
            "UninstallString": f'"{uninstall_file}" /S',
        },
        root_name="HKCU",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Example",
        registry_view="user",
    )

    assert entry is not None
    assert entry.install_location.split(" | ")[0] == str(app_dir.resolve())


def test_software_locations_search_common_roots_and_nested_data(tmp_path, monkeypatch):
    install_root = tmp_path / "D" / "Program Files"
    install_dir = install_root / "ExampleTool"
    data_root = tmp_path / "Users" / "me" / "AppData" / "Roaming"
    nested_data = data_root / "ExampleCorp" / "ExampleTool"
    install_dir.mkdir(parents=True)
    nested_data.mkdir(parents=True)
    _write_bytes(install_dir / "ExampleTool.exe", 1)
    _write_bytes(nested_data / "settings.json", 1)
    monkeypatch.setattr(drive_cleaner, "_software_install_search_roots", lambda: [install_root])
    monkeypatch.setattr(drive_cleaner, "_software_data_search_roots", lambda: [data_root])
    monkeypatch.setenv("APPDATA", str(data_root))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Users" / "me" / "AppData" / "Local"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))

    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "ExampleTool",
            "Publisher": "ExampleCorp",
        },
        root_name="HKCU",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ExampleTool",
        registry_view="user",
    )

    assert entry is not None
    assert str(install_dir.resolve()) in entry.install_location
    assert str(nested_data.resolve()) in entry.data_location


def test_cursor_user_suffix_does_not_match_other_user_data_dirs(tmp_path, monkeypatch):
    appdata = tmp_path / "Users" / "me" / "AppData" / "Roaming"
    cursor_data = appdata / "Cursor"
    unrelated_user_dirs = [
        appdata / "Antigravity" / "User",
        appdata / "Billfish" / "user",
        appdata / "Code" / "User",
    ]
    _write_bytes(cursor_data / "settings.json", 1)
    for user_dir in unrelated_user_dirs:
        _write_bytes(user_dir / "settings.json", 1)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Users" / "me" / "AppData" / "Local"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setattr(drive_cleaner, "_find_software_install_locations", lambda _name, _publisher="": [])
    monkeypatch.setattr(drive_cleaner, "_software_data_search_roots", lambda: [appdata])

    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "Cursor (User) 3.7.42",
            "Publisher": "Cursor",
        },
        root_name="HKCU",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Cursor",
        registry_view="user",
    )

    assert entry is not None
    assert entry.data_location == str(cursor_data)
    for user_dir in unrelated_user_dirs:
        assert str(user_dir) not in entry.data_location


def test_software_force_path_rejects_generic_user_directory(tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_bytes(user_dir / "settings.json", 1)

    assert drive_cleaner._software_force_path_allowed(user_dir) is False


def test_software_force_paths_deduplicate_install_and_data_locations(tmp_path):
    install_dir = tmp_path / "Users" / "me" / "AppData" / "Local" / "Programs" / "antigravity"
    updater_dir = tmp_path / "Users" / "me" / "AppData" / "Local" / "antigravity-updater"
    data_dir = tmp_path / "Users" / "me" / "AppData" / "Roaming" / "Antigravity"
    for path in (install_dir, updater_dir, data_dir):
        _write_bytes(path / "settings.json", 1)
    entry = SoftwareEntry(
        id="soft_antigravity",
        name="Antigravity (User)",
        version="1.23.2",
        publisher="Antigravity",
        size_bytes=0,
        install_location=f"{install_dir} | {updater_dir} | {install_dir}",
        data_location=f"{updater_dir} | {data_dir} | {data_dir}",
        uninstall_command="",
        quiet_uninstall_command="",
        registry_root="HKCU",
        registry_path=r"Software\Test",
        registry_view="user",
        software_type="用户",
        is_system=False,
        reason="",
    )

    assert drive_cleaner.software_entry_force_paths(entry) == [
        str(install_dir),
        str(updater_dir),
        str(data_dir),
    ]


def test_software_location_filters_empty_directories(tmp_path, monkeypatch):
    install_root = tmp_path / "D" / "Program Files"
    empty_install = install_root / "EmptyTool"
    empty_install.mkdir(parents=True)
    monkeypatch.setattr(drive_cleaner, "_software_install_search_roots", lambda: [install_root])

    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "EmptyTool",
            "Publisher": "Empty",
        },
        root_name="HKCU",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\EmptyTool",
        registry_view="user",
    )

    assert entry is not None
    assert entry.install_location == ""
    assert drive_cleaner.software_entry_should_display(entry) is False


def test_software_display_keeps_system_without_paths():
    entry = SoftwareEntry(
        id="soft_system",
        name="Runtime Component",
        version="",
        publisher="Microsoft",
        size_bytes=0,
        install_location="",
        data_location="",
        uninstall_command="",
        quiet_uninstall_command="",
        registry_root="HKLM",
        registry_path="Software\\Test",
        registry_view="machine64",
        software_type="系统",
        is_system=True,
        reason="系统组件",
    )

    assert drive_cleaner.software_entry_should_display(entry) is True


def test_software_install_location_ignores_missing_registry_path(tmp_path, monkeypatch):
    missing_install = tmp_path / "Program Files" / "Missing App"
    monkeypatch.setattr(drive_cleaner, "_find_software_install_locations", lambda _name, _publisher="": [])
    monkeypatch.setattr(drive_cleaner, "_software_data_search_roots", lambda: [])
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))

    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "Missing App",
            "InstallLocation": str(missing_install),
        },
        root_name="HKCU",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Missing",
        registry_view="user",
    )

    assert entry is not None
    assert entry.install_location == ""
    assert drive_cleaner.software_entry_should_display(entry) is False


def test_software_registry_values_classify_system_component(tmp_path):
    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "Windows Component",
            "Publisher": "Microsoft Corporation",
            "InstallLocation": str(tmp_path / "Windows"),
            "SystemComponent": 1,
        },
        root_name="HKLM",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Component",
        registry_view="machine64",
    )

    assert entry is not None
    assert entry.software_type == "系统"
    assert entry.is_system is True


def test_software_system_component_from_third_party_stays_user(tmp_path):
    app_dir = tmp_path / "Snagit"
    _write_bytes(app_dir / "Snagit.exe", 1)
    entry = drive_cleaner._software_entry_from_registry_values(
        {
            "DisplayName": "Snagit",
            "Publisher": "TechSmith Corporation",
            "InstallLocation": str(app_dir),
            "SystemComponent": 1,
        },
        root_name="HKLM",
        registry_path=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Snagit",
        registry_view="machine64",
    )

    assert entry is not None
    assert entry.software_type == "用户"
    assert entry.is_system is False


def test_uninstall_rejects_system_software():
    entry = SoftwareEntry(
        id="soft_system",
        name="System App",
        version="",
        publisher="Microsoft",
        size_bytes=0,
        install_location="",
        data_location="",
        uninstall_command="",
        quiet_uninstall_command="",
        registry_root="HKLM",
        registry_path="Software\\Test",
        registry_view="machine64",
        software_type="系统",
        is_system=True,
        reason="系统组件",
    )

    report = uninstall_software([entry], ["soft_system"], force=True)

    assert report.items[0].status == "Rejected"
    assert "系统软件" in report.items[0].message


# --- P0 回归：磁盘清理器不得永久删除用户真实目录 -----------------------------
# 缺陷链路（修复前）：
#   1. 任意名为 temp/tmp 的目录被 _classify_directory 判为 SAFE；
#   2. named_temp_dir 在 _candidate_allowed_by_cleaner 白名单内 → allowed_by_cleaner=True；
#   3. _candidate_can_fast_delete 只要文本含 "temp"/"cache" 就允许绕过回收站永久删除；
#   4. _is_forbidden_broad_path 只做精确等值匹配，不含桌面/文档等用户目录。
# 结果：D:\Projects\temp 这类目录会被建议清理并在勾选后被永久清空且不可恢复。


def _redirect_cleanup_roots(monkeypatch, tmp_path: Path) -> None:
    """把所有受管清理根重定向到 tmp_path 内部，用于构造"非受管位置"。

    必须这样做：pytest 的 tmp_path 本身位于 %LOCALAPPDATA%\\Temp 之下，
    直接使用真实环境时它的所有子目录都算"受管"，无法构造反例。
    """
    users = tmp_path / "Users" / "tester"
    local = users / "AppData" / "Local"
    env = {
        "SystemRoot": str(tmp_path / "Windows"),
        "LOCALAPPDATA": str(local),
        "APPDATA": str(users / "AppData" / "Roaming"),
        "PROGRAMDATA": str(tmp_path / "ProgramData"),
        "USERPROFILE": str(users),
        "TEMP": str(local / "Temp"),
        "TMP": str(local / "Temp"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _make_candidate(path: Path, *, rule: str, risk: str = SAFE, kind: str = "temp") -> CleanupCandidate:
    return CleanupCandidate(
        id="cand_1",
        path=str(path),
        size_bytes=1024,
        kind=kind,
        risk=risk,
        cleanup_mode=CONTENTS,
        source_rule=rule,
        last_write_time="",
        allowed_by_cleaner=risk == SAFE,
        reason="test",
    )


def test_project_temp_dir_is_not_treated_as_safe(tmp_path, monkeypatch):
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    project_temp = tmp_path / "Projects" / "temp"
    project_temp.mkdir(parents=True)

    classified = drive_cleaner._classify_directory(project_temp, "temp")

    assert classified is not None
    assert classified[3] == CONFIRM_REQUIRED
    assert not drive_cleaner._candidate_allowed_by_cleaner(project_temp, classified[0], classified[1], classified[2])


def test_project_logs_dir_is_not_treated_as_safe(tmp_path, monkeypatch):
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    project_logs = tmp_path / "Projects" / "MyApp" / "logs"
    project_logs.mkdir(parents=True)

    classified = drive_cleaner._classify_directory(project_logs, "logs")

    assert classified is not None
    assert classified[3] == CONFIRM_REQUIRED
    assert not drive_cleaner._candidate_allowed_by_cleaner(project_logs, classified[0], classified[1], classified[2])


def test_managed_temp_root_still_safe(tmp_path, monkeypatch):
    """正向对照：受管根内的临时目录仍判为 SAFE，保证正常清理能力不退化。"""
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    managed_temp = Path(os.environ["TEMP"]) / "nested" / "temp"
    managed_temp.mkdir(parents=True)

    classified = drive_cleaner._classify_directory(managed_temp, "temp")

    assert classified is not None
    assert classified[3] == SAFE
    assert drive_cleaner._candidate_allowed_by_cleaner(managed_temp, classified[0], classified[1], classified[2])


def test_named_temp_dir_is_never_permanently_deleted(tmp_path, monkeypatch):
    """即使显式选择"永久删除"，名称匹配得到的临时目录也只会移入回收站。"""
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    unmanaged = tmp_path / "Projects" / "temp"
    managed = Path(os.environ["TEMP"]) / "temp"
    unmanaged.mkdir(parents=True)
    managed.mkdir(parents=True)

    # 名字匹配规则 → 永不直删
    assert drive_cleaner._effective_delete_mode(_make_candidate(unmanaged, rule="named_temp_dir"), "permanent") == "recycle"
    assert (
        drive_cleaner._effective_delete_mode(
            _make_candidate(unmanaged, rule="named_temp_dir_unmanaged", risk=CONFIRM_REQUIRED), "permanent"
        )
        == "recycle"
    )
    # 受管根内、由显式环境变量规则识别的真实临时目录 → 允许直删
    assert (
        drive_cleaner._effective_delete_mode(
            _make_candidate(managed, rule="user_temp_windows", kind="user_temp"), "permanent"
        )
        == "permanent"
    )
    # 未请求永久删除时一律回收站
    assert (
        drive_cleaner._effective_delete_mode(
            _make_candidate(managed, rule="user_temp_windows", kind="user_temp"), "recycle"
        )
        == "recycle"
    )


def test_managed_rule_outside_managed_root_falls_back_to_recycle(tmp_path, monkeypatch):
    """双重保险：规则命中白名单但路径不在受管根内时，退化为回收站删除。"""
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    stray = tmp_path / "Projects" / "cache"
    stray.mkdir(parents=True)

    assert (
        drive_cleaner._effective_delete_mode(_make_candidate(stray, rule="npm_cache", kind="dev_cache"), "permanent")
        == "recycle"
    )


def test_scan_downgrades_unmanaged_project_temp_dir(tmp_path, monkeypatch):
    """端到端：扫描工程目录时，其中的 temp 目录降级为待确认而不是"可安全清理"。"""
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    target = tmp_path / "Projects" / "temp"
    _write_bytes(target / "user-work.bin", 1024 * 1024 + 1)

    report = scan_drives(
        [str(tmp_path)],
        min_candidate_size_mb=1,
        min_large_file_size_mb=100,
        min_duplicate_size_mb=100,
        max_depth=4,
        scan_modes=["cleanup"],
    )

    matches = [candidate for candidate in report.drives[0].candidates if Path(candidate.path).resolve() == target.resolve()]
    assert matches, "仍应作为候选项出现，供用户确认后清理"
    assert matches[0].risk == CONFIRM_REQUIRED
    assert matches[0].allowed_by_cleaner is False


def test_user_document_roots_are_protected(tmp_path, monkeypatch):
    """桌面/文档等用户目录及其子目录即使名为 temp，也不得进入清理候选。"""
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    desktop_temp = tmp_path / "Users" / "tester" / "Desktop" / "temp"
    desktop_temp.mkdir(parents=True)

    assert drive_cleaner._is_forbidden_broad_path(desktop_temp)
    classified = drive_cleaner._classify_directory(desktop_temp, "temp")
    assert classified is not None
    assert classified[3] == drive_cleaner.AVOID
    assert not drive_cleaner._candidate_allowed_by_cleaner(desktop_temp, "temp", CONTENTS, "named_temp_dir")


def test_downloads_dir_still_cleanable_after_confirmation(tmp_path, monkeypatch):
    """有意保留：下载目录仍是"确认后可清理"的目标，不受用户目录保护影响。"""
    _redirect_cleanup_roots(monkeypatch, tmp_path)
    downloads = tmp_path / "Users" / "tester" / "Downloads"
    downloads.mkdir(parents=True)

    assert not drive_cleaner._is_forbidden_broad_path(downloads)
