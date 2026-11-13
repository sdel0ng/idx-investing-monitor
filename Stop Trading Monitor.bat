@echo off
rem Stops the Trading Monitor dashboard (frees port 8765)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "127.0.0.1:8765" ^| findstr LISTENING') do (
    taskkill /PID %%p /F >nul 2>&1 && echo Dashboard stopped (PID %%p).
)
timeout /t 2 >nul
