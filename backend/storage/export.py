import csv
import io
import math


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
    for w in wavelengths:
        if not isinstance(w, (int, float)) or isinstance(w, bool):
            raise ValueError("wavelengths_nm must contain numbers")
        if not math.isfinite(w):
            raise ValueError("wavelengths_nm must contain only finite values")

    if not isinstance(half_window_nm, (int, float)) or isinstance(half_window_nm, bool):
        raise ValueError("half_window_nm must be a number")
    if not math.isfinite(half_window_nm) or half_window_nm <= 0:
        raise ValueError("half_window_nm must be finite and > 0")

    if not isinstance(center_nm, (int, float)) or isinstance(center_nm, bool):
        raise ValueError("center_nm must be a number")
    if not math.isfinite(center_nm):
        raise ValueError("center_nm must be finite")

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
        spectrum = record["spectrum"]
        if len(spectrum) != n_pixels:
            raise ValueError(
                f"record {record.get('record_id')!r}: spectrum length "
                f"{len(spectrum)} does not match wavelength length {n_pixels}"
            )

        total = 0
        for i in included:
            value = spectrum[i]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"record {record.get('record_id')!r}: non-numeric spectrum value"
                )
            if not math.isfinite(value):
                raise ValueError(
                    f"record {record.get('record_id')!r}: non-finite spectrum value"
                )
            total += value

        mean_counts = total / len(included)

        delay_ps = record["delay_ps"]
        exposure_s = record["exposure_s"]
        if not math.isfinite(delay_ps):
            raise ValueError(
                f"record {record.get('record_id')!r}: non-finite delay_ps"
            )
        if not math.isfinite(exposure_s):
            raise ValueError(
                f"record {record.get('record_id')!r}: non-finite exposure_s"
            )

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

