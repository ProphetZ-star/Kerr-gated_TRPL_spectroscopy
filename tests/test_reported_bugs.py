"""Focused regressions for qa/BUG_REPORT.md; no broad suite or GUI launch."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import importlib
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tracemalloc

import h5py
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from backend.models import RunRequest,FitRequest
from backend.worker import Engine
from backend.core import make_plan
from backend.analysis import fit_records
from backend.drivers.sim import SimCamera,SimStage,convolved_decay
from backend.storage.export import export_csv
from backend.storage.hdf5 import HDFWriter,read_run,append_analysis
from backend.calibration import make_frame
from backend.calibration_service import CalibrationWorkbench
from backend.errors import CommandError,error_status
from backend.manager import WorkerManager
import desktop_service
import desktop
import browser

ROOT=Path(__file__).resolve().parents[1]
app_module=importlib.import_module('backend.app')

def wait(fn,predicate=lambda value:not value['busy']):
    end=time.monotonic()+8
    while time.monotonic()<end:
        value=fn()
        if predicate(value):return value
        time.sleep(.005)
    raise AssertionError(value)

def body(**changes):
    return RunRequest(**(dict(request_id=os.urandom(8).hex(),mode='single',autosave=False,speedup=1000,
                             exposure_s=.01,settle_s=0)|changes)).model_dump()

@pytest.fixture
def factory(tmp_path,monkeypatch):
    engines=[]
    def create(events=None):
        monkeypatch.setenv('KERR_STATE_DIR',str(tmp_path/f'state-{len(engines)}'))
        engine=Engine(events or queue.Queue());engines.append(engine);return engine
    yield create
    for engine in engines:
        engine.stop.set();engine.pause.clear()
        for control in engine.task_controls.values():control['cancel'].set()
        if engine.job:engine.job.join(5)
        engine.pool.shutdown(wait=True,cancel_futures=True)

@pytest.fixture
def client(factory,monkeypatch):
    engine=factory()
    class Manager:
        def call(self,name,data=None):
            try:return engine.handle(name,data or {})
            except Exception as exc:raise CommandError(str(exc),error_status(exc,name)) from exc
        def after(self,seq):return []
        def close(self):pass
    monkeypatch.setattr(app_module,'WorkerManager',Manager)
    monkeypatch.setattr(app_module,'acquire_instance_lock',lambda:SimpleNamespace(close=lambda:None))
    with TestClient(app_module.app) as c:
        c.headers['X-Session-Token']=c.get('/api/v1/session').json()['token'];yield c

@pytest.mark.parametrize('literal',['NaN','Infinity','-Infinity'])
@pytest.mark.parametrize('route,template',[
    ('/camera/configure','{"exposure_s":%s}'),
    ('/runs','{"request_id":"nan","exposure_s":%s}'),
    ('/calibration/fit','{"frame_id":"x","pairs":[{"pixel":%s,"wavelength_nm":500}]}')])
def test_P2_nan_json_returns_422(client,literal,route,template):
    r=client.post('/api/v1'+route,content=template%literal,headers={'Content-Type':'application/json'})
    assert r.status_code==422,r.text
    json.dumps(r.json(),allow_nan=False)

def test_F2_http_validation_and_missing_resource_codes(client,tmp_path):
    assert client.get('/api/v1/runs/missing').status_code==404
    assert client.get('/api/v1/tasks/missing').status_code==404
    assert client.post('/api/v1/runs/open',json={'path':str(tmp_path/'missing.h5')}).status_code==404
    assert client.post('/api/v1/scan/plan',json=body(mode='scan',segments=[dict(start=0,end=6000,step=1)])).status_code==422
    for query in ['center_nm=nan','center_nm=inf','half_window_nm=-1']:
        assert client.get('/api/v1/runs/missing/export?'+query).status_code==422

def test_P2_export_memory_and_numpy_scalars(factory):
    engine=factory();run=engine.handle('run',body());wait(engine.status)
    record=engine.runs[run['run_id']]['records'][0]
    engine.runs[run['run_id']]['records']=[dict(record,record_id=i,spectrum=np.full(1024,1000,dtype=np.int32)) for i in range(300)]
    tracemalloc.start()
    text=engine.handle('export',dict(run_id=run['run_id'],center_nm=600,half_window_nm=5))
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert len(text.splitlines())==304 and peak<3*1024**2,(peak,len(text))

@pytest.mark.parametrize('change',[{'delay_ps':True},{'exposure_s':False},{'exposure_s':0},{'spectrum':[float('nan')]*3}])
def test_NUM_export_rejects_invalid_record_fields(change):
    record=dict(record_id=0,round_index=0,point_index=0,frame_index=0,delay_ps=0.,exposure_s=1.,spectrum=[1,2,3])|change
    with pytest.raises(ValueError):export_csv([record],[500,600,700],600,5,'simulation')

def test_NUM_csv_source_and_missing_fields():
    with pytest.raises(ValueError):export_csv([], [500,600],600,5,'simulation\ninjected')
    with pytest.raises(ValueError):export_csv([{}],[500,600],600,5,'simulation')

def test_P2_live_axis_and_discard(factory):
    engine=factory();engine.handle('run',body());wait(engine.status)
    frozen=list(engine.status()['run']['metadata']['calibration']['wavelengths_nm'])
    axis=np.linspace(520,720,1024).tolist()
    engine.handle('calibration',dict(calibration_id='new',source='measured',wavelengths_nm=axis))
    assert engine.status()['live_axis']==axis
    assert engine.status()['run']['metadata']['calibration']['wavelengths_nm']==frozen
    engine.handle('spectrograph',dict(center_nm=610,grating=300,slit_um=200))
    assert engine.status()['live_axis'] is None
    engine.handle('discard',dict(run_id=engine.current))
    assert engine.status()['latest'] is None

def fit_data():
    axis=np.linspace(500,800,1024);camera=SimCamera();records=[]
    for i,t in enumerate(np.linspace(-1,12,66)):
        counts,saturated=camera.acquire(t,42,i,.2)
        records.append(dict(delay_ps=float(t),spectrum=counts,exposure_s=.2,saturated_pixels=saturated))
    return records,axis

def test_P2_fit_ignores_saturation_outside_time_window():
    records,axis=fit_data();records[0]['saturated_pixels']=1
    result=fit_records(records,axis,FitRequest(model='single',min_delay_ps=0))
    assert min(result['delays_ps'])>=0
    with pytest.raises(ValueError,match='saturated'):fit_records(records,axis,FitRequest(model='single'))

@pytest.mark.parametrize('field,value',[('exposure_s',0),('exposure_s',float('inf')),('delay_ps',float('nan'))])
def test_NUM_fit_rejects_nonfinite_and_zero(field,value):
    records,axis=fit_data();records[4][field]=value
    with pytest.raises(ValueError):fit_records(records,axis,FitRequest(model='single'))

def test_NUM_convolution_camera_and_irf_guards():
    for tau,sigma in [(0,.1),(1,0),(float('nan'),.1)]:
        with pytest.raises(ValueError):convolved_decay(0,tau,sigma)
    with pytest.raises(ValueError):SimCamera().acquire(0,42,0,0)
    for irf in [1e-9,1e6]:
        with pytest.raises(ValueError):FitRequest(irf_fwhm_ps=irf)

def test_NUM_motion_estimate_and_plan_identity():
    request=RunRequest(**body(mode='repeat',repeats=2,segments=[dict(start=-1,end=3,step=.2)],exposure_s=.001))
    plan=make_plan(request,current_delay=0);stage=SimStage();motion=0
    for _ in range(plan['rounds']):
        motion+=stage.duration(plan['approach_mm']);stage.position_mm=plan['approach_mm']
        for target in plan['targets_mm']:motion+=stage.duration(target);stage.position_mm=target
    assert plan['estimated_seconds']==pytest.approx(motion+plan['total_frames']*(.001+.03))
    assert make_plan(request,current_delay=2)['plan_id']==plan['plan_id']
    tiny=make_plan(RunRequest(**body(mode='scan',segments=[dict(start=0,end=3e-8,step=1e-8)])))
    assert len(tiny['targets_mm'])==4
    endpoint=make_plan(RunRequest(**body(mode='scan',segments=[dict(start=0,end=.30000000000000004,step=.1)])))
    assert len(endpoint['targets_mm'])==4

def metadata(engine):
    request=body();return engine.metadata(request,make_plan(RunRequest(**request)),'bug-record')

def record(index=0):
    return dict(record_id=index,round_index=0,point_index=index,frame_index=0,delay_ps=float(index),exposure_s=1.,spectrum=np.arange(1024,dtype=np.int32))

def saved_file(engine,tmp_path):
    writer=HDFWriter(tmp_path/'experiment.h5',metadata(engine));writer.call('record',record());return Path(writer.call('close','completed'))

def test_NUM_strict_json_dtype_and_failed_init_cleanup(factory,tmp_path):
    engine=factory();meta=metadata(engine);meta['meta']['bad']=float('nan')
    with pytest.raises(ValueError):HDFWriter(tmp_path/'nan.h5',meta)
    assert not (tmp_path/'nan.partial.h5').exists()
    (tmp_path/'conflict.events.jsonl').write_text('existing',encoding='utf-8')
    with pytest.raises(FileExistsError):HDFWriter(tmp_path/'conflict.h5',metadata(engine))
    assert not (tmp_path/'conflict.partial.h5').exists()
    writer=HDFWriter(tmp_path/'counts.h5',metadata(engine))
    with pytest.raises(ValueError):writer.call('record',record()|dict(spectrum=np.full(1024,12.9)))
    writer.call('close','error')

@pytest.mark.parametrize('corruption',['committed','group','json','terminal'])
def test_NUM_corrupt_file_is_value_error(factory,tmp_path,corruption):
    path=saved_file(factory(),tmp_path)
    with h5py.File(path,'r+') as f:
        if corruption=='committed':del f['raw/committed'];f['raw'].create_dataset('committed',data=[True,True])
        if corruption=='group':del f['calibration']
        if corruption=='json':f['meta'].attrs['json']='{"bad": NaN}'
        if corruption=='terminal':f['meta'].attrs['terminal_status']='stopping-forever'
    with pytest.raises(ValueError,match='Invalid or damaged'):read_run(path)

def test_NUM_duplicate_analysis_has_explicit_error(factory,tmp_path):
    path=saved_file(factory(),tmp_path);append_analysis(path,'fit',{'observed':[1]})
    with pytest.raises(ValueError,match='already exists'):append_analysis(path,'fit',{'observed':[1]})

def test_F3_L3_terminal_stop_cannot_corrupt_state(factory):
    reached=threading.Event();release=threading.Event()
    class Events:
        def put(self,message,timeout=None):
            if message['type']=='run.state' and message['payload'].get('status')=='completed':
                reached.set();assert release.wait(5)
    engine=factory(Events());run=engine.handle('run',body())
    assert reached.wait(5)
    try:
        with pytest.raises(CommandError):engine.handle('stop',{'run_id':run['run_id']})
    finally:release.set()
    assert wait(engine.status)['run']['status']=='completed'

def test_L4_pause_after_last_point_finishes(factory):
    holder={}
    class Events:
        def put(self,message,timeout=None):
            if message['type']=='point.committed':holder['engine'].pause.set()
    engine=factory(Events());holder['engine']=engine;engine.handle('run',body())
    assert wait(engine.status)['run']['status']=='completed'

def test_F4_S1_background_cancel_and_stop_isolation(factory):
    engine=factory();engine.handle('configure',{'exposure_s':3600})
    engine.handle('background',dict(kind='dark',condition='bug reproduction'))
    with pytest.raises(CommandError,match='background'):engine.handle('stage_stop',{})
    engine.handle('background_stop',{});wait(engine.status)
    assert engine.background is None
    engine.handle('move',dict(delay_ps=900,relative=False,request_id='jog'))
    with pytest.raises(CommandError,match='move'):engine.handle('preview_stop',{})
    engine.handle('stage_stop',{});wait(engine.status)

def test_F6_F8_L7_offline_data_and_exposure(factory,tmp_path):
    engine=factory();engine.handle('configure',{'exposure_s':.1});engine.background={'exposure_s':.1}
    old_revision=engine.revision;run=engine.handle('run',body(exposure_s=.2));wait(engine.status)
    assert engine.background is None and engine.revision>old_revision
    engine.handle('disconnect',{});saved=engine.handle('save',dict(run_id=run['run_id'],path=str(tmp_path/'offline.h5')))
    engine.handle('discard',dict(run_id=run['run_id']))
    engine.camera.exposure_s=1
    engine.handle('open',{'path':saved['path']})
    assert engine.camera.exposure_s==.2
    task=engine.handle('fit',dict(run_id=run['run_id'],options={'model':'single'}))
    assert task['task_id']
    engine.handle('discard',dict(run_id=run['run_id']))

def test_F9_repeated_pause_is_idempotent(factory):
    engine=factory();run=engine.handle('run',body(mode='scan',segments=[dict(start=0,end=1,step=.1)],exposure_s=.2,speedup=1))
    wait(engine.status,lambda s:s['run']['status']=='running')
    engine.handle('pause',{'run_id':run['run_id']});engine.handle('pause',{'run_id':run['run_id']})
    engine.handle('stop',{'run_id':run['run_id']});wait(engine.status)

def test_L9_S5_task_bound_and_cancel(factory,monkeypatch):
    engine=factory();engine.tasks={str(i):{'status':'completed'} for i in range(100)};engine.prune_tasks();assert len(engine.tasks)==64
    run=engine.handle('run',body());wait(engine.status);started=threading.Event()
    def fit(*args,cancel=None):started.set();cancel.wait(5);raise ValueError('Fit cancelled')
    monkeypatch.setattr(importlib.import_module('backend.worker'),'fit_records',fit)
    task=engine.handle('fit',dict(run_id=run['run_id'],options={'model':'single'}));assert started.wait(2)
    engine.handle('task_cancel',task)
    result=wait(lambda:engine.handle('task',task),lambda t:t['status']!='running')
    assert result['status']=='cancelled'

def test_F5_event_gap_marker():
    manager=object.__new__(WorkerManager);manager.lock=threading.Lock();manager.ring=[dict(seq=50,type='x',schema_version=1,worker_session_id='s',timestamp_utc='now')]
    batch=manager.after(2);assert batch[0]['type']=='stream.gap' and batch[0]['payload']['resync_required']

def test_P2_worker_crash_clean_websocket_1011(tmp_path,monkeypatch):
    monkeypatch.setenv('KERR_STATE_DIR',str(tmp_path));monkeypatch.setattr(app_module,'acquire_instance_lock',lambda:SimpleNamespace(close=lambda:None))
    with TestClient(app_module.app) as c:
        c.get('/api/v1/session')
        process=app_module.app.state.worker.process;process.terminate();process.join(5)
        response=c.get('/api/v1/status');assert response.status_code==503 and 'Restart' in response.text
        with c.websocket_connect('/api/v1/events') as ws:
            with pytest.raises(WebSocketDisconnect) as error:ws.receive_json()
            assert error.value.code==1011

@pytest.mark.parametrize('calibration',[{'wavelengths_nm':None},{'calibration_id':'c','source':'bogus','wavelengths_nm':None}])
def test_CAL3_invalid_null_calibration_warns(tmp_path,monkeypatch,calibration):
    monkeypatch.setenv('KERR_STATE_DIR',str(tmp_path));workbench=CalibrationWorkbench();spec=dict(center_nm=600,grating=300,slit_um=200)
    workbench.path.write_text(json.dumps(dict(version=1,spectrograph=spec,calibration=calibration)),encoding='utf-8')
    assert workbench.restore(spec)==dict(calibration_id=None,source=None,wavelengths_nm=None)
    assert workbench.warning
    workbench.persist(dict(calibration_id='ok',source='synthetic',wavelengths_nm=None),spec)
    workbench.warning='old';assert workbench.restore(spec) and workbench.warning is None

def test_CAL1_and_CAL2_transient_windows_reader_retry(tmp_path,monkeypatch):
    with pytest.raises(ValueError):make_frame(10**400,42)
    monkeypatch.setenv('KERR_STATE_DIR',str(tmp_path));workbench=CalibrationWorkbench();replace=os.replace;calls=[]
    def busy_once(*args):
        calls.append(1)
        if len(calls)<3:raise PermissionError('sharing violation')
        return replace(*args)
    monkeypatch.setattr(os,'replace',busy_once)
    workbench.persist(dict(calibration_id='ok',source='synthetic',wavelengths_nm=None),dict(center_nm=600,grating=300,slit_um=200))
    assert len(calls)==3 and workbench.path.exists() and not list(tmp_path.glob('*.tmp'))

def test_E3_concurrent_start_reuses_winner(tmp_path,monkeypatch):
    monkeypatch.setattr(desktop_service,'ROOT',tmp_path);service=desktop_service.LocalService();child=Mock(pid=123);child.poll.return_value=1
    def spawn():service.process=child;service.log=io.StringIO()
    monkeypatch.setattr(service,'_spawn',spawn);monkeypatch.setattr(service,'existing',Mock(side_effect=[False,False,True]))
    assert service.__enter__() is service and not service.owned

def test_E4_identification_does_not_wait_for_busy_worker(monkeypatch):
    service=desktop_service.LocalService();expected=desktop_service.identity(desktop_service.ROOT)|dict(process_id=99)
    def request(path,**kwargs):
        if path=='/api/v1/status':raise TimeoutError('writing a large file')
        return expected
    monkeypatch.setattr(service,'request',request);assert service.existing()

def test_E19_reject_other_checkout(monkeypatch):
    service=desktop_service.LocalService();wrong=desktop_service.identity(desktop_service.ROOT)|dict(checkout_id='other')
    monkeypatch.setattr(service,'request',lambda *args,**kwargs:wrong)
    with pytest.raises(RuntimeError,match='another Kerr checkout'):service.existing()

def test_E9_popen_failure_closes_log(tmp_path,monkeypatch):
    monkeypatch.setattr(desktop_service,'ROOT',tmp_path);service=desktop_service.LocalService()
    monkeypatch.setattr(desktop_service.subprocess,'Popen',Mock(side_effect=OSError('spawn failed')))
    with pytest.raises(OSError):service._spawn()
    assert service.log is None and not service.owned

def test_E8_failed_kill_retains_stop_signal(tmp_path,monkeypatch):
    monkeypatch.setattr(desktop_service,'ROOT',tmp_path);service=desktop_service.LocalService();service.stop_file.parent.mkdir(parents=True)
    child=Mock(pid=555);child.poll.return_value=None;child.wait.side_effect=subprocess.TimeoutExpired('backend',1)
    service.process=child;service.log=io.StringIO()
    with pytest.raises(RuntimeError,match='signal retained'):service.close()
    assert service.process is child and service.stop_file.exists() and child.kill.called

def test_E2_closing_during_startup_cancels_without_wait(monkeypatch,tmp_path):
    service=SimpleNamespace(cancel_startup=threading.Event(),owned=True,close=Mock())
    service.__enter__=lambda:service.cancel_startup.wait(3)
    monkeypatch.setattr(desktop_service,'LocalService',lambda:service)
    window=Mock();threads=[]
    def create(url,on_closing,on_started):
        thread=threading.Thread(target=on_started,args=(window,));threads.append(thread);thread.start();time.sleep(.02)
        started=time.monotonic();assert on_closing(window) is True;assert time.monotonic()-started<.1
        thread.join(2);return 0
    monkeypatch.setattr(desktop,'create_desktop',create)
    assert desktop.run_desktop()==0
    assert service.cancel_startup.is_set() and service.close.called and not window.load_url.called

def test_E5_desktop_single_instance(tmp_path,monkeypatch):
    from desktop_instance import DesktopInstance
    first,second=DesktopInstance(tmp_path),DesktopInstance(tmp_path);monkeypatch.setattr(second,'activate',Mock())
    try:
        assert first.acquire();assert not second.acquire();assert second.activate.called
    finally:first.close();second.close()

def test_E11_bounded_logs(tmp_path):
    from log_utils import CappedStream,rotate_file
    path=tmp_path/'service.log'
    with path.open('a',encoding='utf-8') as stream:
        bounded=CappedStream(stream,100)
        for _ in range(50):bounded.write('x'*40+'\n');bounded.flush()
    assert path.stat().st_size<150
    rotate_file(path,10);assert path.with_suffix('.log.1').exists()

@pytest.mark.parametrize('port',['0','-1','65536','70000'])
def test_E6_invalid_cli_port_has_no_traceback(port):
    result=subprocess.run([sys.executable,'-X','utf8','-m','backend','--port',port,'--no-browser'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==2 and 'Traceback' not in result.stderr and '1024' in result.stderr

def test_E12_empty_shutdown_nonce_rejected():
    result=subprocess.run([sys.executable,'-m','backend','--shutdown-file',str(ROOT/'data'/'.desktop-stop-'),'--no-browser'],cwd=ROOT,capture_output=True,text=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==2 and 'Traceback' not in result.stderr

def test_E18_parent_watchdog_observes_only_liveness():
    module=importlib.import_module('backend.__main__');alive,release=module.parent_monitor(os.getpid())
    try:assert alive()
    finally:release()

def test_E1_python_fallback_before_venv(tmp_path):
    script="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; . '"+str(ROOT/'bootstrap.ps1')+"'; $script:attempts=@(); function py { if($args[0] -eq '-3.12'){$script:attempts+='3.12';$global:LASTEXITCODE=1;return};$script:attempts+='3';$global:LASTEXITCODE=0;Write-Output ('"+sys.executable+"' | ConvertTo-Json -Compress) }; $found=Get-KerrPython; @{found=$found;attempts=$script:attempts}|ConvertTo-Json -Compress"
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-Command',script],capture_output=True,text=True,encoding='utf-8',timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout);assert data['attempts']==['3.12','3'] and Path(data['found']).is_file()

def test_E14_E15_launch_wrappers_forward_arguments():
    for name in ['start-app.cmd','start-sim.cmd']:
        source=(ROOT/name).read_text();assert '%*' in source and '-ExecutionPolicy Bypass' in source and 'if not' in source
    assert (ROOT/'stop-web.cmd').is_file()

def test_NUM_double_fit_single_component_warns():
    axis=np.linspace(500,800,1024);records=[]
    for t in np.linspace(-1,12,80):
        value=100+20000*float(convolved_decay(t,1.8,.25/2.354820045))
        records.append(dict(delay_ps=float(t),exposure_s=1,saturated_pixels=0,spectrum=np.full(1024,value)))
    result=fit_records(records,axis,FitRequest(model='double'))
    assert result['warnings'],'A second unidentifiable lifetime must not be presented without warning'

def test_E7_readiness_checks_identity_and_logs_failure(monkeypatch,caplog):
    module=importlib.import_module('backend.__main__')
    opener=SimpleNamespace(open=lambda *args,**kwargs:io.BytesIO(b'{"application":"other"}'))
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *args:opener)
    monkeypatch.setattr(module.time,'sleep',lambda delay:None);opened=Mock();monkeypatch.setattr(module.webbrowser,'open',opened)
    assert module.open_when_ready('http://127.0.0.1:12345') is False and not opened.called
    assert 'not ready' in caplog.text

def test_E4_status_transient_timeout_retried(monkeypatch):
    service=desktop_service.LocalService();responses=Mock(side_effect=[TimeoutError(),dict(busy=None)])
    monkeypatch.setattr(service,'request',responses)
    assert service.retry_status(.5)==dict(busy=None) and responses.call_count==2

def test_E10_close_checks_do_not_block_gui_thread(monkeypatch):
    started=threading.Event();release=threading.Event();destroyed=threading.Event()
    service=SimpleNamespace(cancel_startup=threading.Event(),owned=True,url='http://127.0.0.1:8765',close=Mock(),__enter__=lambda:None)
    def confirm(window):started.set();release.wait(2);return True
    service.confirm_close=confirm;window=Mock();window.destroy.side_effect=destroyed.set
    monkeypatch.setattr(desktop_service,'LocalService',lambda:service)
    def create(url,on_closing,on_started):
        on_started(window);before=time.monotonic();assert on_closing(window) is False
        assert time.monotonic()-before<.1 and started.wait(1)
        release.set();assert destroyed.wait(2);return 0
    monkeypatch.setattr(desktop,'create_desktop',create)
    assert desktop.run_desktop()==0

def test_E17_legacy_webview_shows_install_message(monkeypatch,tmp_path):
    class Closing:
        def __iadd__(self,fn):return self
    window=Mock();window.events=SimpleNamespace(closing=Closing())
    fake=SimpleNamespace(settings={},guilib=SimpleNamespace(renderer='mshtml'),create_window=Mock(return_value=window))
    fake.start=lambda **kwargs:kwargs['func']()
    monkeypatch.setitem(sys.modules,'webview',fake);monkeypatch.setattr(desktop,'ROOT',tmp_path);monkeypatch.setattr(desktop,'ERROR_LOG',tmp_path/'error.log')
    monkeypatch.setattr(desktop,'_loading_page',lambda:'<html>Loading</html>')
    callback=Mock();desktop.create_desktop(None,on_started=callback)
    assert not callback.called and 'WebView2' in window.load_html.call_args.args[0]

def test_E21_no_browser_error_never_opens_messagebox(tmp_path,monkeypatch):
    monkeypatch.setattr(browser,'ERROR_LOG',tmp_path/'error.log');monkeypatch.setattr(browser,'SHOW_ERROR_DIALOG',False)
    import ctypes
    dialog=Mock();monkeypatch.setattr(ctypes.windll.user32,'MessageBoxW',dialog)
    browser._report_error(RuntimeError('test'));assert not dialog.called

def test_CAL2_persistent_external_lock_preserves_old_file(tmp_path,monkeypatch):
    monkeypatch.setenv('KERR_STATE_DIR',str(tmp_path));workbench=CalibrationWorkbench();spec=dict(center_nm=600,grating=300,slit_um=200)
    workbench.persist(dict(calibration_id='old',source='synthetic',wavelengths_nm=None),spec);old=workbench.path.read_bytes()
    monkeypatch.setattr(os,'replace',Mock(side_effect=PermissionError('reader refuses delete sharing')))
    with pytest.raises(ValueError,match='previous calibration is retained'):
        workbench.persist(dict(calibration_id='new',source='synthetic',wavelengths_nm=None),spec)
    assert workbench.path.read_bytes()==old and not list(tmp_path.glob('*.tmp'))

def test_L9_reopened_analysis_history_is_bounded_without_deleting_disk_results(factory,tmp_path):
    path=saved_file(factory(),tmp_path)
    for i in range(70):append_analysis(path,str(i),dict(finished_utc=f'{i:03}',observed=[1]))
    data,_,_=read_run(path);assert len(data['analysis'])==64
    with h5py.File(path,'r') as file:assert len(file['analysis'])==70

def test_T1_acquisition_fixture_isolated_on_two_runs(tmp_path,tmp_path_factory,monkeypatch):
    import importlib.util
    spec=importlib.util.spec_from_file_location('original_acquisition_tests',ROOT/'tests/test_acquisition.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    sentinel=str(tmp_path/'untouched-user-state');monkeypatch.setenv('KERR_STATE_DIR',sentinel)
    monkeypatch.setattr(app_module,'acquire_instance_lock',lambda:SimpleNamespace(close=lambda:None))
    for _ in range(2):
        fixture=module.client.__wrapped__(tmp_path_factory);client=next(fixture)
        try:
            assert client.get('/api/v1/status').json()['spectrograph']['center_nm']==600
            assert client.post('/api/v1/spectrograph/config',json=dict(center_nm=610,grating=300,slit_um=200)).status_code==200
        finally:
            with pytest.raises(StopIteration):next(fixture)
        assert os.environ['KERR_STATE_DIR']==sentinel
    assert not Path(sentinel).exists()

def test_E20_invalid_interpreter_does_not_create_venv(tmp_path):
    script="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); . '"+str(ROOT/'bootstrap.ps1')+"'; function Get-KerrPython { throw 'Unsupported interpreter' }; try { Initialize-KerrEnvironment '"+str(tmp_path)+"' @('fastapi') 'requirements.txt' } catch { Write-Output $_.Exception.Message }; if(Test-Path -LiteralPath '"+str(tmp_path/'.venv')+"'){exit 5}"
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-Command',script],capture_output=True,text=True,encoding='utf-8',timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0 and 'Unsupported interpreter' in result.stdout

def test_E16_missing_pythonw_uses_hidden_python_without_opening_app():
    for name in ['start-app.vbs','start-web.vbs']:assert 'Scripts\\python.exe' in (ROOT/name).read_text()
    script="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); function Test-Path { param($LiteralPath); if($LiteralPath -like '*pythonw.exe'){return $false}; Microsoft.PowerShell.Management\\Test-Path -LiteralPath $LiteralPath }; function Start-Process { param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle); @{file=$FilePath;style=$WindowStyle;args=$ArgumentList}|ConvertTo-Json -Compress }; . '"+str(ROOT/'start-app.ps1')+"'"
    script=script.replace('Management\\\\Test-Path','Management\\Test-Path')
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-Command',script],capture_output=True,text=True,encoding='utf-8',timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stderr
    result=json.loads(result.stdout.strip().splitlines()[-1]);assert result['file'].endswith('python.exe') and result['style']=='Hidden'

def test_E18_stop_endpoint_refuses_unsaved_data_and_stops_only_idle(client,monkeypatch):
    shutdown=Mock();monkeypatch.setattr(app_module.app.state,'request_shutdown',shutdown,raising=False)
    run=client.post('/api/v1/runs',json=body()).json()
    wait(lambda:client.get('/api/v1/status').json())
    assert client.post('/api/v1/service/stop',json={}).status_code==409 and not shutdown.called
    assert client.post('/api/v1/runs/'+run['run_id']+'/discard',json={}).status_code==200
    assert client.post('/api/v1/service/stop',json={}).status_code==200
    wait(lambda:shutdown.called,lambda done:done)
