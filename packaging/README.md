# Atlas Windows 打包

## 怎么出包

运行 `packaging/build_release.ps1`。脚本会：

1. 构建前端（`npm run build`）
2. 创建隔离的打包虚拟环境 `.venv-packaging` 并装 PyInstaller
3. 用 PyInstaller 生成 `release/Atlas/`
4. 把 `frontend/dist/*` 复制成 `release/Atlas/web/`
5. 把 `Atlas.iss` 复制到 `release/`

随后用 Inno Setup 的 `ISCC.exe` 编译 `release/Atlas.iss`，产出 `Atlas-Setup-x.y.z.exe`。

```powershell
.\packaging\build_release.ps1
& "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe" .\release\Atlas.iss
```

## 唯一权威的构建方式是这个脚本

`Atlas.spec` 只负责 PyInstaller 那一步，**单独用它只会得到只有后端的 `dist/Atlas/`**
（没有 `web/`，双击后只有 API、没有界面）。前端是靠脚本第 4 步补进去的。
spec 留着是为了调试；出包一律走脚本。

## 启动器行为（`packaging/launcher.py`）

发行版双击 `Atlas.exe` 之后：

- **不弹控制台窗口**（spec 里 `console=False`）
- **自动打开浏览器**到界面地址
- **端口**：优先 `8000`；被别的程序占用时自动换一个空闲端口，并写进日志
- **已有实例**：本机已经在跑 Atlas 时不再起第二个进程（两个进程写同一个数据目录会打架），
  直接把浏览器指过去
- **日志**：`%LOCALAPPDATA%\Atlas\atlas.log`（滚动，最多 1MB + 1 个备份）
  —— 没有控制台，这是唯一的排查线索
- **启动失败**会弹消息框并指向日志文件，而不是"什么都没发生"

环境变量：

| 变量 | 作用 |
|---|---|
| `FILEPILOT_PORT` | 指定端口（被占用时仍会自动换） |
| `FILEPILOT_OPEN_BROWSER` | 设 `0` 则不自动开浏览器（当后台服务跑时用） |
| `FILEPILOT_DATA_DIR` | 指定数据目录（默认 `%LOCALAPPDATA%\Atlas`） |

## 用户环境要求

发行版不需要用户安装 Python、Node 或 npm。
程序只监听 `127.0.0.1`，数据默认保存到 `%LOCALAPPDATA%\Atlas`。

> 注意：`Atlas.iss` 目前是 `PrivilegesRequired=admin` + 装到 `{autopf}`（Program Files），
> 安装需要管理员权限。若要免管理员按用户安装，改成
> `PrivilegesRequired=lowest` + `DefaultDirName={localappdata}\Atlas`。
