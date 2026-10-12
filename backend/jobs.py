"""任务模型：把每个耗时操作变成一个有状态、有进度、可中断、可回滚的 Job。

这是整个项目的骨架 —— 扫描、查重、垃圾、迁移、清理全部走这一套，
前端靠 SSE 订阅进度来实现"流程可视化"。
"""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import config, database, fsutil, permissions, store

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_WAITING = "waiting"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

TERMINAL = {JOB_DONE, JOB_FAILED, JOB_CANCELLED}

def reclaimed_bytes(job_type: str, result: dict) -> int:
    """这次任务实际腾出了多少字节。

    **只认真正动过文件的类型**，而且要显式取字段，不去猜 ——
    猜错会污染「累计腾出」这个数字，而这个数字是给人信心的，不能虚。

    - `clean`：`freed_size`（文件已移进隔离区）
    - `migrate`：`total_size`（真搬了才算，dry_run 不算）
    - 其它（scan / tree / duplicates / junk / similar）：**一律 0**
      —— 它们只是"看"，没有动过任何文件
    """
    if not isinstance(result, dict):
        return 0
    if job_type == "clean":
        return max(0, int(result.get("freed_size") or 0))
    if job_type == "migrate":
        if result.get("dry_run"):
            return 0
        return max(0, int(result.get("total_size") or 0))
    return 0


JOB_TYPES: dict[str, str] = {
    "scan": "扫描分析",
    "tree": "空间地图",
    # 内容索引**不在** WRITE_JOB_TYPES 里：它只读用户文件，
    # 索引库写在项目自己的状态目录（内部状态），所以「只读」档必须放行。
    "index": "内容索引",
    "virtual": "整理预览",
    "projects": "项目雷达",
    "duplicate": "重复文件",
    "similar": "相似图片",
    "junk": "垃圾文件",
    "migrate": "跨盘迁移",
    "clean": "清理",
    "backup": "项目备份",
    "agent_task": "Agent 任务",
}

