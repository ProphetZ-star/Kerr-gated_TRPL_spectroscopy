from types import SimpleNamespace
from unittest.mock import Mock
import sys
import pytest
import desktop
from desktop_service import LocalService


def test_reuses_external_service_without_shutdown(monkeypatch):
    service=LocalService()
    monkeypatch.setattr(service,'existing',lambda:True)
    with service as result:
        assert result is service and not result.owned
        assert service.confirm_close(Mock())
    assert not service.owned


def test_cancel_close_preserves_memory(monkeypatch):
    service=LocalService();service.process=Mock()
    request=Mock(return_value={'busy':None,'run':{'committed_frames':5,'saved':False}})
    monkeypatch.setattr(service,'request',request)
    window=Mock();window.create_confirmation_dialog.return_value=False
    assert service.confirm_close(window) is False
    assert request.call_count==1


def test_close_stops_scan_before_exit(monkeypatch):
    service=LocalService();service.process=Mock()
    calls=[]
    def request(path,body=None,**kwargs):
        calls.append((path,body))
        if len(calls)==1:return dict(busy='scan',run=dict(run_id='test',committed_frames=3,saved=False))
        return dict(busy=None)
    monkeypatch.setattr(service,'request',request)
    window=Mock();window.create_confirmation_dialog.return_value=True
    assert service.confirm_close(window) is True
    assert ('/api/v1/runs/test/stop',{}) in calls


def test_own_process_shutdown_file(tmp_path):
    service=LocalService();process=Mock();process.poll.return_value=None
    service.process=process;service.stop_file=tmp_path/'stop';service.log=Mock()
    def wait(timeout):
        assert service.stop_file.exists()
        process.poll.return_value=0
        return 0
    process.wait.side_effect=wait
    service.close()
    process.terminate.assert_not_called()
    assert not service.stop_file.exists()


def test_native_window_has_no_browser_chrome(monkeypatch):
    class Closing:
        handler=None
        def __iadd__(self,callback):self.handler=callback;return self
    window=SimpleNamespace(events=SimpleNamespace(closing=Closing()))
    fake=SimpleNamespace(create_window=Mock(return_value=window),start=Mock(),settings={})
    monkeypatch.setitem(sys.modules,'webview',fake)
    assert desktop.create_desktop('http://127.0.0.1:8765',lambda window:False)==0
    assert window.events.closing.handler() is False
    assert fake.create_window.call_args.kwargs['frameless'] is False
    assert fake.start.call_args.kwargs['gui']=='edgechromium'
    assert fake.start.call_args.kwargs['private_mode'] is True
    assert fake.settings['ALLOW_DOWNLOADS'] is True


def test_invalid_port():
    with pytest.raises(ValueError):LocalService(0)
