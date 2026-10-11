$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root 'frontend'
$Release = Join-Path $Root 'release'
$PyInstaller = Join-Path $Root '.venv-packaging\Scripts\pyinstaller.exe'
# Preflight: fail early with a readable message instead of a cryptic command-not-found.
# Messages stay ASCII on purpose (PowerShell 5.1 reads UTF-8 files without BOM as ANSI).
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw 'npm not found. Install Node.js first (it builds the frontend).'
}
if (-not (Test-Path $PyInstaller) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw 'Neither .venv-packaging nor python on PATH. Install Python 3 first.'
}
Push-Location $Frontend
npm run build
Pop-Location
if (-not (Test-Path $PyInstaller)) {
  python -m venv (Join-Path $Root '.venv-packaging')
  & (Join-Path $Root '.venv-packaging\Scripts\python.exe') -m pip install --upgrade pip pyinstaller -r (Join-Path $Root 'requirements.txt')
}
if (Test-Path $Release) { Remove-Item $Release -Recurse -Force }
New-Item $Release -ItemType Directory | Out-Null
Push-Location $Root
$Icon = Join-Path $PSScriptRoot 'atlas.ico'
# --noconsole：双击不弹黑窗口。
# --icon：窗口/任务栏/资源管理器里都用品牌图标（没有它就只能是 PyInstaller 的默认图标）。
# --hidden-import webview*：releases 里 webview 是懒加载的，显式带上更稳妥。
# UPX 绝不能用来压 pythonnet 的 Python.Runtime.dll（.NET 程序集被压会坏），
# 所以这里显式 --noupx —— 别让"碰巧装了 UPX 的机器"把包打坏。
& $PyInstaller --noconfirm --clean --onedir --name Atlas --noconsole --noupx `
  --icon $Icon `
  --hidden-import webview --hidden-import webview.platforms.edgechromium `
  --paths $Root --collect-submodules backend --collect-data uvicorn --collect-data PIL packaging/launcher.py
Pop-Location
$Web = Join-Path $Release 'Atlas\web'
New-Item $Web -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $Root 'dist\Atlas\*') (Join-Path $Release 'Atlas') -Recurse -Force
Copy-Item (Join-Path $Frontend 'dist\*') $Web -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot 'Atlas.iss') $Release -Force
Write-Host "Release staged at $Release\Atlas"

# ---- 校验和（对标 filepilot-ai 的 "checksum sidecars"）----
# 出包后同时产出一个 .sha256：以后拿到包先验一次，不怕传输坏。
# 格式跟 sha256sum 一致：<哈希>  <文件名>
$Zip = Get-ChildItem (Join-Path $Release '*.zip') -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Zip) {
  $Hash = (Get-FileHash $Zip.FullName -Algorithm SHA256).Hash
  $Side = "$($Zip.FullName).sha256"
  [System.IO.File]::WriteAllText($Side, "$Hash  $($Zip.Name)`n", (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "Checksum written to $Side"
}
