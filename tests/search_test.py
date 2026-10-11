"""全文检索（B1）的专项测试：建索引、增量、中文命中、白名单护栏。

覆盖的是**服务层**，不需要起后端 —— 这样跑得快，也不碰用户真实的索引库
（`FILEPILOT_DATA_DIR` 指向临时目录）。

用法：
    python tests/search_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ★ 必须**在导入 backend 之前**改数据目录：config 在导入时就定下了 STATE_DIR，
#   晚一步就会把测试索引写进用户真实的那份库里。
_TMP = tempfile.mkdtemp(prefix="_filepilot_search_test")
os.environ["FILEPILOT_DATA_DIR"] = _TMP

# ★ 样本树要建在**另一个**临时目录里，不能建在数据目录下：
#   数据目录是自保护目录，iter_entries 会剪掉它的子目录（根层文件不受影响），
#   于是样本里的子目录会整层消失 —— 表现为"只索引到根目录那几个文件"。
_TREE = tempfile.mkdtemp(prefix="_filepilot_search_tree")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, database  # noqa: E402
from backend.services import index  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'OK' if ok else 'FAIL'}] {name}{(' —— ' + detail) if detail and not ok else ''}")


class FakeCtx:
    """最小 JobContext：索引只用到 params / phase / tick / 取消检查。"""

    def __init__(self, path: str) -> None:
        self.params = {"path": path}
        self.cancelled_flag = False

    def phase(self, _index: int, _message: str = "") -> None:
        pass

    def tick(self, *_args, **_kwargs) -> None:
        pass

    def check_cancel(self) -> None:
        pass

    def cancelled(self) -> bool:
        return self.cancelled_flag


def main() -> int:
    print("=" * 60)
    print("全文检索测试")
    print("=" * 60)

    check("FTS5 可用（当前构建支持全文索引）", database.search_available(config.DATABASE_FILE))

    base = Path(_TREE) / "tree"
    (base / "sub").mkdir(parents=True, exist_ok=True)
    (base / "玉米表型测量报告.md").write_text(
        "这是一份关于玉米表型测量的说明文档，包含 budget 与 report。", encoding="utf-8"
    )
    (base / "sub" / "notes.txt").write_text("玉米 表型 数据 汇总 report budget", encoding="utf-8")
    (base / "sub" / "big.bin").write_bytes(b"\x00" * 5000)          # 二进制，非白名单
    (base / "photo.jpg").write_bytes(b"not really a jpeg")          # 非白名单
    (base / "sub" / "二进制伪装.log").write_bytes(b"\x00\x01\x02")   # 白名单扩展名但内容是二进制

    # ---- 1) 首次建索引 ---------------------------------------------------
    result = index.run_index(FakeCtx(str(base)))
    check("首次建索引收录 5 个文件", result["total_files"] == 5, str(result["total_files"]))
    check("新增 5 条", result["added"] == 5, str(result["added"]))
    check("只有 2 个文件抽到了正文", result["text_files"] == 2, str(result["text_files"]))
    # 按文件名筛选要用 `path` —— `name` 是**带高亮标记**的（`\x01二进制\x02…`），
    # 拿它做 startswith 会永远打不中。
    disguised = [row for row in index.query("二进制")["results"] if row["path"].endswith(".log")]
    check("二进制伪装成 .log 的：按文件名仍搜得到", len(disguised) == 1, str(len(disguised)))
    check("……但正文不进索引（is_text=False）",
          bool(disguised) and disguised[0]["is_text"] is False, str(disguised))

    # ---- 2) 中文：2 字词能命中（默认分词器做不到这一点）-------------------
    hits = index.query("表型")["results"]
    check("中文 2 字词「表型」能命中", len(hits) == 2, str([h["name"] for h in hits]))
    check("中文 2 字词「玉米」能命中", len(index.query("玉米")["results"]) == 2)
    check("不相邻的不能误命中", len(index.query("汇总数据")["results"]) == 0,
          str(index.query("汇总数据")["match"]))
    check("命中片段带高亮标记", database.SEARCH_MARK_OPEN in (hits[0]["snippet"] or ""), hits[0]["snippet"])
    check("文件名还原后没有多余空格", "测量报告.md" in "".join(
        h["name"] for h in hits if h["name"].endswith(".md")), str([h["name"] for h in hits]))

    # ---- 3) 英文与文件名 -------------------------------------------------
    check("英文词命中", len(index.query("report")["results"]) == 2)
    check("文件名可搜", len(index.query("notes")["results"]) >= 1)
    check("搜不到的词返回 0 条", len(index.query("zzzzzz")["results"]) == 0)
    check("空关键词不查库", index.query("")["results"] == [] and index.query("   ")["results"] == [])

    # ---- 4) 增量：没变就全跳过 -------------------------------------------
    again = index.run_index(FakeCtx(str(base)))
    check("无变化时全部跳过", again["added"] == 0 and again["updated"] == 0 and again["skipped"] == 5,
          f"added={again['added']} updated={again['updated']} skipped={again['skipped']}")

    # ---- 5) 增量：改一个只更新一个 ---------------------------------------
    target = base / "sub" / "notes.txt"
    target.write_text("玉米 表型 数据 汇总 report budget 新增一行内容", encoding="utf-8")
    os.utime(target, (1_000_000_000, 1_000_000_000))
    changed = index.run_index(FakeCtx(str(base)))
    check("改一个文件只更新一条", changed["updated"] == 1 and changed["skipped"] == 4,
          f"updated={changed['updated']} skipped={changed['skipped']}")
    check("改完能搜到新内容", len(index.query("新增一行")["results"]) == 1)

    # ---- 6) 增量：删一个能被检出 -----------------------------------------
    (base / "photo.jpg").unlink()
    removed = index.run_index(FakeCtx(str(base)))
    check("删除的文件会被移出索引", removed["removed"] == 1, str(removed["removed"]))
    check("删掉的文件搜不到", len(index.query("photo")["results"]) == 0)

    # ---- 7) 状态与移除 ---------------------------------------------------
    state = index.status()
    check("状态里能看到已索引目录", state["total_files"] == 4, str(state["total_files"]))
    check("移除索引后文件数归零",
          index.remove_root(str(base))["removed"] == 4 and index.status()["total_files"] == 0)

    print()
    print("=" * 60)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
