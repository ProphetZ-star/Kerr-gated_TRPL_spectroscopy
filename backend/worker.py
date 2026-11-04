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
from .models import RunRequest, FitRequest, SpectrographConfig
from .drivers.sim import SimCamera, SimStage
from .devices import DeviceConnections, serial_ports
from .drivers.hardware import HardwareError, AcquisitionCancelled, camera_choices
from .storage.hdf5 import HDFWriter, read_run, append_analysis
from .storage.frames import FrameTable
from .storage import schema
from .storage.output import output_info,prepare_output
from .storage.export import export_csv
from .calibration_service import CalibrationWorkbench
import os
from pathlib import Path

DEVICE_LOG=Path(os.environ.get('KERR_STATE_DIR',Path(__file__).resolve().parents[1]/'data'))/'device-errors.log'

def log_device_error(device,message,link):
    '''Timestamped record of every device error, so an unstable connection can be told apart from rejected commands.'''
    try:
        DEVICE_LOG.parent.mkdir(parents=True,exist_ok=True)
        with DEVICE_LOG.open('a',encoding='utf-8') as stream:
            stream.write(f"{datetime.now().isoformat(timespec='milliseconds')}\t{device}\t{'link' if link else 'command'}\t{message}\n")
    except OSError:pass
from .readout import readout_axis,readout_shape
from .errors import CommandError,error_status


def fit_run(*args,**kwargs):
    # Keep expensive fitting imports out of the worker's first status handshake.
    from .analysis import fit_run as fit
    return fit(*args,**kwargs)


