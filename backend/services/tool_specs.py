"""Agent 工具的**唯一事实源**。

以前一个工具的信息分散在 6 张表里，新增一个工具要同步改 6 处：

    ALLOWED_ACTIONS / TOOL_REGISTRY / TOOL_GROUPS
    WRITE_ACTIONS / ACTION_PARAMS / PATH_PARAMS

漏改任何一处都不会立刻报错，而是在运行到那一步时才炸 —— 2026-10-17 审计就抓到过
一例：`large_scan` 有 ALLOWED_ACTIONS / ACTION_PARAMS / PATH_PARAMS 三处条目，
**唯独缺 TOOL_REGISTRY**。后果不是崩溃，是更阴的「静默失效」：它能被规划，
但 label 取不到、也不进 function-calling schema —— 模型根本看不见这个工具。

现在每个工具只声明一次 `ToolSpec`，其余常量全部派生：

    ToolSpec
      ├─ ALLOWED_ACTIONS   （动作名列表，保持原声明顺序）
      ├─ TOOL_REGISTRY     （{label, kind, group, write}，给后端与前端看）
      ├─ WRITE_ACTIONS     （access == "write" 的集合）
      ├─ ACTION_PARAMS     （允许的参数键）
      ├─ PATH_PARAMS       （必须是真实目录的参数）
      └─ QUERY_TOOLS / HELPER_TOOLS（查询与辅助工具的展示元信息）

`tests/test_tool_registry.py` 会校验这些派生结果自洽，**新增工具忘了声明写权限、
或参数名拼错，测试阶段就会红**，不会再等到运行时。

★ 这里只管「工具是什么」。真正执行时：
  - 风险等级由 `agent_intent.compute_risk()` 本地算（**不采信这里的任何元信息**）；
  - 写权限由 `fsutil.write_block_reason()` 在服务层兜底；
  - 用户确认由 `agent_planner` 强制插在第一个写动作之前。
  所以本模块的 access 字段只是「声明」，不是「凭据」。
"""
from __future__ import annotations

from dataclasses import dataclass

