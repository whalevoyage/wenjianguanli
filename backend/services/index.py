"""B1 全文检索：给指定目录建 SQLite FTS5 索引，支持增量更新与即时检索。

完整方案见 `docs/SEARCH_PLAN.md`。这里只记**代码里必须知道的三件事**：

1. **中文必须逐字预切。** SQLite FTS5 默认的 `unicode61` 会把一整段中文当成**一个 token**
   —— 实测 `MATCH '表型'` / `'玉米'` 全都命中不了（只有整串能命中），`trigram` 又只对
   **≥3 字**的查询有效。所以写入和查询前都过一遍 `_split_cjk()`，把汉字逐个用空格隔开，
   查询时再拼成**相邻短语**（`"表 型"`）—— 2 字词才搜得到。

2. **增量靠 (size, mtime) 比对。** 两个都没变就完全跳过（既不重读文件、也不动 FTS 行）。
   已有条目一次性读进内存比对，绝不逐文件查库。

3. **索引只读用户文件。** 不调用 `permissions.require_write()`，因此「只读」档照常放行 ——
   索引库写在项目自己的状态目录里，属于内部状态，不是用户文件。

绝不引第三方依赖：分词、抽取、编码回退全是标准库。
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime
from .. import config, database, fsutil
from ..jobs import JobContext

# CJK（含假名、扩展区 A、兼容表意文字）：这些字符要逐个拆开
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 还原 _split_cjk 插入的字间空格。
# 只认「挨着汉字」的空格（前后任一即可）—— 只处理"夹在两个汉字之间"是不够的：
# 那样 `报告.md` 里的 `告 .md` 会留一个空格，文件名显示成 `测量报告 .md`。
# 只吃空格与制表符、**不吃换行**，免得正文片段被并成一大坨。
_CJK_GAP = re.compile(
    r"(?<=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])[ \t]+"
    r"|"
    r"[ \t]+(?=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])"
)
# FTS5 查询语法里的运算符与引号：用户输入里出现就丢掉，避免拼出非法 MATCH 表达式
_QUERY_NOISE = re.compile(r"[\"\*\(\)\:\^\-\+~<>{}&|]")

# 一次索引最多新处理多少个文件（超出如实标 truncated，不假装全部完成）
_BATCH = 400


def _split_cjk(text: str) -> str:
    """把 CJK 逐字用空格分隔，供 FTS5 建索引；ASCII/数字串原样保留。"""
    return _CJK.sub(lambda m: f" {m.group(0)} ", text).strip()


def _restore(text: str) -> str:
    """把 `_split_cjk` 插入的字间空格去掉，还原成正常可读的文本。"""
    return _CJK_GAP.sub("", text).strip()


def build_match(query: str) -> str | None:
    """把用户输入拼成安全的 FTS5 MATCH 表达式。

    按空白切成若干个「词」，词的组合用 `AND`：
    - 含中文的词 → 拆成单字后组成**相邻短语** `"表 型"`。用短语而不是 `A AND B`，
      是因为后者只要求两个字都出现，连「表中型态」这种**不相邻**的也会误命中（实测过）。
    - 纯 ASCII 词 → 原样小写后加引号（避免被当成 FTS 运算符）。
    """
    terms: list[str] = []
    for raw in _QUERY_NOISE.sub(" ", query or "").split():
        if _CJK.search(raw):
            chars = [char for char in _split_cjk(raw).split() if char]
            if chars:
                terms.append('"' + " ".join(chars) + '"')
        else:
            terms.append('"' + raw.lower() + '"')
    return " AND ".join(terms) if terms else None


# ---------------------------------------------------------------------------
# 正文抽取
# ---------------------------------------------------------------------------

def _read_text(path: str, size: int) -> str | None:
    """读一个文件的正文；读不了/不是文本就返回 None（仍然只索引文件名）。

    三层护栏：扩展名白名单（调用方判）→ 大小上限 → 二进制嗅探 → 多编码回退。
    """
    if size > config.SEARCH_MAX_FILE_BYTES:
        return None
    try:
        with open(path, "rb") as stream:
            raw = stream.read(config.SEARCH_MAX_FILE_BYTES + 1)
    except OSError:
        return None
    if not raw:
        return None
    # 二进制嗅探：前 8KB 里出现 NUL 基本可以断定不是文本
    if b"\x00" in raw[:8192]:
        return None
    # 中文 Windows 上 GBK 文本很常见，utf-8-sig 是带 BOM 的
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# 建 / 更新索引（Job）
# ---------------------------------------------------------------------------

def _index_one_root(ctx: JobContext, root, excludes, max_depth: int, label: str) -> dict:
    """给单个根建 / 更新索引，返回该根的明细。

    索引是**按 root_id 分桶**的（search_root 一张表存多个根），所以多来源时逐个根各跑一遍，
    每个根仍管自己的 state / 自己的 root_id —— 这样「移除某个根」依旧只影响那个根。
    """
    root_id = str(root)
    ctx.phase(0, f"{label}正在读取 {root} 的现有索引")
    state = database.search_state(config.DATABASE_FILE, root_id)

    records: list[dict] = []
    seen: set[str] = set()
    added = updated = skipped = text_files = 0
    truncated = False

    for entry in fsutil.iter_entries(
        root, excludes=excludes, max_depth=max_depth,
        progress=ctx.tick, cancelled=ctx.cancelled,
    ):
        ctx.check_cancel()
        try:
            stat = entry.stat()
        except OSError:
            continue
        size, mtime, path = stat.st_size, stat.st_mtime, entry.path
        seen.add(path)

        previous = state.get(path)
        if previous and previous[1] == size and previous[2] == mtime:
            # ★ 没变过，一个字节都不重读 —— 绝大多数文件都停在这一行
            skipped += 1
            continue
        if added + updated >= config.SEARCH_FILE_LIMIT:
            truncated = True
            continue

        name = entry.name
        ext = os.path.splitext(name)[1].lower()
        body = _read_text(path, size) if ext in config.SEARCH_TEXT_EXTS else None
        is_text = body is not None
        if is_text:
            text_files += 1
        records.append({
            "rowid": previous[0] if previous else None,
            "path": path,
            "name": name,
            "ext": ext,
            "size": size,
            "mtime": mtime,
            "is_text": 1 if is_text else 0,
            "name_tokens": _split_cjk(name),
            "body_tokens": _split_cjk(body) if body else "",
        })
        if previous:
            updated += 1
        else:
            added += 1

        if len(records) >= _BATCH:
            database.search_apply(config.DATABASE_FILE, root_id, str(root), records, [], fsutil.now_iso())
            records = []
            ctx.phase(1, f"{label}正在写入索引 —— 新增 {added} · 更新 {updated} · 跳过 {skipped}")

    # 索引里有、这一轮没见到的 = 文件已被删/移走
    gone = [path for path in state if path not in seen]

    ctx.phase(1, f"{label}正在落盘")
    database.search_apply(config.DATABASE_FILE, root_id, str(root), records, gone, fsutil.now_iso())
    return {
        "root": str(root),
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "removed": len(gone),
        "text_files": text_files,
        "total_files": len(seen),
        "truncated": truncated,
    }


def run_index(ctx: JobContext) -> dict:
    """对一个或多个目录建立 / 更新检索索引。**只读取文件**，不移动不删除任何东西。"""
    params = ctx.params
    roots, notes = fsutil.resolve_sources(params)
    excludes = params.get("excludes")
    max_depth = params.get("max_depth") or 0
    enabled = database.search_available(config.DATABASE_FILE)
    started = time.perf_counter()

    per_root: list[dict] = []
    for index, root in enumerate(roots):
        if ctx.cancelled():
            break
        label = "" if len(roots) == 1 else f"[{index + 1}/{len(roots)}] "
        per_root.append(_index_one_root(ctx, root, excludes, max_depth, label))

    duration = round(time.perf_counter() - started, 2)
    return {
        "root": str(roots[0]),
        "roots": [str(root) for root in roots],
        "source_count": len(roots),
        "notes": notes,
        "indexed_at": fsutil.now_iso(),
        "added": sum(item["added"] for item in per_root),
        "updated": sum(item["updated"] for item in per_root),
        "skipped": sum(item["skipped"] for item in per_root),
        "removed": sum(item["removed"] for item in per_root),
        "text_files": sum(item["text_files"] for item in per_root),
        "total_files": sum(item["total_files"] for item in per_root),
        "duration_seconds": duration,
        "truncated": any(item["truncated"] for item in per_root),
        "search_enabled": enabled,
        "per_root": per_root,
    }


# ---------------------------------------------------------------------------
# 检索（即时，不做成 Job）
# ---------------------------------------------------------------------------

def query(keyword: str, limit: int | None = None, root_id: str = "") -> dict:
    """按关键词检索。**同步即时返回** —— 检索要的是敲完就出结果，
    套一层 Job + SSE 只会平白加延迟（Job 是给"耗时长、要进度、要中断"的任务用的）。"""
    match = build_match(keyword)
    enabled = database.search_available(config.DATABASE_FILE)
    if not match or not enabled:
        return {
            "query": keyword, "match": match or "", "enabled": enabled,
            "results": [], "total": 0, "truncated": False, "took_ms": 0.0,
            "reason": "" if match else "请输入要查找的关键词",
        }

    limit = max(1, min(int(limit or config.SEARCH_RESULT_LIMIT), 500))
    started = time.perf_counter()
    rows = database.search_query(config.DATABASE_FILE, match, limit, root_id)
    took_ms = round((time.perf_counter() - started) * 1000, 1)

    results = []
    for row in rows:
        name_hl = _restore(row.get("name_hl") or row.get("name") or "")
        body_hl = _restore(row.get("body_hl") or "")
        size = int(row.get("size") or 0)
        mtime = float(row.get("mtime") or 0)
        results.append({
            # 高亮用标记字符分段（前端按它切段渲染，不走 v-html）
            "name": name_hl,
            "snippet": body_hl,
            "path": row.get("path", ""),
            "directory": os.path.dirname(row.get("path", "")),
            "ext": row.get("ext", ""),
            "size": size,
            "size_text": fsutil.format_size(size),
            "mtime": mtime,
            "modified_time": fsutil.format_mtime(mtime) if mtime else "—",
            "is_text": bool(row.get("is_text")),
        })

    return {
        "query": keyword,
        "match": match,
        "enabled": enabled,
        "results": results,
        "total": len(results),
        "truncated": len(results) >= limit,
        "took_ms": took_ms,
        "reason": "",
    }


def _age_days(stamp: str) -> int | None:
    """索引时间过去了多少天；解析不出来返回 None（不知道就不猜）。"""
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return max(0, int((datetime.now().astimezone() - then).total_seconds() // 86400))


def status() -> dict:
    """索引现状：有没有可用的 FTS5、已索引哪些目录、一共多少文件、有没有过期的。

    **只报"过期"，不自动重建** —— 重建是个可能跑很久的任务，
    自用工具不该在用户没点过的情况下自己发起。
    """
    roots = database.search_roots(config.DATABASE_FILE)
    stale_days = config.SEARCH_STALE_DAYS
    for root in roots:
        root["age_days"] = _age_days(root.get("indexed_at") or "")
        root["stale"] = root["age_days"] is not None and root["age_days"] >= stale_days
    return {
        "enabled": database.search_available(config.DATABASE_FILE),
        "roots": roots,
        "total_files": sum(int(root.get("file_count") or 0) for root in roots),
        "stale_days": stale_days,
        "any_stale": any(root["stale"] for root in roots),
    }


def reveal(value: str) -> dict:
    """在系统的资源管理器里定位到这个文件（选中它）。

    护栏：**只允许定位已经索引过的文件** —— 这个接口会去调系统 shell，
    不能被当成"探任意路径是否存在"的通道。
    """
    target = fsutil.normalize(value)
    if not target.exists():
        raise FileNotFoundError(f"文件不存在：{target}")
    if not database.search_known(config.DATABASE_FILE, str(target)):
        raise ValueError("这个文件不在当前索引里 —— 先给它所在的目录建一次索引")
    # 只有 Windows 形态会走到这里（项目只有 Windows 版）
    subprocess.Popen(["explorer", "/select,", str(target)])
    return {"ok": True, "path": str(target)}


def remove_root(value: str) -> dict:
    """把一个目录的索引整份移除。

    用 `normalize` 而不是 `resolve_dir`：目录可能已经被删掉了，
    「忘记它的索引」这件事仍然必须能做。
    """
    root_id = str(fsutil.normalize(value))
    removed = database.search_forget_root(config.DATABASE_FILE, root_id)
    return {"removed": removed, "root": root_id}
