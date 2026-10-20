# Atlas Windows packaging

## Release build

Run this from the repository root in Windows PowerShell with CPython 3.12, Node.js/npm and network access to the package registries:

```powershell
.\packaging\build_release.ps1
```

The script performs the release pipeline in order:

```text
npm ci
→ npm run build
→ recreate .venv-packaging
→ install packaging/requirements-release.lock
→ PyInstaller onedir build
→ copy frontend dist into release/Atlas/web
→ create release/Atlas-x.y.z-win64-portable.zip
→ create and verify the .sha256 sidecar
→ extract ZIP and run packaged smoke checks
```

The lock pins PyInstaller, Pillow, pywebview and their complete Python dependency set. It was captured from the Windows packaging environment used for the published 2.1.0 build. The script checks that `backend/config.py`, `pyproject.toml` and `Atlas.iss` use the same version before creating an artifact. The archive name comes from `pyproject.toml`.

Build outputs are written under ignored `dist/` and `release/` directories. The script replaces only its known generated outputs (`dist/Atlas`, `release/Atlas`, the versioned ZIP and sidecar, and `release/Atlas.iss`).

## Packaged smoke

`smoke_release.ps1` extracts the ZIP into a unique temporary directory, launches the actual `Atlas.exe` with an isolated data directory and `FILEPILOT_OPEN_BROWSER=0`, then verifies:

- `/api/health` returns `status=ok` and the expected version.
- `/` returns the packaged HTML UI.
- The process is stopped and the temporary directory is removed afterward.

The release build invokes this automatically. To run it against an existing archive:

```powershell
& .\packaging\smoke_release.ps1 `
  -ArchivePath .\release\Atlas-2.1.0-win64-portable.zip `
  -ExpectedVersion 2.1.0
```

## Optional installer

After a successful portable build, install Inno Setup 6 and run:

```powershell
& "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe" .\release\Atlas.iss
```

The installer is per-user: it installs to `%LOCALAPPDATA%\Atlas` and uses `PrivilegesRequired=lowest`, so it does not require administrator rights. The portable ZIP remains the primary release artifact.

## Development and CI dependencies

- `requirements.txt` contains broad minimum runtime requirements for a quick source install; it is not a reproducible lock.
- Contributors and CI use `requirements.lock` plus `npm ci` to install the checked-in Python and npm dependency locks.
- Portable releases use `packaging/requirements-release.lock`, a separate exact Windows packaging lock.
