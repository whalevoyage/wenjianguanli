"""「切换选项卡 / 断开订阅」不应取消任务的回归测试。

做法：起一个真任务 → 连上 SSE 读几帧 → 立刻断开连接（模拟切页时组件卸载）
→ 之后只轮询任务状态，看后端会不会把它标成 cancelled。

同时跑一个对照组：全程保持 SSE 连接，最终应为 done。
两者都应为 done：SSE 断开只退订，绝不取消任务。

用法：
    FILEPILOT_API=http://127.0.0.1:8001/api python tests/job_disconnect_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("FILEPILOT_API", "http://127.0.0.1:8000/api").rstrip("/")
TARGET = os.getenv("REPRO_TARGET", r"D:\FilePilotLite\frontend\node_modules")
# 用查重（要读文件内容算指纹）而不是扫描——扫描太快，来不及模拟「切页」
KIND = os.getenv("REPRO_KIND", "duplicates")


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"{}")


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:
        return json.loads(response.read() or b"{}")


def start_job() -> str:
    endpoint = "/duplicates/scan" if KIND == "duplicates" else "/scan"
    payload = post(endpoint, {"path": TARGET, "excludes": []})
    return payload["job_id"]


def watch(path: str, stop_after: int | None) -> list[str]:
    """订阅 SSE。stop_after=None 表示一直读到结束；给数字表示读到第 N 帧就断开。"""
    seen: list[str] = []
    request = urllib.request.Request(f"{BASE}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw in response:
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("data:"):
                    seen.append(line[5:].strip())
                    if len(seen) % 5 == 0:
                        print(f"    · 已读 {len(seen)} 帧")
                if stop_after is not None and len(seen) >= stop_after:
                    print(f"    · 达到 {stop_after} 帧，主动断开（模拟切页）")
                    return seen
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"    · 连接结束：{exc}")
    return seen


def poll(job_id: str, seconds: float) -> list[str]:
    timeline: list[str] = []
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        job = get(f"/jobs/{job_id}")
        status = job.get("status")
        if status != last:
            timeline.append(status)
            print(f"    [{time.time() % 1000:6.2f}] status={status} message={job.get('message')} "
                  f"cancel_requested={job.get('cancel_requested')}")
            last = status
        if status in {"done", "failed", "cancelled"}:
            break
        time.sleep(0.6)
    return timeline


def main() -> int:
    print("=" * 60)
    print(f"目标目录：{TARGET}")
    print("=" * 60)

    print("\n[对照组] 全程保持订阅")
    job_a = start_job()
    print(f"  job={job_a}")
    watch(f"/jobs/{job_a}/stream", stop_after=None)
    timeline_a = poll(job_a, 40)
    print(f"  时间线：{' → '.join(timeline_a)}")

    print("\n[实验组] 读 3 帧后断开（模拟切换到别的选项卡）")
    job_b = start_job()
    print(f"  job={job_b}")
    time.sleep(0.4)
    watch(f"/jobs/{job_b}/stream", stop_after=3)
    timeline_b = poll(job_b, 40)
    print(f"  时间线：{' → '.join(timeline_b)}")

    print("\n" + "=" * 60)
    print(f"对照组最终状态：{timeline_a[-1] if timeline_a else '未变化'}")
    print(f"实验组最终状态：{timeline_b[-1] if timeline_b else '未变化'}")
    cancelled = "cancelled" in timeline_b
    print("结论：" + ("❌ 断开订阅后任务被取消了（复现成功）" if cancelled
                     else "✅ 断开订阅不影响任务（未复现）"))
    return 1 if cancelled else 0


if __name__ == "__main__":
    raise SystemExit(main())
