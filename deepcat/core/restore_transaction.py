"""为已校验的备份文件提供可回滚的整体切换。"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Callable


logger = logging.getLogger(__name__)


def _target_path(root: Path, name: str) -> Path:
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"恢复目标超出数据目录: {name}")
    if target.exists() and not target.is_file():
        raise ValueError(f"恢复目标不是文件: {name}")
    return target


def replace_restored_files(
    staging_dir: Path,
    app_dir: Path,
    names: list[str],
    replace_file: Callable[[Path, Path, str], None],
) -> None:
    """旧数据库及 WAL 一起保留，全部安装成功后才清除回滚副本。"""
    affected = set(names)
    for name in names:
        if name.endswith(".db"):
            affected.update((name + "-wal", name + "-shm"))
    targets = {name: _target_path(app_dir, name) for name in sorted(affected)}
    rollback_dir = Path(tempfile.mkdtemp(prefix="deepcat_restore_rollback_", dir=app_dir))
    preserved: list[str] = []
    installed: list[str] = []
    keep_rollback = False
    try:
        for name, target in targets.items():
            if target.exists():
                backup = rollback_dir / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
                preserved.append(name)
        for name in names:
            replace_file(staging_dir, app_dir, name)
            installed.append(name)
    except BaseException as error:
        failures: list[str] = []
        for name in reversed(installed):
            if name not in preserved:
                try:
                    targets[name].unlink(missing_ok=True)
                except OSError:
                    failures.append(name)
        for name in reversed(preserved):
            try:
                (rollback_dir / name).replace(targets[name])
            except OSError:
                failures.append(name)
        if failures:
            # 回滚失败时必须保留仍能恢复的原文件，不能由临时目录清理将其删除。
            keep_rollback = True
            raise RuntimeError(f"恢复回滚未完成，原文件保留在 {rollback_dir}；涉及: {', '.join(failures)}") from error
        raise
    finally:
        if not keep_rollback:
            try:
                shutil.rmtree(rollback_dir)
            except OSError:
                logger.warning("清理恢复回滚目录失败: %s", rollback_dir, exc_info=True)
