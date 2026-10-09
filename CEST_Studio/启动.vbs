Option Explicit
' CEST Studio 无窗口启动（pythonw + 隐藏窗口，不留黑框）
' 附带的参数会原样传给 app\main.py（便于自动化/截图测试）
'
' 自动找 Python（按顺序，第一个存在的就用）：
'   1) CEST_Studio\runtime\pythonw.exe
'   2) CEST_Studio\.venv\Scripts\pythonw.exe
'   3) <上一级>\.venv\Scripts\pythonw.exe     ← 「安装依赖.bat」建的那个
'   4) <上一级>\runtime\pythonw.exe
'   5) 已知可用的固定路径（本机开发 venv）
'   6) %LOCALAPPDATA%\Programs\Python\Python3{12,11,10,9}\pythonw.exe
'   7) C:\Python3{12,11,10}\pythonw.exe
' 都找不到 → 弹框把找过的位置全列出来。
' ※ 「已知可用」必须排在「系统 Python」前面：系统 Python 往往没装 PySide6 等依赖。

Dim sh, fso, here, up, py, i, extra, cand, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
up   = fso.GetParentFolderName(here)
sh.CurrentDirectory = here

cand = Array( _
  here & "\runtime\pythonw.exe", _
  here & "\.venv\Scripts\pythonw.exe", _
  here & "\venv\Scripts\pythonw.exe", _
  up   & "\.venv\Scripts\pythonw.exe", _
  up   & "\runtime\pythonw.exe", _
  "C:\Users\Administrator\Desktop\PycharmProjects\机器学习\venv\Scripts\pythonw.exe", _
  sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python312\pythonw.exe", _
  sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe", _
  sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python310\pythonw.exe", _
  sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python39\pythonw.exe", _
  "C:\Python312\pythonw.exe", _
  "C:\Python311\pythonw.exe", _
  "C:\Python310\pythonw.exe", _
  "C:\Python39\pythonw.exe" )

py = ""
For i = 0 To UBound(cand)
  If py = "" And fso.FileExists(cand(i)) Then py = cand(i)
Next

If py = "" Then
  MsgBox "找不到 Python（pythonw.exe）。" & vbCrLf & vbCrLf & _
         "请任选一种：" & vbCrLf & _
         "  1) 装 Python 3.10 / 3.11（安装时勾选 Add Python to PATH），" & vbCrLf & _
         "     然后双击同目录上一级的「安装依赖.bat」；" & vbCrLf & _
         "  2) 或者把 pythonw.exe 的完整路径填进本文件的 cand 列表第一项。" & vbCrLf & vbCrLf & _
         "已经找过这些位置：" & vbCrLf & Join(cand, vbCrLf), 16, "CEST Studio"
  WScript.Quit 1
End If

extra = ""
For i = 0 To WScript.Arguments.Count - 1
  extra = extra & " " & WScript.Arguments(i)
Next

cmd = """" & py & """ -X utf8 """ & here & "\app\main.py""" & extra
sh.Run cmd, 0, False      ' 0 = 隐藏窗口
