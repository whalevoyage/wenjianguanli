# Contributing

This is a personal project, but issues and pull requests are welcome.

## Report an issue

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Replace real file paths with placeholders before posting; Atlas works with files on your computer.

Do not report security issues publicly. See [SECURITY.md](SECURITY.md).

## Before opening a pull request

Small fixes such as typos, clear bugs and compatibility issues can go straight into a PR. For larger changes, open an issue first to discuss the design and existing interaction constraints.

## Local development

From the repository root, contributors should use the checked-in locks:

```powershell
python -m venv .venv
$env:PYTHONUTF8 = '1'
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m ruff check backend/ tests/ packaging/ main.py
.venv\Scripts\python.exe -m pytest

cd frontend
npm ci
npm run build
npm run dev
```

`requirements.txt` is a quick install path with broad compatible runtime ranges, not a reproducible lock. CI uses `requirements.lock` and `npm ci`. Windows release builds use `packaging/requirements-release.lock`.

### End-to-end smoke test on Windows

The smoke test creates sample files under the repository root, so run it against a separate backend started from this checkout, not the packaged app. Use two PowerShell terminals.

Terminal A:

```powershell
$env:FILEPILOT_DATA_DIR = Join-Path $env:TEMP 'atlas-test-data'
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8236
```

Terminal B:

```powershell
$env:FILEPILOT_API = 'http://127.0.0.1:8236/api'
python tests/smoke_test.py
Remove-Item Env:FILEPILOT_API
```

Stop the test backend with Ctrl+C when finished. The packaged ZIP has its own smoke procedure in [packaging/README.md](packaging/README.md).

## Security invariants

1. **All writes to user files must go through `backend/execution.py`.** Do not call `shutil.move`, `.unlink()` or `rmtree()` directly for user data. Execution owns permission checks, protected paths, link escape checks and directory boundaries.
2. **The model cannot operate on files directly.** It only proposes a plan; tool names are validated against `backend/services/tool_specs.py`, and local code inserts confirmation before write actions.
3. **Describe destructive behavior accurately.** Quarantine items may be restored; permanent purge and Agent cleanup after confirmation cannot.
4. Run Ruff and the full pytest suite before submitting. CI additionally runs a dedicated safety suite and frontend production build.

## Style

- Python 3.11+, 120-character line length, Ruff checked.
- Explain why in comments rather than restating what the code does.
- Reuse frontend design tokens from `frontend/src/styles/global.css`.

## Do not commit

Do not commit `_local_shots/`, `_local_docs/`, `.filepilot/`, `release/`, `dist/`, `node_modules/`, API keys or other credentials.
