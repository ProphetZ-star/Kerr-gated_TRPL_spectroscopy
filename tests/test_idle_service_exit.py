"""Only regressions for safe cleanup of a reused backend at desktop exit."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import os
import socket
import shutil
import subprocess
import sys
import threading
import time
import urllib.error

import pytest
from websockets.sync.client import connect
import desktop
import desktop_service
from backend.errors import CommandError
from test_reported_bugs import factory,client,body,wait,app_module

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('data',[
    dict(busy='scan'),dict(busy='preview'),
    dict(runs={'saved':dict(saved=True)}),dict(runs={'memory':dict(saved=False)}),
    dict(background={'id':'background'}),dict(frame={'id':'calibration'}),
    dict(tasks={'fit':dict(status='running')}),
])
def test_worker_preserves_active_work_and_all_experiment_data(factory,data):
    engine=factory()
    for key,value in data.items():
        if key=='frame':engine.calibration_workbench.frame=value
        else:setattr(engine,key,value)
    with pytest.raises(CommandError,match='active work or experiment data'):
        engine.handle('prepare_empty_shutdown',dict(worker_session_id=engine.session))
    assert not engine.shutdown_prepared


def test_empty_shutdown_reservation_prevents_a_new_acquisition(factory):
    engine=factory()
    assert engine.handle('prepare_empty_shutdown',dict(worker_session_id=engine.session))['stopping']
    with pytest.raises(CommandError,match='shutting down'):
        engine.handle('run',body())
    assert engine.handle('status',{})['run'] is None


def test_changed_worker_session_cannot_be_stopped(factory):
    engine=factory()
    with pytest.raises(CommandError,match='session changed'):
        engine.handle('prepare_empty_shutdown',dict(worker_session_id='previous'))
    assert not engine.shutdown_prepared


def stop_body(client):
    identity=client.get('/api/v1/identity').json()
    return dict(checkout_id=identity['checkout_id'],process_id=identity['process_id'],
                worker_session_id=client.get('/api/v1/status').json()['worker_session_id'])


def test_empty_stop_api_checks_identity_and_reserves_worker(client,monkeypatch):
    shutdown=Mock();monkeypatch.setattr(app_module.app.state,'request_shutdown',shutdown,raising=False)
    data=stop_body(client)
    for changes in [dict(checkout_id='0'*64),dict(process_id=data['process_id']+1),dict(worker_session_id='previous')]:
        assert client.post('/api/v1/service/stop-if-empty',json=data|changes).status_code==409
    assert not shutdown.called
    assert client.post('/api/v1/service/stop-if-empty',json=data).status_code==200
    assert client.post('/api/v1/runs',json=body()).status_code==409
    wait(lambda:shutdown.called,lambda done:done)


@pytest.mark.parametrize('autosave',[False,True])
def test_empty_stop_api_preserves_saved_and_memory_experiments(client,monkeypatch,tmp_path,autosave):
    shutdown=Mock();monkeypatch.setattr(app_module.app.state,'request_shutdown',shutdown,raising=False)
    options=body(autosave=autosave)
    if autosave:options['save_path']=str(tmp_path/'run.h5')
    assert client.post('/api/v1/runs',json=options).status_code==200
    wait(lambda:client.get('/api/v1/status').json())
    assert client.post('/api/v1/service/stop-if-empty',json=stop_body(client)).status_code==409
    assert not shutdown.called


@pytest.mark.parametrize('status',[dict(busy='scan',run=None),dict(busy=None,run={'saved':True})])
def test_desktop_never_requests_cleanup_for_busy_or_loaded_service(monkeypatch,status):
    service=desktop_service.LocalService();monkeypatch.setattr(service,'existing',lambda:True)
    request=Mock(return_value=status);monkeypatch.setattr(service,'request',request)
    assert not service.stop_reused_if_empty()
    assert request.call_count==1 and request.call_args.args[0]=='/api/v1/status'


def test_changed_checkout_or_unavailable_server_is_left_alone(monkeypatch):
    service=desktop_service.LocalService()
    monkeypatch.setattr(service,'existing',Mock(side_effect=RuntimeError('another checkout')))
    request=Mock();monkeypatch.setattr(service,'request',request)
    assert not service.stop_reused_if_empty() and not request.called


def test_cleanup_conflict_does_not_force_terminate(monkeypatch):
    service=desktop_service.LocalService();service.server_pid=123
    monkeypatch.setattr(service,'existing',lambda:True)
    request=Mock(side_effect=[dict(busy=None,run=None,worker_session_id='session'),
                urllib.error.HTTPError('http://127.0.0.1',409,'New experiment started',{},None)])
    monkeypatch.setattr(service,'request',request)
    assert not service.stop_reused_if_empty() and request.call_count==2
    assert service.process is None and not service.stop_file.exists()


def test_reused_cleanup_runs_off_gui_thread(monkeypatch):
    started=threading.Event();release=threading.Event();destroyed=threading.Event()
    service=SimpleNamespace(cancel_startup=threading.Event(),owned=False,url='http://127.0.0.1:8765',close=Mock(),__enter__=lambda:None)
    def cleanup():started.set();release.wait(2);return True
    service.stop_reused_if_empty=Mock(side_effect=cleanup)
    monkeypatch.setattr(desktop_service,'LocalService',lambda:service)
    window=Mock();window.destroy.side_effect=destroyed.set
    def create(url,on_closing,on_started):
        on_started(window);before=time.monotonic()
        assert on_closing(window) is False
        assert time.monotonic()-before<.1 and started.wait(1)
        release.set();assert destroyed.wait(2)
        assert on_closing(window) is True
        return 0
    monkeypatch.setattr(desktop,'create_desktop',create)
    assert desktop.run_desktop()==0
    service.stop_reused_if_empty.assert_called_once()


def test_real_reused_empty_backend_and_browser_host_both_exit(tmp_path,monkeypatch):
    checkout=tmp_path/'checkout';checkout.mkdir()
    for directory in ['backend','frontend']:
        shutil.copytree(ROOT/directory,checkout/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['index.html','browser.py','desktop_service.py','log_utils.py']:
        shutil.copy2(ROOT/name,checkout/name)
    monkeypatch.setattr(desktop_service,'ROOT',checkout)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    process=subprocess.Popen([sys.executable,'browser.py','--no-browser','--port',str(port)],cwd=checkout,
        env=os.environ|dict(KERR_STATE_DIR=str(tmp_path/'state')),stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    service=desktop_service.LocalService(port)
    try:
        deadline=time.monotonic()+15
        while not service.existing():
            assert process.poll() is None and time.monotonic()<deadline
            time.sleep(.1)
        service.__enter__()
        assert not service.owned and service.request('/api/v1/health')['worker_alive']
        token=service.request('/api/v1/session')['token']
        with connect(service.url.replace('http:','ws:')+'/api/v1/events',
                     additional_headers={'Cookie':'kerr_session='+token},open_timeout=3) as stream:
            assert 'snapshot' in stream.recv(timeout=3)
            assert service.stop_reused_if_empty()
            assert process.wait(timeout=15)==0
        assert not service.existing()
    finally:
        if process.poll() is None:
            try:service.request('/api/v1/service/stop',{});process.wait(timeout=15)
            except Exception:process.terminate();process.wait(timeout=5)
