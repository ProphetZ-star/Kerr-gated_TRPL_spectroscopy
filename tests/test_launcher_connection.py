"""Regression for Windows venv wrapper PID causing immediate service shutdown."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desktop_service import LocalService


def test_owned_launch_survives_different_wrapper_pid(monkeypatch):
    service=LocalService()
    child=Mock(pid=123)
    child.poll.return_value=None
    service.process=child
    service.server_pid=456
    service.server_launch_id=service.launch_id
    monkeypatch.setattr(service,'existing',lambda:True)
    close=Mock()
    monkeypatch.setattr(service,'close',close)
    assert service.__enter__() is service
    assert service.owned and not close.called


def test_other_launch_is_not_claimed_even_with_matching_pid(monkeypatch):
    service=LocalService()
    child=Mock(pid=123)
    child.poll.return_value=None
    service.process=child
    service.server_pid=123
    service.server_launch_id='other-launch'
    monkeypatch.setattr(service,'existing',lambda:True)
    close=Mock()
    monkeypatch.setattr(service,'close',close)
    assert service.__enter__() is service
    close.assert_called_once()


@pytest.mark.parametrize('executable',['python.exe','pythonw.exe'])
def test_real_browser_launcher_stays_reachable_and_reuses_service(executable,tmp_path):
    interpreter=Path(sys.executable).with_name(executable)
    if os.name!='nt' or not interpreter.is_file():
        pytest.skip('Windows launcher regression')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
    env=os.environ|dict(KERR_STATE_DIR=str(tmp_path/'state'))
    command=[str(interpreter),str(ROOT/'browser.py'),'--no-browser','--port',str(port)]
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    launcher=subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    service=LocalService(port)
    try:
        deadline=time.monotonic()+15
        while not service.existing():
            assert launcher.poll() is None,'Launcher exited before HTTP readiness'
            assert time.monotonic()<deadline,'Local backend did not become reachable'
            time.sleep(.1)
        first=service.request('/api/v1/identity')
        assert first['launch_id'] and service.request('/api/v1/health')['worker_alive']
        time.sleep(.5)
        assert launcher.poll() is None,'A ready backend must remain running'
        with service.opener.open(service.url+'/',timeout=3) as response:
            assert response.status==200 and b'Kerr Studio' in response.read()
        assert 'busy' in service.request('/api/v1/status')
        # Reopening reuses the live service and must not shut down its owner.
        repeat=subprocess.run(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                              capture_output=True,timeout=10,creationflags=flags)
        assert repeat.returncode==0
        assert launcher.poll() is None
        assert service.request('/api/v1/identity')['process_id']==first['process_id']
        service.request('/api/v1/service/stop',{})
        assert launcher.wait(timeout=15)==0
        assert not service.existing()
    finally:
        if launcher.poll() is None:
            try:
                service.request('/api/v1/service/stop',{})
                launcher.wait(timeout=15)
            except Exception:
                launcher.terminate()
                launcher.wait(timeout=5)
