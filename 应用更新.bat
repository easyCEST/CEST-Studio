@echo off
chcp 936 >nul
title CEST Studio 更新（应用代码更新包）
cd /d "%~dp0"

set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo.
  echo [错误] 找不到随包运行环境：
  echo         %PY%
  echo         请确认整个 cest-gui 文件夹是完整拷过来的。
  echo.
  pause
  exit /b 1
)

"%PY%" "%~dp0CEST_Studio\tools\apply_update.py" %*
echo.
pause