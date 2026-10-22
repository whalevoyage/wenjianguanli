"""Agent 的本地意图规则（零 token 路径）。

Phase 6 从 `agent.py` 拆出来的第二块：`parse_local` 是那里最大的单个函数
（272 行，全靠正则命中固定话术），配套还有几个正则常量和两个参数抽取辅助。

这一块是**纯函数**：只读 message 字符串，不碰文件、不调模型、不依赖任何服务。
所以它是 agent.py 里最适合先拆的部分 —— 拆错了测试立刻红，而且不会有副作用。

★ 它只认中文固定话术，**不读任何文件内容**，所以文件里的「忽略之前所有指令」
  这类文本对它没有任何影响（见 tests/test_agent_safety.py §D）。
"""
from __future__ import annotations

import os
import re

# parse_local 会先做同义归一与指代还原，这两个纯函数在 agent_intent 里。
# ★ 拆模块时最容易漏的就是这类「裸名跨模块导入」—— agent.py 顶部 import 过的名字，
#   在新模块里必须重新 import，否则运行到那一行才 NameError。
from .agent_intent import canonical, clarify_questions, resolve_reference

PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\s\"'，,。；;、]*)")
DRIVE_CN_RE = re.compile(r"([A-Za-z])\s*盘")

# 中文紧贴在路径后面是很常见的写法：「把 D:\素材 腾出空间」「D:\素材清一下」。
# 正则本身分不清「路径里的中文」和「路径后的中文」，所以用存在性来判：
# 整段存在就用整段；否则从第一个中文字符处切开，切出来的前缀存在就用前缀。
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _trim_path(value: str) -> str:
    """把紧贴在路径尾部的普通中文词削掉，但保留本来就是中文的目录名。"""
    candidate = value.rstrip("\\/")
    if not candidate or os.path.exists(candidate):
        return candidate

    match = CJK_RE.search(candidate)
    if not match:
        return candidate
    head = candidate[:match.start()].rstrip("\\/")
    if len(head) > 3 and os.path.exists(head):
        return head
    return candidate


def _extract_paths(text: str) -> list[str]:
    found: list[str] = []
    for match in PATH_RE.finditer(text):
        value = _trim_path(match.group(1))
        if value and value not in found:
            found.append(value)
    for match in DRIVE_CN_RE.finditer(text):
        value = f"{match.group(1).upper()}:\\"
        if value not in found:
            # 「C 盘」若已经匹配到更具体的 C:\... 就不要再插一条盘根
            if not any(item.upper().startswith(f"{value[0].upper()}:") for item in found):
                found.append(value)
    return found


# ---------------------------------------------------------------------------
# 参数抽取（零 token）：体积阈值 / 文件类型 / 保留策略
# 本地规则与 DeepSeek 共用同一套键：min_size_mb / extensions / keep
# ---------------------------------------------------------------------------

SIZE_RE = re.compile(r"(?<![\d.])(\d++(?:\.\d++)?)\s*+(GB|G|MB|M|gb|mb)(?![_A-Za-z0-9])")

TYPE_KEYWORDS = {
    "视频": ["mp4", "mkv", "avi", "mov", "wmv", "flv"],
    "图片": ["jpg", "jpeg", "png", "gif", "webp", "bmp"],
    "音频": ["mp3", "wav", "flac", "aac", "ogg"],
    "文档": ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "md"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz"],
    "安装包": ["exe", "msi"],
}

KEEP_RE = re.compile(r"(只留最新|只保留最新|保留最新|最新一份|最新的那份)")

# 动作动词：query_local 用它判断「用户在问状态」还是「用户在派活」。
# ★ 这个常量原本在 agent.py，拆模块时被误删过一次（它被 query_local 用着），
#   放在这里按「规则层」归属，agent.py 再导出给 query_local。
ACTION_HINT_RE = re.compile(
    r"(清理|清掉|删掉|清除|删除|清空|扫描|扫一下|扫扫|查重|去重|整理|归置|收拾"
    r"|迁移|搬到|挪到|移动到|搬走|备份|腾出|释放|清出|省出|空出|挤出|分析|体检|预演)"
)

