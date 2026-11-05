"""Free memory and disk space, for the acquisition's resource guard.

Memory-only runs retain frames in RAM; autosaved runs keep flushed frames on disk with a bounded page cache.
A scan stops by itself, keeping every complete point, once
free physical memory or free disk space falls below a reserve. Physical memory is what counts: Windows pages to
disk long before an allocation fails, and the acquisition would crawl well before it ran out.
"""
import ctypes
import os
import shutil
import sys

GiB = 1024 ** 3
MiB = 1024 ** 2
# Kept free for the system, the GUI and the frame table's next growth step.
MEMORY_RESERVE_MIN = 768 * MiB
MEMORY_RESERVE_FRACTION = 0.08
DISK_RESERVE = 256 * MiB
# A frame in memory: its spectrum (int32 per pixel) plus the per-frame columns (index, delay, stage, temperature,
# state, saturation, timing), rounded up.
FRAME_COLUMN_BYTES = 64


def frame_bytes(pixels):
    return int(pixels) * 4 + FRAME_COLUMN_BYTES


class _MemoryStatus(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total_physical', ctypes.c_ulonglong),
                ('available_physical', ctypes.c_ulonglong), ('total_page_file', ctypes.c_ulonglong),
                ('available_page_file', ctypes.c_ulonglong), ('total_virtual', ctypes.c_ulonglong),
                ('available_virtual', ctypes.c_ulonglong), ('available_extended_virtual', ctypes.c_ulonglong)]


def memory():
    """(available, total) physical memory in bytes; (None, None) when the system does not say."""
    try:
        if sys.platform == 'win32':
            status = _MemoryStatus(); status.length = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.available_physical), int(status.total_physical)
            return None, None
        page = os.sysconf('SC_PAGE_SIZE')
        return int(os.sysconf('SC_AVPHYS_PAGES') * page), int(os.sysconf('SC_PHYS_PAGES') * page)
    except (AttributeError, OSError, ValueError):
        return None, None


def memory_reserve(total):
    return max(MEMORY_RESERVE_MIN, int((total or 0) * MEMORY_RESERVE_FRACTION))


def usable_memory():
    """Bytes a run may still take before the guard stops it (None when unknown)."""
    available, total = memory()
    return None if available is None else max(0, available - memory_reserve(total))


def disk_free(path):
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return None


def describe(nbytes):
    return f'{nbytes / GiB:.1f} GB' if nbytes >= GiB else f'{nbytes / MiB:.0f} MB'
