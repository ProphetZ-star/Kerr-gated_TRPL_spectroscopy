"""Columnar frame storage: the in-memory form of an experiment's frame/ group.

Acquisition appends one frame at a time, the file reader builds a table straight from the stored arrays, and
analysis, export and the GUI read whole columns or blocks of rows. Row i is record_id i. Exposure and source
mode are the same for every frame of a run, so the table holds them once.
"""
import math
import numpy as np
from .schema import COLUMNS, TEMPERATURE_STATES

INT32 = np.iinfo(np.int32)


def _number(value):
    return math.nan if value is None else float(value)


def _optional(value):
    value = float(value)
    return value if math.isfinite(value) else None


def frame_row(record):
    """The column values of one acquired frame (a dict from the scan loop)."""
    state = record.get('temperature_state')
    return dict(index=[record['round_index'], record['point_index'], record['frame_index']], delay_ps=float(record['delay_ps']),
                stage_reported_mm=_number(record.get('stage_reported_mm')), temperature_c=_number(record.get('temperature_c')),
                temperature_state=TEMPERATURE_STATES.index(state) if state in TEMPERATURE_STATES else -1,
                saturated_pixels=int(record.get('saturated_pixels') or 0),
                t_start_s=_number(record.get('t_start_s')), t_end_s=_number(record.get('t_end_s')))


def validate_record(record, row, pixels):
    """One acquired frame before it is stored: complete, in order (record_id is the row number), integer counts."""
    if not isinstance(record, dict) or not {'spectrum', 'record_id', 'round_index', 'point_index', 'frame_index', 'delay_ps'} <= record.keys():
        raise ValueError('Experiment record is missing required fields')
    spectrum = np.asarray(record['spectrum'])
    if spectrum.shape != (pixels,) or spectrum.dtype.kind not in 'iu' or (spectrum.size and (spectrum.min() < INT32.min or spectrum.max() > INT32.max)):
        raise ValueError(f'Raw spectrum must contain exactly {pixels} int32-compatible integer counts')
    for key in ('record_id', 'round_index', 'point_index', 'frame_index'):
        if isinstance(record[key], (bool, np.bool_)) or not isinstance(record[key], (int, np.integer)) or record[key] < 0:
            raise ValueError(f'{key} must be a nonnegative integer')
    if record['record_id'] != row:raise ValueError(f'Record {record["record_id"]} arrived out of order (expected {row})')
    if not math.isfinite(float(record['delay_ps'])):raise ValueError('delay_ps must be finite')


def validate_arrays(spectra, columns):
    """A block of rows read from a file or received from the worker."""
    if spectra.ndim != 2 or not 1 <= spectra.shape[1] <= 1024:raise ValueError('Spectra must be a 2-D array of at most 1024 pixels')
    if spectra.dtype.kind not in 'iu':raise ValueError('Spectra must have an integer dtype')
    rows = len(spectra)
    for name, _, width in COLUMNS:
        column = columns[name]
        if column.shape != ((rows, width) if width else (rows,)):raise ValueError(f'Frame column {name} has the wrong shape')
    if rows and columns['index'].min() < 0:raise ValueError('Frame indices must be nonnegative')
    if not np.isfinite(columns['delay_ps']).all():raise ValueError('Frame delays must be finite')


class FrameTable:
    def __init__(self, pixels, exposure_s, source_mode, capacity=0):
        self.pixels = int(pixels); self.exposure_s = float(exposure_s); self.source_mode = source_mode
        self._size = 0; self._allocate(max(16, int(capacity)))

    def _allocate(self, capacity):
        old_spectra = getattr(self, '_spectra', None); old_columns = getattr(self, '_columns', {})
        self._spectra = np.zeros((capacity, self.pixels), 'i4')
        self._columns = {name: np.zeros((capacity, width) if width else (capacity,), dtype) for name, dtype, width in COLUMNS}
        if old_spectra is not None:
            self._spectra[:self._size] = old_spectra[:self._size]
            for name, column in old_columns.items():self._columns[name][:self._size] = column[:self._size]

    @classmethod
    def from_arrays(cls, spectra, columns, exposure_s, source_mode):
        validate_arrays(spectra, columns)
        table = cls.__new__(cls)
        table.pixels = int(spectra.shape[1]); table.exposure_s = float(exposure_s); table.source_mode = source_mode
        table._spectra = np.ascontiguousarray(spectra, dtype='i4')
        table._columns = {name: np.ascontiguousarray(columns[name], dtype=dtype) for name, dtype, _ in COLUMNS}
        table._size = len(spectra)
        return table

    def __len__(self):return self._size

    def __bool__(self):return self._size > 0

    @property
    def spectra(self):return self._spectra[:self._size]

    def column(self, name):return self._columns[name][:self._size]

    @property
    def delays(self):return self.column('delay_ps')

    def append(self, record):
        validate_record(record, self._size, self.pixels)
        if self._size == len(self._spectra):
            self._allocate(self._next_capacity())
        row = frame_row(record); i = self._size
        self._spectra[i] = np.asarray(record['spectrum'])
        for name, column in self._columns.items():column[i] = row[name]
        self._size += 1

    def truncate(self, size):
        """Keep the first `size` frames (a stopped scan drops its unfinished round)."""
        if not 0 <= int(size) <= self._size:raise ValueError(f'Cannot keep {size} of {self._size} frames')
        self._size = int(size)

    def _next_capacity(self):
        return self._size + max(64, self._size // 2)

    def growth_bytes(self):
        '''Memory the next append takes if the table must grow: old and new arrays exist together while copying.'''
        if self._size < len(self._spectra):
            return 0
        return self._next_capacity() * (self.pixels * 4 + 64)

    def record(self, i):
        """One frame as a dict (the latest frame shown in the GUI, single-frame previews)."""
        i = range(self._size)[i]
        round_index, point_index, frame_index = (int(v) for v in self._columns['index'][i])
        state = int(self._columns['temperature_state'][i])
        return dict(record_id=i, round_index=round_index, point_index=point_index, frame_index=frame_index,
                    delay_ps=float(self._columns['delay_ps'][i]), exposure_s=self.exposure_s, source_mode=self.source_mode,
                    stage_reported_mm=_optional(self._columns['stage_reported_mm'][i]), temperature_c=_optional(self._columns['temperature_c'][i]),
                    temperature_state=TEMPERATURE_STATES[state] if 0 <= state < len(TEMPERATURE_STATES) else None,
                    saturated_pixels=int(self._columns['saturated_pixels'][i]),
                    t_start_s=_optional(self._columns['t_start_s'][i]), t_end_s=_optional(self._columns['t_end_s'][i]),
                    spectrum=self._spectra[i].copy())

    def block(self, start, stop):
        """Rows start..stop as arrays, the unit in which frames go to the writer and to the GUI."""
        start = max(0, int(start)); stop = min(self._size, int(stop))
        stop = max(start, stop)
        return dict(offset=start, exposure_s=self.exposure_s, source_mode=self.source_mode, spectra=self._spectra[start:stop],
                    **{name: self._columns[name][start:stop] for name, _, _ in COLUMNS})

    def completed_points(self, frames_per_point):
        index = self.column('index')
        last = index[index[:, 2] == frames_per_point - 1]
        return len(np.unique(last[:, :2], axis=0)) if len(last) else 0
