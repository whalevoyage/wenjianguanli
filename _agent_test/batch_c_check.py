"""补做部分自检：模型 action=none 也要给选项 + 偏好自动积累。"""
import json
import sys

sys.path.insert(0, ".")

from backend import config
from backend.services import agent

print("=== 1) 模型回 action=none（模拟真实那次「那个东西你看着办」）===")
plan = agent.normalize(
    {"action": "none", "reason": "还不清楚你指的是哪个目录、想做什么操作，请指明路径和动作", "params": {}},
    "那个东西你看着办", {},
)
p = agent.none_payload(plan, "那个东西你看着办", "deepseek", 1286)
print("type    =", p.get("type"))
print("source  =", p.get("source"), "| tokens =", p.get("tokens"))
print("message =", p.get("message"))
print("选项    =", [o["label"] for o in p.get("options") or []])

print("\n=== 2) 带路径时的选项 ===")
p2 = agent.none_payload({"action": "none", "reason": "缺具体对象"}, "把 D:\\FilePilotLite 那个啥弄一下", "deepseek")
for o in p2.get("options") or []:
    print("   ", o["label"], "—", o["note"])

print("\n=== 3) 偏好自动积累 ===")
config.PREFS_FILE.unlink(missing_ok=True)
agent.add_common_path("D:\\FilePilotLite")
agent.add_common_path("C:\\")                    # 盘符根不该被记住
agent.add_common_path("D:\\FilePilotLite")       # 重复不该叠加
agent.add_clean_strategy("junk_clean", "D:\\FilePilotLite", "把 D:\\FilePilotLite 清理垃圾")
agent.add_clean_strategy("junk_clean", "D:\\FilePilotLite", "把 D:\\FilePilotLite 清理垃圾")
prefs = agent.read_prefs()
print("common_paths =", json.dumps(prefs["common_paths"], ensure_ascii=False))
print("strategies   =", json.dumps(prefs["clean_strategies"], ensure_ascii=False))

print("\n=== 4) 白名单仍然拦得住 ===")
bad = agent.update_prefs({"clean_strategies": [{"action": "rm -rf", "path": "x"},
                                               {"action": "junk_clean", "path": "D:\\a", "message": "m"}]})
print("收窄后 =", json.dumps(bad["clean_strategies"], ensure_ascii=False))

config.PREFS_FILE.unlink(missing_ok=True)
print("\n已重置 prefs.json")
