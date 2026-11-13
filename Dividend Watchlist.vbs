' Dividend Watchlist launcher - double-click to open (no console window)
' Starts the local app (http://127.0.0.1:8766) and opens it in your browser.
Option Explicit
Dim fso, shell, monitorDir, rootDir, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
monitorDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootDir = fso.GetParentFolderName(monitorDir)
pythonw = rootDir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "Python environment not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Run setup_new_pc.ps1 first.", vbExclamation, "Dividend Watchlist"
    WScript.Quit 1
End If
shell.CurrentDirectory = monitorDir
shell.Run """" & pythonw & """ """ & monitorDir & "\dividend_app.py""", 0, False
