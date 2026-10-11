# 踩过的坑（完整清单）

`MEMORY.md` 只留最高频的规则，这里是全量。**改前面的代码之前先扫一眼相关小节。**

---

## 一、性能：遍历与规则匹配

### 遍历只有一条路

`fsutil.iter_entries(root, ..., on_dir=..., skip_self_protected=...)`，yield `os.DirEntry`。
`iter_files`（Path 版）只是给非要 Path 的调用方留的兼容壳，内部复用 —— **新代码别再用它 + `path.stat()`**。

为什么必须给 `DirEntry`：Windows 的目录列表**本身就带回了 size/mtime**，`entry.stat()` 复用这些数据是
**零系统调用**；而 `os.walk` 只给你文件名，要拿大小必须**每个文件再打一次**。同一批文件实测 **8~10×**。

两个可选参数：

- **`on_dir(path, depth, raw_dirs, raw_files)`** —— 每进一个目录回调一次，给的是**裁剪前**的条目名。
  有些目录自己不会被遍历、但它存在就是信息（`.git` / `package.json` 是项目归属的证据），
  光看 yield 出来的文件是看不到的。
  **★ 顺序是硬要求：它必须在该目录的文件 yield 之前触发** —— 否则要靠目录名提前建立上下文的
  调用方（虚拟整理的项目归属）还没准备好，文件就已经处理完了。
- **`skip_self_protected=False`** —— 只剪系统只读区、不剪自保护目录。只读预览类服务用
  （否则预览自己的项目目录会是一片空白）。

**业务服务一律走它，别再自己写 `os.walk`。** `virtual.py` 曾自造一套：逐目录 `resolve()` +
逐文件 `path.stat()`，还顺带把进度写成 `total=None`（恒 0%）—— 三处错挤在一个函数里。

### 遍历里一次都别调 `path.resolve()`

Windows 上一个 **0.42ms**：

| 调用 | 600 个文件 |
|---|---|
| `str(path.resolve())` | **255.15 ms** |
| `str(path)` | **0.04 ms** |

**差 6679 倍，而且 100% 是浪费** —— 遍历出来的路径本来就是绝对的
（root 已 resolve，`scandir` / `os.walk(followlinks=False)` 都不进链接）。

替换手法：算深度 → **数分隔符**（`current[base_len:].count(os.sep)`）；
算相对路径 → **字符串切**；保护/黑名单判定 → **前缀比较**（前缀列表缓存一次）；
拿它当缓存键 → 直接 `str(path)`。
`duplicates._cached_hash` 曾拿 `str(path.resolve())` 当缓存键。

### `fnmatch.fnmatch` 逐文件 × 逐模式是最容易漏的热点

它对**名字和模式各调一次** `os.path.normcase`，在 Windows 上那就是 `LCMapStringEx` 系统调用。
实测：垃圾扫描 **36,568 次 fnmatch / 73,344 次 normcase**，占该服务**一半**时间；虚拟整理占**三成**。

```python
# 错：每个文件 × 每个模式都来一次
if any(fnmatch.fnmatch(name, p.lower()) for p in patterns): ...

# 对：规则表在导入时编译成一条正则，名字只 lower 一次
_MATCHER_ORDER = config.junk_matchers(frozenset({"empty_dir", "zero_byte", "huge_file", "browser_cache"}))
for rule, matcher in _MATCHER_ORDER:
    if matcher is not None and matcher.match(name):
        return rule["id"]
```

统一的入口是 `config.glob_matcher(tuple(patterns))`（lru_cache + `fnmatch.translate` 拼接，
等价于「两者都 lower 后逐个 fnmatchcase」）。
`config.junk_matchers(skip)` 给 JUNK_RULES 建「(规则, 预编译正则)」有序表，
**junk / agent_tools / agent 三处共用**，三处的 skip 集合略有不同所以做成参数。
效果：规则匹配 **174.7ms → 12.0ms（14.6×）**。

### `category_for` 别用 `Path(path).suffix`

pathlib 造对象 + 逐分类遍历。换成 `os.path.splitext` + 导入时建的反查表 → **4.1×**（scan 里曾占 20%）。
语义等价性验过：目录名里的点不算、`.gitignore` 这类前导点文件仍无扩展名。

### 时间格式化别用 `strftime`

`datetime.fromtimestamp(ts)` + f-string 快 **2.65×**，输出逐字节一致
（3000 个时间戳 16.8ms → 6.3ms）。统一走 `fsutil.format_mtime` / `format_date`。

### 查重可以并行

两层哈希剩下的唯一大头是**打开文件**（`_io.open` 占整轮一半），而 Windows 的
CreateFile / `readinto` / `hashlib.update(>2KB)` **都会释放 GIL** —— 所以线程是真能重叠 I/O 延迟的。

实测：整文件哈希 8 线程 **2.2×**、读文件头 4KB **1.76×**；端到端查重
**1.79×**（真实大文件目录）/ **1.25×**（小文件树）。

- `config.HASH_WORKERS` 封顶 8、`FILEPILOT_HASH_WORKERS` 可调小 ——
  **机械盘上并发读会因寻道互相拖累**，这是唯一的风险点。
- **进度上报与错误收集留在主线程**：`_run_pool` 是生成器，主线程边收边报，
  工作线程只做纯计算（异常也变成返回值，逐文件错误日志与顺序都不变）。
- `_hash_cache()` 的懒加载要**双重检查 + 锁**：多线程同时进来时，不加锁会把先建立的那份替换掉，先前写入的条目就丢了。

### 哈希缓存别"每文件读一遍写一遍"

`_cached_hash` 原来每次都全量读写 `hash_cache.json`（实测已 4.1MB）→ **O(n²) I/O**，
比哈希本身贵一个数量级（6.2MB 缓存下每个文件光花在 JSON 上就 112ms）。
改成**进程内驻留 + 收尾落盘一次**，实测 **33×**。

### `hash_file` 用 `file_digest`，但要自己把取消能力包回去

`hashlib.file_digest` 比 Python 里 `while: read()` 循环快 **1.2×**
（24MB 文件 18.6ms → 15.5ms，再往上就是磁盘/CPU 的物理上限）。

**但它一口气读到尾，中间没有插取消检查的机会** —— 大文件上"点了取消没反应"。包一层代理：

```python
class _CancelAwareReader:
    def readable(self):  return True        # ← 少了这句直接 ValueError
    def readinto(self, buf): self._check(); return self._stream.readinto(buf)
    def read(self, size=-1): self._check(); return self._stream.read(size)
```

`file_digest` 会先问 `readable()`，没有就抛
`ValueError: ... is not a file-like object in binary reading mode`。
另给 `ValueError` 加一条回退到循环读块的分支，防某些版本对 fileobj 更挑剔。

### 进度要真百分比，就得先数一遍

`progress(message, current, total, path)` 里 `total=None` → 百分比**恒为 0**，
而界面看着像"卡住了"（已运行 17 分钟、0%）。`fsutil.count_entries()` 本来就是为这个写的
（注释就写着"用于进度百分比"），**但在 2026-10-08 之前从来没被调用过**。

现在 `iter_entries` 带 `progress` 时**自动预统计**。三个必做细节：

