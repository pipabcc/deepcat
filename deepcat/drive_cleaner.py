from __future__ import annotations

import hashlib
import ctypes
import heapq
import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional


SAFE = "Safe"
CONFIRM_REQUIRED = "ConfirmRequired"
AVOID = "Avoid"
CONTENTS = "Contents"
SELF = "Self"

_GB = 1024 ** 3
_MB = 1024 ** 2
_CLEANUP_MEASURE_MAX_FILES = 30_000
_CLEANUP_MEASURE_MAX_SECONDS = 2.5
_LARGE_FILE_RESULT_LIMIT = 1000
_SPACE_DIR_RESULT_LIMIT = 5000
_SPACE_PREVIEW_LIMIT = 80
_SPACE_PROGRESS_INTERVAL_SECONDS = 0.35
_LARGE_FILE_PROGRESS_INTERVAL_SECONDS = 0.35
_EXTERNAL_RULE_PREFIX = "external_rule:"
_EXTERNAL_RULE_SKIP_FILES = {
    "cdisk_cleaner_config.json",
    "cdisk_cleaner_global_settings.json",
}
_EXTERNAL_RULE_SAFE_TOKENS = (
    "cache",
    "cached",
    "caches",
    "code cache",
    "gpucache",
    "dawncache",
    "shadercache",
    "webcache",
    "temp",
    "tmp",
    "log",
    "logs",
    "crash",
    "dump",
    "缓存",
    "临时",
    "日志",
    "崩溃",
)
_EXTERNAL_RULE_CONFIRM_TOKENS = (
    "download",
    "downloads",
    "history",
    "workspace",
    "backup",
    "backups",
    "known",
    "下载",
    "历史",
    "备份",
)
_SOFTWARE_INSTALL_ROOT_CACHE: Optional[list[Path]] = None
_SOFTWARE_DATA_ROOT_CACHE: Optional[list[Path]] = None
_SOFTWARE_DIR_INDEX_CACHE: dict[str, list[Path]] = {}


@dataclass
class DriveInfo:
    drive: str
    root: str
    drive_type: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


@dataclass
class CleanupCandidate:
    id: str
    path: str
    size_bytes: int
    kind: str
    risk: str
    cleanup_mode: str
    source_rule: str
    last_write_time: str
    allowed_by_cleaner: bool
    reason: str
    priority: int = 50


@dataclass
class LargeEntry:
    path: str
    size_bytes: int
    kind: str
    last_write_time: str
    last_access_time: str = ""
    extension: str = ""
    type_label: str = ""
    type_description: str = ""
    access_time_basis: str = "访问时间"


@dataclass
class DuplicateGroup:
    size_bytes: int
    hash: str
    paths: list[str]
    content_hash_confirmed: bool = True
    hash_errors: list[str] = field(default_factory=list)


@dataclass
class SpaceScanResult:
    entries: list[LargeEntry] = field(default_factory=list)
    total_size_bytes: int = 0
    total_dirs: int = 0
    displayed_dirs: int = 0
    display_limit: int = _SPACE_DIR_RESULT_LIMIT
    truncated: bool = False
    scanned_files: int = 0


@dataclass
class DriveScanResult:
    drive: DriveInfo
    candidates: list[CleanupCandidate] = field(default_factory=list)
    large_dirs: list[LargeEntry] = field(default_factory=list)
    large_files: list[LargeEntry] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    scan_errors: list[str] = field(default_factory=list)
    space_total_size_bytes: int = 0
    space_total_dirs: int = 0
    space_displayed_dirs: int = 0
    space_display_limit: int = _SPACE_DIR_RESULT_LIMIT
    space_result_truncated: bool = False
    space_scanned_files: int = 0


@dataclass
class ScanReport:
    schema_version: str
    created_at: str
    drives: list[DriveScanResult]
    advisor_summary: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class CleanupItem:
    id: str
    path: str
    mode: str
    expected_kind: str
    source_rule: str


@dataclass
class CleanupItemResult:
    id: str
    path: str
    mode: str
    before_size: int
    after_size: int
    freed_size: int
    status: str
    error_count: int = 0
    first_error: str = ""
    delete_mode: str = ""


@dataclass
class CleanupReport:
    schema_version: str
    started_at: str
    finished_at: str
    dry_run: bool
    items: list[CleanupItemResult]


@dataclass
class SoftwareEntry:
    id: str
    name: str
    version: str
    publisher: str
    size_bytes: int
    install_location: str
    data_location: str
    uninstall_command: str
    quiet_uninstall_command: str
    registry_root: str
    registry_path: str
    registry_view: str
    software_type: str
    is_system: bool
    reason: str


@dataclass
class SoftwareUninstallItemResult:
    id: str
    name: str
    mode: str
    status: str
    message: str = ""
    removed_paths: list[str] = field(default_factory=list)


@dataclass
class SoftwareUninstallReport:
    schema_version: str
    started_at: str
    finished_at: str
    mode: str
    items: list[SoftwareUninstallItemResult]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def format_bytes(value: int) -> str:
    """格式化字节数为可读字符串，支持负值（表示空间增加）。"""
    raw = int(value or 0)
    prefix = "-" if raw < 0 else ""
    n = float(abs(raw))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{prefix}{int(n)} {unit}"
            return f"{prefix}{n:.2f} {unit}"
        n /= 1024
    return f"{prefix}{n:.2f} TB"


def enumerate_drives() -> list[DriveInfo]:
    roots = _windows_drive_roots() if os.name == "nt" else ["/"]
    drives: list[DriveInfo] = []
    for root in roots:
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        total = int(usage.total)
        free = int(usage.free)
        used = max(0, total - free)
        drives.append(
            DriveInfo(
                drive=str(Path(root).drive or root).rstrip("\\/"),
                root=root,
                drive_type=_drive_type(root),
                total_bytes=total,
                used_bytes=used,
                free_bytes=free,
            )
        )
    return drives


def scan_drives(
    roots: Iterable[str],
    *,
    min_candidate_size_mb: int = 20,
    min_large_file_size_mb: int = 200,
    min_duplicate_size_mb: int = 10,
    max_depth: int = 7,
    scan_modes: Optional[Iterable[str]] = None,
    confirm_duplicate_content_hash: bool = True,
    cancel: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[str], None]] = None,
    item_found: Optional[Callable[[str, object], None]] = None,
) -> ScanReport:
    modes = _normalize_scan_modes(scan_modes)
    drive_map = {d.root.upper(): d for d in enumerate_drives()}
    results: list[DriveScanResult] = []
    for root in roots:
        if _cancelled(cancel):
            break
        normalized_root = _normalize_drive_root(root)
        if progress:
            progress(f"扫描 {normalized_root}")
        drive = drive_map.get(normalized_root.upper())
        if drive is None:
            try:
                usage = shutil.disk_usage(normalized_root)
                drive = DriveInfo(
                    drive=str(Path(normalized_root).drive or normalized_root).rstrip("\\/"),
                    root=normalized_root,
                    drive_type=_drive_type(normalized_root),
                    total_bytes=int(usage.total),
                    used_bytes=int(usage.total - usage.free),
                    free_bytes=int(usage.free),
                )
            except OSError as exc:
                results.append(
                    DriveScanResult(
                        drive=DriveInfo(str(root), normalized_root, "unknown", 0, 0, 0),
                        scan_errors=[f"无法访问盘符: {exc}"],
                    )
                )
                continue
        results.append(
            _scan_one_drive(
                drive,
                min_candidate_size_mb=int(min_candidate_size_mb),
                min_large_file_size_mb=int(min_large_file_size_mb),
                min_duplicate_size_mb=int(min_duplicate_size_mb),
                max_depth=int(max_depth),
                scan_modes=modes,
                confirm_duplicate_content_hash=bool(confirm_duplicate_content_hash),
                cancel=cancel,
                progress=progress,
                item_found=item_found,
            )
        )
    return ScanReport(
        schema_version="1.0",
        created_at=now_iso(),
        drives=results,
        advisor_summary=_build_rule_advisor_summary(results),
        warnings=[
            "默认只基于本地规则生成建议；模型只用于解释，不会决定删除路径。",
            "真实清理前请关闭浏览器、IDE、包管理器等可能占用缓存的程序。",
        ],
    )


def cleanup_items(
    candidates: Iterable[CleanupCandidate],
    selected_ids: Iterable[str],
    *,
    dry_run: bool = True,
    confirmed_by_user: bool = False,
    safe_age_hours: int = 24,
    allow_confirm_required: bool = False,
    cancel: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[int, int, str, int, CleanupItemResult], None]] = None,
    detail_progress: Optional[Callable[[int, int, str, int, int, str], None]] = None,
    delete_mode: str = "recycle",
) -> CleanupReport:
    """执行清理操作，支持取消。"""
    started = now_iso()
    by_id = {c.id: c for c in candidates}
    ids = [str(item_id) for item_id in selected_ids]
    total = len(ids)
    results: list[CleanupItemResult] = []
    if not confirmed_by_user:
        return CleanupReport("1.0", started, now_iso(), bool(dry_run), results)
    released_total = 0
    for item_id in ids:
        if _cancelled(cancel):
            break
        candidate = by_id.get(str(item_id))
        if candidate is None:
            continue
        result = _cleanup_candidate(
            candidate,
            dry_run=bool(dry_run),
            safe_age_hours=int(safe_age_hours),
            allow_confirm_required=bool(allow_confirm_required),
            cancel=cancel,
            detail_progress=(
                lambda current, detail_total, detail_path, done=len(results), candidate_path=str(candidate.path): detail_progress(
                    done, total, candidate_path, current, detail_total, detail_path
                )
                if detail_progress is not None
                else None
            ),
            delete_mode=delete_mode,
        )
        results.append(result)
        released_total += int(result.freed_size or 0)
        if progress is not None:
            progress(len(results), total, str(result.path or candidate.path), released_total, result)
    return CleanupReport("1.0", started, now_iso(), bool(dry_run), results)


