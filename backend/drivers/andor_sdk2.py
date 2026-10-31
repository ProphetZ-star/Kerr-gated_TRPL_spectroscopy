"""Optional real-camera adapter. Never imported by simulation startup.

This adapter is NOT commissioned on physical hardware. Stage integration is gated
until its controller and installation limits have been supplied.
"""
class AndorCamera:
    def connect(self, dll_directory, temperature_c, fan_mode='on'):
        import pylablib as pll
        pll.par['devices/dlls/andor_sdk2'] = dll_directory
        from pylablib.devices import Andor
        self.camera = None
        try:
            self.camera = Andor.AndorSDK2Camera(temperature=temperature_c, fan_mode=fan_mode)
            self.camera.set_read_mode('fvb')
            self.camera.set_trigger_mode('int')
            self.camera.set_exposure(1.0)
            return self.camera.get_device_info()
        except Exception:
            self.close()
            raise

    def close(self):
        if getattr(self, 'camera', None) is not None:
            self.camera.close()
            self.camera = None