- **计数过程本身也要报进度**，否则大目录上先静默几分钟，用户看到的还是 0%。
- 计数函数要能**标记"我就是在数数"**（`count_only=True`），免得无限递归。
- 代价：文件多时约 2%、**目录多时可到 ~150%**，净提速仍有 3~10×。

**`ProgressFn` 的签名是 `(message, current, total, path)`，第一个是消息** ——
写回调时少一个参数，任务直接 failed（`takes 3 positional arguments but 4 were given`）。
这类错误只在**真跑**时才暴露（写对的那些模块照常通过），所以每改一次共享遍历，
**所有走它的服务都要端到端跑一遍**。

另：前端 `JobProgress.vue` 早就有 `v-if="job.total"` 显示 `current / total` ——
缺百分比先查后端有没有上报 `total`，别急着改界面。

### `InterruptedError` 就是「已取消」信号

`hash_file` / `copy_file` 都抛它，`human_error()` 也早把它翻成「操作已取消」。
所以 `JobManager` 的取消分支必须写 **`except (JobCancelled, InterruptedError)`** ——
只写 `JobCancelled` 的话，取消一个大文件查重会被当成普通异常 →
任务卡显示**「执行失败」**而不是「已取消」，用户以为出错了。

### 别为了「少一趟遍历」把功能悄悄剪掉

优化时最容易顺手犯的错：把某段**独立的一趟扫描**并进主遍历。
主遍历按用户排除项剪枝，而那趟独立扫描原本**不看排除项** ——
于是 `__pycache__` / `build` / `dist` / `node_modules` 里的东西**静默消失**。

本类项目里这几乎总是**功能回退**：清空后的空 `__pycache__`、构建残留的空 `build`
恰恰是空目录清理的主要目标，而它们**全在排除项里**。

判断标准：**问"并进去之后，哪些候选会消失"**。会消失就别并，改成
「一趟只数名字、不 stat 的轻遍历」（`excludes=[]` 只留 IMPLICIT）—— 仍然比原来便宜，而不丢结果。

顺带：旧版 junk 那趟 `os.walk(topdown=False)` 有个真 bug —— 对系统只读区
**只"记完 continue"、没清空 `subdirs`** → **整棵走进了 C:\Windows**。
这类"以为跳过了其实还在往下钻"的写法，正是"扫 C 盘很慢"的常见来源。
**裁剪目录一定要同时改 `subdirs`（或干脆不进）。**

### 算法层：合并打开、按格式压缩

到这里"调参数"就到头了，剩下的收益要**换算法**。两个实测有效的：

#### A. 查重：小文件一次打开、两个哈希一起算

原三层过滤里，第二层「读 4KB 算头哈希」和第三层「读全量算 SHA256」是**两次独立的 open**。
而 Windows 上一次 `open` 约 **0.22ms**，多读 16KB 只约 **0.016ms** ——
对小文件来说，「打开」比「多读几 KB」贵一个数量级。

所以 ≤ `config.DUP_SMALL_FILE_BYTES`（16KB）的候选，**第二层就一次读完、两个哈希一起算**，
第三层直接用，不再打开。顺带把算出的完整哈希写进缓存（`_remember`），下次扫描直接命中。

**盈亏平衡推导**（这是定阈值的关键）：
- 合并的代价 = 多读 `(size − 4KB)`
- 合并的收益 = 省一次 `open`
- 只有当文件**真的会进入完整哈希阶段**（概率 p）时才赚 → `p > (size−4KB) / (open + size)`

| 阈值 | 有利场景* | 最坏情况** | 结论 |
|---|---|---|---|
| 16KB | **1.44×** | 0.79×（损失 0.015s） | ✅ 盈亏平衡点只要 7%，几乎无风险 |
| 64KB | 1.42× | 0.61× | 还行，但风险面变大 |
| 1MB | 1.34× | — | 开始亏 |
| 2MB | **0.88×** | — | **明确亏**（读全量的代价反超） |

\* ms-playwright，904 个候选，其中 80% 是 ≤16KB 的小文件
\** 400 个同大小、内容两两不同的小文件（头哈希全部剪掉，合并白读）

**阈值不能开大** —— 1MB 以上收益转负。16KB 的最坏损失每次只有 0.016ms，可忽略。

实测端到端：**0.449s → 0.179s = 2.50×**，与独立参考实现逐组一致（128 组）。

> ★ 验证时的一个假警报：参考实现忘了套 `min_size`，把 0 字节文件算进去，
> 于是"多出一组"（size=0 的空文件，sha256 全是 `e3b0c442…`）。**对比结果前先确认两边口径一致。**

#### B. 备份：对已是压缩编码的格式直接 STORED

对 mp4 / jpg / zip 这类**本身已是压缩编码**的文件做 deflate，是纯浪费：
实测 72MB 的 mp4 —— deflate 6 花 **3.13s**、产物 **72.0MB**（一点没小）；`ZIP_STORED` 只要 **0.43s**。

`backup` 里按扩展名走 `config.NON_COMPRESSIBLE_EXTS` → `ZIP_STORED`，其余照常 deflate。

- **产物大小不变**（这些文件本来就压不动），CRC 逐项一致。
- **exe / dll / pdf 不在此列** —— 它们常能压 20~50%，压了是值的。
- **bmp / wav 也不在** —— 无损原始数据，deflate 能压 30~70%。

实测混合目录（8 mp4 + 4 csv，51MB）：**1.25s → 0.20s（6.3×）**，产物 48.5MB 不变。

#### 还能做但没做的

- **迁移时「边复制边哈希」**：现在 `copy`（读源+写目标）之后 `verify`（再读源+读目标），
  源文件被读了**两遍**。复制时顺带算源哈希，能省一次全量读 —— 大文件迁移约快 25%。
  要改 `copy_file` 的契约（返回哈希），动静稍大，暂缓。
- **USN Journal 增量扫描**：真正的 O(变更) 重扫。但 `\\.\C:` 的卷句柄**需要管理员权限**，
  本工具以非提升令牌运行，做不了。
- **并行目录枚举**：冷缓存（首次扫大目录）时 `scandir` 占 26%，并发枚举可能 2~4×；
  但会改变遍历的顺序语义（进度、取消、`on_dir` 的目录上下文都要重想），且热缓存无收益。暂缓。

### 改性能前先量

块大小 `CHUNK` 已是 1MB，测过没余量就跳过 —— 别凭感觉改。

---

## 二、权限与写保护

- **写入门禁必须加在服务层**（`permissions.require_write()`），**只写在 `main._launch()` 会漏**：
  回滚、还原/清空隔离区、建定时任务都绕过。审计实测：只读档下回滚与还原**真的改动了文件**。
- **门禁异常要能被路由统一兜底**：`BlockedPathError` 是 `ValueError` 子类
  （原来是 `Exception`，会直接逃成 500）。**不要用 `PermissionError`** ——
  `fsutil.human_error()` 会把它翻成「没有权限访问（可能被占用或需要管理员）」，文案指错方向。
- **`is_self_protected`（自保护）与 `write_block_reason`（按档位）是两条线**：
  读遍历只剪自保护 + 系统只读区；软保护（桌面）只限制写、读必须放行 ——
  否则「扫描 C 盘」会**静默漏掉桌面**。`config.SAFETY_BLOCKLIST` 只给风险评级（写语义）用。
