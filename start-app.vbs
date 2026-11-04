Option Explicit

Dim shell, fso, root, python, pythonw, probe, marker, requirements, entry, setup, args, result, setupCmd, launchCmd

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
python = fso.BuildPath(root, ".venv\Scripts\python.exe")
pythonw = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
probe = fso.BuildPath(root, "runtime_probe.py")
marker = fso.BuildPath(root, ".venv\.trpl-requirements.sha256")
requirements = fso.BuildPath(root, "requirements.txt")
entry = fso.BuildPath(root, "desktop.py")
setup = fso.BuildPath(root, "start-app.cmd")
shell.CurrentDirectory = root

If Not fso.FileExists(probe) Then
    MsgBox "runtime_probe.py is missing. Re-extract the complete TRPL folder.", 16, "TRPL"
    WScript.Quit 2
End If
If Not fso.FileExists(entry) Then
    MsgBox "desktop.py is missing. Re-extract the complete TRPL folder.", 16, "TRPL"
    WScript.Quit 2
End If
If Not fso.FileExists(setup) Then
    MsgBox "start-app.cmd is missing. Re-extract the complete TRPL folder.", 16, "TRPL"
    WScript.Quit 2
End If

result = 1
If fso.FileExists(python) Then
    args = Quote(probe) & " --probe --requirements " & Quote(requirements) & " --marker " & Quote(marker)
    result = shell.Run(Quote(python) & " " & args, 0, True)
End If

If result <> 0 Then
    setupCmd = Quote(setup)
    shell.Run setupCmd, 1, False
Else
    If Not fso.FileExists(pythonw) Then
        MsgBox "The local environment is incomplete. Run start-app.cmd to repair it.", 16, "TRPL"
        WScript.Quit 2
    End If
    launchCmd = Quote(pythonw) & " " & Quote(entry)
    shell.Run launchCmd, 0, False
End If

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