def run_inputs(run,count=None):
    """What processing needs from a run besides its frames: wavelength axis, background, full scale, plan.

    Frames are only ever appended, so the first count rows are a stable snapshot for another thread."""
    frames=run['frames'];count=len(frames) if count is None else count;metadata=run['metadata']
    background=metadata.get('background') or {}
    full_scale=((metadata.get('instrument') or {}).get('camera') or {}).get('full_scale')
    return dict(block=frames.block(0,count),axis=copy.deepcopy(metadata['calibration']['wavelengths_nm']),
                background=background.get('spectrum') if background.get('present') else None,
                full_scale=full_scale,targets_mm=(metadata.get('acquisition') or {}).get('targets_mm'))


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
    def __init__(self, events, device_factories=None):
        self.events=events;self.lock=RLock();self.seq=0;self.session=uuid.uuid4().hex
        self.devices=DeviceConnections(device_factories);self.zero=150.0;self.zero_version=1
        self.revision=0;self.busy=None;self.latest=None;self.background=None;self.background_progress=None
        self.calibration=dict(calibration_id=None,source=None,wavelengths_nm=None)
        self.latest_axis=None
        self.spectrograph=dict(center_nm=600.0,grating=300,slit_um=200.0)
        self.calibration_workbench=CalibrationWorkbench()
        recorded=self.calibration_workbench.recorded_spectrograph()
        if recorded is not None:self.spectrograph=recorded
        restored=self.calibration_workbench.restore(self.spectrograph)
        if restored is not None and restored.get('source')=='measured':self.calibration=restored
        self.latest_axis=self.live_axis()
        self.analysis_lock=RLock()
        self.current=None;self.runs={};self.stop=Event();self.pause=Event();self.job=None
        self.commands={};self.tasks={};self.pool=ThreadPoolExecutor(max_workers=1);self.last_preview=0
        self.task_controls={};self.analysis_runs={}
        self.shutdown_prepared=False;self.preview_moving=False
        self.event_times={};self.latest_version=0;self.capture_tasks={};self.device_generation=0
        self.mode_calibrations={'hardware':copy.deepcopy(self.calibration)}
        self.mode_zeros={'hardware':self.zero};self.mode_spectrographs={'hardware':self.spectrograph.copy()}

    @property
    def camera(self):return self.devices.camera

    @property
    def stage(self):return self.devices.stage

    @property
    def connected(self):return all(link['connected'] for link in self.devices.links.values())

    def live_axis(self):
        # The installed calibration covers the full chip; live spectra use the current ROI/binning.
        readout=getattr(self.camera,'readout',None)
        axis=self.calibration['wavelengths_nm']
        return copy.deepcopy(axis) if readout is None else readout_axis(axis,readout)

    def stage_state(self,run=None):
        connected=self.devices.connected('stage');simulated=self.devices.mode=='simulation'
        return dict(velocity_mm_s=getattr(self.stage,'velocity_mm_s',None) if connected else None,
                    max_velocity_mm_s=getattr(self.stage,'max_velocity_mm_s',None) if connected else None,
                    referenced=bool(getattr(self.stage,'referenced',True)) if connected else None,
                    controller_reported_mm=self.stage.position_mm if connected else None,
                    position_source='simulated_controller' if simulated else getattr(self.stage,'position_source','controller') if connected else 'unavailable',
                    position_sensor=bool(getattr(self.stage,'position_sensor',False)),
                    delay_ps=mm_to_delay(self.stage.position_mm,self.zero) if connected else None,
                    zero_mm=self.zero,zero_version=self.zero_version,
                    moving=self.preview_moving or self.busy=='move' or bool(self.busy=='scan' and run and run['step']=='moving'))

    @staticmethod
    def run_clock(run):
        return time.time()-run['clock_origin']

    def hardware_failure(self,exc):
        # A rejected command or timeout is reported but leaves the device connected; only a lost link takes it offline.
        link=getattr(exc,'link',True)
        log_device_error(exc.device,str(exc),link)
        if link:self.devices.failed(exc.device,exc)
        self.revision+=1
        self.emit('device.error',dict(device=exc.device,message=str(exc),link=link))

    def require_operation(self,name):
        needed={'configure':('camera',),'move':('stage',),'zero':('stage',),
                'preview_start':('camera',),'background':('camera',),
                'calibration_capture':('camera',),'run':('stage','camera'),'camera_readout':('camera',),'camera_cooling':('camera',),
                'stage_velocity':('stage',),'stage_reference':('stage',)}.get(name,())
        self.devices.require(*needed)
        if name in ('move','run') and not getattr(self.stage,'referenced',True):
            raise CommandError('Reference the delay stage before moving it')

    def acquire_counts(self,delay,seed,index,exposure,center,fault='none',**options):
        if self.devices.mode=='simulation':return self.camera.acquire(delay,seed,index,exposure,center,fault,**options)
        try:return self.camera.capture_spectrum(exposure,self.stop)
        except AcquisitionCancelled:raise Cancelled()

    def make_scan_plan(self,request):
        limits=(self.stage.lower,self.stage.upper) if self.devices.mode=='hardware' and self.devices.connected('stage') else (0.,300.)
        plan=make_plan(request,mm_to_delay(self.stage.position_mm,self.zero),self.zero,revision=self.revision,limits=limits)
        if self.devices.mode=='hardware' and self.devices.connected('stage'):
            for target in plan['targets_mm']+[plan['approach_mm']]:self.stage.duration(target)
        return plan

    def source_changed(self):
        self.latest=None;self.latest_version+=1;self.background=None
        self.latest_axis=self.live_axis()
        self.calibration_workbench.invalidate();self.capture_tasks={};self.commands.clear();self.revision+=1;self.device_generation+=1
        self.emit('device.status',dict(source_mode=self.devices.mode,devices=self.devices.status()))

    def clear_wavelength_calibration(self):
        empty=dict(calibration_id=None,source=None,wavelengths_nm=None)
        self.calibration_workbench.persist(empty,self.spectrograph)
        self.calibration=empty;self.latest_axis=None;self.background=None
        self.calibration_workbench.invalidate();self.revision+=1

    def capture_hardware_frame(self,capture_id,data):
        try:
            image=self.camera.capture(data['exposure_s'],data['readout'],self.stop)
            frame=self.calibration_workbench.record_hardware_frame(image,self.camera.exposure_s,data['readout'],self.spectrograph,capture_id)
            self.capture_tasks[capture_id]=dict(status='completed',frame=frame)
        except AcquisitionCancelled:self.capture_tasks[capture_id]=dict(status='cancelled',error='CCD capture cancelled')
        except Exception as exc:
            self.capture_tasks[capture_id]=dict(status='error',error=str(exc))
            if isinstance(exc,HardwareError):self.hardware_failure(exc)
        self.emit('calibration.ready',dict(capture_id=capture_id,status=self.capture_tasks[capture_id]['status']))

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
            return {k:serial(v) for k,v in run.items() if k not in {'frames','writer'}}

    def compact_status(self,since=None,latest_version=-1):
        with self.lock:
            run=self.runs.get(self.current)
            stamp=(self.session,self.revision,self.current,run.get('analysis_version',0) if run else 0,
                   id(self.latest_axis),id(self.background),self.calibration_workbench.warning)
            token=hashlib.sha256(repr(stamp).encode()).hexdigest()
            full=since!=token
            state=dict(source_mode=self.devices.mode,device_generation=self.device_generation,connected=self.connected,devices=self.devices.status(),available=self.devices.available(),busy=self.busy,worker_session_id=self.session,seq=self.seq,revision=self.revision,
                       stage=self.stage_state(run),
                       camera=self.camera.capabilities()|dict(exposure_s=self.camera.exposure_s),
                       background_progress=copy.deepcopy(self.background_progress),
                       run={k:serial(v) for k,v in run.items() if k not in {'frames','writer','metadata','request','plan'}} if run else None,
                       _sync=dict(full=full,token=token,latest_version=self.latest_version))
            if full:
                state.update(calibration=serial(self.calibration),spectrograph=self.spectrograph.copy(),calibration_warning=self.calibration_workbench.warning,
                             live_axis=serial(self.latest_axis),background=serial(self.background))
                if run:
                    # The whole fit history (at most 64) goes along so Analysis can list and reopen every saved fit.
                    state['run'].update(metadata=serial(run['metadata']),request=serial(run['request']),plan=serial(run['plan']))
            if full or latest_version!=self.latest_version:state['latest']=serial(self.latest)
            return state

    def status(self):
        with self.lock:
            return dict(source_mode=self.devices.mode,device_generation=self.device_generation,connected=self.connected,devices=self.devices.status(),available=self.devices.available(),busy=self.busy,
                        worker_session_id=self.session,seq=self.seq,revision=self.revision,
                        stage=self.stage_state(self.runs.get(self.current)),
                        camera=self.camera.capabilities()|dict(exposure_s=self.camera.exposure_s),
                        calibration=copy.deepcopy(self.calibration),spectrograph=self.spectrograph.copy(),
                        calibration_warning=self.calibration_workbench.warning,
                        latest=serial(self.latest),live_axis=self.latest_axis,background=serial(self.background),
                        run=self.public_run(self.runs[self.current]) if self.current else None)

    def idle(self,devices=True):
        if self.busy:raise CommandError('Devices are busy: '+self.busy)

    def set_exposure(self,exposure):
        if self.camera.exposure_s!=exposure:
            if self.devices.mode=='hardware':self.camera.set_exposure(exposure)
            else:self.camera.exposure_s=exposure
            self.background=None;self.revision+=1

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
            except (Cancelled,AcquisitionCancelled):pass
            except HardwareError as exc:self.hardware_failure(exc)
            except Exception as exc:
                self.emit('device.error',dict(message=str(exc)))
            finally:
                with self.lock:self.busy=None
                self.emit('device.status',dict(busy=None))
        self.job=Thread(target=wrapped,daemon=True,name='device-owner')
        self.job.start()

    def move(self,target,speedup=50):
        if self.devices.mode=='hardware':
            try:self.stage.move_to(target,self.stop)
            except AcquisitionCancelled:raise Cancelled()
            self.emit('stage.position',self.stage_state());return
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

    def run_calibration(self):
        # Runs store the axis of their own binned pixels. A fitted mapping keeps its polynomial, from which the
        # full-chip axis follows; only a calibration without one keeps the chip axis, and only when it differs.
        calibration=self.calibration;fit=calibration.get('provenance') or {}
        chip=calibration.get('wavelengths_nm')
        axis=self.live_axis() if getattr(self.camera,'readout',None) is not None and chip is not None else copy.deepcopy(chip)
        pairs=[[pair['pixel'],pair['wavelength_nm']] for pair in fit.get('line_pairs') or [] if pair.get('enabled',True)]
        result=dict(wavelengths_nm=axis,coefficients=fit.get('coefficients'),line_pairs=pairs or None,
                    calibration_id=calibration.get('calibration_id'),source=calibration.get('source'),installed_utc=calibration.get('installed_utc'),
                    convention=calibration.get('convention') or fit.get('convention'),degree=fit.get('degree'),
                    pixel_origin=fit.get('pixel_origin'),pixel_scale=fit.get('pixel_scale'),rms_nm=fit.get('rms_nm'),
                    max_error_nm=fit.get('max_error_nm'),reference_name=fit.get('reference_name'))
        if fit.get('coefficients') is None and chip is not None and list(chip)!=list(axis):result['chip_wavelengths_nm']=copy.deepcopy(chip)
        return result

    def run_background(self):
        background=self.background
        if not background:return dict(present=False,spectrum=None,frames=None,condition=None,temperature_c=None,acquired_utc=None)
        return dict(present=True,spectrum=copy.deepcopy(background['spectrum']),frames=background.get('frames',1),condition=background.get('condition'),
                    temperature_c=background.get('temperature_c'),acquired_utc=background.get('timestamp_utc'))

    def metadata(self,request,plan,run_id,created):
        """Run description in the layout of the experiment file (backend/storage/hdf5.py)."""
        readout=copy.deepcopy(self.camera.readout);camera=self.camera.capabilities();links=self.devices.status()
        spectrometer=links['spectrometer'];stage=self.stage_state()
        height=readout['y1']-readout['y0']+1
        # Field lists come from the file schema; whatever a device does not report is stored empty.
        return dict(root=dict(schema_version=schema.SCHEMA_VERSION,software_version=schema.SOFTWARE_VERSION,run_id=run_id,source_mode=self.devices.mode,
                              created_utc=created.isoformat(),ended_utc=None,terminal_status=None,completed_rounds=0),
                    instrument=dict(
                        camera=schema.pick(camera,schema.INSTRUMENT['camera']),
                        stage=dict(model='PI M-414.32S',controller_model=getattr(self.stage,'controller_model',None),
                                   controller_identity=links['stage']['identity'],velocity_mm_s=stage['velocity_mm_s'],referenced=stage['referenced'],
                                   position_source=stage['position_source'],position_sensor=stage['position_sensor']),
                        spectrometer=dict(driver=spectrometer['config'].get('driver'),model=spectrometer['config'].get('model'),
                                          manual=spectrometer['capabilities'].get('manual'),identity=spectrometer['identity'])),
                    request=dict(schema.pick(request,schema.REQUEST),segments=copy.deepcopy(request['segments'])),
                    coordinates=dict(zero_mm=plan['zero_mm'],path_multiplier=plan['path_multiplier'],delay_sign=plan['delay_sign']),
                    acquisition=dict({k:copy.deepcopy(plan[k]) for k in schema.PLAN_ARRAYS},**schema.pick(plan,schema.PLAN),
                                     exposure_s=self.camera.exposure_s,readout_mode=readout['mode'],
                                     readout_roi=[readout['x0'],readout['x1'],readout['y0'],readout['y1']],hbin=readout['hbin'],
                                     # V bin "full" (all rows summed on the chip) is stored as the ROI height.
                                     vbin=height if readout['vbin']=='full' else readout['vbin'],pixels=readout_shape(readout)[1],
                                     center_nm=self.spectrograph['center_nm'],grating=self.spectrograph['grating'],slit_um=self.spectrograph['slit_um']),
                    calibration=self.run_calibration(),background=self.run_background(),events=[])

    def run_scan(self,run):
        request=run['request'];plan=run['plan'];writer=run.get('writer')
        terminal='completed';pending=deque()
        def commit(record):
            with self.lock:
                run['frames'].append(record);run['committed_frames']=len(run['frames']);self.latest=record;self.latest_version+=1
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
            self.emit('run.state',{k:run[k] for k in ['run_id','status','step','completed_points','committed_frames','acquired_frames']})
        def transition(status,message=None):
            # The file's event log holds state changes only; progress goes to the live display.
            event=dict(t_s=self.run_clock(run),status=status,message=message or '')
            with self.lock:run['metadata']['events'].append(event)
            return event
        try:
            if request['autosave'] and writer is None:
                writer=HDFWriter(run['path'],run['metadata'])
                with self.lock:run['writer']=writer
            with self.lock:run['status']='running'
            event=transition('running')
            if writer:writer.submit('event',event)
            for round_index in range(plan['rounds']):
                with self.lock:run['current_round']=round_index
                if request['mode']!='single':
                    step('moving');self.move(plan['approach_mm'],request['speedup'])
                for point_index,(delay,target) in enumerate(zip(plan['delays_ps'],plan['targets_mm'])):
                    if self.stop.is_set():raise Cancelled()
                    fault=request['fault']['kind'] if request['fault']['point_index']==point_index else 'none'
                    step('moving')
                    if fault=='move_timeout':raise TimeoutError('Injected stage move timeout')
                    self.move(target,request['speedup'])
                    step('settling');self.wait(request['settle_s'],request['speedup'] if self.devices.mode=='simulation' else 1)
                    for frame_index in range(request['frames_per_point']):
                        started=self.run_clock(run);step('exposing')
                        if self.devices.mode=='simulation':self.wait(request['exposure_s'],request['speedup'])
                        if fault=='camera_disconnect':raise ConnectionError('Injected camera disconnect')
                        step('reading')
                        if self.devices.mode=='simulation':self.wait(.03,request['speedup'])
                        if self.stop.is_set():raise Cancelled()
                        drain();record_id=run['acquired_frames']
                        spectrum,saturated=self.acquire_counts(delay,request['seed'],record_id,request['exposure_s'],
                                                               run['metadata']['acquisition']['center_nm'],fault)
                        ended=self.run_clock(run);camera=self.camera.capabilities()
                        record=dict(record_id=record_id,round_index=round_index,point_index=point_index,frame_index=frame_index,
                                    delay_ps=delay,stage_reported_mm=self.stage.position_mm,exposure_s=self.camera.exposure_s,
                                    temperature_c=camera.get('temperature_c'),temperature_state=camera.get('temperature_state'),
                                    t_start_s=started,t_end_s=ended,saturated_pixels=saturated,spectrum=spectrum,source_mode=self.devices.mode)
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
                        event=transition('paused')
                        if writer:writer.submit('event',event)
                        self.emit('run.state',self.public_run(run))
                        while self.pause.is_set():self.wait(.05,1)
                        with self.lock:run['status']='running'
                        event=transition('running')
                        if writer:writer.submit('event',event)
                with self.lock:run['completed_rounds']=round_index+1
            drain(wait=True)
            if self.stop.is_set():raise Cancelled()
        except Cancelled:
            terminal='stopped'
        except Exception as exc:
            if isinstance(exc,HardwareError):self.hardware_failure(exc)
            terminal='error';run['error']=str(exc)
            self.emit('run.error',dict(message=str(exc),committed_frames=len(run['frames'])))
        finally:
            # Stop/pause acknowledge every already-acquired queued frame. Failed
            # writes expose only the successfully committed contiguous prefix.
            try:drain(wait=True)
            except Exception as exc:terminal='error';run['error']=str(exc)
            with self.lock:
                if terminal=='completed' and self.stop.is_set():terminal='stopped'
                run['status']='finalizing';run['step']='finalizing'
            self.emit('run.state',self.public_run(run))
            # Only whole rounds are kept when a scan is stopped: the round in progress is dropped, in memory
            # and in the file, so every stored round covers the full delay range.
            per_round=len(plan['delays_ps'])*request['frames_per_point']
            whole=len(run['frames'])//per_round;message=run['error']
            if terminal=='stopped':
                keep=whole*per_round;dropped=len(run['frames'])-keep
                try:
                    if writer and dropped:writer.call('truncate',keep)
                    with self.lock:
                        run['frames'].truncate(keep);run['committed_frames']=run['acquired_frames']=keep
                        run['completed_points']=whole*len(plan['delays_ps']);run['dropped_frames']=dropped
                        self.revision+=1
                    message=f"Stopped after {whole} complete round{'s' if whole!=1 else ''}"+(f'; the {dropped} frames of round {whole+1} were discarded' if dropped else '')
                except Exception as exc:
                    terminal='error';run['error']=message=f'Could not drop the unfinished round: {exc}'
            with self.lock:run['completed_rounds']=whole
            ended=now();event=transition(terminal,message)
            try:
                if writer:
                    try:writer.call('event',event)
                    except Exception as exc:
                        terminal='error';run['error']=f'Event write failed: {exc}'
                    run['path']=writer.call('close',dict(status=terminal,ended_utc=ended,completed_rounds=whole))
                    run['writer_stats']=dict(writer.stats)
                    run['saved']=True
            except Exception as exc:
                terminal='error';run['error']=(run['error']+'; ' if run['error'] else '')+f'Finalization failed: {exc}'
                run['path']=str(writer.partial) if writer else run['path']
            with self.lock:
                run.pop('writer',None);run['status']=terminal;run['step']='idle';run['ended_utc']=ended
                run['metadata']['root'].update(ended_utc=ended,terminal_status=terminal,completed_rounds=whole)
            self.emit('run.state',self.public_run(run))

    def preview(self):
        index=0
        while not self.stop.is_set():
            if self.devices.mode=='simulation':self.wait(self.camera.exposure_s,50)
            counts,saturated=self.acquire_counts(mm_to_delay(self.stage.position_mm,self.zero),101,index,self.camera.exposure_s,self.spectrograph['center_nm'])
            with self.lock:
                self.latest=dict(spectrum=counts,saturated_pixels=saturated,delay_ps=self.stage_state()['delay_ps'],exposure_s=self.camera.exposure_s,source_mode=self.devices.mode)
                self.latest_axis=self.live_axis();self.latest_version+=1
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

    def reference_stage(self):
        if self.devices.mode=='hardware':
            try:self.stage.reference(self.stop)
            except AcquisitionCancelled:raise Cancelled()
        else:
            self.wait(4,1)
            with self.lock:self.stage.referenced=True
        with self.lock:self.revision+=1
        self.emit('stage.position',self.stage_state())

    def acquire_background(self,data):
        # The mean of several frames lowers the noise the subtraction adds; Stop keeps the frames already taken.
        exposure=self.camera.exposure_s;wanted=int(data.get('frames',1));total=None;taken=0
        with self.lock:self.background_progress=dict(frames=0,total=wanted)
        try:
            for index in range(wanted):
                if self.stop.is_set():break
                try:
                    if self.devices.mode=='simulation':self.wait(exposure,50)
                    counts,_=self.acquire_counts(0,849,index,exposure,self.spectrograph['center_nm'],dark=data['kind']=='dark',optical_background=data['kind']=='optical_background')
                except (Cancelled,AcquisitionCancelled):break  # Stop during an exposure keeps the frames already averaged
                counts=np.asarray(counts,dtype=float);total=counts if total is None else total+counts;taken+=1
                with self.lock:self.background_progress=dict(frames=taken,total=wanted)
        finally:
            with self.lock:self.background_progress=None
        if not taken:return
        with self.lock:
            self.background=dict(spectrum=(total/taken).tolist(),frames=taken,exposure_s=exposure,kind=data['kind'],condition=data['condition'],source_mode=self.devices.mode,
                                 revision=self.revision,temperature_c=self.camera.capabilities().get('temperature_c'),read_mode=getattr(self.camera,'readout',{}).get('mode','fvb'),timestamp_utc=now())
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
        self.require_operation(name)
        try:result=self.edit_preview(name,data) if self.busy=='preview' and name in {'configure','move','zero','spectrograph','camera_readout','stage_velocity'} else self._handle(name,data)
        except HardwareError as exc:self.hardware_failure(exc);raise
        if request_id:
            self.commands[key]=(copy.deepcopy(data),result)
            if len(self.commands)>1000:self.commands.pop(next(iter(self.commands)))
        return result

    def _handle(self,name,data):
        if name=='status':return self.compact_status(data.get('since'),data.get('latest_version',-1)) if data.get('compact') else self.status()
        if name=='capabilities':return self.camera.capabilities()
        if name=='connect':
            self.idle(devices=False)
            mode=data.get('mode','hardware')
            if mode not in {'hardware','simulation'}:raise ValueError('Unknown acquisition source')
            if mode!=self.devices.mode or mode=='simulation' and not self.connected:
                self.mode_calibrations[self.devices.mode]=copy.deepcopy(self.calibration)
                self.mode_zeros[self.devices.mode]=self.zero;self.mode_spectrographs[self.devices.mode]=self.spectrograph.copy()
                self.devices.switch(mode)
                self.zero=self.mode_zeros.get(mode,150.0);self.zero_version+=1
                self.spectrograph=self.mode_spectrographs.get(mode,dict(center_nm=600.0,grating=300,slit_um=200.0)).copy()
                self.calibration_workbench.set_mode(mode)
                self.calibration=copy.deepcopy(self.mode_calibrations.get(mode) or (dict(calibration_id='synthetic-500-800-v1',source='synthetic',wavelengths_nm=self.camera.axis.tolist()) if mode=='simulation' else dict(calibration_id=None,source=None,wavelengths_nm=None)))
                self.source_changed()
            return self.status()
        if name=='disconnect':
            self.idle(devices=False);self.devices.close();self.source_changed();return self.status()
        if name=='device_ports':return serial_ports()
        if name=='device_drivers':return self.devices.catalog()
        if name=='device_pi_usb':
            self.idle(devices=False)
            if self.devices.connected('stage'):raise CommandError('Disconnect the stage before finding USB controllers')
            from .drivers.pi_usb import usb_choices
            return usb_choices(data.get('pi_dll',''))
        if name=='device_cameras':
            self.idle(devices=False)
            if self.devices.connected('camera'):raise CommandError('Disconnect the CCD before scanning SDK2 cameras')
            return camera_choices(data.get('dll_directory',''))
        if name in {'device_connect','device_disconnect'}:
            self.idle(devices=False)
            before=self.devices.status()
            old_spec=copy.deepcopy(self.devices.links['spectrometer']['config'])
            try:
                if name=='device_connect':self.devices.connect(data['device'],{k:v for k,v in data.items() if k!='device'})
                else:self.devices.disconnect(data['device'])
            finally:
                spec=self.devices.links['spectrometer'];optics_changed=spec.get('reported_settings') is not None and spec['reported_settings']!=self.spectrograph
                if data['device']=='spectrometer' and (old_spec!=spec['config'] or optics_changed):
                    if spec.get('reported_settings') is not None:self.spectrograph=spec['reported_settings'].copy()
                    self.calibration_workbench.set_instrument(self.devices.links['spectrometer']['config'])
                    self.clear_wavelength_calibration()
                    # Never restore an axis cached for another spectrometer after a source switch.
                    self.mode_calibrations.clear()
                if before!=self.devices.status():self.source_changed()
            return self.status()
        if name=='configure':
            self.idle();self.set_exposure(data['exposure_s'])
            return dict(exposure_s=self.camera.exposure_s)
        if name=='camera_readout':
            self.idle()
            readout,notes=self.camera.set_readout(data)
            with self.lock:
                self.latest=None;self.latest_version+=1;self.background=None;self.latest_axis=self.live_axis();self.revision+=1
            return dict(readout=readout,notes=notes,pixels=readout_shape(readout)[1])
        if name=='camera_cooling':
            return self.camera.set_cooling(data['enabled'],data['target_c'])
        if name=='stage_velocity':
            self.idle()
            return self.stage.set_velocity(data['velocity_mm_s'])
        if name=='stage_reference':
            self.launch('move',self.reference_stage);return dict(accepted=True)
        if name=='move':
            current=mm_to_delay(self.stage.position_mm,self.zero)
            target=delay_to_mm(current+data['delay_ps'] if data['relative'] else data['delay_ps'],self.zero)
            self.stage.duration(target);self.launch('move',self.move,target);return dict(accepted=True)
        if name=='zero':
            self.idle()
            if self.devices.mode=='hardware':self.stage.position_mm=self.stage.value('POS?')
            self.zero=self.stage.position_mm;self.zero_version+=1;self.revision+=1;return self.status()
        if name=='spectrograph':
            self.idle()
            if data!=self.spectrograph:
                if self.devices.mode=='hardware':self.devices.check_spectrometer_update(self.spectrograph,data)
                self.clear_wavelength_calibration()  # Invalidate before any physical setting can change.
                if self.devices.mode=='hardware':
                    try:applied=self.devices.configure_spectrometer(self.spectrograph,data)
                    except Exception as exc:raise HardwareError('spectrometer',str(exc)) from exc
                else:applied=data
                self.spectrograph=SpectrographConfig(**applied).model_dump()
                self.calibration_workbench.persist(self.calibration,self.spectrograph)
            return self.status()
        if name=='calibration':
            self.idle(devices=False)
            if self.devices.mode=='hardware' and data.get('source')!='measured':raise ValueError('Hardware mode requires a measured wavelength calibration')
            self.calibration_workbench.persist(data,self.spectrograph)
            self.calibration=data;self.latest_axis=self.live_axis();self.revision+=1;return self.status()
        if name=='calibration_frame':
            return self.calibration_workbench.current_frame()
        if name=='calibration_capture':
            self.idle()
            if self.devices.mode=='simulation':return self.calibration_workbench.capture(data,self.spectrograph)
            capture_id=uuid.uuid4().hex
            self.capture_tasks={capture_id:dict(status='running')}
            self.calibration_workbench.invalidate()
            self.launch('calibration',self.capture_hardware_frame,capture_id,data)
            return dict(pending=True,capture_id=capture_id)
        if name=='calibration_capture_status':
            if data['capture_id'] not in self.capture_tasks:raise CommandError('Calibration capture expired',404)
            return self.capture_tasks[data['capture_id']]
        if name=='calibration_stop':
            if self.busy=='calibration':self.stop.set()
            return dict(accepted=True)
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
            self.latest_axis=self.live_axis()
            self.revision+=1
            self.emit('calibration.applied',dict(calibration_id=self.calibration['calibration_id']))
            return self.status()
        if name=='plan':
            request=RunRequest(**data)
            return self.make_scan_plan(request)
        if name=='output_check':return output_info(data['path'])
        if name=='run':
            self.idle();request=RunRequest(**data)
            if self.devices.mode=='hardware' and request.fault.kind!='none':raise ValueError('Fault injection is only available in Simulation mode')
            plan=self.make_scan_plan(request)
            if any(task['status']=='running' and task.get('run_id') in self.runs for task in self.tasks.values()):
                raise CommandError('Wait for or cancel analysis before starting another acquisition')
            run_id=uuid.uuid4().hex[:16]
            path=None;writer=None
            self.set_exposure(request.exposure_s)
            plan=self.make_scan_plan(request)
            # Every time column in the file counts seconds from this instant.
            created=datetime.now(timezone.utc)
            metadata=self.metadata(request.model_dump(),plan,run_id,created)
            if request.autosave:
                with prepare_output(request.save_path,run_id,plan['estimated_bytes'],request.overwrite_token) as path:
                    writer=HDFWriter(path,metadata)
            acquisition=metadata['acquisition']
            frames=FrameTable(acquisition['pixels'],acquisition['exposure_s'],self.devices.mode,capacity=plan['total_frames'])
            run=dict(run_id=run_id,status='preparing',step='preparing',request=data,plan=plan,frames=frames,
                     metadata=metadata,path=str(path) if path else None,saved=False,created_utc=metadata['root']['created_utc'],ended_utc=None,
                     clock_origin=created.timestamp(),
                     acquired_frames=0,committed_frames=0,completed_points=0,current_round=0,completed_rounds=0,
                     until_stopped=plan['until_stopped'],error=None)
            if writer:run['writer']=writer
            # Replace a memory-only experiment only after new output is ready.
            self.runs={run_id:run};self.current=run_id
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
                if self.current and not self.runs[self.current]['saved'] and self.runs[self.current]['frames']:raise ValueError('Save or discard the current run first')
            metadata,frames,terminal=read_run(data['path']);run_id=metadata['root']['run_id']
            if separate and len(self.analysis_runs)>=8:
                active={task['run_id'] for task in self.tasks.values() if task['status']=='running'}
                old=next((id for id in self.analysis_runs if id not in active),None)
                if old is None:raise CommandError('Close an Analysis file or finish its fit before loading another')
                self.analysis_runs.pop(old)
            if separate:run_id='analysis-'+uuid.uuid4().hex
            plan={k:metadata['acquisition'][k] for k in schema.PLAN_ARRAYS+schema.names(schema.PLAN)}
            run=dict(run_id=run_id,status=terminal,step='idle',metadata=metadata,frames=frames,
                     request=metadata['request'],plan=plan,path=data['path'],saved=True,
                     acquired_frames=len(frames),committed_frames=len(frames),
                     completed_points=frames.completed_points(metadata['request']['frames_per_point']),
                     completed_rounds=metadata['root']['completed_rounds'] or 0,current_round=None,
                     until_stopped=metadata['request']['mode']=='repeat' and metadata['request']['repeats']==0,
                     created_utc=metadata['root']['created_utc'],ended_utc=metadata['root']['ended_utc'],error=None)
            if separate:
                self.analysis_runs[run_id]=run;return self.public_run(run)
            self.runs={run_id:run};self.current=run_id;self.latest=frames.record(-1) if frames else None;self.latest_version+=1
            self.latest_axis=metadata['calibration']['wavelengths_nm']
            self.set_exposure(run['request']['exposure_s'])
            return self.public_run(run)
        if name in {'get_run','records','export','save','fit','kinetics','discard','run_files_removed','analysis_close'}:
            separate=bool(data.get('analysis'))
            run=(self.analysis_runs if separate else self.runs).get(data.get('run_id',self.current))
            if not run:raise CommandError('Run not found',404)
            if name=='analysis_close':
                if not separate:raise CommandError('Not an Analysis file')
                for id,task in self.tasks.items():
                    if task['run_id']==run['run_id'] and task['status']=='running':self._handle('task_cancel',dict(task_id=id))
                self.analysis_runs.pop(run['run_id']);return dict(closed=True)
            if name=='get_run':return self.public_run(run)
            if name=='kinetics':
                # The Analysis decay plot: the same selection and averaging the fit uses (whole delay range).
                from .processing import kinetics
                inputs=run_inputs(run);options=FitRequest(**data['options'])
                if inputs['axis'] is None:raise CommandError('Wavelength calibration is required')
                return kinetics(inputs['block'],inputs['axis'],options,inputs['background'],inputs['full_scale'],inputs['targets_mm'],delay_range=False)
            if name=='records':
                # A block of rows as arrays (spectra, index, delay_ps, ...); worker_main sends it without converting to lists.
                offset=data.get('offset',0);limit=data.get('limit',2000)
                frames=run['frames'];block=frames.block(offset,offset+limit)
                return dict(block={k:v.copy() if isinstance(v,np.ndarray) else v for k,v in block.items()},total=len(frames))
            if name=='export':
                if run['metadata']['calibration']['wavelengths_nm'] is None:raise CommandError('Wavelength calibration is required for export')
                inputs=run_inputs(run)
                return export_csv(run['frames'],run['metadata']['calibration']['wavelengths_nm'],data['center_nm'],data['half_window_nm'],run['metadata']['root']['source_mode'],
                                  inputs['background'],inputs['full_scale'],inputs['targets_mm'])
            if name=='save':
                self.idle(devices=False)
                with self.analysis_lock:
                    # A run whose unfinished files were moved to the Recycle Bin can be written out again.
                    if run['saved'] and not run.get('files_removed'):return self.public_run(run)
                    frames=run['frames'];estimate=run['plan'].get('estimated_bytes',len(frames)*(4096+2048))
                    with prepare_output(data.get('path'),run['run_id'],estimate,data.get('overwrite_token')) as path:
                        writer=HDFWriter(path,run['metadata'])
                        ended=run['ended_utc'] or now()
                        try:
                            for start in range(0,len(frames),1024):writer.call('rows',frames.block(start,start+1024))
                            for event in run['metadata'].get('events',[]):writer.call('event',event)
                        except Exception:
                            writer.call('close',dict(status='error',ended_utc=ended));raise
                        run['path']=writer.call('close',dict(status=run['status'],ended_utc=ended,completed_rounds=run.get('completed_rounds') or 0))
                        run['saved']=True;run['files_removed']=False
                        for result in run['metadata'].get('analysis',{}).values():result['stored']=True
                        run['analysis_version']=run.get('analysis_version',0)+1;return self.public_run(run)
            if name=='run_files_removed':
                # The frames stay in memory; the run still counts as handled, so a new scan can start without a prompt.
                run['files_removed']=True;run['path']=None;self.revision+=1;return self.public_run(run)
            if name=='discard':
                self.idle(devices=False);self.runs={};self.current=None;self.latest=None;self.latest_version+=1;self.latest_axis=self.live_axis();return dict(discarded=True)
            if name=='fit':
                if not separate:self.idle(devices=False)
                self.prune_tasks()
                if sum(t['status']=='running' for t in self.tasks.values())>=4:raise CommandError('Four fit tasks are already running or queued; wait or cancel a task')
                # A stopped run whose files went to the Recycle Bin keeps saved=True but has no file to append to.
                on_disk=bool(run['saved'] and run.get('path') and not run.get('files_removed'))
                if not on_disk and len(run['metadata'].get('analysis',{}))+sum(t['status']=='running' and t.get('run_id')==run['run_id'] for t in self.tasks.values())>=64:
                    raise CommandError('Save this memory-only experiment before creating more than 64 fit results')
                options=FitRequest(**data['options']);task_id=uuid.uuid4().hex[:16]
                inputs=run_inputs(run);count=len(inputs['block']['delay_ps'])
                self.tasks[task_id]=dict(status='running',run_id=run['run_id'])
                cancel=Event();self.task_controls[task_id]=dict(cancel=cancel)
                def fit():
                    try:
                        result=fit_run(inputs['block'],inputs['axis'],options,inputs['background'],inputs['full_scale'],inputs['targets_mm'],cancel=cancel)
                        result['request']=options.model_dump()
                        result['run_id']=run['run_id'];result['task_id']=task_id
                        result['record_count']=count
                        result['finished_utc']=now()
                        # Whether the fit is in the experiment file: fits on a file opened in Analysis stay in memory.
                        result['stored']=bool(on_disk and not separate)
                        if cancel.is_set():raise ValueError('Fit cancelled')
                        with self.analysis_lock:
                            if on_disk and not separate:append_analysis(run['path'],task_id,result)
                            with self.lock:
                                history=run['metadata'].setdefault('analysis',{});history[task_id]=result;run['analysis_version']=run.get('analysis_version',0)+1
                                if on_disk:
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


