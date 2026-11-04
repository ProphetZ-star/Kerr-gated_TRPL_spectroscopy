"""Explicit hardware adapters. No SDK imports, port opens or motion on startup."""
from pathlib import Path
import ctypes as ct
import math
import os
import re
import threading
import time
import numpy as np
from ..readout import default_readout,normalize_readout,readout_shape,spectrum_full_scale


class HardwareError(ConnectionError):
    '''link=False marks a command-level failure (rejected command, timeout, position check) on a device that is
    still talking to us; only link failures should take the device offline.'''
    def __init__(self, device, message, link=True):
        self.device=device;self.link=link
        super().__init__(message)


class AcquisitionCancelled(Exception):
    pass


def sdk_library(directory=''):
    if os.name!='nt':raise ValueError('Andor SDK2 requires Windows')
    folders=[Path(directory)] if directory else [Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Andor SDK',Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Andor SOLIS']
    files=[]
    for folder in folders:
        if folder.is_file():files.append(folder)
        else:
            files.extend(folder/name for name in ('atmcd64d.dll','Drivers/atmcd64d.dll','SDK/atmcd64d.dll'))
    path=next((p.resolve() for p in files if p.is_file() and p.name.lower()=='atmcd64d.dll'),None)
    if path is None:raise ValueError('64-bit Andor SDK2 not found. Choose the folder containing atmcd64d.dll.')
    # SOLIS ships atmcd64d.dll as an aggregate that plain-LoadLibrary()s atmcd64d_legacy.dll/_sdk3.dll;
    # add_dll_directory does not cover that lookup, so without PATH it reports zero cameras.
    folder=str(path.parent);entries=os.environ.get('PATH','').split(os.pathsep)
    if not any(os.path.normcase(e.rstrip('\\/'))==os.path.normcase(folder) for e in entries if e):os.environ['PATH']=folder+os.pathsep+os.environ.get('PATH','')
    cookie=os.add_dll_directory(folder)
    try:dll=ct.WinDLL(str(path))
    except Exception:cookie.close();raise
    signatures={'GetAvailableCameras':[ct.POINTER(ct.c_long)],'GetCameraHandle':[ct.c_long,ct.POINTER(ct.c_long)],'SetCurrentCamera':[ct.c_long],
                'Initialize':[ct.c_char_p],'GetDetector':[ct.POINTER(ct.c_int),ct.POINTER(ct.c_int)],'GetCameraSerialNumber':[ct.POINTER(ct.c_int)],
                'SetReadMode':[ct.c_int],'SetAcquisitionMode':[ct.c_int],'SetTriggerMode':[ct.c_int],'SetExposureTime':[ct.c_float],
                'SetImage':[ct.c_int]*6,'GetStatus':[ct.POINTER(ct.c_int)],'GetAcquiredData':[ct.POINTER(ct.c_int32),ct.c_ulong],
                'GetAcquisitionTimings':[ct.POINTER(ct.c_float)]*3,'SetFVBHBin':[ct.c_int],
                'GetTemperature':[ct.POINTER(ct.c_int)],'GetTemperatureRange':[ct.POINTER(ct.c_int)]*2,'SetTemperature':[ct.c_int],
                'IsCoolerOn':[ct.POINTER(ct.c_int)],'CoolerON':[],'CoolerOFF':[],'SetCoolerMode':[ct.c_int],
                'StartAcquisition':[],'AbortAcquisition':[],'ShutDown':[]}
    try:
        for name,args in signatures.items():
            func=getattr(dll,name);func.argtypes=args;func.restype=ct.c_uint
    except Exception:cookie.close();raise
    return dll,cookie,path


def sdk_check(dll,name,*args):
    code=getattr(dll,name)(*args)
    if code!=20002:raise HardwareError('camera',f'Andor SDK2 {name} failed ({code}); check camera power, USB and SDK driver.')


def camera_choices(directory=''):
    dll,cookie,path=sdk_library(directory)
    try:
        count=ct.c_long();sdk_check(dll,'GetAvailableCameras',ct.byref(count))
        return dict(cameras=[dict(index=i,label=f'Andor SDK2 camera {i}') for i in range(count.value)],dll_directory=str(path.parent))
    finally:cookie.close()


