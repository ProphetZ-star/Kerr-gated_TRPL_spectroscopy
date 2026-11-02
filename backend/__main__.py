import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path
import uvicorn
from .identity import identity

def port_value(value):
    try:port=int(value)
    except (TypeError,ValueError) as exc:raise argparse.ArgumentTypeError('Port must be an integer') from exc
    if not 1024<=port<=65535:raise argparse.ArgumentTypeError('Port must be from 1024 to 65535')
    return port

def parent_monitor(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.windll.kernel32
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x100000,False,pid)
        return (lambda:bool(handle) and kernel.WaitForSingleObject(handle,0)==258,
                lambda:kernel.CloseHandle(handle) if handle else None)
    def alive():
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
        except PermissionError:return True
    return alive,lambda:None

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Kerr Studio desktop local acquisition service')
    parser.add_argument('--port',type=port_value,default=8765)
    parser.add_argument('--no-browser',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--shutdown-file',help='Internal desktop-host shutdown signal path')
    parser.add_argument('--parent-pid',type=int,help='Internal parent process watchdog')
    parser.add_argument('--launch-id',help='Internal launcher ownership identifier; not an authentication token')
    args=parser.parse_args()
    if args.launch_id is not None and not re.fullmatch(r'[0-9a-f]{32}',args.launch_id):
        parser.error('Launch identifier must be 32 hexadecimal characters')
    if args.parent_pid is not None and args.parent_pid<=0:parser.error('Parent PID must be positive')
    stop_path=None;nonce=None
    if args.shutdown_file:
        stop_path=Path(args.shutdown_file).resolve()
        expected_dir=Path(__file__).resolve().parents[1]/'data'
        if stop_path.parent!=expected_dir or not re.fullmatch(r'\.desktop-stop-[0-9a-f]{32}',stop_path.name):
            parser.error('Shutdown signal must be a unique desktop stop file in the project data directory')
        nonce=stop_path.name.removeprefix('.desktop-stop-')
    from log_utils import CappedStream
    if sys.stderr is None:
        log_dir=Path(__file__).resolve().parents[1]/'data';log_dir.mkdir(exist_ok=True)
        sys.stderr=(log_dir/'desktop-service.log').open('a',encoding='utf-8')
    sys.stderr=CappedStream(sys.stderr)
    if sys.stdout is not None:sys.stdout=CappedStream(sys.stdout)
    from .app import app
    app.state.launch_id=args.launch_id
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,workers=1,access_log=False))
    app.state.request_shutdown=lambda:setattr(server,'should_exit',True)
    alive,release=parent_monitor(args.parent_pid) if args.parent_pid else (lambda:True,lambda:None)
    def monitor_shutdown():
        while not server.should_exit:
            requested=False
            if stop_path is not None:
                try:requested=stop_path.read_text(encoding='ascii')==nonce
                except (OSError,UnicodeError):pass
            if requested or not alive():server.should_exit=True;return
            time.sleep(.15)
    threading.Thread(target=monitor_shutdown,daemon=True).start()
    try:server.run()
    finally:release()
