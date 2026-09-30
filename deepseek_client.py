"""AI 建议兼容入口；未配置密钥时由后端使用本地规则建议。"""
import os


def is_configured() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY"))
