import argparse
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
import uvicorn


def open_when_ready(url):
    for _ in range(100):
        try:
            with urllib.request.urlopen(url+'/api/v1/status',timeout=.5) as response:
                if response.status==200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(.3)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Kerr Studio simulation server')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--shutdown-file',help='Internal desktop-host shutdown signal path')
    args=parser.parse_args()
    if not args.no_browser:
        threading.Thread(target=open_when_ready,args=(f'http://127.0.0.1:{args.port}',),daemon=True).start()
    server=uvicorn.Server(uvicorn.Config('backend.app:app',host='127.0.0.1',port=args.port,workers=1))
    if args.shutdown_file:
        stop_path=Path(args.shutdown_file).resolve()
        expected_dir=Path(__file__).resolve().parents[1]/'data'
        if stop_path.parent!=expected_dir or not stop_path.name.startswith('.desktop-stop-'):
            parser.error('Shutdown signal must be a desktop stop file in project data directory')
        def monitor_shutdown():
            while not server.should_exit:
                if stop_path.exists():
                    server.should_exit=True
                    return
                time.sleep(.15)
        threading.Thread(target=monitor_shutdown,daemon=True).start()
    server.run()
