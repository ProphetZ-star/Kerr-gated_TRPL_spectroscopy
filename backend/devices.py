"""Independent connections and action availability; hardware is the safe default."""
from copy import deepcopy
import re
from .drivers.sim import SimCamera, SimStage
from .errors import CommandError
from .drivers.registry import default_registry,SPECTRAL_CONTROLS
from .models import SpectrographConfig

NAMES=('stage','camera','spectrometer')
CONNECTION_DEFAULTS=dict(port='',baudrate=38400,axis='',camera_index=0,dll_directory='',pi_dll='',usb_device='',resource='')


class OfflineCamera:
    pixels=1024
    full_scale=65535
    exposure_s=1.0
    temperature_c=None

    def capabilities(self):
        return dict(model='Andor Newton DU920P',shape=[255,1024],pixels=1024,full_scale=65535,
                    read_modes=['fvb','full_image'],source_mode='hardware',temperature_c=None)


class OfflineStage:
    # Internal scan-planning origin only, never reported as a measured position.
    position_mm=150.0


def serial_ports():
    try:
        from serial.tools import list_ports
        return dict(ports=[dict(port=p.device,label=f'{p.device} · {p.description}') for p in list_ports.comports()],error=None)
    except ImportError:
        return dict(ports=[],error='pyserial is not installed. Run start-app.cmd to install the updated dependencies.')


