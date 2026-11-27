'''Save a raw CCD frame (counts) for use outside Kerr Studio: CSV for spreadsheets and plotting programs, or an
uncompressed 16-bit grayscale TIFF for image programs (ImageJ / Fiji and the like).'''
import json
import struct
from pathlib import Path

import numpy as np

# Frame facts written beside the counts.
HEADER_KEYS = ('frame_id', 'captured_utc', 'source_mode', 'source', 'readout', 'exposure_s', 'shape', 'full_scale', 'spectrograph')


def describe(frame):
    return {key: frame.get(key) for key in HEADER_KEYS if frame.get(key) is not None}


def write_frame(path, image, frame, tiff=False):
    '''Write `image` (rows x columns of counts) to `path`; returns the path written.'''
    image = np.asarray(image)
    if image.ndim != 2:
        raise ValueError('A CCD frame has rows and columns')
    path = Path(path)
    if tiff:
        if path.suffix.lower() not in ('.tif', '.tiff'):
            path = path.with_suffix('.tif')
        _write_tiff(path, image, json.dumps(describe(frame), default=str))
    else:
        if path.suffix.lower() != '.csv':
            path = path.with_suffix('.csv')
        _write_csv(path, image, describe(frame))
    return path


def _write_csv(path, image, info):
    lines = ['# Kerr Studio CCD frame: raw counts, one CCD row per line, columns = CCD pixels 0…%d' % (image.shape[1] - 1)]
    lines += [f'# {key}: {json.dumps(value, default=str)}' for key, value in info.items()]
    body = '\n'.join(','.join(str(int(v)) for v in row) for row in image)
    path.write_text('\n'.join(lines) + '\n' + body + '\n', encoding='utf-8')


def _write_tiff(path, image, description):
    '''Baseline TIFF, one strip, 16-bit unsigned grayscale, little-endian; counts above 65535 are clipped.'''
    data = np.clip(np.rint(image.astype(np.float64)), 0, 65535).astype('<u2')
    rows, columns = data.shape
    text = (description + '\0').encode('ascii', 'replace')
    entries = 11
    ifd_offset = 8
    ifd_size = 2 + entries * 12 + 4
    text_offset = ifd_offset + ifd_size
    image_offset = text_offset + len(text) + (len(text) % 2)
    def entry(tag, kind, count, value):
        # kind 3 = SHORT, 4 = LONG, 2 = ASCII; values of 4 bytes or less sit in the entry itself
        if kind == 3 and count == 1:
            return struct.pack('<HHIHH', tag, kind, count, value, 0)
        return struct.pack('<HHII', tag, kind, count, value)
    ifd = struct.pack('<H', entries) + b''.join((
        entry(256, 4, 1, columns),            # ImageWidth
        entry(257, 4, 1, rows),               # ImageLength
        entry(258, 3, 1, 16),                 # BitsPerSample
        entry(259, 3, 1, 1),                  # Compression: none
        entry(262, 3, 1, 1),                  # PhotometricInterpretation: black is zero
        entry(270, 2, len(text), text_offset),  # ImageDescription: the frame's settings
        entry(273, 4, 1, image_offset),       # StripOffsets
        entry(277, 3, 1, 1),                  # SamplesPerPixel
        entry(278, 4, 1, rows),               # RowsPerStrip
        entry(279, 4, 1, data.nbytes),        # StripByteCounts
        entry(339, 3, 1, 1),                  # SampleFormat: unsigned integer
    )) + struct.pack('<I', 0)
    with open(path, 'wb') as stream:
        stream.write(b'II*\0' + struct.pack('<I', ifd_offset))
        stream.write(ifd)
        stream.write(text + (b'\0' if len(text) % 2 else b''))
        stream.write(data.tobytes())
