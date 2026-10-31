import csv
import io
import time
import uuid
import numpy as np
import pytest
import h5py
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.app import app
from backend.models import RunRequest, MoveRequest, CalibrationRequest
from backend.core import make_plan, delay_to_mm, mm_to_delay
from backend.storage.export import export_csv
from backend.drivers.sim import SimCamera


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('KERR_STATE_DIR',str(tmp_path_factory.mktemp('acquisition-state')))
        with TestClient(app) as c:
            token=c.get('/api/v1/session').json()['token']
            c.headers['X-Session-Token']=token
            yield c


def request(tmp_path,**changes):
    return dict(mode='scan',segments=[dict(start=-.2,end=.2,step=.1)],repeats=1,
                exposure_s=.05,frames_per_point=1,speedup=1000,seed=42,settle_s=0,
                autosave=True,save_path=str(tmp_path/(uuid.uuid4().hex+'.h5')),
                request_id=uuid.uuid4().hex,**changes)


def post(c,path,data=None):
    r=c.post('/api/v1'+path,json=data or {})
    assert r.status_code==200,r.text
    return r.json()


def wait(c,predicate,timeout=12):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        s=c.get('/api/v1/status').json()
        if predicate(s):return s
        time.sleep(.03)
    raise AssertionError(s)


def completed(c):return wait(c,lambda s:not s['busy'])['run']


def test_models_and_plan():
    r=RunRequest(request_id='x',autosave=False)
    assert len(make_plan(r)['delays_ps'])==97
    assert MoveRequest(delay_ps=1,relative=True,request_id='x').relative
    for bad in [float('nan'),float('inf'),True]:
        with pytest.raises(ValidationError):RunRequest(request_id='x',exposure_s=bad)
    with pytest.raises(ValidationError):RunRequest(request_id='x',repeats=1.5)
    with pytest.raises(ValueError):make_plan(RunRequest(request_id='x',segments=[dict(start=0,end=1,step=.1),dict(start=.5,end=2,step=.1)]))
    p=make_plan(RunRequest(request_id='x',segments=[dict(start=0,end=1,step=.3)]))
    assert p['delays_ps']==[0,.3,.6,.9,1]
    assert mm_to_delay(delay_to_mm(2.4))==pytest.approx(2.4)
    with pytest.raises(ValueError):make_plan(RunRequest(request_id='x',segments=[dict(start=0,end=5000,step=1)]))
    with pytest.raises(ValidationError):CalibrationRequest(calibration_id='bad',wavelengths_nm=[1]*1024)


def test_sim_reproducible():
    camera=SimCamera()
    a,s=camera.acquire(0,42,0,1)
    b,_=camera.acquire(0,42,0,1)
    c,_=camera.acquire(0,42,1,1)
    assert np.array_equal(a,b) and not np.array_equal(a,c)
    assert a.dtype==np.int32 and a.shape==(1024,)
    _,s=camera.acquire(0,42,0,1,fault='saturation')
    assert s>0


def test_api_security_and_hardware_gate(client):
    assert client.post('/api/v1/stage/zero',headers={'X-Session-Token':'wrong'}).status_code==403
    assert client.get('/api/v1/status',headers={'Origin':'https://example.org'}).status_code==403
    r=client.post('/api/v1/devices/connect',json={'mode':'hardware'})
    assert r.status_code==409 and 'no simulation fallback' in r.text
    assert client.get('/.venv/pyvenv.cfg').status_code==404
    with client.websocket_connect('/api/v1/events') as ws:
        assert ws.receive_json()['type']=='snapshot'


def test_repeat_file_and_csv(client,tmp_path):
    body=request(tmp_path);body.update(mode='repeat',repeats=2,frames_per_point=2)
    run=post(client,'/runs',body)
    assert post(client,'/runs',body)['run_id']==run['run_id']
    terminal=completed(client)
    assert terminal['status']=='completed' and terminal['committed_frames']==20
    records=client.get(f"/api/v1/runs/{run['run_id']}/records").json()['records']
    assert len(records)==20
    with h5py.File(terminal['path'],'r') as f:
        assert f['raw/spectra'].shape==(20,1024)
        assert f['raw/committed'][:].all()
        np.testing.assert_array_equal(f['raw/spectra'][0],records[0]['spectrum'])
        assert f['meta'].attrs['terminal_status']=='completed'
    text=client.get(f"/api/v1/runs/{run['run_id']}/export").text
    rows=list(csv.DictReader(io.StringIO('\n'.join(x for x in text.splitlines() if not x.startswith('#')))))
    assert len(rows)==20
    axis=np.linspace(500,800,1024);mask=abs(axis-600)<=5
    assert float(rows[0]['mean_counts_per_pixel'])==pytest.approx(np.mean(np.asarray(records[0]['spectrum'])[mask]))
    post(client,'/runs/open',dict(path=terminal['path']))
    assert client.get(f"/api/v1/runs/{run['run_id']}/records").json()['records']==records


