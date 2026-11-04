"""The experiment-file layout (schema version 2), defined once.

The writer, the reader and the worker's run description all take their field lists from here, so the file,
the in-memory run and the code that fills it cannot drift apart. Field kinds: s text, f float, i integer,
b boolean, ia integer array. A value that is unavailable is stored empty ('' for text, NaN for numbers).

Order on disk: root attributes, instrument, request, coordinates, acquisition, calibration, background,
frame, events, analysis. Every time column is in seconds since the root attribute created_utc.
"""

SCHEMA_VERSION = 2
SOFTWARE_VERSION = '0.1.1'
# No fixed frame limit: a run takes frames until free memory or disk space runs low (backend/resources.py).
# Delay points per scan stay bounded so the TRPL map (points x pixels) remains a reasonable image.
MAX_POINTS = 20_000

# completed_rounds: whole repeat rounds in frame/. A stopped scan keeps only whole rounds; the round in
# progress when Stop was pressed is not stored.
ROOT = (('schema_version', 'i'), ('software_version', 's'), ('run_id', 's'), ('source_mode', 's'),
        ('created_utc', 's'), ('ended_utc', 's'), ('terminal_status', 's'), ('completed_rounds', 'i'))
INSTRUMENT = {
    'camera': (('model', 's'), ('serial_number', 's'), ('detector_rows', 'i'), ('chip_pixels', 'i'), ('bit_depth', 'i'),
               ('full_scale', 'f'), ('cooler_on', 'b'), ('target_temperature_c', 'f'), ('temperature_resolution_c', 'f'),
               # Photoelectrons per count and read noise (electrons rms): the shot-noise limit of the spectra.
               ('electrons_per_count', 'f'), ('read_noise_e', 'f')),
    'stage': (('model', 's'), ('controller_model', 's'), ('controller_identity', 's'), ('velocity_mm_s', 'f'),
              ('referenced', 'b'), ('position_source', 's'), ('position_sensor', 'b')),
    'spectrometer': (('driver', 's'), ('model', 's'), ('manual', 'b'), ('identity', 's')),
}
# request/segments is a (S, 3) dataset beside these attributes. repeats 0 means "repeat until stopped".
REQUEST = (('mode', 's'), ('repeats', 'i'), ('frames_per_point', 'i'), ('exposure_s', 'f'), ('settle_s', 'f'))
SEGMENT_COLUMNS = ('start', 'end', 'step')
COORDINATES = (('zero_mm', 'f'), ('path_multiplier', 'f'), ('delay_sign', 'i'))
# acquisition/delays_ps and acquisition/targets_mm are datasets; the scan plan's scalars come first.
# For a run repeated until stopped, rounds/total_points/total_frames are 0 (unbounded); completed_rounds says how many.
PLAN_ARRAYS = ('delays_ps', 'targets_mm')
PLAN = (('plan_id', 's'), ('approach_mm', 'f'), ('rounds', 'i'), ('total_points', 'i'), ('total_frames', 'i'))
SETTINGS = (('exposure_s', 'f'), ('readout_mode', 's'), ('readout_roi', 'ia'), ('hbin', 'i'), ('vbin', 'i'), ('pixels', 'i'),
            ('center_nm', 'f'), ('grating', 'i'), ('slit_um', 'f'))
ACQUISITION = PLAN + SETTINGS
# Datasets: wavelengths_nm (the spectra's x axis), coefficients, line_pairs (L, 2) and, only for a calibration
# without a polynomial whose chip axis differs, chip_wavelengths_nm. The intensity response, when one is installed:
# response_wavelengths_nm and response (relative, 1 at the peak, NaN where too weak to divide by); the frames stay
# raw counts and the response-corrected signal is (raw - background) / response (backend/response.py).
CALIBRATION_ARRAYS = ('wavelengths_nm', 'coefficients', 'line_pairs', 'chip_wavelengths_nm', 'response_wavelengths_nm', 'response')
CALIBRATION = (('calibration_id', 's'), ('source', 's'), ('installed_utc', 's'), ('convention', 's'), ('degree', 'i'),
               ('pixel_origin', 'f'), ('pixel_scale', 'f'), ('rms_nm', 'f'), ('max_error_nm', 'f'), ('reference_name', 's'),
               ('response_id', 's'), ('response_source', 's'), ('response_reference', 's'), ('response_installed_utc', 's'))
# background/spectrum (float64, the mean of `frames` frames) is a dataset, present only when present is true.
BACKGROUND = (('present', 'b'), ('frames', 'i'), ('condition', 's'), ('temperature_c', 'f'), ('acquired_utc', 's'))

# frame/: one row per frame; the row number is the record_id. (name, dtype, columns) besides frame/spectra.
COLUMNS = (('index', 'i4', 3), ('delay_ps', 'f8', 0), ('stage_reported_mm', 'f8', 0), ('temperature_c', 'f4', 0),
           ('temperature_state', 'i1', 0), ('saturated_pixels', 'i4', 0), ('t_start_s', 'f8', 0), ('t_end_s', 'f8', 0))
INDEX_COLUMNS = ('round_index', 'point_index', 'frame_index')
TEMPERATURE_STATES = ('off', 'not_reached', 'not_stabilized', 'stabilized', 'drift')
TEMPERATURE_CODES = '0=off, 1=not_reached, 2=not_stabilized, 3=stabilized, 4=drift, -1=unknown'

# events/: t_s, status, message. The file says 'failed' where the application says 'error'.
FILE_STATUS = {'completed': 'completed', 'stopped': 'stopped', 'error': 'failed'}
RUN_STATUS = {'completed': 'completed', 'stopped': 'stopped', 'failed': 'error', '': 'partial'}

# analysis/<task_id>/: fitted kinetics as datasets, scalars as attributes. Each parameter p has p (value),
# p_stderr (local Jacobian error) and p_round_se (between-round error; NaN with fewer than two rounds fitted).
# rounds_requested is empty when all rounds were asked for; rounds_used lists the rounds that contributed.
FIT_ARRAYS = ('delays_ps', 'observed', 'predicted', 'residuals')
FIT_SETTINGS = (('wavelength_nm', 'f'), ('half_window_nm', 'f'), ('irf_fwhm_ps', 'f'), ('min_delay_ps', 'f'), ('max_delay_ps', 'f'))
FIT_PROCESSING = (('subtract_background', 'b'), ('exclude_saturated', 'b'), ('exclude_unstable', 'b'), ('position_tolerance_um', 'f'),
                  ('response_corrected', 'b'))
FIT_SUMMARY = (('converged', 'b'), ('jacobian_condition', 'f'), ('record_count', 'i'), ('frames_used', 'i'), ('round_fits', 'i'),
               ('finished_utc', 's'))
FIT_EXCLUDED = (('saturated_points', 'i'), ('unstable_frames', 'i'), ('off_position_frames', 'i'))


def names(fields):
    return tuple(name for name, _ in fields)


def pick(values, fields):
    """The schema's fields of a dict, in schema order; missing ones are None (written empty)."""
    values = values or {}
    return {name: values.get(name) for name, _ in fields}
