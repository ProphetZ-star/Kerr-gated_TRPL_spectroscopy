Option Explicit
Dim shell, fso, root, pythonw, entry, result
Set shell=CreateObject("WScript.Shell")
Set fso=CreateObject("Scripting.FileSystemObject")
root=fso.GetParentFolderName(WScript.ScriptFullName)
pythonw=fso.BuildPath(root,".venv\Scripts\pythonw.exe")
If Not fso.FileExists(pythonw) Then
    pythonw=fso.BuildPath(root,".venv\Scripts\python.exe")
End If
entry=fso.BuildPath(root,"desktop.py")
shell.CurrentDirectory=root
If Not fso.FileExists(pythonw) Then
    MsgBox "Run start-app.cmd once to prepare the Python environment.",48,"Kerr Studio setup"
Else
    result=shell.Run(Chr(34) & pythonw & Chr(34) & " " & Chr(34) & entry & Chr(34),0,False)
End If