def test_pause_stop_and_lock(client,tmp_path):
    body=request(tmp_path);body.update(exposure_s=.2,speedup=1)
    run=post(client,'/runs',body);identifier=run['run_id']
    wait(client,lambda s:s['run']['status']=='running')
    post(client,f'/runs/{identifier}/pause')
    s=wait(client,lambda s:s['run']['status']=='paused')
    count=s['run']['committed_frames'];time.sleep(.15)
    assert client.get('/api/v1/status').json()['run']['committed_frames']==count
    assert client.post('/api/v1/stage/zero',json={}).status_code==409
    post(client,f'/runs/{identifier}/resume')
    wait(client,lambda s:s['run']['step']=='exposing')
    post(client,f'/runs/{identifier}/stop')
    terminal=completed(client)
    assert terminal['status']=='stopped' and terminal['committed_frames']<5
    with h5py.File(terminal['path'],'r') as f:
        assert len(f['raw/spectra'])==terminal['committed_frames']


@pytest.mark.parametrize('kind',['move_timeout','camera_disconnect','write_failure'])
def test_fault_preserves_records(client,tmp_path,kind):
    body=request(tmp_path);body['fault']={'kind':kind,'point_index':2}
    post(client,'/runs',body)
    run=completed(client)
    assert run['status']=='error' and run['committed_frames']==2 and run['error']
    with h5py.File(run['path'],'r') as f:assert f['raw/committed'][:].sum()==2


def test_zero_background_preview_calibration(client,tmp_path):
    post(client,'/stage/move',dict(delay_ps=1,relative=False,request_id=uuid.uuid4().hex))
    wait(client,lambda s:not s['busy']);before=client.get('/api/v1/status').json()
    after=post(client,'/stage/zero')
    assert after['stage']['controller_reported_mm']==before['stage']['controller_reported_mm']
    assert after['stage']['delay_ps']==0
    post(client,'/camera/configure',dict(exposure_s=.1))
    post(client,'/backgrounds',dict(kind='dark',condition='simulated closed shutter'))
    s=wait(client,lambda s:not s['busy']);assert len(s['background']['spectrum'])==1024
    post(client,'/preview/start');wait(client,lambda s:s['latest'] is not None)
    post(client,'/preview/stop');wait(client,lambda s:not s['busy'])
    post(client,'/spectrograph/config',dict(center_nm=610,grating=300,slit_um=200))
    assert client.get('/api/v1/status').json()['calibration']['wavelengths_nm'] is None
    post(client,'/calibration/load',dict(calibration_id='synthetic-test',source='synthetic',wavelengths_nm=np.linspace(500,800,1024).tolist()))


def test_memory_save_and_fit(client,tmp_path):
    body=request(tmp_path);body.update(autosave=False,segments=[dict(start=-1,end=1,step=.1),dict(start=1,end=15,step=.5)],exposure_s=1)
    run=post(client,'/runs',body);terminal=completed(client)
    assert terminal['status']=='completed' and not terminal['saved']
    assert client.post('/api/v1/runs',json=request(tmp_path)).status_code==409
    task=post(client,f"/runs/{run['run_id']}/fits",dict(model='double',wavelength_nm=600,half_window_nm=5,irf_fwhm_ps=.25))
    for _ in range(100):
        result=client.get('/api/v1/tasks/'+task['task_id']).json()
        if result['status']!='running':break
        time.sleep(.05)
    assert result['status']=='completed',result
    p=result['result']['parameters']
    assert p['tau_1_ps']==pytest.approx(.42,rel=.1)
    assert p['tau_2_ps']==pytest.approx(3.8,rel=.1)
    saved=post(client,f"/runs/{run['run_id']}/save",dict(path=str(tmp_path/'memory.h5')))
    assert saved['saved']
    with h5py.File(saved['path'],'r') as f:
        assert task['task_id'] in f['analysis']


def test_single_stop_manual_and_no_calibration(client,tmp_path):
    before=client.get('/api/v1/status').json()['stage']['controller_reported_mm']
    post(client,'/stage/move',dict(delay_ps=500,relative=True,request_id=uuid.uuid4().hex))
    time.sleep(.03);post(client,'/stage/stop')
    state=wait(client,lambda s:not s['busy'])
    assert before<state['stage']['controller_reported_mm']<before+delay_to_mm(500,zero=0)
    post(client,'/calibration/load',dict(calibration_id='none',wavelengths_nm=None))
    body=request(tmp_path);body['mode']='single';body['frames_per_point']=3
    run=post(client,'/runs',body);terminal=completed(client)
    assert terminal['completed_points']==1 and terminal['committed_frames']==3
    assert client.get(f"/api/v1/runs/{run['run_id']}/export").status_code==409
    with h5py.File(terminal['path'],'r') as f:
        assert f['raw/spectra'].shape==(3,1024)
    post(client,'/calibration/load',dict(calibration_id='synthetic',source='synthetic',wavelengths_nm=np.linspace(500,800,1024).tolist()))


def test_csv_invalid_window():
    with pytest.raises(ValueError):export_csv([],None,600,5,'simulation')
    with pytest.raises(ValueError):export_csv([],[500,600],700,200,'simulation')