def shutdown_engine(engine):
    engine.stop.set();engine.pause.clear()
    for control in engine.task_controls.values():control['cancel'].set()
    if engine.job:engine.job.join(10)
    engine.pool.shutdown(wait=True,cancel_futures=True)
    if not engine.job or not engine.job.is_alive():
        try:engine.devices.close()
        except Exception:traceback.print_exc()


def watch_parent(engine):
    '''If the window process dies without sending shutdown (crash, Task Manager), stop motion, release the CCD
    and stage, and exit, instead of lingering in commands.get() while holding the hardware.'''
    import multiprocessing,multiprocessing.connection,os
    parent=multiprocessing.parent_process()
    if parent is None:return
    def run():
        multiprocessing.connection.wait([parent.sentinel])
        try:shutdown_engine(engine)
        finally:os._exit(0)
    Thread(target=run,daemon=True,name='parent-watchdog').start()


def worker_main(commands,responses,events):
    engine=Engine(events)
    watch_parent(engine)
    while True:
        message=commands.get()
        if message['name']=='shutdown':
            shutdown_engine(engine)
            return
        try:
            result=engine.handle(message['name'],message['data'])
            # Frame blocks keep their NumPy arrays: the queue pickles them far faster than lists of numbers.
            responses.put(dict(id=message['id'],result=result if message['name']=='records' else serial(result)))
        except Exception as exc:
            responses.put(dict(id=message['id'],error=str(exc),error_type=type(exc).__name__,status_code=error_status(exc,message['name'])))
