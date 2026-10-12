"""任务落库时裁掉巨型字段（scan 的 `result.dir_sizes`）。

2026-10-13 实测：用户库里躺着一条 5.5 MB 的扫描结果（`dir_sizes` 44,845 项），
`GET /api/jobs` 带上它就 500，任务中心整个打不开。
`dir_sizes` 前端从来不用（只有点「保存当前为快照」那一刻用，而那条走的是
前端内存里的当次结果），快照 diff 读的也是快照文件 —— 所以裁掉它不伤任何功能。
"""
from backend import database, jobs


def test_persisted_job_drops_dir_sizes_but_keeps_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.config, "DATABASE_FILE", tmp_path / "filepilot.db")
    manager = jobs.JobManager()
    job = manager.create("scan", "扫描 D:\\", "D:\\", {})
    # 真实流程里 result 是和终态一起写进去的（只有 status/actions/plan 会触发落库）
    manager.update(job["id"], status=jobs.JOB_DONE, result={
        "root": "D:\\",
        "total_files": 10,
        "total_size": 100,
        "dir_sizes": {f"D:\\dir{i}": i for i in range(500)},
    })
    # 内存里那份保持完整：SSE 要推给前端，「保存为快照」也要用它
    assert len(manager.get(job["id"])["result"]["dir_sizes"]) == 500
    # 落库那份被裁掉，其它字段一个不少
    stored = database.read_jobs(jobs.config.DATABASE_FILE, 50)
    assert "dir_sizes" not in stored[0]["result"]
    assert stored[0]["result"]["total_files"] == 10
    assert stored[0]["result"]["root"] == "D:\\"


def test_load_trims_existing_bloat_without_dropping_the_job(tmp_path, monkeypatch):
    """老库里已经躺着的巨型记录，读入时也要裁（自愈，但不删任务）。"""
    monkeypatch.setattr(jobs.config, "DATABASE_FILE", tmp_path / "filepilot.db")
    database.replace_jobs(jobs.config.DATABASE_FILE, [{
        "id": "bloated",
        "type": "scan",
        "type_label": "扫描分析",
        "title": "扫描 D:\\",
        "target": "D:\\",
        "status": jobs.JOB_DONE,
        "result": {"root": "D:\\", "total_files": 7, "dir_sizes": {f"D:\\d{i}": i for i in range(300)}},
    }], 50)
    manager = jobs.JobManager()
    loaded = manager.get("bloated")
    assert loaded["title"] == "扫描 D:\\"            # 任务还在
    assert "dir_sizes" not in loaded["result"]       # 只是被裁
    assert loaded["result"]["total_files"] == 7
