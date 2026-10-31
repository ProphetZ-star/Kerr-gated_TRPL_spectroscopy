'''Open Kerr Studio in a web browser; an owned local service stays alive.'''
from pathlib import Path
import argparse
import os
import traceback
import webbrowser
import sys
from log_utils import rotate_file,CappedStream
SHOW_ERROR_DIALOG=True

ROOT=Path(__file__).resolve().parent
ERROR_LOG=ROOT/'data'/'browser-error.log'


def _report_error(exc):
    try:
        ERROR_LOG.parent.mkdir(parents=True,exist_ok=True)
        rotate_file(ERROR_LOG)
        with ERROR_LOG.open('a',encoding='utf-8') as stream:
            stream=CappedStream(stream)
            stream.write(os.linesep)
            stream.write(f'Browser launch failed: {exc}')
            stream.write(os.linesep)
            traceback.print_exception(type(exc),exc,exc.__traceback__,file=stream)
    except Exception:
        pass
    if SHOW_ERROR_DIALOG and os.name=='nt':
        import ctypes
        try:
            ctypes.windll.user32.MessageBoxW(
                None,f'{exc}{os.linesep}{os.linesep}See data/browser-error.log',
                'Kerr Studio could not start',0x10)
        except Exception:
            pass


def main():
    global SHOW_ERROR_DIALOG
    parser=argparse.ArgumentParser(description='Open the Kerr Studio local service in a browser.')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--stop',action='store_true',help='Stop an idle local service after its experiments are saved')
    args=parser.parse_args()
    SHOW_ERROR_DIALOG=not args.no_browser and not args.stop
    from desktop_service import LocalService
    if args.stop:
        service=LocalService(args.port)
        if not service.existing():raise RuntimeError('No matching Kerr service is running')
        service.request('/api/v1/service/stop',{})
        return 0
    with LocalService(args.port) as service:
        if not args.no_browser and not webbrowser.open(service.url):
            raise RuntimeError(f'Could not open a web browser for {service.url}')
        if service.owned:
            returncode=service.process.wait()
            if returncode:
                raise RuntimeError(f'Local service exited with code {returncode}. See data/desktop-service.log')
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:
        _report_error(exc)
        if not SHOW_ERROR_DIALOG:print(str(exc),file=sys.stderr)
        raise SystemExit(1)
