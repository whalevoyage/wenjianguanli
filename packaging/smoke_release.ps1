param(
  [Parameter(Mandatory = $true)][string]$ArchivePath,
  [Parameter(Mandatory = $true)][string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ArchivePath = [System.IO.Path]::GetFullPath($ArchivePath)
if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) { throw "Archive not found: $ArchivePath" }

$Id = [guid]::NewGuid().ToString('N')
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "atlas-packaged-smoke-$Id"
$TempPrefix = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
$TempRootFull = [System.IO.Path]::GetFullPath($TempRoot)
if (-not $TempRootFull.StartsWith($TempPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    [System.IO.Path]::GetFileName($TempRootFull) -notlike 'atlas-packaged-smoke-*') {
  throw 'Temporary smoke path failed validation.'
}
$Extracted = Join-Path $TempRoot 'extracted'
$DataDir = Join-Path $TempRoot 'data'
$ExePath = Join-Path $Extracted 'Atlas.exe'
$PreviousEnv = @{
  FILEPILOT_OPEN_BROWSER = $env:FILEPILOT_OPEN_BROWSER
  FILEPILOT_PORT = $env:FILEPILOT_PORT
  FILEPILOT_DATA_DIR = $env:FILEPILOT_DATA_DIR
}
$Process = $null

try {
  New-Item -ItemType Directory -Path $Extracted, $DataDir -Force | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($ArchivePath, $Extracted)
  if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) { throw 'The ZIP does not contain Atlas.exe at its root.' }

  $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $Listener.Start()
  $Port = ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
  $Listener.Stop()

  $env:FILEPILOT_OPEN_BROWSER = '0'
  $env:FILEPILOT_PORT = [string]$Port
  $env:FILEPILOT_DATA_DIR = $DataDir
  $Process = Start-Process -FilePath $ExePath -WorkingDirectory $Extracted -PassThru -WindowStyle Hidden

  $Health = $null
  $Deadline = [DateTime]::UtcNow.AddSeconds(60)
  while ([DateTime]::UtcNow -lt $Deadline) {
    if ($Process.HasExited) { throw "Packaged Atlas.exe exited early with code $($Process.ExitCode)." }
    try {
      $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
      if ($Health.status -eq 'ok') { break }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $Health -or $Health.status -ne 'ok') { throw 'Packaged /api/health did not become ready within 60 seconds.' }
  if ($Health.version -ne $ExpectedVersion) { throw "Expected version $ExpectedVersion but /api/health returned $($Health.version)." }

  $Page = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -TimeoutSec 10 -UseBasicParsing
  if ($Page.StatusCode -ne 200 -or $Page.Content -notmatch '<html') { throw 'Packaged root path did not return the UI HTML.' }
  Write-Host "Packaged smoke PASS: /api/health=$($Health.status), version=$($Health.version), root=HTTP $($Page.StatusCode)."
} finally {
  if ($Process -and -not $Process.HasExited) { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue }
  foreach ($Name in $PreviousEnv.Keys) {
    if ($null -eq $PreviousEnv[$Name]) { Remove-Item "Env:$Name" -ErrorAction SilentlyContinue }
    else { Set-Item "Env:$Name" $PreviousEnv[$Name] }
  }
  if (Test-Path -LiteralPath $TempRootFull) { Remove-Item -LiteralPath $TempRootFull -Recurse -Force }
}
