"""无界面上下文：让服务层能脱离 JobManager 被同步调用。

`services/*` 里的入口都是 `run_x(ctx)` 的形状，靠 ctx 报进度、查取消、拿参数。
CLI 和 MCP server 都是一问一答、不需要进度条，但**不该为了它们去改服务层** ——
所以给它们喂这个空实现。

放成独立模块是因为它现在有两个使用方（`backend/cli.py` 与 `backend/mcp_server.py`），
各写一份迟早会不一致。
"""
from __future__ import annotations


class HeadlessContext:
    """所有回调都实现成空操作，只保留 `params`。"""

    def __init__(self, params: dict | None = None) -> None:
        self._params = params or {}
        self.job_id = "headless"

    @property
    def params(self) -> dict:
        return self._params

    def cancelled(self) -> bool:
        return False

    def check_cancel(self) -> None:
        return None

    def phase(self, *_args, **_kwargs) -> None:
        return None

    def progress(self, *_args, **_kwargs) -> None:
        return None

    def tick(self, *_args, **_kwargs) -> None:
        return None

    def add_actions(self, *_args, **_kwargs) -> None:
        return None

    def set_plan(self, *_args, **_kwargs) -> None:
        return None

    def waiting(self, *_args, **_kwargs) -> None:
        return None

    def sleep(self, *_args, **_kwargs) -> None:
        return None

    def job(self) -> dict:
        return {"id": self.job_id, "params": self._params}


# MCP server 里的旧名字，保留别名免得改两处
Headless = HeadlessContext
