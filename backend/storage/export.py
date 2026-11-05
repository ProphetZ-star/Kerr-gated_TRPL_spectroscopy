import csv
import io
import math
from numbers import Real
import numpy as np


def finite_number(value, name, positive=False):
    if isinstance(value,bool) or not isinstance(value,Real):raise ValueError(f'{name} must be a number')
    try:valid=math.isfinite(value)
    except (TypeError,OverflowError):valid=False
    if not valid or (positive and value<=0):raise ValueError(f'{name} must be finite'+(' and > 0' if positive else ''))
    return float(value)


def export_csv(
    frames,
    wavelengths_nm,
    center_nm,
    half_window_nm,
    source_mode,
    background=None,
    full_scale=None,
    targets_mm=None,
) -> str:
    """Export the frames of a FrameTable to CSV: per frame, the mean count of the window's pixels.

    The raw mean comes first; when the run has a recorded background, background_counts_per_pixel and
    net_counts_per_pixel (raw minus background) follow, empty otherwise. The remaining columns carry what
    Analysis can exclude a frame for: saturated pixels in the window, the CCD temperature and its state, and
    the stage position against the plan.
    """
    from .schema import TEMPERATURE_STATES
    if wavelengths_nm is None:
        raise ValueError("wavelength calibration is required for export")

    wavelengths = list(wavelengths_nm)
    if not wavelengths:
        raise ValueError("wavelength calibration is empty")
    wavelengths=[finite_number(w,'wavelengths_nm',True) for w in wavelengths]

    half_window_nm=finite_number(half_window_nm,'half_window_nm',True)
    center_nm=finite_number(center_nm,'center_nm',True)
    if not isinstance(source_mode,str) or not source_mode or any(ord(c)<32 or ord(c)==127 for c in source_mode):
        raise ValueError('source_mode must be a nonempty single-line label')

    if not min(wavelengths) <= center_nm <= max(wavelengths):
        raise ValueError("center_nm is outside the calibrated range")

    lo = center_nm - half_window_nm
    hi = center_nm + half_window_nm

    included = [i for i, w in enumerate(wavelengths) if lo <= w <= hi]
    if not included:
        raise ValueError(
            "no pixels fall within the requested wavelength window"
        )

    n_pixels = len(wavelengths)
    if background is not None and len(background) != n_pixels:
        raise ValueError("the recorded background does not match the wavelength axis")

    buffer = io.StringIO()
    buffer.write(f"# source_mode: {source_mode}\n")
    buffer.write("# window_definition: mean of included pixels\n")
    buffer.write("# processing: raw counts; net = raw minus the recorded background"
                 + ("" if background is not None else " (no background recorded: columns empty)") + "\n")
    if full_scale is not None:
        buffer.write(f"# full_scale_counts: {float(full_scale):g}\n")

    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "record_id",
            "round_index",
            "point_index",
            "frame_index",
            "delay_ps",
            "wavelength_nm",
            "half_window_nm",
            "mean_counts_per_pixel",
            "exposure_s",
            "background_counts_per_pixel",
            "net_counts_per_pixel",
            "saturated_pixels_in_window",
            "temperature_c",
            "temperature_state",
            "stage_reported_mm",
            "position_error_um",
            "t_start_s",
            "t_end_s",
        ]
    )

    exposure_s = finite_number(frames.exposure_s, "exposure_s", True)
    background_mean = None if background is None else float(np.asarray(background, dtype=float)[included].sum()) / len(included)
    targets = None if targets_mm is None else np.asarray(targets_mm, dtype=float)
    def optional(value, digits=None):
        value = float(value)
        return "" if not math.isfinite(value) else (round(value, digits) if digits is not None else value)
    from .disk_frames import FrameView
    for block in FrameView(frames).iter_blocks():
        spectra = block['spectra']
        if spectra.shape[1] != n_pixels:
            raise ValueError(f"spectrum length {spectra.shape[1]} does not match wavelength length {n_pixels}")
        totals = spectra[:, included].astype('int64').sum(axis=1)
        delays = block['delay_ps']
        if not np.isfinite(delays).all():raise ValueError('delay_ps must be finite')
        saturated = (spectra[:, included] >= full_scale).sum(axis=1) if full_scale is not None else None
        index = block['index']; temperature = block['temperature_c']; state = block['temperature_state']
        reported = block['stage_reported_mm']; start = block['t_start_s']; end = block['t_end_s']
        for row, total in enumerate(totals):
            mean_counts = float(total) / len(included)
            point = int(index[row, 1])
            error = abs(float(reported[row]) - targets[point]) * 1000 if targets is not None and 0 <= point < len(targets) else math.nan
            code = int(state[row])
            writer.writerow(
                [
                    block['offset'] + row,
                    int(index[row, 0]),
                    point,
                    int(index[row, 2]),
                    float(delays[row]),
                    center_nm,
                    half_window_nm,
                    mean_counts,
                    exposure_s,
                    "" if background_mean is None else background_mean,
                    "" if background_mean is None else mean_counts - background_mean,
                    "" if saturated is None else int(saturated[row]),
                    optional(temperature[row], 3),
                    TEMPERATURE_STATES[code] if 0 <= code < len(TEMPERATURE_STATES) else "",
                    optional(reported[row]),
                    optional(error, 4),
                    optional(start[row], 6),
                    optional(end[row], 6),
                ]
            )

    return buffer.getvalue()
