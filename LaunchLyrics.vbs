Option Explicit

Dim fso, shell, rootDir, pythonw, appFile
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

rootDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = rootDir & "\auto-lrc-engine\.venv\Scripts\pythonw.exe"
appFile = rootDir & "\AutoLrcAligner.pyw"

If Not fso.FileExists(pythonw) Then
    MsgBox "The Lyrics runtime is not installed yet. Use Start Menu > Install or repair CPU runtime, then launch Lyrics again.", vbExclamation, "Lyrics"
    WScript.Quit 1
End If

shell.Run """" & pythonw & """ """ & appFile & """", 1, False
