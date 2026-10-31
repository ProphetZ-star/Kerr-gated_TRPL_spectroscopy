"""Only regressions for confirmed replacement, memory scans, and HDF5 picking."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest
import desktop
import backend.worker as worker
from backend.errors import CommandError
from backend.storage.hdf5 import read_run
from backend.storage.output import output_info
from test_reported_bugs import factory,client,body,wait,app_module


def memory(engine):
    run=engine.handle('run',body());wait(engine.status)
    return run['run_id']


def test_new_scan_replaces_memory_only_after_it_is_accepted(factory):
    engine=factory();old=memory(engine)
    new=engine.handle('run',body(seed=81));wait(engine.status)
    assert new['run_id']!=old and old not in engine.runs
    assert engine.runs[new['run_id']]['records']


def test_output_preflight_does_not_create_a_folder_or_change_memory(factory,tmp_path):
    engine=factory();old=memory(engine);folder=tmp_path/'new folder'
    result=engine.handle('output_check',dict(path=str(folder/'sample.h5')))
    assert not result['conflicts'] and not folder.exists() and engine.current==old


def test_conflict_and_changed_confirmation_preserve_disk_and_memory(factory,tmp_path):
    engine=factory();old=memory(engine);path=tmp_path/'sample.h5';path.write_bytes(b'old')
    token=output_info(path)['overwrite_token']
    with pytest.raises(CommandError,match='confirm overwrite'):
        engine.handle('run',body(autosave=True,save_path=str(path)))
    path.write_bytes(b'changed since confirmation')
    with pytest.raises(CommandError,match='confirm overwrite'):
        engine.handle('run',body(autosave=True,save_path=str(path),overwrite_token=token))
    assert engine.current==old and path.read_bytes()==b'changed since confirmation'
    assert not list(tmp_path.glob('.*overwrite-backup-*'))


def test_confirmed_replacement_creates_new_hdf5_and_keeps_original_backup(factory,tmp_path):
    engine=factory();old=memory(engine);path=tmp_path/'sample.h5'
    original={path:b'old HDF',path.with_suffix('.events.jsonl'):b'old events',path.with_suffix('.stopped.h5'):b'old stopped'}
    for file,content in original.items():file.write_bytes(content)
    info=output_info(path)
    engine.handle('run',body(autosave=True,save_path=str(path),overwrite_token=info['overwrite_token']))
    run=wait(engine.status)['run'];assert run['saved'] and run['run_id']!=old
    assert read_run(path)[1]
    backup=next(tmp_path.glob('.sample.overwrite-backup-*'))
    for file,content in original.items():assert (backup/file.name).read_bytes()==content


def test_writer_initialization_failure_restores_original_files_and_memory(factory,tmp_path,monkeypatch):
    engine=factory();old=memory(engine);path=tmp_path/'sample.h5';path.write_bytes(b'original')
    token=output_info(path)['overwrite_token']
    monkeypatch.setattr(worker,'HDFWriter',Mock(side_effect=OSError('cannot create new output')))
    with pytest.raises(OSError,match='cannot create'):
        engine.handle('run',body(autosave=True,save_path=str(path),overwrite_token=token))
    assert path.read_bytes()==b'original' and engine.current==old
    assert not list(tmp_path.glob('.*overwrite-backup-*'))


def test_save_memory_uses_the_same_confirmation_rules(factory,tmp_path):
    engine=factory();id=memory(engine);path=tmp_path/'save.h5';path.write_bytes(b'previous')
    with pytest.raises(CommandError):engine.handle('save',dict(run_id=id,path=str(path)))
    info=output_info(path)
    result=engine.handle('save',dict(run_id=id,path=str(path),overwrite_token=info['overwrite_token']))
    assert result['saved'] and read_run(path)[1]


def test_go_to_zero_moves_without_redefining_the_zero(factory):
    engine=factory();engine.stage.position_mm=152.4;engine.handle('zero',{})
    zero,version=engine.zero,engine.zero_version;engine.stage.position_mm=153.7
    engine.handle('move',dict(delay_ps=0,relative=False));wait(engine.status)
    assert engine.stage.position_mm==zero and engine.zero==zero and engine.zero_version==version


@pytest.mark.parametrize('result',[None,('D:/Experiments/sample.h5',)])
def test_native_hdf5_picker_and_cancel(monkeypatch,result):
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(FileDialog=SimpleNamespace(OPEN=10)))
    bridge=desktop.DesktopBridge();bridge._window=Mock();bridge._window.create_file_dialog.return_value=result
    assert bridge.select_hdf5_file('data')==(result[0] if result else None)
    bridge._window.create_file_dialog.assert_called_once_with(10,directory=str(desktop.ROOT/'data'),allow_multiple=False,file_types=('TRPL HDF5 (*.h5)',))


def test_http_preflight_confirm_and_browser_import(client,tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'ROOT',tmp_path)
    path=tmp_path/'sample.h5';path.write_bytes(b'previous')
    info=client.post('/api/v1/output/check',json=dict(path=str(path))).json()
    request=body(autosave=True,save_path=str(path))
    assert client.post('/api/v1/runs',json=request).status_code==409
    result=client.post('/api/v1/runs',json=request|dict(overwrite_token=info['overwrite_token']))
    assert result.status_code==200,result.text
    wait(lambda:client.get('/api/v1/status').json())
    source=path.read_bytes()
    imported=client.post('/api/v1/runs/import',content=source,headers={'Content-Type':'application/x-hdf5','X-File-Name':'sample.h5'})
    assert imported.status_code==200,imported.text
    imported_path=Path(imported.json()['path'])
    assert imported_path!=path and imported_path.read_bytes()==source and path.read_bytes()==source
    before=client.get('/api/v1/status').json()['run']['run_id']
    invalid=client.post('/api/v1/runs/import',content=b'invalid HDF',headers={'X-File-Name':'invalid.h5'})
    assert invalid.status_code==422 and client.get('/api/v1/status').json()['run']['run_id']==before
    assert len(list((tmp_path/'data'/'imports').glob('*/*.h5')))==1
