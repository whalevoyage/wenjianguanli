"""密钥存储的安全性质测试。

2026-10-17 之前的版本只有一个 round-trip 断言，而**它在 fail-open 的实现下同样通过**：
DPAPI 失败时 protect() 返回明文，unprotect() 对无前缀的值原样返回 —— 两头一对，
测试全绿，磁盘上却是明文。所以这里不只测「能用」，重点测「失败时不许降级」。
"""
from __future__ import annotations

import os
import types

import pytest

from backend import secrets, store
from backend.services import agent

WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# 一、正常路径
# ---------------------------------------------------------------------------

def test_round_trip():
    """加密后能解回原文（Windows 走真实 DPAPI；其他平台由开发模式覆盖）。"""
    if not WINDOWS:
        pytest.skip("非 Windows 平台没有 DPAPI")
    value = "sk-abcdef1234567890"
    encoded = secrets.protect(value)
    assert secrets.unprotect(encoded) == value


@pytest.mark.skipif(not WINDOWS, reason="DPAPI 仅 Windows 可用")
def test_encrypted_value_is_not_the_plaintext():
    value = "sk-abcdef1234567890"
    encoded = secrets.protect(value)
    assert encoded != value
    assert value not in encoded


@pytest.mark.skipif(not WINDOWS, reason="DPAPI 仅 Windows 可用")
def test_encrypted_value_is_prefixed_with_dpapi():
    assert secrets.protect("sk-xyz").startswith(secrets.DPAPI_PREFIX)


def test_empty_value_stays_empty():
    assert secrets.protect("") == ""
    assert secrets.unprotect("") == ""


# ---------------------------------------------------------------------------
# 二、fail-closed：加密失败绝不允许降级成明文
# ---------------------------------------------------------------------------

class _RejectingCrypt32:
    """CryptProtectData 始终失败（返回 0），模拟 DPAPI 不可用。"""

    def CryptProtectData(self, *args):
        return 0


@pytest.fixture
def dpapi_rejecting(monkeypatch):
    if not WINDOWS:
        pytest.skip("需要在 Windows 上替换 crypt32 才能触发真实调用路径")
    import ctypes

    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(crypt32=_RejectingCrypt32()))
    return True


def test_dpapi_failure_raises_instead_of_returning_plaintext(dpapi_rejecting):
    value = "sk-should-never-leak"
    with pytest.raises(secrets.SecretStorageError) as info:
        secrets.protect(value)
    # 错误信息里也不能带明文（否则会顺着异常进日志）
    assert value not in str(info.value)


def test_dpapi_failure_does_not_persist_api_key(monkeypatch, dpapi_rejecting):
    """加密失败时 update_settings 必须抛错，且一个字节都不写进 settings。"""
    saved: dict = {}
    monkeypatch.setattr(store, "read_agent_settings", lambda: {})
    monkeypatch.setattr(store, "save_agent_settings", lambda data: saved.update(data))
    monkeypatch.setattr(agent, "settings_view", lambda: {"ok": True})

    with pytest.raises(secrets.SecretStorageError):
        agent.update_settings(api_key="sk-must-not-land")

    assert saved == {}, f"加密失败却写了设置：{saved}"


def test_non_windows_refuses_plaintext_by_default(monkeypatch):
    """非 Windows 默认拒绝保存 —— 不允许「原样存盘」被包装成安全存储。"""
    monkeypatch.delenv(secrets.PLAINTEXT_ENV, raising=False)
    monkeypatch.setattr(secrets.os, "name", "posix")
    with pytest.raises(secrets.SecretStorageError):
        secrets.protect("sk-abc")


def test_dev_mode_plaintext_is_explicitly_marked(monkeypatch):
    """显式开发模式可以存明文，但必须带 plain: 前缀，一眼看得出没加密。"""
    monkeypatch.setenv(secrets.PLAINTEXT_ENV, "1")
    monkeypatch.setattr(secrets.os, "name", "posix")

    encoded = secrets.protect("sk-dev-only")
    assert encoded != "sk-dev-only"
    assert encoded == secrets.PLAIN_PREFIX + "sk-dev-only"
    assert secrets.storage_state(encoded) == secrets.STORAGE_PLAIN_DEV
    assert secrets.unprotect(encoded) == "sk-dev-only"


