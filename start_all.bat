@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo [ERROR] Python not found.& exit /b 1)
where npm >nul 2>nul || (echo [ERROR] Node/npm not found.& exit /b 1)
if not exist frontend\node_modules (echo [INFO] Installing frontend dependencies...& pushd frontend& call npm ci& if errorlevel 1 exit /b 1& popd)
start "FilePilot backend" cmd /k "cd /d "%~dp0" && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
timeout /t 2 /nobreak >nul
start "FilePilot frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"
endlocal
