@echo off
cd /d "%~dp0"
start "FilePilot backend" cmd /k "python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
start "FilePilot frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"
