#!/usr/bin/env python3
'''Headless dependency probe for the kerr-trpl-qt Windows source package.'''
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata as metadata
import struct
import sys
from pathlib import Path

MODULES = (
    ('numpy', 'numpy'),
    ('h5py', 'h5py'),
    ('pydantic', 'pydantic'),
    ('serial', 'pyserial'),
    ('pipython', 'PIPython'),
    ('usb', 'pyusb'),
    ('PySide6.QtCore', 'PySide6-Essentials'),
    ('pyqtgraph', 'pyqtgraph'),
)

def fail(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code

def check_python() -> int:
    if sys.version_info < (3, 12) or struct.calcsize('P') != 8:
        return fail('Python 3.12 64-bit is required.', 2)
    return 0

def check_marker(requirements: str | None, marker: str | None) -> int:
    if not requirements or not marker:
        return 0
    req = Path(requirements)
    mark = Path(marker)
    if not req.is_file():
        return fail(f'Requirements file is missing: {req}', 4)
    if not mark.is_file():
        return fail(f'Requirements marker is missing: {mark}', 4)
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    if mark.read_text(encoding='utf-8').strip() != digest:
        return fail('Requirements changed; setup must run again.', 4)
    return 0

def check_imports(full: bool) -> int:
    # Every runtime dependency is imported; `full` is kept for older setup scripts.
    for module, distribution in MODULES:
        try:
            importlib.import_module(module)
        except Exception as error:
            return fail(f'Import failed: {module} ({distribution}): {error}', 3)
        try:
            metadata.version(distribution)
        except metadata.PackageNotFoundError:
            return fail(f'Package metadata is missing: {distribution}', 3)
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description='Headless TRPL Qt dependency probe')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--requirements')
    parser.add_argument('--marker')
    args = parser.parse_args()
    code = check_python()
    if code:
        return code
    code = check_marker(args.requirements, args.marker)
    if code:
        return code
    return check_imports(args.full)

if __name__ == '__main__':
    raise SystemExit(main())
