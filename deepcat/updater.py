from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from deepcat.utils.paths import get_app_dir


REPO_OWNER = "pipabcc"
REPO_NAME = "deepcat"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
USER_AGENT = "DeepCat-Updater"
PRESERVED_UPDATE_NAMES = {
    "settings.json",
    "settings.json.bak",
    "files",
    "data",
    "logs",
    "updates",
}


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: str
    tag_name: str
    asset_name: str
    download_url: str
    release_url: str
    published_at: str
    notes: str
    asset_size: int = 0
    asset_digest: str = ""


def _clean_version(version: str) -> str:
    text = str(version or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    return text


def _version_key(version: str) -> tuple[tuple[int, ...], bool, str]:
    text = _clean_version(version)
    if not text:
        return ((0,), True, "")
    match = re.match(r"^(\d+(?:\.\d+)*)", text)
    if not match:
        return ((0,), True, text.lower())
    numbers = tuple(int(part) for part in match.group(1).split("."))
    suffix = text[match.end() :].strip().lower()
    prerelease = bool(suffix and not suffix.startswith("+"))
    return (numbers, prerelease, suffix)


def compare_versions(left: str, right: str) -> int:
    left_nums, left_pre, left_suffix = _version_key(left)
    right_nums, right_pre, right_suffix = _version_key(right)
    max_len = max(len(left_nums), len(right_nums))
    padded_left = left_nums + (0,) * (max_len - len(left_nums))
    padded_right = right_nums + (0,) * (max_len - len(right_nums))
    if padded_left != padded_right:
        return 1 if padded_left > padded_right else -1
    if left_pre != right_pre:
        return -1 if left_pre else 1
    if left_suffix == right_suffix:
        return 0
    return 1 if left_suffix > right_suffix else -1


def is_newer_version(latest: str, current: str) -> bool:
    return compare_versions(latest, current) > 0


def _request_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    req = Request(
        str(url),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(req, timeout=float(timeout)) as resp:
            payload = resp.read()
    except HTTPError as exc:
        raise UpdateError(f"GitHub release request failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise UpdateError(f"GitHub release request failed: {exc.reason}") from exc
    except OSError as exc:
        raise UpdateError(f"GitHub release request failed: {exc}") from exc
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise UpdateError("GitHub release response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise UpdateError("GitHub release response is not an object")
    return data


def select_release_asset(release: dict[str, Any]) -> Optional[dict[str, Any]]:
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        return None
    assets = [asset for asset in raw_assets if isinstance(asset, dict)]
    zip_assets = [
        asset
        for asset in assets
        if str(asset.get("name", "") or "").strip().lower().endswith(".zip")
        and str(asset.get("browser_download_url", "") or "").strip()
    ]
    if not zip_assets:
        return None

    def score(asset: dict[str, Any]) -> tuple[int, int, int, str]:
        name = str(asset.get("name", "") or "").lower()
        return (
            1 if "deepcat" in name else 0,
            1 if ("windows" in name or re.search(r"(^|[-_.])win($|[-_.])", name)) else 0,
            int(asset.get("size", 0) or 0),
            name,
        )

    return sorted(zip_assets, key=score, reverse=True)[0]


def info_from_release(release: dict[str, Any], current_version: str) -> UpdateInfo:
    tag_name = str(release.get("tag_name", "") or "").strip()
    if not tag_name:
        raise UpdateError("GitHub release does not include tag_name")
    latest_version = _clean_version(tag_name)
    asset = select_release_asset(release)
    return UpdateInfo(
        available=is_newer_version(latest_version, current_version),
        current_version=str(current_version or ""),
        latest_version=latest_version,
        tag_name=tag_name,
        asset_name=str((asset or {}).get("name", "") or ""),
        download_url=str((asset or {}).get("browser_download_url", "") or ""),
        release_url=str(release.get("html_url", "") or ""),
        published_at=str(release.get("published_at", "") or ""),
        notes=str(release.get("body", "") or ""),
        asset_size=int((asset or {}).get("size", 0) or 0),
        asset_digest=str((asset or {}).get("digest", "") or ""),
    )


def check_latest_release(current_version: str) -> UpdateInfo:
    release = _request_json(LATEST_RELEASE_API)
    return info_from_release(release, current_version)


def _safe_filename(name: str, fallback: str = "deepcat-update.zip") -> str:
    text = Path(str(name or fallback)).name
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    if not text:
        text = fallback
    if not text.lower().endswith(".zip"):
        text = f"{text}.zip"
    return text


def _expected_sha256(digest: str) -> str:
    text = str(digest or "").strip()
    if text.lower().startswith("sha256:"):
        value = text.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return ""


def verify_zip_contains_exe(zip_path: Path, exe_name: str = "deepcat.exe") -> bool:
    target = str(exe_name or "deepcat.exe").lower()
    try:
        with ZipFile(zip_path) as zf:
            for name in zf.namelist():
                parts = [part for part in Path(name).parts if part not in {"", "."}]
                if parts and parts[-1].lower() == target:
                    return True
    except (BadZipFile, OSError):
        return False
    return False


def download_update(info: UpdateInfo, progress_cb: Optional[Callable[[int, int], None]] = None) -> Path:
    if not info.download_url:
        raise UpdateError("Release does not include a downloadable zip asset")
    updates_dir = get_app_dir() / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    file_name = _safe_filename(info.asset_name, f"deepcat-{info.latest_version}.zip")
    dest = updates_dir / file_name
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = Request(
        info.download_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": USER_AGENT,
        },
    )
    expected_hash = _expected_sha256(info.asset_digest)
    digest = hashlib.sha256()
    total = int(info.asset_size or 0)
    downloaded = 0
    try:
        with urlopen(req, timeout=30.0) as resp, tmp.open("wb") as fh:
            header_len = str(resp.headers.get("Content-Length", "") or "").strip()
            if header_len.isdigit():
                total = int(header_len)
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress_cb is not None:
                    progress_cb(downloaded, total)
    except HTTPError as exc:
        raise UpdateError(f"Update download failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise UpdateError(f"Update download failed: {exc.reason}") from exc
    except OSError as exc:
        raise UpdateError(f"Update download failed: {exc}") from exc
    if expected_hash and digest.hexdigest().lower() != expected_hash:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise UpdateError("Downloaded update package did not match sha256 digest")
    if not verify_zip_contains_exe(tmp):
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise UpdateError("Downloaded update package does not contain deepcat.exe")
    tmp.replace(dest)
    if progress_cb is not None:
        progress_cb(downloaded, total)
    return dest


def _ps_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _installer_script_text(zip_path: Path, app_dir: Path, exe_name: str, current_pid: int, expected_sha256: str = "") -> str:
    preserved = ", ".join(_ps_single_quote(name) for name in sorted(PRESERVED_UPDATE_NAMES))
    return f"""$ErrorActionPreference = 'Stop'
$ZipPath = {_ps_single_quote(str(zip_path))}
$AppDir = {_ps_single_quote(str(app_dir))}
$ExeName = {_ps_single_quote(str(exe_name or "deepcat.exe"))}
$OldPid = {int(current_pid)}
$ExpectedHash = {_ps_single_quote(str(expected_sha256 or "").lower())}
$LogPath = Join-Path $AppDir 'updates\\update.log'
$Staging = Join-Path $AppDir 'updates\\staging'
$BackupDir = Join-Path $AppDir 'updates\\backup'
$Preserved = @({preserved})
$BackupReady = $false

function Write-UpdateLog([string]$Message) {{
  $dir = Split-Path -Parent $LogPath
  if (!(Test-Path -LiteralPath $dir)) {{ New-Item -ItemType Directory -Path $dir -Force | Out-Null }}
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "[$stamp] $Message"
}}

try {{
  Write-UpdateLog 'installer started'
  Start-Sleep -Milliseconds 800
  $deadline = (Get-Date).AddSeconds(40)
  while ((Get-Process -Id $OldPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 500
  }}

  if ($ExpectedHash -ne '') {{
    $actualHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLower()
    if ($actualHash -ne $ExpectedHash) {{ throw "update package sha256 mismatch: $actualHash" }}
    Write-UpdateLog 'package sha256 verified'
  }}

  if (Test-Path -LiteralPath $Staging) {{ Remove-Item -LiteralPath $Staging -Recurse -Force }}
  New-Item -ItemType Directory -Path $Staging -Force | Out-Null
  Expand-Archive -LiteralPath $ZipPath -DestinationPath $Staging -Force

  $sourceExe = Get-ChildItem -LiteralPath $Staging -Filter $ExeName -Recurse -File | Select-Object -First 1
  if ($null -eq $sourceExe) {{ throw "update package does not contain $ExeName" }}
  $SourceRoot = $sourceExe.Directory.FullName
  $rootExe = Join-Path $Staging $ExeName
  if (Test-Path -LiteralPath $rootExe) {{ $SourceRoot = $Staging }}

  # 整目录备份：现有内容（保留名单除外）整体移入备份目录，失败可完整回滚
  if (Test-Path -LiteralPath $BackupDir) {{ Remove-Item -LiteralPath $BackupDir -Recurse -Force }}
  New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
  foreach ($item in Get-ChildItem -LiteralPath $AppDir -Force) {{
    if ($Preserved -contains $item.Name) {{ continue }}
    Move-Item -LiteralPath $item.FullName -Destination (Join-Path $BackupDir $item.Name) -Force
  }}
  $BackupReady = $true
  Write-UpdateLog 'existing files moved to backup'

  foreach ($item in Get-ChildItem -LiteralPath $SourceRoot -Force) {{
    if ($Preserved -contains $item.Name) {{ continue }}
    Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $AppDir $item.Name) -Recurse -Force
  }}

  $newExe = Join-Path $AppDir $ExeName
  if (!(Test-Path -LiteralPath $newExe)) {{ throw "installed executable missing: $newExe" }}
  Write-UpdateLog 'installer completed'
  Start-Process -FilePath $newExe -WorkingDirectory $AppDir
}} catch {{
  Write-UpdateLog ("installer failed: " + $_.Exception.Message)
  try {{
    if (Test-Path -LiteralPath $BackupDir) {{
      if ($BackupReady) {{
        foreach ($item in Get-ChildItem -LiteralPath $AppDir -Force) {{
          if ($Preserved -contains $item.Name) {{ continue }}
          Remove-Item -LiteralPath $item.FullName -Recurse -Force
        }}
        Write-UpdateLog 'partial install removed before rollback'
      }}
      foreach ($item in Get-ChildItem -LiteralPath $BackupDir -Force) {{
        $target = Join-Path $AppDir $item.Name
        if (Test-Path -LiteralPath $target) {{ Remove-Item -LiteralPath $target -Recurse -Force }}
        Move-Item -LiteralPath $item.FullName -Destination $target -Force
      }}
      Write-UpdateLog 'rollback completed'
    }}
  }} catch {{
    Write-UpdateLog ("restore failed: " + $_.Exception.Message)
  }}
  exit 1
}} finally {{
  try {{ if (Test-Path -LiteralPath $Staging) {{ Remove-Item -LiteralPath $Staging -Recurse -Force }} }} catch {{ }}
}}
"""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def launch_installer(
    zip_path: Path,
    current_pid: int,
    app_dir: Path,
    exe_name: str = "deepcat.exe",
    *,
    asset_digest: str = "",
) -> None:
    app_dir = Path(app_dir).resolve()
    zip_path = Path(zip_path).resolve()
    if not zip_path.exists():
        raise UpdateError(f"Update package not found: {zip_path}")
    # 安装前重新校验：发布方提供 digest 时与其比对（覆盖下载→安装全程的 TOCTOU），
    # 没有 digest 则记录当前哈希并嵌入脚本，至少封死启动→脚本执行之间的替换窗口。
    expected = _expected_sha256(asset_digest)
    actual = _file_sha256(zip_path)
    if expected and actual != expected:
        raise UpdateError("Update package no longer matches its sha256 digest")
    updates_dir = app_dir / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    script_path = updates_dir / "install_update.ps1"
    script_path.write_text(
        _installer_script_text(zip_path, app_dir, exe_name, int(current_pid or 0), expected or actual),
        encoding="utf-8",
    )
    powershell = "powershell.exe" if os.name == "nt" else "pwsh"
    try:
        subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            cwd=str(app_dir),
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        raise UpdateError(f"Could not launch updater helper: {exc}") from exc


def current_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return get_app_dir()
