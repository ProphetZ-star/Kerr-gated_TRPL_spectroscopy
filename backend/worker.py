"""Hardware ownership, cancellable sequential acquisition and command dispatch."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, RLock, Thread
import copy
import json
import time
import traceback
import uuid
import numpy as np
from .core import make_plan, mm_to_delay, delay_to_mm
from .models import RunRequest, FitRequest
from .drivers.sim import SimCamera, SimStage
from .storage.hdf5 import HDFWriter, choose_path, read_run, append_analysis
from .storage.export import export_csv
from .analysis import fit_records
from .calibration_service import CalibrationWorkbench


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

    def emit(self, kind, payload):
        with self.lock:
            self.seq+=1
            message=dict(schema_version=1,worker_session_id=self.session,seq=self.seq,
                         run_id=self.current,type=kind,timestamp_utc=now(),payload=serial(payload))
        # Stream is for presentation. Authoritative records remain available via pagination.
        try:self.events.put(message,timeout=.2)
        except Exception:pass
        return message

    def public_run(self,run):
        with self.lock:
            return {k:serial(v) for k,v in run.items() if k not in {'records','writer'}}

    def status(self):
        with self.lock:
            return dict(source_mode='simulation',connected=self.connected,busy=self.busy,
                        worker_session_id=self.session,seq=self.seq,revision=self.revision,
                        stage=dict(controller_reported_mm=self.stage.position_mm,position_source='simulated_controller',
                                   delay_ps=mm_to_delay(self.stage.position_mm,self.zero),zero_mm=self.zero,zero_version=self.zero_version,
                                   moving=self.busy=='move' or bool(self.current and self.runs[self.current]['step']=='moving')),
                        camera=self.camera.capabilities()|dict(exposure_s=self.camera.exposure_s),
                        calibration=copy.deepcopy(self.calibration),spectrograph=self.spectrograph.copy(),
                        calibration_warning=self.calibration_workbench.warning,
                        latest=serial(self.latest),live_axis=self.latest_axis,background=serial(self.background),
                        run=self.public_run(self.runs[self.current]) if self.current else None)

    def idle(self):
        if not self.connected:raise ValueError('Devices are disconnected')
        if self.busy:raise ValueError('Devices are busy: '+self.busy)

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
        return dict(meta=dict(schema_version=1,software_version='0.1.0',source_mode='simulation',run_id=run_id,created_utc=now()),
                    config=dict(request=request,plan=plan,applied=dict(read_mode='fvb',exposure_s=request['exposure_s'],hbin=1)),
                    devices=dict(camera=self.camera.capabilities(),stage=dict(model='PI M-414.32S (simulated)',position_source='simulated_controller')),
                    coordinates=dict(zero_mm=self.zero,zero_version=self.zero_version,path_multiplier=2,delay_sign=1),
                    calibration=copy.deepcopy(self.calibration),background=serial(self.background),
                    simulation=dict(seed=request['seed'],speedup=request['speedup'],fault=request['fault'],
                                    tau_ps=[.42,3.8],irf_fwhm_ps=.25,gain_e_per_count=2.5))

    def run_scan(self,run):
        request=run['request'];plan=run['plan'];writer=None
        terminal='completed'
        def step(name):
            with self.lock:run['step']=name
            message=self.emit('run.state',{k:run[k] for k in ['run_id','status','step','completed_points','committed_frames','acquired_frames']})
            if writer:writer.call('event',message)
        try:
            if request['autosave']:
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
                        record_id=len(run['records'])
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
                        if writer:writer.call('record',record|dict(inject_write_failure=fault=='write_failure'))
                        with self.lock:
                            run['records'].append(record);run['committed_frames']=len(run['records']);self.latest=record
                            self.latest_axis=run['metadata']['calibration']['wavelengths_nm']
                        if time.monotonic()-self.last_preview>=.1 or record_id+1==plan['total_frames']:
                            self.emit('spectrum.preview',record);self.last_preview=time.monotonic()
                        self.emit('storage.status',dict(committed_frames=run['committed_frames'],on_disk=bool(writer)))
                    with self.lock:run['completed_points']+=1
                    self.emit('point.committed',dict(completed_points=run['completed_points'],total_points=plan['total_points']))
                    if self.pause.is_set():
                        with self.lock:run['status']='paused';run['step']='paused'
                        self.emit('run.state',self.public_run(run))
                        while self.pause.is_set():self.wait(.05,1)
                        with self.lock:run['status']='running'
            if self.stop.is_set():raise Cancelled()
        except Cancelled:
            terminal='stopped'
        except Exception as exc:
            terminal='error';run['error']=str(exc)
            self.emit('run.error',dict(message=str(exc),committed_frames=len(run['records'])))
        finally:
            with self.lock:run['status']='finalizing';run['step']='finalizing'
            self.emit('run.state',self.public_run(run))
            try:
                if writer:
                    try:writer.call('event',dict(type='run.terminal',status=terminal,error=run['error'],timestamp_utc=now()))
                    except Exception as exc:
                        terminal='error';run['error']=f'Event write failed: {exc}'
                    run['path']=writer.call('close',terminal)
                    run['saved']=True
            except Exception as exc:
                terminal='error';run['error']=f'Finalization failed: {exc}'
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
                self.latest_axis=self.calibration['wavelengths_nm']
            self.emit('spectrum.preview',self.latest);index+=1
            self.wait(.2,1)

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
        request_id=data.get('request_id')
        key=(name,request_id)
        if request_id and key in self.commands:
            old,result=self.commands[key]
            if old!=data:raise ValueError('request_id was already used with different parameters')
            return result
        result=self._handle(name,data)
        if request_id:
            self.commands[key]=(copy.deepcopy(data),result)
            if len(self.commands)>1000:self.commands.pop(next(iter(self.commands)))
        return result

    def _handle(self,name,data):
        if name=='status':return self.status()
        if name=='capabilities':return self.camera.capabilities()
        if name=='connect':
            if data.get('mode','simulation')!='simulation':
                raise ValueError('Hardware mode is not commissioned. Supply the PI controller model and validate adapters first; no simulation fallback.')
            if self.busy:raise ValueError('Devices are busy')
            self.connected=True;return self.status()
        if name=='disconnect':
            self.idle();self.connected=False;return self.status()
        if name=='configure':
            self.idle();self.camera.exposure_s=data['exposure_s'];self.revision+=1;self.background=None
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
                self.calibration_workbench.invalidate()
                self.background=None;self.revision+=1
            return self.status()
        if name=='calibration':
            self.idle();self.calibration_workbench.persist(data,self.spectrograph)
            self.calibration=data;self.revision+=1;return self.status()
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
            self.revision+=1
            self.emit('calibration.applied',dict(calibration_id=self.calibration['calibration_id']))
            return self.status()
        if name=='plan':
            request=RunRequest(**data)
            return make_plan(request,mm_to_delay(self.stage.position_mm,self.zero),self.zero,revision=self.revision)
        if name=='run':
            self.idle();request=RunRequest(**data)
            plan=make_plan(request,mm_to_delay(self.stage.position_mm,self.zero),self.zero,revision=self.revision)
            # Keep at most one in-memory run; saved experiments can be reopened.
            if self.current and not self.runs[self.current]['saved'] and self.runs[self.current]['records']:
                raise ValueError('Save or explicitly discard the previous memory-only run before starting another')
            run_id=uuid.uuid4().hex[:16]
            path=choose_path(request.save_path,run_id,plan['estimated_bytes']) if request.autosave else None
            metadata=self.metadata(data,plan,run_id);metadata['config']['center_nm']=self.spectrograph['center_nm']
            run=dict(run_id=run_id,status='preparing',step='preparing',request=data,plan=plan,records=[],
                     metadata=metadata,path=str(path) if path else None,saved=False,created_utc=now(),ended_utc=None,
                     acquired_frames=0,committed_frames=0,completed_points=0,error=None)
            self.runs={run_id:run};self.current=run_id;self.camera.exposure_s=request.exposure_s
            self.launch('scan',self.run_scan,run);return self.public_run(run)
        if name in {'pause','resume','stop'}:
            run=self.runs.get(data['run_id'])
            if not run or self.current!=data['run_id'] or self.busy!='scan':raise ValueError('No matching active run')
            if name=='pause':
                if run['status']!='running':raise ValueError('Run is not running')
                self.pause.set();run['status']='pausing'
            elif name=='resume':
                if run['status'] not in {'paused','pausing'}:raise ValueError('Run is not paused')
                self.pause.clear();run['status']='running'
            else:
                self.stop.set();self.pause.clear();run['status']='stopping'
            self.emit('run.state',self.public_run(run));return self.public_run(run)
        if name=='preview_start':self.launch('preview',self.preview);return dict(accepted=True)
        if name in {'preview_stop','stage_stop'}:
            if self.busy not in {'preview','move',None}:raise ValueError('Stop the active scan using its run ID')
            self.stop.set();return dict(accepted=True)
        if name=='background':self.launch('background',self.acquire_background,data);return dict(accepted=True)
        if name=='open':
            self.idle()
            if self.current and not self.runs[self.current]['saved'] and self.runs[self.current]['records']:raise ValueError('Save or discard the current run first')
            metadata,records,terminal=read_run(data['path']);run_id=metadata['meta']['run_id']
            run=dict(run_id=run_id,status=terminal,step='idle',metadata=metadata,records=records,
                     request=metadata['config']['request'],plan=metadata['config']['plan'],path=data['path'],saved=True,
                     acquired_frames=len(records),committed_frames=len(records),
                     completed_points=len({(r['round_index'],r['point_index']) for r in records if r['frame_index']==metadata['config']['request']['frames_per_point']-1}),
                     created_utc=metadata['meta']['created_utc'],ended_utc=None,error=None)
            self.runs={run_id:run};self.current=run_id;self.latest=records[-1] if records else None
            self.latest_axis=metadata['calibration']['wavelengths_nm']
            return self.public_run(run)
        if name in {'get_run','records','export','save','fit','discard'}:
            run=self.runs.get(data.get('run_id',self.current))
            if not run:raise ValueError('Run not found')
            if name=='get_run':return self.public_run(run)
            if name=='records':
                offset=data.get('offset',0);limit=data.get('limit',100)
                return dict(records=serial(run['records'][offset:offset+limit]),total=len(run['records']))
            if name=='export':
                return export_csv(serial(run['records']),run['metadata']['calibration']['wavelengths_nm'],data['center_nm'],data['half_window_nm'],run['metadata']['meta']['source_mode'])
            if name=='save':
                self.idle()
                with self.analysis_lock:
                    if run['saved']:return self.public_run(run)
                    path=choose_path(data.get('path'),run['run_id'],run['plan']['estimated_bytes'])
                    writer=HDFWriter(path,run['metadata'])
                    try:
                        for record in run['records']:writer.call('record',record)
                    except Exception:
                        writer.call('close','error');raise
                    run['path']=writer.call('close',run['status']);run['saved']=True;return self.public_run(run)
            if name=='discard':
                self.idle();self.runs={};self.current=None;return dict(discarded=True)
            if name=='fit':
                self.idle()
                options=FitRequest(**data['options']);task_id=uuid.uuid4().hex[:16]
                records=list(run['records']);axis=copy.deepcopy(run['metadata']['calibration']['wavelengths_nm'])
                self.tasks[task_id]=dict(status='running',run_id=run['run_id'])
                def fit():
                    try:
                        result=fit_records(records,axis,options)
                        result['request']=options.model_dump()
                        result['run_id']=run['run_id'];result['task_id']=task_id
                        result['record_count']=len(records)
                        result['finished_utc']=now()
                        with self.analysis_lock:
                            if run['saved']:append_analysis(run['path'],task_id,result)
                            with self.lock:run['metadata'].setdefault('analysis',{})[task_id]=result
                            self.tasks[task_id]=dict(status='completed',run_id=run['run_id'],result=result)
                    except Exception as exc:self.tasks[task_id]=dict(status='error',error=str(exc))
                    self.emit('analysis.ready',dict(task_id=task_id,**self.tasks[task_id]))
                self.pool.submit(fit);return dict(task_id=task_id)
        if name=='task':
            if data['task_id'] not in self.tasks:raise ValueError('Task not found')
            return self.tasks[data['task_id']]
        raise ValueError('Unknown command: '+name)


def worker_main(commands,responses,events):
    engine=Engine(events)
    while True:
        message=commands.get()
        if message['name']=='shutdown':
            engine.stop.set();engine.pause.clear()
            if engine.job:engine.job.join(10)
            engine.pool.shutdown(wait=False,cancel_futures=True)
            return
        try:
            result=engine.handle(message['name'],message['data'])
            responses.put(dict(id=message['id'],result=serial(result)))
        except Exception as exc:
            responses.put(dict(id=message['id'],error=str(exc),error_type=type(exc).__name__))
