'''Native window draft by DeepSeek, integrated and reviewed by Codex.'''
from html import escape
from pathlib import Path
import os
import traceback

ROOT=Path(__file__).resolve().parent
ERROR_LOG=ROOT/'data'/'desktop-error.log'

_PAGE_STYLE=(
    '<style>'
    ':root{color-scheme:light}'
    'html,body{height:100%;margin:0}'
    'body{display:flex;align-items:center;justify-content:center;background:#f5f7fb;color:#172033;font-family:Segoe UI,Arial,sans-serif}'
    'main{max-width:620px;padding:40px;text-align:center}'
    'h1{margin:0 0 14px;font-size:28px;font-weight:600}'
    'p{margin:10px 0;color:#4b5563;font-size:16px;line-height:1.55}'
    '.error{color:#9f1239;white-space:pre-wrap;overflow-wrap:anywhere}'
    'code{display:inline-block;max-width:100%;padding:8px 10px;background:#e8edf5;border-radius:6px;color:#172033;font-size:14px;overflow-wrap:anywhere}'
    '</style>'
)
_LOADING_PAGE=(
    '<!doctype html><html lang=en><head><meta charset=utf-8>'
    '<title>Kerr Studio</title>'
    + _PAGE_STYLE +
    '</head><body><main>'
    '<h1>Starting local service…</h1>'
    '<p>Kerr Studio is preparing the local acquisition service. This window will update automatically.</p>'
    '</main></body></html>'
)


def _error_page(exc):
    message=escape(str(exc))
    log_path=escape(str(ERROR_LOG))
    return (
        '<!doctype html><html lang=en><head><meta charset=utf-8>'
        '<title>Kerr Studio</title>'
        + _PAGE_STYLE +
        '</head><body><main>'
        '<h1>Could not start the local service</h1>'
        f'<p class=error>{message}</p>'
        '<p>Details were written to:</p>'
        f'<code>{log_path}</code>'
        '<p>Close this window and try again.</p>'
        '</main></body></html>'
    )


def _write_log(message,exc):
    try:
        ERROR_LOG.parent.mkdir(parents=True,exist_ok=True)
        with ERROR_LOG.open('a',encoding='utf-8') as stream:
            stream.write(os.linesep)
            stream.write(message)
            stream.write(os.linesep)
            traceback.print_exception(type(exc),exc,exc.__traceback__,file=stream)
    except Exception:
        pass


def create_desktop(url,on_closing=None,on_started=None):
    '''Block in the native GUI. Closing callback returns False to cancel.'''
    import webview
    # Blob/anchor CSV exports need an explicit native Save As dialog in WebView2.
    webview.settings['ALLOW_DOWNLOADS']=True
    storage=ROOT/'data'/'webview'
    storage.mkdir(parents=True,exist_ok=True)
    loading=url is None
    window=webview.create_window(
        'Kerr Studio · TRPL',url=url,html=_LOADING_PAGE if loading else None,width=1440,height=940,min_size=(1000,700),
        resizable=True,frameless=False,easy_drag=False,
        background_color='#f5f7fb' if loading else '#0b1220',text_select=True,
    )
    if on_closing is not None:
        def closing():
            return bool(on_closing(window))
        window.events.closing+=closing
    def run_started():
        on_started(window)
    webview.start(func=run_started if on_started is not None else None,gui='edgechromium',
                  debug=False,private_mode=True,storage_path=str(storage))
    return 0


def main():
    import threading
    from desktop_service import LocalService
    service=LocalService()
    startup_started=threading.Event()
    startup_finished=threading.Event()
    startup_failed=threading.Event()
    gui_closed=threading.Event()

    def bootstrap(window):
        startup_started.set()
        try:
            if gui_closed.is_set():
                return
            service.__enter__()
            if gui_closed.is_set():
                return
            window.load_url(service.url)
        except Exception as exc:
            startup_failed.set()
            _write_log('Local service startup failed',exc)
            if not gui_closed.is_set():
                try:
                    window.load_html(_error_page(exc))
                except Exception:
                    pass
        finally:
            startup_finished.set()

    def closing_window(window):
        if not startup_finished.is_set():
            return False
        if startup_failed.is_set():
            return True
        return service.confirm_close(window)

    try:
        return create_desktop(None,on_closing=closing_window,on_started=bootstrap)
    finally:
        gui_closed.set()
        if startup_started.is_set():
            startup_finished.wait()
        service.close()


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        _write_log('Desktop launch failed',exc)
        if os.name=='nt':
            import ctypes
            try:
                ctypes.windll.user32.MessageBoxW(None,f'{exc}{os.linesep}{os.linesep}See data/desktop-error.log','Kerr Studio could not start',0x10)
            except Exception:
                pass
        raise SystemExit(1)
