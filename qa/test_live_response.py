"""Headless regressions for clearing the live intensity response.

Run: python -m unittest discover -s qa -p test_live_response.py
"""
import copy
import os
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from PySide6.QtWidgets import QApplication, QLabel

from backend.worker import Engine
from qt_frontend.bridge import BackendBridge
from qt_frontend.plots import SpectrumPlot
from qt_frontend.state import Dataset
from qt_frontend.window import MainWindow


class LiveResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.env = patch.dict(os.environ, KERR_STATE_DIR=self.temp.name)
        self.env.start()
        self.engine = Engine(Queue())
        self.raw = [100, 200, 600, 300, 100]
        self.axis = [500, 525, 550, 575, 600]
        self.engine.latest = dict(spectrum=self.raw.copy(), source_mode='hardware', saturated_pixels=0)
        self.engine.latest_axis = self.axis
        self.engine.latest_version = 1
        self.response = dict(response_id='test-response', source='imported', reference='test',
                             wavelengths_nm=self.axis, values=[.5, .75, 1, .75, .5])
        self.engine.set_response(self.response)
        self.bridge = SimpleNamespace(_snapshot={}, _since=None, _latest_version=0)
        self.plot = SpectrumPlot()
        self.scale = 'linear'
        self.view = SimpleNamespace(
            controller=SimpleNamespace(acquisition=Dataset()), _snapshot={},
            _picked_spectrum_active=lambda: False,
            _spectrum_legend_current=QLabel(), spectrum_plot=self.plot,
            spectrum_scale=SimpleNamespace(current=lambda: self.scale),
            spectrum_legend=SimpleNamespace(set_hint=lambda *args: None),
            saturation=QLabel(), _sync_pins=lambda: None,
            _update_live_noise=lambda *args: None,
        )

    def tearDown(self):
        self.plot.close()
        self.engine.pool.shutdown(wait=True)
        self.env.stop()
        self.temp.cleanup()

    def update(self):
        snapshot = self.engine.compact_status(self.bridge._since, self.bridge._latest_version)
        BackendBridge._merge(self.bridge, snapshot)
        snapshot = copy.deepcopy(self.bridge._snapshot)
        # A multi-row ROI's full-scale sum must not flatten a weak raw spectrum.
        snapshot['camera']['full_scale'] = 65535 * 255
        self.view._snapshot = snapshot
        MainWindow._update_live_spectrum(self.view)

    def clear(self):
        self.engine.handle('response_clear', {})
        self.update()

    def test_clear_restores_raw_counts_and_background_without_new_frame(self):
        self.engine.background = dict(spectrum=[25] * 5)
        self.update()
        np.testing.assert_allclose(self.plot._readout_curve[1],
                                   (np.array(self.raw) - 25) / np.array(self.response['values']))
        self.clear()
        np.testing.assert_array_equal(self.plot._readout_curve[1], self.raw)
        self.assertFalse(self.view._live_corrected)
        self.assertEqual(self.plot.left_axis.labelText, 'CCD / counts')
        self.assertEqual(self.engine.latest['spectrum'], self.raw)
        self.assertTrue(any(getattr(item, '_series_keys', ()) == ('background',)
                            for item in self.plot._items))
        self.assertIsNone(self.engine.calibration_workbench.restore_response(self.engine.spectrograph))

    def test_raw_signal_remains_visible_after_clear(self):
        self.update()
        self.clear()
        low, high = self.plot.viewRange()[1]
        self.assertGreater((max(self.raw) - min(self.raw)) / (high - low), .1)

    def test_clear_releases_corrected_y_zoom_but_keeps_x_zoom(self):
        self.update()
        self.plot.set_axis_bounds('x', 520, 580, manual=True)
        self.plot.set_axis_bounds('y', 10000, 20000, manual=True)
        self.clear()
        self.assertNotIn('y', self.plot._manual_ranges)
        self.assertEqual(self.plot._manual_ranges['x'][:2], (520, 580))
        low, high = self.plot.viewRange()[1]
        self.assertLessEqual(low, min(self.raw))
        self.assertGreaterEqual(high, max(self.raw))

    def test_log_clear_and_reapply_do_not_mutate_raw_frame(self):
        self.scale = 'log'
        self.update()
        self.clear()
        np.testing.assert_array_equal(self.plot._readout_curve[1], self.raw)
        self.engine.set_response(self.response)
        self.update()
        self.assertTrue(self.view._live_corrected)
        self.assertEqual(self.engine.latest['spectrum'], self.raw)

    def test_normal_refresh_preserves_manual_raw_y_range(self):
        self.update()
        self.clear()
        self.plot.set_axis_bounds('y', 0, 1000, manual=True)
        self.update()
        self.assertEqual(self.plot._manual_ranges['y'][:2], (0, 1000))

    def test_clearing_installed_response_keeps_picked_run_correction(self):
        dataset = self.view.controller.acquisition
        dataset.run = dict(metadata=dict(calibration=dict(wavelengths_nm=self.axis,
            response_id='recorded', response_wavelengths_nm=self.axis, response=self.response['values'])))
        dataset._groups[0.] = SimpleNamespace(raw=np.array(self.raw), saturated=np.zeros(5, bool), count=1)
        self.view._picked_spectrum_active = lambda: True
        self.view._picked_delay = 0.
        self.update()
        before = self.plot._readout_curve[1].copy()
        self.clear()
        np.testing.assert_array_equal(self.plot._readout_curve[1], before)
        self.assertTrue(self.view._live_corrected)


if __name__ == '__main__':
    unittest.main()