READ = "read"
WRITE = "write"


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明。新增工具**只改这一处**。"""

    name: str
    label: str
    kind: str                                  # navigation | job | query | helper
    group: str                                 # query | execute | helper
    access: str = READ                         # read | write
    params: tuple[str, ...] = ()               # 允许模型填的参数键（多吐的一律丢弃）
    path_params: tuple[str, ...] = ()          # 必须是真实存在目录的参数

    @property
    def write(self) -> bool:
        return self.access == WRITE

    def meta(self) -> dict:
        """旧 TOOL_REGISTRY 的形状，供后端与前端沿用。"""
        return {"label": self.label, "kind": self.kind, "group": self.group, "write": self.write}


# ---------------------------------------------------------------------------
# 一、动作（会被模型看见、可被规划成 Job 或导航）
# ---------------------------------------------------------------------------

ACTION_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("open_tab", "打开功能页", "navigation", "query", params=("route", "path")),
    ToolSpec("overview", "查看首页概况", "query", "query"),
    ToolSpec("scan", "扫描目录", "job", "query",
             params=("path", "min_size_mb", "extensions"), path_params=("path",)),
    ToolSpec("tree", "生成空间地图", "job", "query",
             params=("path", "min_size_mb", "extensions"), path_params=("path",)),
    ToolSpec("virtual_preview", "生成整理预览", "job", "execute",
             params=("path", "group_by", "min_size_mb"), path_params=("path",)),
    ToolSpec("projects_scan", "扫描项目雷达", "job", "query",
             params=("path", "zombie_days", "max_depth"), path_params=("path",)),
    ToolSpec("duplicate_scan", "查找重复文件", "job", "query",
             params=("path", "min_size_mb", "extensions"), path_params=("path",)),
    ToolSpec("junk_scan", "查找垃圾文件", "job", "query", params=("path",), path_params=("path",)),
    ToolSpec("migrate_plan", "创建迁移计划", "job", "execute",
             params=("source_dir", "target_dir", "min_size_mb", "extensions"),
             path_params=("source_dir",)),
    ToolSpec("jobs_list", "查看任务中心", "query", "query"),
    ToolSpec("trash_list", "查看回收站", "query", "query"),
    ToolSpec("logs_list", "查看操作日志", "query", "query"),
    ToolSpec("duplicate_clean", "清理重复文件", "job", "execute", access=WRITE,
             params=("path", "keep", "min_size_mb", "extensions"), path_params=("path",)),
    ToolSpec("junk_clean", "清理垃圾文件", "job", "execute", access=WRITE,
             params=("path",), path_params=("path",)),
    ToolSpec("migrate_execute", "执行跨盘迁移", "job", "execute", access=WRITE,
             params=("source_dir", "target_dir", "min_size_mb", "extensions"),
             path_params=("source_dir",)),
    ToolSpec("backup", "创建项目备份", "job", "execute", access=WRITE,
             params=("source_dir", "dest_dir"), path_params=("source_dir",)),
    ToolSpec("trash_restore", "恢复隔离区文件", "job", "execute", access=WRITE,
             params=("path",), path_params=("path",)),
    # job_cancel 不算写：它只是让一个任务停下来，不碰任何文件（只读档也该能停掉跑疯的任务）。
    ToolSpec("job_cancel", "取消任务", "job", "execute"),
    ToolSpec("job_rollback", "回滚任务", "job", "execute", access=WRITE),
    # ---- 2026-10-13「能力放开」：把页面有、Agent 却没有的能力接上 ----
    ToolSpec("similar_scan", "查找相似图片", "job", "query",
             params=("path", "min_size_kb", "threshold", "rotations"), path_params=("path",)),
    ToolSpec("similar_clean", "清理相似图片", "job", "execute", access=WRITE,
             params=("path", "keep", "min_size_mb", "extensions"), path_params=("path",)),
    # 大文件：此前能列不能清。★ large_scan 曾漏了 TOOL_REGISTRY，等于模型看不见它
    ToolSpec("large_clean", "清理大文件", "job", "execute", access=WRITE,
             params=("path", "min_mb", "limit"), path_params=("path",)),
    ToolSpec("large_scan", "查找大文件", "job", "query",
             params=("path", "min_mb", "limit"), path_params=("path",)),
    ToolSpec("search_index", "建立全文索引", "job", "query",
             params=("paths",), path_params=("paths",)),
    # 显式清空已有隔离区条目的动作：不可恢复，最高风险；Agent 清理另有确认后的批次清空流程。
    ToolSpec("trash_purge", "彻底删除隔离区内容", "job", "execute", access=WRITE,
             params=("ids", "all")),
    # 2026-10-14：整理真执行（就地移动到分组子目录，只移动不删除、可回滚）
    # ★ 放在末尾是为了让 ALLOWED_ACTIONS 的**顺序**与拆分前逐字一致 ——
    #   它直接回给前端（registry_view 的 all_actions），顺序变了就是行为变了。
    ToolSpec("virtual_organize", "执行整理", "job", "execute", access=WRITE,
             params=("path", "group_by"), path_params=("path",)),
)


# ---------------------------------------------------------------------------
# 二、只读查询工具（当场执行、直接回答，零 token）
# ---------------------------------------------------------------------------

QUERY_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("drive_status", "查询磁盘状态", "query", "query", params=("path",)),
    ToolSpec("dir_size", "查询目录大小", "query", "query", params=("path", "max_depth")),
    ToolSpec("dir_tree", "查询目录树", "query", "query", params=("path", "depth")),
    ToolSpec("type_distribution", "查询文件类型分布", "query", "query", params=("path",)),
    ToolSpec("recent_files", "查询最近修改文件", "query", "query",
             params=("path", "days", "limit")),
    ToolSpec("large_files", "查询大文件", "query", "query", params=("path", "min_mb", "limit")),
    ToolSpec("duplicate_files", "查询重复文件", "query", "query",
             params=("path", "min_mb", "limit")),
    ToolSpec("junk_files", "查询垃圾文件", "query", "query", params=("path", "limit")),
    ToolSpec("project_dirs", "查询项目目录", "query", "query", params=("path", "max_depth")),
    ToolSpec("job_status", "查询任务状态", "query", "query", params=("limit",)),
    ToolSpec("trash_contents", "查询回收站内容", "query", "query", params=("limit",)),
    ToolSpec("operation_logs", "查询历史操作记录", "query", "query", params=("limit",)),
    # 2026-10-13「能力放开」补的四个：检索 / 备份核对 / 快照
    ToolSpec("search", "全文检索文件内容", "query", "query", params=("keyword", "limit")),
    ToolSpec("backup_verify", "核对备份能否还原", "query", "query",
             params=("zip_path", "mode")),
    ToolSpec("snapshot_list", "列出扫描快照", "query", "query", params=("root",)),
    ToolSpec("snapshot_compare", "对比两个扫描快照", "query", "query",
             params=("root", "base_id", "target_id")),
)


# ---------------------------------------------------------------------------
# 三、辅助工具（算空间、查冲突、估耗时——都是纯本地只读）
# ---------------------------------------------------------------------------

HELPER_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("check_disk_space", "检查磁盘空间", "helper", "helper", params=("path", "need_mb")),
    ToolSpec("check_path_exists", "检查路径是否存在", "helper", "helper", params=("path",)),
    ToolSpec("check_path_protected", "检查路径是否受保护", "helper", "helper", params=("path",)),
    ToolSpec("check_target_conflict", "检查目标是否冲突", "helper", "helper",
             params=("source_dir", "target_dir")),
    ToolSpec("check_user_permission", "检查用户权限", "helper", "helper"),
    ToolSpec("estimate_task", "估算耗时与释放空间", "helper", "helper",
             params=("action", "items")),
)


# ---------------------------------------------------------------------------
# 四、派生（**下面全是计算出来的，不要手改**）
# ---------------------------------------------------------------------------

ALLOWED_ACTIONS: list[str] = [spec.name for spec in ACTION_SPECS]

TOOL_REGISTRY: dict[str, dict] = {spec.name: spec.meta() for spec in ACTION_SPECS}

WRITE_ACTIONS: set[str] = {spec.name for spec in ACTION_SPECS if spec.write}

ACTION_PARAMS: dict[str, set[str]] = {spec.name: set(spec.params) for spec in ACTION_SPECS}

PATH_PARAMS: dict[str, list[str]] = {
    spec.name: list(spec.path_params) for spec in ACTION_SPECS
}

QUERY_TOOLS: dict[str, dict] = {
    spec.name: {"label": spec.label, "params": list(spec.params)} for spec in QUERY_SPECS
}

HELPER_TOOLS: dict[str, dict] = {
    spec.name: {"label": spec.label, "params": list(spec.params)} for spec in HELPER_SPECS
}

# 计划树里的「输入 / 输出 / 风险」展示分组。
#
# ★ 这一张**故意手写**：它不是工具定义，而是「在前端怎么分组摆」——口径与
#   ToolSpec.group 不同（比如 similar_scan 的 group 是 query，但它在计划树里
#   跟检索排在一起，而不是跟 open_tab 排在一起）。硬从 spec 派生会改变现有
#   展示顺序，属于「为了结构好看破坏已有行为」。
#   一致性由 `validate()` 保证：这里出现的每个名字都必须在册。
TOOL_GROUPS: dict[str, list[str]] = {
    "query": [
        "drive_status", "dir_size", "dir_tree", "type_distribution", "recent_files",
        "large_files", "duplicate_files", "junk_files", "project_dirs",
        "job_status", "trash_contents", "operation_logs",
        "search", "backup_verify", "snapshot_list", "snapshot_compare",
        "similar_scan", "search_index", "large_scan",
    ],
    "execute": [
        "virtual_preview", "virtual_organize", "migrate_plan", "migrate_execute", "junk_clean",
        "duplicate_clean", "backup", "trash_restore", "job_cancel", "job_rollback",
        "similar_clean", "large_clean", "trash_purge",
    ],
    "helper": [
        "check_disk_space", "check_path_exists", "check_path_protected",
        "check_target_conflict", "check_user_permission", "estimate_task",
    ],
}

EVERY_TOOL: dict[str, ToolSpec] = {
    spec.name: spec for spec in (*ACTION_SPECS, *QUERY_SPECS, *HELPER_SPECS)
}


# ---------------------------------------------------------------------------
# 五、自检（测试与 CI 调用；不在导入期跑，避免一处笔误拖垮整个服务）
# ---------------------------------------------------------------------------

def validate() -> list[str]:
    """返回问题清单，空列表表示自洽。测试直接断言 `validate() == []`。"""
    problems: list[str] = []

    names = [spec.name for spec in (*ACTION_SPECS, *QUERY_SPECS, *HELPER_SPECS)]
    duplicates = {name for name in names if names.count(name) > 1}
    for name in sorted(duplicates):
        problems.append(f"工具名重复：{name}")

    for spec in ACTION_SPECS:
        if spec.kind not in {"navigation", "job", "query"}:
            problems.append(f"{spec.name}：kind 非法（{spec.kind}）")
        if spec.group not in {"query", "execute"}:
            problems.append(f"{spec.name}：group 非法（{spec.group}）")
        if spec.access not in {READ, WRITE}:
            problems.append(f"{spec.name}：access 非法（{spec.access}）")
        # 写动作必须是 job —— 导航/纯查询不该带写权限
        if spec.write and spec.kind != "job":
            problems.append(f"{spec.name}：写动作的 kind 必须是 job")
        # path 参数必须同时出现在 params 里，否则模型填了也会被丢弃
        for key in spec.path_params:
            if key not in spec.params:
                problems.append(f"{spec.name}：path_params 里的 {key} 不在 params 中")

    for spec in QUERY_SPECS:
        if spec.access == WRITE:
            problems.append(f"{spec.name}：查询工具不允许声明写权限")
    for spec in HELPER_SPECS:
        if spec.access == WRITE:
            problems.append(f"{spec.name}：辅助工具不允许声明写权限")

    for group, members in TOOL_GROUPS.items():
        for name in members:
            if name not in EVERY_TOOL:
                problems.append(f"TOOL_GROUPS[{group}] 引用了未注册的工具：{name}")

    return problems


def describe() -> dict:
    """给 /api/agent/tools 与报告用的汇总（不含任何执行路径）。"""
    return {
        "actions": len(ACTION_SPECS),
        "query_tools": len(QUERY_SPECS),
        "helper_tools": len(HELPER_SPECS),
        "total": len(EVERY_TOOL),
        "write_actions": sorted(WRITE_ACTIONS),
        "problems": validate(),
    }
