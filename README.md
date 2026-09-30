# FilePilot Lite

FilePilot Lite 是一个本地文件管理工具，提供文件扫描、重复文件识别、智能整理、备份和报告导出能力。项目采用 FastAPI 后端 + Vue 3/Vite 前端，文件处理默认在本机完成。

## 功能

- 扫描文件并按类型、大小和时间查看
- 通过哈希识别重复文件
- 预览整理方案并执行文件归类
- 创建备份、查看操作日志和导出报告
- 可选接入 DeepSeek 生成整理建议（需要配置 API Key）

## 技术栈

- 后端：Python、FastAPI、Uvicorn、Pydantic
- 前端：Vue 3、Vite、Element Plus、Axios

## 环境要求

- Python 3.10+
- Node.js 18+
- Windows（启动脚本为 `.bat` 格式）

## 安装与启动

```powershell
# 安装 Python 依赖
python -m pip install -r requirements.txt

# 安装前端依赖
cd frontend
npm install
cd ..
```

启动方式一：双击 `start_all.bat`。

启动方式二：分别运行：

```powershell
# 终端一：后端
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

# 终端二：前端
cd frontend
npm run dev
```

启动后访问：

- 前端：<http://127.0.0.1:5173>
- 后端 API：<http://127.0.0.1:8000/api>
- API 文档：<http://127.0.0.1:8000/docs>

## 可选配置

如需使用 DeepSeek AI 建议功能，请在启动后端前设置环境变量 `DEEPSEEK_API_KEY`。不要把真实密钥写入代码或提交到 Git 仓库。

PowerShell 示例：

```powershell
$env:DEEPSEEK_API_KEY = "your-api-key"
```

## 项目结构

```text
FilePilotLite/
├─ backend/             # FastAPI API 服务
├─ frontend/            # Vue 3 前端界面
├─ *.py                 # 文件扫描、整理、备份和报告模块
├─ start_*.bat          # Windows 启动脚本
├─ requirements.txt     # Python 依赖
└─ README.md
```

## 注意事项

文件移动、整理和备份操作会影响本地磁盘内容。执行批量操作前请先检查预览结果，并确保目标目录有足够空间。

## License

当前项目未指定开源许可证，默认保留所有权利。
