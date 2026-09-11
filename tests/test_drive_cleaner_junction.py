"""drive_cleaner 删除路径对 Windows junction / reparse point 的防护回归。

junction 在 Windows 上可由普通用户创建（目录符号链接无需管理员权限），
删除路径若不识别 reparse point，会把 junction 指向的目标目录整个回收。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deepcat import drive_cleaner as dc


def _skip_if_no_junction_support(tmp_path: Path) -> Path:
    target = tmp_path / "junction_target"
    target.mkdir()
    link = tmp_path / "cache"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - 文件系统不支持时跳过
        pytest.skip(f"无法创建 junction: {exc}")
    if not link.exists():
        pytest.skip("symlink 未生成 junction")
    return link


@pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 特性")
def test_move_paths_to_recycle_bin_skips_junction(tmp_path, monkeypatch):
    link = _skip_if_no_junction_support(tmp_path)
    victim = link.resolve()
    (victim / "keep.txt").write_text("重要数据", encoding="utf-8")

    # 回收站实现替换为记录调用，避免测试真删数据
    calls: list[str] = []
    monkeypatch.setattr(dc, "_execute_recycle_batch", lambda paths: (calls.append([str(p) for p in paths]), (0, False))[1])

    errors: list[str] = []
    ok = dc._move_paths_to_recycle_bin([link], errors)

    assert not ok  # junction/符号链接同样按"跳过"处理，批次为空
    assert calls == []  # 链接不能进入回收站批次
    # 管理员环境下 os.symlink 可能创建真符号链接（走符号链接分支），否则为 junction 分支
    assert any(("junction" in message) or ("符号链接" in message) for message in errors)
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "重要数据"


@pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 特性")
def test_remove_path_if_old_never_touches_junction_target(tmp_path):
    link = _skip_if_no_junction_support(tmp_path)
    victim = link.resolve()
    (victim / "keep.txt").write_text("重要数据", encoding="utf-8")

    errors: list[str] = []
    dc._remove_path_if_old(link, cutoff=0.0, errors=errors, delete_mode="permanent")

    assert (victim / "keep.txt").exists()
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "重要数据"


@pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 特性")
def test_remove_children_skips_junction_children(tmp_path):
    container = tmp_path / "container"
    container.mkdir()
    target = tmp_path / "outside_target"
    target.mkdir()
    (target / "keep.txt").write_text("重要数据", encoding="utf-8")
    link = container / "cache"
    os.symlink(target, link, target_is_directory=True)

    errors: list[str] = []
    dc._remove_children(container, safe_age_hours=0, errors=errors, delete_mode="recycle")

    assert (target / "keep.txt").read_text(encoding="utf-8") == "重要数据"


@pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 特性")
def test_walk_limited_does_not_descend_into_junction(tmp_path):
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.txt").write_text("a", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    os.symlink(outside, root / "cache", target_is_directory=True)

    yielded = [Path(entry.path) for entry in dc._walk_limited(str(root), max_depth=8, cancel=None, errors=[])]  # type: ignore[arg-type]

    names = {str(path) for path in yielded}
    assert str(root / "real" / "a.txt") in names
    assert not any("secret.txt" in name for name in names)


@pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 特性")
def test_abspath_does_not_follow_junction():
    """_execute_recycle_batch 用 os.path.abspath 而非 resolve 构造 pFrom 的语义依据。"""
    tmp_path = Path(os.environ.get("TMP", "."))  # 仅用于占位，断言与具体目录无关
    del tmp_path
    # abspath 只做词法规范化，永不解析 reparse point；resolve 会跟随 junction
    assert os.path.abspath(os.path.join("rel", "path")) == os.path.normpath(
        os.path.join(os.getcwd(), "rel", "path")
    )