class AndorSDK2Camera:
    pixels=1024
    full_scale=65535
    source_mode='hardware'
    # GetTemperature return codes describe the cooler state rather than success/failure.
    TEMPERATURE_STATES={20034:'off',20035:'not_stabilized',20036:'stabilized',20037:'not_reached',20040:'drift'}

    def __init__(self,config):
        self.exposure_s=1.0;self.dll=None;self.cookie=None;self.initialized=False
        # The status poll reads the temperature while the owner thread exposes; SDK calls never interleave.
        self.lock=threading.RLock();self._temperature=(None,'unknown',0.)
        try:
            self.dll,self.cookie,self.path=sdk_library(config.get('dll_directory',''))
            count=ct.c_long();self.call('GetAvailableCameras',ct.byref(count))
            index=config.get('camera_index',0)
            if not 0<=index<count.value:raise ValueError('Selected Andor camera is not available')
            handle=ct.c_long();self.call('GetCameraHandle',index,ct.byref(handle));self.call('SetCurrentCamera',handle)
            self.call('Initialize',str(self.path.parent).encode('mbcs'));self.initialized=True
            width=ct.c_int();height=ct.c_int();self.call('GetDetector',ct.byref(width),ct.byref(height))
            # DU920P-BEX2-DD reports 1024 x 256; full-image readout uses rows 1-255 and FVB bins the whole chip.
            if width.value!=1024 or height.value<255:raise ValueError(f'This workbench requires a 1024-pixel-wide Newton CCD with at least 255 rows (detector reports {width.value} x {height.value})')
            self.rows=height.value
            number=ct.c_int();self.call('GetCameraSerialNumber',ct.byref(number));self.serial_number=number.value
            low=ct.c_int();high=ct.c_int();self.call('GetTemperatureRange',ct.byref(low),ct.byref(high));self.temperature_range=(low.value,high.value)
            cooler=ct.c_int();self.call('IsCoolerOn',ct.byref(cooler));self.cooler_on=bool(cooler.value)
            self.target_c=max(low.value,min(high.value,-60))
            self.electrons_per_count=self._sensitivity()
            self.call('SetAcquisitionMode',1);self.call('SetTriggerMode',0)
            self.call('SetReadMode',0)
            self.readout=default_readout(self.rows)
            self.set_exposure(1.0)
        except Exception:self.close();raise

    def call(self,name,*args):
        with self.lock:sdk_check(self.dll,name,*args)

    def _sensitivity(self):
        # Electrons per count at the readout in use (AD channel 0, horizontal speed 0, conventional amplifier,
        # pre-amp gain 0: this driver never changes them). Older SDK builds lack GetSensitivity.
        function=getattr(self.dll,'GetSensitivity',None)
        if function is None:return None
        value=ct.c_float()
        try:
            with self.lock:code=function(0,0,0,0,ct.byref(value))
        except Exception:return None
        return float(value.value) if code==20002 and value.value>0 else None

    @property
    def temperature_c(self):return self.temperature()[0]

    @property
    def temperature_resolution_c(self):
        # GetTemperatureF reports fractional degrees; older SDK builds only have the whole-degree GetTemperature.
        return None if getattr(self.dll,'GetTemperatureF',None) is not None else 1.0

    def temperature(self):
        value,state,stamp=self._temperature
        if time.monotonic()-stamp>1:
            precise=getattr(self.dll,'GetTemperatureF',None)
            reading=ct.c_float() if precise is not None else ct.c_int()
            with self.lock:code=(precise or self.dll.GetTemperature)(ct.byref(reading))
            value,state=float(reading.value),self.TEMPERATURE_STATES.get(code,str(code))
            self._temperature=(value,state,time.monotonic())
        return value,state

    def capabilities(self):
        temperature,state=self.temperature()
        return dict(model='Andor Newton DU920P',shape=[255,1024],pixels=readout_shape(self.readout)[1],chip_pixels=1024,detector_rows=self.rows,
                    bit_depth=16,full_scale=spectrum_full_scale(self.readout),read_modes=['fvb','full_image'],source_mode='hardware',
                    temperature_c=temperature,temperature_state=state,cooler_on=self.cooler_on,target_temperature_c=self.target_c,
                    temperature_range_c=list(self.temperature_range),temperature_resolution_c=self.temperature_resolution_c,
                    readout=dict(self.readout),serial_number=str(self.serial_number),
                    # The SDK reports no read noise; the shot-noise limit then counts photon noise only.
                    electrons_per_count=self.electrons_per_count,read_noise_e=None)

    def set_cooling(self,enabled,target_c):
        low,high=self.temperature_range;target=int(round(max(low,min(high,target_c))))
        self.call('SetTemperature',target);self.target_c=target
        if enabled:
            # Keep regulating after ShutDown so a disconnect or exit never warms the CCD abruptly.
            self.call('SetCoolerMode',1);self.call('CoolerON')
        else:self.call('CoolerOFF')
        self.cooler_on=bool(enabled);self._temperature=(None,'unknown',0.)
        return dict(enabled=self.cooler_on,target_c=target,requested_c=target_c,clamped=target!=round(target_c),range_c=[low,high])

    def set_readout(self,data):
        readout,notes=normalize_readout(data,self.rows)
        self.readout=readout
        return readout,notes

    def set_exposure(self,value):
        self.call('SetExposureTime',float(value))
        exposure=ct.c_float();accumulation=ct.c_float();kinetic=ct.c_float()
        self.call('GetAcquisitionTimings',ct.byref(exposure),ct.byref(accumulation),ct.byref(kinetic))
        self.exposure_s=float(exposure.value)

    def capture(self,exposure_s,readout,stop):
        """Full-chip frames for calibration; independent of the acquisition ROI/binning."""
        if stop.is_set():raise AcquisitionCancelled()
        self.call('SetReadMode',0 if readout=='fvb' else 4)
        height=1 if readout=='fvb' else 255
        if height>1:self.call('SetImage',1,1,1,1024,1,255)
        else:self.call('SetFVBHBin',1)
        return self._acquire(exposure_s,height,1024,stop)

    def capture_spectrum(self,exposure_s,stop):
        """One spectrum with the acquisition readout: returns (counts, saturated read pixels)."""
        if stop.is_set():raise AcquisitionCancelled()
        r=self.readout;rows,pixels=readout_shape(r)
        if r['mode']=='fvb' and r['x0']==0 and r['x1']==1023:
            self.call('SetReadMode',0);self.call('SetFVBHBin',r['hbin'])
        else:
            self.call('SetReadMode',4)
            vbin=r['y1']-r['y0']+1 if r['vbin']=='full' else r['vbin']
            self.call('SetImage',r['hbin'],vbin,r['x0']+1,r['x1']+1,r['y0']+1,r['y1']+1)
        image=self._acquire(exposure_s,rows,pixels,stop)
        return image.sum(axis=0,dtype=np.int64).astype(np.int32),int(np.count_nonzero(image>=self.full_scale))

    def _acquire(self,exposure_s,height,width,stop):
        self.set_exposure(exposure_s)
        if stop.is_set():raise AcquisitionCancelled()
        self.call('StartAcquisition')
        try:
            deadline=time.monotonic()+exposure_s+30
            while True:
                if stop.is_set():raise AcquisitionCancelled()
                status=ct.c_int();self.call('GetStatus',ct.byref(status))
                if status.value==20073:break
                if status.value!=20072:raise HardwareError('camera',f'Unexpected Andor status {status.value}')
                if time.monotonic()>deadline:raise HardwareError('camera','Andor exposure/readout timed out')
                stop.wait(.02)
            image=np.empty((height,width),dtype=np.int32)
            self.call('GetAcquiredData',image.ctypes.data_as(ct.POINTER(ct.c_int32)),image.size)
            return image
        finally:
            # Abort is also the SDK's normal cleanup for a cancelled single exposure.
            with self.lock:self.dll.AbortAcquisition()

    def close(self):
        if self.dll is not None and self.initialized:
            with self.lock:self.dll.AbortAcquisition();self.dll.ShutDown();self.initialized=False
        if self.cookie is not None:self.cookie.close();self.cookie=None