# 流程可视化的阶段名（前端画流程图用）
PHASES = ["扫描", "分析", "生成清单", "等待确认", "执行", "校验", "完成"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# 只在「当次结果」里有意义、却不该进历史/列表的巨型字段。
#
# 典型是 scan 的 `result.dir_sizes`——「每个目录一条」的大小表，扫一整块盘能到几万条、
# 几 MB（实测扫 D:\ 5.46 MB / 44,845 项）。前端**从来不用它**（只有点「保存当前为快照」
# 那一刻用，而那条走的是前端内存里的当次结果，不读 job），快照 diff 读的也是快照文件。
# 它却会被 persist 进 jobs 表、再被 `GET /api/jobs` 原样带出去 ——
# 2026-10-13 实测：列表里带上这么一条 5.5 MB 的结果后，/api/jobs 直接 500，任务中心整个打不开。
HEAVY_RESULT_FIELDS = ("dir_sizes",)


def _trim_job(job: dict) -> dict:
    """落库 / 读入前裁掉巨型字段。

    **不删任务、不动其它字段**，只把 `result.dir_sizes` 这类"当次才用"的表去掉；
    内存里那份（SSE 推给前端、供「保存为快照」用）保持完整。
    """
    result = job.get("result")
    if not isinstance(result, dict):
        return job
    if not any(result.get(field) for field in HEAVY_RESULT_FIELDS):
        return job
    trimmed = dict(job)
    trimmed["result"] = {key: value for key, value in result.items() if key not in HEAVY_RESULT_FIELDS}
    return trimmed


def _blank_job(job_type: str, title: str, target: str, params: dict) -> dict:
    return {
        "id": new_id(),
        "type": job_type,
        "type_label": JOB_TYPES.get(job_type, job_type),
        "title": title,
        "target": target,
        "params": params,
        "status": JOB_PENDING,
        "phase": PHASES[0],
        "phase_index": 0,
        "message": "已创建，等待执行",
        "current": 0,
        "total": None,
        "percent": 0,
        "plan": [],
        "actions": [],
        "result": None,
        "error": None,
        # Agent 多步骤任务（agent_task）专用：计划树与待确认方案
        "steps": [],
        "step_index": 0,
        "step_total": 0,
        "intent": None,
        "proposal": None,
        "permission": permissions.current(),
        "created_at": fsutil.now_iso(),
        "started_at": None,
        "finished_at": None,
        "cancel_requested": False,
        "rollbackable": False,
        "event_seq": 0,
    }


class JobCancelled(Exception):
    pass


class JobPaused(Exception):
    """任务主动停在「等待确认」。

    与 JobCancelled 的区别：cancel 是终态，paused 只是挂起——
    Agent 多步骤任务跑完只读分析后停在这里，用户确认后可以把同一个
    job_id 再 start 一次继续执行，任务卡不会分裂成两条。
    """


class JobContext:
    """交给各 service 使用的执行上下文。"""

    def __init__(self, manager: "JobManager", job_id: str, cancel_event: threading.Event):
        self._manager = manager
        self.job_id = job_id
        self._cancel = cancel_event

    @property
    def job(self) -> dict:
        return self._manager.get(self.job_id)

    @property
    def params(self) -> dict:
        return self.job.get("params") or {}

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def phase(self, index: int, message: str = "") -> None:
        self._manager.update(self.job_id, phase=PHASES[index], phase_index=index, message=message)

    def progress(self, message: str, current: int, total: int | None = None, path: str = "") -> None:
        percent = 0
        if total:
            percent = min(int(current / total * 100), 99)
        self._manager.update(
            self.job_id,
            message=message,
            current=current,
            total=total,
            percent=percent,
            current_path=path,
        )

    def tick(self, message: str, current: int, total: int | None = None, path: str = "") -> None:
        """progress 的别名，方便遍历回调直接传。"""
        self.progress(message, current, total, path)

    def set_plan(self, plan: list[dict]) -> None:
        self._manager.update(self.job_id, plan=plan, phase=PHASES[2], phase_index=2)

    def add_action(self, action: dict) -> None:
        job = self.job
        actions = list(job.get("actions") or [])
        actions.append(action)
        self._manager.update(self.job_id, actions=actions, rollbackable=True)

    def add_actions(self, actions: list[dict]) -> None:
        if not actions:
            return
        job = self.job
        merged = list(job.get("actions") or []) + actions
        self._manager.update(self.job_id, actions=merged, rollbackable=True)

    def waiting(self, message: str) -> None:
        self._manager.update(self.job_id, status=JOB_WAITING, phase=PHASES[3], phase_index=3, message=message)

    def sleep(self, seconds: float = 0.02) -> None:
        """限速：给磁盘留 IO 空隙。"""
        fsutil.sleep_short(seconds)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self._queues: dict[str, list[queue.Queue]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._last_emit: dict[str, float] = {}
        self._history: dict[str, list[str]] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="filepilot-job")
        self._load()

    # -- 持久化 -----------------------------------------------------------
    def _load(self) -> None:
        jobs = database.read_jobs(config.DATABASE_FILE, config.MAX_JOBS_KEPT)
        if not jobs:
            jobs = store.load_json(config.JOBS_FILE, [])
            if jobs:
                database.replace_jobs(config.DATABASE_FILE, jobs, config.MAX_JOBS_KEPT)
        for job in jobs:
            if isinstance(job, dict) and job.get("id"):
                # 重启后未完成的任务标记为中断
                if job.get("status") in {JOB_PENDING, JOB_RUNNING, JOB_WAITING}:
                    job["status"] = JOB_FAILED
                    job["error"] = "程序重启，任务已中断"
                # 读入也裁一遍：老库里可能已经躺着几条几 MB 的 dir_sizes（自愈，不删任务）
                self._jobs[job["id"]] = _trim_job(job)
                self._order.append(job["id"])

    def _persist(self) -> None:
        with self._lock:
            ordered = [self._jobs[i] for i in self._order if i in self._jobs]
        # 落库前裁掉 dir_sizes 这类巨型字段：DB 不膨胀，/api/jobs 也不会被带崩
        database.replace_jobs(
            config.DATABASE_FILE,
            [_trim_job(item) for item in ordered[-config.MAX_JOBS_KEPT:]],
            config.MAX_JOBS_KEPT,
        )

    # -- 查询 -------------------------------------------------------------
    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            ordered = [self._jobs[i] for i in self._order if i in self._jobs]
        return list(reversed(ordered))[:limit]

    def active_count(self) -> int:
        return sum(1 for job in self.list(999) if job.get("status") in {JOB_RUNNING, JOB_WAITING, JOB_PENDING})

    # -- 创建与更新 -------------------------------------------------------
    def create(self, job_type: str, title: str, target: str, params: dict | None = None) -> dict:
        job = _blank_job(job_type, title, target, params or {})
        job["event_seq"] = 1
        with self._lock:
            self._jobs[job["id"]] = job
            self._order.append(job["id"])
            self._cancel[job["id"]] = threading.Event()
        self._persist()
        self._emit(job["id"], force=True)
        return job

    def update(self, job_id: str, force: bool = False, **fields: Any) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            if job.get("status") in TERMINAL and fields.get("status") not in (None, job.get("status")):
                fields = {key: value for key, value in fields.items() if key != "status"}
            job["event_seq"] = int(job.get("event_seq") or 0) + 1
            job.update(fields)
            snapshot = dict(job)
        self._emit(job_id, force=force)
        if set(fields) & {"status", "actions", "plan"}:
            self._persist()
        return snapshot

    # -- 订阅（SSE）-------------------------------------------------------
    def subscribe(self, job_id: str) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._queues.setdefault(job_id, []).append(channel)
        return channel

    def unsubscribe(self, job_id: str, channel: queue.Queue) -> None:
        with self._lock:
            channels = self._queues.get(job_id) or []
            if channel in channels:
                channels.remove(channel)

    def _emit(self, job_id: str, force: bool = False) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if not force:
                now = time.time()
                last = self._last_emit.get(job_id, 0)
                # 进度事件节流：至少间隔 0.2 秒，除非是状态变化
                if now - last < 0.2:
                    return
                self._last_emit[job_id] = now
            else:
                self._last_emit[job_id] = time.time()
            payload = json.dumps(job, ensure_ascii=False)
            self._history.setdefault(job_id, []).append(payload)
            self._history[job_id] = self._history[job_id][-128:]
            channels = list(self._queues.get(job_id) or [])

        for channel in channels:
            try:
                channel.put_nowait(payload)
            except queue.Full:
                pass

        if job.get("status") in TERMINAL:
            for channel in channels:
                try:
                    channel.put_nowait(None)
                except queue.Full:
                    pass

    def stream(self, job_id: str, last_event_id: int = 0):
        """SSE 生成器：先推快照，再推增量，任务结束后关闭。"""
        job = self.get(job_id)
        current_seq = int(job.get("event_seq") or 0)
        replayed = False
        if last_event_id:
            with self._lock:
                history = list(self._history.get(job_id) or [])
            for raw in history:
                event = json.loads(raw)
                if int(event.get("event_seq") or 0) > last_event_id:
                    replayed = True
                    yield f"id: {event.get('event_seq', 0)}\ndata: {raw}\n\n"
        if not replayed and current_seq > last_event_id:
            yield f"id: {current_seq}\ndata: {json.dumps(job, ensure_ascii=False)}\n\n"
        if job.get("status") in TERMINAL:
            return

        channel = self.subscribe(job_id)
        try:
            while True:
                try:
                    payload = channel.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if payload is None:
                    final = self.get(job_id)
                    yield f"id: {final.get('event_seq', 0)}\ndata: {json.dumps(final, ensure_ascii=False)}\n\n"
                    return
                event = json.loads(payload)
                yield f"id: {event.get('event_seq', 0)}\ndata: {payload}\n\n"
        finally:
            self.unsubscribe(job_id, channel)

    # -- 执行 -------------------------------------------------------------
    def start(self, job_id: str, worker: Callable[[JobContext], Any]) -> dict:
        job = self.get(job_id)
        with self._lock:
            if job.get("status") == JOB_WAITING:
                # 续跑（例如 Agent 任务确认后继续）：换一条干净的取消信号，
                # 否则上一轮如果点过取消，续跑会立刻被判定为已取消。
                self._cancel[job_id] = threading.Event()
            else:
                self._cancel.setdefault(job_id, threading.Event())

        def runner() -> None:
            ctx = JobContext(self, job_id, self._cancel[job_id])
            self.update(
                job_id, status=JOB_RUNNING, started_at=fsutil.now_iso(),
                message="开始执行", phase=PHASES[0], phase_index=0, force=True,
            )
            try:
                result = worker(ctx) or {}
                ctx.check_cancel()
                self.update(
                    job_id, status=JOB_DONE, phase=PHASES[6], phase_index=6,
                    message="已完成", percent=100, finished_at=fsutil.now_iso(),
                    result=result, force=True,
                )
                store.write_log(
                    job["type"], "成功", job.get("target", ""),
                    job.get("title") or job.get("type_label", ""),
                    job_id=job_id, job_type=job["type"], permission=job.get("permission"),
                    reclaimed=reclaimed_bytes(job["type"], result),
                )
            except (JobCancelled, InterruptedError):
                # InterruptedError 是 fsutil 里统一的"已取消"信号（见 hash_file / copy_file），
                # 不并进来的话，取消一个大文件查重会被当成普通异常 →
                # 任务卡显示「执行失败」而不是「已取消」，用户以为出错了。
                self.update(
                    job_id, status=JOB_CANCELLED, message="已取消",
                    finished_at=fsutil.now_iso(), force=True,
                )
                store.write_log(job["type"], "已取消", job.get("target", ""),
                                note=f"取消来源：{job.get("cancel_reason") or "未标注"}",
                                job_id=job_id, permission=job.get("permission"))
            except JobPaused:
                # 挂起而非结束：同一个 job_id 之后可以再次 start 继续
                self.update(
                    job_id, status=JOB_WAITING, phase=PHASES[3], phase_index=3,
                    message="等待你确认后继续", force=True,
                )
            except Exception as exc:  # noqa: BLE001
                self.update(
                    job_id, status=JOB_FAILED, message="执行失败", error=fsutil.human_error(exc),
                    finished_at=fsutil.now_iso(), force=True,
                )
                store.write_log(job["type"], "失败", job.get("target", ""), fsutil.human_error(exc), job_id=job_id, permission=job.get("permission"))
            finally:
                self._persist()

        self._executor.submit(runner)
        return self.get(job_id)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            event = self._cancel.get(job_id)
            job = self._jobs.get(job_id)
        if event:
            event.set()
        if job:
            self.update(job_id, cancel_requested=True, message="正在取消…", force=True)
        return self.get(job_id)

    # -- 回滚 -------------------------------------------------------------
    def rollback(self, job_id: str) -> dict:
        """把一个任务的写入动作逐个撤销回去。

        这里同样过权限门禁：门禁只写在 `_launch()` 里的话，只读档下**回滚照样能改文件**
        （2026-10-08 审计实测），「只读档不写任何东西」这条不变量就破了。
        """
        permissions.require_write("回滚任务（会把文件搬回原位）")
        job = self.get(job_id)
        actions = list(job.get("actions") or [])
        restored: list[dict] = []
        errors: list[str] = []

        for action in reversed(actions):
            op = action.get("op")
            try:
                if op == "move":
                    src, dst = Path(action["to"]), Path(action["from"])
                    if not src.exists():
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    final = fsutil.ensure_unique(dst) if dst.exists() else dst
                    shutil.move(str(src), str(final))
                    restored.append({"op": "move_back", "path": str(final)})
                elif op == "copy":
                    target = Path(action["to"])
                    if target.exists():
                        target.unlink()
                        restored.append({"op": "remove_copy", "path": str(target)})
                elif op == "trash":
                    trash_path, original = Path(action["to"]), Path(action["from"])
                    if trash_path.exists():
                        original.parent.mkdir(parents=True, exist_ok=True)
                        final = fsutil.ensure_unique(original) if original.exists() else original
                        shutil.move(str(trash_path), str(final))
                        restored.append({"op": "restore", "path": str(final)})
                    entries = [e for e in store.read_trash() if e.get("trash_path") != action["to"]]
                    store.save_trash(entries)
                elif op == "rmdir":
                    target = Path(action["to"])
                    if not target.exists():
                        target.mkdir(parents=True, exist_ok=True)
                        restored.append({"op": "recreate_dir", "path": str(target)})
                elif op == "recycle":
                    # 送进系统回收站的文件无法用工具还原，需去系统回收站找回
                    errors.append(f"{action.get('to')}：已进系统回收站，请到系统回收站还原")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{action.get('to')}: {fsutil.human_error(exc)}")

        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["actions"] = []
                self._jobs[job_id]["rollbackable"] = False
        self._persist()
        self._emit(job_id, force=True)

        store.write_log(job["type"], "已回滚" if not errors else "回滚部分失败", job.get("target", ""),
                        f"恢复 {len(restored)} 项", job_id=job_id)
        return {"job_id": job_id, "restored_count": len(restored), "restored": restored, "errors": errors}


manager = JobManager()
