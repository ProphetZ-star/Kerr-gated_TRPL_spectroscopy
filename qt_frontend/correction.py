'''The intensity response applied to what the window shows: (raw - background) / R(lambda).

The live display uses the response installed now (snapshot['response']); a run's map, decays and Analysis use
the response recorded with that run (its /calibration), so an old experiment is shown as it was measured.'''
import numpy as np
from backend.response import on_axis, correct

LABEL = 'response-corrected'


def _curve(grid, values):
    if not grid or not values:
        return None
    return np.asarray(grid, dtype=float), np.asarray([np.nan if v is None else v for v in values], dtype=float)


def installed(snapshot):
    '''(grid, values) of the installed response, or None.'''
    response = (snapshot or {}).get('response') or {}
    return _curve(response.get('wavelengths_nm'), response.get('values'))


def recorded(run):
    '''(grid, values) of the response recorded with a run, or None.'''
    calibration = (((run or {}).get('metadata') or {}).get('calibration')) or {}
    return _curve(calibration.get('response_wavelengths_nm'), calibration.get('response'))


def on(curve, axis):
    '''The response at the axis wavelengths (NaN where undefined), or None when there is none to apply.'''
    if curve is None or axis is None or not len(axis):
        return None
    values = on_axis(curve[0], curve[1], np.asarray(axis, dtype=float))
    return values if np.isfinite(values).any() else None


def run_background(run):
    background = (((run or {}).get('metadata') or {}).get('background')) or {}
    spectrum = background.get('spectrum') if background.get('present') or background.get('spectrum') else None
    return np.asarray(spectrum, dtype=float) if spectrum else None


def apply(spectrum, response, background=None):
    '''Corrected spectrum; the background is used when it matches the spectrum, else its baseline offset.'''
    spectrum = np.asarray(spectrum, dtype=float)
    response = np.asarray(response, dtype=float)[:spectrum.shape[-1]]
    if background is not None and len(background) != spectrum.shape[-1]:
        background = None
    return correct(spectrum, response, background)
