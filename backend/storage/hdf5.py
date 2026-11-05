"""Experiment files. The layout is defined in schema.py; all HDF5 access for a live run occurs in one
bounded writer thread. Rows below frame/@committed_frames are valid; the counter advances only after the
rows it covers are flushed to disk."""
from concurrent.futures import Future
from pathlib import Path
from queue import Queue, Empty
from threading import Thread, Lock, Event
import math
import time
import heapq
import h5py
import numpy as np
from . import schema
from .schema import (SCHEMA_VERSION, ROOT, INSTRUMENT, REQUEST, SEGMENT_COLUMNS, COORDINATES, PLAN_ARRAYS, ACQUISITION,
                     CALIBRATION, BACKGROUND, COLUMNS, TEMPERATURE_CODES, FILE_STATUS, RUN_STATUS, FIT_ARRAYS, FIT_SETTINGS, FIT_SUMMARY,
                     FIT_PROCESSING, FIT_EXCLUDED)
from .frames import frame_row, validate_record

TEXT = h5py.string_dtype('utf-8')


def _number(value):
    return math.nan if value is None else float(value)


def _put(node, values, fields):
    for name, kind in fields:
        value = (values or {}).get(name)
        if kind == 's':node.attrs.create(name, '' if value is None else str(value), dtype=TEXT)
        elif kind == 'f':node.attrs[name] = np.float64(_number(value))
        elif kind == 'ia':node.attrs[name] = np.asarray([] if value is None else value, dtype='i8')
        elif value is None:node.attrs[name] = h5py.Empty('i1')
        else:node.attrs[name] = np.bool_(value) if kind == 'b' else np.int64(value)


def _get(node, fields):
    out = {}
    for name, kind in fields:
        value = node.attrs.get(name)
        if value is None or isinstance(value, h5py.Empty):out[name] = None
        elif kind == 's':out[name] = (value.decode() if isinstance(value, bytes) else str(value)) or None
        elif kind == 'f':out[name] = None if not np.isfinite(value) else float(value)
        elif kind == 'ia':out[name] = np.asarray(value).tolist()
        elif kind == 'b':out[name] = bool(value)
        else:out[name] = int(value)
    return out


def _group(parent, name):
    return parent.create_group(name, track_order=True)


def _array(node, name, values, columns=0):
    data = np.asarray([] if values is None else values, dtype='f8').reshape((-1, columns) if columns else (-1,))
    return node.create_dataset(name, data=data, dtype='f8')


def _texts(values):
    return np.asarray([str(v) for v in values or []], dtype=object)


def write_analysis(parent, task_id, result):
    """One lifetime fit as a group: fitted kinetics as arrays, every scalar as an attribute."""
    group = _group(parent, task_id)
    for name in FIT_ARRAYS:
        group.create_dataset(name, data=np.asarray([_number(v) for v in result.get(name) or []], dtype='f8'))
    group.attrs.create('model', str(result.get('model') or ''), dtype=TEXT)
    names = list((result.get('parameters') or {}).keys())
    group.attrs.create('parameter_names', _texts(names), dtype=TEXT)
    spread = result.get('round_spread') or {}
    for name in names:
        group.attrs[name] = np.float64(_number(result['parameters'][name]))
        group.attrs[name + '_stderr'] = np.float64(_number((result.get('standard_errors') or {}).get(name)))
        group.attrs[name + '_round_se'] = np.float64(_number(spread.get(name)))
    settings = dict(result.get('request') or {})
    settings.setdefault('irf_fwhm_ps', result.get('irf_fwhm_ps'))
    _put(group, settings, FIT_SETTINGS)
    _put(group, result.get('processing') or settings, FIT_PROCESSING)
    group.attrs['rounds_requested'] = np.asarray(settings.get('rounds') or [], dtype='i8')
    group.attrs['rounds_used'] = np.asarray(result.get('rounds_used') or [], dtype='i8')
    _put(group, {'excluded_' + k: v for k, v in (result.get('excluded') or {}).items()}, tuple(('excluded_' + n, k) for n, k in FIT_EXCLUDED))
    _put(group, result, FIT_SUMMARY)
    group.attrs.create('warnings', _texts(result.get('warnings')), dtype=TEXT)


