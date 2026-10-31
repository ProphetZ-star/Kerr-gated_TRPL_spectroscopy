"""Focused desktop/template/output checks, without opening a window."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import re
import sys

import pytest
import desktop
from test_reported_bugs import factory,body,wait

ROOT=Path(__file__).resolve().parents[1]


def test_startup_uses_the_real_disabled_workbench_and_inlines_assets():
    page=desktop._loading_page()
    for marker in ['Live Spectrum','TRPL Map & Decay','File name','startup-spinner','id="appShell" inert','is-starting']:
        assert marker in page
    assert '<script' not in page and 'href="/frontend/' not in page
    assert '@keyframes startup-spin' in page
    assert '<style>' in page


@pytest.mark.parametrize('result',[None,('D:/TRPL measurements',)])
def test_native_folder_dialog_result_or_cancel(monkeypatch,result):
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20)))
    bridge=desktop.DesktopBridge();bridge._window=Mock()
    bridge._window.create_file_dialog.return_value=result
    assert bridge.select_output_folder('data')==(result[0] if result else None)
    bridge._window.create_file_dialog.assert_called_once_with(20,directory=str(ROOT/'data'),allow_multiple=False)


def test_compact_calibration_structure_keeps_metrics_in_upper_chart():
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    assert 'CCD frame / pixel' in html and 'Extracted spectrum / ' in html
    assert 'Select a row below, then click its peak.' not in html
    assert 'Mean extraction · 0 saturated pixels' not in html
    mapping=html.index('class="cal-canvas cal-mapping-plot"')
    assert mapping<html.index('id="calRms"')<html.index('id="calMapping"')<html.index('id="calResidual"')
    assert '<label for="savePath" class="save-label">File name</label>' in html


def test_output_folder_and_user_filename_reach_hdf5(factory,tmp_path):
    engine=factory();target=tmp_path/'measurements'/'Sample 01 TRPL.h5'
    run=engine.handle('run',body(mode='single',autosave=True,save_path=str(target)))
    result=wait(engine.status)['run']
    assert result['run_id']==run['run_id'] and result['saved']
    assert Path(result['path'])==target and target.is_file()
    assert result['request']['save_path']==str(target)


def test_hardware_selection_cannot_silently_become_simulation(factory):
    engine=factory()
    with pytest.raises(ValueError,match='not commissioned'):
        engine.handle('connect',dict(mode='hardware'))
    assert engine.status()['source_mode']=='simulation'
