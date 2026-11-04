'''QThread bridge that exclusively owns WorkerManager and blocking requests.'''
import time,uuid
from queue import Queue,Empty
from PySide6.QtCore import QThread,Signal
from .backend_api import validate_command

class _Request:
    __slots__=('token','name','data')
    def __init__(self,token,name,data):
        self.token=token; self.name=name; self.data=data
class _Retry:
    pass

class BackendBridge(QThread):
    snapshotReady=Signal(dict)
    commandDone=Signal(str,object)
    commandFailed=Signal(str,object)
    readyChanged=Signal(bool)
    startupFailed=Signal(str)
    pollFailed=Signal(str)
    shutdownFinished=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self._requests=Queue(); self._shutdown_requested=False; self._shutdown_emitted=False
        self._snapshot={}; self._since=None; self._latest_version=0; self._last_full=0.0
        self._ready=False; self._poll_message_at=0.0
    def start(self,*args,**kwargs):
        if not self.isRunning() and not self._shutdown_requested:
            super().start(*args,**kwargs)
    def submit(self,command,data=None,token=None):
        normalized=validate_command(command,data)
        token=token or uuid.uuid4().hex
        if self._shutdown_requested:
            raise RuntimeError('Backend bridge is shutting down')
        self._requests.put(_Request(token,command,normalized))
        if not self.isRunning():
            self.start()
        return token
    def retry_startup(self):
        self._requests.put(_Retry())
        if not self.isRunning() and not self._shutdown_requested:
            self.start()
    def shutdown(self):
        self._shutdown_requested=True
        if not self.isRunning():
            self._emit_shutdown()
            return
        self._requests.put(None)
    def _emit_shutdown(self):
        if not self._shutdown_emitted:
            self._shutdown_emitted=True; self.readyChanged.emit(False); self.shutdownFinished.emit()
    def _create_manager(self):
        from backend.manager import WorkerManager
        return WorkerManager()
    def _start_manager(self,manager):
        # WorkerManager starts its process and confirms readiness in __init__.
        return
    def _stop_manager(self,manager):
        manager.close()
    def _call(self,manager,name,data):
        return manager.call(name,data)
    def _error_payload(self,error):
        code=getattr(error,'status_code',None) or getattr(error,'status',None) or 500
        return {'message':str(error),'status_code':int(code)}
    def _merge(self,new):
        if not isinstance(new,dict):
            return
        base=dict(self._snapshot)
        for key,value in new.items():
            if key=='run':
                old=base.get('run')
                if value is None or not isinstance(value,dict):
                    base[key]=value
                elif isinstance(old,dict) and old.get('run_id')==value.get('run_id'):
                    merged=dict(old); merged.update(value); base[key]=merged
                else:
                    base[key]=dict(value)
            elif isinstance(value,dict) and isinstance(base.get(key),dict) and key in {'devices','stage','camera','calibration','spectrograph','available'}:
                merged=dict(base[key]); merged.update(value); base[key]=merged
            else:
                base[key]=value
        self._snapshot=base
        sync=new.get('_sync') if isinstance(new,dict) else None
        if isinstance(sync,dict):
            if isinstance(sync.get('token'),str):
                self._since=sync['token']
            version=sync.get('latest_version')
            if isinstance(version,int):
                self._latest_version=version
    def _poll(self,manager):
        data={'compact':True,'latest_version':self._latest_version}
        now=time.monotonic()
        if self._since is not None and now-self._last_full<60.0:
            data['since']=self._since
        result=self._call(manager,'status',data)
        if not isinstance(result,dict):
            return
        self._merge(result)
        sync=result.get('_sync') or {}
        if sync.get('full') or self._since is None:
            self._last_full=now
        if not self._ready:
            self._ready=True; self.readyChanged.emit(True)
        self.snapshotReady.emit(dict(self._snapshot))
    def _fail_startup(self,error):
        message=str(error)
        if time.monotonic()-self._poll_message_at>1.5:
            self._poll_message_at=time.monotonic(); self.readyChanged.emit(False); self.startupFailed.emit(message)
    def run(self):
        manager=None; next_poll=time.monotonic(); startup_error=''
        while True:
            now=time.monotonic()
            if manager is not None and now>=next_poll:
                try:
                    self._poll(manager)
                except Exception as error:
                    message=str(error)
                    self._ready=False
                    if time.monotonic()-self._poll_message_at>1.5:
                        self._poll_message_at=time.monotonic(); self.pollFailed.emit(message)
                next_poll=time.monotonic()+0.15
            timeout=max(0.0,min(0.15,next_poll-time.monotonic()))
            try:
                request=self._requests.get(timeout=timeout)
            except Empty:
                if self._shutdown_requested and self._requests.empty():
                    break
                continue
            if request is None:
                break
            if isinstance(request,_Retry):
                if manager is None:
                    try:
                        manager=self._create_manager(); self._start_manager(manager); startup_error=''; self.readyChanged.emit(True); self._ready=True
                    except Exception as error:
                        self._fail_startup(error)
                continue
            if manager is None:
                try:
                    manager=self._create_manager(); self._start_manager(manager); startup_error=''; self.readyChanged.emit(True); self._ready=True
                except Exception as error:
                    startup_error=str(error); self._fail_startup(error)
                    self.commandFailed.emit(request.token,{'message':'Backend unavailable: '+startup_error,'status_code':503})
                    continue
            try:
                result=self._call(manager,request.name,request.data)
                if request.name=='status' and isinstance(result,dict):
                    self._merge(result); self.commandDone.emit(request.token,result); self.snapshotReady.emit(dict(self._snapshot))
                else:
                    self.commandDone.emit(request.token,result)
            except Exception as error:
                self.commandFailed.emit(request.token,self._error_payload(error))
            next_poll=time.monotonic()
        try:
            if manager is not None:
                self._stop_manager(manager)
        except Exception as error:
            self.pollFailed.emit(str(error))
        finally:
            self._ready=False
            self._emit_shutdown()
