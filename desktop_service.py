"""Local service identity, cancellable startup, ownership and safe exit."""
from pathlib import Path
from threading import Event, RLock
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from backend.identity import identity
from log_utils import rotate_file

ROOT=Path(__file__).resolve().parent

class LocalService:
    def __init__(self,port=8765):
        if isinstance(port,bool) or not isinstance(port,int) or not 1024<=port<=65535:
            raise ValueError('Invalid local service port; use 1024–65535')
        self.url=f'http://127.0.0.1:{port}'
        self.port=port;self.process=None;self.log=None;self.token=None;self.server_pid=None
        self.launch_id=uuid.uuid4().hex;self.server_launch_id=None
        self.stop_file=ROOT/'data'/f'.desktop-stop-{uuid.uuid4().hex}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.cancel_startup=Event();self.close_lock=RLock()

    @property
    def owned(self):
        return self.process is not None

    def request(self,path,body=None,timeout=3,_retry=True):
        headers={'Content-Type':'application/json'}
        if body is not None:
            if self.token is None:self.token=self.request('/api/v1/session')['token']
            headers['X-Session-Token']=self.token
        request=urllib.request.Request(self.url+path,headers=headers,
                    data=json.dumps(body,allow_nan=False).encode() if body is not None else None)
        try:
            with self.opener.open(request,timeout=timeout) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            if body is not None and exc.code==403 and _retry:
                self.token=None;return self.request(path,body,timeout,False)
            raise

    def retry_status(self,seconds=15):
        deadline=time.monotonic()+seconds
        while True:
            try:return self.request('/api/v1/status',timeout=min(3,max(.1,deadline-time.monotonic())))
            except (urllib.error.URLError,TimeoutError,OSError) as exc:
                if isinstance(exc,urllib.error.HTTPError) and exc.code not in {502,503,504}:raise
                if time.monotonic()>=deadline:raise RuntimeError('Backend is still busy or writing to disk. Wait for it to finish, then try again.') from exc
                time.sleep(.1)

    def existing(self):
        try:info=self.request('/api/v1/identity',timeout=1)
        except urllib.error.HTTPError as exc:
            if exc.code in {502,503,504}:return False
            raise RuntimeError(f'Port {self.port} is occupied by another application or an older Kerr backend. Stop that service first.') from exc
        except (urllib.error.URLError,TimeoutError,OSError):return False
        expected=identity(ROOT)
        if any(info.get(key)!=expected[key] for key in ('application','protocol_version','checkout_id','build_id')):
            raise RuntimeError(f'Port {self.port} belongs to another Kerr checkout or version. Stop that service or choose another port.')
        self.server_pid=info.get('process_id')
        self.server_launch_id=info.get('launch_id')
        return True

    def _spawn(self):
        self.stop_file.parent.mkdir(parents=True,exist_ok=True)
        log_path=ROOT/'data'/'desktop-service.log';rotate_file(log_path)
        self.log=log_path.open('a',encoding='utf-8')
        try:
            self.process=subprocess.Popen(
                [sys.executable,'-m','backend','--no-browser','--port',str(self.port),
                 '--shutdown-file',str(self.stop_file),'--parent-pid',str(os.getpid()),
                 '--launch-id',self.launch_id],
                cwd=ROOT,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except BaseException:
            self.log.close();self.log=None;raise

    def __enter__(self):
        deadline=time.monotonic()+30;spawned=False
        try:
            while time.monotonic()<deadline:
                if self.cancel_startup.is_set():raise RuntimeError('Local service startup cancelled')
                if self.existing():
                    # Windows venv redirectors have a different PID from the Python
                    # process serving HTTP. Match this launch, not the wrapper PID.
                    if self.process is not None and (self.process.poll() is not None or self.server_launch_id!=self.launch_id):
                        self.close()  # Only this launch's child; never the identified sibling service.
                    return self
                if not spawned:self._spawn();spawned=True
                # An exited child may have lost an instance-lock race. Keep probing the winner.
                self.cancel_startup.wait(.1)
            raise RuntimeError('Local backend did not become ready. See data/desktop-service.log; check for another running service.')
        except BaseException:
            try:self.close()
            except Exception as cleanup:
                if self.log:self.log.write(f'\nStartup cleanup also failed: {cleanup}\n');self.log.flush()
            raise

    def stop_reused_if_empty(self):
        """Best-effort graceful cleanup; never terminate an unowned process."""
        if self.owned:return False
        try:
            if not self.existing():return False
            status=self.request('/api/v1/status',timeout=2)
            if status.get('busy') or status.get('run') is not None:return False
            result=self.request('/api/v1/service/stop-if-empty',dict(
                checkout_id=identity(ROOT)['checkout_id'],process_id=self.server_pid,
                worker_session_id=status['worker_session_id']),timeout=3)
            return result.get('stopping') is True
        except (urllib.error.URLError,TimeoutError,OSError,RuntimeError,ValueError,KeyError):
            # Changed identity, active work, an old backend or a failed probe all
            # mean leave the shared service alone. Closing the GUI still succeeds.
            return False

    def confirm_close(self,window):
        if not self.owned:return True
        try:
            status=self.retry_status()
            run=status.get('run')
            unsaved=bool(run and run['committed_frames'] and not run['saved'])
            if status.get('busy') or unsaved:
                message=('Exit Kerr Studio? Active acquisition will stop and completed autosave frames will be retained. '
                         'Any memory-only experiment will be lost. Cancel to save it first.')
                if not window.create_confirmation_dialog('Exit Kerr Studio',message):return False
            if status.get('busy')=='scan' and run and run['status'] not in {'finalizing','completed','stopped','error'}:
                self.request(f"/api/v1/runs/{run['run_id']}/stop",{})
            elif status.get('busy')=='preview':self.request('/api/v1/preview/stop',{})
            elif status.get('busy')=='move':self.request('/api/v1/stage/stop',{})
            elif status.get('busy')=='background':self.request('/api/v1/backgrounds/stop',{})
            deadline=time.monotonic()+15
            while status.get('busy') and time.monotonic()<deadline:
                time.sleep(.1);status=self.retry_status(max(.1,deadline-time.monotonic()))
            if status.get('busy'):
                window.create_confirmation_dialog('Operation still finishing','The backend is still writing or finalizing. Wait, then try closing again.')
                return False
            return True
        except Exception:
            try:
                if self.request('/api/v1/health',timeout=1).get('worker_alive') is False:
                    return window.create_confirmation_dialog('Acquisition worker failed',
                        'The acquisition worker has stopped. Saved or committed data remain on disk; memory-only data cannot be recovered. Exit this window, then restart the local backend?')
            except Exception:pass
            # A status timeout is not consent to terminate a process that may be writing.
            window.create_confirmation_dialog('Backend busy or unavailable',
                'The backend may still be writing to disk. This window will remain open; wait and try again. If the worker has failed, restart the local backend after checking saved files.')
            return False

    def close(self):
        with self.close_lock:
            process=self.process
            if process is None:
                if self.log:self.log.close();self.log=None
                return
            try:
                if process.poll() is None:
                    self.stop_file.write_text(self.stop_file.name.removeprefix('.desktop-stop-'),encoding='ascii')
                    try:process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        if self.log:self.log.write('\nGraceful shutdown timed out; terminating owned backend.\n');self.log.flush()
                        process.terminate()
                        try:process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            try:process.wait(timeout=5)
                            except subprocess.TimeoutExpired as exc:
                                raise RuntimeError(f'Owned backend {process.pid} did not exit. Shutdown signal retained; retry shutdown.') from exc
            finally:
                if process.poll() is not None:
                    self.process=None
                    if self.log:self.log.close();self.log=None
                    self.stop_file.unlink(missing_ok=True)

    def __exit__(self,*exc):
        if exc and exc[0] is not None:
            try:self.close()
            except Exception:return False
        else:self.close()
