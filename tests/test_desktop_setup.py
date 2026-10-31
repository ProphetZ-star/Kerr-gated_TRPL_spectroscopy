"""Exercise VBS setup routing without opening a GUI or installing packages."""
from pathlib import Path
import os
import re
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('python_exists,pythonw_exists,probe_result,expected',[
    (False,False,0,'setup'),
    (True,True,1,'setup'),
    (True,True,0,'pythonw.exe'),
    (True,False,0,'python.exe'),
])
def test_vbs_routes_missing_environment_or_dependencies_to_setup(
        tmp_path,python_exists,pythonw_exists,probe_result,expected):
    if os.name!='nt':pytest.skip('Windows VBS entry point')
    scripts=tmp_path/'.venv'/'Scripts'
    scripts.mkdir(parents=True)
    for exists,name in [(python_exists,'python.exe'),(pythonw_exists,'pythonw.exe')]:
        if exists:(scripts/name).touch()
    stub=f'''
Class StubShell
    Public CurrentDirectory
    Public Function Run(command, style, wait)
        Dim fileSystem, logFile
        Set fileSystem=CreateObject("Scripting.FileSystemObject")
        Set logFile=fileSystem.OpenTextFile(fileSystem.BuildPath(fileSystem.GetParentFolderName(WScript.ScriptFullName),"calls.txt"),8,True,True)
        logFile.WriteLine CStr(style) & "|" & CStr(wait) & "|" & command
        logFile.Close
        If InStr(command, " -c ") > 0 Then
            Run={probe_result}
        Else
            Run=0
        End If
    End Function
End Class
'''
    source=(ROOT/'start-app.vbs').read_text(encoding='utf-8')
    source=source.replace('Option Explicit','Option Explicit\n'+stub,1)
    source=source.replace('Set shell=CreateObject("WScript.Shell")','Set shell=New StubShell')
    runner=tmp_path/'start-app.vbs'
    runner.write_text(source,encoding='utf-8')
    result=subprocess.run(['cscript.exe','//Nologo','//U',str(runner)],capture_output=True,
                          text=True,encoding='utf-16-le',timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stdout+result.stderr
    calls=(tmp_path/'calls.txt').read_text(encoding='utf-16').strip().splitlines()
    assert len(calls)==(2 if python_exists else 1)
    if python_exists:
        assert calls[0].startswith('0|True|') and 'find_spec' in calls[0]
    if expected=='setup':
        assert calls[-1].startswith('1|False|') and calls[-1].endswith('start-app.cmd"')
        assert 'desktop.py' not in calls[-1]
    else:
        assert calls[-1].startswith('0|False|') and expected in calls[-1] and 'desktop.py' in calls[-1]


def test_probe_rejects_missing_webview_without_importing_gui():
    source=(ROOT/'start-app.vbs').read_text(encoding='utf-8')
    probe=re.search(r'^\s*probe="([^"]+)"',source,re.M).group(1)
    code="import importlib.util; original=importlib.util.find_spec; importlib.util.find_spec=lambda n: None if n=='webview' else original(n); "+probe
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=10,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==1 and not result.stderr


def test_vbs_and_powershell_probe_the_same_desktop_dependencies():
    vbs=(ROOT/'start-app.vbs').read_text(encoding='utf-8')
    ps=(ROOT/'start-app.ps1').read_text(encoding='utf-8')
    vbs_modules=re.search(r'for n in \(([^)]+)\)',vbs).group(1)
    ps_modules=re.search(r'@\(([^)]+)\)',ps).group(1)
    assert set(re.findall(r"'([^']+)'",vbs_modules))==set(re.findall(r"'([^']+)'",ps_modules))
