@echo off
rem 双击这个也不留黑窗口：立刻把工作交给 启动.vbs 后自己退出
start "" wscript.exe "%~dp0启动.vbs" %*
exit /b 0
