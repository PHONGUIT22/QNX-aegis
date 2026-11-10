@echo off
title QNX-Aegis - FastMCP Tools Test Suite
cls
echo ============================================================
echo   QNX-Aegis: Running 6 Model Context Protocol (MCP) Tools
echo ============================================================
echo.
cd /d "%~dp0\mcp-server"
.\venv\Scripts\python.exe test_mcp_tools.py
echo.
echo ============================================================
pause