def read_analysis(group, run_id):
    names = [str(n) for n in group.attrs.get('parameter_names', [])]
    value = lambda key: None if not np.isfinite(group.attrs[key]) else float(group.attrs[key])
    optional = lambda key: value(key) if key in group.attrs else None
    result = dict(model=str(group.attrs.get('model', '')) or None,
                  parameters={n: value(n) for n in names}, standard_errors={n: value(n + '_stderr') for n in names})
    spread = {n: optional(n + '_round_se') for n in names}
    result['round_spread'] = spread if any(v is not None for v in spread.values()) else None
    request = _get(group, FIT_SETTINGS)
    processing = _get(group, FIT_PROCESSING)
    if processing['exclude_saturated'] is None:processing['exclude_saturated'] = False
    for key in ('subtract_background', 'exclude_unstable'):processing[key] = bool(processing[key])
    rounds = [int(r) for r in group.attrs.get('rounds_requested', [])]
    result['irf_fwhm_ps'] = request['irf_fwhm_ps']
    result['processing'] = processing
    result['request'] = dict(model=result['model'], **request, **processing, rounds=rounds or None)
    result['rounds_used'] = [int(r) for r in group.attrs.get('rounds_used', [])]
    result['excluded'] = {n: v for (n, _), v in zip(FIT_EXCLUDED, _get(group, tuple(('excluded_' + n, k) for n, k in FIT_EXCLUDED)).values())}
    for name in FIT_ARRAYS:
        result[name] = [None if not np.isfinite(v) else float(v) for v in group[name][:]]
    result.update(_get(group, FIT_SUMMARY))
    result['warnings'] = [str(w) for w in group.attrs.get('warnings', [])]
    result.update(run_id=run_id, task_id=group.name.rsplit('/', 1)[-1], stored=True)
    return result


def write_metadata(file, metadata):
    # completed_rounds is updated in place at close, which an empty attribute does not allow.
    _put(file, dict(metadata['root'], completed_rounds=metadata['root'].get('completed_rounds') or 0), ROOT)
    instrument = _group(file, 'instrument')
    for name, fields in INSTRUMENT.items():_put(_group(instrument, name), metadata['instrument'].get(name), fields)
    request = _group(file, 'request')
    segments = [[segment[key] for key in SEGMENT_COLUMNS] for segment in metadata['request'].get('segments') or []]
    _array(request, 'segments', segments, columns=3).attrs.create('columns', 'start_ps, end_ps, step_ps', dtype=TEXT)
    _put(request, metadata['request'], REQUEST)
    _put(_group(file, 'coordinates'), metadata['coordinates'], COORDINATES)
    acquisition = _group(file, 'acquisition')
    for name in PLAN_ARRAYS:_array(acquisition, name, metadata['acquisition'][name])
    _put(acquisition, metadata['acquisition'], ACQUISITION)
    calibration = _group(file, 'calibration'); values = metadata['calibration']
    _array(calibration, 'wavelengths_nm', values.get('wavelengths_nm'))
    _array(calibration, 'coefficients', values.get('coefficients'))
    _array(calibration, 'line_pairs', values.get('line_pairs'), columns=2).attrs.create('columns', 'pixel, wavelength_nm', dtype=TEXT)
    if values.get('chip_wavelengths_nm') is not None:_array(calibration, 'chip_wavelengths_nm', values['chip_wavelengths_nm'])
    if values.get('response') is not None:
        # Undefined response (too weak to divide by) is stored as NaN.
        _array(calibration, 'response_wavelengths_nm', values.get('response_wavelengths_nm'))
        _array(calibration, 'response', [math.nan if v is None else v for v in values['response']]).attrs.create(
            'meaning', 'relative intensity response, 1 at its peak; corrected = (raw - background) / response', dtype=TEXT)
    _put(calibration, values, CALIBRATION)
    background = _group(file, 'background'); values = metadata['background']
    if values.get('present'):background.create_dataset('spectrum', data=np.asarray(values['spectrum'], dtype='f8'))
    _put(background, values, BACKGROUND)