- **停进程只按进程名，绝不按端口猜**：曾看到某端口有 pid 就 `Stop-Process`，
  结果那是宿主自己的 `sandbox-cli.exe`，杀完连自己那条命令都断了。

---

## 三、打包与启动

- **`console=False` 的打包必须验一次 `pythonw.exe`**：那才是真实运行环境（`sys.stderr is None`）。
  要用 `RotatingFileHandler` 把日志落到 state 目录，并用 MessageBox 报启动失败 ——
  否则用户只看到"什么都没发生"。
- **`.spec` 与 `build_release.ps1` 是两条并存的出包路径，权威的是脚本** ——
  脚本用 PyInstaller 命令行参数、**不读 spec**。踩过：改了 spec 的 `console=False` 完全没生效，
  因为脚本没传 `--noconsole`。**改打包配置前先确认哪条路径才是真正出包的。**
- **打包时 `--noupx` 是必须的**：UPX 压 pythonnet 的 `Python.Runtime.dll`（.NET 程序集）会压坏它。
  本机没装 UPX 不代表别人的机器没装 —— 不能依赖"碰巧"。
- **PyInstaller 构建会在 CWD 生成 `%SystemDrive%\ProgramData\...\Caches\*.db`**：
  某组件把 `%SystemDrive%` 当字面量用了。**只发生在构建期，运行期不会有**。构建后看一眼仓库根。
- **前端 API 地址绝不能用绝对端口**：打包后由后端同源托管，而启动器会挑空闲端口。
  正解 = `import.meta.env.DEV ? 'http://127.0.0.1:8000/api' : '/api'`（dev 走绝对、build 走相对）。
  **不要把可用性押在 `vite.config.js` 的 proxy 上** —— 改那个配置**必须重启 dev server**，
  而用户手上那个往往不重启，于是 `/api/*` 被 Vite 的 SPA 兜底当成路由、**返回 index.html**，
  界面一片空。诊断特征：`GET 5173/api/health` → **200 但内容是 HTML**。
- **判断"要不要复用已有实例"不能只看健康接口**：开发模式的后端是**同一个应用**，
  照样答 `/api/health`，但它没有 `web/`，根路径是裸 404。**必须同时验证根路径真的返回 HTML。**
- **判"端口是否被占"不要用 `connect_ex`**：Windows 上设了 `settimeout` 之后它会返回
  `10035`(WSAEWOULDBLOCK) 而不是 0；而且探测本身会占掉对方 `listen(backlog)` 的槽位。
  **直接 `bind()` 才是权威判定**。更好的做法是**先自己 bind 好 socket，再交给
  `uvicorn.Server.run(sockets=[sock])`** —— "预检通过、真 bind 失败"的竞态就不存在了。
- **原生窗口（pywebview）的三个关键约束**：
  1. **窗口循环必须在主线程** → uvicorn 放后台线程，且必须
     `server.install_signal_handlers = lambda: None`，否则后台线程装信号处理器直接抛 `ValueError`。
  2. **必须 `private_mode=False`**：默认 `True` 会清空 localStorage，
     而 Agent 页靠它记住「当前是哪个会话」，清了每次开局都是新会话。
  3. `webview.start()` 阻塞到窗口关闭才返回 → 天然实现「关窗口 = 进程退出」。
- **想看 WebView2 窗口内部，接 CDP 就行**：
  给进程加 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9223`，
  再用 Playwright `chromium.connectOverCDP` 接上去。
  **被遮挡的 WebView2 窗口会节流、应用不 boot**：这时"窗口没发请求"**不能当 bug 证据**。

---

## 四、前端过渡 / 白屏

### 页面过渡（两版都试过，别再翻烙饼）

`mode="out-in"` → 旧页先消失、新页再淡入，中间有空白帧；
「两页同时交叉淡入」→ **照样漏底色**，新页 250ms 淡入期间不是 100% 不透明，
而内容区自己没有底色，透出的是 `body --bg`（浅色白一下 / 深色黑一下）。

**正解**：进场类**刻意不写任何规则**（新页首帧就 100% 不透明），
离场页 `position:absolute; z-index:2` 压在上层淡出 ——
任何一帧都有不透明页面垫底，结构上就不可能闪。

判定标准：rAF 逐帧读 `.workspace-content` 子元素 opacity，
背景可见度 = 1 − max(opacity)，`minMaxOpacity` **必须恒为 1**。

### 首屏必须 `router.isReady().catch().then(mount)`

直接 `mount` 会先画一帧「侧栏在、内容区空着」的半成品（实测 3 帧 ≈ 50ms）；
等就绪再挂载反而更快（首屏内容 358ms → 246ms）。

### 异步恢复的页面要加闸门

`Agent.vue` 的记忆恢复是异步的，`v-if="!messages.length"` 的空欢迎页会先闪出来 ——
用 `hydrating` ref 闸住。「新建任务」（用户主动要空白页）不受影响。

### Agent 页默认落在「新任务」欢迎页（详见 `docs/UI_REVAMP.md` v26）

用 `sessionStorage` 的 `filepilot.agent.booted` 区分「本次启动首访」与「同一次运行内切回」——
首访一律 `newConversation()`；同一次运行内**只认 localStorage 里那条**，
**不能**再用「stored 不在后端列表就退回最近一条历史」的老 fallback：
新建的空会话在发第一条消息前不会落库，一 fallback 就会把用户顶到一条不相干的旧对话上。

### 路由级懒加载 + 预取

13 个页面路由级懒加载 → `main.js` 在 `load` 后分批预取 chunk（间隔 160ms），
首访导航提交从"等 chunk"降到 21~77ms。

### `v-loading` 的遮罩是"白屏一下"的来源之一

（只有 Home / Jobs / Logs / Trash 用了它）
Element Plus 把 `--el-mask-color`（浅色下近白 `rgba(251,251,253,.72)`）**写成内联样式**，
所以 `global.css` 里那条 `.el-loading-mask { background-color: var(--glass) }` **从来没生效过**。

改法：`.el-loading-mask { background-color: var(--inset) !important; }` ——
必须 `!important`，方向从"提亮"改成"压暗"。
**别去改 `--el-mask-color` 本身**：它同时管 `.el-overlay`（弹窗遮罩），会波及所有弹窗。

### 长列表页的「白屏一下」= 主线程长任务 + 合成器淡出（详见 `docs/UI_REVAMP.md` v27）

`Logs.vue` 原本把 464 条日志一次性全渲染进 `el-table`（464 行 × 5 列，4 列带自定义 slot），
主线程阻塞 **1251ms**；而离场页的 `opacity` 淡出跑在**合成器线程**上、不受阻塞影响会一路跑到底，
新页图层还没提交绘制 → 屏幕上只剩空白底色。

**修法**：① 表格分页（每页 50）；② 灌数据前 `await nextPaint()`（双 rAF）先让「骨架 + 遮罩」提交绘制。
**诊断四件套**：CDP screencast 逐帧录屏 + rAF 读 `maxOp` + PerformanceObserver 抓 longtask + 对照实验。
注意长任务期间 rAF 回调**根本不执行**，只看 rAF 数据会漏掉空白帧。

` .el-loading-mask` 底色早就是 `rgba(120,120,128,0.1)`，**不是**这次白屏的来源。

### `<script setup>` 的顶层变量是「每实例」的

整块会编译进 `setup()` —— 想跨挂载保留必须放独立模块（如 `utils/overviewCache.js`）。

---

## 五、环境与验证

- **本机有 `http_proxy`（`http://127.0.0.1:65042`），连 `127.0.0.1` 也劫持**：
  Python `urllib` 打打包版的随机端口会拿到 **502 Bad Gateway**。
  解法：`urllib.request.build_opener(urllib.request.ProxyHandler({}))` 显式绕开；
  curl 用 `--noproxy '*'`。
