"""Headless startup, lazy-page and idle-refresh regressions; no real devices or backend process."""
import copy
import json
import os
from pathlib import Path
from queue import Queue
import sys
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import runtime_probe
from backend.worker import Engine, shutdown_engine
from qt_frontend.bridge import BackendBridge
from qt_frontend.controller import Controller
from qt_frontend.window import MainWindow
from qt_frontend.theme import current_mode
from qt_frontend.state import Dataset


class NoDeviceBridge(BackendBridge):
    def __init__(self):
        super().__init__(); self.sent=[]
    def start(self,*args,**kwargs):pass
    def submit(self,name,data=None,token=None):
        self.sent.append((name,data));return token or str(len(self.sent))


class LazyPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.errors=[]
        hook=patch.object(sys,'excepthook',lambda kind,value,tb:self.errors.append(value));hook.start();self.addCleanup(hook.stop)
        self.bridge=NoDeviceBridge();self.controller=Controller(bridge=self.bridge)
        self.window=MainWindow(controller=self.controller,auto_start=False)
        self.window._auto_connect_started=True
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,KERR_STATE_DIR=self.temp.name);self.env.start();self.addCleanup(self.env.stop)
        self.engine=Engine(Queue());self.addCleanup(lambda:shutdown_engine(self.engine))
        self.engine.handle('connect',dict(mode='simulation'))
        self.snapshot=self.engine.status()
        self.snapshot.update(latest=dict(spectrum=[100,200,600,300,100],source_mode='simulation',saturated_pixels=0),
                             live_axis=[500,525,550,575,600])
        self.controller._on_snapshot(self.snapshot)

    def tearDown(self):
        self.window._allow_close=True
        for timer in (self.window._map_timer,self.window._label_timer,self.window._cal_timer):timer.stop()
        self.window.close();self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.assertEqual(self.errors,[],f'Qt slot errors: {self.errors}')

    def test_initial_page_only_and_first_switch_hydrates_current_state(self):
        w=self.window
        self.assertEqual(w.stack.count(),1)
        self.assertFalse(hasattr(w,'analysis_curve'))
        self.assertFalse(hasattr(w,'cal_image'))
        w._on_data();w._on_analysis();w._toggle_theme()
        self.assertEqual(w.stack.count(),1)
        for page,attribute in [('analysis','analysis_curve'),('calibration','cal_image')]:
            w.show_page(page)
            self.assertTrue(hasattr(w,attribute))
            first=w._pages[page]
            w.show_page('acquisition');w.show_page(page)
            self.assertIs(w._pages[page],first)
        self.assertEqual(w.stack.count(),3)
        self.assertEqual(current_mode(),'dark')
        self.assertEqual(w.cal_source.text(),'Simulation')
        self.assertTrue(w.cal_capture.isEnabled())

    def test_lazy_calibration_still_freezes_during_scan(self):
        snapshot=copy.deepcopy(self.snapshot);snapshot['busy']='scan'
        self.controller._on_snapshot(snapshot)
        self.window.show_page('calibration')
        self.assertFalse(self.window.cal_capture.isEnabled())
        self.assertFalse(self.window.cal_readout.isEnabled())
        self.controller._on_snapshot(self.snapshot)
        self.assertTrue(self.window.cal_capture.isEnabled())

    def test_data_received_before_analysis_creation_and_independent_import(self):
        run=dict(run_id='current',metadata=dict(calibration=dict(wavelengths_nm=[500,525,550,575,600])))
        records=[dict(delay_ps=delay,exposure_s=1.,spectrum=[100,200,600,300,100]) for delay in (0.,1.)]
        acquired=Dataset(run=run,records=records,wave=550)
        self.controller.acquisition=acquired
        self.window._on_data()
        self.assertFalse(hasattr(self.window,'analysis_curve'))
        self.window.show_page('analysis')
        self.assertEqual(self.window.analysis_points.text(),'2 delays')
        imported=Dataset(run=dict(run,path='imported.h5'),records=records[:1],wave=525)
        self.controller.analysis_imported=imported
        self.controller.analysisChanged.emit()
        self.assertEqual(self.window.analysis_points.text(),'1 delays')
        self.assertEqual(self.window.analysis_source.text(),'Imported · imported.h5')
        self.assertIs(self.controller.acquisition,acquired)
        self.window.show_page('acquisition');self.window._flush_map()
        self.assertEqual(self.controller.acquisition.delays,[0.,1.])

    def test_static_live_data_does_not_redraw_but_changes_do(self):
        w=self.window
        with patch.object(w,'_update_live_spectrum',wraps=w._update_live_spectrum) as draw:
            w._flush_labels();w._flush_labels()
            self.assertEqual(draw.call_count,1)
            for _ in range(3):
                self.controller._on_snapshot(copy.deepcopy(self.snapshot));w._flush_labels()
            self.assertEqual(draw.call_count,1,'Identical Qt snapshot copies must reuse the plot')
            changed=copy.deepcopy(self.snapshot);changed['latest']['spectrum'][2]=700
            self.controller._on_snapshot(changed);w._flush_labels()
            self.assertEqual(draw.call_count,2)
            changed['response']=dict(response_id='test',wavelengths_nm=changed['live_axis'],values=[.5,.75,1,.75,.5])
            self.controller._on_snapshot(changed);w._flush_labels()
            self.assertTrue(w._live_corrected)
            self.assertTrue(w.spectrum_legend.entries['background'].isHidden())
            changed['response']={};self.controller._on_snapshot(changed);w._flush_labels()
            self.assertFalse(w._live_corrected)
            self.assertFalse(w.spectrum_legend.entries['background'].isHidden())
            self.assertEqual(draw.call_count,4)
        self.assertTrue(w._map_timer.isSingleShot())
        self.assertTrue(w._label_timer.isSingleShot())

    def test_identical_counts_with_new_frame_version_refresh_noise(self):
        w=self.window
        snapshot=copy.deepcopy(self.snapshot);snapshot['_sync']={'latest_version':1}
        self.controller._on_snapshot(snapshot);w._flush_labels()
        with patch.object(w,'_update_live_spectrum',wraps=w._update_live_spectrum) as draw:
            snapshot['_sync']['latest_version']=2
            self.controller._on_snapshot(snapshot);w._flush_labels()
            self.assertEqual(draw.call_count,1)

    def test_minimize_suspends_draw_and_restore_schedules_latest(self):
        w=self.window;w._flush_labels()
        w.setWindowState(Qt.WindowMinimized)
        self.assertTrue(self.bridge._background)
        self.assertFalse(w._map_timer.isActive())
        self.assertFalse(w._label_timer.isActive())
        with patch.object(w,'_update_live_spectrum',wraps=w._update_live_spectrum) as draw:
            changed=copy.deepcopy(self.snapshot);changed['latest']['spectrum'][2]=900
            self.controller._on_snapshot(changed);w._on_data();w._flush_labels();w._flush_map()
            draw.assert_not_called()
            w.setWindowState(Qt.WindowNoState)
            self.assertFalse(self.bridge._background)
            self.assertTrue(w._label_timer.isActive())
            w._flush_labels();self.assertEqual(draw.call_count,1)

    def test_unchanged_idle_timers_stop_after_flush(self):
        w=self.window
        with patch.object(w,'_update_live_spectrum',wraps=w._update_live_spectrum) as draw:
            w._schedule_refresh()
            deadline=time.monotonic()+.4
            while time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.005)
            self.assertEqual(draw.call_count,1)
            self.assertFalse(w._map_timer.isActive())
            self.assertFalse(w._label_timer.isActive())