def read_metadata(file):
    request = _get(file['request'], REQUEST)
    request['segments'] = [dict(zip(SEGMENT_COLUMNS, map(float, row))) for row in file['request/segments'][:]]
    acquisition = _get(file['acquisition'], ACQUISITION)
    for name in PLAN_ARRAYS:acquisition[name] = file['acquisition'][name][:].tolist()
    calibration = _get(file['calibration'], CALIBRATION)
    for name in schema.CALIBRATION_ARRAYS:
        calibration[name] = (file['calibration'][name][:].tolist() if name in file['calibration'] else []) or None
    if calibration.get('response') is not None:
        calibration['response'] = [None if not math.isfinite(v) else v for v in calibration['response']]
    background = _get(file['background'], BACKGROUND)
    background['spectrum'] = file['background/spectrum'][:].tolist() if 'spectrum' in file['background'] else None
    return dict(root=_get(file, ROOT), instrument={name: _get(file['instrument'][name], fields) for name, fields in INSTRUMENT.items()},
                request=request, coordinates=_get(file['coordinates'], COORDINATES), acquisition=acquisition,
                calibration=calibration, background=background)


def rows_of_records(records):
    """Column arrays for acquired frame dicts (the live scan path)."""
    rows = [frame_row(record) for record in records]
    block = {name: np.asarray([row[name] for row in rows], dtype=dtype) for name, dtype, _ in COLUMNS}
    block['spectra'] = np.stack([np.asarray(record['spectrum']) for record in records])
    return block