- **打包版启动日志里可能有两个「界面地址」**：上一轮没退干净的 Atlas 还活着，
  `grep | head -1` 会打到死端口（直连返回 000）。取 **`tail -1`**，
  并先 `Stop-Process -Name Atlas` 清场。
- **后台起进程 / 打包必须用工具的后台模式**：命令里写 `cmd &` 会在该次调用返回时被一起收走
  （日志空、端口没人听）；**PyInstaller 在前台跑也会被中途掐掉**（SIGTERM，日志停在 `Processing hook`）。
- **跑 `tests/*.py` 要带 `PYTHONPATH=.`**；通过的测试**多数静默退出**（不打印汇总），
  看退出码不要找输出行。`tests/smoke_test.py` 是端到端总闸（73 项），另有 13 个专项测试文件。
- **`smoke_test.py` 要显式给 `FILEPILOT_API`**：默认打到 `127.0.0.1:8000`（开发服务器，
  **配了 DeepSeek Key**），于是「没配 Key」那条 `else` 分支**在本机永远不执行** ——
  里面一条断言漂了很久没人发现（见下）。用临时数据目录另起一个测试后端再指过去，两条分支才都覆盖到。
- **别把测试写成"设备相关的假设"**：那条断言写的是 `reply.source == "none"`，
  实际回复的 `source` 是 `"local"`（界面显示「本地规则」）；`"none"` 其实来自
  `/agent/status` 里「Key 来源」那个**同名字段**。要守住的是**实质**（`plan.action == "none"`
  + 给的是追问 + 没有凭空生出的执行步骤），不是字段的某个取值。
- **benchmark 有三个坑（全踩过）**：
  1. 取 `min` 多轮时**每轮都要清空被缓存的东西** —— 查重的哈希缓存不清，
     第 2、3 轮命中的是缓存，量到的是"缓存"不是"计算"。
  2. **cProfile 对多线程会失真**（只 profile 主线程），并行相关的结论必须用无 profiler 的干净计时。
  3. 想精确量某处改动的收益，**把旧实现 monkeypatch 回去做消融对照**，
     比"改前跑一遍、改后跑一遍"可靠得多（同进程、同批文件、只切换被测实现）。
- **跑脚本前先看它会不会写盘**：`run_duplicate_scan` 收尾会 `_flush_hash_cache()` ——
  在临时脚本里直接调它，会把用户真实的那份 4.1MB 哈希缓存**覆盖成测试数据**。
  先设 `FILEPILOT_DATA_DIR` 指向临时目录。
- 用 Playwright 真实渲染验证；`vite build` 会撞批量删除守卫；抓过渡/时序问题用 rAF 逐帧采样。

---

## 六、样式 / Element Plus

- **深色模式的选择器必须压过 Element Plus 的 `html.dark`**：
  `theme-chalk/dark/css-vars.css` 把整套调色板声明在 **`html.dark`（0,1,1）** 下，
  而原来的深色块是 **`[data-theme="dark"]`（0,1,0）** —— **低一层，全被压掉**，
  后果是**深色下全站 EP 控件都用 EP 默认色**（`#409eff` 蓝 / `#67c23a` 绿 / `#e6a23c` 橙）。
  浅色没事，因为浅色令牌在 `:root` 与 EP 同优先级、靠后加载取胜。
  **正解**：深色块选择器用 `:root[data-theme="dark"]`（0,2,0），并把 EP 映射整套重述一遍。
- **找"有没有现成规则"不能只 grep `^\.foo`**：复合选择器（`.a .foo`、`.a > .foo`）全都匹配不到。
  踩过：想给 `.option-block` 加"跟随容器变宽"，用 `^\.option-block` 查觉得没有，
  就新加了一条 `flex: 1 1 360px` —— 实际早就存在 `.form-page .option-block { flex: 1 1 0 }`，
  把新规则按优先级压死、**完全不生效**。**用 `grep -rn "\.foo\b"` 不加行首锚点才找得全。**
- **表单页宽度只留一道上限**：`.form-page` 原来自己卡 1120，而 `--content-width` 是 1460 ——
  两层嵌套上限让 8 个表单页在宽屏下左右各空约 286px。现在统一由 `--content-width` 收口。
  **嵌套的宽度上限是一类隐蔽的"内容缩在中间"来源。**
- **对比度自动扫描只能找嫌疑，定论要回到单元素直接量**：自己写的扫描器报过一处
  1.58:1 的"问题"，实测是 4.3:1 —— 它累加祖先 alpha 背景时算错了。
- **全站没配 Element Plus 中文 locale**：`el-pagination` 的 `total` 版式会渲染成英文 `Total 464`、
  `jumper` 是 `Go to`。新增带分页的页面时，条数文案自己写（`共 N 条`），别用 `total` 版式。
- EP 自动按需引入：命令式调用（`ElMessageBox` / `ElMessage`）需显式 import theme-chalk 样式。
- 全局输入聚焦无蓝框；Chrome 自动填充蓝底是浏览器行为，不是本站样式。
- **hover 浮层的两个"点不到"陷阱**（消息操作行，两处都要防）：
  1. **浮层前不要用 `margin` 挪位置** —— 消息底边与浮层顶边之间的那几 px 是死区，
     光标跨过时既不在父元素也不在浮层里，父级 `mouseleave` 立刻触发，浮层当着面消失。
     改用 `padding-top`：浮层盒子从父元素底边**紧贴**开始，留白感一样但没有死区。
  2. **浮层伸到兄弟节点上时会被兄弟盖住** —— `position:absolute; top:100%` 的浮层高度约 23px，
     而相邻消息只隔 12px，后来者居上。修法：`父:hover { z-index: 3 }` 抬一层，
     并给浮层 `background: var(--surface)` 免脏底。
  验证手法：量 `浮层top − 父bottom`（必须为 0），再 `document.elementFromPoint(按钮中心)`
  确认命中的就是那个 `BUTTON` —— 只截图证明不了可点。
- **间距只从 `--space-1..6`（4/8/12/16/24/32）取**，字号只用 `--fs-title/card-title/body/note/meta/micro`。
  散落的 5px / 7px / 13px 会让整页节奏变脏，而节奏脏正是「不够精致」的主因。

---

## 七、记忆 / 数据库 / 杂项

