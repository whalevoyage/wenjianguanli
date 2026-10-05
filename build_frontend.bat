@echo off
setlocal
cd /d "%~dp0frontend"
where npm >nul 2>nul || (echo [ERROR] Node/npm not found.& exit /b 1)
if not exist node_modules call npm ci
if errorlevel 1 exit /b 1
call npm run build
endlocal
