Option Explicit
Dim shell, fso, root, pythonw, browserScript, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fso.FileExists(pythonw) Then
    pythonw = fso.BuildPath(root, ".venv\Scripts\python.exe")
End If
browserScript = fso.BuildPath(root, "browser.py")
If Not fso.FileExists(pythonw) Then
    MsgBox "Kerr Studio is not ready yet. Please run start-sim.cmd first.", vbExclamation, "Kerr Studio"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & browserScript & Chr(34)
shell.Run command, 0, False