class HDFWriter:
    """Bounded asynchronous FIFO; futures acknowledge only flushed commit markers.

    Operations: 'record' (one acquired frame dict), 'rows' (a FrameTable.block of frames already in memory,
    used when a memory-only run is saved), 'truncate' (keep the first N frames), 'event' (a state change)
    and 'close'."""
    capacity = 128
    batch_size = 32
    batch_latency = .010

    def __init__(self, path, metadata):
        self.path = Path(path); self.partial = self.path.with_suffix('.partial.h5')
        self.tasks = Queue(maxsize=self.capacity); self.ready = Future(); self.submit_lock = Lock()
        self.closed = False; self.failure = None; self.finished = Event()
        self.stats = dict(frames=0, batches=0, flushes=0, events=0, max_queue=0, backpressure=0)
        self.thread = Thread(target=self._loop, args=(metadata,), daemon=True, name='hdf-writer')
        self.thread.start()
        try:self.ready.result(timeout=15)
        except Exception:self.thread.join(2); raise

    def submit(self, operation, value=None):
        future = Future()
        with self.submit_lock:
            if self.closed or self.finished.is_set() or not self.thread.is_alive():raise RuntimeError('Experiment writer is closed')
            if operation in ('record', 'rows') and self.failure:
                future.set_exception(RuntimeError(self.failure)); return future
            if self.tasks.full():self.stats['backpressure'] += 1
            self.tasks.put((operation, value, future), timeout=10)
            if operation == 'close':self.closed = True
            if self.finished.is_set() and not future.done():future.set_exception(RuntimeError(self.failure or 'Writer closed'))
            self.stats['max_queue'] = max(self.stats['max_queue'], self.tasks.qsize())
        return future

    def call(self, operation, value=None):
        return self.submit(operation, value).result(timeout=30)

    def _loop(self, metadata):
        file = None; created = initialized = False; active = []
        pixels = int(metadata['acquisition'].get('pixels') or 1024)
        def flush():
            file.flush(); self.stats['flushes'] += 1
        def fail(future, exc):
            if not future.done():future.set_exception(exc)
        try:
            file = h5py.File(self.partial, 'x', track_order=True); created = True
            write_metadata(file, metadata)
            frame = _group(file, 'frame')
            spectra = frame.create_dataset('spectra', (0, pixels), maxshape=(None, pixels), dtype='i4', chunks=(32, pixels), compression='lzf', shuffle=True)
            columns = {}
            for name, dtype, width in COLUMNS:
                shape = (0, width) if width else (0,)
                columns[name] = frame.create_dataset(name, shape, maxshape=(None,) + shape[1:], dtype=dtype, chunks=(256,) + shape[1:], compression='lzf')
            columns['index'].attrs.create('columns', 'round, point, frame', dtype=TEXT)
            columns['temperature_state'].attrs.create('codes', TEMPERATURE_CODES, dtype=TEXT)
            frame.attrs['committed_frames'] = np.int64(0)
            events = _group(file, 'events')
            log = dict(t_s=events.create_dataset('t_s', (0,), maxshape=(None,), dtype='f8', chunks=(64,)),
                       status=events.create_dataset('status', (0,), maxshape=(None,), dtype=TEXT, chunks=(64,)),
                       message=events.create_dataset('message', (0,), maxshape=(None,), dtype=TEXT, chunks=(64,)))
            analysis = _group(file, 'analysis')
            for task_id, result in (metadata.get('analysis') or {}).items():write_analysis(analysis, task_id, result)
            flush(); initialized = True; self.ready.set_result(True)

            def append(block):
                # The data is on disk before the counter that declares it valid; a failed append is never counted.
                offset = len(spectra); size = len(block['spectra'])
                if size == 0:return
                spectra.resize(offset + size, axis=0); spectra[offset:] = block['spectra']
                for name, dataset in columns.items():
                    dataset.resize(offset + size, axis=0); dataset[offset:] = np.asarray(block[name], dtype=dataset.dtype)
                flush(); frame.attrs.modify('committed_frames', np.int64(offset + size)); flush()
                self.stats['batches'] += 1; self.stats['frames'] += size

            done = False
            while not done:
                active = [self.tasks.get()]; deadline = time.monotonic() + self.batch_latency
                while len(active) < self.batch_size and active[-1][0] not in {'close','read'}:
                    try:active.append(self.tasks.get(timeout=max(.00001, deadline - time.monotonic())))
                    except Empty:break
                    if time.monotonic() >= deadline:break
                cursor = 0
                while cursor < len(active):
                    operation, value, future = active[cursor]
                    if operation == 'record':
                        batch = []; invalid = None; end = cursor; offset = len(spectra)
                        while end < len(active) and active[end][0] == 'record':
                            _, record, ack = active[end]; end += 1
                            if self.failure:fail(ack, RuntimeError(self.failure)); continue
                            try:
                                validate_record(record, offset + len(batch), pixels)
                                if record.get('inject_write_failure'):raise OSError('Injected disk write failure')
                                batch.append((record, ack))
                            except Exception as exc:
                                self.failure = str(exc); invalid = (ack, exc)
                        if batch:
                            try:
                                append(rows_of_records([record for record, _ in batch]))
                                for _, ack in batch:ack.set_result(True)
                            except Exception as exc:
                                self.failure = str(exc)
                                for _, ack in batch:fail(ack, exc)
                        if invalid:fail(*invalid)
                        cursor = end; continue
                    try:
                        if operation == 'rows':
                            if self.failure:raise RuntimeError(self.failure)
                            if value['offset'] != len(spectra):raise ValueError(f'Frame block starts at {value["offset"]}, expected {len(spectra)}')
                            if value['spectra'].shape[1:] != (pixels,):raise ValueError(f'Frame block must have {pixels} pixels per spectrum')
                            append(value); future.set_result(True)
                        elif operation == 'event':
                            offset = len(log['t_s'])
                            for dataset in log.values():dataset.resize(offset + 1, axis=0)
                            log['t_s'][offset] = _number(value.get('t_s'))
                            log['status'][offset] = FILE_STATUS.get(value['status'], value['status'])
                            log['message'][offset] = str(value.get('message') or '')
                            flush(); self.stats['events'] += 1; future.set_result(True)
                        elif operation == 'truncate':
                            # A stopped scan drops its unfinished round. The counter goes down first, so the rows
                            # above it are invalid before they are removed.
                            if self.failure:raise RuntimeError(self.failure)
                            count = int(value)
                            if not 0 <= count <= len(spectra):raise ValueError(f'Cannot keep {count} of {len(spectra)} frames')
                            frame.attrs.modify('committed_frames', np.int64(count)); flush()
                            spectra.resize(count, axis=0)
                            for dataset in columns.values():dataset.resize(count, axis=0)
                            flush(); self.stats['frames'] = count; future.set_result(True)
                        elif operation == 'read':
                            start, stop = value
                            if not 0 <= start <= stop <= int(frame.attrs['committed_frames']):
                                raise ValueError('Read exceeds committed frames')
                            future.set_result({name: frame[name][start:stop] for name in ('spectra',) + tuple(n for n, _, _ in COLUMNS)})
                        elif operation == 'close':
                            status = value['status']
                            if status not in FILE_STATUS:raise ValueError('Invalid terminal status')
                            terminal = 'error' if self.failure else status
                            # modify() keeps each attribute in its place; create() would move it to the end.
                            file.attrs.modify('ended_utc', str(value.get('ended_utc') or ''))
                            file.attrs.modify('terminal_status', FILE_STATUS[terminal])
                            file.attrs.modify('completed_rounds', np.int64(value.get('completed_rounds') or 0))
                            stored = len(spectra)
                            flush(); file.close(); file = None
                            # A stopped scan that kept whole rounds is a usable experiment under its own name;
                            # one with no whole round is NAME.stopped.h5.
                            kept = terminal == 'completed' or (terminal == 'stopped' and stored > 0)
                            final = self.partial if terminal == 'error' else self.path if kept else self.path.with_suffix('.stopped.h5')
                            if final != self.partial:
                                if final.exists():raise FileExistsError(final)
                                self.partial.rename(final)
                            future.set_result(str(final)); done = True
                        else:raise ValueError('Unknown writer operation: ' + operation)
                    except Exception as exc:
                        if operation != 'read':self.failure = str(exc)
                        fail(future, exc)
                        if operation == 'event':
                            for _, _, ack in active[cursor + 1:]:fail(ack, exc)
                            raise
                        if operation == 'close':done = True
                    cursor += 1
                active = []
        except Exception as exc:
            self.failure = str(exc)
            if not self.ready.done():self.ready.set_exception(exc)
            for _, _, future in active:fail(future, exc)
        finally:
            self.finished.set()
            if file is not None:
                try:file.close()
                except Exception as exc:self.failure = self.failure or str(exc)
            while True:
                try:_, _, future = self.tasks.get_nowait(); fail(future, RuntimeError(self.failure or 'Writer closed'))
                except Empty:break
            if created and not initialized:self.partial.unlink(missing_ok=True)


