"""Hardware ownership, cancellable sequential acquisition and command dispatch."""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from datetime import datetime, timezone
from threading import Event, RLock, Thread
import copy
import hashlib
import json
import time
import traceback
import uuid
import numpy as np
from .core import make_plan, mm_to_delay, delay_to_mm
from .models import RunRequest, FitRequest
from .drivers.sim import SimCamera, SimStage
from .storage.hdf5 import HDFWriter, choose_path, read_run, append_analysis
from .storage.output import output_info,prepare_output
from .storage.export import export_csv
from .calibration_service import CalibrationWorkbench
from .errors import CommandError,error_status


def fit_records(*args,**kwargs):
    # Keep expensive fitting imports out of the worker's first status handshake.
    from .analysis import fit_records as fit
    return fit(*args,**kwargs)


def now():
    return datetime.now(timezone.utc).isoformat()


def serial(value):
    if isinstance(value,np.ndarray):
        return value.tolist()
    if isinstance(value,dict):
        return {k:serial(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):
        return [serial(v) for v in value]
    if isinstance(value,np.generic):
        return value.item()
    return value


class Cancelled(Exception):
    pass


class Engine:
    def __init__(self, events):
        self.events=events;self.lock=RLock();self.seq=0;self.session=uuid.uuid4().hex
        self.camera=SimCamera();self.stage=SimStage();self.zero=150.0;self.zero_version=1
        self.revision=0;self.connected=True;self.busy=None;self.latest=None;self.background=None
        self.calibration=dict(calibration_id='synthetic-500-800-v1',source='synthetic',wavelengths_nm=self.camera.axis.tolist())
        self.latest_axis=self.calibration['wavelengths_nm']
        self.spectrograph=dict(center_nm=600.0,grating=300,slit_um=200.0)
        self.calibration_workbench=CalibrationWorkbench()
        recorded=self.calibration_workbench.recorded_spectrograph()
        if recorded is not None:self.spectrograph=recorded
        restored=self.calibration_workbench.restore(self.spectrograph)
        if restored is not None:self.calibration=restored
        self.latest_axis=self.calibration['wavelengths_nm']
        self.analysis_lock=RLock()
        self.current=None;self.runs={};self.stop=Event();self.pause=Event();self.job=None
        self.commands={};self.tasks={};self.pool=ThreadPoolExecutor(max_workers=1);self.last_preview=0
        self.task_controls={};self.analysis_runs={}
        self.shutdown_prepared=False;self.preview_moving=False
        self.event_times={};self.latest_version=0

    def emit(self, kind, payload):
        with self.lock:
            stamp=time.monotonic()
            key=(kind,payload.get('status')) if kind=='run.state' else (kind,)
            limited=kind in {'spectrum.preview','storage.status','point.committed','stage.position'} or kind=='run.state' and payload.get('status')=='running'
            if limited and stamp-self.event_times.get(key,-1)<.1:return None
            self.event_times[key]=stamp
            self.seq+=1
            message=dict(schema_version=1,worker_session_id=self.session,seq=self.seq,
                         run_id=self.current,type=kind,timestamp_utc=now(),payload=serial(payload))
        # Stream is for presentation. Authoritative records remain available via pagination.
        try:self.events.put(message,timeout=0)
        except Exception:pass
        return message

    def public_run(self,run):
        with self.lock:
            return {k:serial(v) for k,v in run.items() if k not in {'records','writer'}}

    def compact_status(self,since=None,latest_version=-1):
        with self.lock:
            run=self.runs.get(self.current)
            stamp=(self.session,self.revision,self.current,run.get('analysis_version',0) if run else 0,
                   id(self.latest_axis),id(self.background),self.calibration_workbench.warning)
            token=hashlib.sha256(repr(stamp).encode()).hexdigest()
            full=since!=token
            state=dict(source_mode='simulation',connected=self.connected,busy=self.busy,worker_session_id=self.session,seq=self.seq,revision=self.revision,
                       stage=dict(controller_reported_mm=self.stage.position_mm,position_source='simulated_controller',delay_ps=mm_to_delay(self.stage.position_mm,self.zero),zero_mm=self.zero,zero_version=self.zero_version,
                                  moving=self.preview_moving or self.busy=='move' or bool(self.busy=='scan' and run and run['step']=='moving')),
                       camera=self.camera.capabilities()|dict(exposure_s=self.camera.exposure_s),
                       run={k:serial(v) for k,v in run.items() if k not in {'records','writer','metadata','request','plan'}} if run else None,
                       _sync=dict(full=full,token=token,latest_version=self.latest_version))
            if full:
                state.update(calibration=serial(self.calibration),spectrograph=self.spectrograph.copy(),calibration_warning=self.calibration_workbench.warning,
                             live_axis=serial(self.latest_axis),background=serial(self.background))
                if run:
                    metadata={k:v for k,v in run['metadata'].items() if k!='analysis'}
                    history=run['metadata'].get('analysis',{})
                    if history:
                        newest=max(history,key=lambda k:history[k].get('finished_utc',''))
                        metadata['analysis']={newest:history[newest]}
                    state['run'].update(metadata=serial(metadata),request=serial(run['request']),plan=serial(run['plan']))
            if full or latest_version!=self.latest_version:state['latest']=serial(self.latest)
            return state

    def status(self):
        with self.lock:
            return dict(source_mode='simulation',connected=self.connected,busy=self.busy,
                        worker_session_id=self.session,seq=self.seq,revision=self.revision,
                        stage=dict(controller_reported_mm=self.stage.position_mm,position_source='simulated_controller',
                                   delay_ps=mm_to_delay(self.stage.position_mm,self.zero),zero_mm=self.zero,zero_version=self.zero_version,
                                   moving=self.preview_moving or self.busy=='move' or bool(self.busy=='scan' and self.current and self.runs[self.current]['step']=='moving')),
                        camera=self.camera.capabilities()|dict(exposure_s=self.camera.exposure_s),
                        calibration=copy.deepcopy(self.calibration),spectrograph=self.spectrograph.copy(),
                        calibration_warning=self.calibration_workbench.warning,
                        latest=serial(self.latest),live_axis=self.latest_axis,background=serial(self.background),
                        run=self.public_run(self.runs[self.current]) if self.current else None)

    def idle(self,devices=True):
        if devices and not self.connected:raise CommandError('Devices are disconnected')
        if self.busy:raise CommandError('Devices are busy: '+self.busy)

    def set_exposure(self,exposure):
        if self.camera.exposure_s!=exposure:
            self.camera.exposure_s=exposure;self.background=None;self.revision+=1

    def prune_tasks(self):
        finished=[key for key,value in self.tasks.items() if value['status']!='running']
        for key in finished[:-64]:
            self.tasks.pop(key,None);self.task_controls.pop(key,None)

    def wait(self,physical_s,speedup):
        if self.stop.wait(max(.001,physical_s/speedup)):
            raise Cancelled()

    def launch(self,kind,target,*args):
        self.idle();self.stop.clear();self.pause.clear();self.busy=kind
        def wrapped():
            try:target(*args)
            except Cancelled:pass
            except Exception as exc:
                self.emit('device.error',dict(message=str(exc)))
            finally:
                with self.lock:self.busy=None
                self.emit('device.status',dict(busy=None))
        self.job=Thread(target=wrapped,daemon=True,name='device-owner')
        self.job.start()

    def move(self,target,speedup=50):
        duration=self.stage.duration(target)
        self.emit('stage.position',dict(moving=True,target_mm=target))
        started=time.monotonic()
        try:self.wait(duration,speedup)
        except Cancelled:
            with self.lock:self.stage.position_mm=self.stage.partial_position(target,(time.monotonic()-started)*speedup)
            self.emit('stage.position',dict(moving=False,controller_reported_mm=self.stage.position_mm,stopped=True))
            raise
        with self.lock:self.stage.position_mm=target
        self.emit('stage.position',dict(moving=False,controller_reported_mm=target,delay_ps=mm_to_delay(target,self.zero)))

    def metadata(self,request,plan,run_id):
        return dict(meta=dict(schema_version=1,software_version='0.1.1',source_mode='simulation',run_id=run_id,created_utc=now()),
                    config=dict(request=request,plan=plan,applied=dict(read_mode='fvb',exposure_s=request['exposure_s'],hbin=1)),
                    devices=dict(camera=self.camera.capabilities(),stage=dict(model='PI M-414.32S (simulated)',position_source='simulated_controller')),
                    coordinates=dict(zero_mm=self.zero,zero_version=self.zero_version,path_multiplier=2,delay_sign=1),
                    calibration=copy.deepcopy(self.calibration),background=serial(self.background),
                    simulation=dict(seed=request['seed'],speedup=request['speedup'],fault=request['fault'],
                                    tau_ps=[.42,3.8],irf_fwhm_ps=.25,gain_e_per_count=2.5))

    def run_scan(self,run):
        request=run['request'];plan=run['plan'];writer=run.get('writer')
        terminal='completed';pending=deque()
        def commit(record):
            with self.lock:
                run['records'].append(record);run['committed_frames']=len(run['records']);self.latest=record;self.latest_version+=1
                self.latest_axis=run['metadata']['calibration']['wavelengths_nm']
                if record['frame_index']==request['frames_per_point']-1:run['completed_points']+=1
            self.emit('spectrum.preview',record)
            self.emit('storage.status',dict(committed_frames=run['committed_frames'],acquired_frames=run['acquired_frames'],on_disk=bool(writer)))
            self.emit('point.committed',dict(completed_points=run['completed_points'],total_points=plan['total_points']))
        def drain(wait=False):
            while pending and (wait or pending[0][1].done()):
                record,ack=pending.popleft();ack.result(timeout=30);commit(record)
        def step(name):
            with self.lock:run['step']=name
            message=self.emit('run.state',{k:run[k] for k in ['run_id','status','step','completed_points','committed_frames','acquired_frames']})
            if writer and message:writer.submit('event',message)
        try:
            if request['autosave'] and writer is None:
                writer=HDFWriter(run['path'],run['metadata'])
                with self.lock:run['writer']=writer
            with self.lock:run['status']='running'
            for round_index in range(plan['rounds']):
                if request['mode']!='single':
                    step('moving');self.move(plan['approach_mm'],request['speedup'])
                for point_index,(delay,target) in enumerate(zip(plan['delays_ps'],plan['targets_mm'])):
                    if self.stop.is_set():raise Cancelled()
                    fault=request['fault']['kind'] if request['fault']['point_index']==point_index else 'none'
                    step('moving')
                    if fault=='move_timeout':raise TimeoutError('Injected stage move timeout')
                    self.move(target,request['speedup'])
                    step('settling');self.wait(request['settle_s'],request['speedup'])
                    for frame_index in range(request['frames_per_point']):
                        started=now();step('exposing');self.wait(request['exposure_s'],request['speedup'])
                        if fault=='camera_disconnect':raise ConnectionError('Injected camera disconnect')
                        step('reading');self.wait(.03,request['speedup'])
                        if self.stop.is_set():raise Cancelled()
                        drain();record_id=run['acquired_frames']
                        spectrum,saturated=self.camera.acquire(delay,request['seed'],record_id,request['exposure_s'],
                                                               run['metadata']['config'].get('center_nm',self.spectrograph['center_nm']),fault)
                        record=dict(record_id=record_id,round_index=round_index,point_index=point_index,frame_index=frame_index,
                                    delay_ps=delay,requested_mm=target,controller_reported_mm=self.stage.position_mm,
                                    position_source='simulated_controller',zero_version=run['metadata']['coordinates']['zero_version'],
                                    exposure_s=request['exposure_s'],requested_exposure_s=request['exposure_s'],temperature_c=-70.0,
                                    host_exposure_start_utc=started,host_readout_end_utc=now(),hardware_timestamp=None,
                                    saturated_pixels=saturated,spectrum=spectrum,source_mode='simulation')
                        with self.lock:run['acquired_frames']+=1
                        step('committing')
                        if fault=='write_failure' and not writer:raise OSError('Injected storage failure (memory-only run)')
                        if writer:
                            pending.append((record,writer.submit('record',record|dict(inject_write_failure=fault=='write_failure'))))
                            if len(pending)>=128:
                                first,ack=pending.popleft();ack.result(timeout=30);commit(first)
                            drain()
                        else:commit(record)
                    if self.pause.is_set() and not (round_index==plan['rounds']-1 and point_index==len(plan['delays_ps'])-1):
                        drain(wait=True)
                        with self.lock:run['status']='paused';run['step']='paused'
                        self.emit('run.state',self.public_run(run))
                        while self.pause.is_set():self.wait(.05,1)
                        with self.lock:run['status']='running'
            drain(wait=True)
            if self.stop.is_set():raise Cancelled()
        except Cancelled:
            terminal='stopped'
        except Exception as exc:
            terminal='error';run['error']=str(exc)
            self.emit('run.error',dict(message=str(exc),committed_frames=len(run['records'])))
        finally:
            # Stop/pause acknowledge every already-acquired queued frame. Failed
            # writes expose only the successfully committed contiguous prefix.
            try:drain(wait=True)
            except Exception as exc:terminal='error';run['error']=str(exc)
            with self.lock:
                if terminal=='completed' and self.stop.is_set():terminal='stopped'
                run['status']='finalizing';run['step']='finalizing'
            self.emit('run.state',self.public_run(run))
            try:
                if writer:
                    try:writer.call('event',dict(type='run.terminal',status=terminal,error=run['error'],timestamp_utc=now()))
                    except Exception as exc:
                        terminal='error';run['error']=f'Event write failed: {exc}'
                    run['path']=writer.call('close',terminal)
                    run['writer_stats']=dict(writer.stats)
                    run['saved']=True
            except Exception as exc:
                terminal='error';run['error']=(run['error']+'; ' if run['error'] else '')+f'Finalization failed: {exc}'
                run['path']=str(writer.partial) if writer else run['path']
            with self.lock:
                run.pop('writer',None);run['status']=terminal;run['step']='idle';run['ended_utc']=now()
            self.emit('run.state',self.public_run(run))

    def preview(self):
        index=0
        while not self.stop.is_set():
            self.wait(self.camera.exposure_s,50)
            counts,saturated=self.camera.acquire(mm_to_delay(self.stage.position_mm,self.zero),101,index,self.camera.exposure_s,self.spectrograph['center_nm'])
            with self.lock:
                self.latest=dict(spectrum=counts,saturated_pixels=saturated,delay_ps=mm_to_delay(self.stage.position_mm,self.zero),exposure_s=self.camera.exposure_s)
                self.latest_axis=self.calibration['wavelengths_nm'];self.latest_version+=1
            self.emit('spectrum.preview',self.latest);index+=1
            self.wait(.2,1)

    def preview_move(self,target):
        try:self.move(target)
        finally:self.preview_moving=False
        self.preview()

    def edit_preview(self,name,data):
        # Finish the preview owner before changing its device settings. Never run
        # a motion and an exposure concurrently; resume with a fresh whole frame.
        self.stop.set();self.job.join(5)
        if self.job.is_alive():raise CommandError('Preview did not stop; retry after stopping it')
        try:
            if name=='move':
                current=mm_to_delay(self.stage.position_mm,self.zero)
                target=delay_to_mm(current+data['delay_ps'] if data['relative'] else data['delay_ps'],self.zero)
                self.stage.duration(target);self.preview_moving=True
                self.launch('preview',self.preview_move,target)
                return dict(accepted=True)
            return self._handle(name,data)
        finally:
            if not self.busy:self.launch('preview',self.preview)

    def acquire_background(self,data):
        exposure=self.camera.exposure_s
        self.wait(exposure,50)
        counts,_=self.camera.acquire(0,849,0,exposure,self.spectrograph['center_nm'],dark=data['kind']=='dark',optical_background=data['kind']=='optical_background')
        with self.lock:
            self.background=dict(spectrum=counts,exposure_s=exposure,kind=data['kind'],condition=data['condition'],source_mode='simulation',
                                 revision=self.revision,temperature_c=-70.0,read_mode='fvb',timestamp_utc=now())
        self.emit('background.ready',self.background)

    def handle(self,name,data):
        # command loop is the only place launching device jobs; SDK calls stay in owner job.
        with self.lock:
            if self.shutdown_prepared and name!='status':
                raise CommandError('The local service is shutting down')
            if name=='prepare_empty_shutdown':
                if data.get('worker_session_id')!=self.session:
                    raise CommandError('The local service session changed')
                if (self.busy or self.runs or self.analysis_runs or self.background is not None
                        or self.calibration_workbench.frame is not None
                        or any(task['status']=='running' for task in self.tasks.values())):
                    raise CommandError('The service has active work or experiment data; it will remain running')
                # Serialized with new commands: no acquisition can start between
                # this check and the API process beginning graceful shutdown.
                self.shutdown_prepared=True
                return dict(stopping=True)
        request_id=data.get('request_id')
        key=(name,request_id)
        if request_id and key in self.commands:
            old,result=self.commands[key]
            if old!=data:raise ValueError('request_id was already used with different parameters')
            return result
        result=self.edit_preview(name,data) if self.busy=='preview' and name in {'configure','move','zero','spectrograph'} else self._handle(name,data)
        if request_id:
            self.commands[key]=(copy.deepcopy(data),result)
            if len(self.commands)>1000:self.commands.pop(next(iter(self.commands)))
        return result

    def _handle(self,name,data):
        if name=='status':return self.compact_status(data.get('since'),data.get('latest_version',-1)) if data.get('compact') else self.status()
        if name=='capabilities':return self.camera.capabilities()
        if name=='connect':
            if data.get('mode','simulation')!='simulation':
                raise ValueError('Hardware mode is not commissioned. Supply the PI controller model and validate adapters first; no simulation fallback.')
            if self.busy:raise ValueError('Devices are busy')
            self.connected=True;return self.status()
        if name=='disconnect':
            self.idle();self.connected=False;return self.status()
        if name=='configure':
            self.idle();self.set_exposure(data['exposure_s'])
            return dict(exposure_s=self.camera.exposure_s)
        if name=='move':
            current=mm_to_delay(self.stage.position_mm,self.zero)
            target=delay_to_mm(current+data['delay_ps'] if data['relative'] else data['delay_ps'],self.zero)
            self.stage.duration(target);self.launch('move',self.move,target);return dict(accepted=True)
        if name=='zero':
            self.idle();self.zero=self.stage.position_mm;self.zero_version+=1;self.revision+=1;return self.status()
        if name=='spectrograph':
            self.idle()
            if data!=self.spectrograph:
                empty=dict(calibration_id=None,source=None,wavelengths_nm=None)
                self.calibration_workbench.persist(empty,data)
                self.spectrograph=data;self.calibration=dict(calibration_id=None,source=None,wavelengths_nm=None)
                self.latest_axis=None
                self.calibration_workbench.invalidate()
                self.background=None;self.revision+=1
            return self.status()
        if name=='calibration':
            self.idle();self.calibration_workbench.persist(data,self.spectrograph)
            self.calibration=data;self.latest_axis=copy.deepcopy(data['wavelengths_nm']);self.revision+=1;return self.status()
        if name=='calibration_frame':
            return self.calibration_workbench.current_frame()
        if name=='calibration_capture':
            self.idle()
            return self.calibration_workbench.capture(data,self.spectrograph)
        if name=='calibration_extract':
            self.idle()
            return self.calibration_workbench.extract(data,self.spectrograph)
        if name=='calibration_fit':
            self.idle()
            return self.calibration_workbench.fit(data,self.spectrograph)
        if name=='calibration_solution':
            solution=self.calibration_workbench.solution(data['solution_id'])
            solution['applied']=solution['calibration_id']==self.calibration.get('calibration_id')
            return solution
        if name=='calibration_apply':
            self.idle()
            self.calibration=self.calibration_workbench.installation(data['solution_id'],self.spectrograph)
            self.latest_axis=copy.deepcopy(self.calibration['wavelengths_nm'])
            self.revision+=1
            self.emit('calibration.applied',dict(calibration_id=self.calibration['calibration_id']))
            return self.status()
        if name=='plan':
            request=RunRequest(**data)
            return make_plan(request,mm_to_delay(self.stage.position_mm,self.zero),self.zero,revision=self.revision)
        if name=='output_check':return output_info(data['path'])
        if name=='run':
            self.idle();request=RunRequest(**data)
            plan=make_plan(request,mm_to_delay(self.stage.position_mm,self.zero),self.zero,revision=self.revision)
            if any(task['status']=='running' and task.get('run_id') in self.runs for task in self.tasks.values()):
                raise CommandError('Wait for or cancel analysis before starting another acquisition')
            run_id=uuid.uuid4().hex[:16]
            path=None;writer=None
            self.set_exposure(request.exposure_s)
            plan=make_plan(request,mm_to_delay(self.stage.position_mm,self.zero),self.zero,revision=self.revision)
            metadata=self.metadata(data,plan,run_id);metadata['config']['center_nm']=self.spectrograph['center_nm']
            if request.autosave:
                with prepare_output(request.save_path,run_id,plan['estimated_bytes'],request.overwrite_token) as path:
                    writer=HDFWriter(path,metadata)
            run=dict(run_id=run_id,status='preparing',step='preparing',request=data,plan=plan,records=[],
                     metadata=metadata,path=str(path) if path else None,saved=False,created_utc=now(),ended_utc=None,
                     acquired_frames=0,committed_frames=0,completed_points=0,error=None)
            if writer:run['writer']=writer
            # Replace a memory-only experiment only after new output is ready.
            self.runs={run_id:run};self.current=run_id;self.camera.exposure_s=request.exposure_s
            self.launch('scan',self.run_scan,run);return self.public_run(run)
        if name in {'pause','resume','stop'}:
            with self.lock:
                run=self.runs.get(data['run_id'])
                if not run or self.current!=data['run_id'] or self.busy!='scan':raise CommandError('No matching active run')
                if run['status'] not in {'running','paused','pausing','stopping'}:raise CommandError('Run is finalizing or already finished')
                if name=='pause':
                    if run['status'] in {'paused','pausing'}:return self.public_run(run)
                    if run['status']!='running':raise CommandError('Run is stopping')
                    self.pause.set();run['status']='pausing'
                elif name=='resume':
                    if run['status']=='running':return self.public_run(run)
                    if run['status'] not in {'paused','pausing'}:raise CommandError('Run is not paused')
                    self.pause.clear();run['status']='running'
                else:
                    self.stop.set();self.pause.clear();run['status']='stopping'
            self.emit('run.state',self.public_run(run));return self.public_run(run)
        if name=='preview_start':self.launch('preview',self.preview);return dict(accepted=True)
        if name in {'preview_stop','stage_stop','background_stop'}:
            expected={'preview_stop':'preview','stage_stop':'move','background_stop':'background'}[name]
            if self.busy is None:return dict(accepted=True)
            if self.busy!=expected:
                hint='Stop the active scan using its run ID' if self.busy=='scan' else f'Use the {self.busy} stop operation'
                raise CommandError(hint)
            self.stop.set();return dict(accepted=True)
        if name=='background':self.launch('background',self.acquire_background,data);return dict(accepted=True)
        if name in {'open','analysis_open'}:
            separate=name=='analysis_open'
            if not separate:
                self.idle(devices=False)
                if self.current and not self.runs[self.current]['saved'] and self.runs[self.current]['records']:raise ValueError('Save or discard the current run first')
            metadata,records,terminal=read_run(data['path']);run_id=metadata['meta']['run_id']
            if separate and len(self.analysis_runs)>=8:
                active={task['run_id'] for task in self.tasks.values() if task['status']=='running'}
                old=next((id for id in self.analysis_runs if id not in active),None)
                if old is None:raise CommandError('Close an Analysis file or finish its fit before loading another')
                self.analysis_runs.pop(old)
            if separate:run_id='analysis-'+uuid.uuid4().hex
            run=dict(run_id=run_id,status=terminal,step='idle',metadata=metadata,records=records,
                     request=metadata['config']['request'],plan=metadata['config']['plan'],path=data['path'],saved=True,
                     acquired_frames=len(records),committed_frames=len(records),
                     completed_points=len({(r['round_index'],r['point_index']) for r in records if r['frame_index']==metadata['config']['request']['frames_per_point']-1}),
                     created_utc=metadata['meta']['created_utc'],ended_utc=None,error=None)
            if separate:
                self.analysis_runs[run_id]=run;return self.public_run(run)
            self.runs={run_id:run};self.current=run_id;self.latest=records[-1] if records else None;self.latest_version+=1
            self.latest_axis=metadata['calibration']['wavelengths_nm']
            self.set_exposure(run['request']['exposure_s'])
            return self.public_run(run)
        if name in {'get_run','records','export','save','fit','discard','analysis_close'}:
            separate=bool(data.get('analysis'))
            run=(self.analysis_runs if separate else self.runs).get(data.get('run_id',self.current))
            if not run:raise CommandError('Run not found',404)
            if name=='analysis_close':
                if not separate:raise CommandError('Not an Analysis file')
                for id,task in self.tasks.items():
                    if task['run_id']==run['run_id'] and task['status']=='running':self._handle('task_cancel',dict(task_id=id))
                self.analysis_runs.pop(run['run_id']);return dict(closed=True)
            if name=='get_run':return self.public_run(run)
            if name=='records':
                offset=data.get('offset',0);limit=data.get('limit',100)
                return dict(records=serial(run['records'][offset:offset+limit]),total=len(run['records']))
            if name=='export':
                if run['metadata']['calibration']['wavelengths_nm'] is None:raise CommandError('Wavelength calibration is required for export')
                return export_csv(run['records'],run['metadata']['calibration']['wavelengths_nm'],data['center_nm'],data['half_window_nm'],run['metadata']['meta']['source_mode'])
            if name=='save':
                self.idle(devices=False)
                with self.analysis_lock:
                    if run['saved']:return self.public_run(run)
                    with prepare_output(data.get('path'),run['run_id'],run['plan']['estimated_bytes'],data.get('overwrite_token')) as path:
                        writer=HDFWriter(path,run['metadata'])
                        try:
                            pending=[]
                            for record in run['records']:
                                pending.append(writer.submit('record',record))
                                if len(pending)>=128:
                                    for ack in pending:ack.result(timeout=30)
                                    pending=[]
                            for ack in pending:ack.result(timeout=30)
                        except Exception:
                            writer.call('close','error');raise
                        run['path']=writer.call('close',run['status']);run['saved']=True;return self.public_run(run)
            if name=='discard':
                self.idle(devices=False);self.runs={};self.current=None;self.latest=None;self.latest_version+=1;self.latest_axis=copy.deepcopy(self.calibration['wavelengths_nm']);return dict(discarded=True)
            if name=='fit':
                if not separate:self.idle(devices=False)
                self.prune_tasks()
                if sum(t['status']=='running' for t in self.tasks.values())>=4:raise CommandError('Four fit tasks are already running or queued; wait or cancel a task')
                if not run['saved'] and len(run['metadata'].get('analysis',{}))+sum(t['status']=='running' and t.get('run_id')==run['run_id'] for t in self.tasks.values())>=64:
                    raise CommandError('Save this memory-only experiment before creating more than 64 fit results')
                options=FitRequest(**data['options']);task_id=uuid.uuid4().hex[:16]
                records=list(run['records']);axis=copy.deepcopy(run['metadata']['calibration']['wavelengths_nm'])
                self.tasks[task_id]=dict(status='running',run_id=run['run_id'])
                cancel=Event();self.task_controls[task_id]=dict(cancel=cancel)
                def fit():
                    try:
                        result=fit_records(records,axis,options,cancel=cancel)
                        result['request']=options.model_dump()
                        result['run_id']=run['run_id'];result['task_id']=task_id
                        result['record_count']=len(records)
                        result['finished_utc']=now()
                        if cancel.is_set():raise ValueError('Fit cancelled')
                        with self.analysis_lock:
                            if run['saved'] and not separate:append_analysis(run['path'],task_id,result)
                            with self.lock:
                                history=run['metadata'].setdefault('analysis',{});history[task_id]=result;run['analysis_version']=run.get('analysis_version',0)+1
                                if run['saved']:
                                    while len(history)>64:history.pop(next(iter(history)))
                            self.tasks[task_id]=dict(status='completed',run_id=run['run_id'],result=result)
                    except Exception as exc:self.tasks[task_id]=dict(status='cancelled' if cancel.is_set() else 'error',run_id=run['run_id'],error=str(exc))
                    self.emit('analysis.ready',dict(task_id=task_id,**self.tasks[task_id]))
                    self.prune_tasks()
                self.task_controls[task_id]['future']=self.pool.submit(fit);return dict(task_id=task_id)
        if name=='task_cancel':
            task=self.tasks.get(data['task_id'])
            if task is None:raise CommandError('Task not found',404)
            if task['status']=='running':
                control=self.task_controls[data['task_id']];control['cancel'].set()
                if control['future'].cancel():task['status']='cancelled'
            return dict(task_id=data['task_id'],**task)
        if name=='task':
            if data['task_id'] not in self.tasks:raise CommandError('Task not found',404)
            return self.tasks[data['task_id']]
        raise ValueError('Unknown command: '+name)


def worker_main(commands,responses,events):
    engine=Engine(events)
    while True:
        message=commands.get()
        if message['name']=='shutdown':
            engine.stop.set();engine.pause.clear()
            for control in engine.task_controls.values():control['cancel'].set()
            if engine.job:engine.job.join(10)
            engine.pool.shutdown(wait=True,cancel_futures=True)
            return
        try:
            result=engine.handle(message['name'],message['data'])
            responses.put(dict(id=message['id'],result=serial(result)))
        except Exception as exc:
            responses.put(dict(id=message['id'],error=str(exc),error_type=type(exc).__name__,status_code=error_status(exc,message['name'])))
