"""Trusted, local instrument registrations. UI/API exchange driver IDs, never code."""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Protocol

SPECTRAL_CONTROLS=('center_nm','grating','slit_um')


class SpectrometerAdapter(Protocol):
    """configure returns a full settings dict; motorized drivers must verify readback.

    capability.controls lists supported keys from SPECTRAL_CONTROLS. Manual
    adapters record the operator's settings without opening a connection.
    Drivers wrap communication failures as HardwareError('spectrometer', ...).
    """
    identity: str
    def configure(self, settings: dict) -> dict: ...
    def read_settings(self) -> dict: ...  # Required for motorized drivers, not manual adapters.
    def close(self) -> None: ...


@dataclass(frozen=True)
class Driver:
    id: str
    family: str
    label: str
    transports: tuple
    factory: Callable | None
    controls: tuple = ()
    manual: bool = False
    defaults: dict = field(default_factory=dict)
    note: str = ''

    def describe(self):
        return dict(id=self.id,family=self.family,label=self.label,transports=list(self.transports),
                    controls=list(self.controls),manual=self.manual,implemented=self.factory is not None,
                    defaults=deepcopy(self.defaults),note=self.note)


class DriverRegistry:
    def __init__(self):self.drivers={}

    def register(self,driver):
        if driver.id in self.drivers:raise ValueError('Duplicate instrument driver: '+driver.id)
        if driver.family not in {'stage','camera','spectrometer'}:raise ValueError('Invalid driver family')
        if not set(driver.controls)<=set(SPECTRAL_CONTROLS):raise ValueError('Unknown spectrometer control')
        if driver.manual and driver.transports!=('manual',):raise ValueError('Manual drivers cannot open transports')
        self.drivers[driver.id]=driver

    def get(self,identifier,family=None):
        if identifier not in self.drivers:raise ValueError('Instrument driver is not installed: '+identifier)
        driver=self.drivers[identifier]
        if family and driver.family!=family:raise ValueError('Driver does not belong to '+family)
        return driver

    def catalog(self):return [driver.describe() for driver in self.drivers.values()]

    def create(self,identifier,config):
        driver=self.get(identifier)
        if driver.factory is None:raise ValueError('This instrument interface is reserved. Install/register a model-specific driver before connecting; no commands were sent.')
        adapter=driver.factory(config)
        required=('configure','close')+(() if driver.manual else ('read_settings',))
        if driver.family=='spectrometer' and any(not callable(getattr(adapter,method,None)) for method in required):
            if callable(getattr(adapter,'close',None)):adapter.close()
            raise ValueError('Spectrometer adapter must implement configure(settings), close(), and read_settings() for a controller')
        return adapter


class ManualSpectrometer:
    def __init__(self,config):self.identity=config.get('model','Oriel 77400 / MS125')
    def configure(self,settings):return deepcopy(settings)
    def close(self):pass


def _stage(config):
    from .hardware import PIStage
    return PIStage(config)


def _camera(config):
    from .hardware import AndorSDK2Camera
    return AndorSDK2Camera(config)


def default_registry():
    registry=DriverRegistry()
    registry.register(Driver('pi_c66312','stage','PI C-663.12 · M-414.32S',('usb','serial'),_stage,
        defaults=dict(transport='usb',axis='1',baudrate=38400,baudrates=[9600,19200,38400,115200]),note='Requires C-663.12 configured for M-414.32S. Position is a step count, not an encoder reading. Use Reference in the Delay Stage panel to activate and reference the axis.'))
    registry.register(Driver('andor_sdk2','camera','Andor Newton DU920P · SDK2',('usb',),_camera,defaults=dict(transport='usb')))
    registry.register(Driver('oriel_77400_manual','spectrometer','Oriel 77400 / MS125 · Manual',('manual',),ManualSpectrometer,
        controls=SPECTRAL_CONTROLS,manual=True,defaults=dict(transport='manual',model='Oriel 77400 / MS125'),
        note='Adjust the instrument manually, then record wavelength, grating and slit settings. No controller or COM port is required.'))
    registry.register(Driver('generic_manual','spectrometer','Other spectrometer · Manual',('manual',),ManualSpectrometer,
        controls=SPECTRAL_CONTROLS,manual=True,defaults=dict(transport='manual',model='Manual spectrometer'),note='Record the settings after adjusting the instrument manually.'))
    registry.register(Driver('spectrometer_serial_reserved','spectrometer','Other spectrometer · Controller interface (driver required)',('serial','usb','tcpip','visa'),None,
        defaults=dict(transport='serial'),note='Reserved for a future model-specific driver. A COM port alone does not identify the protocol.'))
    return registry
