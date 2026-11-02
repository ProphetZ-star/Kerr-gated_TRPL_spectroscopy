"""Explicit hardware adapters. No SDK imports, port opens or motion on startup."""
from pathlib import Path
import ctypes as ct
import math
import os
import re
import time
import numpy as np


class HardwareError(ConnectionError):
    def __init__(self, device, message):
        self.device=device
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
    cookie=os.add_dll_directory(str(path.parent))
    try:dll=ct.WinDLL(str(path))
    except Exception:cookie.close();raise
    signatures={'GetAvailableCameras':[ct.POINTER(ct.c_long)],'GetCameraHandle':[ct.c_long,ct.POINTER(ct.c_long)],'SetCurrentCamera':[ct.c_long],
                'Initialize':[ct.c_char_p],'GetDetector':[ct.POINTER(ct.c_int),ct.POINTER(ct.c_int)],'GetCameraSerialNumber':[ct.POINTER(ct.c_int)],
                'SetReadMode':[ct.c_int],'SetAcquisitionMode':[ct.c_int],'SetTriggerMode':[ct.c_int],'SetExposureTime':[ct.c_float],
                'SetImage':[ct.c_int]*6,'GetStatus':[ct.POINTER(ct.c_int)],'GetAcquiredData':[ct.POINTER(ct.c_int32),ct.c_ulong],
                'GetAcquisitionTimings':[ct.POINTER(ct.c_float)]*3,
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
    temperature_c=None

    def __init__(self,config):
        self.exposure_s=1.0;self.dll=None;self.cookie=None;self.initialized=False
        try:
            self.dll,self.cookie,self.path=sdk_library(config.get('dll_directory',''))
            count=ct.c_long();sdk_check(self.dll,'GetAvailableCameras',ct.byref(count))
            index=config.get('camera_index',0)
            if not 0<=index<count.value:raise ValueError('Selected Andor camera is not available')
            handle=ct.c_long();sdk_check(self.dll,'GetCameraHandle',index,ct.byref(handle));sdk_check(self.dll,'SetCurrentCamera',handle)
            sdk_check(self.dll,'Initialize',str(self.path.parent).encode('mbcs'));self.initialized=True
            width=ct.c_int();height=ct.c_int();sdk_check(self.dll,'GetDetector',ct.byref(width),ct.byref(height))
            if (width.value,height.value)!=(1024,255):raise ValueError('This workbench currently requires a 1024 × 255 Newton CCD')
            number=ct.c_int();sdk_check(self.dll,'GetCameraSerialNumber',ct.byref(number));self.serial_number=number.value
            sdk_check(self.dll,'SetAcquisitionMode',1);sdk_check(self.dll,'SetTriggerMode',0)
            sdk_check(self.dll,'SetReadMode',0)
            self.set_exposure(1.0)
        except Exception:self.close();raise

    def capabilities(self):
        return dict(model='Andor Newton DU920P',shape=[255,1024],pixels=1024,bit_depth=16,full_scale=65535,
                    read_modes=['fvb','full_image'],source_mode='hardware',temperature_c=self.temperature_c,serial_number=self.serial_number)

    def set_exposure(self,value):
        sdk_check(self.dll,'SetExposureTime',float(value))
        exposure=ct.c_float();accumulation=ct.c_float();kinetic=ct.c_float()
        sdk_check(self.dll,'GetAcquisitionTimings',ct.byref(exposure),ct.byref(accumulation),ct.byref(kinetic))
        self.exposure_s=float(exposure.value)

    def capture(self,exposure_s,readout,stop):
        if stop.is_set():raise AcquisitionCancelled()
        sdk_check(self.dll,'SetReadMode',0 if readout=='fvb' else 4)
        height=1 if readout=='fvb' else 255
        if height>1:sdk_check(self.dll,'SetImage',1,1,1,1024,1,255)
        self.set_exposure(exposure_s)
        if stop.is_set():raise AcquisitionCancelled()
        sdk_check(self.dll,'StartAcquisition')
        try:
            deadline=time.monotonic()+exposure_s+30
            while True:
                if stop.is_set():raise AcquisitionCancelled()
                status=ct.c_int();sdk_check(self.dll,'GetStatus',ct.byref(status))
                if status.value==20073:break
                if status.value!=20072:raise HardwareError('camera',f'Unexpected Andor status {status.value}')
                if time.monotonic()>deadline:raise HardwareError('camera','Andor exposure/readout timed out')
                stop.wait(.02)
            image=np.empty((height,1024),dtype=np.int32)
            sdk_check(self.dll,'GetAcquiredData',image.ctypes.data_as(ct.POINTER(ct.c_int32)),image.size)
            return image
        finally:
            # Abort is also the SDK's normal cleanup for a cancelled single exposure.
            self.dll.AbortAcquisition()

    def close(self):
        if self.dll is not None and self.initialized:
            self.dll.AbortAcquisition();self.dll.ShutDown();self.initialized=False
        if self.cookie is not None:self.cookie.close();self.cookie=None


class PIStage:
    """C-663.12 / M-414.32S. POS is the profile-generator step count, not an encoder."""
    source_mode='hardware'
    position_source='controller_step_counter'
    position_sensor=False
    controller_model='C-663.12'

    def __init__(self,config):
        self.port=None
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
            if not self.value('SVO?'):raise ValueError('C-663.12 motor is disabled. Activate it in PIMikroMove first.')
            if not self.value('FRF?'):raise ValueError('PI axis is not referenced. Reference it in PI software first.')
            self.position_mm=self.value('POS?')
        except Exception:self.close();raise

    def query(self,command):
        lines=[]
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
        if not self.value('SVO?') or not self.value('FRF?'):raise HardwareError('stage','PI motor/reference state changed; configure the controller before motion')
        failed=True
        deadline=time.monotonic()+120
        try:
            self.port.write(f'MOV {self.axis} {target:.12g}\n'.encode('ascii'))
            if self.query('ERR?')!='0':raise HardwareError('stage','PI rejected the move; check controller configuration')
            while True:
                if stop.is_set():raise AcquisitionCancelled()
                self.position_mm=self.value('POS?')
                if self.value('ONT?'):
                    if abs(self.position_mm-target)>.01:raise HardwareError('stage','PI stopped outside the requested position')
                    failed=False
                    return
                if time.monotonic()>deadline:raise HardwareError('stage','PI move timed out')
                stop.wait(.05)
        except OSError as exc:raise HardwareError('stage','PI serial connection failed: '+str(exc)) from exc
        finally:
            if failed:
                try:
                    self.port.write(f'HLT {self.axis}\n'.encode('ascii'))
                    error=self.query('ERR?')
                    # C-663 reports 10 for a deliberate HLT. Consume it here so
                    # a subsequent move is not mistaken for a failed command.
                    if error not in {'0','10'}:raise HardwareError('stage','PI stop reported error '+error)
                    self.position_mm=self.value('POS?')
                except Exception as exc:raise HardwareError('stage','Could not confirm the PI stop: '+str(exc)) from exc

    def close(self):
        if self.port is not None:self.port.close();self.port=None
