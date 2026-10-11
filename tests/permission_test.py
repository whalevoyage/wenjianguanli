"""权限档位门禁的专项测试（不碰任何真实文件）。

覆盖三件事：
1. 档位接口：默认档、切 full 必须 confirm、非法档位被拒；
2. 只读档：写任务在入口被拒、扫描与「只生成计划」的迁移放行；
3. 门禁函数 write_block_reason 的判定矩阵（纯函数，不写盘）。

用法：
    FILEPILOT_API=http://127.0.0.1:8001/api python tests/permission_test.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = os.getenv("FILEPILOT_API", "http://127.0.0.1:8000/api").rstrip("/")

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'OK' if ok else 'FAIL'}] {name}{(' —— ' + detail) if detail and not ok else ''}")


def call(path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            return exc.code, json.loads(body or "{}")
        except json.JSONDecodeError:
            return exc.code, {"detail": body[:120]}


def main() -> int:
    print("=" * 56)
    print("权限档位门禁测试")
    print("=" * 56)

    # ---- 1. 档位接口 ----
    status, body = call("/settings/safety")
    check("读取档位接口可用", status == 200 and body.get("mode") in {"readonly", "standard", "full"})
    # 先显式归位再断言默认档：否则上一次跑测试残留的档位会让这条误报
    call("/settings/safety", {"mode": "standard"})
    body = call("/settings/safety")[1]
    check("可归位到标准档", body.get("mode") == "standard", str(body.get("mode")))
    check("档位表包含三档", len(body.get("modes") or []) == 3)
    # 底线声明：**断言"这三条底线都在"，而不是数条数**。
    # 写死 `== 2` 的后果已经发生过一次：代码补了「只读档一律拒绝」这第三条，
    # 测试却一直红着 —— 而这种红是"测试过时"，不是"代码坏了"，看久了就没人看了。
    guards = " ".join(body.get("guards") or [])
    check("底线声明存在", len(body.get("guards") or []) >= 3, str(len(body.get("guards") or [])))
    check("底线含：删除先进隔离区", "隔离区" in guards and "永不直接删" in guards)
    check("底线含：自保护不可写", "任何档位都不可写" in guards)
    check("底线含：只读档一律拒绝", "只读" in guards and "一律拒绝" in guards)
    tones = {m["id"]: m.get("tone") for m in body.get("modes") or []}
    check("三档配色互不相同（胶囊能看出差别）", len(set(tones.values())) == 3, str(tones))

    check("非法档位被拒", call("/settings/safety", {"mode": "god"})[0] == 400)
    check("切完全访问缺 confirm 被拒", call("/settings/safety", {"mode": "full"})[0] == 400)

    status, body = call("/settings/safety", {"mode": "full", "confirm": True})
    check("带 confirm 可切到完全访问", status == 200 and body.get("mode") == "full")
    check("完全访问标记 relaxes_protection", body.get("relaxes_protection") is True)

    # ---- 2. 只读档的入口拦截 ----
    call("/settings/safety", {"mode": "readonly"})
    check("只读档：写任务被入口拒绝", call("/backup", {
        "source_dir": "D:\\FilePilotLite", "target_dir": "D:\\_perm_test_bk", "output_dir": "D:\\_perm_test_bk",
    })[0] == 400)
    check("只读档：扫描放行", call("/scan", {"path": "D:\\FilePilotLite", "excludes": []})[0] == 200)
    check("只读档：只生成计划的迁移放行", call("/migrate", {
        "source_dir": "D:\\FilePilotLite", "target_dir": "D:\\_perm_test_tgt",
        "mode": "move", "dry_run": True, "excludes": [],
    })[0] == 200)

    # 收尾：无论成败都回到标准档
    call("/settings/safety", {"mode": "standard"})
    check("已回到标准档", call("/settings/safety")[1].get("mode") == "standard")

    # ---- 3. 门禁判定矩阵（纯函数）----
    from backend import config, fsutil  # noqa: PLC0415

    home = Path.home()
    matrix = [
        ("桌面 / 标准", home / "Desktop" / "x.txt", "standard", True),
        ("桌面 / 完全访问", home / "Desktop" / "x.txt", "full", False),
        ("状态目录 / 完全访问", config.STATE_DIR / "jobs.json", "full", True),
        ("项目自身 / 完全访问", config.APP_ROOT / "README.md", "full", True),
        ("系统目录 / 标准", Path("C:/Windows/System32/x.dll"), "standard", True),
        ("系统目录 / 完全访问", Path("C:/Windows/System32/x.dll"), "full", False),
        ("普通目录 / 标准", Path("D:/Workspace/a.txt"), "standard", False),
    ]
    for name, path, mode, should_block in matrix:
        blocked = fsutil.write_block_reason(path, mode) is not None
        check(f"门禁[{name}]", blocked == should_block, f"实际 {'拦' if blocked else '放行'}")

    print("\n" + "=" * 56)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for name in FAILED:
        print(f"  ✗ {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
