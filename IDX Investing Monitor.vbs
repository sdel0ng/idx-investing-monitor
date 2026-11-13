' IDX Investing Monitor - double-click to open the app (no console window).
' Uses the Python environment created by setup_new_pc.ps1 (.venv next to this folder, or inside it).
Option Explicit
Dim fso, shell, appDir, pythonw, candidates, candidate
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
candidates = Array(fso.GetParentFolderName(appDir) & "\.venv\Scripts\pythonw.exe", appDir & "\.venv\Scripts\pythonw.exe")
pythonw = ""
For Each candidate In candidates
    If fso.FileExists(candidate) Then
        pythonw = candidate
        Exit For
    End If
Next
If pythonw = "" Then
    MsgBox "Python environment not found." & vbCrLf & vbCrLf & _
           "Right-click setup_new_pc.ps1 -> Run with PowerShell (one time), then open this again.", _
           vbExclamation, "IDX Investing Monitor"
    WScript.Quit 1
End If
shell.CurrentDirectory = appDir
shell.Run """" & pythonw & """ """ & appDir & "\desktop_app.py""", 0, False
