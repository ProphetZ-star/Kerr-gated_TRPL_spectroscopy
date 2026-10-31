Option Explicit
Dim shell, fso, root, python, pythonw, entry, setup, probe, result
Set shell=CreateObject("WScript.Shell")
Set fso=CreateObject("Scripting.FileSystemObject")
root=fso.GetParentFolderName(WScript.ScriptFullName)
pythonw=fso.BuildPath(root,".venv\Scripts\pythonw.exe")
python=fso.BuildPath(root,".venv\Scripts\python.exe")
If Not fso.FileExists(pythonw) Then
    pythonw=python
End If
entry=fso.BuildPath(root,"desktop.py")
setup=fso.BuildPath(root,"start-app.cmd")
shell.CurrentDirectory=root
result=1
If fso.FileExists(python) Then
    probe="import sys,struct,importlib.util; sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P')==8 and all(importlib.util.find_spec(n) is not None for n in ('webview','clr','clr_loader','bottle','proxy_tools','fastapi','uvicorn','numpy','scipy','h5py','pydantic','websockets')) else 1)"
    result=shell.Run(Chr(34) & python & Chr(34) & " -c " & Chr(34) & probe & Chr(34),0,True)
End If
If result<>0 Then
    ' Show installation progress and leave any setup error visible through CMD.
    shell.Run Chr(34) & setup & Chr(34),1,False
Else
    result=shell.Run(Chr(34) & pythonw & Chr(34) & " " & Chr(34) & entry & Chr(34),0,False)
End If
