'''Main-thread controller for snapshots, acquisition data and Analysis isolation.'''
import time,uuid
from pathlib import Path
from PySide6.QtCore import QObject,QTimer,Signal
from .state import Dataset
from .bridge import BackendBridge

class Controller(QObject):
    snapshotChanged=Signal(dict)
    stateChanged=Signal()
    dataChanged=Signal()
    analysisChanged=Signal()
    error=Signal(str)
    ready=Signal(bool)
    readyChanged=Signal(bool)
    fitReady=Signal(str,object)
    fitFailed=Signal(str,str)
    fitStarted=Signal(str)
    fitCancelled=Signal(str)
    closingFinished=Signal()
    data=Signal(); state=Signal()
    def __init__(self,bridge=None,parent=None):
        super().__init__(parent)
        self.bridge=bridge or BackendBridge(self)
        self.snapshot={}; self.acquisition=Dataset(); self.analysis_imported=None
        self.online=False; self._callbacks={}; self._started=False
        self._records_inflight=None; self._record_failed=None
        self._analysis_generation=0
        self._fit_jobs={}; self._fit_generations={}; self._fit_timer=QTimer(self); self._fit_timer.setInterval(300); self._fit_timer.timeout.connect(self._poll_fits)
        self._closing=False; self._close_callback=None; self._close_stop_sent=False; self._close_shutdown=False; self._close_finished=False; self._close_since=0.0; self._close_barrier_pending=False; self._close_warned=False
        self._close_timer=QTimer(self); self._close_timer.setInterval(150); self._close_timer.timeout.connect(self._check_close)
        self._attach_bridge()
    @property
    def analysis_context(self):
        return self.analysis_imported or self.acquisition
    @property
    def analysis_imported_active(self):
        return self.analysis_imported is not None
    def _attach_bridge(self):
        for name,slot in [('snapshotReady',self._on_snapshot),('commandDone',self._on_done),('commandFailed',self._on_failed),('readyChanged',self._on_ready),('startupFailed',self._on_startup_failed),('pollFailed',self._on_poll_failed),('shutdownFinished',self._on_shutdown_finished)]:
            signal=getattr(self.bridge,name,None)
            if signal is not None:
                try: signal.connect(slot)
                except Exception: pass
    def start(self):
        if self._started:
            return
        self._started=True
        try:
            self.bridge.start()
        except Exception as error:
            self.error.emit(str(error)); self.ready.emit(False); self.readyChanged.emit(False)
            return
        self.dispatch('status',{'compact':False})
    def retry(self):
        retry=getattr(self.bridge,'retry_startup',None)
        if callable(retry): retry()
        self.dispatch('status',{'compact':False})
    def dispatch(self,command,data=None,success=None,failure=None):
        try:
            token=self.bridge.submit(command,data)
        except Exception as error:
            token=uuid.uuid4().hex
            payload={'message':str(error),'status_code':400}
            message=str(error)
            def run_immediate():
                if failure is not None:
                    failure(payload)
                else:
                    self.error.emit(message)
            QTimer.singleShot(0,run_immediate)
            return token
        self._callbacks[token]=(success,failure,command)
        return token
    def _on_done(self,token,result):
        callback=self._callbacks.pop(token,None)
        if callback and callback[0] is not None:
            try: callback[0](result)
            except Exception as error: self.error.emit(str(error))
    def _on_failed(self,token,payload):
        callback=self._callbacks.pop(token,None)
        message=payload.get('message') if isinstance(payload,dict) else str(payload)
        if callback and callback[1] is not None:
            try: callback[1](payload)
            except Exception as error: self.error.emit(str(error))
        else:
            self.error.emit(message)
    def _on_ready(self,ready):
        self.online=bool(ready)
        self.ready.emit(bool(ready)); self.readyChanged.emit(bool(ready))
    def _on_startup_failed(self,message):
        self.online=False; self.ready.emit(False); self.readyChanged.emit(False); self.error.emit(message)
    def _on_poll_failed(self,message):
        self.online=False; self.ready.emit(False); self.readyChanged.emit(False); self.error.emit(message)
    def request_status(self):
        self.dispatch('status',{'compact':False})
    def _on_snapshot(self,snapshot):
        if not isinstance(snapshot,dict):
            return
        old_session=(self.snapshot or {}).get('worker_session_id')
        new_session=snapshot.get('worker_session_id')
        session_changed=bool(old_session and new_session and old_session!=new_session)
        if session_changed:
            self._invalidate_all_fits()
            self._analysis_generation+=1
            self.analysis_imported=None
            self.acquisition=Dataset()
            self._records_inflight=None; self._record_failed=None
            self.analysisChanged.emit()
        self.snapshot=dict(snapshot); self.online=True
        run=snapshot.get('run')
        current=self.acquisition.run
        old_id=current.get('run_id') if isinstance(current,dict) else None
        new_id=run.get('run_id') if isinstance(run,dict) else None
        changed=old_id!=new_id
        if changed:
            wave=self.acquisition.wave; half=self.acquisition.half_window
            self._invalidate_fits_for_dataset(self.acquisition)
            self.acquisition=Dataset(run=run,wave=wave,half_window=half)
            self._records_inflight=None; self._record_failed=None
        elif isinstance(run,dict):
            self.acquisition.run=run
        elif current is not None:
            wave=self.acquisition.wave; half=self.acquisition.half_window
            self._invalidate_fits_for_dataset(self.acquisition)
            self.acquisition=Dataset(wave=wave,half_window=half)
            self._records_inflight=None; self._record_failed=None
        self._maybe_fetch_records()
        self.snapshotChanged.emit(dict(snapshot)); self.stateChanged.emit(); self.state.emit(); self.data.emit()
        if changed or session_changed:
            self.dataChanged.emit()
        if self._closing:
            self._check_close()
    def _maybe_fetch_records(self):
        run=self.acquisition.run
        if not isinstance(run,dict) or self._records_inflight is not None:
            return
        run_id=run.get('run_id')
        try: needed=int(run.get('committed_frames') or 0)
        except Exception: needed=0
        if needed<=len(self.acquisition.records):
            return
        if self._record_failed==(run_id,needed):
            return
        dataset=self.acquisition; offset=len(dataset.records)
        def success(page):
            if dataset is not self.acquisition or dataset.run is None or dataset.run.get('run_id')!=run_id:
                return
            self._records_inflight=None
            records=page.get('records',[]) if isinstance(page,dict) else []
            added=dataset.append_records(records)
            total=page.get('total',len(dataset.records)) if isinstance(page,dict) else len(dataset.records)
            self._record_failed=None
            self.dataChanged.emit()
            if added==0 and total>len(dataset.records):
                self._record_failed=(run_id,needed); self.error.emit('The backend returned an incomplete record page; the current dataset was left unchanged.')
            elif len(dataset.records)<int(total or 0):
                QTimer.singleShot(0,self._maybe_fetch_records)
        def failure(payload):
            if dataset is not self.acquisition:
                return
            self._records_inflight=None; self._record_failed=(run_id,needed)
            message=payload.get('message') if isinstance(payload,dict) else str(payload)
            self.error.emit(message)
            code=payload.get('status_code') if isinstance(payload,dict) else None
            if code in {404,409}:
                self.request_status()
        token=self.dispatch('records',{'run_id':run_id,'analysis':False,'offset':offset,'limit':200},success=success,failure=failure)
        self._records_inflight=token
    def open_analysis_file(self,path,success=None,failure=None):
        self._analysis_generation+=1; generation=self._analysis_generation
        def opened(run):
            if generation!=self._analysis_generation:
                if isinstance(run,dict) and run.get('run_id'):
                    self.dispatch('analysis_close',{'run_id':run['run_id'],'analysis':True})
                return
            if not isinstance(run,dict) or not run.get('run_id'):
                self._open_failed(generation,failure,'Analysis backend returned no run')
                return
            dataset=Dataset(run=run,wave=600.0,half_window=5.0)
            self._load_analysis_page(dataset,run['run_id'],generation,0,success,failure)
        def failed(payload):
            if generation!=self._analysis_generation:
                return
            message=payload.get('message') if isinstance(payload,dict) else str(payload)
            if failure is not None: failure(message)
            else: self.error.emit(message)
        self.dispatch('analysis_open',{'path':str(path)},success=opened,failure=failed)
    def _open_failed(self,generation,failure,message,new_run_id=None):
        if new_run_id:
            self.dispatch('analysis_close',{'run_id':new_run_id,'analysis':True})
        if generation==self._analysis_generation:
            if failure is not None: failure(message)
            else: self.error.emit(message)
    def _load_analysis_page(self,dataset,run_id,generation,offset,success,failure):
        def page_ok(page):
            if generation!=self._analysis_generation:
                self.dispatch('analysis_close',{'run_id':run_id,'analysis':True}); return
            records=page.get('records',[]) if isinstance(page,dict) else []
            dataset.append_records(records)
            total=page.get('total',len(dataset.records)) if isinstance(page,dict) else len(dataset.records)
            if len(dataset.records)<int(total or 0) and records:
                self._load_analysis_page(dataset,run_id,generation,len(dataset.records),success,failure)
                return
            if len(dataset.records)<int(total or 0):
                self._open_failed(generation,failure,'Analysis record pagination stopped before completion',run_id); return
            old=self.analysis_imported
            self.analysis_imported=dataset
            self._invalidate_all_fits()
            self.analysisChanged.emit(); self.dataChanged.emit(); self.data.emit()
            if old is not None and old.run and old.run.get('run_id')!=run_id:
                self.dispatch('analysis_close',{'run_id':old.run.get('run_id'),'analysis':True})
            if success is not None: success(dataset.run)
        def page_fail(payload):
            message=payload.get('message') if isinstance(payload,dict) else str(payload)
            self._open_failed(generation,failure,'Could not load Analysis records: '+message,run_id)
        self.dispatch('records',{'run_id':run_id,'analysis':True,'offset':offset,'limit':200},success=page_ok,failure=page_fail)
    def close_analysis(self,success=None,failure=None):
        dataset=self.analysis_imported
        if dataset is None:
            if success is not None: success()
            return
        run_id=dataset.run.get('run_id') if dataset.run else None
        def closed(result):
            if self.analysis_imported is dataset:
                self.analysis_imported=None; self._invalidate_all_fits(); self._analysis_generation+=1
                self.analysisChanged.emit(); self.dataChanged.emit()
            if success is not None: success()
        def failed(payload):
            message=payload.get('message') if isinstance(payload,dict) else str(payload)
            if failure is not None: failure(message)
            else: self.error.emit(message)
        self.dispatch('analysis_close',{'run_id':run_id,'analysis':True},success=closed,failure=failed)
    def run_fit(self,options,scope='analysis',success=None,failure=None):
        dataset=self.analysis_context
        if dataset.run is None or not dataset.records:
            message='No experiment is available for fitting.'
            self.fitFailed.emit(scope,message)
            if failure is not None: failure(message)
            return None
        if not dataset.axis:
            message='Wavelength calibration is required for fitting.'
            self.fitFailed.emit(scope,message)
            if failure is not None: failure(message)
            return None
        run_id=dataset.run.get('run_id'); analysis=dataset is self.analysis_imported
        old=self._fit_jobs.get(scope)
        if old:
            self.invalidate_fit(scope,cancel=True)
        generation=self._fit_generations.get(scope,0)+1
        self._fit_generations[scope]=generation
        def started(result):
            task_id=result.get('task_id') if isinstance(result,dict) else None
            if self._fit_generations.get(scope)!=generation or dataset is not self.analysis_context:
                if task_id:self.dispatch('task_cancel',{'task_id':task_id})
                return
            if not task_id:
                message='Backend did not return a fit task id.'
                self.fitFailed.emit(scope,message)
                if failure is not None: failure(message)
                return
            dataset.fit=None
            job={'task_id':task_id,'dataset':dataset,'run_id':run_id,'options':dict(options),'analysis':analysis,'inflight':False}
            self._fit_jobs[scope]=job; self._fit_timer.start(); self.fitStarted.emit(scope)
            self.dataChanged.emit(); self.analysisChanged.emit()
            if success is not None: success(task_id)
        def failed(payload):
            message=payload.get('message') if isinstance(payload,dict) else str(payload)
            self.fitFailed.emit(scope,message)
            if failure is not None: failure(message)
            else: self.error.emit(message)
        self.dispatch('fit',{'run_id':run_id,'analysis':analysis,'options':options},success=started,failure=failed)
        return None
    def _poll_fits(self):
        if not self._fit_jobs:
            self._fit_timer.stop(); return
        for scope,job in list(self._fit_jobs.items()):
            if job.get('inflight'):
                continue
            def checked(result,job=job,scope=scope):
                if self._fit_jobs.get(scope) is not job:
                    return
                job['inflight']=False
                status=result.get('status') if isinstance(result,dict) else None
                if status=='running':
                    return
                self._fit_jobs.pop(scope,None); dataset=job['dataset']
                if status=='completed' and result.get('result') is not None:
                    current_ok=(dataset is self.acquisition) or (dataset is self.analysis_imported)
                    if current_ok and dataset.run and dataset.run.get('run_id')==job['run_id']:
                        dataset.fit=result.get('result'); self.dataChanged.emit(); self.analysisChanged.emit(); self.fitReady.emit(scope,result.get('result'))
                else:
                    message=result.get('error') if isinstance(result,dict) else None
                    message=message or ('Fit cancelled' if status=='cancelled' else 'Fit task failed')
                    dataset.fit=None; self.dataChanged.emit(); self.fitFailed.emit(scope,message)
                if not self._fit_jobs: self._fit_timer.stop()
            def task_failed(payload,job=job,scope=scope):
                if self._fit_jobs.get(scope) is not job: return
                self._fit_jobs.pop(scope,None)
                message=payload.get('message') if isinstance(payload,dict) else str(payload)
                job['dataset'].fit=None; self.fitFailed.emit(scope,message); self.dataChanged.emit()
                if not self._fit_jobs: self._fit_timer.stop()
            job['inflight']=True
            self.dispatch('task',{'task_id':job['task_id']},success=checked,failure=task_failed)
    def invalidate_fit(self,scope='analysis',cancel=True):
        self._fit_generations[scope]=self._fit_generations.get(scope,0)+1
        job=self._fit_jobs.pop(scope,None)
        if job:
            dataset=job['dataset']; dataset.fit=None
            if cancel: self.dispatch('task_cancel',{'task_id':job['task_id']})
        else:
            self.analysis_context.fit=None
        if not self._fit_jobs: self._fit_timer.stop()
        self.fitCancelled.emit(scope); self.dataChanged.emit(); self.analysisChanged.emit()
    def _invalidate_fits_for_dataset(self,dataset):
        for scope,job in list(self._fit_jobs.items()):
            if job.get('dataset') is dataset:
                self._fit_jobs.pop(scope,None); self.dispatch('task_cancel',{'task_id':job['task_id']}); self.fitCancelled.emit(scope)
        if not self._fit_jobs: self._fit_timer.stop()
    def _invalidate_all_fits(self):
        for scope in self._fit_generations:
            self._fit_generations[scope]+=1
        for scope,job in list(self._fit_jobs.items()):
            self._fit_jobs.pop(scope,None); self.dispatch('task_cancel',{'task_id':job['task_id']}); self.fitCancelled.emit(scope)
        self._fit_timer.stop()
    def begin_close(self,callback=None):
        if self._closing:
            if callback is not None and self._close_callback is None: self._close_callback=callback
            return
        self._closing=True; self._close_callback=callback; self._close_since=time.monotonic()
        self._close_barrier_pending=True
        def checked(snapshot):
            self._close_barrier_pending=False
            self._on_snapshot(snapshot)
            self._close_timer.start();self._check_close()
        def failed(payload):
            self._close_barrier_pending=False
            # A dead worker has no live writer; an alive worker must stay owned.
            if not self.online:self._shutdown_bridge()
            else:self.error.emit(str(payload));self._close_timer.start()
        self.dispatch('status',{'compact':False},success=checked,failure=failed)
    def _check_close(self):
        if not self._closing or self._close_shutdown or self._close_barrier_pending:
            return
        busy=(self.snapshot or {}).get('busy')
        if busy:
            if not self._close_stop_sent:
                self._close_stop_sent=True
                run=(self.snapshot or {}).get('run') or {}; run_id=run.get('run_id')
                if busy=='scan' and run_id:
                    self.dispatch('stop',{'run_id':run_id})
                else:
                    command={'move':'stage_stop','preview':'preview_stop','background':'background_stop','calibration':'calibration_stop'}.get(busy)
                    if command: self.dispatch(command,{})
            if time.monotonic()-self._close_since>45.0 and not self._close_warned:
                self._close_warned=True
                self.error.emit('Acquisition is still stopping or saving. Please wait; the worker will remain open until data finalization finishes.')
            return
        self._shutdown_bridge()
    def _shutdown_bridge(self):
        if self._close_shutdown:
            return
        self._close_shutdown=True; self._close_timer.stop()
        running=False
        try: running=bool(self.bridge.isRunning())
        except Exception: running=False
        if not running:
            QTimer.singleShot(0,self._on_shutdown_finished); return
        try:
            self.bridge.shutdown()
        except Exception as error:
            self.error.emit(str(error)); QTimer.singleShot(0,self._on_shutdown_finished)
    def _on_shutdown_finished(self):
        if not self._closing or self._close_finished:
            return
        self._close_finished=True; self._close_timer.stop(); self.online=False
        callback=self._close_callback; self._close_callback=None
        self.closingFinished.emit()
        if callback is not None:
            callback()