- **SQLite 没有迁移机制**：`connect()` 只跑 `CREATE TABLE IF NOT EXISTS`，对已存在的表什么都不做 ——
  光改建表语句**补不上老库的列**。加列必须另写一段 `PRAGMA table_info` 查缺 + `ALTER TABLE`（见
  `database._ensure_memory_payload()`），并且要用**旧 schema 的库**单独验一次、确认老行没被破坏。
- **Agent 对话记忆分两栏存**：`content` 给模型（`build_context_memory` 只读 role/content），
  `payload` 给界面还原计划卡。payload 走 `MEMORY_PAYLOAD_KEYS` 白名单 —— 存整份会把磁盘剩余空间
  这类一次性数据冻进历史，第二天就是错的。老记录没有 payload，退化成纯文本，**不拿正文反推假计划**。
- **Agent 记忆分层**：`memory`（对话历史）与 `state`（最近意图快照，含 `recent_paths`）**按会话隔离**；
  `prefs` 只放**用户手设**的长期设置（排除项 / 永久约束 / 常用清理策略），仍全局生效。
  **自动攒的"用过的目录"不许进全局** —— 那会让新对话"记得"你在别的对话里逛过哪，
  也就是「每个对话共用一个记忆」。喂模型的 `prefs` 会剔掉 `common_paths`（双保险）。
- **查"谁在写这个字段"要把整个后端 grep 一遍**：这次找全局自动写入时，
  顺着 `_remember_path` 看只找到一处，**`main._launch()` 里还有一处更频繁的** —— 只看第一处就会改漏。
- **`iter_files(max_depth)`：0 / None 都表不限深度。**
- 隔离区跟源盘走，固定为 `<盘符>:\_filepilot_trash`（跨盘 `shutil.move` 会退化成复制+删除）。
- `is_blocked` 不能用来剪只读遍历。
- 侧栏 `navGroups` 与路由独立，只加路由不会显示。

---

## 八、任务并发（实测数据）

### 上限是 2，其余排队

`JobManager` 的 `ThreadPoolExecutor(max_workers=2)`。实测连开 5 个任务（每个 1.5s）：

```
 0.04s  RR···      ← 只有前两个真正在跑
 1.62s  DDRR·
 3.19s  DDDDR
 4.75s  DDDDD
```

**同时 running 的峰值 = 2**。第 3 个起停在 `pending`，界面上显示 **「排队中」**
（`JobProgress.vue` 的 `STATUS_META.pending.label`）。注意 `JobContext.progress()` 只有在
worker 真正被调度起来（`runner()` 里设 `status=running`）之后才会跑，
所以排队阶段没有进度是正常的，不是卡住。

### ★ 重活同时跑两个会更慢，不是更快

查重现在每个任务开 `HASH_WORKERS`（默认 8）个线程。两个查重同时跑就是 16 个线程 —— **过订阅**。
实测（`ms-playwright`，冷缓存，同一块 NVMe）：

| 同时提交 | 总墙钟 | 每个任务 |
|---|---|---|
| 1 个 | 0.64s | 0.58s |
| 2 个 | 1.46s | 1.35s / 1.31s（**每个慢 2.3×**） |
| 3 个 | 2.09s | 1.12s / 1.11s / **0.33s** |

按吞吐算：1 个是 1.72 次/秒，2 个并发降到 1.37 次/秒 —— **总吞吐反而低了 20%**。

**结论：重 I/O 任务（查重 / 迁移 / 备份）串行跑更快。**
轻任务（扫描 / 空间地图）之间并发无所谓，扫描 + 迁移这种"一轻一重"也没问题。

**但有个副作用是好的**：`_HASH_CACHE` 是**进程级共享**的，
所以第 3 个同目录任务只要 **0.33s**（前两个已经把哈希灌进内存了）——
重复扫同一个目录时，排队反而省事。

### 解法：进程级读盘闸门（已实现）

并发度不能**按任务**算。`fsutil._io_slot()` 是一个进程级信号量，名额数 = `config.HASH_WORKERS`，
**所有读文件的哈希（`hash_file` / `quick_hash`，以及走它们的复制校验）都要从这里过**：

```python
with _io_slot(), path.open("rb") as stream:   # 串行调用方几乎不花钱（无竞争时几十纳秒）
    ...
```

- 任务少时照样吃满 8 路；两个查重同时跑就各分一半 —— 总并发读盘数**恒 ≤ HASH_WORKERS**。
- **抢不到名额的工作线程阻塞在信号量上，不烧 CPU**，所以每任务仍然开 8 个线程没关系。
- 闸门**懒建** + `fsutil.reset_io_gate()`，改完 `config.HASH_WORKERS` 能重建（测试用）。
- 单任务的多线程收益**不受影响**：实测 1→8 路仍是 **1.65×**。

**效果**（同一目录、冷缓存、同一块 NVMe）：

| 同时提交 | 加闸门前 | 加闸门后 | 吞吐变化 |
|---|---|---|---|
| 1 个 | 0.64s（1.72/s） | 0.51s（1.95/s） | — |
| 2 个 | 1.46s（**1.37/s**） | **0.92s（2.18/s）** | **+59%** |
| 4 个 | — | 2.77s（1.44/s） | — |

每个任务的耗时也从 1.35s 降到 0.82s —— 不再互相踩踏。

验证手法：把 `fsutil._io_slot` 换成一个**在拿到名额之后、放开之前计数**的探针，
就能量到"同时持有名额"的峰值。实测 `HASH_WORKERS=3/1/6` 时峰值并发正好是 3/1/6。

> 注意：`copy_file`（迁移/备份的复制本身）**没有**过这道闸门 ——
> 复制是单线程的、每个任务只占一路，不存在过订阅问题。
> 将来如果给复制也加并行，记得一并接进来。

---

## 九、Agent 对话延迟（实测）

「Agent 回复慢」要先分清是**哪条路径慢** —— 本地规则和模型调用的耗时结构完全不同。

### 先量：本地路径本来就不慢

逐项测下来：`status_context()` 1.06ms（含 disk_stats 0.30 / list_trash 0.66）、
`append_memory` 2.37ms/次、`load_memory`+`build_context_memory` 1.11ms、`parse_local` 0.02ms。
**`chat()` 全流程（零 token 路径）只要 18ms。**

所以本地回复"感觉慢"**不是计算问题，是人为加的延迟**：

```python
def _chunks(text, size=3): ...          # 每 3 个字符一帧
...
for piece in _chunks(msg):
    yield {"type": "delta", "text": piece}
    time.sleep(0.015)                   # ← 一条 200 字的追问 = 67 次 × 15ms ≈ 1 秒
```

本意是"打字机效果"，代价是**本地回复被硬拖 0.1~1.0 秒**，而计算本身只要 18ms。
前端本来就是逐帧渲染 delta 的，拆帧只是让它少拼一次长字符串，不是节流阀。

**修法**：块从 3 → 24，`sleep` 删掉。效果（流式端到端，含 HTTP）：
本地意图 **102ms**、追问 **26ms**，事件序列（phase/delta/done）不变。

### 模型路径：本地能省的只有握手，剩下的在模型

- `urllib.request.urlopen` 每次新建 TCP+TLS，实测到 `api.deepseek.com` 握手 **175ms**。
  但相对一次 3~6s 的生成只占 3~5%，**不值得**为此引入连接池的失效重连复杂度。