def _read_run(path):
    with h5py.File(path, 'r') as f:
        version = f.attrs.get('schema_version')
        if version is None or isinstance(version, h5py.Empty) or int(version) != SCHEMA_VERSION:
            raise ValueError(f'Unsupported experiment file (schema version {version}); this release reads version {SCHEMA_VERSION} only')
        stored = f['frame/spectra']
        count = int(f['frame'].attrs['committed_frames'])
        if stored.ndim != 2 or not 1 <= stored.shape[1] <= 1024 or stored.dtype.kind not in 'iu':
            raise ValueError('Invalid raw spectra dataset')
        for name, _, width in COLUMNS:
            column = f['frame'][name]
            if column.shape[1:] != ((width,) if width else ()):
                raise ValueError(f'Frame column {name} has the wrong shape')
        if not 0 <= count <= min([len(stored)] + [len(f['frame'][name]) for name, _, _ in COLUMNS]):
            raise ValueError('Committed frame count exceeds the stored rows')
        metadata = read_metadata(f)
        run_id = metadata['root']['run_id']
        if not run_id or not metadata['root']['created_utc']:raise ValueError('Invalid experiment identity')
        if not metadata['acquisition']['delays_ps']:raise ValueError('Missing experiment scan plan')
        exposure = metadata['acquisition']['exposure_s']
        if exposure is None or not exposure > 0:raise ValueError('Invalid exposure time')
        from .disk_frames import DiskFrames
        frames = DiskFrames(path, count, stored.shape[1], exposure, metadata['root']['source_mode'], run_id)
        events = f['events']; optional = lambda value: None if not np.isfinite(value) else float(value)
        metadata['events'] = [dict(t_s=optional(t), status=RUN_STATUS.get(str(s), str(s)), message=str(m))
                              for t, s, m in zip(events['t_s'][:], events['status'].asstr()[:], events['message'].asstr()[:])]
        recent = []
        for name, group in f['analysis'].items():
            result = read_analysis(group, run_id)
            heapq.heappush(recent, (str(result.get('finished_utc') or ''), name, result))
            if len(recent) > 64:heapq.heappop(recent)
        metadata['analysis'] = {name: result for _, name, result in sorted(recent)}
        terminal = RUN_STATUS.get(metadata['root']['terminal_status'] or '')
        if terminal is None:raise ValueError('Invalid experiment terminal status')
        return metadata, frames, terminal


