from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote
import xml.etree.ElementTree as ET
import requests

logger = logging.getLogger(__name__)


def _safe_path_segments(value: str) -> list[str]:
    segments: list[str] = []
    for part in str(value or "").replace("\\", "/").split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        segments.append(part)
    return segments


def _safe_filename(value: str) -> str:
    filename = PurePosixPath(str(value or "").replace("\\", "/")).name
    if not filename or filename in {".", ".."}:
        raise ValueError("invalid remote filename")
    return filename


class WebDAVClient:
    """坚果云/WebDAV 客户端轻量级实现，用于备份数据的云端同步"""

    def __init__(self, server_url: str, username: str, password: str, backup_dir: str = "DeepCatBackup"):
        self.server_url = server_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.backup_dir_segments = _safe_path_segments(backup_dir)
        self.backup_dir = "/".join(self.backup_dir_segments)
        self.auth = (username, password) if username and password else None

    def _backup_dir_url(self) -> str:
        if not self.backup_dir_segments:
            return self.server_url
        encoded = "/".join(quote(segment, safe="") for segment in self.backup_dir_segments)
        return self.server_url + encoded + "/"

    def _file_url(self, remote_filename: str) -> str:
        filename = _safe_filename(remote_filename)
        segments = [*self.backup_dir_segments, filename]
        encoded = "/".join(quote(segment, safe="") for segment in segments)
        return self.server_url + encoded

    def test_connection(self) -> tuple[bool, str]:
        """测试 WebDAV 连接与账号认证是否成功"""
        try:
            headers = {"Depth": "0"}
            r = requests.request("PROPFIND", self.server_url, auth=self.auth, headers=headers, timeout=10)
            if r.status_code in (200, 207):
                return True, "连接成功"
            elif r.status_code in (401, 403):
                return False, "用户名或密码错误"
            else:
                return False, f"服务器响应异常，状态码: {r.status_code}"
        except requests.exceptions.RequestException as e:
            logger.error(f"WebDAV test connection failed: {e}")
            return False, f"网络请求失败: {e}"

    def ensure_backup_directory(self) -> bool:
        """确保云端备份文件夹存在，若不存在则创建"""
        if not self.backup_dir_segments:
            return True
        url = self._backup_dir_url()
        try:
            # 先用 PROPFIND 探探路，如果存在返回 200/207
            r = requests.request("PROPFIND", url, auth=self.auth, headers={"Depth": "0"}, timeout=5)
            if r.status_code in (200, 207):
                return True
            # 不存在则创建
            r_mk = requests.request("MKCOL", url, auth=self.auth, timeout=10)
            if r_mk.status_code in (201, 405): # 201 表示创建成功，405 表示已存在
                return True
            logger.error(f"Failed to create remote dir: {r_mk.status_code}")
            return False
        except Exception as e:
            logger.error(f"ensure_backup_directory failed: {e}")
            return False

    def upload_file(self, local_path: Path, remote_filename: str) -> bool:
        """上传本地文件到云端备份文件夹"""
        if not self.ensure_backup_directory():
            return False
        url = self._file_url(remote_filename)
        try:
            with open(local_path, "rb") as f:
                r = requests.put(url, auth=self.auth, data=f, timeout=60)
            if r.status_code in (201, 204):
                return True
            logger.error(f"Upload failed: status code {r.status_code}")
            return False
        except Exception as e:
            logger.error(f"upload_file failed: {e}")
            return False

    def download_file(self, remote_filename: str, local_path: Path) -> bool:
        """下载云端备份文件到本地路径"""
        url = self._file_url(remote_filename)
        try:
            r = requests.get(url, auth=self.auth, stream=True, timeout=60)
            if r.status_code != 200:
                logger.error(f"Download failed: status code {r.status_code}")
                return False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"download_file failed: {e}")
            return False

    def list_files(self) -> list[dict[str, Any]]:
        """获取云端备份文件列表，按文件名（包含时间戳）倒序排列"""
        if not self.ensure_backup_directory():
            return []
        url = self._backup_dir_url()
        try:
            r = requests.request("PROPFIND", url, auth=self.auth, headers={"Depth": "1"}, timeout=15)
            if r.status_code not in (200, 207):
                return []

            root = ET.fromstring(r.content)
            files = []
            ns = "{DAV:}"

            for resp in root.findall(f".//{ns}response"):
                href_el = resp.find(f"{ns}href")
                if href_el is None or not href_el.text:
                    continue
                href = unquote(href_el.text)

                is_dir = False
                prop = resp.find(f".//{ns}prop")
                if prop is not None:
                    res_type = prop.find(f"{ns}resourcetype")
                    if res_type is not None and res_type.find(f"{ns}collection") is not None:
                        is_dir = True

                    size_el = prop.find(f"{ns}getcontentlength")
                    mtime_el = prop.find(f"{ns}getlastmodified")

                    size = int(size_el.text) if (size_el is not None and size_el.text) else 0
                    mtime = mtime_el.text if (mtime_el is not None and mtime_el.text) else ""
                else:
                    size = 0
                    mtime = ""

                if is_dir or href.rstrip("/").endswith(self.backup_dir):
                    continue

                filename = _safe_filename(href.rstrip("/").split("/")[-1])
                if filename and filename.endswith(".zip"):
                    files.append({
                        "name": filename,
                        "size": size,
                        "mtime": mtime,
                        "href": href
                    })

            # 按文件名倒序排列（备份文件名为 deepcat_backup_YYYYMMDD_HHMMSS.zip 格式，可以直接按字母倒序即是最新修改时间倒序）
            files.sort(key=lambda x: x["name"], reverse=True)
            return files
        except Exception as e:
            logger.error(f"list_files failed: {e}")
            return []

    def delete_file(self, remote_filename: str) -> bool:
        """删除云端备份文件"""
        url = self._file_url(remote_filename)
        try:
            r = requests.delete(url, auth=self.auth, timeout=10)
            if r.status_code in (200, 204):
                return True
            logger.error(f"Delete failed: status code {r.status_code}")
            return False
        except Exception as e:
            logger.error(f"delete_file failed: {e}")
            return False
