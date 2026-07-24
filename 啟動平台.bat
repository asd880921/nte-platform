@echo off
REM 雙擊即可開啟 NTE 自動化平台 (pythonw = 不顯示黑色主控台視窗)
cd /d "%~dp0"
start "" pythonw "launcher\app.py"
