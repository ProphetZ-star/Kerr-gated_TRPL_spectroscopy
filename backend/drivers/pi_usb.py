"""Native PI USB transport through PI's official PIPython/GCS2 library."""
from .hardware import HardwareError


def pi_device(dll_path=''):
    try:
        from pipython import GCSDevice
        return GCSDevice('C-663.12',gcsdll=dll_path)
    except Exception as exc:
        raise ValueError('PI USB requires PIPython, the PI USB driver and 64-bit PI GCS2 DLL. Install PI Software Suite or select its GCS2 DLL. '+str(exc)) from exc


def usb_choices(dll_path=''):
    device=pi_device(dll_path)
    try:return dict(devices=[dict(id=s,label=s) for s in device.EnumerateUSB('C-663')])
    finally:device.CloseConnection()


class PIUSB:
    """Byte-stream facade for the shared, validated C-663.12 command adapter."""
    def __init__(self,config):
        self.device=pi_device(config.get('pi_dll',''));self.lines=[]
        try:
            description=config.get('usb_device','').strip()
            if not description:raise ValueError('Find and select a C-663.12 USB device first')
            self.device.ConnectUSB(description)
        except Exception:self.close();raise

    def write(self,data):
        command=data.decode('ascii').strip()
        try:
            if command.split()[0].endswith('?'):
                answer=self.device.ReadGCSCommand(command)
                self.lines=[line.encode('ascii')+b'\n' for line in answer.splitlines()]
                if len(self.lines)>1:
                    self.lines=[line.rstrip(b'\r\n')+b' \n' if i<len(self.lines)-1 else line for i,line in enumerate(self.lines)]
            else:self.device.GcsCommandset(command)
            return len(data)
        except Exception as exc:raise HardwareError('stage','PI USB communication failed: '+str(exc)) from exc

    def readline(self):return self.lines.pop(0) if self.lines else b''

    def close(self):self.device.CloseConnection()
