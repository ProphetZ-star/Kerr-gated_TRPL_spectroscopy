Option Explicit
Dim shell, fso, root, python, pythonw, entry, setup, runtime, modules, result
Set shell=CreateObject("WScript.Shell")
Set fso=CreateObject("Scripting.FileSystemObject")
root=fso.GetParentFolderName(WScript.ScriptFullName)
python=fso.BuildPath(root,".venv\Scripts\python.exe")
entry=fso.BuildPath(root,"desktop.py")
setup=fso.BuildPath(root,"start-app.cmd")
shell.CurrentDirectory=root
runtime=fso.BuildPath(root,"desktop_runtime.py")
modules="webview,clr,clr_loader,bottle,proxy_tools,fastapi,uvicorn,numpy,scipy,h5py,pydantic,websockets,serial,pipython,usb"
result=1
If fso.FileExists(python) And fso.FileExists(runtime) Then
    result=shell.Run(Chr(34) & python & Chr(34) & " " & Chr(34) & runtime & Chr(34) & " --probe " & modules,0,True)
End If
If result<>0 Then
    ' Show installation progress and leave any setup error visible through CMD.
    shell.Run Chr(34) & setup & Chr(34),1,False
Else
    pythonw=fso.BuildPath(root,".venv\DesktopRuntime\pythonw.exe")
    If Not fso.FileExists(pythonw) Then
        pythonw=fso.BuildPath(root,".venv\DesktopRuntime\python.exe")
    End If
    result=shell.Run(Chr(34) & pythonw & Chr(34) & " " & Chr(34) & entry & Chr(34),0,False)
End If
