@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
title CEST Studio 更新工具（一键）

rem ============================================================
rem  本文件夹拷进要更新的那份 cest-gui 里（放哪一层都行），双击本文件即可。
rem    · 安装目录 = 自己往上找：有 CEST_Studio\app\main.py + runtime\python.exe 的那层
rem    · 更新包   = 自己找本文件夹 更新包\ 里最新的 CEST-Studio-更新-v*（文件夹或 zip）
rem  双击就是一条路：自己找目录、自己用 更新包\ 里最新的包，中途不用你按任何键。
rem  只有把某个包拖到本文件上时，才改用你拖的那个；没有自带包才弹文件框。
rem ============================================================

set "HERE=%~dp0"
set "ROOT="
set "D=%HERE%"
for /l %%i in (1,1,6) do (
  if not defined ROOT (
    if exist "!D!CEST_Studio\app\main.py" (
      if exist "!D!runtime\python.exe" (
        pushd "!D!"
        set "ROOT=!CD!"
        popd
      )
    )
  )
  if defined ROOT (goto :found)
  set "D=!D!..\"
)

echo.
echo [错误] 没找到 CEST Studio 安装目录。
echo.
echo   本工具要放在「要更新的那份 cest-gui 文件夹」里面才有用
echo   （就是有「启动 CEST Studio.vbs」和 runtime 文件夹的地方，
echo     和它同一层、或往里面任意一层都可以）。
echo.
echo   请把整个「更新工具」文件夹拷进 cest-gui 里，再双击本文件。
echo.
pause
exit /b 1

:found
echo 安装目录：%ROOT%

set "ZIPARG="
rem  两条路：%~1 真的是个存在的东西（你拖了个包上来）才走你拖的那个；
rem 带 --dry-run/--yes 这类开关时，仍然用自带包（不然带开关就退化成弹窗）。
if exist "%~1" goto :run
set "ZIP="
for /f "delims=" %%f in ('dir /b /ad /o-d "%HERE%更新包\CEST-Studio-更新-v*" 2^>nul') do (
  if not defined ZIP set "ZIP=%HERE%更新包\%%f"
)
if not defined ZIP for /f "delims=" %%f in ('dir /b /a-d /o-d "%HERE%更新包\CEST-Studio-更新-v*.zip" 2^>nul') do (
  if not defined ZIP set "ZIP=%HERE%更新包\%%f"
)
if defined ZIP (
  echo 自带更新包：!ZIP!
  set "ZIPARG="!ZIP!""
) else (
  echo （本文件夹里没找到自带更新包，改为让你手动选）
)

:run
echo.
"%ROOT%\runtime\python.exe" "%HERE%apply_update.py" --root "%ROOT%" --yes %ZIPARG% %*
echo.
pause