class PIStage:
    """C-663.12 / M-414.32S. POS is the profile-generator step count, not an encoder."""
    source_mode='hardware'
    position_source='controller_step_counter'
    position_sensor=False
    controller_model='C-663.12'

    def __init__(self,config):
        self.port=None
        # One request/response at a time on the controller link, whichever thread asks.
        self.lock=threading.RLock()
        try:
            if config.get('transport','serial')=='usb':
                from .pi_usb import PIUSB
                self.port=PIUSB(config)
            else:
                try:import serial
                except ImportError as exc:raise ValueError('Install the pyserial dependency to use COM ports') from exc
                self.port=serial.Serial(config['port'],baudrate=config.get('baudrate',38400),timeout=.5,write_timeout=.5)
            self.identity=self.query('*IDN?')
            if not re.search(r'\bC-663\.12(?:\b|,)',self.identity,re.I):raise ValueError('Selected controller is not a PI C-663.12: '+self.identity)
            axes=self.query('SAI?').split()
            self.axis=config.get('axis','').strip() or (axes[0] if len(axes)==1 else '')
            if not self.axis or self.axis not in axes:raise ValueError('Choose an axis reported by the PI controller: '+', '.join(axes))
            self.stage_model=self.query('CST? '+self.axis).rsplit('=',1)[-1].strip()
            if not re.search(r'\bM-414\.32S\b',self.stage_model,re.I):raise ValueError('C-663.12 is not configured for M-414.32S. Select the stage in PIMikroMove first: '+self.stage_model)
            # SVO is motor activation for C-663; it does not imply sensor feedback.
            self.lower=self.value('TMN?');self.upper=self.value('TMX?')
            if not 0<self.upper-self.lower<=300.1:raise ValueError('PI controller travel limits are incompatible with the 300 mm M-414.32S')
            # Motor/reference state is reported, not required: the Reference action activates and references the axis.
            self.motor_on=bool(self.value('SVO?'));self.referenced=self.motor_on and bool(self.value('FRF?'))
            self.max_velocity_mm_s=self.parameter(0xA)
            self.velocity_mm_s=self.value('VEL?')
            self.position_mm=self.value('POS?')
        except Exception:self.close();raise

    def parameter(self,number):
        answer=self.query(f'SPA? {self.axis} 0x{number:X}')
        try:value=float(answer.rsplit('=',1)[-1].strip())
        except ValueError as exc:raise HardwareError('stage','Unexpected PI response: '+answer) from exc
        if not math.isfinite(value) or value<=0:raise HardwareError('stage','PI controller reported an invalid parameter: '+answer)
        return value

    def set_velocity(self,requested):
        if not math.isfinite(requested) or requested<=0:raise ValueError('Velocity must be a positive number')
        applied=min(requested,self.max_velocity_mm_s)
        self.send(f'VEL {self.axis} {applied:.6g}')
        error=self.query('ERR?')
        if error!='0':raise HardwareError('stage','PI rejected the velocity (error '+error+')',link=False)
        self.velocity_mm_s=self.value('VEL?')
        return dict(velocity_mm_s=self.velocity_mm_s,requested_mm_s=requested,max_velocity_mm_s=self.max_velocity_mm_s,clamped=applied<requested)

    def travel_timeout(self,distance_mm):
        return 30+1.5*abs(distance_mm)/max(self.velocity_mm_s,1e-3)

    def reference(self,stop):
        if stop.is_set():raise AcquisitionCancelled()
        if not self.value('SVO?'):
            self.send(f'SVO {self.axis} 1')
            if self.query('ERR?')!='0':raise HardwareError('stage','PI could not activate the motor',link=False)
        self.motor_on=True;self.referenced=False
        # This setup references against the positive limit switch (FPL), not the middle reference switch (FRF).
        self.send(f'FPL {self.axis}')
        if self.query('ERR?')!='0':raise HardwareError('stage','PI rejected the reference move',link=False)
        deadline=time.monotonic()+self.travel_timeout(self.upper-self.lower)
        try:
            while True:
                if stop.is_set():raise AcquisitionCancelled()
                time.sleep(.05);self.position_mm=self.value('POS?')
                if self.value('FRF?'):break
                if time.monotonic()>deadline:raise HardwareError('stage','PI reference move timed out',link=False)
        except AcquisitionCancelled:
            self.send(f'HLT {self.axis}');self.query('ERR?');self.position_mm=self.value('POS?');raise
        self.lower=self.value('TMN?');self.upper=self.value('TMX?')
        self.position_mm=self.value('POS?');self.referenced=True

    def query(self,command):
        # Queries are side-effect free, so one transient USB/serial glitch is retried before the link is declared lost.
        try:return self._query(command)
        except HardwareError as first:
            time.sleep(.1)
            try:return self._query(command)
            except HardwareError:raise first

    def _query(self,command):
        lines=[]
        with self.lock:
            try:
                self.port.write((command+'\n').encode('ascii'))
                for _ in range(32):
                    raw=self.port.readline()
                    if not raw:break
                    lines.append(raw.decode('ascii',errors='strict').strip())
                    if not raw.rstrip(b'\r\n').endswith(b' '):break
            except (OSError,UnicodeError) as exc:raise HardwareError('stage','PI serial connection failed: '+str(exc)) from exc
        if not lines:raise HardwareError('stage','PI controller did not respond; check the COM port and baud rate')
        return '\n'.join(lines)

    def send(self,command):
        '''A command without a reply (MOV, SVO, VEL, FPL, HLT). Never retried: it may already have taken effect.'''
        with self.lock:
            try:self.port.write((command+'\n').encode('ascii'))
            except HardwareError:raise
            except OSError as exc:raise HardwareError('stage','PI serial connection failed: '+str(exc)) from exc

    def value(self,command):
        answer=self.query(command+' '+self.axis)
        try:value=float(answer.rsplit('=',1)[-1].strip())
        except ValueError as exc:raise HardwareError('stage','Unexpected PI response: '+answer) from exc
        if not math.isfinite(value):raise HardwareError('stage','Non-finite PI controller position')
        return value

    def duration(self,target):
        if not self.lower<=target<=self.upper:raise ValueError(f'Stage target exceeds controller travel limits {self.lower}–{self.upper} mm')
        return 0.

    def move_to(self,target,stop):
        if stop.is_set():raise AcquisitionCancelled()
        self.duration(target)
        if not self.value('SVO?') or not self.value('FRF?'):self.referenced=False;raise HardwareError('stage','PI motor/reference state changed; reference the stage before motion',link=False)
        failed=True
        deadline=time.monotonic()+self.travel_timeout(target-self.position_mm)
        try:
            self.send(f'MOV {self.axis} {target:.12g}')
            if self.query('ERR?')!='0':raise HardwareError('stage','PI rejected the move; check controller configuration',link=False)
            while True:
                if stop.is_set():raise AcquisitionCancelled()
                self.position_mm=self.value('POS?')
                if self.value('ONT?'):
                    # The POS? above was read while still moving; the move may have ended in the ~30 ms before ONT?.
                    self.position_mm=self.value('POS?')
                    if abs(self.position_mm-target)>.01:raise HardwareError('stage','PI stopped outside the requested position',link=False)
                    failed=False
                    return
                if time.monotonic()>deadline:raise HardwareError('stage','PI move timed out',link=False)
                stop.wait(.05)
        # HardwareError is an OSError: without this, every error above was re-reported as a serial failure.
        except HardwareError:raise
        except OSError as exc:raise HardwareError('stage','PI serial connection failed: '+str(exc)) from exc
        finally:
            if failed:
                try:
                    self.send(f'HLT {self.axis}')
                    error=self.query('ERR?')
                    # C-663 reports 10 for a deliberate HLT. Consume it here so
                    # a subsequent move is not mistaken for a failed command.
                    if error not in {'0','10'}:raise HardwareError('stage','PI stop reported error '+error,link=False)
                    self.position_mm=self.value('POS?')
                except HardwareError:raise
                except Exception as exc:raise HardwareError('stage','Could not confirm the PI stop: '+str(exc)) from exc

    def close(self):
        if self.port is not None:self.port.close();self.port=None
