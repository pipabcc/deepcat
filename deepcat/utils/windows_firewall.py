"""Windows 防火墙放行助手。

打包后的程序在缺少防火墙规则时，Windows 防火墙可能在 WFP 的
ALE_AUTH_RECV_ACCEPT 层丢掉入站 SYN：端口依旧是 LISTENING，调用方却只会
停在 SYN_SENT 直到超时，看起来像「服务没有启动」。本模块只负责构造并提权
执行放行命令，命令是否真正生效由调用方重新自检回环连接确认。
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from deepcat.utils.logger import get_logger

logger = get_logger("deepcat.windows_firewall")

# 规则名沿用用户在「允许应用通过防火墙」里看到的名称，重复执行只会覆盖同名规则。
FIREWALL_RULE_NAME = "DeepCat"

# ShellExecuteW 返回值大于该阈值才代表进程创建成功，32 及以下都是错误码。
_SHELL_EXECUTE_ERROR_MAX = 32


def is_packaged_app() -> bool:
    """当前是否运行在 PyInstaller 等打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def default_target_executable() -> str:
    """需要放行的可执行文件路径：打包态是 exe 自身。"""
    executable = str(getattr(sys, "executable", "") or "").strip()
    if not executable:
        return ""
    try:
        return str(Path(executable).resolve())
    except OSError:
        return executable


def build_firewall_allow_parameters(program_path: str, rule_name: str = FIREWALL_RULE_NAME) -> str:
    """构造 netsh 放行命令的参数字符串。

    程序路径常含空格（例如 "D:\\Tencent Files\\..."），必须整体加引号，
    否则 netsh 会把路径截断到第一个空格处。
    """
    program = str(program_path or "").strip()
    if not program:
        raise ValueError("program_path 不能为空")
    if '"' in program:
        raise ValueError("program_path 不能包含双引号")
    name = str(rule_name or "").strip() or FIREWALL_RULE_NAME
    if '"' in name:
        raise ValueError("rule_name 不能包含双引号")
    return (
        "advfirewall firewall add rule "
        f'name="{name}" '
        "dir=in action=allow "
        f'program="{program}" '
        "enable=yes profile=any"
    )


def request_firewall_allow(program_path: str = "", rule_name: str = FIREWALL_RULE_NAME) -> tuple[bool, str]:
    """弹出 UAC 并以管理员身份添加放行规则。

    返回 (是否已发起, 给用户看的说明)。发起成功不代表放行已生效：用户仍可能
    在 UAC 弹窗里取消，所以调用方需要在用户确认后重新做回环自检。
    """
    if not sys.platform.startswith("win"):
        return False, "只有 Windows 需要配置防火墙放行。"
    if not is_packaged_app():
        return False, "只有打包后的 DeepCat 支持一键允许；当前是源码运行。"
    target = str(program_path or "").strip() or default_target_executable()
    if not target:
        return False, "无法确定需要放行的程序路径。"
    try:
        parameters = build_firewall_allow_parameters(target, rule_name)
    except ValueError as exc:
        return False, str(exc)
    try:
        result = int(ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", parameters, None, 0))
    except Exception as exc:
        logger.warning("发起防火墙放行失败: %s", repr(exc))
        return False, f"发起提权失败：{exc}"
    if result <= _SHELL_EXECUTE_ERROR_MAX:
        logger.info("防火墙放行未执行: ShellExecuteW 返回 %s", result)
        return False, "已取消授权，未添加防火墙规则。"
    logger.info("已发起防火墙放行: program=%s", target)
    return True, "已申请放行，请在系统弹窗中确认。"
