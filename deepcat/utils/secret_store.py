from __future__ import annotations

"""基于 Windows DPAPI 的本机密钥保护。

加密结果格式为 "dpapi:<base64>"，与明文可区分；非 Windows 平台或加解密失败时
按约定降级：protect 返回明文（保证功能可用），unprotect 对明文原样放行、对无法
解密的密文返回空串（密文跨机器/跨用户不可解）。
"""

import base64
import ctypes
import os
from ctypes import wintypes

PROTECTED_PREFIX = "dpapi:"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def _bytes_from_blob(blob: _DATA_BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _dpapi_protect(data: bytes) -> bytes:
    in_blob = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    return _bytes_from_blob(out_blob)


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob = _blob_from_bytes(data)
    out_blob = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    return _bytes_from_blob(out_blob)


def is_protected(value: str) -> bool:
    return str(value or "").startswith(PROTECTED_PREFIX)


def protect_text(plain: str) -> str:
    """加密文本；空串/已加密值原样返回，失败时降级返回明文。"""
    text = str(plain or "")
    if not text or is_protected(text):
        return text
    if os.name != "nt":
        return text
    try:
        cipher = _dpapi_protect(text.encode("utf-8"))
        return PROTECTED_PREFIX + base64.b64encode(cipher).decode("ascii")
    except Exception:
        return text


def unprotect_text(value: str) -> str:
    """解密文本；明文原样放行，密文解密失败（跨机器等）返回空串。"""
    text = str(value or "")
    if not is_protected(text):
        return text
    if os.name != "nt":
        return ""
    try:
        cipher = base64.b64decode(text[len(PROTECTED_PREFIX):].encode("ascii"), validate=True)
        return _dpapi_unprotect(cipher).decode("utf-8")
    except Exception:
        return ""