- 想再快只能动两头：**砍输出 token**（生成是大头，prompt 里要求的 JSON 越啰嗦越慢）
  或**砍上下文**（memory 预算 6000 字符 ≈ 2K token，砍一半能省零点几秒，但影响记忆质量）。
- DeepSeek 的 key 在**运行中的 dev server 环境**里、磁盘上找不到 ——
  想实测 API 延迟得先让 `resolve_key()` 拿得到，否则只会得到 401。

---

## 十、任务进度卡：为什么"看起来卡死"

用户截图反馈「百分比和秒数不是实时显示的」。**拆开看是两个独立的缺陷**，
而且都不能靠"多推点事件"解决。

### 1. 秒数：computed 里读 `new Date()` 是死的

```js
const elapsed = computed(() => {
  const end = finished ? new Date(finished) : new Date()   // ← new Date() 不是响应式依赖
  ...
})
```

computed 只在**依赖变化**时重算；`props.job` 被 SSE 替换时才重算，
于是**后端两次事件之间有多久，秒数就冻多久**。

**修法**：挂一个自己会走的时钟，让 computed 依赖它。

```js
const nowTick = ref(Date.now())
let ticker = null
watch(isRunning, (running) => {
  stopTicker()
  if (running && !props.job?.finished_at) {
    nowTick.value = Date.now()
    ticker = setInterval(() => { nowTick.value = Date.now() }, 1000)
  }
}, { immediate: true })
onBeforeUnmount(stopTicker)
```

**同类信号**：任何"相对现在"的显示（已运行多久、还剩多久、多久前）都要挂时钟，
不能只靠数据推送驱动。

### 2. 百分比：预热统计阶段**必然**没有分母

要真百分比就得先数一遍（`count_entries`），而"数"这个动作**本身没有分母**。
实测把 `count_entries` 拖慢到 15 秒后抓 SSE：

```
 t(s)  percent  message
 0.02        0  正在统计条目数（用于进度）
 ...     （15 秒内 ~300 条事件，percent 全是 0，消息一字不变）
15.29      100  已完成
```

**事件在推，但每条都不带新信息** —— 界面看起来就是卡死的。
Agent 任务的 `agent_task._publish` 更糟：`total=None` 时 `fraction` **写死 0.5**，
整段恒等于 `(index + 0.5) / total`（用户截图里的 35% 就是 `(2+0.5)/7`）。

**修法（三层，都不撒谎）**：

- **把实时条目数写进消息**：`"正在统计条目数 —— 已数 12,345 项"`。
  这一刻唯一真实且在动的信号就是它。
- **没有总数就不摆死数字**：进度条走**不确定态**（一个来回扫动的片段），
  右侧只留实时秒数，不再显示一个恒为 0% 的百分比。
- **运行中的条子加一道流动高光**：宽度仍是真实百分比，高光只表示"还在动"。

**别给没有分母的阶段编一个平滑上涨的假百分比** —— 项目一贯不假装精确
（同 `_bounded_walk` 超限如实标 `truncated`）。

### 3. 验证：组件探针 + 真实 Job 集成，两样都要

只验组件不够（证明不了真数据对不对），只验集成也不够（证明不了三种形态都渲染对）。

- **组件探针**：一个临时 `.html`，`import JobProgress from '/src/components/JobProgress.vue'`，
  喂 4 种假 job（无 total / Agent 35% / 有 total / 已完成），跑在**用户已开着的 dev server** 上
  （新增的 html 会被 Vite 直接服务，不用另起服务）。探针读 `getComputedStyle` 与 `::after` 的
  `animationName`，**不靠截图判断**。
- **集成探针**：临时后端 + 探针页**自己 POST 建任务**再轮询
  （先建任务再开页面会错过开头）。它同时验证了：真实 job 字段形状、SSE 推送、
  秒数逐秒递增（1→…→16）、百分比推进（0→69）、预热阶段真的走不确定态。
- **CORS 是按 Origin 判的**：探针页跑在 5173 上（白名单内），
  可以正常请求另一个端口（8070）的临时后端 —— 不需要改 CORS 配置。

### 4. 顺带踩到：批量删除守卫有「每轮配额」

本轮累计删除超过阈值后，**之后任何一条带删除的命令都会整条被中止**
（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，`scope: "turn"`），
连同一条命令里的其它动作也一起不执行 —— 表现是"命令莫名退出、目标文件也没生成"。

- **PyInstaller 的 `--clean` 会撞它**（它要清空 `build/<name>/`）。
  打包前先用脚本**逐个文件**清掉那个目录，然后**不要带 `--clean`**。
- 删除配额耗尽后，**改成"全程不删除"**：PyInstaller 用尚不存在的输出路径就不用删旧产物，
  组装用 `mkdir + cp` 即可；只有最后覆盖 zip 那一步需要删 1 个文件。
- 更稳的做法：**清理和构建/组装别写进同一条命令**，免得清理被拦时把构建也带崩。

---

## 十一、数据可视化：两个吃时间的坑

### 1. 内联 `background` 简写会静默清掉 `background-image`

```html
<div :style="{ background: token }">   <!-- ✗ 简写把 background-image 重置成 none -->
```
```css
.seg { background-image: linear-gradient(180deg, var(--chart-sheen), transparent); }
```

内联样式的优先级永远高于样式表里的同类声明 —— `background` 是**简写**，
它会把 `background-image` 一并重置。于是「样式表里明明写了渐变，页面上就是没有」。

**修法**：内联只写 `backgroundColor`，把 `background-image` 留给样式表。

**怎么发现的**：探针里读 `getComputedStyle(el).backgroundImage`，拿到 `none`。
**别靠截图判断渐变有没有生效** —— 浅色下 30% 的白色高光在截图里几乎看不出来。

### 2. `depth` 之外的文件不参与统计 → 目录会算成 0 字节

`build_tree` 里 `level >= depth` 的目录只生成 `{"size": 0, "collapsed": True}`。
后果比"看不到深层"严重得多：**一个只包含子目录的目录，体积会被算成 0**。

- `depth=2` 时，`media/`（里面只有 `4k/`、`photos/`）→ **0 字节**
- 界面若按 `size > 0` 过滤，**整层里一半条目直接消失**，页面上是一片空白

**两条对策**（都要做）：
- **默认 3 层**（后端 `TREE_DEFAULT_DEPTH` 本来就是 3，是前端写死成 2 才踩的）
- **折叠节点不要丢**：标成「未展开」并且可点进去，而不是静默过滤掉。
  "看不见"和"没有"是两件事，界面不能把前者画成后者。

### 3. 顺带：界面上的数字要给"口径"，不能只给"数值"

同一个数字（`3.4 GB`）在不同口径下意思完全不同 —— 排除了系统目录吗？算到第几层？
**把口径写在数字旁边**（「只统计可写目录 · 每层算到第 3 层 · 已跳过 …」），
并且给数字起一个**说明它是什么**的名字（「可清理范围」而不是裸的「3.4 GB」）。
用户看到"C 盘 3.4 GB"会以为 C 盘只用了 3.4 GB —— 这不是用户看错了，是界面没说。

### 4. 深色主题下的「轨道 / 底槽」必须提亮，否则整块设计散架

