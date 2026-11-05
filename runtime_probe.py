#!/usr/bin/env python3
'''Headless dependency probe for the kerr-trpl-qt Windows source package.'''
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata as metadata
import json
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


def _verification_key(requirements):
    req = Path(requirements)
    versions = {}
    for line in req.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):continue
        name, expected = line.split('==', 1)
        actual = metadata.version(name)
        if actual != expected:
            raise ValueError(f'{name}: expected {expected}, installed {actual}')
        versions[name] = actual
    return dict(python=sys.version, executable=str(Path(sys.executable).resolve()), versions=versions,
                requirements=hashlib.sha256(req.read_bytes()).hexdigest(),
                probe=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def _receipt_paths(requirements):
    environment = Path(requirements).resolve().parent / '.venv'
    return environment / '.runtime-verified.json', environment / '.runtime-startup-failed'


def quick_verified(requirements):
    """Reuse a successful import check only while the interpreter and dependency files are unchanged."""
    if not requirements:return False
    receipt, failed = _receipt_paths(requirements)
    try:
        if failed.exists():return False
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if saved['key'] != _verification_key(requirements):return False
        if not saved['files']:return False
        for name, size, modified in saved['files']:
            stat = Path(name).stat()
            if (stat.st_size, stat.st_mtime_ns) != (size, modified):return False
        return True
    except (OSError, ValueError, KeyError, TypeError, metadata.PackageNotFoundError):
        return False


def save_verification(requirements):
    if not requirements:return
    receipt, failed = _receipt_paths(requirements)
    try:
        key = _verification_key(requirements)
        files = set()
        for name in key['versions']:
            distribution = metadata.distribution(name)
            for item in distribution.files or []:
                # Include native libraries/plugins and Python runtime code, but not bundled examples/tests.
                if any(part in {'tests','test','examples','__pycache__'} for part in item.parts):continue
                if item.suffix.lower() in {'.py','.pyd','.dll'} or item.name in {'METADATA','RECORD'}:
                    files.add(Path(distribution.locate_file(item)).resolve())
        fingerprints = []
        for file in sorted(files):
            stat = file.stat(); fingerprints.append([str(file), stat.st_size, stat.st_mtime_ns])
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(key=key,files=fingerprints)),encoding='utf-8')
        temporary.replace(receipt)
        failed.unlink(missing_ok=True)
    except (OSError, ValueError, metadata.PackageNotFoundError):
        pass  # A receipt is an optimization, never a prerequisite for a verified launch.

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
    parser.add_argument('--quick', action='store_true', help='Reuse unchanged dependencies; fall back to full verification')
    parser.add_argument('--requirements')
    parser.add_argument('--marker')
    args = parser.parse_args()
    code = check_python()
    if code:
        return code
    code = check_marker(args.requirements, args.marker)
    if code:
        return code
    if args.quick and not args.full and quick_verified(args.requirements):return 0
    if args.requirements:
        try:_verification_key(args.requirements)
        except (OSError, ValueError, metadata.PackageNotFoundError) as error:return fail(str(error),3)
    code = check_imports(True)
    if code == 0:save_verification(args.requirements)
    return code

if __name__ == '__main__':
    raise SystemExit(main())
