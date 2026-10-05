@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo [ERROR] Python not found.& exit /b 1)
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
endlocal