def export_scan_report(report: ScanReport, path: str | Path) -> Path:
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() == ".json":
        dst.write_text(json.dumps(_scan_report_to_dict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        dst.write_text(scan_report_to_markdown(report), encoding="utf-8")
    return dst


def request_llm_advice(report: ScanReport, model_config: dict, *, timeout_seconds: int = 30) -> str:
    import httpx

    cfg = dict(model_config or {})
    base_url = str(cfg.get("base_url", "") or "").strip().rstrip("/")
    model = str(cfg.get("model_name", "") or cfg.get("model", "") or "").strip()
    api_key = str(cfg.get("api_key", "") or "").strip()
    if not base_url or not model:
        raise ValueError("模型配置缺少 API 地址或模型名称")
    url = _chat_completions_url(base_url)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 800,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 Windows 磁盘清理建议助手。只根据用户提供的扫描元数据给中文解释和排序建议。"
                    "不要输出命令，不要新增路径，不要建议清理 Avoid 项。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(_llm_advisor_payload(report), ensure_ascii=False),
            },
        ],
    }
    with httpx.Client(timeout=float(timeout_seconds)) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    content = ""
    try:
        content = str(data["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        content = ""
    if not content:
        raise ValueError("模型未返回有效建议")
    return content


def scan_report_to_markdown(report: ScanReport) -> str:
    lines = [
        "# 磁盘清理优化扫描报告",
        "",
        f"- 扫描时间: {report.created_at}",
        f"- 建议摘要: {report.advisor_summary}",
        "",
        "## 盘符概览",
        "",
        "| 盘符 | 总空间 | 已用 | 可用 | 空间分析总大小 | 空间目录 | 候选清理 | 大文件 | 重复组 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in report.drives:
        d = result.drive
        space_dirs = int(result.space_total_dirs or len(result.large_dirs))
        displayed_dirs = int(result.space_displayed_dirs or len(result.large_dirs))
        lines.append(
            f"| {d.drive} | {format_bytes(d.total_bytes)} | {format_bytes(d.used_bytes)} | "
            f"{format_bytes(d.free_bytes)} | {format_bytes(int(result.space_total_size_bytes or 0))} | "
            f"{displayed_dirs}/{space_dirs} | {len(result.candidates)} | {len(result.large_files)} | {len(result.duplicate_groups)} |"
        )
    lines.extend(["", "## 清理候选项", ""])
    lines.extend(_candidate_table(c for r in report.drives for c in r.candidates))
    lines.extend(["", "## 大文件", ""])
    lines.extend(_large_entry_table(e for r in report.drives for e in r.large_files[:50]))
    lines.extend(["", "## 重复文件组", ""])
    for group in (g for r in report.drives for g in r.duplicate_groups[:30]):
        lines.append(f"- {format_bytes(group.size_bytes)} x {len(group.paths)}: " + " | ".join(group.paths[:4]))
    if any(r.scan_errors for r in report.drives):
        lines.extend(["", "## 扫描错误", ""])
        for result in report.drives:
            for err in result.scan_errors:
                lines.append(f"- {result.drive.drive}: {err}")
    return "\n".join(lines).rstrip() + "\n"


def cleanup_report_to_markdown(report: CleanupReport) -> str:
    lines = [
        "# 磁盘清理执行报告",
        "",
        f"- 开始时间: {report.started_at}",
        f"- 结束时间: {report.finished_at}",
        f"- 模式: {'Dry-run 预演' if report.dry_run else '真实清理'}",
        "",
        "| 状态 | 删除方式 | 路径 | 清理前 | 清理后 | 逻辑释放 | 错误 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for item in report.items:
        lines.append(
            f"| {item.status} | {item.delete_mode or '-'} | {item.path} | {format_bytes(item.before_size)} | "
            f"{format_bytes(item.after_size)} | {format_bytes(item.freed_size)} | {item.error_count} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def software_uninstall_report_to_text(report: SoftwareUninstallReport) -> str:
    lines = [
        "软件卸载执行报告",
        f"开始时间: {report.started_at}",
        f"结束时间: {report.finished_at}",
        f"模式: {'强力卸载' if report.mode == 'force' else '标准卸载'}",
        "",
    ]
    for item in report.items:
        lines.append(f"- {item.status}  {item.name}: {item.message}")
        for path in item.removed_paths:
            lines.append(f"  已处理路径: {path}")
    if not report.items:
        lines.append("- 未处理任何软件。")
    return "\n".join(lines).rstrip()


def enumerate_installed_software(*, fast: bool = False) -> list[SoftwareEntry]:
    """枚举已安装软件。fast=True 时跳过磁盘目录扫描，秒级完成。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    if not fast:
        _reset_software_directory_caches()
    entries: list[SoftwareEntry] = []
    seen: set[str] = set()
    registry_roots = [
        ("HKCU", winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "user", 0),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "machine64", getattr(winreg, "KEY_WOW64_64KEY", 0)),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", "machine32", getattr(winreg, "KEY_WOW64_32KEY", 0)),
    ]
    for root_name, root_key, path, view_name, view_flag in registry_roots:
        try:
            with winreg.OpenKey(root_key, path, 0, winreg.KEY_READ | view_flag) as uninstall_root:
                subkey_count = int(winreg.QueryInfoKey(uninstall_root)[0])
                for index in range(subkey_count):
                    try:
                        subkey_name = winreg.EnumKey(uninstall_root, index)
                    except OSError:
                        continue
                    registry_path = f"{path}\\{subkey_name}"
                    try:
                        with winreg.OpenKey(root_key, registry_path, 0, winreg.KEY_READ | view_flag) as item_key:
                            values = _registry_values(item_key)
                    except OSError:
                        continue
                    entry = _software_entry_from_registry_values(
                        values,
                        root_name=root_name,
                        registry_path=registry_path,
                        registry_view=view_name,
                        skip_disk_scan=fast,
                    )
                    if entry is None:
                        continue
                    key = f"{entry.name.lower()}|{entry.version.lower()}|{entry.install_location.lower()}|{entry.registry_root}|{entry.registry_path}"
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(entry)
        except OSError:
            continue
    entries.sort(key=lambda item: (item.is_system, item.name.lower(), item.version.lower()))
    return entries


def _reset_software_directory_caches() -> None:
    global _SOFTWARE_INSTALL_ROOT_CACHE, _SOFTWARE_DATA_ROOT_CACHE
    _SOFTWARE_INSTALL_ROOT_CACHE = None
    _SOFTWARE_DATA_ROOT_CACHE = None
    _SOFTWARE_DIR_INDEX_CACHE.clear()


def reset_software_caches() -> None:
    """重置软件目录搜索缓存，用于刷新扫描前清除旧数据。"""
    _reset_software_directory_caches()


def enrich_software_entry(entry: SoftwareEntry) -> SoftwareEntry:
    """对单条软件条目补全安装目录、数据目录和大小信息（需要磁盘扫描）。"""
    if os.name != "nt":
        return entry
    try:
        import winreg
    except ImportError:
        return entry

    _root_map = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    _view_map = {
        "user": 0,
        "machine64": getattr(winreg, "KEY_WOW64_64KEY", 0),
        "machine32": getattr(winreg, "KEY_WOW64_32KEY", 0),
    }
    root_key = _root_map.get(entry.registry_root)
    view_flag = _view_map.get(entry.registry_view, 0)
    if root_key is None:
        return entry

    try:
        with winreg.OpenKey(root_key, entry.registry_path, 0, winreg.KEY_READ | view_flag) as item_key:
            values = _registry_values(item_key)
    except OSError:
        return entry

    install_location = _infer_software_install_location(entry.name, entry.publisher, values)
    data_location = _infer_software_data_location(entry.name, entry.publisher, install_location)

    # 当注册表未提供 EstimatedSize 时，从安装目录估算实际大小
    size_bytes = entry.size_bytes
    if size_bytes == 0 and install_location:
        size_bytes = _calculate_dir_size_fast(install_location)

    return SoftwareEntry(
        id=entry.id,
        name=entry.name,
        version=entry.version,
        publisher=entry.publisher,
        size_bytes=size_bytes,
        install_location=install_location,
        data_location=data_location,
        uninstall_command=entry.uninstall_command,
        quiet_uninstall_command=entry.quiet_uninstall_command,
        registry_root=entry.registry_root,
        registry_path=entry.registry_path,
        registry_view=entry.registry_view,
        software_type=entry.software_type,
        is_system=entry.is_system,
        reason=entry.reason,
    )


def software_entry_should_display(entry: SoftwareEntry) -> bool:
    """过滤明显残留的软件项，避免把空目录/失效目录当成可卸载软件展示。"""
    if entry.is_system or entry.software_type == "系统":
        return True
    if entry.install_location or entry.data_location:
        return True
    return _registry_command_has_existing_target(entry.uninstall_command) or _registry_command_has_existing_target(entry.quiet_uninstall_command)


def _calculate_dir_size_fast(paths_text: str, *, max_files: int = 5000, max_depth: int = 5) -> int:
    """快速估算目录大小，限制递归深度和文件数量以避免大目录阻塞。"""
    total = 0
    file_count = 0
    for path_text in _split_path_list(paths_text):
        root = Path(path_text)
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        root_depth = str(root).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = str(dirpath).count(os.sep) - root_depth
            if depth >= max_depth:
                dirnames.clear()
                continue
            for filename in filenames:
                if file_count >= max_files:
                    return total
                file_count += 1
                try:
                    total += os.path.getsize(os.path.join(dirpath, filename))
                except OSError:
                    pass
    return total


def uninstall_software(
    entries: Iterable[SoftwareEntry],
    selected_ids: Iterable[str],
    *,
    force: bool,
    cancel: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[int, int, SoftwareEntry, SoftwareUninstallItemResult], None]] = None,
) -> SoftwareUninstallReport:
    started = now_iso()
    entry_by_id = {entry.id: entry for entry in entries}
    ids = [str(item_id) for item_id in selected_ids]
    total = len(ids)
    results: list[SoftwareUninstallItemResult] = []
    for item_id in ids:
        if _cancelled(cancel):
            break
        entry = entry_by_id.get(str(item_id))
        if entry is None:
            continue
        if entry.is_system or entry.software_type == "系统":
            result = SoftwareUninstallItemResult(entry.id, entry.name, "force" if force else "standard", "Rejected", "系统软件不可卸载")
            results.append(result)
            if progress is not None:
                progress(len(results), total, entry, result)
            continue
        if force:
            result = _force_uninstall_entry(entry)
        else:
            result = _standard_uninstall_entry(entry)
        results.append(result)
        if progress is not None:
            progress(len(results), total, entry, result)
    return SoftwareUninstallReport("1.0", started, now_iso(), "force" if force else "standard", results)


def _registry_values(key: object) -> dict[str, object]:
    try:
        import winreg
    except ImportError:
        return {}
    values: dict[str, object] = {}
    try:
        value_count = int(winreg.QueryInfoKey(key)[1])
    except OSError:
        return values
    for index in range(value_count):
        try:
            name, value, _value_type = winreg.EnumValue(key, index)
        except OSError:
            continue
        values[str(name)] = value
    return values


def _software_entry_from_registry_values(
    values: dict[str, object],
    *,
    root_name: str,
    registry_path: str,
    registry_view: str,
    skip_disk_scan: bool = False,
) -> Optional[SoftwareEntry]:
    name = str(values.get("DisplayName") or "").strip()
    if not name:
        return None
    version = str(values.get("DisplayVersion") or "").strip()
    publisher = str(values.get("Publisher") or "").strip()
    uninstall_command = str(values.get("UninstallString") or "").strip()
    quiet_uninstall_command = str(values.get("QuietUninstallString") or "").strip()
    install_location = _infer_software_install_location(name, publisher, values, skip_disk_scan=skip_disk_scan)
    size_bytes = _estimated_size_bytes(values.get("EstimatedSize"))
    system_component = _registry_int(values.get("SystemComponent")) == 1
    release_type = str(values.get("ReleaseType") or "").strip()
    parent_key = str(values.get("ParentKeyName") or "").strip()
    windows_path = _path_under_system_root(install_location)
    no_standard_uninstall = not uninstall_command and not quiet_uninstall_command
    publisher_lower = publisher.lower()
    microsoft_system = publisher_lower == "microsoft corporation" and (system_component or release_type or parent_key or windows_path or no_standard_uninstall)
    is_system = root_name == "HKLM" and bool(windows_path or microsoft_system or ((release_type or parent_key) and publisher_lower == "microsoft corporation"))
    software_type = "系统" if is_system else "用户"
    data_location = "" if skip_disk_scan else _infer_software_data_location(name, publisher, install_location)
    reason = "系统组件或系统级软件，不允许勾选卸载" if is_system else "用户软件，可使用标准卸载；强力卸载前请确认路径"
    stable_id = hashlib.md5(f"{root_name}|{registry_view}|{registry_path}|{name}".lower().encode()).hexdigest()[:14]
    return SoftwareEntry(
        id=f"soft_{stable_id}",
        name=name,
        version=version,
        publisher=publisher,
        size_bytes=size_bytes,
        install_location=install_location,
        data_location=data_location,
        uninstall_command=uninstall_command,
        quiet_uninstall_command=quiet_uninstall_command,
        registry_root=root_name,
        registry_path=registry_path,
        registry_view=registry_view,
        software_type=software_type,
        is_system=is_system,
        reason=reason,
    )


def _standard_uninstall_entry(entry: SoftwareEntry) -> SoftwareUninstallItemResult:
    command = entry.uninstall_command or entry.quiet_uninstall_command
    if not command:
        return SoftwareUninstallItemResult(entry.id, entry.name, "standard", "Rejected", "未找到标准卸载命令")
    command_path = _path_from_registry_command(command)
    if command_path and not Path(command_path).exists():
        return SoftwareUninstallItemResult(entry.id, entry.name, "standard", "Rejected", f"卸载器不存在: {command_path}")
    try:
        subprocess.Popen(command, shell=True)
    except PermissionError as exc:
        return SoftwareUninstallItemResult(entry.id, entry.name, "standard", "Failed", f"权限不足，无法启动卸载器: {exc}")
    except FileNotFoundError as exc:
        return SoftwareUninstallItemResult(entry.id, entry.name, "standard", "Failed", f"卸载器不存在: {exc}")
    except Exception as exc:
        return SoftwareUninstallItemResult(entry.id, entry.name, "standard", "Failed", str(exc))
    return SoftwareUninstallItemResult(entry.id, entry.name, "standard", "Started", "已启动标准卸载程序，请按卸载向导完成操作")


def _force_uninstall_entry(entry: SoftwareEntry) -> SoftwareUninstallItemResult:
    errors: list[str] = []
    removed_paths: list[str] = []
    for path_text in software_entry_force_paths(entry):
        path = Path(path_text)
        if not _software_force_path_allowed(path):
            errors.append(f"跳过受保护路径: {path_text}")
            continue
        before_exists = path.exists()
        if not before_exists:
            continue
        _remove_self(path, safe_age_hours=0, errors=errors, cancel=None, delete_mode="recycle")
        if not path.exists():
            removed_paths.append(str(path))
    registry_deleted = _delete_uninstall_registry_key(entry)
    if registry_deleted:
        message = "已将可确认路径移入回收站并清理卸载注册表项"
    else:
        message = "已将可确认路径移入回收站；注册表项可能需要管理员权限或已不存在"
    if errors:
        return SoftwareUninstallItemResult(entry.id, entry.name, "force", "Partial", message + "；" + "；".join(errors[:3]), removed_paths)
    return SoftwareUninstallItemResult(entry.id, entry.name, "force", "Success", message, removed_paths)


def software_entry_force_paths(entry: SoftwareEntry) -> list[str]:
    paths: list[str] = []
    for value in [entry.install_location, entry.data_location]:
        for part in str(value or "").split(" | "):
            text = part.strip()
            if text:
                paths.append(text)
    return _deduplicate_path_texts(paths)


def _software_force_path_allowed(path: Path) -> bool:
    if not str(path).strip() or _is_forbidden_broad_path(path) or _is_broad_software_dir(path):
        return False
    try:
        if path.is_symlink():
            return False
        if path.exists() and path.is_dir() and not _directory_has_visible_content(path):
            return False
    except OSError:
        return False
    return True


def _registry_command_has_existing_target(command: str) -> bool:
    target = _path_from_registry_command(command)
    if not target:
        return False
    try:
        return Path(target).exists()
    except OSError:
        return False


def _delete_uninstall_registry_key(entry: SoftwareEntry) -> bool:
    if os.name != "nt" or not entry.registry_root or not entry.registry_path:
        return False
    try:
        import winreg
    except ImportError:
        return False
    root = winreg.HKEY_CURRENT_USER if entry.registry_root == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    view_flag = getattr(winreg, "KEY_WOW64_32KEY", 0) if entry.registry_view == "machine32" else getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        winreg.DeleteKeyEx(root, entry.registry_path, view_flag, 0)
        return True
    except Exception:
        try:
            winreg.DeleteKey(root, entry.registry_path)
            return True
        except Exception:
            return False


def _estimated_size_bytes(value: object) -> int:
    try:
        return max(0, int(value or 0)) * 1024
    except (TypeError, ValueError):
        return 0


def _registry_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clean_registry_path(path: str) -> str:
    text = str(path or "").strip().strip('"')
    return os.path.expandvars(text)


def _infer_software_install_location(name: str, publisher: str, values: dict[str, object], *, skip_disk_scan: bool = False) -> str:
    candidates: list[str] = []
    direct_keys = ("InstallLocation", "InstallSource", "Inno Setup: App Path")
    command_keys = ("DisplayIcon", "UninstallString", "QuietUninstallString", "ModifyPath")
    for key in direct_keys:
        _append_unique_path(candidates, _clean_registry_path(str(values.get(key) or "")))
    for key in command_keys:
        command_path = _path_from_registry_command(str(values.get(key) or ""))
        if command_path:
            parent = _probable_install_parent(command_path)
            _append_unique_path(candidates, parent or command_path)
    if not skip_disk_scan:
        for found in _find_software_install_locations(name, publisher):
            _append_unique_path(candidates, str(found))
    existing = [_normalize_existing_display_path(path) for path in candidates if _usable_existing_software_path(path)]
    if existing:
        return " | ".join(_deduplicate_path_texts(existing)[:4])
    return ""


def _path_from_registry_command(command: str) -> str:
    text = os.path.expandvars(str(command or "")).strip()
    if not text:
        return ""
    text = text.strip()
    if text.lower().startswith("msiexec"):
        return ""
    quoted = re.match(r'^"([^"]+)"', text)
    if quoted:
        return _strip_display_icon_suffix(quoted.group(1))
    # 常见格式：C:\Path\App.exe,0 或 C:\Path\uninstall.exe /S
    match = re.match(r"^([A-Za-z]:\\.*?\\[^\\/:*?\"<>|]+\.(?:exe|msi|bat|cmd|com|ico|dll))(?:[, ]|$)", text, flags=re.IGNORECASE)
    if match:
        return _strip_display_icon_suffix(match.group(1))
    return ""


def _strip_display_icon_suffix(path: str) -> str:
    text = str(path or "").strip().strip('"')
    return re.sub(r",\s*-?\d+\s*$", "", text).strip()


def _probable_install_parent(path_text: str) -> str:
    path = Path(path_text)
    try:
        if path.exists() and path.is_dir():
            return str(path)
    except OSError:
        return ""
    suffix = path.suffix.lower()
    if suffix in {".exe", ".msi", ".bat", ".cmd", ".com", ".ico", ".dll"}:
        parent = path.parent
        lower_name = parent.name.lower()
        if lower_name in {"bin", "app", "application", "installer", "uninstall", "update"} and parent.parent != parent:
            return str(parent.parent)
        return str(parent)
    return str(path)


def _append_unique_path(paths: list[str], path_text: str) -> None:
    text = str(path_text or "").strip()
    if not text:
        return
    if _normcase(text) not in {_normcase(path) for path in paths}:
        paths.append(text)


def _usable_existing_software_path(path_text: str) -> bool:
    text = str(path_text or "").strip()
    if not text:
        return False
    path = Path(text)
    try:
        return (
            path.exists()
            and path.is_dir()
            and _directory_has_visible_content(path)
            and (not _is_broad_software_dir(path))
            and not _is_forbidden_broad_path(path)
        )
    except OSError:
        return False


def _directory_has_visible_content(path: Path) -> bool:
    try:
        with os.scandir(path) as it:
            for _entry in it:
                return True
    except OSError:
        return False
    return False


def _normalize_existing_display_path(path_text: str) -> str:
    try:
        return str(Path(path_text).resolve())
    except OSError:
        return str(Path(path_text).absolute())


def _deduplicate_text(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = _normcase(text)
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _deduplicate_path_texts(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = _path_dedupe_key(text)
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _path_dedupe_key(path_text: str) -> str:
    text = os.path.expandvars(str(path_text or "").strip())
    if not text:
        return ""
    path = Path(text)
    try:
        return _normcase(str(path.resolve(strict=False)))
    except (OSError, RuntimeError):
        return _normcase(str(path.absolute()))


def _path_under_system_root(path: str) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    try:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        return _normcase(str(Path(text).resolve())).startswith(_normcase(str(system_root)).rstrip("\\/") + "\\")
    except OSError:
        return False


def _find_software_install_locations(name: str, publisher: str = "") -> list[Path]:
    tokens = _software_name_tokens(name)
    matches: list[Path] = []
    seen: set[str] = set()
    for root in _software_install_search_roots():
        for candidate in _matching_child_dirs(root, tokens, max_children=800):
            key = _normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            matches.append(candidate)
            if len(matches) >= 4:
                return matches
    return matches


def _software_install_search_roots() -> list[Path]:
    global _SOFTWARE_INSTALL_ROOT_CACHE
    if _SOFTWARE_INSTALL_ROOT_CACHE is not None:
        return list(_SOFTWARE_INSTALL_ROOT_CACHE)
    roots: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(env_name, "")
        if not value:
            continue
        base = Path(value)
        _append_unique_path_obj(roots, base)
        if env_name in {"LOCALAPPDATA", "APPDATA"}:
            _append_unique_path_obj(roots, base / "Programs")
    user_profile = os.environ.get("USERPROFILE", "")
    if user_profile:
        _append_unique_path_obj(roots, Path(user_profile) / "AppData" / "Local" / "Programs")
    for drive in _windows_drive_roots():
        drive_root = Path(drive)
        for child in ("Program Files", "Program Files (x86)", "Programs", "Apps"):
            _append_unique_path_obj(roots, drive_root / child)
        users_dir = drive_root / "Users"
        try:
            for user_dir in users_dir.iterdir():
                _append_unique_path_obj(roots, user_dir / "AppData" / "Local" / "Programs")
        except OSError:
            pass
    _SOFTWARE_INSTALL_ROOT_CACHE = [path for path in roots if _path_is_existing_dir(path)]
    return list(_SOFTWARE_INSTALL_ROOT_CACHE)


def _infer_software_data_location(name: str, publisher: str = "", install_location: str = "") -> str:
    candidates: list[Path] = []
    tokens = _software_name_tokens(name)
    for base_name in ("LOCALAPPDATA", "APPDATA", "PROGRAMDATA"):
        base = os.environ.get(base_name, "")
        if not base:
            continue
        base_path = Path(base)
        for token in tokens:
            candidates.append(base_path / token)
        for matched in _matching_child_dirs(base_path, tokens, max_children=1000):
            candidates.append(matched)
    for install_path in _split_path_list(install_location):
        install_name = Path(install_path).name
        for base in _software_data_search_roots():
            candidates.append(base / install_name)
            for matched in _matching_child_dirs(base, [install_name], max_children=1000):
                candidates.append(matched)
    existing: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            if (
                not path.exists()
                or not path.is_dir()
                or not _directory_has_visible_content(path)
                or _is_broad_software_dir(path)
                or _is_forbidden_broad_path(path)
            ):
                continue
        except OSError:
            continue
        key = _path_dedupe_key(str(path))
        if key in seen:
            continue
        seen.add(key)
        existing.append(str(path))
    return " | ".join(existing[:4])


def _software_data_search_roots() -> list[Path]:
    global _SOFTWARE_DATA_ROOT_CACHE
    if _SOFTWARE_DATA_ROOT_CACHE is not None:
        return list(_SOFTWARE_DATA_ROOT_CACHE)
    roots: list[Path] = []
    for env_name in ("LOCALAPPDATA", "APPDATA", "PROGRAMDATA"):
        value = os.environ.get(env_name, "")
        if value:
            _append_unique_path_obj(roots, Path(value))
    _SOFTWARE_DATA_ROOT_CACHE = [path for path in roots if _path_is_existing_dir(path)]
    return list(_SOFTWARE_DATA_ROOT_CACHE)


def _matching_child_dirs(root: Path, tokens: list[str], *, max_children: int) -> list[Path]:
    if not tokens or not _path_is_existing_dir(root):
        return []
    normalized_tokens = [_normalize_software_token(token) for token in tokens if _normalize_software_token(token)]
    if not normalized_tokens:
        return []
    return [
        child
        for child in _indexed_child_dirs(root, max_children=max_children)
        if _software_token_matches(_normalize_software_token(child.name), normalized_tokens)
    ]


def _indexed_child_dirs(root: Path, *, max_children: int) -> list[Path]:
    key = f"{_normcase(str(root))}|{int(max_children)}"
    cached = _SOFTWARE_DIR_INDEX_CACHE.get(key)
    if cached is not None:
        return list(cached)
    dirs: list[Path] = []
    seen: set[str] = set()
    try:
        children = list(root.iterdir())
    except OSError:
        _SOFTWARE_DIR_INDEX_CACHE[key] = []
        return []
    children_seen = 0
    for child in children:
        if children_seen >= max_children:
            break
        children_seen += 1
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except OSError:
            continue
        _append_indexed_dir(dirs, seen, child)
        try:
            nested = list(child.iterdir())[:160]
        except OSError:
            nested = []
        for nested_child in nested:
            try:
                if nested_child.is_dir() and not nested_child.is_symlink():
                    _append_indexed_dir(dirs, seen, nested_child)
            except OSError:
                continue
    _SOFTWARE_DIR_INDEX_CACHE[key] = dirs
    return list(dirs)


def _append_indexed_dir(dirs: list[Path], seen: set[str], path: Path) -> None:
    key = _normcase(str(path))
    if key in seen:
        return
    seen.add(key)
    dirs.append(path)


def _software_token_matches(value: str, tokens: list[str]) -> bool:
    if not value:
        return False
    for token in tokens:
        if not token:
            continue
        if value == token:
            return True
        if len(token) >= 4 and token in value:
            return True
        if len(value) >= 4 and value in token:
            return True
    return False


def _normalize_software_token(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").lower())


def _append_unique_path_obj(paths: list[Path], path: Path) -> None:
    key = _normcase(str(path))
    if key not in {_normcase(str(item)) for item in paths}:
        paths.append(path)


def _path_is_existing_dir(path: Path) -> bool:
    try:
        return path.exists() and path.is_dir() and not path.is_symlink()
    except OSError:
        return False


def _is_broad_software_dir(path: Path | str) -> bool:
    name = _normalize_software_token(Path(path).name)
    return name in {
        "appdata",
        "local",
        "roaming",
        "user",
        "users",
        "programfiles",
        "programfilesx86",
        "programs",
        "application",
        "applications",
        "bin",
        "installer",
        "install",
        "uninstall",
        "update",
        "common",
        "shared",
    }


def _split_path_list(paths_text: str) -> list[str]:
    return [part.strip() for part in str(paths_text or "").split(" | ") if part.strip()]


def _software_name_tokens(name: str, publisher: str = "") -> list[str]:
    raw_tokens = [_software_display_name_core(name), publisher]
    output: list[str] = []
    for token in raw_tokens:
        clean = re.sub(r"\s+", " ", str(token or "")).strip()
        clean = re.sub(r"\s+\d+(?:\.\d+)*$", "", clean).strip()
        if clean and clean not in output:
            output.append(clean)
        compact = clean.replace(" ", "")
        if compact and compact not in output:
            output.append(compact)
    return output[:8]


def _software_display_name_core(name: str) -> str:
    text = re.sub(r"\s*\([^)]*\)\s*", " ", str(name or ""))
    text = re.sub(r"\s+\d+(?:\.\d+)*$", "", text).strip()
    return re.sub(r"\s+", " ", text)


def _scan_one_drive(
    drive: DriveInfo,
    *,
    min_candidate_size_mb: int,
    min_large_file_size_mb: int,
    min_duplicate_size_mb: int,
    max_depth: int,
    scan_modes: set[str],
    confirm_duplicate_content_hash: bool,
    cancel: Optional[Callable[[], bool]],
    progress: Optional[Callable[[str], None]],
    item_found: Optional[Callable[[str, object], None]],
) -> DriveScanResult:
    errors: list[str] = []
    root = drive.root
    candidates: list[CleanupCandidate] = []
    large_files: list[LargeEntry] = []
    large_dirs: list[LargeEntry] = []
    space_summary = SpaceScanResult()
    duplicate_input: dict[int, list[str]] = {}
    duplicate_seen_keys: set[str] = set()

    do_cleanup = "cleanup" in scan_modes
    do_space = "space" in scan_modes
    do_large_files = "large_files" in scan_modes
    do_duplicates = "duplicates" in scan_modes
    cleanup_only = scan_modes == {"cleanup"}

    if do_cleanup:
        if progress is not None and cleanup_only:
            progress("常规清理采用快速安全扫描，优先检查临时目录和应用缓存")
        known_paths = _known_candidate_paths(root)
        for path, kind, mode, rule, risk, reason in known_paths:
            if _cancelled(cancel):
                break
            if not path.exists():
                continue
            _add_candidate(
                candidates,
                path,
                kind,
                mode,
                rule,
                risk,
                reason,
                min_candidate_size_mb,
                errors,
                cancel,
                item_found,
                max_size_files=_CLEANUP_MEASURE_MAX_FILES if cleanup_only else None,
                max_size_seconds=_CLEANUP_MEASURE_MAX_SECONDS if cleanup_only else None,
            )
        if cleanup_only:
            _scan_cleanup_discovery_roots(
                root,
                candidates=candidates,
                min_candidate_size_mb=min_candidate_size_mb,
                max_depth=max(2, min(5, int(max_depth))),
                errors=errors,
                cancel=cancel,
                progress=progress,
                item_found=item_found,
            )
            candidates = _deduplicate_candidates(candidates)
            return DriveScanResult(
                drive=drive,
                candidates=sorted(candidates, key=lambda x: (x.risk != SAFE, -x.size_bytes, x.path.lower())),
                large_dirs=_build_large_dirs(candidates)[:50],
                large_files=[],
                duplicate_groups=[],
                scan_errors=errors[:200],
            )

    if do_space:
        space_summary = _scan_top_level_dirs(
            root,
            max_depth=max_depth,
            cancel=cancel,
            progress=progress,
            item_found=item_found,
            errors=errors,
        )
        large_dirs = space_summary.entries

    if do_space and not (do_cleanup or do_large_files or do_duplicates):
        return DriveScanResult(
            drive=drive,
            candidates=[],
            large_dirs=large_dirs[:_SPACE_DIR_RESULT_LIMIT],
            large_files=[],
            duplicate_groups=[],
            scan_errors=errors[:200],
            space_total_size_bytes=space_summary.total_size_bytes,
            space_total_dirs=space_summary.total_dirs,
            space_displayed_dirs=space_summary.displayed_dirs,
            space_display_limit=space_summary.display_limit,
            space_result_truncated=space_summary.truncated,
            space_scanned_files=space_summary.scanned_files,
        )

    if do_large_files:
        large_files = _scan_large_files(
            root,
            min_size_bytes=int(min_large_file_size_mb) * _MB,
            cancel=cancel,
            progress=progress,
            item_found=item_found,
            errors=errors,
        )

    last_progress_at = 0.0

    if do_cleanup or do_duplicates:
        for entry in _walk_limited(root, max_depth=max_depth, cancel=cancel, errors=errors):
            if _cancelled(cancel):
                break
            try:
                now = time.monotonic()
                if progress is not None and now - last_progress_at >= 0.25:
                    progress(f"正在扫描: {entry.path}")
                    last_progress_at = now
                if entry.is_dir(follow_symlinks=False):
                    if do_cleanup:
                        name = entry.name.lower()
                        classified = _classify_directory(Path(entry.path), name)
                        if classified is not None:
                            kind, mode, rule, risk, reason = classified
                            _add_candidate(
                                candidates,
                                Path(entry.path),
                                kind,
                                mode,
                                rule,
                                risk,
                                reason,
                                min_candidate_size_mb,
                                errors,
                                cancel,
                                item_found,
                            )
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
                size = int(st.st_size)
                if do_duplicates and size >= int(min_duplicate_size_mb) * _MB:
                    if _should_skip_duplicate_file_path(entry.path):
                        continue
                    duplicate_key = _duplicate_file_identity(entry.path)
                    if duplicate_key in duplicate_seen_keys:
                        continue
                    duplicate_seen_keys.add(duplicate_key)
                    same_size = duplicate_input.setdefault(size, [])
                    same_size.append(entry.path)
                    if len(same_size) == 2 and item_found is not None:
                        digest = hashlib.md5(
                            "|".join(sorted(same_size)).lower().encode()
                        ).hexdigest()[:12]
                        item_found(
                            "duplicate_group_preview",
                            DuplicateGroup(
                                size,
                                f"preview:{size}:{digest}",
                                sorted(same_size),
                                False,
                                [],
                            ),
                        )
            except (OSError, PermissionError) as exc:
                if len(errors) < 500:
                    errors.append(f"{entry.path}: {exc}")

    large_files.sort(key=lambda x: x.size_bytes, reverse=True)
    if not do_space:
        large_dirs = _build_large_dirs(candidates)
    duplicates = _find_duplicates(
        duplicate_input,
        confirm_content_hash=bool(confirm_duplicate_content_hash),
        cancel=cancel,
        progress=progress,
        item_found=item_found,
    ) if do_duplicates else []
    candidates = _deduplicate_candidates(candidates)
    return DriveScanResult(
        drive=drive,
        candidates=sorted(candidates, key=lambda x: (x.risk != SAFE, -x.size_bytes, x.path.lower())),
        large_dirs=large_dirs[:_SPACE_DIR_RESULT_LIMIT] if do_space else large_dirs[:50],
        large_files=large_files[:_LARGE_FILE_RESULT_LIMIT],
        duplicate_groups=duplicates[:50],
        scan_errors=errors[:200],
        space_total_size_bytes=space_summary.total_size_bytes,
        space_total_dirs=space_summary.total_dirs,
        space_displayed_dirs=space_summary.displayed_dirs,
        space_display_limit=space_summary.display_limit,
        space_result_truncated=space_summary.truncated,
        space_scanned_files=space_summary.scanned_files,
    )


def _normalize_scan_modes(scan_modes: Optional[Iterable[str]]) -> set[str]:
    allowed = {"cleanup", "space", "large_files", "duplicates"}
    if scan_modes is None:
        return set(allowed)
    modes = {str(mode or "").strip().lower() for mode in scan_modes}
    modes = {mode for mode in modes if mode in allowed}
    return modes or set(allowed)


def _scan_top_level_dirs(
    root: str,
    *,
    max_depth: int,
    cancel: Optional[Callable[[], bool]],
    progress: Optional[Callable[[str], None]],
    item_found: Optional[Callable[[str, object], None]],
    errors: list[str],
) -> SpaceScanResult:
    root_path = Path(root)
    records: dict[str, dict[str, object]] = {}
    stack: list[tuple[str, int, list[str]]] = [(str(root_path), 0, [])]
    last_progress_at = 0.0
    files_seen = 0
    total_size_bytes = 0
    preview_emitted: set[str] = set()
    cancelled = False

    while stack:
        if _cancelled(cancel):
            cancelled = True
            break
        current_text, depth, ancestors = stack.pop()
        current_path = Path(current_text)
        try:
            if depth > 0 and (_should_skip_space_dir(current_path) or _path_is_reparse_point(current_path)):
                continue
            current_ancestors = ancestors
            if 0 < depth <= max(1, int(max_depth)):
                key = _normcase(str(current_path))
                if key not in records:
                    try:
                        mtime = current_path.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    records[key] = {
                        "path": str(current_path),
                        "size": 0,
                        "mtime": float(mtime or 0.0),
                        "depth": depth,
                    }
                    if item_found is not None and len(preview_emitted) < _SPACE_PREVIEW_LIMIT:
                        preview_emitted.add(key)
                        item_found(
                            "large_dir_preview",
                            LargeEntry(
                                path=str(current_path),
                                size_bytes=0,
                                kind="directory",
                                last_write_time=_mtime_iso(float(mtime or 0.0)),
                                type_label="目录",
                                type_description="扫描中预览：正在累计目录大小，完成后会按真实占用重新排序。",
                            ),
                        )
                current_ancestors = ancestors + [key]
            now = time.monotonic()
            if progress is not None:
                if now - last_progress_at >= _SPACE_PROGRESS_INTERVAL_SECONDS:
                    progress(f"正在统计目录体积: {current_path}")
                    last_progress_at = now
            with os.scandir(current_path) as it:
                for entry in it:
                    if _cancelled(cancel):
                        cancelled = True
                        break
                    try:
                        if entry.is_symlink() or _entry_is_reparse_point(entry):
                            continue
                        if entry.is_file(follow_symlinks=False):
                            size = int(entry.stat(follow_symlinks=False).st_size)
                            files_seen += 1
                            total_size_bytes += size
                            for key in current_ancestors:
                                records[key]["size"] = int(records[key].get("size", 0) or 0) + size
                        elif entry.is_dir(follow_symlinks=False):
                            child_path = Path(entry.path)
                            if _should_skip_space_dir(child_path):
                                continue
                            stack.append((entry.path, depth + 1, current_ancestors))
                    except OSError as exc:
                        if len(errors) < 500:
                            errors.append(f"{entry.path}: {exc}")
        except OSError as exc:
            if len(errors) < 500:
                errors.append(f"{current_path}: {exc}")

    entries: list[LargeEntry] = []
    for record in records.values():
        description = (
            f"部分统计：扫描已取消，大小为取消前累计值，已扫描约 {files_seen} 个文件。"
            if cancelled
            else f"完整统计：单次遍历累计可访问文件大小，已扫描约 {files_seen} 个文件。"
        )
        entries.append(
            LargeEntry(
                path=str(record.get("path", "")),
                size_bytes=int(record.get("size", 0) or 0),
                kind="directory",
                last_write_time=_mtime_iso(float(record.get("mtime", 0.0) or 0.0)),
                type_label="目录",
                type_description=description,
            )
        )
    if progress is not None:
        progress("正在整理空间分析结果")
    total_dirs = len(entries)
    truncated = total_dirs > _SPACE_DIR_RESULT_LIMIT
    if len(entries) > _SPACE_DIR_RESULT_LIMIT:
        entries = heapq.nlargest(_SPACE_DIR_RESULT_LIMIT, entries, key=lambda x: x.size_bytes)
    entries.sort(key=lambda x: x.size_bytes, reverse=True)
    return SpaceScanResult(
        entries=entries,
        total_size_bytes=total_size_bytes,
        total_dirs=total_dirs,
        displayed_dirs=len(entries),
        display_limit=_SPACE_DIR_RESULT_LIMIT,
        truncated=truncated,
        scanned_files=files_seen,
    )


def _scan_cleanup_discovery_roots(
    root: str,
    *,
    candidates: list[CleanupCandidate],
    min_candidate_size_mb: int,
    max_depth: int,
    errors: list[str],
    cancel: Optional[Callable[[], bool]],
    progress: Optional[Callable[[str], None]],
    item_found: Optional[Callable[[str, object], None]],
) -> None:
    for scan_root, scan_depth in _cleanup_discovery_roots(root, max_depth=max_depth):
        if _cancelled(cancel):
            return
        if not scan_root.exists() or not scan_root.is_dir():
            continue
        if progress is not None:
            progress(f"正在检查常规清理入口: {scan_root}")
        last_progress_at = 0.0
        for entry in _walk_limited(str(scan_root), max_depth=scan_depth, cancel=cancel, errors=errors):
            if _cancelled(cancel):
                return
            try:
                now = time.monotonic()
                if progress is not None and now - last_progress_at >= 0.75:
                    progress(f"正在扫描: {entry.path}")
                    last_progress_at = now
                if not entry.is_dir(follow_symlinks=False):
                    continue
                classified = _classify_directory(Path(entry.path), entry.name.lower())
                if classified is None:
                    continue
                kind, mode, rule, risk, reason = classified
                _add_candidate(
                    candidates,
                    Path(entry.path),
                    kind,
                    mode,
                    rule,
                    risk,
                    reason,
                    min_candidate_size_mb,
                    errors,
                    cancel,
                    item_found,
                    max_size_files=_CLEANUP_MEASURE_MAX_FILES,
                    max_size_seconds=_CLEANUP_MEASURE_MAX_SECONDS,
                )
            except (OSError, PermissionError) as exc:
                if len(errors) < 500:
                    errors.append(f"{entry.path}: {exc}")


def _cleanup_discovery_roots(root: str, *, max_depth: int) -> list[tuple[Path, int]]:
    root_path = Path(root)
    if not _is_drive_root_path(root_path):
        return [(root_path, int(max_depth))]
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    programdata = Path(os.environ.get("PROGRAMDATA", ""))
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    raw_paths: list[tuple[Path, int]] = [
        (local / "Google" / "Chrome" / "User Data", 2),
        (local / "Microsoft" / "Edge" / "User Data", 2),
        (local / "BraveSoftware" / "Brave-Browser" / "User Data", 2),
        (local / "Chromium" / "User Data", 2),
        (local / "Vivaldi" / "User Data", 2),
        (local / "Mozilla" / "Firefox" / "Profiles", 2),
        (roaming / "Mozilla" / "Firefox" / "Profiles", 2),
        (roaming / "Code" / "User", 1),
        (roaming / "Code - Insiders" / "User", 1),
        (local, 1),
        (roaming, 1),
        (Path(os.environ.get("TEMP", "")), 2),
        (Path(os.environ.get("TMP", "")), 2),
        (system_root / "Temp", 2),
        (system_root / "Minidump", 1),
        (system_root / "LiveKernelReports", 1),
        (local / "CrashDumps", 1),
        (local / "Microsoft" / "Windows" / "WER", 2),
        (local / "D3DSCache", 1),
        (programdata / "Microsoft" / "Windows" / "WER", 2),
    ]
    if _is_non_system_drive_root(root_path):
        raw_paths.extend(
            [
                (root_path / "Temp", 4),
                (root_path / "TMP", 4),
                (root_path / "Cache", 4),
                (root_path / "Caches", 4),
                (root_path / "Download", 3),
                (root_path / "Downloads", 3),
                (root_path / "Logs", 3),
                (root_path / "Log", 3),
                (root_path / "Backup", 2),
                (root_path / "Backups", 2),
                (root_path, 1),
            ]
        )
    roots: list[tuple[Path, int]] = []
    seen: set[str] = set()
    for path, depth in raw_paths:
        if not str(path).strip():
            continue
        if not _is_inside_root(path, root_path):
            continue
        key = _normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        roots.append((path, max(1, min(int(depth), int(max_depth)))))
    return roots


def _is_non_system_drive_root(root: Path) -> bool:
    if not _is_drive_root_path(root):
        return False
    try:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        resolved = root.resolve()
    except OSError:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).absolute()
        resolved = root.absolute()
    root_drive = _normcase(str(Path(resolved.anchor))).rstrip("\\/")
    system_drive = _normcase(str(Path(system_root.anchor))).rstrip("\\/")
    return bool(root_drive) and root_drive != system_drive


def _windows_drive_roots() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import ctypes

        bitmask = int(ctypes.windll.kernel32.GetLogicalDrives())
    except Exception:
        return [f"{chr(c)}:\\" for c in range(ord("A"), ord("Z") + 1) if Path(f"{chr(c)}:\\").exists()]
    roots: list[str] = []
    for i in range(26):
        if bitmask & (1 << i):
            roots.append(f"{chr(ord('A') + i)}:\\")
    return roots


def _drive_type(root: str) -> str:
    if os.name != "nt":
        return "fixed"
    try:
        import ctypes

        code = int(ctypes.windll.kernel32.GetDriveTypeW(str(root)))
    except Exception:
        return "unknown"
    return {
        2: "removable",
        3: "fixed",
        4: "network",
        5: "cdrom",
        6: "ramdisk",
    }.get(code, "unknown")


def _known_candidate_paths(root: str) -> list[tuple[Path, str, str, str, str, str]]:
    items: list[tuple[Path, str, str, str, str, str]] = []
    root_path = Path(root)
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    programdata = Path(os.environ.get("PROGRAMDATA", ""))
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    for path, kind, mode, rule, risk, reason in [
        (Path(os.environ.get("TEMP", "")), "user_temp", CONTENTS, "user_temp_windows", SAFE, "用户临时目录，通常可重新生成"),
        (Path(os.environ.get("TMP", "")), "user_temp", CONTENTS, "user_temp_windows", SAFE, "用户临时目录，通常可重新生成"),
        (system_root / "Temp", "system_temp", CONTENTS, "system_temp_windows", SAFE, "系统临时目录，需要权限时会自动跳过"),
        (system_root / "SoftwareDistribution" / "Download", "windows_update_download", CONTENTS, "windows_update_download", CONFIRM_REQUIRED, "Windows 更新下载缓存，建议确认更新状态后清理"),
        (system_root / "ServiceProfiles" / "NetworkService" / "AppData" / "Local" / "Microsoft" / "Windows" / "DeliveryOptimization" / "Cache", "windows_update_download", CONTENTS, "delivery_optimization_cache", CONFIRM_REQUIRED, "传递优化缓存，系统会按需重新获取"),
        (root_path / "Windows.old", "windows_old", SELF, "windows_old", CONFIRM_REQUIRED, "旧系统备份，确认不再需要回滚后可整体删除（通常可释放数十 GB）"),
        (system_root / "Logs", "system_logs", CONTENTS, "windows_logs", CONFIRM_REQUIRED, "Windows 系统日志（CBS/更新等），近期排查系统问题时建议保留"),
        # 崩溃转储与错误报告：纯诊断数据，删除不影响任何功能
        (local / "CrashDumps", "crash_dump", CONTENTS, "crash_dumps", SAFE, "应用崩溃转储，仅用于诊断"),
        (system_root / "Minidump", "crash_dump", CONTENTS, "crash_dumps", SAFE, "系统蓝屏小转储，仅用于诊断"),
        (system_root / "MEMORY.DMP", "crash_dump", SELF, "crash_dumps", SAFE, "系统蓝屏内存转储文件，仅用于诊断"),
        (system_root / "LiveKernelReports", "crash_dump", CONTENTS, "crash_dumps", CONFIRM_REQUIRED, "内核实时报告（诊断转储），确认无排查需求后清理"),
        (local / "Microsoft" / "Windows" / "WER" / "ReportQueue", "error_reports", CONTENTS, "wer_reports", SAFE, "Windows 错误报告队列，仅用于诊断"),
        (local / "Microsoft" / "Windows" / "WER" / "ReportArchive", "error_reports", CONTENTS, "wer_reports", SAFE, "Windows 错误报告存档，仅用于诊断"),
        (programdata / "Microsoft" / "Windows" / "WER" / "ReportQueue", "error_reports", CONTENTS, "wer_reports", SAFE, "系统级 Windows 错误报告队列，仅用于诊断"),
        (programdata / "Microsoft" / "Windows" / "WER" / "ReportArchive", "error_reports", CONTENTS, "wer_reports", SAFE, "系统级 Windows 错误报告存档，仅用于诊断"),
        # GPU/着色器与系统缓存：首次使用会自动重建
        (local / "D3DSCache", "gpu_cache", SELF, "gpu_shader_cache", SAFE, "DirectX 着色器缓存，系统按需自动重建"),
        (local / "NVIDIA Corporation" / "NV_Cache", "gpu_cache", SELF, "gpu_shader_cache", SAFE, "NVIDIA 着色器缓存，自动重建"),
        (local / "NVIDIA" / "GLCache", "gpu_cache", SELF, "gpu_shader_cache", SAFE, "NVIDIA OpenGL/Vulkan 着色器缓存，自动重建"),
        (local / "AMD" / "DxCache", "gpu_cache", SELF, "gpu_shader_cache", SAFE, "AMD DirectX 着色器缓存，自动重建"),
        (local / "AMD" / "GlCache", "gpu_cache", SELF, "gpu_shader_cache", SAFE, "AMD OpenGL 着色器缓存，自动重建"),
        (local / "AMD" / "VkCache", "gpu_cache", SELF, "gpu_shader_cache", SAFE, "AMD Vulkan 着色器缓存，自动重建"),
        (local / "Intel" / "ShaderCache", "gpu_cache", SELF, "gpu_shader_cache", SAFE, "Intel 着色器缓存，自动重建"),
        (local / "Microsoft" / "Windows" / "Explorer", "thumbnail_cache", CONTENTS, "thumbnail_cache", SAFE, "缩略图与图标缓存，系统自动重建；被占用的文件会自动跳过"),
        (local / "Microsoft" / "Windows" / "INetCache", "system_web_cache", CONTENTS, "inet_cache", SAFE, "系统 Web 缓存（WinINet），可安全重建"),
        # 开发工具缓存：删除后按需重新下载/重建
        (local / "npm-cache", "dev_cache", SELF, "npm_cache", SAFE, "npm 缓存，后续安装依赖时可重新下载"),
        (local / "pip" / "cache", "dev_cache", SELF, "pip_cache", SAFE, "pip 缓存，后续安装依赖时可重新下载"),
        (home / ".bun" / "install" / "cache", "dev_cache", SELF, "bun_cache", SAFE, "Bun 安装缓存，后续可重新下载"),
        (local / "ms-playwright", "dev_cache", SELF, "playwright_cache", SAFE, "Playwright 浏览器缓存，测试需要时会重新下载"),
        (local / "Nuitka" / "Nuitka" / "Cache", "dev_cache", SELF, "nuitka_cache", SAFE, "Nuitka 编译缓存，可重新生成"),
        (local / "go-build", "dev_cache", SELF, "go_build_cache", SAFE, "Go 构建缓存，编译时自动重建"),
        (home / ".gradle" / "caches", "dev_cache", SELF, "gradle_cache", SAFE, "Gradle 构建缓存，构建时自动重建"),
        (local / "Sun" / "Java" / "Deployment" / "cache", "dev_cache", SELF, "java_deployment_cache", SAFE, "Java 部署缓存，可重新下载"),
        (home / ".cache", "dev_cache", CONTENTS, "generic_user_dev_cache", CONFIRM_REQUIRED, "通用开发缓存（可能包含依赖、模型等大文件），确认不需要后清理"),
        (home / "Downloads", "downloads", CONTENTS, "downloads_folder", CONFIRM_REQUIRED, "下载目录可能包含用户文件"),
        (roaming / "Code" / "User" / "workspaceStorage", "ide_history", CONTENTS, "vscode_workspace_storage", CONFIRM_REQUIRED, "会影响 VS Code 工作区状态和历史"),
        (roaming / "Code" / "User" / "History", "ide_history", CONTENTS, "vscode_history", CONFIRM_REQUIRED, "会删除 VS Code 本地历史"),
    ]:
        if str(path).strip() and _is_inside_root(path, root_path):
            items.append((path, kind, mode, rule, risk, reason))
    items.extend(_external_rule_candidate_paths(root_path))
    return _deduplicate_known_candidate_paths(items)


def _deduplicate_known_candidate_paths(
    items: list[tuple[Path, str, str, str, str, str]]
) -> list[tuple[Path, str, str, str, str, str]]:
    output: list[tuple[Path, str, str, str, str, str]] = []
    seen: set[str] = set()
    for item in items:
        path = item[0]
        key = _normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _external_rule_candidate_paths(root_path: Path) -> list[tuple[Path, str, str, str, str, str]]:
    rules_dir = Path(__file__).resolve().parent / "rules"
    if not rules_dir.exists():
        return []
    items: list[tuple[Path, str, str, str, str, str]] = []
    seen: set[str] = set()
    for file_path in sorted(rules_dir.glob("*.json")):
        if file_path.name in _EXTERNAL_RULE_SKIP_FILES:
            continue
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            parsed = _parse_external_rule_row(row, file_path.stem, root_path)
            if parsed is None:
                continue
            path, kind, mode, rule, risk, reason = parsed
            key = _normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            items.append((path, kind, mode, rule, risk, reason))
    return items


def _parse_external_rule_row(row: object, source_name: str, root_path: Path) -> Optional[tuple[Path, str, str, str, str, str]]:
    if not isinstance(row, list) or len(row) < 3:
        return None
    title = str(row[0] or "").strip()
    raw_path = str(row[1] or "").strip()
    raw_type = str(row[2] or "").strip().lower()
    if not raw_path or raw_type not in {"dir", "file"}:
        return None
    expanded = _expand_rule_path(raw_path)
    if not expanded or "%" in expanded:
        return None
    path = Path(expanded)
    if not _is_inside_root(path, root_path):
        return None
    if _is_forbidden_broad_path(path):
        risk = AVOID
    else:
        risk = _external_rule_risk(title, expanded)
    kind = _external_rule_kind(title, expanded, raw_type, risk)
    mode = SELF if raw_type == "file" else CONTENTS
    description = str(row[4] or "").strip() if len(row) > 4 else ""
    reason = description or f"来自规则集 {source_name}: {title or raw_path}"
    rule_hash = hashlib.md5(f"{source_name}|{raw_path}".lower().encode()).hexdigest()[:10]
    rule = f"{_EXTERNAL_RULE_PREFIX}{source_name}:{rule_hash}"
    return (path, kind, mode, rule, risk, reason)


def _expand_rule_path(raw_path: str) -> str:
    aliases = {
        "appdata": os.environ.get("APPDATA", ""),
        "localappdata": os.environ.get("LOCALAPPDATA", ""),
        "userprofile": os.environ.get("USERPROFILE", str(Path.home())),
        "windir": os.environ.get("WINDIR", os.environ.get("SystemRoot", r"C:\Windows")),
        "systemroot": os.environ.get("SystemRoot", os.environ.get("WINDIR", r"C:\Windows")),
        "commonappdata": os.environ.get("PROGRAMDATA", ""),
        "programdata": os.environ.get("PROGRAMDATA", ""),
        "temp": os.environ.get("TEMP", ""),
        "tmp": os.environ.get("TMP", os.environ.get("TEMP", "")),
        "documents": str(Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"),
    }
    env_lookup = {key.lower(): value for key, value in os.environ.items()}
    env_lookup.update({key: value for key, value in aliases.items() if value})

    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip().lower()
        return str(env_lookup.get(name, match.group(0)))

    return re.sub(r"%([^%]+)%", replace, str(raw_path)).strip()


def _external_rule_risk(title: str, path: str) -> str:
    text = f"{title} {path}".lower()
    if any(token in text for token in _EXTERNAL_RULE_CONFIRM_TOKENS):
        return CONFIRM_REQUIRED
    if any(token in text for token in _EXTERNAL_RULE_SAFE_TOKENS):
        return SAFE
    return CONFIRM_REQUIRED


def _external_rule_kind(title: str, path: str, raw_type: str, risk: str) -> str:
    text = f"{title} {path}".lower()
    if "backup" in text or "备份" in text:
        return "external_backup"
    if "history" in text or "workspace" in text or "历史" in text:
        return "external_history"
    if "crash" in text or "dump" in text or "崩溃" in text:
        return "external_crash"
    if "log" in text or "日志" in text:
        return "external_log"
    if "temp" in text or "tmp" in text or "临时" in text:
        return "external_temp"
    if "cache" in text or "缓存" in text:
        return "external_cache"
    if raw_type == "file" and risk == SAFE:
        return "external_file_cache"
    return "external_rule"


def _classify_directory(path: Path, lower_name: str) -> Optional[tuple[str, str, str, str, str]]:
    normalized = str(path).lower().replace("/", "\\")
    if _is_forbidden_broad_path(path) or "\\.git" in normalized or normalized.endswith("\\.git") or _is_under_git_repo(path):
        return ("protected", CONTENTS, "protected_path", AVOID, "系统目录、源码仓库或配置主体不建议清理")
    if lower_name in {"cache", "code cache", "cachestorage", ".cache"}:
        if "user data" in normalized or "chrom" in normalized or "edge" in normalized:
            return ("browser_cache", SELF, "browser_cache", SAFE, "浏览器缓存，关闭浏览器后通常可安全重建")
        return ("generic_cache", SELF, "generic_cache", CONFIRM_REQUIRED, "通用缓存目录，需要确认所属应用")
    if lower_name in {"gpucache", "shadercache", "grshadercache", "gpcache", "d3dscache", "dxcache", "glcache", "vkcache", "nv_cache"}:
        if "user data" in normalized or "chrom" in normalized or "edge" in normalized:
            return ("browser_cache", SELF, "browser_cache", SAFE, "浏览器 GPU 缓存，关闭浏览器后通常可安全重建")
        return ("gpu_cache", SELF, "gpu_shader_cache", SAFE, "GPU/着色器缓存，驱动按需自动重建")
    if lower_name in {"crashpad", "crashes", "crash reports", "crashdumps", "crash dumps", "minidump", "minidumps"}:
        return ("crash_dump", SELF, "crash_dumps", SAFE, "崩溃转储缓存，通常仅用于诊断")
    if lower_name in {"reportqueue", "reportarchive"}:
        return ("error_reports", CONTENTS, "wer_reports", SAFE, "Windows 错误报告队列/存档，仅用于诊断")
    if lower_name in {"temp", "tmp"}:
        return ("temp", CONTENTS, "named_temp_dir", SAFE, "临时目录，通常可重新生成")
    if lower_name in {"log", "logs"}:
        return ("logs", CONTENTS, "logs_dir", SAFE, "日志目录，清理后可能影响问题追踪")
    if lower_name in {"download", "downloads"}:
        return ("downloads", CONTENTS, "downloads_named_dir", CONFIRM_REQUIRED, "下载目录可能包含用户需要保留的文件")
    if lower_name in {"backup", "backups"}:
        return ("backup", CONTENTS, "backup_dir", CONFIRM_REQUIRED, "备份目录可能用于恢复")
    if lower_name in {"sessions", "conversations", "history", "workspaceStorage".lower()}:
        return ("history", CONTENTS, "history_dir", CONFIRM_REQUIRED, "可能包含会话、历史或工作区状态")
    return None


def _add_candidate(
    candidates: list[CleanupCandidate],
    path: Path,
    kind: str,
    mode: str,
    rule: str,
    risk: str,
    reason: str,
    min_candidate_size_mb: int,
    errors: list[str],
    cancel: Optional[Callable[[], bool]],
    item_found: Optional[Callable[[str, object], None]],
    max_size_files: Optional[int] = None,
    max_size_seconds: Optional[float] = None,
) -> None:
    try:
        size = measure_path_size(
            path,
            cancel=cancel,
            max_files=max_size_files,
            max_seconds=max_size_seconds,
        )
        if size < int(min_candidate_size_mb) * _MB and risk != AVOID:
            return
        mtime = path.stat().st_mtime
        allowed = risk == SAFE and _candidate_allowed_by_cleaner(path, kind, mode, rule)
        stable_id = f"cand_{hashlib.md5(str(path).lower().encode()).hexdigest()[:12]}"
        candidate = CleanupCandidate(
            id=stable_id,
            path=str(path),
            size_bytes=size,
            kind=kind,
            risk=risk,
            cleanup_mode=mode,
            source_rule=rule,
            last_write_time=_mtime_iso(mtime),
            allowed_by_cleaner=allowed,
            reason=reason,
            priority=_priority(kind, risk, size),
        )
        candidates.append(candidate)
        if item_found is not None:
            item_found("candidate", candidate)
    except (OSError, PermissionError) as exc:
        _log_error(errors, f"{path}: {exc}")


def measure_path_size(
    path: Path | str,
    *,
    cancel: Optional[Callable[[], bool]] = None,
    max_files: Optional[int] = None,
    max_seconds: Optional[float] = None,
    max_depth: Optional[int] = None,
) -> int:
    size, _truncated, _files_seen = _measure_path_size_with_budget(
        path,
        cancel=cancel,
        max_files=max_files,
        max_seconds=max_seconds,
        max_depth=max_depth,
    )
    return size


def _measure_path_size_with_budget(
    path: Path | str,
    *,
    cancel: Optional[Callable[[], bool]] = None,
    max_files: Optional[int] = None,
    max_seconds: Optional[float] = None,
    max_depth: Optional[int] = None,
) -> tuple[int, bool, int]:
    p = Path(path)
    try:
        if p.is_file() and not p.is_symlink():
            return int(p.stat().st_size), False, 1
    except OSError:
        return 0, False, 0
    total = 0
    files_seen = 0
    truncated = False
    started_at = time.monotonic()
    stack: list[tuple[str | Path, int]] = [(p, 0)]
    while stack:
        if _cancelled(cancel):
            truncated = True
            break
        if max_seconds is not None and time.monotonic() - started_at >= float(max_seconds):
            truncated = True
            break
        current, depth = stack.pop()
        if max_depth is not None and depth > int(max_depth):
            truncated = True
            continue
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if _cancelled(cancel):
                        truncated = True
                        break
                    if max_seconds is not None and time.monotonic() - started_at >= float(max_seconds):
                        truncated = True
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            total += int(entry.stat(follow_symlinks=False).st_size)
                            files_seen += 1
                            if max_files is not None and files_seen >= int(max_files):
                                return total, True, files_seen
                        elif entry.is_dir(follow_symlinks=False):
                            if max_depth is None or depth < int(max_depth):
                                stack.append((entry.path, depth + 1))
                            else:
                                truncated = True
                    except OSError:
                        continue
        except OSError:
            continue
    return total, truncated, files_seen


def _make_large_file_entry(path: str, size: int, st: os.stat_result) -> LargeEntry:
    type_label, type_description = _large_file_type_info(path)
    return LargeEntry(
        path,
        int(size),
        "large_file",
        _mtime_iso(st.st_mtime),
        _mtime_iso(getattr(st, "st_atime", st.st_mtime)),
        Path(path).suffix.lower(),
        type_label,
        type_description,
        _large_file_access_basis(st),
    )


def _should_skip_large_file_dir(path: Path) -> bool:
    normalized = str(path).lower().replace("/", "\\").rstrip("\\/")
    skip_tokens = [
        "\\documents and settings",
        "\\local settings",
        "\\application data",
        "\\system volume information",
        "\\$recycle.bin",
    ]
    return any(token in normalized for token in skip_tokens)


def _scan_large_files(
    root: str,
    *,
    min_size_bytes: int,
    cancel: Optional[Callable[[], bool]],
    progress: Optional[Callable[[str], None]],
    item_found: Optional[Callable[[str, object], None]],
    errors: list[str],
) -> list[LargeEntry]:
    """全深度扫描大文件。大文件查找是发现型功能，不复用清理扫描的保守跳过规则。"""
    threshold = max(0, int(min_size_bytes or 0))
    stack: list[Path] = [Path(root)]
    heap: list[tuple[int, int, LargeEntry]] = []
    sequence = 0
    files_seen = 0
    last_progress_at = 0.0

    while stack:
        if _cancelled(cancel):
            break
        current = stack.pop()
        try:
            if _should_skip_large_file_dir(current) or _path_is_reparse_point(current):
                continue
        except OSError:
            continue
        now = time.monotonic()
        if progress is not None and now - last_progress_at >= _LARGE_FILE_PROGRESS_INTERVAL_SECONDS:
            progress(f"正在扫描大文件: {current}")
            last_progress_at = now
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if _cancelled(cancel):
                        break
                    try:
                        if entry.is_symlink() or _entry_is_reparse_point(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            child = Path(entry.path)
                            if not _should_skip_large_file_dir(child):
                                stack.append(child)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        st = entry.stat(follow_symlinks=False)
                        files_seen += 1
                        size = int(st.st_size)
                        if size < threshold:
                            continue
                        large_entry = _make_large_file_entry(entry.path, size, st)
                        if item_found is not None:
                            item_found("large_file", large_entry)
                        sequence += 1
                        record = (size, sequence, large_entry)
                        if len(heap) < _LARGE_FILE_RESULT_LIMIT:
                            heapq.heappush(heap, record)
                        elif size > heap[0][0]:
                            heapq.heapreplace(heap, record)
                    except (OSError, PermissionError) as exc:
                        if len(errors) < 500:
                            errors.append(f"{entry.path}: {exc}")
        except (OSError, PermissionError) as exc:
            if len(errors) < 500:
                errors.append(f"{current}: {exc}")

    if progress is not None:
        progress(f"正在整理大文件列表，已扫描约 {files_seen} 个文件")
    results = [entry for _size, _sequence, entry in heap]
    results.sort(key=lambda item: (-item.size_bytes, _normcase(item.path)))
    return results


def _walk_limited(
    root: str,
    *,
    max_depth: int,
    cancel: Optional[Callable[[], bool]],
    errors: list[str],
):
    root_path = Path(root)
    stack: list[tuple[Path, int]] = [(root_path, 0)]
    while stack:
        if _cancelled(cancel):
            return
        current, depth = stack.pop()
        if depth > max_depth or _should_skip_scan_dir(current):
            continue
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if _cancelled(cancel):
                        return
                    yield entry
                    try:
                        # junction/reparse point 同样不能深入：is_symlink() 在 Windows
                        # 上对 junction 返回 False，必须额外检查 reparse 属性。
                        if (
                            entry.is_dir(follow_symlinks=False)
                            and not entry.is_symlink()
                            and not _entry_is_reparse_point(entry)
                        ):
                            stack.append((Path(entry.path), depth + 1))
                    except OSError:
                        continue
        except (OSError, PermissionError) as exc:
            if len(errors) < 500:
                errors.append(f"{current}: {exc}")


def _should_skip_scan_dir(path: Path) -> bool:
    normalized = str(path).lower().rstrip("\\/")
    skip_tokens = [
        "\\documents and settings",
        "\\local settings",
        "\\application data",
        "\\windows\\winsxs",
        "\\system volume information",
        "\\$recycle.bin",
        "\\program files\\windowsapps",
        "\\node_modules",
        "\\.git",
        "\\.venv",
        "\\venv",
    ]
    return any(token in normalized for token in skip_tokens)


def _should_skip_space_dir(path: Path) -> bool:
    normalized = str(path).lower().replace("/", "\\").rstrip("\\/")
    skip_tokens = [
        "\\documents and settings",
        "\\local settings",
        "\\application data",
        "\\system volume information",
        "\\$recycle.bin",
        "\\program files\\windowsapps",
    ]
    return any(token in normalized for token in skip_tokens)


def _entry_is_reparse_point(entry: os.DirEntry) -> bool:
    if os.name != "nt":
        return False
    try:
        attrs = int(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) or 0)
    except OSError:
        return False
    return bool(attrs & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _path_is_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        attrs = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) or 0)
    except OSError:
        return False
    return bool(attrs & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _should_skip_duplicate_file_path(path: str | Path) -> bool:
    try:
        parts = [str(part).lower() for part in Path(path).parts]
    except Exception:
        return False
    legacy_parts = {"documents and settings", "local settings", "application data"}
    return any(part in legacy_parts for part in parts)


def _duplicate_file_identity(path: str | Path) -> str:
    try:
        return _normcase(os.path.realpath(str(path)))
    except OSError:
        return _normcase(str(path))


def _large_file_type_info(path: str | Path) -> tuple[str, str]:
    text = str(path or "")
    normalized = text.lower().replace("/", "\\")
    suffix = Path(text).suffix.lower()
    video_exts = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".ts", ".m4v"}
    if any(part in normalized for part in ("\\cache\\", "\\caches\\", "\\temp\\", "\\tmp\\")) and suffix in video_exts | {".m3u8", ".m4s"}:
        return ("视频缓存", "通常可清理，但请确认来源应用。")
    if suffix in {".exe", ".msi", ".dmg", ".pkg"}:
        return ("安装包", "可能是软件安装文件，可确认后归档或删除。")
    if suffix in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}:
        return ("压缩包", "可能包含备份资料，建议先预览路径。")
    if suffix in video_exts:
        return ("视频", "可能是用户视频文件，建议移动归档或打开目录确认。")
    if suffix in {".iso", ".img"}:
        return ("镜像", "可能是系统或软件镜像文件，建议确认用途后处理。")
    if suffix == ".log":
        return ("日志", "普通应用日志可清理，业务或数据库日志需确认来源。")
    return ("其它", "大文件可能是用户资料或程序数据，处理前请确认内容。")


def _large_file_access_basis(st: os.stat_result) -> str:
    try:
        atime = float(getattr(st, "st_atime", 0.0) or 0.0)
        mtime = float(getattr(st, "st_mtime", 0.0) or 0.0)
        if atime <= 0 or abs(atime - mtime) < 2:
            return "修改时间估算"
    except Exception:
        return "修改时间估算"
    return "访问时间"


def _build_large_dirs(candidates: list[CleanupCandidate]) -> list[LargeEntry]:
    entries = [
        LargeEntry(c.path, c.size_bytes, c.kind, c.last_write_time)
        for c in candidates
        if c.size_bytes > 0
    ]
    entries.sort(key=lambda x: x.size_bytes, reverse=True)
    return entries


def _find_duplicates(
    by_size: dict[int, list[str]],
    *,
    confirm_content_hash: bool = True,
    cancel: Optional[Callable[[], bool]],
    progress: Optional[Callable[[str], None]],
    item_found: Optional[Callable[[str, object], None]],
) -> list[DuplicateGroup]:
    """重复判定：大小分组，快速指纹预筛，可选完整内容哈希最终确认。"""
    groups: list[DuplicateGroup] = []
    for size, paths in by_size.items():
        if _cancelled(cancel):
            break
        if len(paths) < 2:
            continue
        # 第一阶段：头尾快速哈希，过滤掉大部分不同文件
        quick_hashes: dict[str, list[str]] = {}
        for path in paths:
            if _cancelled(cancel):
                break
            if progress is not None:
                progress(f"正在快速比对: {path}")
            digest = _quick_hash_file(path)
            if digest:
                quick_hashes.setdefault(digest, []).append(path)
            else:
                if progress is not None:
                    progress(f"哈希失败: {_hash_failure_reason(path)}  {path}")
        # 第二阶段：仅对快速哈希匹配的文件做全量哈希
        for quick_digest, same_quick in quick_hashes.items():
            if _cancelled(cancel):
                break
            if len(same_quick) < 2:
                continue
            if not confirm_content_hash:
                group = DuplicateGroup(size, f"quick:{size}:{quick_digest}", sorted(same_quick), False, [])
                groups.append(group)
                if item_found is not None:
                    item_found("duplicate_group", group)
                if progress:
                    progress(f"发现疑似重复文件组: {format_bytes(size)} x {len(same_quick)}")
                continue
            full_hashes: dict[str, list[str]] = {}
            hash_errors: list[str] = []
            for path in same_quick:
                if _cancelled(cancel):
                    break
                if progress is not None:
                    progress(f"正在计算完整哈希: {path}")
                digest = _hash_file(path, cancel=cancel)
                if digest:
                    full_hashes.setdefault(digest, []).append(path)
                else:
                    hash_errors.append(f"{path}（{_hash_failure_reason(path)}）")
            for digest, same in full_hashes.items():
                if len(same) > 1:
                    group = DuplicateGroup(size, digest, sorted(same), True, hash_errors[:8])
                    groups.append(group)
                    if item_found is not None:
                        item_found("duplicate_group", group)
                    if progress:
                        progress(f"发现重复文件组: {format_bytes(size)} x {len(same)}")
    groups.sort(key=lambda g: g.size_bytes * len(g.paths), reverse=True)
    return groups


def _quick_hash_file(path: str, sample_size: int = 8192) -> str:
    """读取文件头尾各 sample_size 字节做快速哈希，用于预过滤。"""
    try:
        with open(path, "rb") as f:
            head = f.read(sample_size)
            f.seek(0, 2)  # 跳到文件末尾
            file_size = f.tell()
            if file_size > sample_size * 2:
                f.seek(-sample_size, 2)
                tail = f.read(sample_size)
            else:
                tail = b""
        h = hashlib.md5()
        h.update(head)
        h.update(tail)
        return h.hexdigest()
    except OSError:
        return ""


def _hash_file(
    path: str,
    chunk_size: int = 1024 * 1024,
    cancel: Optional[Callable[[], bool]] = None,
) -> str:
    """计算文件完整 SHA-256 哈希，支持取消。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                if _cancelled(cancel):
                    return ""
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _hash_failure_reason(path: str) -> str:
    try:
        with open(path, "rb"):
            return "未知错误"
    except PermissionError:
        return "权限不足"
    except OSError as exc:
        text = str(exc).lower()
        if "being used" in text or "used by another process" in text or "另一个程序" in str(exc):
            return "文件被占用"
        if "not found" in text or "不存在" in str(exc):
            return "路径不存在"
        return str(exc) or "未知错误"


def _deduplicate_candidates(candidates: list[CleanupCandidate]) -> list[CleanupCandidate]:
    """去重候选项，保留路径较短（更具体）的项，保持原始稳定 ID 不变。"""
    seen: set[str] = set()
    output: list[CleanupCandidate] = []
    for candidate in sorted(candidates, key=lambda c: len(c.path)):
        key = _normcase(candidate.path)
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    # 不再重写 ID，使用基于路径哈希的稳定 ID，确保流式 UI 和最终报告一致
    return output


def _cleanup_candidate(
    candidate: CleanupCandidate,
    *,
    dry_run: bool,
    safe_age_hours: int,
    allow_confirm_required: bool = False,
    cancel: Optional[Callable[[], bool]] = None,
    detail_progress: Optional[Callable[[int, int, str], None]] = None,
    delete_mode: str = "recycle",
) -> CleanupItemResult:
    path = Path(candidate.path)
    before = measure_path_size(path)
    mode = _effective_delete_mode(candidate, delete_mode)
    can_clean = candidate.allowed_by_cleaner and candidate.risk == SAFE
    can_clean_after_confirm = bool(allow_confirm_required) and _candidate_cleanable_after_confirmation(candidate)
    if not (can_clean or can_clean_after_confirm):
        return CleanupItemResult(candidate.id, candidate.path, candidate.cleanup_mode, before, before, 0, "Rejected", 1, "未通过本地白名单", mode)
    if _is_forbidden_broad_path(path):
        return CleanupItemResult(candidate.id, candidate.path, candidate.cleanup_mode, before, before, 0, "Rejected", 1, "禁止清理宽泛或受保护路径", mode)
    if _is_protected_config_path(path):
        return CleanupItemResult(candidate.id, candidate.path, candidate.cleanup_mode, before, before, 0, "Rejected", 1, "配置、数据库或用户配置类文件默认保护", mode)
    if dry_run:
        return CleanupItemResult(candidate.id, candidate.path, candidate.cleanup_mode, before, before, 0, "DryRun", 0, "", mode)
    errors: list[str] = []
    try:
        if candidate.cleanup_mode == CONTENTS:
            _remove_children(
                path,
                safe_age_hours=safe_age_hours,
                errors=errors,
                cancel=cancel,
                detail_progress=detail_progress,
                delete_mode=mode,
            )
        elif candidate.cleanup_mode == SELF:
            _remove_self(path, safe_age_hours=safe_age_hours, errors=errors, cancel=cancel, delete_mode=mode)
        else:
            _log_error(errors, "未知清理模式")
    except OSError as exc:
        _log_error(errors, str(exc))
    after = measure_path_size(path)
    status = "Success" if not errors else ("Partial" if after < before else "Failed")
    return CleanupItemResult(
        candidate.id,
        candidate.path,
        candidate.cleanup_mode,
        before,
        after,
        max(0, before - after),
        status,
        len(errors),
        errors[0] if errors else "",
        mode,
    )


def _effective_delete_mode(candidate: CleanupCandidate, requested_mode: str) -> str:
    requested = str(requested_mode or "recycle").strip().lower()
    if requested != "permanent":
        return "recycle"
    if _candidate_can_fast_delete(candidate):
        return "permanent"
    return "recycle"


def _candidate_can_fast_delete(candidate: CleanupCandidate) -> bool:
    if str(candidate.risk) != SAFE:
        return False
    text = f"{candidate.kind} {candidate.source_rule} {candidate.reason} {candidate.path}".lower()
    if any(token in text for token in ("temp", "tmp", "cache", "缓存", "临时")):
        return True
    return False


def _is_protected_config_path(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".db", ".sqlite", ".sqlite3", ".json", ".yaml", ".yml", ".toml", ".ini"}:
        return True
    normalized = str(path).lower().replace("/", "\\")
    protected_tokens = (
        "\\user data\\default\\preferences",
        "\\user data\\default\\secure preferences",
        "\\profiles\\",
        "\\config\\",
        "\\settings\\",
    )
    return any(token in normalized for token in protected_tokens)


def _candidate_cleanable_after_confirmation(candidate: CleanupCandidate) -> bool:
    path = Path(candidate.path)
    if candidate.risk != CONFIRM_REQUIRED:
        return False
    if candidate.cleanup_mode not in {CONTENTS, SELF}:
        return False
    if _is_forbidden_broad_path(path) or _is_under_git_repo(path):
        return False
    return str(candidate.kind) not in {"backup", "external_backup"}


def _remove_children(
    path: Path,
    *,
    safe_age_hours: int,
    errors: list[str],
    cancel: Optional[Callable[[], bool]] = None,
    detail_progress: Optional[Callable[[int, int, str], None]] = None,
    delete_mode: str = "permanent",
) -> None:
    if not path.exists() or not path.is_dir() or path.is_symlink() or _path_is_reparse_point(path):
        return
    cutoff = time.time() - max(0, int(safe_age_hours)) * 3600
    try:
        children = list(path.iterdir())
    except OSError as exc:
        _log_error(errors, str(exc))
        return
    if str(delete_mode) == "recycle" and int(safe_age_hours) <= 0:
        eligible: list[Path] = []
        for child in children:
            try:
                if not child.is_symlink() and not _path_is_reparse_point(child) and child.stat().st_mtime <= cutoff:
                    eligible.append(child)
            except OSError as exc:
                _log_error(errors, f"{child}: {exc}")
        _move_paths_to_recycle_bin(eligible, errors, cancel=cancel, progress=detail_progress)
        return
    for child in children:
        if _cancelled(cancel):
            break
        _remove_path_if_old(child, cutoff, errors, cancel, delete_mode=delete_mode)


def _remove_self(
    path: Path,
    *,
    safe_age_hours: int,
    errors: list[str],
    cancel: Optional[Callable[[], bool]] = None,
    delete_mode: str = "permanent",
) -> None:
    if not path.exists() or path.is_symlink() or _path_is_reparse_point(path):
        return
    cutoff = time.time() - max(0, int(safe_age_hours)) * 3600
    _remove_path_if_old(path, cutoff, errors, cancel, delete_mode=delete_mode)


def _remove_path_if_old(
    path: Path,
    cutoff: float,
    errors: list[str],
    cancel: Optional[Callable[[], bool]] = None,
    delete_mode: str = "permanent",
) -> None:
    """按 mtime 安全删除路径。对目录递归检查子文件 mtime，避免误删含近期文件的目录。"""
    if _cancelled(cancel):
        return
    try:
        if path.is_symlink() or _path_is_reparse_point(path):
            return
        if path.is_dir():
            if str(delete_mode) == "recycle" and path.stat().st_mtime <= cutoff:
                _move_path_to_recycle_bin(path, errors)
                return
            _remove_dir_children_if_old(path, cutoff, errors, cancel)
            if _cancelled(cancel):
                return
            # 仅当目录已清空时尝试删除空目录自身
            try:
                if not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
        else:
            if path.stat().st_mtime > cutoff:
                return
            if str(delete_mode) == "recycle":
                _move_path_to_recycle_bin(path, errors)
            else:
                path.unlink(missing_ok=True)
    except OSError as exc:
        _log_error(errors, f"{path}: {exc}")


def _remove_dir_children_if_old(
    path: Path,
    cutoff: float,
    errors: list[str],
    cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """递归检查目录下每个子项的 mtime，仅删除过期文件，保留近期修改的文件。"""
    if _cancelled(cancel):
        return
    try:
        children = list(path.iterdir())
    except OSError as exc:
        _log_error(errors, f"{path}: {exc}")
        return
    for child in children:
        if _cancelled(cancel):
            break
        try:
            if child.is_symlink() or _path_is_reparse_point(child):
                continue
            if child.is_dir():
                _remove_dir_children_if_old(child, cutoff, errors, cancel)
                if _cancelled(cancel):
                    break
                # 子目录清空后删除空目录
                try:
                    if not any(child.iterdir()):
                        child.rmdir()
                except OSError:
                    pass
            else:
                if child.stat().st_mtime <= cutoff:
                    try:
                        child.unlink(missing_ok=True)
                    except PermissionError:
                        try:
                            os.chmod(str(child), stat.S_IWRITE)
                            child.unlink(missing_ok=True)
                        except Exception as exc:
                            _log_error(errors, f"{child}: {exc}")
        except OSError as exc:
            _log_error(errors, f"{child}: {exc}")


_RECYCLE_BATCH_SIZE = 64


def recycle_error_message(code: int, *, aborted: bool = False) -> str:
    error_code = int(code or 0)
    messages = {
        32: "文件正在被其他程序占用",
        120: "源路径访问被拒绝，可能是系统保护目录或程序仍在使用",
        124: "Windows 回收站接口无法处理该路径，路径可能正在变化或包含特殊目录结构",
    }
    if error_code in messages:
        return f"{messages[error_code]}（错误码 {error_code}）"
    if aborted and error_code == 0:
        return "回收站操作被系统取消"
    if error_code:
        return f"移入回收站失败（错误码 {error_code}）"
    return "移入回收站后路径仍然存在"


def _execute_recycle_batch(paths: list[Path]) -> tuple[int, bool]:
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.USHORT),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = 0x0003  # FO_DELETE
    # 不能用 path.resolve()：它会跟随 junction/symlink 返回目标路径，
    # 导致回收站操作作用于链接目标。abspath 仅做词法规范化。
    operation.pFrom = "\0".join(os.path.abspath(str(path)) for path in paths) + "\0\0"
    operation.pTo = None
    operation.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004  # FOF_ALLOWUNDO | NOCONFIRMATION | NOERRORUI | SILENT
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    return int(result), bool(operation.fAnyOperationsAborted)


def _move_paths_to_recycle_bin(
    paths: Iterable[Path],
    errors: list[str],
    *,
    cancel: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    batch_size: int = _RECYCLE_BATCH_SIZE,
) -> bool:
    valid_paths: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            _log_error(errors, f"{path}: 路径不存在")
        elif path.is_symlink():
            _log_error(errors, f"{path}: 跳过符号链接")
        elif _path_is_reparse_point(path):
            # junction 指向扫描根之外：删除链接本身安全，但绝不能跟随目标删除。
            _log_error(errors, f"{path}: 跳过 junction/reparse point")
        else:
            valid_paths.append(path)
    if not valid_paths:
        return not errors
    if os.name != "nt":
        for path in valid_paths:
            _log_error(errors, f"{path}: 当前系统不支持移动到回收站")
        return False

    total = len(valid_paths)
    processed = 0
    size = max(1, int(batch_size or _RECYCLE_BATCH_SIZE))
    for offset in range(0, total, size):
        if _cancelled(cancel):
            break
        batch = valid_paths[offset:offset + size]
        try:
            result, aborted = _execute_recycle_batch(batch)
            remaining = [path for path in batch if path.exists()]
            if result != 0 or aborted or remaining:
                message = recycle_error_message(result, aborted=aborted)
                for path in remaining:
                    _log_error(errors, f"{path}: {message}")
        except Exception as exc:
            for path in batch:
                if path.exists():
                    _log_error(errors, f"{path}: 移入回收站失败：{exc}")
        processed += len(batch)
        if progress is not None:
            progress(processed, total, str(batch[-1]))
    return not any(path.exists() for path in valid_paths)


def _move_path_to_recycle_bin(path: Path, errors: list[str]) -> bool:
    return _move_paths_to_recycle_bin([path], errors, batch_size=1)


def move_path_to_recycle_bin(path: str | Path) -> tuple[bool, str]:
    errors: list[str] = []
    ok = _move_path_to_recycle_bin(Path(path), errors)
    return ok, (errors[0] if errors else "")


# _on_remove_error 已不再使用（P1修复后 _remove_path_if_old 不再使用 shutil.rmtree）


def _candidate_allowed_by_cleaner(path: Path, kind: str, mode: str, rule: str) -> bool:
    if mode not in {CONTENTS, SELF}:
        return False
    if _is_forbidden_broad_path(path) or _is_under_git_repo(path):
        return False
    allowed_rules = {
        "user_temp_windows",
        "system_temp_windows",
        "npm_cache",
        "pip_cache",
        "bun_cache",
        "playwright_cache",
        "nuitka_cache",
        "go_build_cache",
        "gradle_cache",
        "java_deployment_cache",
        "browser_cache",
        "gpu_shader_cache",
        "crash_reports",
        "crash_dumps",
        "wer_reports",
        "thumbnail_cache",
        "inet_cache",
        "named_temp_dir",
        "logs_dir",
    }
    if str(rule).startswith(_EXTERNAL_RULE_PREFIX):
        return str(kind) in {"external_cache", "external_temp", "external_log", "external_crash", "external_file_cache"}
    return str(rule) in allowed_rules and str(kind) not in {"downloads", "backup", "history", "ide_history"}


def _is_forbidden_broad_path(path: Path | str) -> bool:
    try:
        p = Path(path).resolve()
    except OSError:
        p = Path(path).absolute()
    text = _normcase(str(p)).rstrip("\\/")
    drive = Path(text).drive
    forbidden = {
        _normcase(f"{drive}\\") if drive else "",
        _normcase(os.environ.get("SystemRoot", r"C:\Windows")),
        _normcase(str(Path.home())),
        _normcase(str(Path.home().parent)) if Path.home().parent else "",
        _normcase(f"{drive}\\Program Files") if drive else "",
        _normcase(f"{drive}\\Program Files (x86)") if drive else "",
        _normcase(f"{drive}\\System Volume Information") if drive else "",
        _normcase(f"{drive}\\Windows\\WinSxS") if drive else "",
    }
    return text in {x.rstrip("\\/") for x in forbidden if x}


_git_repo_cache: dict[str, bool] = {}


def _is_under_git_repo(path: Path | str, max_depth: int = 4) -> bool:
    """检查路径是否在 git 仓库内。限制最多向上搜索 max_depth 层，
    并使用全局缓存避免重复 I/O 检查。"""
    try:
        current = Path(path).resolve()
    except OSError:
        current = Path(path).absolute()
    if current.is_file():
        current = current.parent

    current_key = str(current)
    if current_key in _git_repo_cache:
        return _git_repo_cache[current_key]

    # 跳过已知的系统/缓存根路径，这些路径不应被 git 检测阻止清理
    known_safe_roots = set()
    for env_var in ("TEMP", "TMP", "LOCALAPPDATA", "APPDATA"):
        val = os.environ.get(env_var, "")
        if val:
            known_safe_roots.add(_normcase(val).rstrip("\\/"))
    current_norm = _normcase(str(current)).rstrip("\\/")
    for safe_root in known_safe_roots:
        if current_norm.startswith(safe_root):
            _git_repo_cache[current_key] = False
            return False

    depth = 0
    visited = []
    found_git = False
    for parent in (current, *current.parents):
        if depth >= max_depth:
            break
        parent_key = str(parent)
        if parent_key in _git_repo_cache:
            found_git = _git_repo_cache[parent_key]
            break
        visited.append(parent_key)
        if (parent / ".git").exists():
            found_git = True
            break
        depth += 1

    for p_key in visited:
        _git_repo_cache[p_key] = found_git
    _git_repo_cache[current_key] = found_git
    return found_git


def _is_inside_root(path: Path, root: Path) -> bool:
    try:
        p = _normcase(str(path.resolve()))
        r = _normcase(str(root.resolve())).rstrip("\\/") + "\\"
        return p == r.rstrip("\\/") or p.startswith(r)
    except OSError:
        return False


def _is_drive_root_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    anchor = Path(resolved.anchor)
    if not str(anchor):
        return False
    return _normcase(str(resolved)).rstrip("\\/") == _normcase(str(anchor)).rstrip("\\/")


def _normalize_drive_root(root: str) -> str:
    text = str(root or "").strip()
    if not text:
        return str(Path.home().anchor or "/")
    if os.name == "nt":
        if len(text) == 2 and text[1] == ":":
            return text + "\\"
        if len(text) == 1 and text.isalpha():
            return text.upper() + ":\\"
    return str(Path(text))


def _normcase(path: str) -> str:
    return os.path.normcase(os.path.abspath(str(path))).replace("/", "\\")


def _mtime_iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return ""


def _priority(kind: str, risk: str, size: int) -> int:
    if risk == AVOID:
        return 99
    base = {
        "user_temp": 1,
        "system_temp": 2,
        "browser_cache": 3,
        "gpu_cache": 4,
        "thumbnail_cache": 5,
        "dev_cache": 6,
        "crash_reports": 7,
        "crash_dump": 7,
        "error_reports": 8,
        "system_web_cache": 8,
        "windows_update_download": 9,
        "windows_old": 4,
        "system_logs": 12,
        "logs": 12,
    }.get(kind, 20)
    if risk == CONFIRM_REQUIRED:
        base += 30
    if size >= 5 * _GB:
        base -= 2
    elif size >= _GB:
        base -= 1
    return max(1, base)


def _build_rule_advisor_summary(results: list[DriveScanResult]) -> str:
    total_candidates = sum(len(r.candidates) for r in results)
    total_size = sum(c.size_bytes for r in results for c in r.candidates if c.risk != AVOID)
    safe_size = sum(c.size_bytes for r in results for c in r.candidates if c.risk == SAFE)
    duplicate_size = sum(g.size_bytes * (len(g.paths) - 1) for r in results for g in r.duplicate_groups)
    return (
        f"发现 {total_candidates} 个候选项，候选占用约 {format_bytes(total_size)}；"
        f"其中本地规则判定可安全清理约 {format_bytes(safe_size)}，"
        f"重复文件理论可释放约 {format_bytes(duplicate_size)}。"
    )


def _candidate_table(candidates: Iterable[CleanupCandidate]) -> list[str]:
    rows = [
        "| 风险 | 类型 | 大小 | 模式 | 路径 | 说明 |",
        "|---|---|---:|---|---|---|",
    ]
    count = 0
    for c in candidates:
        count += 1
        rows.append(f"| {c.risk} | {c.kind} | {format_bytes(c.size_bytes)} | {c.cleanup_mode} | {c.path} | {c.reason} |")
    if count == 0:
        rows.append("| 无 | - | - | - | - | - |")
    return rows


def _large_entry_table(entries: Iterable[LargeEntry]) -> list[str]:
    rows = [
        "| 类型 | 大小 | 路径 |",
        "|---|---:|---|",
    ]
    count = 0
    for e in entries:
        count += 1
        rows.append(f"| {e.kind} | {format_bytes(e.size_bytes)} | {e.path} |")
    if count == 0:
        rows.append("| 无 | - | - |")
    return rows


def _scan_report_to_dict(report: ScanReport) -> dict:
    return {
        "schema_version": report.schema_version,
        "created_at": report.created_at,
        "advisor_summary": report.advisor_summary,
        "warnings": list(report.warnings),
        "drives": [
            {
                "drive": asdict(r.drive),
                "candidates": [asdict(c) for c in r.candidates],
                "large_dirs": [asdict(e) for e in r.large_dirs],
                "large_files": [asdict(e) for e in r.large_files],
                "duplicate_groups": [asdict(g) for g in r.duplicate_groups],
                "scan_errors": list(r.scan_errors),
                "space_total_size_bytes": int(r.space_total_size_bytes),
                "space_total_dirs": int(r.space_total_dirs),
                "space_displayed_dirs": int(r.space_displayed_dirs),
                "space_display_limit": int(r.space_display_limit),
                "space_result_truncated": bool(r.space_result_truncated),
                "space_scanned_files": int(r.space_scanned_files),
            }
            for r in report.drives
        ],
    }


def _llm_advisor_payload(report: ScanReport) -> dict:
    candidates = []
    for result in report.drives:
        for candidate in result.candidates[:30]:
            candidates.append(
                {
                    "id": candidate.id,
                    "path": _sanitize_path(candidate.path),
                    "size_mb": round(candidate.size_bytes / _MB, 2),
                    "kind": candidate.kind,
                    "risk": candidate.risk,
                    "source_rule": candidate.source_rule,
                    "allowed_by_cleaner": candidate.allowed_by_cleaner,
                    "reason": candidate.reason,
                }
            )
    return {
        "schema_version": report.schema_version,
        "drive_summary": [
            {
                "drive": result.drive.drive,
                "total_gb": round(result.drive.total_bytes / _GB, 2),
                "used_gb": round(result.drive.used_bytes / _GB, 2),
                "free_gb": round(result.drive.free_bytes / _GB, 2),
            }
            for result in report.drives
        ],
        "candidates": candidates[:50],
        "large_files": [
            {"path": _sanitize_path(entry.path), "size_mb": round(entry.size_bytes / _MB, 2)}
            for result in report.drives
            for entry in result.large_files[:10]
        ][:20],
        "duplicate_groups": [
            {"size_mb": round(group.size_bytes / _MB, 2), "count": len(group.paths)}
            for result in report.drives
            for group in result.duplicate_groups[:10]
        ][:20],
    }


def _sanitize_path(path: str) -> str:
    """脱敏路径：替换用户主目录和用户名，仅在路径分隔符边界上匹配用户名。"""
    text = str(path or "")
    home = str(Path.home())
    if home and text.lower().startswith(home.lower()):
        text = "%USERPROFILE%" + text[len(home):]
    username = os.environ.get("USERNAME", "")
    if username and len(username) >= 2:
        # 仅替换路径分隔符边界上的用户名，避免误替换路径中的短字符串
        import re
        pattern = re.compile(
            r'(?<=[\\/])' + re.escape(username) + r'(?=[\\/]|$)',
            re.IGNORECASE,
        )
        text = pattern.sub("<USER>", text)
    return text


def _chat_completions_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    lower = base.lower()
    if lower.endswith("/chat/completions"):
        return base
    for suffix in ("/models", "/responses", "/completions"):
        if lower.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            lower = base.lower()
            break
    if lower.endswith(("/v1", "/v1beta", "/v2", "/v3", "/v4")):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _cancelled(cancel: Optional[Callable[[], bool]]) -> bool:
    if cancel is None:
        return False
    try:
        return bool(cancel())
    except Exception:
        return False


def _log_error(errors: list[str], msg: str, limit: int = 500) -> None:
    if len(errors) < limit:
        errors.append(msg)
