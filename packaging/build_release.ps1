$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root 'frontend'
$Release = Join-Path $Root 'release'
$Venv = Join-Path $Root '.venv-packaging'
$Python = Join-Path $Venv 'Scripts\python.exe'
$PyInstaller = Join-Path $Venv 'Scripts\pyinstaller.exe'
$Lock = Join-Path $PSScriptRoot 'requirements-release.lock'
$SmokeScript = Join-Path $PSScriptRoot 'smoke_release.ps1'
$NpmCache = Join-Path $env:TEMP 'atlas-npm-ci-cache'
$PipCache = Join-Path $env:TEMP 'atlas-pip-release-cache'
$RootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
$RootPrefix = "$RootFull\"

function Read-RequiredVersion {
  param([string]$Path, [string]$Pattern, [string]$Source)
  $Content = Get-Content -Raw -LiteralPath $Path
  $Match = [regex]::Match($Content, $Pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
  if (-not $Match.Success) { throw "Could not read version from $Source" }
  return $Match.Groups[1].Value
}

function Assert-GeneratedPathInsideRoot {
  param([string]$Path)
  $FullPath = [System.IO.Path]::GetFullPath($Path)
  if (-not $FullPath.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Generated build path escapes the repository: $FullPath"
  }
  $CurrentPath = $FullPath
  while ($CurrentPath.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    if (Test-Path -LiteralPath $CurrentPath) {
      $Entry = Get-Item -LiteralPath $CurrentPath -Force
      if (($Entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to clean a generated path through a link: $CurrentPath"
      }
    }
    $CurrentPath = Split-Path -Parent $CurrentPath
  }
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm not found. Install Node.js first.' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'python not found. Install Python 3.11+ first.' }
$PythonVersion = (& python --version).Trim()
if ($PythonVersion -notmatch '^Python 3\.12\.') {
  throw "The pinned Windows release lock is for CPython 3.12; found $PythonVersion."
}

$PackageVersion = Read-RequiredVersion (Join-Path $Root 'pyproject.toml') '^version\s*=\s*"([^\"]+)"' 'pyproject.toml'
$RuntimeVersion = Read-RequiredVersion (Join-Path $Root 'backend\config.py') '^APP_VERSION\s*=\s*"([^\"]+)"' 'backend/config.py'
$InstallerVersion = Read-RequiredVersion (Join-Path $PSScriptRoot 'Atlas.iss') '^#define\s+MyAppVersion\s+"([^\"]+)"' 'packaging/Atlas.iss'
if (($RuntimeVersion -ne $PackageVersion) -or ($InstallerVersion -ne $PackageVersion)) {
  throw "Version mismatch: pyproject=$PackageVersion, backend=$RuntimeVersion, installer=$InstallerVersion"
}

$ZipName = "Atlas-$PackageVersion-win64-portable.zip"
$ZipPath = Join-Path $Release $ZipName
$SidecarPath = "$ZipPath.sha256"
$Staging = Join-Path $Release 'Atlas'
$Dist = Join-Path $Root 'dist\Atlas'
New-Item -ItemType Directory -Path $Release -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root 'build') -Force | Out-Null

# Build the web assets from the committed npm lock, then create a fresh Python
# environment and install only the exact Windows release lock.
Push-Location $Frontend
try {
  npm ci --cache $NpmCache
  if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
} finally { Pop-Location }

python -m venv --clear $Venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create a clean packaging virtual environment.' }
& $Python -m pip install --disable-pip-version-check --cache-dir $PipCache pip==26.2.1
if ($LASTEXITCODE -ne 0) { throw 'Could not install the pinned pip version.' }
& $Python -m pip install --disable-pip-version-check --cache-dir $PipCache --requirement $Lock
if ($LASTEXITCODE -ne 0) { throw 'Could not install the locked release dependencies.' }

# Replace only known generated outputs; preserve unrelated files under release/.
Assert-GeneratedPathInsideRoot $Release
Assert-GeneratedPathInsideRoot $Staging
Assert-GeneratedPathInsideRoot $Dist
Assert-GeneratedPathInsideRoot $ZipPath
Assert-GeneratedPathInsideRoot $SidecarPath
foreach ($GeneratedPath in @($Staging, $Dist, $ZipPath, $SidecarPath, (Join-Path $Release 'Atlas.iss'))) {
  if (Test-Path -LiteralPath $GeneratedPath) { Remove-Item -LiteralPath $GeneratedPath -Recurse -Force }
}

Push-Location $Root
try {
  & $PyInstaller --noconfirm --onedir --name Atlas --noconsole --noupx `
    --specpath (Join-Path $Root 'build') `
    --icon (Join-Path $PSScriptRoot 'atlas.ico') `
    --hidden-import webview --hidden-import webview.platforms.edgechromium `
    --paths $Root --collect-submodules backend --collect-data uvicorn --collect-data PIL packaging/launcher.py
  if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally { Pop-Location }

New-Item -ItemType Directory -Path $Staging -Force | Out-Null
Copy-Item (Join-Path $Dist '*') $Staging -Recurse -Force
$Web = Join-Path $Staging 'web'
New-Item -ItemType Directory -Path $Web -Force | Out-Null
Copy-Item (Join-Path $Frontend 'dist\*') $Web -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot 'Atlas.iss') (Join-Path $Release 'Atlas.iss') -Force

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Staging, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText($SidecarPath, "$Hash  $ZipName`n", (New-Object System.Text.UTF8Encoding($false)))

if (-not (Test-Path -LiteralPath $ZipPath) -or -not (Test-Path -LiteralPath $SidecarPath)) {
  throw 'Portable ZIP or SHA256 sidecar was not created.'
}
$SidecarHash = ((Get-Content -Raw -LiteralPath $SidecarPath).Trim() -split '\s+')[0]
if ($Hash -ne $SidecarHash) { throw 'SHA256 sidecar does not match the generated ZIP.' }

& $SmokeScript -ArchivePath $ZipPath -ExpectedVersion $PackageVersion
if ($LASTEXITCODE -ne 0) { throw 'Packaged smoke failed.' }

$Zip = Get-Item -LiteralPath $ZipPath
Write-Host "Release version: $PackageVersion"
Write-Host "Portable ZIP: $($Zip.FullName) ($($Zip.Length) bytes)"
Write-Host "SHA256: $Hash"
Write-Host "Sidecar: $SidecarPath"
