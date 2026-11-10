@echo off
title QNX-Aegis - Start Dashboard
cls
echo ============================================================
echo   QNX-Aegis: Real-Time Microkernel Dashboard Launcher
echo ============================================================
echo.

netstat -ano | findstr :8000 | findstr LISTENING >NUL
if "%ERRORLEVEL%"=="0" (
    echo [INFO] Backend Dashboard dang chay tren cong 8000.
) else (
    echo [INFO] Dang khoi dong Backend FastAPI tren cong 8000...
    start /B "" "mcp-server\venv\Scripts\python.exe" -m uvicorn src.api_bridge:app --app-dir "mcp-server" --host 0.0.0.0 --port 8000
    timeout /t 3 /nobreak >NUL
)

echo [INFO] Dang mo Dashboard tren trinh duyet...
start http://localhost:8000/
echo.
echo Dashboard da san sang tai http://localhost:8000/
echo Nhan phim bat ky de thoat cua so nay.
pause >NUL
