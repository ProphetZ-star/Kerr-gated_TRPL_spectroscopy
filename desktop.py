'''Native Kerr Studio desktop entry point. QApplication is created only in main guard.'''
import multiprocessing
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _log_exception(root, message=None):
    try:
        data = root / 'data'
        data.mkdir(parents=True, exist_ok=True)
        with open(data / 'desktop-error.log', 'a', encoding='utf-8') as stream:
            stream.write(datetime.now().isoformat() + ' ')
            if message:
                stream.write(str(message) + ' ')
            stream.write(traceback.format_exc())
            stream.write('\n')
    except Exception:
        pass


def _instance(root):
    try:
        from desktop_instance import DesktopInstance
    except Exception:
        try:
            from qt_frontend.desktop_instance import DesktopInstance
        except Exception:
            return None, True
    helper = DesktopInstance(root)
    if hasattr(helper, 'acquire') and callable(getattr(helper, 'acquire')):
        try:
            ok = helper.acquire()
        except Exception:
            _log_exception(root, 'single-instance acquire')
            return helper, False
        return helper, ok is not False
    enter = getattr(helper, '__enter__', None)
    if callable(enter):
        try:
            ok = enter()
        except Exception:
            _log_exception(root, 'single-instance enter')
            return helper, False
        return helper, ok is not False
    return helper, True


def _release(helper):
    if helper is None:
        return
    try:
        close = getattr(helper, 'close', None)
        if callable(close):
            close()
            return
        release = getattr(helper, 'release', None)
        if callable(release):
            release()
            return
        exit_method = getattr(helper, '__exit__', None)
        if callable(exit_method):
            exit_method(None, None, None)
    except Exception:
        pass


def main():
    multiprocessing.freeze_support()
    root = Path(__file__).resolve().parent
    os.chdir(root)
    helper, acquired = _instance(root)
    if not acquired:
        return 0
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
        from qt_frontend.theme import apply_to_app
        from qt_frontend.window import MainWindow
        app = QApplication(sys.argv)
        app.setApplicationName('Kerr Studio')
        app.setApplicationDisplayName('Kerr Studio · TRPL Acquisition')
        apply_to_app('light')
        icon_path = root / 'assets' / 'TRPL.ico'
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
        window = MainWindow(auto_start=False)
        if icon_path.exists():
            window.setWindowIcon(QIcon(str(icon_path)))
        window.show()
        window.start_backend()
        result = int(app.exec())
        window.controller.bridge.wait(15000)
        return result
    except Exception:
        _log_exception(root)
        return 1
    finally:
        _release(helper)


if __name__ == '__main__':
    sys.exit(main())
