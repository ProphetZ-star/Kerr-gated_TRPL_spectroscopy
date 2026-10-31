"""One native window per checkout; a second launch activates the existing process."""
from pathlib import Path
import os

class DesktopInstance:
    def __init__(self,root):self.path=Path(root)/'data'/'.desktop-instance.lock';self.handle=None;self.pid=None
    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.handle=self.path.open('a+b')
        self.handle.seek(0)
        if self.path.stat().st_size==0:self.handle.write(b'0');self.handle.flush();self.handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.handle.seek(1)
            try:self.pid=int(self.handle.read().decode() or '0')
            except ValueError:self.pid=None
            self.handle.close();self.handle=None;self.activate();return False
        self.handle.seek(1);self.handle.truncate();self.handle.write(str(os.getpid()).encode());self.handle.flush();return True
    def activate(self):
        if os.name!='nt' or not self.pid:return
        import ctypes
        from ctypes import wintypes
        user=ctypes.windll.user32
        callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        def visit(hwnd,param):
            pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value==self.pid and user.IsWindowVisible(hwnd):
                user.ShowWindow(hwnd,9);user.SetForegroundWindow(hwnd);return False
            return True
        user.EnumWindows(callback(visit),0)
    def close(self):
        if self.handle:self.handle.close();self.handle=None
