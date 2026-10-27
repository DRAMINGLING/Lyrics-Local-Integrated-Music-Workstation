Option Explicit

Dim fso, shell, rootDir, pythonw, appFile
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

rootDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = rootDir & "\auto-lrc-engine\.venv\Scripts\pythonw.exe"
appFile = rootDir & "\AutoLrcAligner.pyw"

If Not fso.FileExists(pythonw) Then
    MsgBox "请先阅读安装目录中的 README.md，再从开始菜单手动安装 CPU 运行环境。本安装程序不会安装 Python 运行环境或依赖。", vbExclamation, "Lyrics"
    WScript.Quit 1
End If

shell.Run """" & pythonw & """ """ & appFile & """", 1, False
