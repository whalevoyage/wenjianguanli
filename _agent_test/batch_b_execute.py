"""批次 B 自检（续）：确认后在同一任务里跑执行段。

沙箱放在项目外的 D:\\_agent_sandbox，避免撞上「项目自身不可写」的自保护。
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, ".")

from backend.jobs import manager
from backend.services import agent, agent_task

SANDBOX = os.path.join(os.environ.get("TEMP") or "C:\\Users\\faker\\AppData\\Local\\Temp", "_agent_sandbox")


def build_sandbox():
    os.makedirs(os.path.join(SANDBOX, "cache"), exist_ok=True)
    names = ["a.tmp", "b.tmp", "c.log", "d.bak"]
    for name in names:
        with open(os.path.join(SANDBOX, name), "wb") as stream:
            stream.write(b"junk" * 300)
    open(os.path.join(SANDBOX, "cache", "empty.tmp"), "wb").close()
    return SANDBOX


def wait(job_id, timeout=120, terminal_only=False):
    start = time.time()
    wanted = ("done", "failed", "cancelled") if terminal_only else ("done", "failed", "cancelled", "waiting")
    while time.time() - start < timeout:
        job = manager.get(job_id)
        if job.get("status") in wanted:
            return job
        time.sleep(0.2)
    return manager.get(job_id)


root = build_sandbox()
before = sorted(os.listdir(root))
print("沙箱：", root)
print("执行前：", before)

# ---- 阶段 1：分析 ----
preview = agent.plan_preview(f"把{root}清理垃圾", {"conversation_id": "t-exec"})
plan = preview["plan"]
job = manager.create("agent_task", plan["goal"], root, {
    "intent": plan["intent"], "plan_params": plan["params"],
    "steps": preview["steps"], "mode": "analyze",
})
manager.start(job["id"], agent_task.run_agent_task)
paused = wait(job["id"])
print("\n阶段1 状态：", paused["status"], "|", paused["message"])
proposal = paused.get("proposal") or {}
print("方案：", proposal.get("action"), proposal.get("item_count"), "项 ·",
      proposal.get("freed_text"), "·", proposal.get("batches"), "批")

# ---- 阶段 2：确认后继续执行（同一个 job_id） ----
params = {**(paused.get("params") or {}), "mode": "execute", "proposal": proposal}
manager.update(job["id"], params=params, message="已确认，准备执行", force=True)
manager.start(job["id"], agent_task.run_agent_task)
done = wait(job["id"], timeout=180, terminal_only=True)

print("\n阶段2 状态：", done["status"], "|", done["message"], "| percent =", done["percent"])
print("\n=== 完整计划树 ===")
for step in done.get("steps") or []:
    print(f"  {step['status']:8} {step['id']:8} {step['title']}  |  {step.get('digest') or step.get('note')}")

summary = (done.get("result") or {}).get("summary") or {}
print("\n=== 执行总结 ===")
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

after = sorted(os.listdir(root))
print("\n执行后：", after)
print("被移走：", [n for n in before if n not in after])
print("回滚动作数：", len(done.get("actions") or []), "| rollbackable =", done.get("rollbackable"))
