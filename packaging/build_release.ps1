$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root 'frontend'
$Release = Join-Path $Root 'release'
$PyInstaller = Join-Path $Root '.venv-packaging\Scripts\pyinstaller.exe'
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
& $PyInstaller --noconfirm --clean --onedir --name Atlas --paths $Root --collect-submodules backend --collect-data uvicorn packaging/launcher.py
Pop-Location
$Web = Join-Path $Release 'Atlas\web'
New-Item $Web -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $Root 'dist\Atlas\*') (Join-Path $Release 'Atlas') -Recurse -Force
Copy-Item (Join-Path $Frontend 'dist\*') $Web -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot 'Atlas.iss') $Release -Force
Write-Host "Release staged at $Release\Atlas"
