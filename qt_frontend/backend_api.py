'''Validation facade for native backend commands.'''
import math
from backend.models import RunRequest,MoveRequest,CameraConfig,ReadoutRequest,CoolingRequest,VelocityRequest,BackgroundRequest,SpectrographConfig,CalibrationRequest,FitRequest
from backend.calibration_models import CaptureRequest,ExtractRequest,MappingRequest,ApplyRequest
from backend.contracts import ConnectBody,DeviceBody,SDKBody,PIUSBBody,DeviceNameBody

class ValidationError(ValueError):
    pass

COMMANDS=set('status connect device_drivers device_ports device_pi_usb device_cameras device_connect device_disconnect configure camera_readout camera_cooling stage_velocity stage_reference move zero stage_stop spectrograph plan run preview_start preview_stop background background_stop pause resume stop get_run records output_check save discard run_files_removed export analysis_open analysis_close fit task task_cancel calibration_frame calibration_capture calibration_capture_status calibration_stop calibration_extract calibration_fit calibration_solution calibration_apply calibration'.split())

def _pydantic_text(name,error):
    try:
        parts=[]
        for item in error.errors():
            loc='.'.join(str(part) for part in item.get('loc',()))
            parts.append((loc+': ' if loc else '')+str(item.get('msg','Invalid value')))
        return name+' input is invalid: '+'; '.join(parts)
    except Exception:
        return name+' input is invalid: '+str(error)

def _model(name,cls,data):
    try:
        return cls(**data).model_dump(mode='python')
    except Exception as error:
        raise ValidationError(_pydantic_text(name,error)) from error

def _dict(data,name):
    if data is None:
        return {}
    if not isinstance(data,dict):
        raise ValidationError(name+' input must be an object')
    return dict(data)

def _str(data,key,name,allow_empty=False):
    value=data.get(key)
    if not isinstance(value,str) or (not allow_empty and not value.strip()):
        raise ValidationError(name+'.'+key+' must be a non-empty string')
    return value

def _number(data,key,name,positive=False):
    value=data.get(key)
    if isinstance(value,bool):
        raise ValidationError(name+'.'+key+' must be numeric')
    try:
        value=float(value)
    except Exception as error:
        raise ValidationError(name+'.'+key+' must be numeric') from error
    if not math.isfinite(value) or (positive and value<=0):
        raise ValidationError(name+'.'+key+' is out of range')
    return value

def _int(data,key,name,default=0,minimum=0):
    value=data.get(key,default)
    if isinstance(value,bool):
        raise ValidationError(name+'.'+key+' must be an integer')
    try:
        value=int(value)
    except Exception as error:
        raise ValidationError(name+'.'+key+' must be an integer') from error
    if value<minimum:
        raise ValidationError(name+'.'+key+' is out of range')
    return value

def _bool(data,key,name,default=False):
    value=data.get(key,default)
    if not isinstance(value,bool):
        raise ValidationError(name+'.'+key+' must be boolean')
    return value

def validate_command(name,data=None):
    if name not in COMMANDS:
        raise ValidationError('Unknown command: '+str(name))
    data=_dict(data,name)
    if name=='status':
        out={}
        if 'compact' in data:
            out['compact']=_bool(data,'compact',name)
        if 'since' in data:
            if data['since'] is not None and not isinstance(data['since'],str):
                raise ValidationError('status.since must be a string or null')
            out['since']=data['since']
        if 'latest_version' in data:
            out['latest_version']=_int(data,'latest_version',name,minimum=0)
        return out
    if name in {'zero','stage_reference','stage_stop','preview_start','preview_stop','background_stop','calibration_frame','calibration_stop','device_ports','device_drivers'}:
        return {}
    if name=='connect':
        return _model(name,ConnectBody,data)
    if name=='device_connect':
        return _model(name,DeviceBody,data)
    if name=='device_disconnect':
        return _model(name,DeviceNameBody,data)
    if name=='device_cameras':
        return _model(name,SDKBody,data)
    if name=='device_pi_usb':
        return _model(name,PIUSBBody,data)
    if name=='configure':
        return _model(name,CameraConfig,data)
    if name=='camera_readout':
        return _model(name,ReadoutRequest,data)
    if name=='camera_cooling':
        return _model(name,CoolingRequest,data)
    if name=='stage_velocity':
        return _model(name,VelocityRequest,data)
    if name=='move':
        return _model(name,MoveRequest,data)
    if name=='spectrograph':
        return _model(name,SpectrographConfig,data)
    if name in {'plan','run'}:
        return _model(name,RunRequest,data)
    if name=='background':
        return _model(name,BackgroundRequest,data)
    if name in {'pause','resume','stop','get_run','discard','run_files_removed'}:
        return {'run_id':_str(data,'run_id',name)}
    if name=='records':
        return {'run_id':_str(data,'run_id',name),'analysis':_bool(data,'analysis',name,False),'offset':_int(data,'offset',name,0),'limit':_int(data,'limit',name,100,1)}
    if name=='output_check':
        return {'path':_str(data,'path',name)}
    if name=='save':
        out={'run_id':_str(data,'run_id',name),'path':_str(data,'path',name)}
        token=data.get('overwrite_token')
        if token is not None:
            if not isinstance(token,str) or len(token)!=64:
                raise ValidationError('save.overwrite_token must contain 64 characters')
            out['overwrite_token']=token
        return out
    if name=='export':
        return {'run_id':_str(data,'run_id',name),'center_nm':_number(data,'center_nm',name,True),'half_window_nm':_number(data,'half_window_nm',name,True)}
    if name=='analysis_open':
        return {'path':_str(data,'path',name)}
    if name=='analysis_close':
        return {'run_id':_str(data,'run_id',name),'analysis':True}
    if name=='fit':
        options=_model(name,FitRequest,_dict(data.get('options'),name+'.options'))
        return {'run_id':_str(data,'run_id',name),'analysis':_bool(data,'analysis',name,False),'options':options}
    if name=='task':
        return {'task_id':_str(data,'task_id',name)}
    if name=='task_cancel':
        return {'task_id':_str(data,'task_id',name)}
    if name=='calibration_capture':
        return _model(name,CaptureRequest,data)
    if name=='calibration_capture_status':
        return {'capture_id':_str(data,'capture_id',name)}
    if name=='calibration_extract':
        return _model(name,ExtractRequest,data)
    if name=='calibration_fit':
        return _model(name,MappingRequest,data)
    if name=='calibration_solution':
        return {'solution_id':_str(data,'solution_id',name)}
    if name=='calibration_apply':
        return _model(name,ApplyRequest,data)
    if name=='calibration':
        return _model(name,CalibrationRequest,data)
    raise ValidationError('Unsupported command: '+str(name))

normalize_command=validate_command
