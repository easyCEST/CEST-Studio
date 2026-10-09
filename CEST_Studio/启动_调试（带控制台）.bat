@echo off
chcp 936 >nul
title CEST Studio（调试）
cd /d "%~dp0"

rem ---- 自动找 python.exe（顺序同 启动.vbs）----
set "PY="
if not defined PY if exist "runtime\python.exe"                  set "PY=%CD%\runtime\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe"            set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe"             set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY if exist "..\.venv\Scripts\python.exe"         set "PY=%CD%\..\.venv\Scripts\python.exe"
if not defined PY if exist "..\runtime\python.exe"               set "PY=%CD%\..\runtime\python.exe"
rem ※ 「已知可用」必须排在「系统 Python」前面：系统 Python 往往没装 PySide6 等依赖
if not defined PY if exist "C:\Users\Administrator\Desktop\PycharmProjects\机器学习\venv\Scripts\python.exe" set "PY=C:\Users\Administrator\Desktop\PycharmProjects\机器学习\venv\Scripts\python.exe"
if not defined PY for %%V in (312 311 310 39) do if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
if not defined PY for %%V in (312 311 310) do if not defined PY if exist "C:\Python%%V\python.exe" set "PY=C:\Python%%V\python.exe"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"

if not defined PY (
  echo [ERROR] 找不到 Python。
  echo   请装 Python 3.10 / 3.11（勾选 Add Python to PATH），
  echo   然后双击同目录上一级的「安装依赖.bat」，或改本文件里的 PY。
  pause
  exit /b 1
)

echo [CEST Studio] 解释器：%PY%
set PYTHONIOENCODING=utf-8
set MPLBACKEND=Agg
"%PY%" -X utf8 "app\main.py" %*
if errorlevel 1 pause