def test_dev_mode_plaintext_is_unreadable_once_switched_off(monkeypatch):
    """关掉开发开关后，plain: 里的明文不再被当成可用密钥。"""
    monkeypatch.delenv(secrets.PLAINTEXT_ENV, raising=False)
    assert secrets.unprotect(secrets.PLAIN_PREFIX + "sk-dev-only") == ""


# ---------------------------------------------------------------------------
# 三、存量数据：旧版本落的明文要能被识别，但不能被当成安全存储
# ---------------------------------------------------------------------------

def test_legacy_plaintext_is_detected_and_still_readable():
    legacy = "sk-legacy-plaintext-key"
    assert secrets.storage_state(legacy) == secrets.STORAGE_LEGACY
    assert secrets.is_encrypted(legacy) is False
    # 仍然认，否则用户升级后 Key 直接失效（用修漏洞的名义破坏功能）
    assert secrets.unprotect(legacy) == legacy


def test_storage_state_for_each_shape():
    assert secrets.storage_state("") == secrets.STORAGE_NONE
    assert secrets.storage_state(secrets.DPAPI_PREFIX + "AAAA") == secrets.STORAGE_DPAPI
    assert secrets.storage_state(secrets.PLAIN_PREFIX + "x") == secrets.STORAGE_PLAIN_DEV
    assert secrets.storage_state("raw") == secrets.STORAGE_LEGACY


# ---------------------------------------------------------------------------
# 四、接口层：只回掩码
# ---------------------------------------------------------------------------

def _stored_key(monkeypatch, key: str) -> None:
    if WINDOWS:
        monkeypatch.setattr(
            store, "read_agent_settings", lambda: {"api_key": secrets.protect(key)}
        )
    else:  # 非 Windows 用显式开发模式造一个合法存储串
        monkeypatch.setenv(secrets.PLAINTEXT_ENV, "1")
        monkeypatch.setattr(
            store, "read_agent_settings", lambda: {"api_key": secrets.PLAIN_PREFIX + key}
        )


def test_settings_view_never_returns_the_full_key(monkeypatch):
    key = "sk-live-1234567890abcdef"
    _stored_key(monkeypatch, key)

    view = agent.settings_view()

    assert view["configured"] is True
    assert key not in repr(view)
    assert view["masked"] and view["masked"] != key
    assert view["masked"].endswith(key[-4:])


def test_settings_view_warns_on_legacy_plaintext(monkeypatch):
    """存量明文必须在界面上被如实指出来，不能悄悄留着。"""
    monkeypatch.setattr(store, "read_agent_settings", lambda: {"api_key": "sk-legacy-plain"})
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    view = agent.settings_view()
    assert view["storage"] == secrets.STORAGE_LEGACY
    assert view["storage_warning"]
    assert "明文" in view["storage_warning"]


def test_settings_view_has_no_warning_when_encrypted(monkeypatch):
    if WINDOWS:
        encoded = secrets.protect("sk-properly-encrypted-key")
    else:
        encoded = secrets.PLAIN_PREFIX + "sk-properly-encrypted-key"
        monkeypatch.setenv(secrets.PLAINTEXT_ENV, "1")

    monkeypatch.setattr(store, "read_agent_settings", lambda: {"api_key": encoded})
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    view = agent.settings_view()
    assert view["storage"] in {secrets.STORAGE_DPAPI, secrets.STORAGE_PLAIN_DEV}
    if WINDOWS:
        assert view["storage_warning"] == ""


def test_settings_endpoint_never_returns_the_full_key(monkeypatch):
    """走真实 HTTP 路由，确认序列化后的响应体里也没有完整 Key。"""
    key = "sk-live-abcdefghijklmnop"
    _stored_key(monkeypatch, key)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    response = client.get("/api/settings/agent")
    assert response.status_code == 200

    assert key not in response.text
    payload = response.json()
    assert payload["masked"] != key
    assert payload["storage"] in {
        secrets.STORAGE_DPAPI,
        secrets.STORAGE_PLAIN_DEV,
        secrets.STORAGE_LEGACY,
    }
