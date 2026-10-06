"""复检：迁移流水线与整理流水线（此前只跑过「释放空间」那条）。

沙箱放在 %TEMP%，不动任何真实数据。
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, ".")

from backend import config
from backend.jobs import manager
from backend.services import agent, agent_task, agent_planner

BASE = os.path.join(os.environ.get("TEMP") or ".", "_agent_mig")
SRC = os.path.join(BASE, "src")
DST = os.path.join(BASE, "dst")


def build():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(os.path.join(SRC, "docs"), exist_ok=True)
    os.makedirs(os.path.join(SRC, "cache"), exist_ok=True)
    for name, size in [("big1.bin", 2 * 1024 * 1024), ("big2.bin", 1024 * 1024), ("small.txt", 200)]:
        with open(os.path.join(SRC, name), "wb") as f:
            f.write(b"x" * size)
    with open(os.path.join(SRC, "docs", "note.md"), "wb") as f:
        f.write(b"note" * 500)
    os.makedirs(DST, exist_ok=True)


def wait(job_id, timeout=180, terminal_only=False):
    wanted = ("done", "failed", "cancelled") if terminal_only else ("done", "failed", "cancelled", "waiting")
    start = time.time()
    while time.time() - start < timeout:
        job = manager.get(job_id)
        if job.get("status") in wanted:
            return job
        time.sleep(0.2)
    return manager.get(job_id)


def run_pipeline(label, message, ctx=None, approve=False):
    print(f"\n{'=' * 62}\n{label}\n{'=' * 62}")
    preview = agent.plan_preview(message, ctx or {"conversation_id": f"t-{label}"})
    print("pipeline =", preview.get("pipeline"))
    print("action   =", (preview.get("plan") or {}).get("action"))
    print("clarify  =", "有" if preview.get("clarify") else "无", preview.get("question") or "")
    for step in preview.get("steps") or []:
        print(f"   {step['index']}. [{step['kind']:7}] {step['title']}  <- {step['input']}")

    plan = preview.get("plan") or {}
    if not (preview.get("steps") or []):
        return None
    job = manager.create("agent_task", plan.get("goal") or "任务", (plan.get("scope") or {}).get("path", ""), {
        "intent": plan.get("intent") or {}, "plan_params": plan.get("params") or {},
        "steps": preview.get("steps"), "mode": "analyze",
    })
    manager.start(job["id"], agent_task.run_agent_task)
    got = wait(job["id"])
    print("\n状态:", got["status"], "|", got["message"])
    for step in got.get("steps") or []:
        print(f"   {step['status']:8} {step['title']}  |  {step.get('digest') or step.get('note')}")
    proposal = got.get("proposal") or {}
    if proposal:
        print("\n方案:", json.dumps({
            "action": proposal.get("action"), "executable": proposal.get("executable"),
            "item_count": proposal.get("item_count"), "freed": proposal.get("freed_text"),
            "duration": proposal.get("duration_text"), "batches": proposal.get("batches"),
            "risk": proposal.get("risk_level"), "blocked": proposal.get("blocked"),
        }, ensure_ascii=False))

    if approve and proposal.get("executable"):
        params = {**(got.get("params") or {}), "mode": "execute", "proposal": proposal}
        manager.update(job["id"], params=params, force=True)
        manager.start(job["id"], agent_task.run_agent_task)
        done = wait(job["id"], terminal_only=True)
        print("\n执行后:", done["status"], "|", done.get("message"))
        for step in done.get("steps") or []:
            print(f"   {step['status']:8} {step['title']}  |  {step.get('digest') or step.get('note')}")
        print("执行总结:", json.dumps((done.get("result") or {}).get("summary") or {}, ensure_ascii=False))
        return done
    return got


build()

# ---- 1) 迁移流水线：只出清单 ----
r1 = run_pipeline("迁移 · 只出清单", f"把 {SRC} 搬到 {DST}", approve=False)
print("\n目标目录执行前:", sorted(os.listdir(DST)))

# ---- 2) 迁移流水线：真搬 ----
build()
r2 = run_pipeline("迁移 · 执行搬移", f"把 {SRC} 搬到 {DST}", approve=True)
print("\n源目录:", sorted(os.listdir(SRC)))
print("目标目录:", sorted(os.listdir(DST)))
print("回滚动作数:", len((r2 or {}).get("actions") or []))

# ---- 3) 整理流水线 ----
build()
r3 = run_pipeline("整理 · 按类型", f"按类型整理 {SRC}", approve=False)

# ---- 4) 迁移缺目标 → 追问 ----
print(f"\n{'=' * 62}\n迁移缺参追问\n{'=' * 62}")
cl = agent.parse_local(f"把 {SRC} 搬走", {"state": {}, "prefs": {}})
print(json.dumps(cl, ensure_ascii=False, default=str)[:600])

# ---- 5) 整理只给了方式没给路径（点选项之后的第二轮）----
print(f"\n{'=' * 62}\n只给整理方式、没给路径\n{'=' * 62}")
cl2 = agent.parse_local("按类型", {"state": {}, "prefs": {}})
print(json.dumps(cl2, ensure_ascii=False, default=str)[:700])

shutil.rmtree(BASE, ignore_errors=True)
config.PREFS_FILE.unlink(missing_ok=True)
print("\n沙箱已清理")
