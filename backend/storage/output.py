"""Explicitly confirmed replacement of one experiment's output files."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import shutil
from ..errors import CommandError


def target_path(path,run_id='experiment'):
    target=Path(path).expanduser() if path else Path(__file__).resolve().parents[2]/'data'/f'{run_id}.h5'
    if target.suffix.lower()!='.h5':raise ValueError('Output filename must end in .h5')
    if target.is_symlink():raise ValueError('Choose a regular output file instead of a symbolic link')
    return target.resolve()


def output_info(path,run_id='experiment'):
    target=target_path(path,run_id)
    family=[target,target.with_suffix('.partial.h5'),target.with_suffix('.stopped.h5'),target.with_suffix('.events.jsonl')]
    versions=[]
    for file in family:
        try:stat=file.stat()
        except FileNotFoundError:continue
        if not file.is_file() or file.is_symlink():raise ValueError('An output path is not a regular file')
        versions.append((str(file),stat.st_size,stat.st_mtime_ns,stat.st_ino))
    token=hashlib.sha256(json.dumps(versions).encode()).hexdigest() if versions else None
    return dict(path=str(target),conflicts=[v[0] for v in versions],overwrite_token=token)


@contextmanager
def prepare_output(path,run_id,estimate=0,overwrite_token=None):
    info=output_info(path,run_id);target=Path(info['path'])
    if info['conflicts'] and (not overwrite_token or overwrite_token!=info['overwrite_token']):
        raise CommandError('Output file exists or changed; confirm overwrite before continuing')
    target.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target.parent).free<estimate+20*1024**2:raise ValueError('Insufficient free disk space')
    if info['conflicts']:
        if output_info(path,run_id)['overwrite_token']!=overwrite_token:
            raise CommandError('Output files changed; confirm overwrite again')
        for name in info['conflicts']:Path(name).unlink()
    yield target
