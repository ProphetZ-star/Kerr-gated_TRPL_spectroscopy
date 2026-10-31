import csv
import io
import math
from numbers import Real, Integral


def finite_number(value, name, positive=False):
    if isinstance(value,bool) or not isinstance(value,Real):raise ValueError(f'{name} must be a number')
    try:valid=math.isfinite(value)
    except (TypeError,OverflowError):valid=False
    if not valid or (positive and value<=0):raise ValueError(f'{name} must be finite'+(' and > 0' if positive else ''))
    return float(value)


def export_csv(
    records,
    wavelengths_nm,
    center_nm,
    half_window_nm,
    source_mode,
) -> str:
    """Export raw spectra to CSV, averaging included pixels per record.

    # source_mode: <source_mode>
    # window_definition: mean of included pixels
    # processing: raw no background subtraction
    """
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

    buffer = io.StringIO()
    buffer.write(f"# source_mode: {source_mode}\n")
    buffer.write("# window_definition: mean of included pixels\n")
    buffer.write("# processing: raw no background subtraction\n")

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
        ]
    )

    for record in records:
        required={'spectrum','record_id','round_index','point_index','frame_index','delay_ps','exposure_s'}
        if not isinstance(record,dict) or not required<=record.keys():raise ValueError('CSV record is missing required fields')
        for key in ('record_id','round_index','point_index','frame_index'):
            if isinstance(record[key],bool) or not isinstance(record[key],Integral) or record[key]<0:raise ValueError(f'{key} must be a nonnegative integer')
        spectrum = record["spectrum"]
        if len(spectrum) != n_pixels:
            raise ValueError(
                f"record {record.get('record_id')!r}: spectrum length "
                f"{len(spectrum)} does not match wavelength length {n_pixels}"
            )

        total = 0
        for i in included:
            value = spectrum[i]
            total += finite_number(value,'spectrum value')

        mean_counts = total / len(included)

        if not math.isfinite(mean_counts):raise ValueError('Mean spectrum count is non-finite')
        delay_ps=finite_number(record['delay_ps'],'delay_ps')
        exposure_s=finite_number(record['exposure_s'],'exposure_s',True)

        writer.writerow(
            [
                record["record_id"],
                record["round_index"],
                record["point_index"],
                record["frame_index"],
                delay_ps,
                center_nm,
                half_window_nm,
                mean_counts,
                exposure_s,
            ]
        )

    return buffer.getvalue()

