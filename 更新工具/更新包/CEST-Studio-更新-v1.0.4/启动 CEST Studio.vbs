Option Explicit
' CEST Studio（免安装版）—— 用随包的 runtime 启动，不留黑窗口
' 附带的参数会原样传给 CEST_Studio\app\main.py
Dim sh, fso, here, py, cmd, extra, i
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here

py = here & "\runtime\pythonw.exe"
If Not fso.FileExists(py) Then
  MsgBox "缺少随包运行环境：" & vbCrLf & py & vbCrLf & vbCrLf & _
         "请确认整个 cest-gui 文件夹是一起拷过来的（runtime 子目录不能少）。", 16, "CEST Studio"
  WScript.Quit 1
End If

extra = ""
For i = 0 To WScript.Arguments.Count - 1
  extra = extra & " " & WScript.Arguments(i)
Next

cmd = """" & py & """ -X utf8 """ & here & "\CEST_Studio\app\main.py""" & extra
sh.Run cmd, 1, False
