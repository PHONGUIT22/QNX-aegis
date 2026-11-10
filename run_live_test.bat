@echo off
title QNX-Aegis - Target Verification Test
cls
echo ============================================================
echo   QNX-Aegis: Running QNX 8.0 Live Target Verification
echo ============================================================
echo.
cd /d "%~dp0\mcp-server"
.\venv\Scripts\python.exe test_live.py
echo.
echo ============================================================
pause