class DeviceConnections:
    def __init__(self,factories=None,registry=None):
        self.registry=registry or default_registry();self.factories=factories
        self.mode='hardware';self.camera=OfflineCamera();self.stage=OfflineStage();self.spectrometer=None
        defaults={'stage':'pi_c66312','camera':'andor_sdk2','spectrometer':'oriel_77400_manual'}
        self.links={}
        for name,identifier in defaults.items():
            driver=self.registry.get(identifier)
            self.links[name]=dict(connected=False,state='disconnected',config=dict(CONNECTION_DEFAULTS,driver=identifier,**deepcopy(driver.defaults)),
                                  identity=None,error=None,reported_settings=None,capabilities=dict(manual=driver.manual,controls=[]))
        self.disconnect('spectrometer')

    def connected(self,name):return self.links[name]['connected']

    def ready(self,name):return self.connected(name) or self.links[name]['state']=='manual'

    def available(self):
        stage=self.connected('stage');camera=self.connected('camera');spectrometer=self.connected('spectrometer')
        controls=self.links['spectrometer']['capabilities'].get('controls',[]) if self.ready('spectrometer') else []
        return dict(stage=stage,camera=camera,spectrometer=spectrometer,preview=camera,background=camera,
                    calibration_capture=camera,scan=stage and camera,spectrograph_config=bool(controls),
                    spectrograph_controls=list(controls),manual_spectrometer=self.links['spectrometer']['state']=='manual')

    def require(self,*names):
        missing=[name for name in names if not self.connected(name)]
        if missing:raise CommandError('Connect '+', '.join(missing)+' before using this operation')

    def status(self):return deepcopy(self.links)

    def catalog(self):return dict(drivers=self.registry.catalog())

    def disconnect(self,name):
        adapter=getattr(self,name)
        if adapter is not None and hasattr(adapter,'close'):adapter.close()
        setattr(self,name,OfflineCamera() if name=='camera' else OfflineStage() if name=='stage' else None)
        link=self.links[name];driver=self.registry.get(link['config']['driver'],name)
        link.update(connected=False,state='disconnected',identity=None,error=None,reported_settings=None,
                    capabilities=dict(manual=driver.manual,controls=[]))
        if name=='spectrometer' and driver.manual:
            self.spectrometer=self.registry.create(driver.id,link['config'])
            link.update(state='manual',identity=self.spectrometer.identity,capabilities=dict(manual=True,controls=list(driver.controls)))

    def switch(self,mode):
        if mode==self.mode and (mode!='simulation' or all(self.connected(n) for n in NAMES)):return
        self.close();self.mode=mode
        if mode=='simulation':
            self.camera=SimCamera();self.stage=SimStage()
            for name in NAMES:self.links[name].update(connected=True,state='simulated',identity='Simulated',error=None,
                capabilities=dict(manual=False,controls=list(SPECTRAL_CONTROLS) if name=='spectrometer' else []))

    def connect(self,name,config):
        if self.mode!='hardware':raise CommandError('Switch to Hardware mode to configure real devices')
        if name not in NAMES:raise ValueError('Unknown device')
        default={'stage':'pi_c66312','camera':'andor_sdk2','spectrometer':'oriel_77400_manual'}[name]
        if name=='spectrometer' and config.get('port') and not config.get('driver'):default='spectrometer_serial_reserved'
        driver=self.registry.get(config.get('driver') or default,name)
        cfg=dict(CONNECTION_DEFAULTS);cfg.update(driver.defaults);cfg.update({k:v for k,v in config.items() if v is not None});cfg['driver']=driver.id
        cfg['transport']=config.get('transport') or ('serial' if config.get('port') else driver.defaults.get('transport',driver.transports[0]))
        cfg['model']=config.get('model','').strip() or driver.defaults.get('model',driver.label)
        if driver.manual:
            for key,value in CONNECTION_DEFAULTS.items():cfg[key]=value
        if cfg['transport'] not in driver.transports:raise ValueError('This driver does not support the selected transport')
        if self.connected(name):raise CommandError('Disconnect this device before changing its connection')
        port=cfg.get('port','').strip()
        if cfg['transport']=='serial':
            if not re.fullmatch(r'COM[1-9][0-9]{0,4}',port,re.I):raise ValueError('Choose a Windows COM port')
            for other in NAMES:
                link=self.links[other]
                if other!=name and link['connected'] and link['config'].get('transport')=='serial' and link['config'].get('port','').upper()==port.upper():raise CommandError('This COM port is already used by '+other)
        self.disconnect(name)
        self.links[name].update(config=deepcopy(cfg),error=None,capabilities=dict(manual=driver.manual,controls=[]))
        try:
            adapter=self.factories[name](cfg) if self.factories is not None and not driver.manual else self.registry.create(driver.id,cfg)
            reported=None
            if name=='spectrometer' and not driver.manual and self.factories is None:
                try:reported=SpectrographConfig(**adapter.read_settings()).model_dump()
                except Exception:adapter.close();raise
        except Exception as exc:
            self.links[name].update(connected=False,state='error',error=str(exc))
            raise CommandError(str(exc)) from exc
        setattr(self,name,adapter)
        controls=list(driver.controls or (SPECTRAL_CONTROLS if self.factories is not None and name=='spectrometer' else ()))
        self.links[name].update(connected=not driver.manual,state='manual' if driver.manual else 'connected',
            identity=getattr(adapter,'identity',None) or ('Andor '+str(getattr(adapter,'serial_number','CCD')) if name=='camera' else cfg['model']),
            error=None,reported_settings=reported,capabilities=dict(manual=driver.manual,controls=controls))

    def check_spectrometer_update(self,current,requested):
        if not self.ready('spectrometer'):raise CommandError('Select a manual spectrometer or connect a supported controller first')
        allowed=set(self.links['spectrometer']['capabilities']['controls'])
        unsupported=[key for key,value in requested.items() if value!=current[key] and key not in allowed]
        if unsupported:raise CommandError('Selected spectrometer does not support: '+', '.join(unsupported))

    def configure_spectrometer(self,current,requested):
        self.check_spectrometer_update(current,requested)
        result=SpectrographConfig(**self.spectrometer.configure(deepcopy(requested))).model_dump()
        if not self.links['spectrometer']['capabilities']['manual']:self.links['spectrometer']['reported_settings']=result.copy()
        return result

    def failed(self,name,error):
        if name in self.links:self.links[name].update(connected=False,state='error',error=str(error))

    def close(self):
        failures=[]
        for name in NAMES:
            try:self.disconnect(name)
            except Exception as exc:self.failed(name,exc);failures.append(str(exc))
        if failures:raise CommandError('Could not close hardware: '+'; '.join(failures))
