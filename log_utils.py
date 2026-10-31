"""Bound log files without logging tokens or changing application state."""
from pathlib import Path
from threading import RLock
import os
import stat

LIMIT=2*1024*1024
def rotate_file(path,limit=LIMIT):
    path=Path(path)
    if path.exists() and path.stat().st_size>=limit:
        try:path.replace(path.with_suffix(path.suffix+'.1'))
        except OSError:pass

class CappedStream:
    def __init__(self,stream,limit=LIMIT):self.stream=stream;self.limit=limit;self.lock=RLock()
    def write(self,text):
        with self.lock:
            try:
                info=os.fstat(self.stream.fileno())
                if stat.S_ISREG(info.st_mode) and info.st_size>=self.limit:
                    self.stream.seek(0);self.stream.truncate(0)
            except (OSError,ValueError,AttributeError):pass
            return self.stream.write(text)
    def flush(self):return self.stream.flush()
    def __getattr__(self,name):return getattr(self.stream,name)
