# B8 一次选多个来源 · 实现方案

> 状态：**第一批已实现并验证**（查重 / 垃圾 / 扫描 / 相似四页；跨目录查重为核心卖点；`main.py` 多源端点回归已修复 —— 前端只发 `paths` 不再 422）
> 来源：`docs/OSS_RESEARCH.md` B8 —— "要动 `TargetPicker` + 每页的任务下发与结果合并，牵涉面广"。

---

## 一、一句话目标

一个任务里同时选**多个目录**当来源，结果合并成一份清单 ——
其中**跨目录查重**是只有多源才做得到的事（现在必须扫两遍，且没法比较 A 目录和 B 目录之间有没有重复）。

---

## 二、现状核实（不是推测）

| 事实 | 数据 |
|---|---|
| 用 `TargetPicker` 的页面 | **9 个**：SpaceMap / Virtual / Projects / Scan / Duplicates / Junk / Migrate / Backup / Search |
| 服务取路径的方式 | 8 个服务用 `params["path"]`（scan / tree / duplicate / similar / junk / projects / virtual / index）；2 个用 `source_dir + target_dir`（migrate / backup） |
| 遍历入口 | `fsutil.iter_entries(root, ...)`，**只接受单个 root** |
| 任务模型 | `Job.target` 是**单个字符串**；title 形如 `扫描 D:\x` |
| 跨页接力 | `useHandoffTarget(ref)` 做的是 `targetRef.value = focus` —— **只支持单个** |
| 检索索引 | **天生就是多根的**（`search_root` 一张表存多个根），只是现在一次只能加一个 |

---

## 三、★ 关键判断：不是每个页面都该支持多源

原评估说"牵涉面广"，是因为默认按"9 页全做"算的。但按语义拆开看，**真正受益的只有一半**：

| 分组 | 页面 | 多源是否有意义 | 理由 |
|---|---|---|---|
| **第一批（做）** | 重复文件 · 垃圾文件 · 扫描分析 · 相似图片 | ✅ **天然** | 结果本来就是**平铺的清单**，多源 = 多遍历几棵树再拼起来。跨目录查重是独有收益 |
| **第二批（做）** | 全文检索 · 项目雷达 | ✅ 顺手 | 索引本来就是多根的；项目雷达是"扫出项目列表"，拼起来即可 |
| **不做** | 空间地图（tree） | ❌ | 结果是一棵**有根的树**，多源要造一个虚拟父节点，会破坏"钻取"的语义 |
| **不做** | 整理预览（virtual） | ❌ | 它是"把一棵树重组成目标结构"，多源会让"按项目/类型归组"的口径说不清 |
| **可选第三批** | 跨盘迁移 · 项目备份 | ⚠️ 见 §九 | 语义是"一个源 → 一个目标"，多源到同一目标可行，但要处理重名冲突 |

**这个划分本身就是方案的一部分**：给"树形结果"的页面硬塞多源，只会让口径变模糊 ——
而口径模糊正是项目在 GOTCHAS 十一·3 里明确反对的（"数字要给口径，不能只给数值"）。

---

## 四、核心改动 1：`fsutil.iter_entries_multi()`

新增到 `backend/fsutil.py`（不动现有 `iter_entries` 的任何行为）：

```python
def iter_entries_multi(roots, *, excludes=None, max_depth=None,
                       progress=None, cancelled=None, **kw) -> Iterator[tuple[Path, os.DirEntry]]:
    """按顺序遍历多个根，产出 (root, entry)。

    与逐个调 iter_entries 的区别只有两点，但都是必须的：
    1. **进度是全局单调的**：先数完所有根的总数，再用 offset 把各根的进度接起来，
       否则每换一个根百分比就归零重来（界面看着像来回跳）。
    2. **条目上限是全局的**：SCAN_ENTRY_LIMIT 现在是**每个根**一份预算，
       多源会变成 N×预算。必须改成全局共享一份，否则大目录组合会失控。
    """
```

返回的 `root` 用来给每条结果打来源标记。

### ★ 嵌套根必须去重（这是个真 bug 源）

如果用户选了 `D:\A` 和 `D:\A\sub`，`sub` 下的文件会被遍历**两次** ——
在查重里表现为"每个文件都是自己的重复"，**凭空造出一批假重复**。

处理：规范化后，**丢弃被另一个根包含的那些根**，并在 `result.warnings` 里说出来：
`D:\A\sub 已包含在 D:\A 内，已自动跳过`。
（不是报错 —— 用户只是多选了一下，不该因此跑不起来。）

---

## 五、核心改动 2：参数契约（`path` + `paths`，向后兼容）

```python
def resolve_roots(params) -> list[Path]:
    """多来源：优先 `paths`（列表），回退到 `path`（单个）。

    ★ 保留单参数形式是为了**不打破既有调用方**：
      Agent 意图解析、CLI、MCP 三处现在都只发 `path`，不能因为加了多源就全改一遍。
    """
    paths = [p for p in (params.get("paths") or []) if str(p).strip()]
    if not paths:
        single = str(params.get("path") or "").strip()
        paths = [single] if single else []
    if not paths:
        raise ValueError("没有指定任何来源目录")
    return [fsutil.resolve_dir(p) for p in paths]   # 任何一个不存在 → 直接失败，不跑一半
```

**任何一个目录不存在就整体失败**，不做"跳过坏的那个继续跑" —— 半份结果比报错更危险
（用户会以为另外那几个目录也没东西）。

