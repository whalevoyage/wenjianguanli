"""本地密钥存储。

**Windows：DPAPI（CryptProtectData），加密失败一律拒绝保存。**

2026-10-17 之前这里是「尽力而为」写法：DPAPI 调用失败、或平台不是 Windows 时
直接 `return value` —— 等于把明文当密文存进 `settings.json`，而界面照样显示
「API Key 已保存」。用户以为加密了，其实没有。这是**静默 fail-open**，本轮改成
fail-closed：

- 加密成功 → `dpapi:<base64>`，可以落盘；
- 加密失败 → 抛 `SecretStorageError`，调用方不得写盘（异常在 `save_*` 之前）；
- 非 Windows 默认也抛（不再假装安全），只有显式设置
  `FILEPILOT_ALLOW_PLAINTEXT_SECRETS=1` 的开发模式才允许存成 `plain:` 前缀 ——
  这个前缀本身就是标记：「明文、仅用于开发」，不会被误认作加密存储。

`SecretStorageError` 继承 `ValueError`：路由层统一兜底是
`except (OSError, ValueError)` → 400 + 原文，继承 Exception 会变成 500，
一次「友好拒绝」看起来就成了服务端崩溃。
"""
from __future__ import annotations

import base64
import os

DPAPI_PREFIX = "dpapi:"
# 显式开发模式下的明文前缀。出现在文件里就说明「这真的没加密」。
PLAIN_PREFIX = "plain:"
PLAINTEXT_ENV = "FILEPILOT_ALLOW_PLAINTEXT_SECRETS"

# 存量数据：旧版本 protect() 失败时把明文原样写进了 settings.json。
# 前缀判定为「两者都不是」即是这种历史明文 —— 读取时仍然认（否则用户 Key 突然失效，
# 等于用修漏洞的名义破坏功能），但会通过 storage_state() 如实暴露，界面提示重新保存。
STORAGE_DPAPI = "dpapi"
STORAGE_PLAIN_DEV = "plain-dev"
STORAGE_LEGACY = "legacy-plaintext"
STORAGE_NONE = "none"


class SecretStorageError(ValueError):
    """密钥无法安全存储（加密不可用 / 加密失败）。

    继承 ValueError 有两个作用：路由层能把它翻成 400 + 人话；调用方在写盘前就中断。
    """


def _dev_plaintext_allowed() -> bool:
    """是否处于显式允许明文存储的开发模式。默认关。"""
    return (os.getenv(PLAINTEXT_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def _protect_dpapi(value: str) -> str:
    """DPAPI 加密。任何一步失败都抛异常，**绝不返回原值**。"""
    try:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = value.encode("utf-8")
        source = (ctypes.c_byte * len(raw)).from_buffer_copy(raw)
        inp = Blob(len(raw), source)
        out = Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
        )
        if not ok:
            raise SecretStorageError(
                "API Key 加密失败（Windows DPAPI 拒绝加密），未保存。"
                "请稍后重试；如果反复失败，请检查当前用户配置文件是否完好。"
            )
        try:
            data = ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except SecretStorageError:
        raise
    except Exception as exc:  # ctypes 不可用、结构体定义失败等
        raise SecretStorageError(
            "API Key 加密失败（无法调用 Windows DPAPI），未保存。"
            "当前环境的加密组件不可用，出于安全考虑不会以明文保存。"
        ) from exc

    encoded = DPAPI_PREFIX + base64.b64encode(data).decode("ascii")
    if encoded == value:  # 理论上不可能，留作最后一道自检
        raise SecretStorageError("API Key 加密结果异常，未保存。")
    return encoded


def _unprotect_dpapi(value: str) -> str:
    """DPAPI 解密。失败返回空串 —— 读不出来就当没配置，不抛出、不猜。"""
    try:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = base64.b64decode(value[len(DPAPI_PREFIX):])
        source = (ctypes.c_byte * len(raw)).from_buffer_copy(raw)
        inp = Blob(len(raw), source)
        out = Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
        ):
            return ""
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:
        return ""


def protect(value: str) -> str:
    """把明文变成可落盘的存储串。**无法安全加密时抛 SecretStorageError。**

    调用方必须在 `save_*` 之前调用它，让失败自然中断保存流程。
    """
    if not value:
        return ""

    if os.name == "nt":
        return _protect_dpapi(value)

    if _dev_plaintext_allowed():
        # 显式开发模式：存成 plain: 前缀。文件里一眼能看出「这不是加密的」。
        return PLAIN_PREFIX + value

    raise SecretStorageError(
        "API Key 未保存：当前系统不支持 Windows DPAPI 加密。"
        f"开发环境如确实需要明文存储，请设置 {PLAINTEXT_ENV}=1。"
    )


def unprotect(value: str) -> str:
    """把存储串还原成明文。读不出来时返回空串（安全侧）。"""
    if not value:
        return ""
    if value.startswith(DPAPI_PREFIX):
        if os.name != "nt":
            return ""
        return _unprotect_dpapi(value)
    if value.startswith(PLAIN_PREFIX):
        # 只认显式开发模式写下的明文；关掉开关后它就不再被当成可用密钥
        return value[len(PLAIN_PREFIX):] if _dev_plaintext_allowed() else ""
    # 历史遗留：旧版本没能加密、直接落的明文。仍然认，避免用户 Key 突然失效；
    # 具体状态由 storage_state() 暴露，界面会提示「重新保存一次以加密」。
    return value


def storage_state(stored: str) -> str:
    """判断磁盘上这条密钥是什么形态，供界面如实展示。"""
    stored = (stored or "").strip()
    if not stored:
        return STORAGE_NONE
    if stored.startswith(DPAPI_PREFIX):
        return STORAGE_DPAPI
    if stored.startswith(PLAIN_PREFIX):
        return STORAGE_PLAIN_DEV
    return STORAGE_LEGACY


def is_encrypted(stored: str) -> bool:
    return storage_state(stored) == STORAGE_DPAPI
