"""`_run_step` 的步骤执行契约。

为什么要单独一组：2026-10-17 之前**没有任何测试覆盖 `_run_step`**，
于是 2026-10-13 引入的一个名字遮蔽 bug 活了四天：

    if kind == "job":
        from . import clean, duplicates, index, junk, ...   # ← 绑定局部名 index
        sub = StepContext(parent, step, step_params, index, total)   # ← 传进去的是模块

函数形参 `index`（步骤序号）被模块对象遮蔽，`max(0, index)` 直接 TypeError。
后果是**所有 Agent 多步任务在执行阶段必崩**，而且表面看起来像「任务失败」，
不像代码缺陷。

这组测试只守执行入口的形状，不重复各服务的业务测试。
"""
from __future__ import annotations

from backend.services import agent_task


class _RecordingCtx:
    """替身上下文：只记录 _run_step 传进来的东西，不碰真实任务管理器。"""

    def __init__(self, parent, step, params, index, total):
        self.seen = {"step": step, "params": params, "index": index, "total": total}

    def __getattr__(self, name):
        # 构造函数以外一律不用，避免掩盖真实的调用顺序问题
        return lambda *args, **kwargs: None


def test_job_step_passes_an_integer_index(monkeypatch, tmp_path):
    """形参 index 必须还是那个整数序号，不能被 `from . import index` 遮蔽。"""
    captured: dict = {}

    class Ctx(_RecordingCtx):
        def __init__(self, parent, step, params, index, total):
            super().__init__(parent, step, params, index, total)
            captured.update(self.seen)

    monkeypatch.setattr(agent_task, "StepContext", Ctx)

    agent_task._run_step(
        {"kind": "job", "tool": "large_scan"},
        {"scope": {"path": str(tmp_path)}, "constraints": {}},
        parent=None,
        index=7,
        total=9,
        items=[],
    )

    assert captured["index"] == 7
    assert isinstance(captured["index"], int), "index 被模块对象遮蔽了"
    assert captured["total"] == 9


def test_every_job_tool_reaches_its_branch(monkeypatch, tmp_path):
    """每个 job 类动作都要能进到自己的分支，而不是落到「暂不支持」。

    这里不验证业务正确性（各服务自己有测试），只验证**分发不丢**——
    漏一个分支的表现是运行期 `ValueError: 多步骤流水线暂不支持的任务`。
    """
    from backend.services import tool_specs as S

    monkeypatch.setattr(agent_task, "StepContext", _RecordingCtx)
    monkeypatch.setattr(
        agent_task, "_step_params", lambda step, intent: {"path": str(tmp_path)}
    )

    job_tools = [spec.name for spec in S.ACTION_SPECS if spec.kind == "job"]
    intent = {"scope": {"path": str(tmp_path)}, "constraints": {}}

    unsupported = []
    for tool in job_tools:
        try:
            agent_task._run_step({"kind": "job", "tool": tool}, intent, None, 0, 1, [])
        except ValueError as exc:
            if "暂不支持" in str(exc):
                unsupported.append(tool)
        except Exception:
            # 业务层报错（缺参数、目录不可读等）不算分发问题
            pass

    assert unsupported == [], f"这些写进白名单的动作没有执行分支：{unsupported}"
