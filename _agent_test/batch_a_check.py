"""批次 A 自检：意图结构 / 工具层 / 状态感知。"""
import json
import sys

sys.path.insert(0, ".")

from backend.services import agent, agent_intent as ai, agent_tools as at


def show(title, obj):
    print(f"\n=== {title} ===")
    print(json.dumps(obj, ensure_ascii=False, default=str)[:1400])


# 1. 同义归一 + 负向约束
ctx = {"prefs": {"exclude_paths": [], "exclude_tags": [], "hard_rules": []}, "state": {}}
local = agent.parse_local("把C盘腾出20G空间，不要动项目和照片", ctx)
plan = agent._local_payload(local, "把C盘腾出20G空间，不要动项目和照片", ctx)
show("腾空间 + 排除项目/照片", {
    "action": plan["plan"]["action"],
    "goal": plan["plan"]["goal"],
    "risk": plan["plan"]["risk_level"],
    "risk_reason": plan["plan"]["risk_reason"],
    "constraints": plan["plan"]["constraints"],
    "estimate": plan["plan"]["estimate"],
})

# 2. 上下文指代
ctx2 = {"prefs": {}, "state": {"last_scope": "D:\\素材"}}
local2 = agent.parse_local("就刚才那个目录查一下重复文件", ctx2)
show("上下文指代", agent._local_payload(local2, "就刚才那个目录", ctx2)["plan"]["params"])

# 3. 整理类追问（选项式）
local3 = agent.parse_local("帮我整理下载文件夹", ctx)
show("整理追问", local3)

# 4. 迁移缺参追问
local4 = agent.parse_local("把资料搬到 D 盘", ctx)
show("迁移追问", local4)

# 5. 取消/回滚/恢复
for text in ["取消现在跑的任务", "回滚上次那个迁移", "把隔离区里误删的东西恢复"]:
    local5 = agent.parse_local(text, ctx)
    print(f"  {text} -> {local5.get('action')}")

# 6. 风险分级：C 盘根 vs 普通目录
p_root = agent.sanitize_plan({"action": "junk_clean", "params": {"path": "C:\\"}}, "清理C盘垃圾", ctx)
p_sub = agent.sanitize_plan({"action": "junk_clean", "params": {"path": "D:\\Temp"}}, "清理D盘Temp", ctx)
print("\n=== 风险分级 ===")
print("C:\\        ->", p_root["risk_level"], "/", p_root["risk_reason"])
print("D:\\Temp    ->", p_sub["risk_level"], "/", p_sub["risk_reason"])

# 7. 工具层
show("工具分组数量", {k: len(v) for k, v in at.TOOL_GROUPS.items()})
show("查询磁盘状态", at.run_tool("drive_status", {})["note"])
show("检查权限", at.run_tool("check_user_permission", {})["note"])
show("检查受保护路径", at.run_tool("check_path_protected", {"path": "C:\\Windows"})["note"])
show("检查路径存在", at.run_tool("check_path_exists", {"path": "D:\\FilePilotLite"})["note"])
show("未知工具被拒", at.run_tool("rm_rf_everything", {}))

# 8. 状态感知
sc = agent.status_context()
show("状态感知", {
    "busy": sc["busy"], "running_count": sc["running_count"],
    "permission": sc["permission"], "model_configured": sc["model_configured"],
    "disks": sc["disks"], "trash": sc["trash"], "last_job": sc["last_job"],
})

# 9. status() 带工具清单
st = agent.status()
print("\n=== status() 新增键 ===", [k for k in st if k in ("tool_groups", "query_tools", "helper_tools", "risk_levels", "runtime")])

# 10. 偏好读写
prefs = agent.update_prefs({"exclude_tags": ["照片", "不存在的标签"], "hard_rules": [{"text": "项目都不要动", "path": "D:\\项目"}]})
print("\n=== 偏好收敛 ===", prefs["exclude_tags"], prefs["hard_rules"])

# 11. 硬约束生效：D:\项目 被永久排除
plan_hard = agent.sanitize_plan({"action": "junk_clean", "params": {"path": "D:\\项目"}}, "清理这个目录", {"prefs": prefs})
print("被永久约束挡下 ->", plan_hard["constraints"]["blocked_paths"])

# 12. 需要路径时的 clarify
plan_need = agent.sanitize_plan({"action": "junk_clean", "params": {}}, "清理垃圾", ctx)
print("缺路径 -> needs_path=", plan_need["needs_path"], "missing=", plan_need["missing"])
