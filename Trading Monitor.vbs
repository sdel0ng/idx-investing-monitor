' Trading Monitor launcher - double-click to open the dashboard (no console window)
' Starts the local web app (http://127.0.0.1:8765) and opens it in your browser.
' If it is already running, it just opens the page.
Option Explicit
Dim fso, shell, monitorDir, rootDir, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
monitorDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootDir = fso.GetParentFolderName(monitorDir)
pythonw = rootDir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "Python environment not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Run setup_new_pc.ps1 first.", vbExclamation, "Trading Monitor"
    WScript.Quit 1
End If
shell.CurrentDirectory = monitorDir
shell.Run """" & pythonw & """ """ & monitorDir & "\dashboard_app.py""", 0, False
