"""Best-effort local secret storage for Windows desktop distribution."""
from __future__ import annotations

import base64
import os


def protect(value: str) -> str:
    if not value:
        return ""
    if os.name != "nt":
        return value
    try:
        import ctypes
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
        raw = value.encode("utf-8")
        source = (ctypes.c_byte * len(raw)).from_buffer_copy(raw)
        inp = Blob(len(raw), source)
        out = Blob()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
            return value
        try:
            data = ctypes.string_at(out.pbData, out.cbData)
            return "dpapi:" + base64.b64encode(data).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return value


def unprotect(value: str) -> str:
    if not value or not value.startswith("dpapi:") or os.name != "nt":
        return value
    try:
        import ctypes
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
        raw = base64.b64decode(value[6:])
        source = (ctypes.c_byte * len(raw)).from_buffer_copy(raw)
        inp = Blob(len(raw), source)
        out = Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
            return ""
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return ""