---

## 六、核心改动 3：`TargetPicker` 加 `multiple` 模式

**加法式改动**，不碰现有单源页面：新增 `multiple: Boolean` 与 `v-model:paths`（数组）。

```
[盘符: C: D: E:]                        ← 点一下 = 追加一个来源（不再覆盖）
来源 1  [ D:\Workspace              ] [×]
来源 2  [ D:\Media                  ] [×]
        [ + 再加一个来源 ]
排除    已排除 12 项  展开
已选 2 个来源
```

- `multiple=false`（默认）→ 行为与现在**完全一致**，9 个页面里不 opt-in 的完全不受影响。
- 至少留 1 行；只有 1 行时隐藏 `×`。
- 排除规则仍是**共享的一套**（`useConfig` 的 `excludes`），多源也只有一个口径。

### 接力的兼容改法

`useHandoffTarget` 现在做 `targetRef.value = focus`。改成：

```js
if (Array.isArray(targetRef.value)) {
  if (!targetRef.value.includes(focus)) targetRef.value = [focus, ...targetRef.value]  // 追加，不覆盖
} else {
  targetRef.value = focus
}
```

从别的页面带一个目录过来 → **追加**成一个来源，而不是把已选的冲掉。

---

## 七、核心改动 4：结果带来源标记 + `cross_source`

每条结果项加 `source`（该条来自哪个根）。UI 三个用法：

1. **结果行显示来源目录**（多个来源时才显示，单源时不占地方）；
2. **可按来源筛选**（顶上一排 chip：全部 / D:\A / D:\B）；
3. ★ **查重组加 `cross_source` 标记**：这一组的成员**来自不同目录** ——
   这是多源唯一能给出、而单源永远给不出的信息，是本次的核心卖点。
   界面上给一个"跨目录"标签，并**默认排在前面**（跨目录的重复才是真该处理的）。

---

## 八、前端接线清单

| 步骤 | 文件 | 内容 |
|---|---|---|
| 1 | `backend/fsutil.py` | 新增 `iter_entries_multi`（全局进度 + 全局条目预算） |
| 2 | `backend/services/{duplicates,junk,scan,similar,projects,index}.py` | 用 `resolve_roots()` + `iter_entries_multi`；结果项打 `source`；duplicates 额外算 `cross_source` |
| 3 | `backend/main.py` | ✅ 四个分析端点（`/api/scan`、`/api/duplicates/scan`、`/api/junk/scan`、`/api/similar/scan`）的 Request 模型加 `paths: list[str] \| None = None` 且 `path` 改为可选；端点内改用 `resolve_sources(request.model_dump())` 取首个来源生成 title/target，多源标题带"等 N 个来源"后缀 |
| 4 | `frontend/src/components/TargetPicker.vue` | 加 `multiple` 模式（加法式） |
| 5 | `frontend/src/composables/useHandoff.js` | 数组 ref 时**追加**而非覆盖 |
| 6 | `frontend/src/api/filepilot.js` | 无需改（payload 多带一个 `paths` 字段即可） |
| 7 | `frontend/src/views/{Duplicates,Junk,Scan,Similar,Projects,Search}.vue` | 切到 `v-model:paths`；结果区加来源 chip 筛选 / 来源列 / 跨目录标签 |
| 8 | `frontend/src/views/Logs.vue` | 无需改（标题由后端给） |

---

## 九、需要你拍板的点

| # | 决策 | 我的建议 |
|---|---|---|
| ① | 做哪些页 | **第一批 4 页**（查重 / 垃圾 / 扫描 / 相似），第二批（检索 / 项目雷达）看第一批的反馈再上。**空间地图与整理预览不做**（树形结果，见 §三） |
| ② | 嵌套根 | **自动跳过被包含的那个 + 警告**，不报错（§四） |
| ③ | 坏路径 | **整体失败**，不跑一半（§五） |
| ④ | 迁移 / 备份 | **本轮不做**。多源→同一目标要处理重名冲突（`D:\A\a.txt` 与 `D:\B\a.txt` 都去 `E:\`），值得单独一轮 |
| ⑤ | 结果默认排序（查重） | **跨目录的重复排前面** —— 那才是多源带来的新信息 |

---

## 十、验收清单

- `tests/multi_source_test.py`（新）：
  单源与多源结果一致（`paths=[x]` 必须等于 `path=x`）；
  **跨目录重复能被找出**（两个目录放同一份文件 → 1 组，`cross_source=true`）；
  嵌套根被自动跳过且进 warnings；
  坏路径整体失败；
  多源进度**单调不回退**；
  条目上限是全局的（不是 N×）。
- `tests/smoke_test.py` 总闸加一段多源查重（走真实 HTTP）。
- 实机：查重页选两个目录 → 结果出现"跨目录"标签且排在前面 → 来源筛选可切。
- 不回归：单源路径下所有既有测试照旧通过（`paths=[x]` ≡ `path=x` 这条断言就是干这个的）。

---

## 十一、明确不做（写进 `docs/LIMITS.md`）

- **空间地图 / 整理预览不做多源** —— 它们的输出是"一棵树"，多源要造虚拟父节点，会毁掉钻取与归组的口径。
- **迁移 / 备份本轮不做** —— 多源到同一目标的重名冲突需要单独的规则设计。
- **不做"来源组"的保存/命名**（把一组目录存成常用组合）—— 等真的重复用到再说。
