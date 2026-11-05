"""HDF5-backed frames with a bounded LRU of immutable pages; no persistent file handles."""
from collections import OrderedDict
from pathlib import Path
from threading import RLock
import shutil
import tempfile

import h5py
import numpy as np

from .frames import FrameTable, validate_arrays
from .schema import COLUMNS

PAGE_FRAMES = 512
CACHE_BYTES = 8 * 1024 * 1024


class FrameView:
    """A fixed frame-count snapshot for a fit/export while its source may still grow."""
    def __init__(self, source, count=None):
        self.source = source
        self.count = len(source) if count is None else min(int(count), len(source))

    def __len__(self):
        return self.count

    def iter_blocks(self, cancel=None):
        for start in range(0, self.count, PAGE_FRAMES):
            if cancel is not None and cancel.is_set():
                raise ValueError('Fit cancelled')
            stop = min(start + PAGE_FRAMES, self.count)
            block = self.source.block(start, stop)
            if len(block['spectra']) != stop-start:
                raise ValueError('Experiment changed while reading; retry the analysis')
            yield block


class DiskFrames:
    def __init__(self, path, count, pixels, exposure_s, source_mode, run_id, writer=None, cache_bytes=CACHE_BYTES):
        self.path = Path(path)
        self.pixels = int(pixels)
        self.exposure_s = float(exposure_s)
        self.source_mode = source_mode
        self.run_id = run_id
        self._size = int(count)
        self._writer = writer
        self._lock = RLock()
        self._cache = OrderedDict()
        self.cache_bytes = max(0, int(cache_bytes))
        self.cached_bytes = 0
        self._temporary = None
        self._file_stamp = None

    def __len__(self):
        return self._size

    def __del__(self):
        temporary = getattr(self, '_temporary', None)
        if temporary is not None:
            try:temporary.cleanup()
            except OSError:pass

    def growth_bytes(self):
        return 0  # Committed frames stay on disk, not in a growing RAM table.

    def append(self, record):
        # Called only after the writer acknowledged the flushed frame.
        with self._lock:
            if record['record_id'] != self._size:
                raise ValueError('Committed frame arrived out of order')
            self._size += 1
            self._drop((self._size - 1) // PAGE_FRAMES)

    def _drop(self, page):
        old = self._cache.pop(page, None)
        if old is not None:
            self.cached_bytes -= sum(v.nbytes for v in old.values() if isinstance(v, np.ndarray))

    def truncate(self, count):
        with self._lock:
            if not 0 <= count <= self._size:
                raise ValueError('Invalid frame truncation')
            self._size = int(count)
            self._cache.clear(); self.cached_bytes = 0

    def finalize(self, writer, terminal):
        # Serialize the rename/close with readers; live reads use the writer's own HDF5 thread.
        with self._lock:
            try:
                self.path = Path(writer.call('close', terminal))
            finally:
                self._writer = None
                if not self.path.exists():
                    self.path = writer.partial
            return str(self.path)

    def preserve_unfinished(self):
        """Keep failed-run data available if the UI moves its output to the Recycle Bin."""
        with self._lock:
            if not self._size or self._temporary is not None:
                return
            temporary = tempfile.TemporaryDirectory(prefix='kerr-frames-')
            try:
                target = Path(temporary.name) / 'frames.h5'
                shutil.copyfile(self.path, target)
            except Exception:
                temporary.cleanup()
                raise
            self._temporary = temporary
            self.path = target

    def _open(self):
        file = h5py.File(self.path, 'r')
        if str(file.attrs.get('run_id', '')) != self.run_id:
            file.close()
            raise ValueError('The experiment file was replaced; reopen it before reading frames')
        return file

    def _page(self, page):
        cached = self._cache.get(page)
        if cached is not None:
            self._cache.move_to_end(page)
            return cached
        start = page * PAGE_FRAMES
        stop = min(start + PAGE_FRAMES, self._size)
        if self._writer is not None:
            arrays = self._writer.call('read', (start, stop))
        else:
            with self._open() as file:
                frame = file['frame']
                if stop > int(frame.attrs['committed_frames']):
                    raise ValueError('Experiment frames were truncated; reopen the file')
                arrays = {name: frame[name][start:stop] for name in ('spectra',) + tuple(n for n, _, _ in COLUMNS)}
        validate_arrays(arrays['spectra'], arrays)
        if len(arrays['spectra']) != stop - start or arrays['spectra'].shape[1] != self.pixels:
            raise ValueError('Incomplete experiment frame page')
        arrays = {name: np.asarray(values, dtype='i4' if name == 'spectra' else dict((n, d) for n, d, _ in COLUMNS)[name])
                  for name, values in arrays.items()}
        for values in arrays.values():
            values.setflags(write=False)
        size = sum(v.nbytes for v in arrays.values())
        if size <= self.cache_bytes:
            while self._cache and self.cached_bytes + size > self.cache_bytes:
                self._drop(next(iter(self._cache)))
            self._cache[page] = arrays; self.cached_bytes += size
        return arrays

    def block(self, start, stop):
        with self._lock:
            if self._writer is None and self._size:
                stat = self.path.stat()
                stamp = (stat.st_ino,stat.st_size,stat.st_mtime_ns)
                if stamp != self._file_stamp:
                    with self._open():pass
                    self._cache.clear(); self.cached_bytes = 0
                    self._file_stamp = stamp
            start = min(self._size, max(0, int(start)))
            stop = max(start, min(self._size, int(stop)))
            arrays = dict(spectra=np.empty((stop-start, self.pixels), dtype='i4'))
            arrays.update({name: np.empty((stop-start, width) if width else (stop-start,), dtype=dtype)
                           for name, dtype, width in COLUMNS})
            page_start = start // PAGE_FRAMES * PAGE_FRAMES
            for offset in range(page_start, stop if start < stop else page_start, PAGE_FRAMES):
                page = self._page(offset // PAGE_FRAMES)
                low, high = max(start, offset), min(stop, offset + PAGE_FRAMES)
                for name in arrays:
                    arrays[name][low-start:high-start] = page[name][low-offset:high-offset]
            return dict(offset=start, exposure_s=self.exposure_s, source_mode=self.source_mode, **arrays)

    def record(self, index):
        index = range(self._size)[index]
        block = self.block(index, index+1)
        table = FrameTable.from_arrays(block['spectra'], block, self.exposure_s, self.source_mode)
        record = table.record(0); record['record_id'] = index
        return record

    def completed_points(self, frames_per_point):
        # Opening a file scans only its small index column, never its spectra.
        seen = set()
        with self._lock, self._open() as file:
            index = file['frame/index']
            for start in range(0, self._size, 65536):
                rows = index[start:min(start+65536, self._size)]
                seen.update(map(tuple, rows[rows[:, 2] == frames_per_point-1, :2].tolist()))
        return len(seen)
