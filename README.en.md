# Atlas

<p align="center">
  <a href="README.md">简体中文</a> ·
  <b>English</b> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.de.md">Deutsch</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.es.md">Español</a>
</p>

**Your C: drive is full. Look before you act.**

Disk cleanup and file management for Windows: scan analysis, duplicate detection, junk cleanup, cross-drive migration, space map.

Every step shows you the file list first and waits for your confirmation. Deleted files go to quarantine first, so you can undo later.
The AI only understands what you say and drafts a plan — the code that actually touches your files is local.
Apart from Agent Q&A, everything runs locally, works offline, and needs no account.

> It doesn't make decisions for you. It lays out exactly which files an action is about to touch.

![Atlas home](docs/images/overview.png)

## Quick start

### For users: unzip and run (recommended)

1. Download [Atlas-2.1.0-win64-portable.zip](https://github.com/whalevoyage/wenjianguanli/releases/latest) and extract it anywhere (e.g. `D:\Atlas`)
2. Double-click `Atlas\Atlas.exe`
3. The app **opens your browser** automatically. No console window appears.

Python, Node and other runtimes are not required. The app only listens on `127.0.0.1`, stores data in
`%LOCALAPPDATA%\Atlas`, and uninstalling means deleting that folder.

**Double-clicking again will not start a second instance** — it points the browser at the running one.
**If port 8000 is taken, it picks a free port** and writes the actual address to the log.

If something goes wrong: **startup failure / blank page** → `%LOCALAPPDATA%\Atlas\atlas.log`; **a job returned wrong results** → the "Logs" page in the app.

**Verify the hash after downloading** (to rule out a corrupted transfer):

```powershell
Get-FileHash .\Atlas-2.1.0-win64-portable.zip -Algorithm SHA256
```

The full SHA256 for 2.1.0 is in the `.sha256` file attached to the release. It changes with each release — always defer to the release page.

### For users: installer

`packaging/Atlas.iss` is an Inno Setup script. It installs per-user into `%LOCALAPPDATA%\Atlas`
and requires **no administrator rights**. Install Inno Setup 6 and compile it yourself:

```powershell
.\packaging\build_release.ps1
& "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe" .\release\Atlas.iss
```

### From source (developers)

```powershell
# backend (listens on 8000 by default)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn backend.main:app --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev          # 5173; calls http://127.0.0.1:8000/api directly (no vite proxy configured)
```

If the backend is not on 8000, override with `VITE_API_BASE_URL=http://127.0.0.1:<port>/api`.

Or use the packaged launcher (opens the browser and picks a free port automatically):

```powershell
python packaging\launcher.py
```

Full packaging notes are in `packaging/README.md`.

### Agent model access (optional)

Everything except the Agent — scanning, duplicate detection, cleanup, migration, backup, logging —
**runs entirely locally with zero network calls and zero tokens**.

Natural-language understanding needs a model API key, configured from **inside the Agent page**.
The key is stored on this machine encrypted with Windows DPAPI (never plaintext); the API only ever
returns a masked value. Without a key the Agent still works, falling back to local rules.

## Why Atlas exists

Files pile up, but traditional file managers are still mostly about "open, copy, delete". The questions
that actually hurt are different:

- What is really filling up this disk?
- Which files are safe to remove, and which must not be touched?
- What should happen to duplicates, temp files and long-dead projects?
- When moving files across drives, how do I know nothing got corrupted — and can I recover if it did?
- When using automation or AI, how do I stop it from modifying important files directly?

Atlas isn't about adding more "delete" buttons. It connects disk analysis, organize previews, explicit
confirmation, post-execution verification and rollback into one flow: understand the result first, then
decide whether to act; keep a record of what happened, and offer a way back when needed.

## What Atlas is for

### High-risk operations become a controlled process

Cleanup and migration are inherently risky. Atlas combines previews, permission tiers, quarantine,
per-file verification and rollback, so you can see the impact before running something — and keep a
recovery path afterwards.

![Junk cleanup preview](docs/images/junk-scan.png)

### Every step can be undone

Cleanup and organization leave a record in the operation journal: when it ran, what triggered it, how
many items succeeded or failed, and whether it can still be undone. Click "Undo" and the batch is
replayed in reverse — deleted files come back from quarantine, moved files return to where they were.

When something *can't* be undone, the app **says why** instead of silently greying out a button
(for example, Agent cleanup is a permanent delete once confirmed — not "hidden for a while").

![Recent operations in the Job Center](docs/images/journal-undo.png)

### "The disk is full" becomes a concrete answer

No need to crawl through directories by hand or learn a complex CLI. The space map, type breakdown,
project radar and duplicate analysis turn "the disk is full" into specific folders, file types and
actionable items.

### AI participates in decisions without taking over your files

The Agent understands natural language and produces a plan, but has no final control over the
filesystem. Path validation, permission checks, user confirmation and actual execution all happen in
the local service — keeping the convenience of natural language without handing important file
operations to a model.

### Main work happens locally

Most features need no cloud service, no account, and never upload scan results. You can scan, analyse,
clean up, migrate and back up while offline.

### Room to grow into local automation

Atlas is more than a UI: it includes a job system, a permission model, a filesystem safety layer, an
audit log and an undoable operation journal. These primitives can extend to more local automation —
while keeping the rule of "preview first, confirm, keep a trace".

## Who it's for

### Everyday PC users

For people who clean up disks regularly, hunt for duplicates, tidy the Downloads folder and reclaim
space — without wanting a black-box "one-click clean" tool.

### Developers and content creators

For developers, designers, video editors and photographers who deal with repositories, assets, build
artifacts, virtualenvs and large project folders.

### Anyone moving data across drives

For moving documents, projects or media to another disk, with free-space prechecks, conflict handling,
verification and rollback.

### People who value privacy and local control

For those who don't want to create an account, upload directory listings, or depend on a remote cloud
service just to tidy their own computer.

### People who want natural language, with a safety net

For phrasing tasks as "find the big files on D:" or "back up this project", while still confirming
before anything is written.

### What it is not

Atlas targets single-machine Windows today. It is not an enterprise management platform, a cloud drive,
a multi-user collaboration system, or professional data-recovery software. Server clusters, enterprise
policy rollout, forensic recovery and large-scale asset management need dedicated tools.

## What's different

### 1. One controlled loop instead of a pile of buttons

Scan, cleanup, migration and rollback are not disconnected features. They form one flow:
**detect → preview → confirm → execute → verify → record → roll back**. You always know what the
system is about to do, what it has done, and whether it can be recovered.

### 2. Safety designed into the flow, not into a warning dialog

Safety isn't a "please be careful" popup. It lives in path protection, permission tiers, target
free-space prechecks, name-conflict handling, per-file verification and quarantine. Even in the
highest permission tier, the project's own directory and the quarantine stay protected.

### 3. Preview-first organization

Organize previews, junk severity levels, duplicate groups and migration plans all produce a readable
list before anything runs. Understand first, act second — avoiding the "one-click scan, one-click
delete" black box.

### 4. Local-first, cloud optional

Scanning, analysis, duplicate detection, migration, backup, logging and job management all run on this
machine by default — no account, no remote database. The Agent prefers local rules: common file intents
cost zero tokens, and a model is only called when nothing matches.

### 5. Natural language separated from deterministic file operations

The Agent only interprets intent and drafts a plan; the actual operations are executed by local
services. The model cannot delete, move or overwrite files directly — every write goes through the
local permission gate and user confirmation, reducing the blast radius of model mistakes.

### 6. Every long-running operation is observable

Scanning and migration never freeze the UI. They run as jobs with stage, progress, current path, errors,
cancellation and rollback. The Job Center keeps history so you can trace what actually happened.

### 7. Built for real Windows machines, not demo data

System directories, desktop protection, cross-drive moves, locked files, name conflicts, low disk space,
long-running jobs and user permissions are all handled — aiming to be something you can leave running
on a personal machine.

## Features

| Page | What it does |
| --- | --- |
| Home | Disk capacity, reclaimable space, job stats and recent jobs |
| Atlas Agent | Natural-language interaction; file tasks first, general questions too |
| Space Map | Treemap of directory usage with drill-down |
| Organize Preview | Preview organization by type, time, size or project — fully read-only |
| Project Radar | Detect project folders, their type, size, file count and stale projects |
| Scan Analysis | Counts, sizes, type distribution, largest and long-untouched files; save snapshots for before/after comparison |
| Duplicate Files | Byte-level duplicate detection; includes *Similar Images* (resized/recompressed/re-saved copies, optional rotation tolerance) |
| Junk Files | Temp files, logs, caches, empty folders and zero-byte files, with three severity levels |
| Full-text Search | Local FTS5 index per directory, search filenames and contents, CJK aware |
| Cross-drive Migration | Copy and verify item by item; source is only handled after verification passes; supports rollback |
| Project Backup | ZIP snapshots excluding common dependency and build folders; restore verification supported |
| Job Center | Live progress, errors, cancellation and rollback status |
| Trash / Quarantine | Cleanup goes to quarantine first and can be restored in place |
| Operation Log | Audit records, exportable |

### Multiple sources

Scan Analysis, Duplicate Files, Junk Files, Similar Images, Project Radar and Full-text Search can all
take **several directories in one job**, merging results into a single list. Multi-source also unlocks
**cross-directory duplicate detection**: identical files between directory A and B are grouped and
sorted to the top.

(Space Map and Organize Preview deliberately **do not** support multiple sources — their output is a
single tree, and merging sources would break drill-down and grouping semantics.)

### Three entry points

The same executable (or the same source tree) exposes three ways in:

| Form | Usage | Write access |
| --- | --- | --- |
| GUI | Double-click `Atlas.exe` (default) | Full, subject to permission tier and gates |
| CLI | `python -m backend.cli scan\|duplicates\|similar\|junk\|tree\|overview\|reclaimed` | **Read-only** — no delete/move commands exist on purpose |
| MCP server | `Atlas.exe --mcp --allow <dir>`, for Claude Code / Cursor etc. | **Read-only + allowlist** — sees nothing by default |

MCP boundary details: [`docs/MCP_SECURITY.md`](docs/MCP_SECURITY.md) (Chinese).

## Safety model

- Cleanup produces a list first; files go to quarantine only after confirmation — never a direct physical delete.
- Migration copies first and verifies; the source is only touched after verification passes.
- Long operations run as background jobs with progress, cancellation and rollback.
- The project's own directory, the state directory and the quarantine are protected.
- System directories (Windows, Program Files, ProgramData) are never written to.
- `node_modules`, `.git`, `__pycache__`, `.venv`, `dist`, `build` and similar are excluded by default.
- Free space at the destination is checked before migration and backup.
- Listens on `127.0.0.1` only — not exposed to the LAN.

Before bulk cleanup or migration, read the preview list and target path. For important data, keep a
separate backup anyway.

## Installation

### Windows portable

Copy the whole Atlas folder to the target machine and run `Atlas.exe`. You must keep the `web` folder —
copying only the exe will not work. Then open `http://127.0.0.1:8000`.

No Python, Node.js or npm needed on the target machine. User data lives in `%LOCALAPPDATA%\Atlas`.

### From source

Requirements: Windows, Python 3.11+ (3.12 recommended), Node.js 18+.

```powershell
python -m pip install -r requirements.txt
cd frontend
npm ci
cd ..
.\start_all.bat
```

Or start them separately:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
cd frontend
npm run dev
```

- Frontend: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000/api`
- Swagger: `http://127.0.0.1:8000/docs`

## The Agent

The Agent is a general assistant that prioritizes file management. Scan, duplicate, cleanup, migration
and backup requests become local Atlas job plans; identity questions, model questions and other general
topics are answered normally. Common file intents are handled by local rules at zero token cost; only
unrecognized requests with a configured API key reach a model. Any file write, cleanup or migration
still requires user confirmation and is executed by the local API.

Configuration example:

```powershell
$env:DEEPSEEK_API_KEY = "your-api-key"
$env:DEEPSEEK_MODEL = "deepseek-flash"
```

Built-in model IDs include `deepseek-flash`, `deepseek-v4-pro`, `deepseek-chat` and `deepseek-reasoner`.
Always defer to your provider's current supported list. Never commit real API keys to code, screenshots
or Git history.

## Architecture

```text
Vue 3 + Element Plus
        │ HTTP JSON / SSE
        ▼
FastAPI routes (backend/main.py)
        ▼
Job manager (backend/jobs.py)
        ▼
Services (backend/services/)
        ▼
Filesystem safety layer (backend/fsutil.py)
        ▼
Local state (dev: .filepilot, release: %LOCALAPPDATA%\Atlas)
```

Long operations return a `job_id`; the frontend subscribes to progress via
`/api/jobs/{id}/stream`. State, logs, quarantine manifests and Agent recipes live locally — no remote
database.

## Repository layout

```text
FilePilotLite/
├─ backend/       FastAPI backend, job system, services
├─ frontend/      Vue 3 frontend
├─ packaging/     Windows packaging scripts and installer config
├─ tests/         unit, API, UI and end-to-end tests
├─ docs/          architecture notes, gotchas, limits
├─ requirements.txt
└─ start_all.bat  one-click dev startup
```

## Testing

```powershell
python -m pytest -q
```

End-to-end smoke test, with the backend running:

```powershell
$env:FILEPILOT_API = "http://127.0.0.1:8000/api"
python tests/smoke_test.py
```

Frontend production build:

```powershell
npm run build --prefix frontend
```

UI tests can use Chromium or Edge:

```powershell
$env:PLAYWRIGHT_CHROMIUM = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$env:FRONTEND_URL = "http://127.0.0.1:5173"
python tests/ep_style_audit.py
python tests/permission_ui_test.py
python tests/messagebox_style_test.py
python tests/agent_composer_audit.py
```

## Windows packaging

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_release.ps1
```

Output goes to `release\Atlas`. With Inno Setup installed you can compile `release\Atlas.iss` into an
installer wizard.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | Optional model fallback for the Agent | unset |
| `DEEPSEEK_MODEL` | Model ID | `deepseek-flash` |
| `FILEPILOT_DATA_DIR` | Override the user data directory | `%LOCALAPPDATA%\Atlas` in releases |
| `FILEPILOT_TRASH` | Fallback location for quarantine | `FilePilotTrash` under your user directory |
| `FILEPILOT_SCAN_LIMIT` | Max entries per scan | `400000` |

In development the state directory defaults to `.filepilot` in the project root; in releases it is
`%LOCALAPPDATA%\Atlas`.

## Data and privacy

Apart from two **user-initiated** network touchpoints (the Agent page and the "?" explanation), file
contents, scan results and job state never leave your machine.

One detail worth calling out: **when you ask the Agent to search file contents**, matching snippets do
enter the model context — that is inherent to the action itself. Scan, duplicate detection, cleanup and
migration only read names, sizes and hashes, never contents.

Full details (where data lives, what is sent, how to delete it) are in [`PRIVACY.md`](PRIVACY.md)
(Chinese).

## License

[MIT License](LICENSE). Use, modify and redistribute freely; just keep the copyright and license notice.

> Security boundaries and vulnerability reporting: [`SECURITY.md`](SECURITY.md) and [`docs/MCP_SECURITY.md`](docs/MCP_SECURITY.md).
> Known limitations: [`docs/LIMITS.md`](docs/LIMITS.md).
> Contributing (and the rules this codebase enforces): [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

*Project documentation is currently written in Chinese. This English README mirrors the Chinese one
section by section; if they ever disagree, the Chinese version is authoritative.*
