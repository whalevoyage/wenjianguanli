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

The release lock pins the full Windows CPython 3.12 runtime and packaging dependency graph, including PyInstaller, Pillow and pywebview. Regenerate it from `packaging/requirements-release.in` using the command documented below. The script checks that `backend/config.py`, `pyproject.toml` and `Atlas.iss` use the same version before creating an artifact. The archive name comes from `pyproject.toml`.

Build outputs are written under ignored `dist/` and `release/` directories. The script replaces only its known generated outputs (`dist/Atlas`, `release/Atlas`, the versioned ZIP and sidecar, and `release/Atlas.iss`).

## Packaged smoke

`smoke_release.ps1` extracts the ZIP into a unique temporary directory, launches the actual `Atlas.exe` with an isolated data directory and `FILEPILOT_OPEN_BROWSER=0`, then verifies:

- `/api/health` returns `status=ok` and the expected version.
- `/` returns the packaged HTML UI.
- The process is stopped and the temporary directory is removed afterward.

The release build invokes this automatically. To run it against an existing archive:

```powershell
& .\packaging\smoke_release.ps1 `
  -ArchivePath .\release\Atlas-2.1.1-win64-portable.zip `
  -ExpectedVersion 2.1.1
```

## Optional installer

After a successful portable build, install Inno Setup 6 and run:

```powershell
& "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe" .\release\Atlas.iss
```

The installer is per-user: it installs to `%LOCALAPPDATA%\Atlas` and uses `PrivilegesRequired=lowest`, so it does not require administrator rights. The portable ZIP remains the primary release artifact.

## Development and CI dependencies

- `requirements.txt` contains broad minimum runtime requirements for a quick source install; it is not a reproducible lock.
- `requirements.lock` pins the complete transitive contributor/CI dependency graph; regenerate it from `requirements.in` with `pip-compile --resolver=backtracking --allow-unsafe -o requirements.lock requirements.in`. To refresh a specific package, add `--upgrade-package <name>`; CI and the scheduled audit catch newly published advisories.
- Contributors and CI install `requirements.lock` plus the checked-in npm lock through `npm ci`; CI verifies the Python lock on Python 3.11 and 3.12.
- Portable releases use `packaging/requirements-release.lock`, generated from `packaging/requirements-release.in` on Windows CPython 3.12 with `pip-compile --resolver=backtracking --allow-unsafe -o packaging/requirements-release.lock packaging/requirements-release.in`. Use `--upgrade-package <name>` when refreshing a package.
- CI installs the project in editable mode and runs `atlas --version`, `atlas-cli --help` and `atlas-mcp --help`; the GUI entrypoint exits without starting a service for help/version requests.

The importable launcher lives in `atlas_app/launcher.py`. `packaging/launcher.py` remains a compatibility wrapper for `python packaging\\launcher.py`, while the `packaging/` directory continues to hold release assets and scripts.
