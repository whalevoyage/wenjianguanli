"""FilePilot Lite 端到端冒烟测试。

只用标准库，不依赖 pytest。启动后端后运行：

    python tests/smoke_test.py
    FILEPILOT_API=http://127.0.0.1:8000/api python tests/smoke_test.py

会用临时目录造样本，覆盖：扫描 / 目录树 / 整理预览（含导出） / 项目雷达 / 查重 / 垃圾 /
**全文检索** / 迁移规划 / 迁移执行 / 回滚 / 隔离区还原 / 日志 / Agent 本地意图。

⚠️ **必须对着「工作目录 = 本仓库」的后端跑**（dev 或 `uvicorn backend.main:app`）。
「保护目录护栏」那 3 项会把样本建在**仓库根**下（`_smoke_protected`），
断言"项目自身不可写"—— 而**打包版**的"项目自身"是 `release\\Atlas`，
所以对打包版跑时这 3 项必然失败（不是回归，是环境不同）。
用 `FILEPILOT_DATA_DIR=<临时目录>` 起测试后端即可，别拿打包好的 Atlas.exe 当靶子。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.getenv("FILEPILOT_API", "http://127.0.0.1:8000/api")
PASSED: list[str] = []
FAILED: list[str] = []


def call(method: str, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"{method} {path} -> {exc.code} {detail[:200]}") from exc
    return json.loads(raw) if raw else {}


def wait_job(job_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        job = call("GET", f"/jobs/{job_id}")
        last = job
        if job.get("status") in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.3)
    raise TimeoutError(f"任务 {job_id} 超时，最后状态：{last.get('status')} {last.get('message')}")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name}{(' — ' + detail) if detail else ''}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}{(' — ' + detail) if detail else ''}")


def build_fixture(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    (root / "docs" / "empty_dir").mkdir(parents=True)
    (root / "media").mkdir(parents=True)
    (root / "node_modules").mkdir(parents=True)
    (root / "archive").mkdir(parents=True)

    (root / "docs" / "a.txt").write_text("aaa", encoding="utf-8")
    (root / "docs" / "a_copy.txt").write_text("aaa", encoding="utf-8")
    (root / "docs" / "b.txt").write_text("bbb", encoding="utf-8")
    (root / "docs" / "note.log").write_text("log content", encoding="utf-8")
    (root / "docs" / "scratch.tmp").write_text("tmp", encoding="utf-8")
    (root / "docs" / "zero.dat").write_bytes(b"")
    (root / "media" / "big.bin").write_bytes(b"\0" * 1_200_000)
    (root / "archive" / "old.zip").write_text("zipdata", encoding="utf-8")
    (root / "node_modules" / "dep.js").write_text("dep", encoding="utf-8")


def write_png(path: Path, rows: list[bytes]) -> None:
    """用标准库写一张 8bit RGB PNG（冒烟测试坚持零第三方依赖，所以不引 Pillow）。

    rows：每行是长度 = width*3 的 bytes（RGB 顺序）；每行前加一个 filter 字节 0。
    """
    import struct
    import zlib

    height = len(rows)
    width = len(rows[0]) // 3 if rows else 0
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def structured_rows(size: int, seed: int) -> list[bytes]:
    """一张结构化、不对称的 RGB 图（有明暗起伏，dHash 才有信息量；不对称，旋转后指纹会变）。"""
    import random

    rnd = random.Random(seed)
    boxes = [
        (rnd.randrange(size), rnd.randrange(size), rnd.randrange(10, size // 2),
         rnd.randrange(10, size // 2), (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
        for _ in range(6)
    ]
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            r, g, b = 232, 232, 232
            for bx, by, bw, bh, col in boxes:
                if bx <= x < bx + bw and by <= y < by + bh:
                    r, g, b = col
            row += bytes((r, g, b))
        rows.append(bytes(row))
    return rows


def rotate90(rows: list[bytes]) -> list[bytes]:
    """把 rows 表示的图旋转 90°（方向无所谓：四个朝向里总有一个对得上）。"""
    height = len(rows)
    width = len(rows[0]) // 3
    out = []
    for x in range(width - 1, -1, -1):
        row = bytearray()
        for y in range(height):
            row += rows[y][x * 3: x * 3 + 3]
        out.append(bytes(row))
    return out


def main() -> int:
    print(f"FilePilot Lite 冒烟测试 → {BASE}\n")

    health = call("GET", "/health")
    check("健康检查", health.get("status") == "ok", health.get("version", ""))

    config = call("GET", "/config")
    check("读取配置", bool(config.get("drives")) and "junk_rules" in config,
          f"盘符 {config.get('drives')}，排除 {len(config.get('excludes', []))} 项")

    fixture = Path(tempfile.gettempdir()) / "filepilot_smoke"
    build_fixture(fixture)
    print(f"\n样本目录：{fixture}")

    # ---- 扫描 ----
    print("\n[扫描分析]")
    job = wait_job(call("POST", "/scan", {"path": str(fixture)})["job_id"])
    check("扫描任务完成", job.get("status") == "done", job.get("error") or "")
    result = job.get("result") or {}
    check("扫描到 8 个文件（node_modules 被排除）", result.get("total_files") == 8,
          f"实际 {result.get('total_files')}")
    labels = [item.get("label") for item in result.get("category_stats", [])]
    check("类型统计带 label 字段", all(labels) and len(labels) > 0, str(labels))
    percentages = [item.get("percentage") for item in result.get("category_stats", [])]
    check("类型统计带 percentage 字段", any(p and p > 0 for p in percentages), str(percentages))

    # ---- 目录树 ----
    print("\n[空间地图]")
    job = wait_job(call("POST", "/tree", {"path": str(fixture), "depth": 2})["job_id"])
    check("目录树任务完成", job.get("status") == "done", job.get("error") or "")
    tree = (job.get("result") or {}).get("tree") or {}
    check("目录树有子节点", len(tree.get("children") or []) >= 3, f"{len(tree.get('children') or [])} 个")
    check("目录树带体积", (tree.get("size") or 0) > 0, tree.get("size_text", ""))

    # ---- 整理预览（只读，必须一个字节都不动） ----
    print("\n[整理预览]")
    vroot = Path(tempfile.gettempdir()) / "filepilot_smoke_virtual"
    if vroot.exists():
        shutil.rmtree(vroot, ignore_errors=True)
    (vroot / "photo").mkdir(parents=True)
    (vroot / "proj").mkdir(parents=True)
    (vroot / "loose").mkdir(parents=True)
    (vroot / "photo" / "pic.jpg").write_bytes(b"J" * 2048)
    (vroot / "photo" / "clip.mp4").write_bytes(b"M" * 4096)
    (vroot / "loose" / "report.pdf").write_bytes(b"P" * 1024)
    (vroot / "proj" / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    (vroot / "proj" / "main.js").write_bytes(b"console.log(1)" * 100)

    def snapshot(root_dir: Path) -> dict:
        return {
            str(path): (path.stat().st_size, int(path.stat().st_mtime))
            for path in sorted(root_dir.rglob("*"))
        }

    before = snapshot(vroot)

    vjob = wait_job(call("POST", "/virtual/organize", {"path": str(vroot), "group_by": "type"})["job_id"])
    check("整理预览任务完成", vjob.get("status") == "done", vjob.get("error") or "")
    vresult = vjob.get("result") or {}
    check("预览标记为只读", vresult.get("readonly") is True)
    vlabels = [bucket["label"] for bucket in vresult.get("buckets", [])]
    check("按类型识别出图片/视频/文档", {"图片", "视频", "文档"} <= set(vlabels), str(vlabels))
    bucket_total = sum(bucket["size"] for bucket in vresult.get("buckets", []))
    check("各分组体积之和等于扫描总量", bucket_total == vresult.get("scanned_size"),
          f"{bucket_total} vs {vresult.get('scanned_size')}")
    percent_total = round(sum(bucket["percentage"] for bucket in vresult.get("buckets", [])), 1)
    check("分组占比合计约 100%", 99.0 <= percent_total <= 101.0, f"{percent_total}%")

    tjob = wait_job(call("POST", "/virtual/organize", {"path": str(vroot), "group_by": "time"})["job_id"])
    tlabels = [bucket["label"] for bucket in (tjob.get("result") or {}).get("buckets", [])]
    check("按时间分组落在配置档位内",
          bool(tlabels) and all(item in {"本月", "近三个月", "今年", "去年", "更早"} for item in tlabels),
          str(tlabels))

    sjob = wait_job(call("POST", "/virtual/organize", {"path": str(vroot), "group_by": "size"})["job_id"])
    slabels = [bucket["label"] for bucket in (sjob.get("result") or {}).get("buckets", [])]
    check("按体积分组落在配置档位内",
          bool(slabels) and all(
              item in {"小于 1 MB", "1 - 100 MB", "100 MB - 1 GB", "大于 1 GB"} for item in slabels),
          str(slabels))

    pjob = wait_job(call("POST", "/virtual/organize", {"path": str(vroot), "group_by": "project"})["job_id"])
    presult = pjob.get("result") or {}
    plabels = [bucket["label"] for bucket in presult.get("buckets", [])]
    check("按项目识别出项目目录", "proj" in plabels, str(plabels))
    check("非项目文件归入散落文件", "散落文件" in plabels, str(plabels))
    preview_targets = [
        item.get("preview_target")
        for bucket in presult.get("buckets", [])
        for item in bucket.get("items", [])
    ]
    check("给出整理后的目标路径（仅字符串）",
          bool(preview_targets) and all(str(vroot) in (value or "") for value in preview_targets),
          preview_targets[0] if preview_targets else "")

    export_items = [
        {
            "group_label": bucket["label"],
            "name": item["name"],
            "source": item["path"],
            "target": item["preview_target"],
            "size": item["size"],
        }
        for bucket in vresult.get("buckets", [])
        for item in bucket.get("items", [])
    ]
    exported = call("POST", "/virtual/export", {
        "root": str(vroot), "group_by": "type", "items": export_items,
    })
    export_path = Path(exported.get("path", ""))
    check("预览清单可导出 CSV",
          exported.get("success") is True and export_path.exists(),
          f"{exported.get('count')} 行 / {exported.get('size_text')}")
    export_lines = (
        export_path.read_text(encoding="utf-8-sig").strip().splitlines()
        if export_path.exists() else []
    )
    check("导出内容含表头与全部清单行",
          bool(export_lines)
          and export_lines[0].startswith("分组,文件名,当前位置,整理后位置,大小(字节)")
          and len(export_lines) == len(export_items) + 1,
          f"{len(export_lines)} 行 / 清单 {len(export_items)} 条")
    if export_path.exists():
        export_path.unlink()

    check("预览全程未改动任何文件", before == snapshot(vroot), f"{len(before)} 项快照比对")

    # ---- 项目雷达（只读） ----
    print("\n[项目雷达]")
    old_proj = vroot / "oldproj"
    (old_proj / ".git").mkdir(parents=True, exist_ok=True)
    (old_proj / "legacy.js").write_bytes(b"L" * 512)
    stale = time.time() - 400 * 86400
    os.utime(old_proj / "legacy.js", (stale, stale))
    os.utime(old_proj / ".git", (stale, stale))

    radar_before = snapshot(vroot)
    radar_job = wait_job(call("POST", "/projects/scan", {"path": str(vroot)})["job_id"])
    check("项目雷达任务完成", radar_job.get("status") == "done", radar_job.get("error") or "")
    radar = radar_job.get("result") or {}
    check("项目雷达标记为只读", radar.get("readonly") is True)
    found = {item["name"]: item for item in radar.get("projects", [])}
    check("识别出两个项目", {"proj", "oldproj"} <= set(found), str(sorted(found)))
    check("项目带类型标签",
          (found.get("proj") or {}).get("shell") == "Node"
          and (found.get("oldproj") or {}).get("shell") == "Git 仓库",
          f"{found.get('proj', {}).get('shell')} / {found.get('oldproj', {}).get('shell')}")
    expected_size = len('{"name":"demo"}') + len(b"console.log(1)") * 100
    check("项目体积与文件数正确",
          (found.get("proj") or {}).get("file_count") == 2
          and (found.get("proj") or {}).get("size") == expected_size,
          f"{found.get('proj', {}).get('file_count')} 个 / {found.get('proj', {}).get('size_text')}")
    check("长期未动的项目被判定为僵尸",
          (found.get("oldproj") or {}).get("is_zombie") is True
          and (found.get("oldproj") or {}).get("inactive_days", 0) > 300,
          f"{found.get('oldproj', {}).get('inactive_days')} 天没动")
    check("僵尸计数与占用对得上",
          radar.get("zombie_count") == 1 and radar.get("zombie_size") == 512,
          f"{radar.get('zombie_count')} 个 / {radar.get('zombie_size_text')}")
    check("存活项目不被误判为僵尸", (found.get("proj") or {}).get("is_zombie") is False)
    check("项目雷达未改动任何文件", radar_before == snapshot(vroot), f"{len(radar_before)} 项快照比对")

    shutil.rmtree(vroot, ignore_errors=True)

    # ---- 查重 ----
    print("\n[重复文件]")
    job = wait_job(call("POST", "/duplicates/scan", {"path": str(fixture)})["job_id"])
    check("查重任务完成", job.get("status") == "done", job.get("error") or "")
    result = job.get("result") or {}
    check("找到 1 组重复", result.get("group_count") == 1, f"实际 {result.get('group_count')}")
    check("按大小淘汰了唯一文件", (result.get("eliminated_by_size") or 0) > 0,
          f"淘汰 {result.get('eliminated_by_size')} 个")
    group = (result.get("groups") or [{}])[0]
    check("重复组字段齐全", bool(group.get("sha256")) and group.get("count") == 2
          and group.get("sha256_short") and group.get("file_count") == 2)

    # ---- 垃圾文件 ----
    print("\n[垃圾文件]")
    job = wait_job(call("POST", "/junk/scan", {"path": str(fixture)})["job_id"])
    check("垃圾任务完成", job.get("status") == "done", job.get("error") or "")
    result = job.get("result") or {}
    levels = {item["level"]: item for item in result.get("levels", [])}
    check("三级分级齐全", set(levels) == {"safe", "confirm", "caution"}, str(list(levels)))
    safe_names = {item["name"] for item in levels.get("safe", {}).get("items", [])}
    check("识别出日志/临时/0字节/空目录",
          {"note.log", "scratch.tmp", "zero.dat", "empty_dir"} <= safe_names, str(sorted(safe_names)))
    check("safe 级默认有可释放体积", (result.get("safe_size") or 0) >= 0, result.get("safe_size_text", ""))

    # ---- 全文检索 ----
    # ★ 用**另一个**样本目录，不去动 build_fixture 那个 —— 上面扫描/查重/垃圾/迁移的断言
    #   都写死了文件数与条数，往里加一个文件就会全崩。
    print("\n[全文检索]")
    sdir = fixture.parent / "filepilot_smoke_search"
    shutil.rmtree(sdir, ignore_errors=True)
    sdir.mkdir(parents=True)
    (sdir / "玉米表型测量报告.md").write_text(
        "这是一份关于玉米表型测量的说明文档，含 budget 与 report。", encoding="utf-8")
    (sdir / "notes.txt").write_text("玉米 表型 数据 汇总 report budget", encoding="utf-8")
    (sdir / "blob.bin").write_bytes(b"\0" * 4096)

    status = call("GET", "/search/status")
    check("检索状态可读，且当前构建支持 FTS5", status.get("enabled") is True, str(status)[:80])

    idx_job = wait_job(call("POST", "/search/index", {"path": str(sdir)})["job_id"])
    check("建索引任务完成", idx_job.get("status") == "done", idx_job.get("error") or "")
    idx = idx_job.get("result") or {}
    check("收录 3 个文件", idx.get("total_files") == 3, f"实际 {idx.get('total_files')}")
    check("其中 2 个抽到了正文", idx.get("text_files") == 2, f"实际 {idx.get('text_files')}")

    again = wait_job(call("POST", "/search/index", {"path": str(sdir)})["job_id"])
    check("再建一次全部跳过（增量生效）", (again.get("result") or {}).get("skipped") == 3,
          f"skipped={(again.get('result') or {}).get('skipped')}")

    hit = call("GET", "/search?q=" + urllib.parse.quote("表型"))
    check("中文 2 字词能命中", hit.get("total") == 2, f"实际 {hit.get('total')}")
    check("命中结果字段齐全", bool((hit.get("results") or [{}])[0].get("path"))
          and "\u0001" in ((hit.get("results") or [{}])[0].get("snippet") or ""))
    check("英文词能命中", call("GET", "/search?q=report").get("total") == 2)
    check("搜不到时如实返回 0 条", call("GET", "/search?q=zzzzzz").get("total") == 0)
    check("状态里能看到这个目录",
          any(r.get("path") == str(sdir) for r in call("GET", "/search/status").get("roots", [])))

    removed = call("POST", "/search/remove", {"path": str(sdir)})
    check("移除索引后归零", removed.get("removed") == 3
          and call("GET", "/search?q=" + urllib.parse.quote("表型")).get("total") == 0,
          f"removed={removed.get('removed')}")
    shutil.rmtree(sdir, ignore_errors=True)

    # ---- 相似图片（含旋转容忍）----
    # ★ 单独一个目录：不污染 fixture 的文件数断言
    print("\n[相似图片]")
    simdir = fixture.parent / "filepilot_smoke_similar"
    shutil.rmtree(simdir, ignore_errors=True)
    simdir.mkdir(parents=True)
    base_rows = structured_rows(96, 7)
    write_png(simdir / "orig.png", base_rows)
    write_png(simdir / "rot90.png", rotate90(base_rows))
    write_png(simdir / "other.png", structured_rows(96, 99))

    job = wait_job(call("POST", "/similar/scan", {"path": str(simdir), "min_size": 1})["job_id"])
    check("相似图任务完成", job.get("status") == "done", job.get("error") or "")
    result = job.get("result") or {}
    if result.get("available") is False:
        print(f"  [SKIP] 相似图片 —— {result.get('reason')}")
    else:
        check("相似图结果带 rotations 字段（默认 False）", result.get("rotations") is False,
              str(result.get("rotations")))
        names_off = {f["name"] for g in result.get("groups", []) for f in g["files"]}
        check("默认关：旋转副本不被并组", "rot90.png" not in names_off, str(sorted(names_off)))

        job2 = wait_job(call("POST", "/similar/scan",
                             {"path": str(simdir), "min_size": 1, "rotations": True})["job_id"])
        check("旋转容忍任务完成", job2.get("status") == "done", job2.get("error") or "")
        r2 = job2.get("result") or {}
        check("开启后 rotations=True", r2.get("rotations") is True)
        groups2 = [{f["name"] for f in g["files"]} for g in r2.get("groups", [])]
        check("开启后 orig 与 rot90 并组", any({"orig.png", "rot90.png"} <= g for g in groups2), str(groups2))
    shutil.rmtree(simdir, ignore_errors=True)

    # ---- 项目备份 + 还原核对 ----
    print("\n[项目备份]")
    bkdir = fixture.parent / "filepilot_smoke_backup_src"
    shutil.rmtree(bkdir, ignore_errors=True)
    (bkdir / "sub").mkdir(parents=True)
    (bkdir / "hello.txt").write_text("hello backup", encoding="utf-8")
    (bkdir / "sub" / "data.txt").write_text("data", encoding="utf-8")

    job = wait_job(call("POST", "/backup", {"source_dir": str(bkdir)})["job_id"])
    check("备份任务完成", job.get("status") == "done", job.get("error") or "")
    bres = job.get("result") or {}
    zip_path = bres.get("zip_path") or ""
    check("产出 ZIP 且文件数正确", bool(zip_path) and bres.get("file_count") == 2,
          f"{bres.get('file_count')} 个 → {os.path.basename(zip_path)}")

    vfy = call("POST", "/backup/verify", {"zip_path": zip_path, "mode": "verify"})
    check("备份核对 verify 通过", vfy.get("ok") is True,
          f"{vfy.get('verified_count')}/{vfy.get('file_count')} 个成员")
    vfy2 = call("POST", "/backup/verify", {"zip_path": zip_path, "mode": "restore_test"})
    check("备份核对 restore_test 通过", vfy2.get("ok") is True, f"{vfy2.get('elapsed_seconds')}s")

    try:
        call("POST", "/backup/verify", {"zip_path": str(bkdir / "nope.zip")})
        check("缺失备份如实报错", False, "竟然没报错")
    except RuntimeError as exc:
        check("缺失备份如实报错（400 而非 500）", "400" in str(exc), str(exc)[:70])
    shutil.rmtree(bkdir, ignore_errors=True)

    # ---- 多来源（B8：四页 + 检索 / 项目雷达）----
    print("\n[多来源]")
    msa = fixture.parent / "filepilot_smoke_ms_a"
    msb = fixture.parent / "filepilot_smoke_ms_b"
    for p in (msa, msb):
        shutil.rmtree(p, ignore_errors=True)
    (msa / "proj_a").mkdir(parents=True)
    (msb / "proj_b").mkdir(parents=True)
    (msa / "proj_a" / "package.json").write_text("{}", encoding="utf-8")
    (msb / "proj_b" / "package.json").write_text("{}", encoding="utf-8")
    payload = "cross source duplicate payload\n"
    (msa / "same.txt").write_text(payload, encoding="utf-8")
    (msb / "same.txt").write_text(payload, encoding="utf-8")

    job = wait_job(call("POST", "/duplicates/scan", {"paths": [str(msa), str(msb)]})["job_id"])
    check("多源查重完成", job.get("status") == "done", job.get("error") or "")
    res = job.get("result") or {}
    check("多源查重 source_count=2", res.get("source_count") == 2, str(res.get("source_count")))
    cross = [g for g in res.get("groups", []) if g.get("cross_source")]
    check("跨目录重复被识别且排最前",
          bool(cross) and (res.get("groups") or [{}])[0].get("cross_source") is True,
          f"{len(cross)} 组跨目录")

    pj = wait_job(call("POST", "/projects/scan", {"paths": [str(msa), str(msb)]})["job_id"])
    check("多源项目雷达完成", pj.get("status") == "done", pj.get("error") or "")
    pres = pj.get("result") or {}
    check("多源项目雷达识别到两个项目", pres.get("project_count") == 2 and pres.get("source_count") == 2,
          f"项目 {pres.get('project_count')} / 来源 {pres.get('source_count')}")

    ij = wait_job(call("POST", "/search/index", {"paths": [str(msa), str(msb)]})["job_id"])
    check("多源建索引完成", ij.get("status") == "done", ij.get("error") or "")
    ires = ij.get("result") or {}
    check("多源建索引覆盖两个来源", ires.get("source_count") == 2 and len(ires.get("roots", [])) == 2,
          str(ires.get("roots")))
    st = call("GET", "/search/status")
    ms_roots = [r for r in st.get("roots", []) if r.get("path") in (str(msa), str(msb))]
    check("两个来源都进了索引状态", len(ms_roots) == 2, f"{len(ms_roots)} 个")
    # 清理：把两个来源从索引里移除，别在测试库里留根
    for p in (msa, msb):
        call("POST", "/search/remove", {"path": str(p)})
    for p in (msa, msb):
        shutil.rmtree(p, ignore_errors=True)

    # ---- 迁移 ----
    print("\n[跨盘迁移]")
    target = fixture.parent / "filepilot_smoke_target"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    plan_job = wait_job(call("POST", "/migrate", {
        "source_dir": str(fixture / "docs"), "target_dir": str(target), "dry_run": True,
    })["job_id"])
    check("生成迁移计划", plan_job.get("status") == "done", plan_job.get("error") or "")
    plan = (plan_job.get("result") or {}).get("plan") or []
    check("计划条数正确", len(plan) == 6, f"{len(plan)} 条")
    check("计划不落盘（源文件还在）", (fixture / "docs" / "b.txt").exists())

    # 同大小但内容不同：绝不允许只比大小就删源（回归用例）
    conflict_src = fixture.parent / "filepilot_smoke_conflict_src"
    conflict_dst = fixture.parent / "filepilot_smoke_conflict_dst"
    for folder in (conflict_src, conflict_dst):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    conflict_src.mkdir()
    conflict_dst.mkdir()
    (conflict_src / "same.txt").write_bytes(b"A" * 64)
    (conflict_dst / "same.txt").write_bytes(b"B" * 64)
    conflict_job = wait_job(call("POST", "/migrate", {
        "source_dir": str(conflict_src), "target_dir": str(conflict_dst),
        "dry_run": False, "mode": "move",
    })["job_id"])
    check("同大小冲突任务完成", conflict_job.get("status") == "done",
          conflict_job.get("error") or "")
    check("同大小不同内容：原目标不被覆盖",
          (conflict_dst / "same.txt").read_bytes() == b"B" * 64)
    check("同大小不同内容：另存副本保留数据",
          (conflict_dst / "same_1.txt").read_bytes() == b"A" * 64)
    check("冲突场景源文件校验通过后才迁出",
          not (conflict_src / "same.txt").exists())
    shutil.rmtree(conflict_src, ignore_errors=True)
    shutil.rmtree(conflict_dst, ignore_errors=True)

    run_job = wait_job(call("POST", "/migrate", {
        "source_dir": str(fixture / "docs"), "target_dir": str(target),
        "dry_run": False, "mode": "move", "items": plan,
    })["job_id"])
    check("迁移执行完成", run_job.get("status") == "done", run_job.get("error") or "")
    run_result = run_job.get("result") or {}
    check("校验通过数量等于复制数量",
          run_result.get("verified_count") == run_result.get("copied_count"),
          f"拷贝 {run_result.get('copied_count')} / 校验 {run_result.get('verified_count')}")
    check("源文件已删除", not (fixture / "docs" / "b.txt").exists())
    check("目标文件存在", (target / "b.txt").exists())
    check("任务可回滚", bool(run_job.get("rollbackable")) and len(run_job.get("actions") or []) == 6,
          f"{len(run_job.get('actions') or [])} 条动作记录")

    rollback = call("POST", f"/jobs/{run_job['id']}/rollback")
    check("回滚成功", rollback.get("restored_count") == 6 and not rollback.get("errors"),
          f"恢复 {rollback.get('restored_count')} 项")
    check("文件已回到原位", (fixture / "docs" / "b.txt").exists())
    check("目标副本已清除", not (target / "b.txt").exists())

    # ---- 保护目录护栏：移动不得触碰桌面 / .workbuddy / 项目自身 ----
    print("\n[保护目录护栏]")
    guard_src = Path(__file__).resolve().parents[1] / "_smoke_protected"
    guard_dst = fixture.parent / "filepilot_smoke_guard_target"
    for folder in (guard_src, guard_dst):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    guard_src.mkdir(parents=True, exist_ok=True)
    (guard_src / "keep.txt").write_text("keep", encoding="utf-8")

    guard_job = wait_job(call("POST", "/migrate", {
        "source_dir": str(guard_src), "target_dir": str(guard_dst),
        "dry_run": False, "mode": "move",
        "items": [{
            "source": str(guard_src / "keep.txt"),
            "target": str(guard_dst / "keep.txt"),
            "size": 4,
        }],
    })["job_id"])
    guard_result = guard_job.get("result") or {}
    check("保护目录内的文件被拒绝移动",
          guard_result.get("skipped_protected") == 1 and guard_result.get("item_count") == 0,
          f"跳过 {guard_result.get('skipped_protected')} 项 / 清单 {guard_result.get('item_count')} 项")
    check("保护目录内的文件原样保留", (guard_src / "keep.txt").exists())
    check("保护目录的内容没有被复制出去",
          not (guard_dst / "keep.txt").exists())
    shutil.rmtree(guard_src, ignore_errors=True)
    shutil.rmtree(guard_dst, ignore_errors=True)

    # ---- 清理 + 隔离区 ----
    print("\n[清理与回收站]")
    junk_job = wait_job(call("POST", "/junk/scan", {"path": str(fixture)})["job_id"])
    junk_result = junk_job.get("result") or {}
    safe_items = []
    for level in junk_result.get("levels", []):
        if level["level"] == "safe":
            safe_items = [{"path": i["path"], "is_dir": i["is_dir"], "rule": i["rule"], "size": i["size"]}
                          for i in level["items"]]
    check("取到待清理清单", len(safe_items) >= 4, f"{len(safe_items)} 项")

    clean_job = wait_job(call("POST", "/clean", {"items": safe_items, "mode": "isolate"})["job_id"])
    check("清理任务完成", clean_job.get("status") == "done", clean_job.get("error") or "")
    check("文件已离开原目录", not (fixture / "docs" / "note.log").exists())

    trash = call("GET", "/trash")
    # 空目录是直接 rmdir，不进隔离区，所以隔离区条目数 = 非空目录项目数
    expect_trash = len([item for item in safe_items if item["rule"] != "empty_dir"])
    check("隔离区有记录", trash.get("count") == expect_trash,
          f"{trash.get('count')} 条 / {trash.get('total_size_text')}")

    ids = [entry["id"] for entry in trash.get("entries", [])]
    restored = call("POST", "/trash/restore", {"ids": ids})
    check("从隔离区还原", restored.get("restored_count") == len(ids), str(restored.get("errors")))
    check("文件已回到原位", (fixture / "docs" / "note.log").exists())

    # ---- 日志 ----
    print("\n[操作日志]")
    logs = call("GET", "/logs")
    check("日志有记录", logs.get("count", 0) > 0, f"{logs.get('count')} 条，来源 {logs.get('source')}")
    check("日志字段规范",
          all({"time", "type", "result", "path", "note"} <= set(item) for item in logs.get("logs", [])))
    exported = call("POST", "/logs/export")
    check("日志可导出", exported.get("success") is True, exported.get("path", ""))
    # ---- 操作流水（Phase 8）----
    # 与操作日志的分工：日志是"跑过什么"，流水是"能撤销什么"。
    print("\n[操作流水]")
    journal_data = call("GET", "/journal")
    entries = journal_data.get("entries") or []
    check("流水有记录", bool(entries), f"{len(entries)} 条")
    check("流水字段规范",
          all({"id", "at", "type", "source", "done", "undoable", "undone"} <= set(item)
              for item in entries))
    stats = journal_data.get("stats") or {}
    check("流水有界且有统计",
          stats.get("limit") == 500 and "undoable" in stats, str(stats))
    # 刚做过清理 + 还原，应该有一条可撤销或已撤销的 clean 记录
    check("流水记录了清理动作",
          any(item.get("type") == "clean" for item in entries),
          ", ".join(sorted({item.get("type", "") for item in entries})))


    # ---- 总览 ----
    print("\n[首页总览]")
    overview = call("GET", "/overview")
    check("总览含磁盘信息", len(overview.get("disks") or []) >= 1,
          ", ".join(f"{d['letter']}: {d['percent']}%" for d in overview.get("disks", [])))
    check("总览含任务与隔离区", "jobs" in overview and "trash" in overview,
          f"任务 {overview.get('jobs', {}).get('total')} 个")

    # ---- Agent ----
    print("\n[Agent 助手]")
    status = call("GET", "/agent/status")
    check("Agent 状态可读", "configured" in status, status.get("provider", ""))
    reply = call("POST", "/agent/chat", {"message": "帮我清理 C 盘垃圾文件"})
    check("本地意图零 token 命中", reply.get("source") == "local" and reply.get("tokens") == 0,
          f"{reply.get('source')}，{reply.get('tokens')} token")
    check("理解出的动作正确", (reply.get("plan") or {}).get("action") == "junk_scan",
          str((reply.get("plan") or {}).get("action")))
    reply2 = call("POST", "/agent/chat", {"message": "把 D:\\大文件 搬到 E:\\归档"})
    check("迁移意图识别", (reply2.get("plan") or {}).get("action") == "migrate_plan",
          json.dumps((reply2.get("plan") or {}).get("params"), ensure_ascii=False))
    reply3 = call("POST", "/agent/chat", {"message": "今天天气怎么样"})
    plan3_none = (reply3.get("plan") or {}).get("action") == "none"
    if status.get("configured"):
        # 配置了 Key：本地认不出应转 DeepSeek，且模型必须如实说"认不出"，不许编造动作
        check("认不出时如实转 DeepSeek（不编造）",
              reply3.get("source") == "deepseek" and plan3_none)
    else:
        # 没配 Key：本地认不出时必须**如实追问**，且不能编造任何可执行动作。
        #
        # 这里刻意不校验 source —— 曾经写成 source == "none"，是把 /agent/status 里
        # 「Key 来源」那个字段混过来了；实际的回复 source 是 "local"（界面显示「本地规则」）。
        # 而这条断言在本机永远走不到（配了 Key 就走 if 分支），所以一直没被发现。
        # 真正要守住的是「不编造」：没有可执行动作、给的是追问、没有凭空生出的执行步骤。
        check("认不出时如实说明（不编造）",
              plan3_none
              and reply3.get("type") == "clarify"
              and not (reply3.get("steps") or []))

    if not status.get("configured"):
        print("  [SKIP] DeepSeek 实调 —— 未配置 DEEPSEEK_API_KEY")

    # ---- 收尾 ----
    shutil.rmtree(fixture, ignore_errors=True)
    shutil.rmtree(target, ignore_errors=True)

    print("\n" + "=" * 56)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name in FAILED:
            print(f"  ✗ {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\n测试中断：{exc}")
        sys.exit(2)
