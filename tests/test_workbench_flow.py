"""HTTP/worker/HDF5 checks without a browser, desktop window, or listening server."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import queue
import re
import threading
import time
import uuid
import os
import tempfile

import h5py
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from html.parser import HTMLParser

from backend.calibration import make_frame, extract_profile, fit_mapping
from backend.calibration_models import CaptureRequest, MappingRequest
from backend.calibration_service import CalibrationWorkbench

app_module = importlib.import_module('backend.app')


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Real spawned worker, isolated calibration persistence; never attach to the user's service.
    monkeypatch.setenv('KERR_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(app_module, 'acquire_instance_lock', lambda: SimpleNamespace(close=lambda: None))
    with TestClient(app_module.app) as c:
        c.headers['X-Session-Token'] = c.get('/api/v1/session').json()['token']
        yield c


def post(c, path, body):
    result = c.post('/api/v1' + path, json=body)
    assert result.status_code == 200, result.text
    return result.json()


def wait_until(fn, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if predicate(result):
            return result
        time.sleep(.02)
    raise AssertionError(result)


def status(c):
    return c.get('/api/v1/status').json()


def run_body(tmp_path, **changes):
    return dict(mode='scan', segments=[dict(start=-1, end=3, step=.2), dict(start=3, end=12, step=1)],
                request_id=uuid.uuid4().hex, exposure_s=.2, seed=42, speedup=1000,
                settle_s=0, autosave=True, save_path=str(tmp_path / (uuid.uuid4().hex + '.h5'))) | changes


def calibration_body(frame, profile):
    return dict(frame_id=frame['frame_id'], row_start=85, row_end=165, degree=2, convention='vacuum',
                reference_name='Illustrative demo lines', reference_source='synthetic',
                pairs=[dict(pixel=round(pixel, 3), wavelength_nm=round(490 + .24*p + .00002*p*p, 3), enabled=True)
                       for pixel, p in zip(profile['peaks_pixels'], [115.23, 282.65, 449.38, 655.14, 851.72])])


def test_calibration_analysis_hdf5_roundtrip(client, tmp_path):
    initial = status(client)
    old = post(client, '/runs', run_body(tmp_path, mode='single'))
    wait_until(lambda: status(client), lambda s: not s['busy'])
    frame = post(client, '/calibration/frames', dict(exposure_s=1, seed=42))
    assert frame['shape'] == [255, 1024] and frame['source'] == 'synthetic'
    assert client.get('/api/v1/calibration/frame').json() == frame
    extracted = post(client, '/calibration/extract', dict(frame_id=frame['frame_id'], row_start=85, row_end=165))
    raw = np.array(frame['image_counts']).reshape(255, 1024)
    np.testing.assert_allclose(extracted['profile_counts'], raw[85:166].mean(axis=0))
    np.testing.assert_allclose(extracted['peaks_pixels'], [115.23, 282.65, 449.38, 655.14, 851.72], atol=.08)
    mapping_request = calibration_body(frame, extracted)
    solution = post(client, '/calibration/fit', mapping_request)
    assert solution['rms_nm'] < .03 and not solution['applied']
    assert solution['convention'] == 'vacuum' and solution['source'] == 'synthetic'
    assert status(client)['calibration'] == initial['calibration']  # Draft fit has no side effects.
    applied = post(client, '/calibration/apply', dict(solution_id=solution['solution_id']))
    assert applied['calibration']['wavelengths_nm'] == solution['wavelengths_nm']
    assert applied['run']['metadata']['calibration'] == old['metadata']['calibration']
    exported = client.get('/api/v1/calibration/solutions/' + solution['solution_id']).json()
    assert exported['applied'] and exported['wavelengths_nm'] == solution['wavelengths_nm']
    assert CalibrationWorkbench().restore(applied['spectrograph']) == applied['calibration']

    run = post(client, '/runs', run_body(tmp_path))
    completed = wait_until(lambda: status(client), lambda s: not s['busy'])['run']
    records = client.get('/api/v1/runs/' + run['run_id'] + '/records').json()['records']
    assert completed['metadata']['calibration']['wavelengths_nm'] == solution['wavelengths_nm']
    fit_request = dict(model='single', wavelength_nm=600, half_window_nm=5, irf_fwhm_ps=.25,
                       min_delay_ps=-.5, max_delay_ps=8)
    task = post(client, '/runs/' + run['run_id'] + '/fits', fit_request)
    result = wait_until(lambda: client.get('/api/v1/tasks/' + task['task_id']).json(), lambda t: t['status'] != 'running')
    assert result['status'] == 'completed', result
    fit = result['result']
    assert fit['request'] == fit_request and fit['run_id'] == run['run_id']
    assert fit['task_id'] == task['task_id'] and fit['record_count'] == len(records)
    assert min(fit['delays_ps']) >= -.5 and max(fit['delays_ps']) <= 8
    np.testing.assert_allclose(np.array(fit['observed']) - fit['predicted'], fit['residuals'])
    mask = np.abs(np.array(solution['wavelengths_nm']) - 600) <= 5
    for t, observed in zip(fit['delays_ps'], fit['observed']):
        expected = np.mean([np.array(r['spectrum'])[mask].mean()/r['exposure_s'] for r in records if r['delay_ps'] == t])
        assert observed == pytest.approx(expected)
    with h5py.File(completed['path'], 'r') as f:
        assert json.loads(f['calibration'].attrs['json']) == applied['calibration']
        assert json.loads(f['analysis'][task['task_id']][()]) == fit
    reopened = post(client, '/runs/open', dict(path=completed['path']))
    assert reopened['metadata']['analysis'][task['task_id']] == fit
    post(client, '/spectrograph/config', dict(center_nm=610, grating=300, slit_um=200))
    assert status(client)['calibration']['wavelengths_nm'] is None
    assert status(client)['run']['metadata']['calibration'] == applied['calibration']
    assert client.post('/api/v1/calibration/apply', json=dict(solution_id=solution['solution_id'])).status_code == 409

    # Genuine backend outputs are also consumed by the no-browser JavaScript contract check.
    artifact = Path(os.environ.get('KERR_FIXTURE_PATH',Path(__file__).resolve().parents[1] / 'test-output' / 'data-flow-fixtures.json'))
    artifact.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=artifact.parent,prefix='.fixture-',suffix='.tmp',delete=False) as stream:
        temporary=Path(stream.name)
        stream.write(json.dumps(dict(initial_status=initial, frame=frame, extracted=extracted,
                                       mapping_request=mapping_request, solution=solution, applied_status=applied,
                                       exported=exported, run=reopened, records=records, fit_request=fit_request,
                                       task=result), allow_nan=False))
    try:os.replace(temporary,artifact)
    finally:temporary.unlink(missing_ok=True)


def test_calibration_validation_busy_and_stale_frames(client, tmp_path):
    assert client.post('/api/v1/calibration/frames', json={'exposure_s': True}).status_code == 422
    assert client.post('/api/v1/calibration/frames', json={}, headers={'X-Session-Token': 'bad'}).status_code == 403
    frame = post(client, '/calibration/frames', {})
    bad_roi = dict(frame_id=frame['frame_id'], row_start=166, row_end=85)
    assert client.post('/api/v1/calibration/extract', json=bad_roi).status_code == 422
    extracted = post(client, '/calibration/extract', dict(frame_id=frame['frame_id']))
    body = calibration_body(frame, extracted)
    duplicate = json.loads(json.dumps(body)); duplicate['pairs'][1]['pixel'] = duplicate['pairs'][0]['pixel']
    assert client.post('/api/v1/calibration/fit', json=duplicate).status_code == 422
    boolean = json.loads(json.dumps(body)); boolean['pairs'][0]['pixel'] = True
    assert client.post('/api/v1/calibration/fit', json=boolean).status_code == 422
    solution = post(client, '/calibration/fit', body)
    fresh_frame = post(client, '/calibration/frames', {'seed': 43})
    assert client.post('/api/v1/calibration/extract', json=dict(frame_id=frame['frame_id'])).status_code == 409
    assert client.post('/api/v1/calibration/apply', json=dict(solution_id=solution['solution_id'])).status_code == 409
    fresh_extraction = post(client, '/calibration/extract', dict(frame_id=fresh_frame['frame_id']))
    run = post(client, '/runs', run_body(tmp_path, exposure_s=1, speedup=1))
    assert client.post('/api/v1/calibration/frames', json={}).status_code == 409
    assert client.post('/api/v1/calibration/extract', json=dict(frame_id=fresh_frame['frame_id'])).status_code == 409
    assert client.post('/api/v1/calibration/fit', json=calibration_body(fresh_frame, fresh_extraction)).status_code == 409
    post(client, '/runs/' + run['run_id'] + '/stop', {})
    wait_until(lambda: status(client), lambda s: not s['busy'])
    saturated = post(client, '/calibration/frames', dict(exposure_s=3600))
    ex = post(client, '/calibration/extract', dict(frame_id=saturated['frame_id']))
    assert ex['saturated_pixels'] > 0
    assert client.post('/api/v1/calibration/fit', json=calibration_body(saturated, ex)).status_code == 409


def test_analysis_rejects_missing_axis_and_invalid_range(client, tmp_path):
    post(client, '/calibration/load', dict(calibration_id='none', wavelengths_nm=None))
    run = post(client, '/runs', run_body(tmp_path))
    wait_until(lambda: status(client), lambda s: not s['busy'])
    path = '/runs/' + run['run_id'] + '/fits'
    assert client.post('/api/v1' + path, json=dict(min_delay_ps=5, max_delay_ps=2)).status_code == 422
    task = post(client, path, dict(model='single'))
    result = wait_until(lambda: client.get('/api/v1/tasks/' + task['task_id']).json(), lambda t: t['status'] != 'running')
    assert result['status'] == 'error' and 'calibration' in result['error']
    assert client.get('/api/v1/tasks/missing').status_code == 404


@pytest.mark.parametrize('descending', [False, True])
def test_mapping_uses_assigned_lines_and_checks_full_domain(descending):
    pixels = [50, 200, 400, 600, 900]
    waves = [780 - .2*p if descending else 480 + .2*p for p in pixels]
    result = fit_mapping(MappingRequest(frame_id='unit', degree=1, pairs=[
        dict(pixel=p, wavelength_nm=w) for p, w in zip(pixels, waves)]))
    np.testing.assert_allclose(result['wavelengths_nm'], [780-.2*p if descending else 480+.2*p for p in range(1024)], atol=1e-9)
    assert max(map(abs, result['residuals_nm'])) < 1e-9
    # All supplied lines increase, but this parabola turns over outside their coverage.
    with pytest.raises(ValueError):
        fit_mapping(MappingRequest(frame_id='fold', degree=2, pairs=[
            dict(pixel=p, wavelength_nm=500+.3*p-.0003*p*p) for p in [50, 100, 200, 300]]))


def test_frame_reproducibility_and_corrupt_persistence(tmp_path, monkeypatch):
    a = make_frame(1, 42)
    assert a.shape == (255, 1024) and a.dtype == np.int32
    np.testing.assert_array_equal(a, make_frame(1, 42))
    assert not np.array_equal(a, make_frame(1, 43))
    with pytest.raises(ValueError): extract_profile(a, True, 165)
    with pytest.raises(ValidationError): CaptureRequest(exposure_s=float('inf'))
    monkeypatch.setenv('KERR_STATE_DIR', str(tmp_path))
    (tmp_path / 'installed-calibration.json').write_text('{invalid', encoding='utf-8')
    workbench = CalibrationWorkbench()
    assert workbench.restore({})['wavelengths_nm'] is None and workbench.warning


def test_frontend_dom_and_api_contracts_are_present():
    root = Path(__file__).resolve().parents[1]
    class IdParser(HTMLParser):
        def __init__(self): super().__init__(); self.ids = []
        def handle_starttag(self, tag, attrs):
            self.ids.extend(value for key, value in attrs if key == 'id')
    parser = IdParser(); parser.feed((root/'index.html').read_text(encoding='utf-8'))
    assert len(parser.ids) == len(set(parser.ids)), 'Duplicate DOM identifiers'
    for module in ['app.js', 'analysis.js', 'calibration.js', 'header.js']:
        referenced = set(re.findall(r"\$\('([A-Za-z][A-Za-z0-9]*)'\)", (root/'frontend'/module).read_text(encoding='utf-8')))
        assert referenced <= set(parser.ids), (module, referenced-set(parser.ids))
    routes = set(app_module.app.openapi()['paths'])
    for path in ['/api/v1/calibration/frame', '/api/v1/calibration/frames', '/api/v1/calibration/extract',
                 '/api/v1/calibration/fit', '/api/v1/calibration/apply', '/api/v1/calibration/solutions/{solution_id}',
                 '/api/v1/runs/{run_id}/fits', '/api/v1/tasks/{task_id}']:
        assert path in routes


def test_nondefault_manual_settings_and_axis_restore_together(tmp_path, monkeypatch):
    monkeypatch.setenv('KERR_STATE_DIR', str(tmp_path))
    settings = dict(center_nm=610, grating=600, slit_um=150)
    calibration = dict(calibration_id='recorded', source='measured', wavelengths_nm=np.linspace(520, 720, 1024).tolist())
    CalibrationWorkbench().persist(calibration, settings)
    worker = importlib.import_module('backend.worker')
    engine = worker.Engine(queue.Queue())
    try:
        assert engine.status()['spectrograph'] == settings
        assert engine.status()['calibration'] == calibration
    finally:
        engine.pool.shutdown(wait=True)


def test_fit_completing_during_save_is_persisted(tmp_path, monkeypatch):
    worker = importlib.import_module('backend.worker')
    monkeypatch.setenv('KERR_STATE_DIR', str(tmp_path / 'state'))
    engine = worker.Engine(queue.Queue())
    from backend.models import RunRequest
    run = engine.handle('run', RunRequest(**run_body(tmp_path, autosave=False)).model_dump())
    wait_until(engine.status, lambda s: not s['busy'])
    fit_started = threading.Event(); release_fit = threading.Event()
    save_started = threading.Event(); release_save = threading.Event()
    real_fit, real_writer = worker.fit_records, worker.HDFWriter
    def slow_fit(*args,**kwargs):
        fit_started.set(); assert release_fit.wait(10)
        return real_fit(*args,**kwargs)
    def slow_writer(*args):
        writer = real_writer(*args)
        save_started.set(); assert release_save.wait(10)
        return writer
    monkeypatch.setattr(worker, 'fit_records', slow_fit)
    monkeypatch.setattr(worker, 'HDFWriter', slow_writer)
    task = engine.handle('fit', dict(run_id=run['run_id'], options=dict(model='single')))
    assert fit_started.wait(5)
    saved, failures = [], []
    def save():
        try: saved.append(engine.handle('save', dict(run_id=run['run_id'], path=str(tmp_path/'race.h5'))))
        except Exception as exc: failures.append(exc)
    thread = threading.Thread(target=save)
    thread.start()
    try:
        assert save_started.wait(5)
        release_fit.set()
    finally:
        release_save.set(); thread.join(12)
    result = wait_until(lambda: engine.handle('task', task), lambda t: t['status'] != 'running')
    assert not failures and result['status'] == 'completed', result
    with h5py.File(saved[0]['path'], 'r') as f:
        assert json.loads(f['analysis'][task['task_id']][()]) == result['result']
    engine.pool.shutdown(wait=True)
