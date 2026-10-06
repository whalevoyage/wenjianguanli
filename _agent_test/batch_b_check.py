"""批次 B 自检：多步骤编排 agent_task（只读分析段 + 挂起等待确认）。"""
import json
import os
import sys
import time

sys.path.insert(0, ".")

from backend import config
from backend.jobs import manager
from backend.services import agent, agent_task, agent_planner

SANDBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox")


def build_sandbox():
    os.makedirs(os.path.join(SANDBOX, "cache"), exist_ok=True)
    os.makedirs(os.path.join(SANDBOX, "node_modules", "lib"), exist_ok=True)
    for name, size in [("a.bin", 300 * 1024), ("b.tmp", 1024), ("big.dat", 2 * 1024 * 1024)]:
        with open(os.path.join(SANDBOX, name), "wb") as stream:
            stream.write(b"x" * size)
    # 造一对重复文件（>1MB 才会进候选）
    for name in ("dup1.bin", "dup2.bin"):
        with open(os.path.join(SANDBOX, name), "wb") as stream:
            stream.write(os.urandom(1200 * 1024))
    # 垃圾：零字节 + 规则名
    open(os.path.join(SANDBOX, "cache", "empty.tmp"), "wb").close()
    with open(os.path.join(SANDBOX, "node_modules", "lib", "junk.log"), "wb") as stream:
        stream.write(b"log" * 400)
    return SANDBOX


def wait(job_id, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        job = manager.get(job_id)
        if job.get("status") in ("done", "failed", "cancelled", "waiting"):
            return job
        time.sleep(0.2)
    return manager.get(job_id)


root = build_sandbox()
print("沙箱：", root)

# 1. 计划预览：一句话 -> 计划树
preview = agent.plan_preview(f"把{root}腾出空间，不要动项目文件", {"conversation_id": "t-plan"})
print("\n=== 计划预览 ===")
print("pipeline =", preview.get("pipeline"))
print("action   =", (preview.get("plan") or {}).get("action"))
print("risk     =", (preview.get("plan") or {}).get("risk_level"), "/", (preview.get("plan") or {}).get("risk_reason"))
print("排除标签 =", ((preview.get("plan") or {}).get("constraints") or {}).get("exclude_tags"))
for step in preview.get("steps") or []:
    print(f"  {step['index']}. [{step['kind']:7}] {step['title']}  <- {step['input']} -> {step['output']}")

# 2. 启动多步骤任务（分析段）
intent = (preview.get("plan") or {}).get("intent") or {}
params = (preview.get("plan") or {}).get("params") or {}
job = manager.create("agent_task", intent.get("goal") or "Agent 任务", intent.get("scope", {}).get("path", ""), {
    "intent": intent, "plan_params": params, "steps": preview.get("steps"), "mode": "analyze",
})
manager.start(job["id"], agent_task.run_agent_task)
result = wait(job["id"])

print("\n=== 任务状态 ===")
print("status =", result.get("status"), "| message =", result.get("message"), "| percent =", result.get("percent"))
print("\n=== 计划树（落库后的每步结果）===")
for step in result.get("steps") or []:
    print(f"  {step['status']:8} {step['title']}  |  {step.get('digest') or step.get('note')}")

proposal = result.get("proposal") or {}
print("\n=== 方案 ===")
print(json.dumps({
    "action": proposal.get("action"),
    "label": proposal.get("label"),
    "item_count": proposal.get("item_count"),
    "freed": proposal.get("freed_text"),
    "duration": proposal.get("duration_text"),
    "batches": proposal.get("batches"),
    "risk": proposal.get("risk_level"),
    "exclude_tags": proposal.get("exclude_tags"),
    "blocked": proposal.get("blocked"),
}, ensure_ascii=False, indent=2))
print("清单前 3 条：")
for item in (proposal.get("items") or [])[:3]:
    print("   ", item)

# 3. 结果观察
print("\n=== 任务结果观察 ===")
obs = agent.summarize_job(result)
print(json.dumps({k: obs.get(k) for k in ("ok", "status", "message", "next_steps")}, ensure_ascii=False, indent=2))

# 4. 挂起状态下不能直接执行（无 proposal.action 时）
print("\n=== 挂起语义 ===")
print("status 落在 waiting（非终态）:", result.get("status") == "waiting")
print("active_count 含它:", manager.active_count())