class PollIntervalTests(unittest.TestCase):
    def test_intervals_and_queue_wakeup(self):
        bridge=BackendBridge()
        self.assertEqual(bridge._poll_interval(),.15)
        bridge._ready=True;bridge._snapshot={'busy':None}
        self.assertEqual(bridge._poll_interval(),1.)
        bridge.set_background(True);self.assertFalse(bridge._requests.empty())
        self.assertEqual(bridge._poll_interval(),3.)
        bridge._snapshot['busy']='scan';self.assertEqual(bridge._poll_interval(),.5)
        bridge.set_background(False);self.assertEqual(bridge._poll_interval(),.15)

    def test_background_wait_is_interrupted_by_commands_and_restore(self):
        polled=Event();command=Event();closed=Event()
        class FakeManager:
            def call(self,name,data):
                if name=='status':
                    polled.set();return dict(busy=None)
                command.set();return {}
            def close(self):closed.set()
        bridge=BackendBridge();bridge._background=True
        with patch.object(bridge,'_create_manager',return_value=FakeManager()):
            try:
                bridge.submit('status',{})
                self.assertTrue(polled.wait(2.))
                time.sleep(.05)  # Allow the immediate post-command poll to complete.
                polled.clear()
                bridge.submit('preview_stop',{})
                self.assertTrue(command.wait(1.),'Command waited for the 3-second idle poll')
                self.assertTrue(polled.wait(1.))
                time.sleep(.05);polled.clear()
                bridge.set_background(False)
                self.assertTrue(polled.wait(1.),'Restore did not request a fresh snapshot')
            finally:
                bridge.shutdown();self.assertTrue(bridge.wait(2000))
        self.assertTrue(closed.is_set())


class QuickProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);(root/'.venv').mkdir()
        self.req=root/'requirements.txt';self.req.write_text('fake==1\n')
        self.dependency=root/'module.py';self.dependency.write_text('verified code\n')
        self.key=dict(python='test',versions={'fake':'1'})
        self.receipt,self.failed=runtime_probe._receipt_paths(self.req)
        self.write_receipt()

    def write_receipt(self):
        stat=self.dependency.stat()
        self.receipt.write_text(json.dumps(dict(key=self.key,files=[[str(self.dependency),stat.st_size,stat.st_mtime_ns]])))

    def test_valid_receipt_uses_no_import_check(self):
        with patch.object(runtime_probe,'_verification_key',return_value=self.key), \
             patch.object(runtime_probe,'check_imports') as imports, \
             patch.object(sys,'argv',['probe','--quick','--requirements',str(self.req)]):
            self.assertEqual(runtime_probe.main(),0);imports.assert_not_called()

    def test_changed_deleted_or_failed_dependency_invalidates(self):
        with patch.object(runtime_probe,'_verification_key',return_value=self.key):
            self.assertTrue(runtime_probe.quick_verified(self.req))
            self.failed.write_text('failed');self.assertFalse(runtime_probe.quick_verified(self.req));self.failed.unlink()
            self.dependency.write_text('modified module contents');self.assertFalse(runtime_probe.quick_verified(self.req))
            self.write_receipt();self.dependency.unlink();self.assertFalse(runtime_probe.quick_verified(self.req))

    def test_cache_miss_falls_back_and_failure_is_reported(self):
        self.receipt.unlink()
        with patch.object(runtime_probe,'_verification_key',return_value=self.key), \
             patch.object(runtime_probe,'check_imports',return_value=3) as imports, \
             patch.object(runtime_probe,'save_verification') as save, \
             patch.object(sys,'argv',['probe','--quick','--requirements',str(self.req)]):
            self.assertEqual(runtime_probe.main(),3);imports.assert_called_once();save.assert_not_called()

    def test_full_flag_forces_imports_despite_valid_cache(self):
        with patch.object(runtime_probe,'_verification_key',return_value=self.key), \
             patch.object(runtime_probe,'check_imports',return_value=0) as imports, \
             patch.object(runtime_probe,'save_verification') as save, \
             patch.object(sys,'argv',['probe','--quick','--full','--requirements',str(self.req)]):
            self.assertEqual(runtime_probe.main(),0);imports.assert_called_once();save.assert_called_once()

    def test_environment_change_and_corrupt_receipt_invalidate(self):
        with patch.object(runtime_probe,'_verification_key',return_value=dict(python='changed')):
            self.assertFalse(runtime_probe.quick_verified(self.req))
        self.receipt.write_text('not json')
        self.assertFalse(runtime_probe.quick_verified(self.req))


if __name__=='__main__':unittest.main()
