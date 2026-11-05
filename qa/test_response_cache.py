"""Incremental correction must match full recomputation and preserve unchanged arrays."""
import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from qt_frontend import correction
from qt_frontend.state import Dataset, SpectrumGroup
from qt_frontend.window import MainWindow


def make_dataset():
    calibration = dict(wavelengths_nm=[500, 525, 550, 575, 600], response_id='same-id',
                       response_wavelengths_nm=[500, 525, 550, 575, 600],
                       response=[.5, .75, 1., .75, None])
    dataset = Dataset(run=dict(metadata=dict(calibration=calibration,
                       background=dict(present=True, spectrum=[10] * 5)),
                       plan=dict(delays_ps=[0., 1., 2.])))
    for delay in range(3):
        group = SpectrumGroup(delay, 5)
        group.add([50, 100, 300, 200, 80], 1.)
        dataset._groups[float(delay)] = group
    return dataset


class ResponseCacheTests(unittest.TestCase):
    def setUp(self):
        self.dataset = make_dataset()
        self.cache = correction.RunSpectrumCache()

    def update(self):
        self.cache.prepare(self.dataset, self.dataset.axis)
        return self.cache.spectra(self.dataset.groups)

    def assert_matches_full(self, spectra):
        response = correction.on(correction.recorded(self.dataset.run), self.dataset.axis)
        background = correction.run_background(self.dataset.run)
        for delay, group in self.dataset.groups.items():
            expected = correction.apply(group.raw, response, background) if response is not None else group.raw
            np.testing.assert_allclose(spectra[delay], expected, rtol=0, atol=0, equal_nan=True)

    def test_only_changed_and_new_delays_are_corrected(self):
        with patch.object(correction, 'apply', wraps=correction.apply) as apply:
            before = self.update()
            self.assertEqual(apply.call_count, 3)
            again = self.update()
            self.assertEqual(apply.call_count, 3)
            self.dataset.groups[1.].add([70, 120, 400, 220, 90], 1.)
            new = SpectrumGroup(3., 5); new.add([20, 50, 80, 90, 60], 1.)
            self.dataset.groups[3.] = new
            after = self.update()
            self.assertEqual(apply.call_count, 5)
        self.assertIs(before[0.], again[0.])
        self.assertIs(before[0.], after[0.])
        self.assertIsNot(before[1.], after[1.])
        self.assert_matches_full(after)

    def test_repeated_status_metadata_does_not_invalidate(self):
        before = self.update(); version = self.cache.version
        self.dataset.run = copy.deepcopy(self.dataset.run)
        after = self.update()
        self.assertEqual(self.cache.version, version)
        self.assertIs(before[0.], after[0.])

    def test_response_axis_and_background_changes_invalidate(self):
        metadata = self.dataset.run['metadata']
        changes = [lambda: metadata['calibration']['response'].__setitem__(0, .25),
                   lambda: metadata['calibration']['response_wavelengths_nm'].__setitem__(0, 490),
                   lambda: metadata['calibration']['wavelengths_nm'].__setitem__(1, 530),
                   lambda: metadata['background']['spectrum'].__setitem__(1, 30)]
        before = self.update()
        for change in changes:
            with self.subTest(change=change):
                version = self.cache.version; change(); after = self.update()
                self.assertGreater(self.cache.version, version)
                self.assertIsNot(before[0.], after[0.])
                self.assert_matches_full(after)
                before = after

    def test_no_background_and_mismatched_background_match_full(self):
        for background in ({}, dict(present=True, spectrum=[1, 2])):
            self.dataset.run['metadata']['background'] = background
            self.assert_matches_full(self.update())

    def test_clear_reapply_and_out_of_range_response(self):
        metadata = self.dataset.run['metadata']; saved = copy.deepcopy(metadata['calibration'])
        raw_before = {d: g.raw_sum.copy() for d, g in self.dataset.groups.items()}
        self.update()
        metadata['calibration']['response'] = None
        self.assert_matches_full(self.update())
        self.assertIsNone(self.cache.response)
        metadata['calibration'] = saved
        self.assert_matches_full(self.update())
        metadata['calibration']['response_wavelengths_nm'] = [900, 925, 950, 975, 1000]
        self.assert_matches_full(self.update())
        self.assertIsNone(self.cache.response)
        for delay, values in raw_before.items():
            np.testing.assert_array_equal(self.dataset.groups[delay].raw_sum, values)

    def test_switch_run_replace_group_and_remove_delays(self):
        before = self.update()
        self.dataset = make_dataset()
        after = self.update()
        self.assertIsNot(before[0.], after[0.])
        replacement = SpectrumGroup(0., 5); replacement.add([90] * 5, 1.)
        self.dataset.groups[0.] = replacement
        del self.dataset.groups[2.]
        after = self.update()
        self.assertNotIn(2., after)
        self.assertNotIn(2., self.cache._entries)
        self.assert_matches_full(after)
        self.dataset.groups.clear()
        self.assertEqual(self.update(), {})

    def test_cached_decay_matches_full_for_multiple_wavelengths(self):
        view = SimpleNamespace(_run_response_on_axis=lambda dataset:
            correction.on(correction.recorded(dataset.run), dataset.axis),
            _decay_window=lambda dataset, wave: [i for i, x in enumerate(dataset.axis) if abs(x-wave) <= 25])
        spectra = self.update()
        for wave in (525, 550):
            expected = MainWindow._raw_decay_points(view, self.dataset, wave)
            with patch.object(correction, 'apply', wraps=correction.apply) as apply:
                actual = MainWindow._raw_decay_points(view, self.dataset, wave, spectra=spectra)
                apply.assert_not_called()
            np.testing.assert_allclose(actual, expected, rtol=0, atol=0)

    def test_map_redraw_shares_cache_with_pins_and_detects_metadata_change(self):
        noop = lambda *args, **kwargs: None
        view = SimpleNamespace(_current_page='acquisition', controller=SimpleNamespace(acquisition=self.dataset),
            map_scale=SimpleNamespace(current=lambda: 'linear'), _decay_pins=[dict(key='pin', wave=550., label='550', color='data')],
            decay_normalize=SimpleNamespace(isChecked=lambda: False, setToolTip=noop),
            _map_dirty=True, _map_key=None, _map_levels=None, _selected_delay=None,
            map_plot=Mock(), map_saturated_key=Mock(), map_color_key=Mock(), color_min=Mock(), color_max=Mock(),
            decay_plot=Mock(), _apply_visual_geometry=noop, _decay_baseline=lambda *args: (0, 'test'),
            _sync_pins=noop, _update_decay_legend=noop, _picked_spectrum_active=lambda: False,
            _decay_window=lambda dataset, wave: [1, 2, 3])
        view.map_plot.set_map.return_value = (0, 1)
        view._raw_decay_points = lambda *args, **kwargs: MainWindow._raw_decay_points(view, *args, **kwargs)
        with patch.object(correction, 'apply', wraps=correction.apply) as apply:
            MainWindow._flush_map(view)
            self.assertEqual(apply.call_count, 3)  # Map, decay and pin share three corrected spectra.
            view.map_scale.current = lambda: 'log'
            MainWindow._flush_map(view)
            self.assertEqual(apply.call_count, 3)
            self.dataset.groups[1.].add([60] * 5, 1.); self.dataset._revision += 1
            MainWindow._flush_map(view)
            self.assertEqual(apply.call_count, 4)
            self.dataset.run['metadata']['background']['spectrum'][0] = 20
            MainWindow._flush_map(view)
            self.assertEqual(apply.call_count, 7)
            MainWindow._flush_map(view)
            self.assertEqual(apply.call_count, 7)
            self.assertEqual(view.map_plot.set_map.call_count, 4)


if __name__ == '__main__':
    unittest.main()