条形图、进度条、构成条都要一条**可见的底槽**。浅色下随手一个浅灰就够，
深色下如果还沿用「比背景深一点」的思路（`#12293f` 贴 `#1c1c1e`），
结果就是**条子变成悬空的线段** —— 没有容器，比例完全读不出来，
用户看到的是"一排随机长度的蓝线"，不是图表。

**深色底槽用半透明白**（`rgb(255 255 255 / 9%)`）比用实心深蓝稳：
它自动跟着卡片底色走，换底色也不会失效。

### 5. 排行榜里 `<1%` 的项要合并，不要硬列

一排 `<1%` 的行，条子缩成一个点，视觉上就是"一堆空行" —— 真正的重点被淹掉。
**只收占比 ≥ 1%（或前 3 名保底），其余合并成「其他 N 项」**。
同理，条长**按最大项归一**而不是按占比：按占比的话 37% 只占三分之一格，
27% 与 26% 的差别在屏幕上等于零。

### 6. `.app-card` 没有内边距 —— 内边距在 `.panel-card` 上

本项目的卡片是两段式：`.app-card` 只给边框 / 圆角 / 阴影，**`.panel-card` 才给 `padding: 24px`**。
新写一张卡片时只写 `class="app-card"`，内容就会**贴着边框**——
不报错、不影响功能，只是"看着没有页边距、很挤"。

**发现方式**：截图看不出来是 class 问题，量 `getComputedStyle(card).padding` 才是 0 才确定。
写新卡片时**照抄同页已有卡片的 class 列表**，别只写 `app-card`。

### 7. 两组"看起来一样"的网格，列宽不相等就是不协和

同一张卡里既有指标块又有卡片网格时，如果它们各自成网格、且**可用宽度不同**
（例如其中一组被右侧的插图挤窄了 200px），列宽就会是 379 vs 305 —— 竖线全部错开，
用户说不出哪里不对，但会说"不协和"。

**对策**：让它们**共用同一个三列网格**（同一容器宽度 + 同一 gap），
或者干脆把挤压宽度的元素（插图）挪出这一行。
验证方法：量两组元素各自的 `getBoundingClientRect().left`，必须逐一相等。

### 6. `.app-card` 没有内边距 —— 内边距在 `.panel-card` 上

本项目的卡片是两段式：`.app-card` 只给边框 / 圆角 / 阴影，**`.panel-card` 才给 `padding: 24px`**。
新写一张卡片时只写 `class="app-card"`，内容就会**贴着边框**——
不报错、不影响功能，只是"看着没有页边距、很挤"。

**发现方式**：截图看不出来是 class 问题，量 `getComputedStyle(card).padding` 才是 0 才确定。
写新卡片时**照抄同页已有卡片的 class 列表**，别只写 `app-card`。

### 7. 两组"看起来一样"的网格，列宽不相等就是不协和

同一张卡里既有指标块又有卡片网格时，如果它们各自成网格、且**可用宽度不同**
（例如其中一组被右侧的插图挤窄了 200px），列宽就会是 379 vs 305 —— 竖线全部错开，
用户说不出哪里不对，但会说"不协和"。

**对策**：让它们**共用同一个三列网格**（同一容器宽度 + 同一 gap），
或者干脆把挤压宽度的元素（插图）挪出这一行。
验证方法：量两组元素各自的 `getBoundingClientRect().left`，必须逐一相等。

---

## 十二、`waiting` 是第三种状态，不是"跑完了"

用户报："我执行任务，从扫描文件到我确认执行阶段的时候，这个任务会从选项卡里面消失。"

**后端一直是对的**：暂停的任务 `status=waiting`、`finished_at=None`，
`JobManager.active_count()` 也把它算作活跃。三个前端问题叠在一起，看起来就成了"任务没了"：

| 病灶 | 代码 |
|---|---|
| 任务中心写「**进行中 0 个**」，同一页右侧却写着「等待确认」 | `jobs.filter(j => ['running','pending'].includes(j.status))` —— **漏了 `waiting`** |
| **刷新页面后 Agent 页的任务卡没了** | `useJob` 的 `ACTIVE_JOBS` 是**模块级内存 Map**，刷新即空；`restore()` 查不到槽位就清掉 |
| 任务中心里**没有「继续执行」** | resume 的入口只有 `/api/agent/task/{id}/approve`，而它只被 Agent 页的计划卡调用 |

### 三条规则

1. **凡是"还在等用户"的状态，都要算进"活跃"**：写 `includes(status)` 这类判断时，
   先问一句「暂停/等待算不算」——`running` / `pending` / `waiting` 是一个族。
   计数还要**分开显示**（进行中 N · 待确认 M），因为两者的"下一步动作"完全不同。
2. **内存里的槽位（`ACTIVE_JOBS` 这类）刷新就没了 —— 必须有从后端认领回来的路径**：
   会话/作用域定下来、槽位为空时，去后端查一次「属于我这里、且停在 waiting 的任务」并接回来。
   只靠内存，用户刷新一次任务就"消失"。
3. **任何一个可暂停的任务，都要在当前页面之外留一个续跑入口**：
   否则用户在别的页面看到它，只能「中断」，等于被迫放弃。

**复现方式**：跑一个 agent_task 到 `waiting`（分析段结束），
然后 `page.reload()` → 修之前 Agent 页任务卡为 0，修之后能认领回来并出现「继续执行」。

---

## 十二、`waiting` 是第三种状态，不是"跑完了"

用户报："我执行任务，从扫描文件到我确认执行阶段的时候，这个任务会从选项卡里面消失。"

**后端一直是对的**：暂停的任务 `status=waiting`、`finished_at=None`，
`JobManager.active_count()` 也把它算作活跃。三个前端问题叠在一起，看起来就成了"任务没了"：

| 病灶 | 代码 |
|---|---|
| 任务中心写「**进行中 0 个**」，同一页右侧却写着「等待确认」 | `jobs.filter(j => ['running','pending'].includes(j.status))` —— **漏了 `waiting`** |
| **刷新页面后 Agent 页的任务卡没了** | `useJob` 的 `ACTIVE_JOBS` 是**模块级内存 Map**，刷新即空；`restore()` 查不到槽位就清掉 |
| 任务中心里**没有「继续执行」** | resume 的入口只有 `/api/agent/task/{id}/approve`，而它只被 Agent 页的计划卡调用 |

### 三条规则

1. **凡是"还在等用户"的状态，都要算进"活跃"**：写 `includes(status)` 这类判断时，
   先问一句「暂停/等待算不算」——`running` / `pending` / `waiting` 是一个族。
   计数还要**分开显示**（进行中 N · 待确认 M），因为两者的"下一步动作"完全不同。
2. **内存里的槽位（`ACTIVE_JOBS` 这类）刷新就没了 —— 必须有从后端认领回来的路径**：
   会话/作用域定下来、槽位为空时，去后端查一次「属于我这里、且停在 waiting 的任务」并接回来。
   只靠内存，用户刷新一次任务就"消失"。
3. **任何一个可暂停的任务，都要在当前页面之外留一个续跑入口**：
   否则用户在别的页面看到它，只能「中断」，等于被迫放弃。

