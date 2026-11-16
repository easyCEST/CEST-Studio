@echo off
chcp 936 >nul
title CEST Studio（调试）
cd /d "%~dp0"

if not exist "runtime\python.exe" (
  echo [ERROR] 缺少随包运行环境：runtime\python.exe
  echo   请确认整个 cest-gui 文件夹是一起拷过来的。
  pause
  exit /b 1
)

set PYTHONIOENCODING=utf-8
set MPLBACKEND=Agg
"runtime\python.exe" -X utf8 "CEST_Studio\app\main.py" %*
if errorlevel 1 pause
