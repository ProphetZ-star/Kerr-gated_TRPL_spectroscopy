"""Application-scoped pythonnet loader, shared by setup, quick probe and desktop.

Windows may classify a copied installation directory as Internet even without a
Zone.Identifier stream. A real app-private Python host reads our CLR config; no
shared Python executable, machine.config, registry or system policy is changed.
"""
from pathlib import Path
import importlib
import importlib.util
import os
import struct
import sys
import traceback

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'desktop-runtime.config'
_ready=False


def _private_host_dir():return ROOT/'.venv'/'DesktopRuntime'


def on_private_host():
    return Path(sys.executable).resolve().parent==_private_host_dir().resolve()


def _copy_if_changed(source,destination):
    import hashlib
    import shutil
    if destination.is_symlink() or destination.exists() and destination.stat().st_nlink>1:
        raise RuntimeError('The private desktop runtime contains a shared file link. Recreate .venv/DesktopRuntime inside this software folder.')
    if destination.is_file() and source.stat().st_size==destination.stat().st_size:
        if hashlib.sha256(source.read_bytes()).digest()==hashlib.sha256(destination.read_bytes()).digest():return
    try:shutil.copyfile(source,destination)
    except PermissionError as exc:raise RuntimeError('Close Kerr Studio before updating its private desktop runtime.') from exc


def prepare_desktop_host():
    if os.name!='nt':raise RuntimeError('The private desktop host is Windows-only')
    if not CONFIG.is_file():raise RuntimeError('Missing desktop-runtime.config. Copy the complete updated TRPL package.')
    environment=ROOT/'.venv'
    if environment.resolve()==ROOT or not environment.resolve().is_relative_to(ROOT):
        raise RuntimeError('The desktop environment must be inside this software folder; a shared external environment is left unchanged.')
    if Path(sys.prefix).resolve()!=environment.resolve():
        raise RuntimeError('Use this package\'s start-app.cmd or TRPL.exe to prepare the desktop runtime.')
    base=Path(sys.base_prefix).resolve();host=_private_host_dir()
    if not host.resolve().is_relative_to(environment.resolve()):
        raise RuntimeError('The private desktop runtime points outside this software environment and was left unchanged.')
    files=['python.exe','pythonw.exe',f'python{sys.version_info.major}{sys.version_info.minor}.dll']
    if not all((base/name).is_file() for name in files):raise RuntimeError('The base Python installation is missing its desktop executable or runtime DLL.')
    host.mkdir(parents=True,exist_ok=True)
    files+=['python3.dll']+[p.name for p in base.glob('vcruntime*.dll')]
    for name in dict.fromkeys(files):
        if (base/name).is_file():_copy_if_changed(base/name,host/name)
    for name in ('python.exe.config','pythonw.exe.config'):_copy_if_changed(CONFIG,host/name)
    return dict(python=str(host/'python.exe'),pythonw=str(host/'pythonw.exe'))


def ensure_desktop_runtime():
    global _ready
    if _ready or os.name!='nt':return
    if not on_private_host():
        raise RuntimeError('The desktop runtime must start through TRPL.exe or start-app.cmd. The shared Python installation is left unchanged.')
    import pythonnet
    # The real, app-private executable reads its adjacent .config for the CLR
    # root domain. A venv redirector would instead use the shared base config.
    pythonnet.load('netfx')
    import clr
    _ready=True


def launch_desktop_if_needed(script):
    if os.name!='nt' or on_private_host():return None
    import subprocess
    host=prepare_desktop_host()
    executable=host['pythonw'] if Path(sys.executable).name.lower()=='pythonw.exe' else host['python']
    return subprocess.call([executable,'-B',str(script),*sys.argv[1:]],cwd=ROOT,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def check_managed_components():
    """Load desktop bridge assemblies only; no window or acquisition is created."""
    if os.name!='nt':return
    ensure_desktop_runtime()
    import clr
    clr.AddReference('System.Windows.Forms')
    spec=importlib.util.find_spec('webview')
    if spec is None:raise ModuleNotFoundError('webview')
    libraries=Path(spec.origin).parent/'lib'
    for name in ('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll'):
        library=libraries/name
        if not library.is_file():raise RuntimeError('Incomplete pywebview installation: '+name)
        clr.AddReference(str(library))


def verify(modules,probe=False):
    if sys.version_info<(3,12) or struct.calcsize('P')!=8:
        raise RuntimeError('TRPL requires 64-bit Python 3.12 or newer')
    missing=[name for name in modules if importlib.util.find_spec(name) is None]
    if missing:raise ModuleNotFoundError('Missing dependencies: '+', '.join(missing))
    if 'clr' in modules:
        if not probe:print('Initializing the application-scoped desktop runtime',flush=True)
        ensure_desktop_runtime()
    if not probe:
        for name in modules:
            print('Checking dependency: '+name,flush=True)
            try:importlib.import_module(name)
            except Exception as exc:raise RuntimeError(f'Dependency {name} could not load: {exc}') from exc
    if 'clr' in modules and 'webview' in modules:
        if not probe:print('Checking WinForms and WebView2 bridge assemblies',flush=True)
        check_managed_components()
    if not probe:print('Dependency and desktop-component checks passed',flush=True)


if __name__=='__main__':
    try:
        modes={'--probe','--verify','--probe-internal','--verify-internal'}
        if len(sys.argv)!=3 or sys.argv[1] not in modes:
            raise ValueError('Usage: desktop_runtime.py --probe|--verify comma-separated-modules')
        mode=sys.argv[1]
        if os.name=='nt' and not mode.endswith('-internal'):
            import subprocess
            host=prepare_desktop_host()
            raise SystemExit(subprocess.call([host['python'],'-B',str(Path(__file__).resolve()),mode+'-internal',sys.argv[2]],cwd=ROOT,
                stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        verify(sys.argv[2].split(','),probe=mode.startswith('--probe'))
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