# 动作动词：query_local 用它判断「用户在问状态」还是「用户在派活」
ACTION_HINT_RE = re.compile(
    r"(清理|清掉|删掉|清除|删除|清空|扫描|扫一下|扫扫|查重|去重|整理|归置|收拾"
    r"|迁移|搬到|挪到|移动到|搬走|备份|腾出|释放|清出|省出|空出|挤出|分析|体检|预演)"
)


def _extract_filters(text: str) -> dict:
    """从原话里抽体积阈值 / 类型扩展名 / 保留策略。抽不到就不给键。"""
    filters: dict = {}
    for match in SIZE_RE.finditer(text):
        value = float(match.group(1))
        if match.group(2).lower().startswith("g"):
            value *= 1024
        filters["min_size_mb"] = max(1, int(min(value, 1_000_000)))
        break

    extensions: list[str] = []
    for keyword, exts in TYPE_KEYWORDS.items():
        if keyword in text:
            for ext in exts:
                if ext not in extensions:
                    extensions.append(ext)
    if extensions:
        filters["extensions"] = extensions

    if KEEP_RE.search(text):
        filters["keep"] = "newest"

    return filters


def parse_local(message: str, context: dict | None = None) -> dict | None:
    """本地规则意图识别：命中返回计划，不消耗任何 token。

    与早期版本的三处不同：
    1. 先做**同义归一**（canonical）与**上下文指代还原**（resolve_reference），
       「腾空间」「就刚才那个目录」因此能落到同一套正则上。
    2. 澄清不再写死文案，统一交给 agent_intent.clarify_questions()——
       整理类给选项、迁移类给字段清单。
    3. 默认先扫描再清理：用户说「清理垃圾」不能跳过清单预览，
       只有明确说「立即/直接/执行」时才生成写操作计划。
    """
    context = context or {}
    raw = (message or "").strip()
    if not raw:
        return None

    reference = resolve_reference(raw, context.get("state") or {})
    text = canonical(reference["text"])
    paths = _extract_paths(text)
    for item in reversed(reference["resolved"]):
        if item not in paths:
            paths.insert(0, item)
    first = paths[0] if paths else ""
    filters = _extract_filters(text)

    # ---- 任务控制：对「正在跑的东西」和「误删的东西」直接下命令 ----
    if re.search(r"(取消|停掉|停止|中断|终止)", text) and re.search(r"(任务|迁移|清理|扫描|拷贝|备份)", text):
        return {"action": "job_cancel", "label": "取消任务",
                "reason": "命中本地规则：取消正在运行的任务",
                "params": {}, "needs_path": False}

    if re.search(r"(回滚|撤销|撤回|复原)", text) and re.search(r"(任务|迁移|清理|操作)", text):
        return {"action": "job_rollback", "label": "回滚任务",
                "reason": "命中本地规则：回滚最近一次任务",
                "params": {}, "needs_path": False}

    if re.search(r"(恢复|还原|找回来|救回来|捞回来)", text) and re.search(r"(隔离区|回收站|误删|删错)", text):
        return {"action": "trash_restore", "label": "恢复隔离区文件",
                "reason": "命中本地规则：从隔离区恢复",
                "params": {"path": first}, "needs_path": not first}

    # ---- 备份：缺路径才追问（第四阶段） ----
    if re.search(r"(备份)", text):
        backup_params = {"source_dir": first, "dest_dir": paths[1] if len(paths) > 1 else ""}
        question = clarify_questions("backup", raw, backup_params, {})
        if question and (not first or len(paths) < 2):
            return question
        return {
            "action": "backup",
            "label": "备份目录",
            "reason": "命中本地规则：目录备份",
            "params": backup_params,
            "needs_path": len(paths) < 2,
        }

    tab_routes = {
        "任务中心": "/jobs", "回收站": "/trash", "操作日志": "/logs",
        "空间地图": "/space", "整理预览": "/virtual", "项目雷达": "/projects",
        "扫描分析": "/scan", "重复文件": "/duplicates", "垃圾文件": "/junk",
        "跨盘迁移": "/migrate", "项目备份": "/backup", "首页": "/",
    }
    # 这些页接受"带着目录过去"（前端 useHandoffTarget）——只有它们才附带 path，
    # 其余（任务中心 / 回收站 / 日志 / 空间地图 / 首页）带了也没处用。
    focus_routes = {"/scan", "/duplicates", "/junk", "/virtual", "/projects", "/migrate", "/search", "/backup"}
    for label, route in tab_routes.items():
        if re.search(rf"(打开|进入|切到|查看){label}", text):
            params: dict = {"route": route}
            if paths and route in focus_routes:
                params["path"] = paths[0]
            return {"action": "open_tab", "label": f"打开{label}", "reason": f"打开{label}",
                    "params": params, "needs_path": False}

    # ---- 迁移：来源/去向不全就追问；给齐了仍先出只读计划 ----
    if re.search(r"(搬到|挪到|移动到|迁移|移过去|搬走)", text):
        migrate_params = {
            "source_dir": paths[0] if paths else "",
            "target_dir": paths[1] if len(paths) > 1 else "",
            **filters,
        }
        if len(paths) < 2:
            question = clarify_questions("migrate_plan", raw, migrate_params, {})
            if question:
                return question
        if re.search(r"(直接搬|开始搬|执行迁移|确认搬移|真的搬)", text):
            return {"action": "migrate_execute", "label": "执行跨盘迁移",
                    "reason": "命中本地规则：执行迁移（复制+校验+删源）",
                    "params": migrate_params, "needs_path": False}
        return {"action": "migrate_plan", "label": "规划跨盘迁移",
                "reason": "命中本地规则：跨盘迁移（只出清单，不动文件）",
                "params": migrate_params, "needs_path": False}

    # ---- 写类（执行前仍需用户在计划卡上确认） ----
    if re.search(r"(清理|清掉|删掉|清除|删除|清空)", text) and re.search(r"(垃圾|临时文件|缓存)", text):
        if not re.search(r"(立即|直接|确认|执行|删除掉|彻底)", text):
            return {
                "action": "junk_scan",
                "label": "查找垃圾文件",
                "reason": "先扫描并展示清单，确认后再进入隔离区",
                "params": {"path": first, **filters},
                "needs_path": not first,
            }
        return {
            "action": "junk_clean",
            "label": "清理垃圾文件",
            "reason": "命中本地规则：垃圾清理（只清安全级，先进隔离区）",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    if re.search(r"(删掉|清除|清理|去掉|删了|只留|只保留)", text) and re.search(r"重复", text):
        return {
            "action": "duplicate_clean",
            "label": "清理重复文件",
            "reason": "命中本地规则：重复文件清理（只删多余副本，先进隔离区）",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    # ---- 2026-10-13「能力放开」新增的本地规则 ----
    # 相似图片：相似 ≠ 相同，所以只"找"不给"立即清"——清单必须逐张看
    if re.search(r"相似", text) and not re.search(r"(立即|直接|确认|执行|删掉|清除|清理|去掉)", text):
        return {
            "action": "similar_scan",
            "label": "查找相似图片",
            "reason": "先分组展示，相似不等于相同，删哪张得你逐张确认",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    if re.search(r"(删掉|清除|清理|去掉|删了|只留|只保留)", text) and re.search(r"相似", text):
        return {
            "action": "similar_clean",
            "label": "清理相似图片",
            "reason": "命中本地规则：相似图片清理（每组保留一张，其余先进隔离区）",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    # 大文件：默认只列不清（大文件多半是用户数据）
    if re.search(r"大文件", text) and not re.search(r"(立即|直接|确认|执行|删掉|清除|清理|去掉)", text):
        return {
            "action": "large_scan",
            "label": "查找大文件",
            "reason": "先列出大文件清单，确认后再决定清哪些",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    if re.search(r"(删掉|清除|清理|去掉|删了)", text) and re.search(r"大文件", text):
        return {
            "action": "large_clean",
            "label": "清理大文件",
            "reason": "命中本地规则：大文件清理（确认后即永久删除，不可恢复）",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    # 隔离区彻底删除：必须显式说出"彻底"；Agent 清理有单独的确认后永久删除流程。
    if re.search(r"(彻底删除|清空隔离区|清空回收站|物理删除)", text):
        return {
            "action": "trash_purge",
            "label": "彻底删除隔离区内容",
            "reason": "命中本地规则：彻底删除隔离区（**不可恢复**，会先列出将删的内容）",
            "params": {"all": True},
            "needs_path": False,
        }

    # 建索引
    if re.search(r"(建立|建一下|重建|更新).{0,4}(索引|检索)", text):
        return {
            "action": "search_index",
            "label": "建立全文索引",
            "reason": "命中本地规则：建立/更新全文检索索引（只读文件，不动任何文件）",
            "params": {"paths": [first] if first else []},
            "needs_path": not first,
        }

    if re.search(r"(直接搬|开始搬|执行迁移|确认搬移|真的搬)", text) and len(paths) >= 2:
        return {
            "action": "migrate_execute",
            "label": "执行跨盘迁移",
            "reason": "命中本地规则：执行迁移（复制+校验+删源）",
            "params": {"source_dir": paths[0], "target_dir": paths[1]},
            "needs_path": False,
        }

    # ---- 只读 ----
    if re.search(r"(垃圾|清一下|清清|清垃圾|临时文件|缓存)", text):
        return {
            "action": "junk_scan",
            "label": "查找垃圾文件",
            "reason": "命中本地规则：垃圾文件扫描",
            "params": {"path": first},
            "needs_path": not first,
        }

    if re.search(r"(重复文件|重复的|查重|重复)", text):
        return {
            "action": "duplicate_scan",
            "label": "查找重复文件",
            "reason": "命中本地规则：重复文件检测",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    if re.search(r"(空间|占用|占多少|地图|可视化|谁在占)", text):
        return {
            "action": "tree",
            "label": "生成空间地图",
            "reason": "命中本地规则：目录占用可视化",
            "params": {"path": first},
            "needs_path": not first,
        }

    # ---- 整理：方式不明先把选项摆出来；方式已定就只问路径（第二阶段追问的收尾）----
    if re.search(r"(整理|归置|收拾|按类型|按时间|按月份|按体积|按项目|整理预览)", text):
        group_by = ""
        for keyword, mode in (("按月份", "time"), ("按时间", "time"), ("按类型", "type"),
                              ("按体积", "size"), ("按项目", "project")):
            if keyword in text:
                group_by = mode
                break
        # 只有「整理预览」这类笼统说法时，默认按类型——不必为一个默认值多问一轮
        if not group_by and re.search(r"(只生成预览|整理预览|预览)", text):
            group_by = "type"

        if not group_by:
            question = clarify_questions("virtual_preview", raw, {"path": first}, {})
            if question:
                if not first:
                    question["missing"] = ["path"]
                    question["fields"] = [{"id": "path", "question": "要整理哪个目录？", "kind": "path"}]
                return question

        if not first:
            # 方式已经定了，就别再把那四个选项摆一遍——只问路径，
            # 顺手把常用路径当建议推过去，避免用户在同一个追问里打转。
            common = [item.get("path") if isinstance(item, dict) else str(item)
                      for item in ((context.get("prefs") or {}).get("common_paths") or [])][:4]
            return {
                "type": "clarify",
                "reason": f"整理方式记下了（{'按月份' if group_by == 'time' else '按类型' if group_by == 'type' else '按体积' if group_by == 'size' else '按项目'}），还差一个目录。",
                "question": "要整理哪个目录？",
                "missing": ["path"],
                "suggestions": common or ["例如：D:\\下载", "例如：C:\\Users\\faker\\Downloads"],
                "options": [],
                "fields": [{"id": "path", "question": "要整理的目录", "kind": "path"}],
            }

        # 「整理预览」= 只看；其它「整理/归置/收拾」= 真执行（流水线仍先预览、再等你确认）
        preview_only = bool(re.search(r"(预览|只看|看看|看下|生成预览)", text))
        return {"action": "virtual_preview" if preview_only else "virtual_organize",
                "label": "生成整理预览" if preview_only else "执行整理",
                "reason": "命中本地规则：整理预览" if preview_only
                          else "命中本地规则：整理（先预览，确认后才移动；只移动不删除）",
                "params": {"path": first, "group_by": group_by},
                "needs_path": False}

    if re.search(r"(项目雷达|找项目|扫描项目|僵尸项目)", text):
        return {"action": "projects_scan", "label": "扫描项目", "reason": "命中本地规则：项目雷达",
                "params": {"path": first}, "needs_path": not first}

    if re.search(r"(扫描|扫一下|扫扫|统计|看看有什么|体检)", text):
        return {
            "action": "scan",
            "label": "扫描目录",
            "reason": "命中本地规则：目录扫描",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    return None