def read_run(path):
    """(metadata, lazy DiskFrames, terminal status) of an experiment file."""
    if not Path(path).is_file():raise FileNotFoundError('Experiment file does not exist')
    try:return _read_run(path)
    except (OSError, KeyError, IndexError, TypeError, OverflowError, ValueError) as exc:
        raise ValueError(f'Invalid or damaged experiment file: {exc}') from exc


def store_analyses(path, run_id, results):
    '''Write fits made on a file opened in Analysis back into it: {task_id: result} into /analysis. The frames and
    every other group are left as they are; a fit already in the file is skipped. Returns the task ids written.'''
    with h5py.File(path, 'r+') as file:
        stored = file.attrs.get('run_id')
        stored = stored.decode() if isinstance(stored, bytes) else str(stored)
        if stored != run_id:raise ValueError(f'The file now holds another experiment (run {stored}, expected {run_id})')
        written = []
        for task_id, result in results.items():
            if task_id in file['analysis']:continue
            write_analysis(file['analysis'], task_id, result); written.append(task_id)
        file.flush()
    return written


def delete_analysis(path, run_id, task_id):
    '''Remove one fit group from /analysis; the frames and the rest of the file are untouched. True if it was there.
    HDF5 does not shrink the file: the freed space is reused by later writes.'''
    with h5py.File(path, 'r+') as file:
        stored = file.attrs.get('run_id')
        stored = stored.decode() if isinstance(stored, bytes) else str(stored)
        if stored != run_id:raise ValueError(f'The file now holds another experiment (run {stored}, expected {run_id})')
        if task_id not in file['analysis']:return False
        del file['analysis'][task_id]; file.flush()
        return True


def append_analysis(path, task_id, result):
    """Only used after live acquisition has finalized and closed its writer."""
    with h5py.File(path, 'r+') as file:
        if task_id in file['analysis']:raise ValueError('Analysis task already exists in this experiment')
        write_analysis(file['analysis'], task_id, result)
        file.flush()
