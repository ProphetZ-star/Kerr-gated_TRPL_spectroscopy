"""Local server ownership and safe exit policy for the native desktop host."""
from pathlib import Path
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parent


class LocalService:
    def __init__(self,port=8765):
        if not isinstance(port,int) or not 1024<=port<=65535:
            raise ValueError('Invalid local service port')
        self.url=f'http://127.0.0.1:{port}'
        self.port=port;self.process=None;self.log=None;self.token=None
        self.stop_file=ROOT/'data'/f'.desktop-stop-{uuid.uuid4().hex}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def owned(self):
        return self.process is not None

    def request(self,path,body=None,timeout=3):
        headers={'Content-Type':'application/json'}
        if body is not None:
            if self.token is None:self.token=self.request('/api/v1/session')['token']
            headers['X-Session-Token']=self.token
        request=urllib.request.Request(self.url+path,headers=headers,
                    data=json.dumps(body).encode() if body is not None else None)
        with self.opener.open(request,timeout=timeout) as response:
            return json.load(response)

    def existing(self):
        try:info=self.request('/openapi.json',timeout=1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'Port {self.port} is occupied by another application') from exc
        except (urllib.error.URLError,TimeoutError,OSError):
            return False
        if info.get('info',{}).get('title')!='Kerr Studio local acquisition':
            raise RuntimeError(f'Port {self.port} is occupied by another application')
        self.request('/api/v1/status')
        return True

    def __enter__(self):
        if self.existing():return self
        self.stop_file.parent.mkdir(parents=True,exist_ok=True)
        self.log=(ROOT/'data'/'desktop-service.log').open('a',encoding='utf-8')
        self.process=subprocess.Popen(
            [sys.executable,'-m','backend','--no-browser','--port',str(self.port),
             '--shutdown-file',str(self.stop_file)],cwd=ROOT,stdin=subprocess.DEVNULL,
            stdout=self.log,stderr=self.log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
        )
        try:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Local backend did not start. See data/desktop-service.log; another project service may already be running.')
                if self.existing():return self
                time.sleep(.15)
            raise RuntimeError('Timed out starting the local backend; see data/desktop-service.log')
        except BaseException:
            self.close()
            raise

    def confirm_close(self,window):
        # An externally launched service keeps running, including acquisition and memory data.
        if not self.owned:return True
        try:
            status=self.request('/api/v1/status')
            run=status.get('run')
            unsaved=bool(run and run['committed_frames'] and not run['saved'])
            if status.get('busy') or unsaved:
                message=('Exit Kerr Studio? Active acquisition will stop and completed autosave frames will be retained. '
                         'Any memory-only experiment will be lost. Cancel to save it first.')
                if not window.create_confirmation_dialog('Exit Kerr Studio',message):return False
            if status.get('busy')=='scan':
                self.request(f"/api/v1/runs/{run['run_id']}/stop",{})
            elif status.get('busy')=='preview':self.request('/api/v1/preview/stop',{})
            elif status.get('busy')=='move':self.request('/api/v1/stage/stop',{})
            deadline=time.monotonic()+15
            while status.get('busy') and time.monotonic()<deadline:
                time.sleep(.1);status=self.request('/api/v1/status')
            if status.get('busy'):
                window.create_confirmation_dialog('Operation still stopping','The backend is still finalizing. Please try closing again after it completes.')
                return False
            return True
        except Exception as exc:
            return window.create_confirmation_dialog('Backend unavailable',f'Backend status could not be checked: {exc}. Exit the window?')

    def close(self):
        if self.process is None:return
        try:
            if self.process.poll() is None:
                self.stop_file.touch()
                try:self.process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    # Only the process created by this host is ever terminated.
                    self.log.write('\nERROR: graceful shutdown timed out; terminating owned simulation server.\n');self.log.flush()
                    self.process.terminate();self.process.wait(timeout=5)
        finally:
            self.process=None
            if self.log:self.log.close();self.log=None
            self.stop_file.unlink(missing_ok=True)

    def __exit__(self,*exc):
        self.close()