**复现方式**：跑一个 agent_task 到 `waiting`（分析段结束），
然后 `page.reload()` → 修之前 Agent 页任务卡为 0，修之后能认领回来并出现「继续执行」。

---

## 十三、MCP server：两个 Windows 专属的坑（都会让客户端收到乱码或连不上）

`backend/mcp_server.py` 是手写的 JSON-RPC over stdio（不用官方 SDK，保持零新增依赖）。
在 Windows 上打包成 `--noconsole` 之后，踩到两个必踩的坑：

### 1. `--noconsole` 的 GUI 子系统里，`sys.stdin/stdout/stderr` 可能是 None

MCP 靠 stdin/stdout 说话。PyInstaller 打 GUI 程序时 Python 启动会把这些标准流置成 `None`，
于是 `for line in sys.stdin` 直接炸 —— 而客户端看到的是"服务器连不上"，没有任何有用信息。

**修法**：拿继承来的文件描述符重新包起来。

```python
if sys.stdout is None:
    sys.stdout = io.TextIOWrapper(io.open(1, "wb", closefd=False),
                                  encoding="utf-8", errors="replace", newline="\n")
```

### 2. ★ Windows 上标准流的默认编码是**控制台代码页**，不是 UTF-8

这条最阴：简体中文环境下 Python 默认按 **GBK** 编码标准流。
初始化响应里没有中文，看着一切正常；一旦 `tools/list` 返回**中文工具描述**，
发出去的就是 GBK 字节，客户端按 UTF-8 解 → **乱码**。

MCP 协议规定 UTF-8，所以必须显式强制：

```python
sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")
```

`newline="\n"` 也要顺手设上：协议是**按行分帧**的，别发 `\r\n`。

### 顺带：测试这个 server 时别用 `subprocess.run(text=True)`

GBK 的 stderr 会让 `communicate()` 里的解码线程抛 `UnicodeDecodeError`，
**子进程不会被回收**，表现成"卡住 5 分钟然后超时"。
用 `capture_output=True` 拿 bytes，自己按 UTF-8 解。

这个假故障让我怀疑了半天"打包版 MCP 坏了"，其实服务本身好好的。

---

## 十四、感知哈希（dHash）：三个必须知道的边界

相似图片查重用的是 dHash（缩成 9×8 灰度、比相邻像素明暗，得到 64 位指纹）。
实测下来有三条边界，**不知道就会以为算法坏了**：

### 1. 纯色 / 极平滑的图，指纹是**全 0**，会把互不相干的图判成"相似"

一张纯红、一张纯绿，dHash 都是 `0x0000000000000000` —— 放进分组就误报。

**必须过滤退化指纹**（全 0 / 全 1）：

```python
ones = bin(value).count("1")
return 0 < ones < 64      # 两极都说明这张图没有明暗起伏
```

宁可漏掉（纯色图本来就没什么可查的），也不能误报。

### 2. 噪声图 / 太"随机"的图，dHash 也不准

dHash 靠的是**相邻像素的相对明暗**。整幅随机噪声（或我造测试图时生成的纯噪声）里，
相邻像素的大小关系本身就是随机的，压到 9×8 之后更是如此 ——
"同一张图缩放后"的两个指纹可能差出十几个位。

**结论**：用**有结构**的图测试（渐变 + 形状 + 线条），不要用噪声。
真实照片/截图的信息量正好落在这个区间，所以生产可用。

### 3. dHash **对旋转不免疫**

转 180° 会让"相邻左右比较"的方向反过来，指纹变化很大。
这是 dHash 的固有性质（pHash 用 DCT 会好一些，代价是慢）。
要不要补旋转不变性，取决于实际有没有"转过的同一张图"要查 —— 目前不做，但要知道有这个漏。

### 4. 相似 ≠ 相同，界面必须分开

精确查重（SHA256）的结论是"这两个文件一模一样"，可以直接给"清理"按钮；
相似查重的结论只是"看起来像"，**删哪张必须人看图**。
两者混在一个列表里 = 迟早误删。所以相似结果是**独立卡片**，
只列出来、标注"建议保留"，不提供一键清理。

---

## 十五、argparse：`--json` 写在子命令后面就失效了

```python
parser.add_argument("--json", action="store_true")          # 只在主 parser 上
sub = parser.add_subparsers(...)
sub.add_parser("duplicates").add_argument("path")
```

`cli duplicates D://x --json` **会报错** —— 子命令不认识这个开关。
而人几乎总是把这类开关写在最后。

**照抄 `parents=[common]` 也踩坑**：子命令上那份的 `default` 会**覆盖**主 parser 已解析好的值，
于是 `cli --json overview` 反而丢了 `--json`。

**正解**：子命令那份的 default 用 `argparse.SUPPRESS` —— 没写就不碰这个属性，
主 parser 上的值才保得住。两头都能写，值不互相覆盖。

## 十六、替换脚本幂等（第四次踩，这次要写死）

同一段「查找 → 替换」的脚本被**执行两次**时，如果**锚点是替换串的前缀或者包含在替换串里**，
第二次就会插进第二份。这次的表现是：**整张「相似图片」卡片在页面上渲染了两遍**，
以及 Vue 报 `Identifier 'xxx' has already been declared`。

**写法要求**：

```python
def sub(path, old, new, why):
    t = Path(path).read_text(encoding="utf-8")
    if new.strip() in t:            # ★ 先看目标状态在不在
        return "already"
    if old not in t:
        return "anchor-missing"     # 找不到锚点要**报出来**，不要静默跳过
    Path(path).write_text(t.replace(old, new, 1), encoding="utf-8")
```

**替换串不要以锚点开头**（`old = "const x = ref('')"`、`new` 也以这句开头 → 必然可重复执行）。

排查手段：改完立刻数一遍关键标识的出现次数（`grep -c`），
**期望值要提前写出来**（import + 调用 = 2，声明 = 1）。这次就是靠"卡片渲染两遍"的截图才发现的。

---

## 十七、在 `<script setup>` 里插代码：先确认被引用的 ref 已经声明（暂时性死区）

**症状**：**整页白屏**，主内容区一个元素都没有；控制台只有一句
`ReferenceError: Cannot access 'z' before initialization`（打包压缩后变量名变成一个字母，根本看不出是哪个）。

**原因**：`useHandoffTarget(path)` 这类调用**在 setup 执行期间就会读 `path`**。
如果 `const path = ref('')` 写在它后面 —— 撞上 JavaScript 的暂时性死区。

**为什么特别难查**：
- 编译不报（`vite build` 退出码 0）
- 模板里也看不出来（报错在 script 的求值顺序，不在模板）
- 页面上什么都不显示，只有控制台一句没有上下文的 `Cannot access 'z'`

**可复用的检查**（这次靠它一次扫出全部 6 个页面）：

```python
import re
m = re.search(r"useHandoffTarget\((\w+)\)", text)
decl = re.search(rf"const {m.group(1)} = ref\(", text)
assert text[:decl.start()].count("\n") < text[:m.start()].count("\n"), "顺序反了会撞死区"
```

**规矩**：用脚本往 `<script setup>` 里插代码时，**先把被引用变量的声明行号打出来**，
确认插入点在它之后 —— 别凭"看起来在前面"判断。
