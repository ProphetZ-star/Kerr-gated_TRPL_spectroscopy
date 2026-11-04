"""Native IPC input contracts; no web framework or HTTP server."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

class SmallBody(BaseModel):
    model_config=ConfigDict(extra='forbid')


class ConnectBody(SmallBody):
    mode: Literal['simulation','hardware']='hardware'


class DeviceBody(SmallBody):
    device: Literal['stage','camera','spectrometer']
    driver: str=Field(default='',max_length=80,pattern=r'^[a-z0-9_]*$')
    transport: Literal['usb','serial','manual','tcpip','visa']|None=None
    model: str=Field(default='',max_length=160)
    resource: str=Field(default='',max_length=512)
    usb_device: str=Field(default='',max_length=512)
    pi_dll: str=Field(default='',max_length=4096)
    port: str=Field(default='',max_length=64)
    baudrate: Literal[9600,19200,38400,57600,115200]=38400
    axis: str=Field(default='',max_length=16,pattern=r'^[A-Za-z0-9_]*$')
    camera_index: int=Field(default=0,ge=0,le=63,strict=True)
    dll_directory: str=Field(default='',max_length=4096)


class SDKBody(SmallBody):
    dll_directory: str=Field(default='',max_length=4096)


class PIUSBBody(SmallBody):
    pi_dll: str=Field(default='',max_length=4096)


class DeviceNameBody(SmallBody):
    device: Literal['stage','camera','spectrometer']


