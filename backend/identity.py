"""Local checkout/build identity; never exposes a filesystem path or credential."""
from pathlib import Path
from functools import lru_cache
import hashlib
import os

@lru_cache(maxsize=8)
def identity(root=None):
    root=Path(root or Path(__file__).resolve().parents[1]).resolve()
    digest=hashlib.sha256()
    files=[root/'index.html']+list((root/'backend').rglob('*.py'))+list((root/'frontend').glob('*.js'))
    for file in sorted(files):
        if '__pycache__' not in file.parts and file.is_file():
            digest.update(file.relative_to(root).as_posix().encode());digest.update(file.read_bytes())
    return dict(application='kerr-studio',protocol_version=2,software_version='0.1.1',
                checkout_id=hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest(),build_id=digest.hexdigest())
