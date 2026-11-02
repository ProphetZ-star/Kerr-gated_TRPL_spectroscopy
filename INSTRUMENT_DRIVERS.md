# Adding an instrument driver

The workbench uses a local driver registry in `backend/drivers/registry.py`.
The UI queries `/api/v1/devices/drivers`; it does not hard-code spectrometer brands.
API requests contain registered IDs, never Python modules, arbitrary commands or executable plugin paths.

## Current profiles

| Driver ID | Connection | Behaviour |
| --- | --- | --- |
| `pi_c66312` | PI native USB / RS-232 | C-663.12, M-414.32S, read-only startup checks; no automatic motor activation or referencing |
| `andor_sdk2` | USB / SDK2 | Newton 1024 × 255, FVB / image |
| `oriel_77400_manual` | Manual | Operator-recorded optical settings; no electronic connection |
| `generic_manual` | Manual | Operator-supplied instrument model and settings |
| `spectrometer_serial_reserved` | Serial / USB / TCP/IP / VISA | Connection fields only; deliberately no factory or commands |

PIPython is imported only for an explicit PI USB discovery or connection.
Andor SDK2 is loaded only for an explicit camera search or connection.
Neither vendor's Windows DLLs or USB drivers are distributed in this project.

## Spectrometer contract

A motorized adapter is constructed with a configuration dict containing the selected
`transport`, `port`, `baudrate`, `resource`, `dll_directory` and `model` where applicable.
It must:

1. Open its own connection and verify the instrument identity. Its constructor must
   close resources before raising if initialization fails.
2. Expose `identity`, a human-readable manufacturer/model/serial identification.
3. Implement `read_settings()`, returning the current full settings dict with
   `center_nm` (finite positive float), `grating` (positive integer, lines/mm), and
   `slit_um` (finite positive float). Report known physical values; do not invent
   values for fields the device cannot query. If a value must be supplied by an
   operator, the model-specific driver must explicitly require it before connecting.
4. Implement `configure(settings)`. Wait for movement to finish, verify readback,
   and return the full actual settings, including rounding performed by the device.
   A supported change must preserve unsupported parameters. Use the documented
   manufacturer protocol, not a guessed generic command set.
5. Implement `close()` to release the connection.
6. Raise `HardwareError('spectrometer', message)` for communication failures, and
   make all blocking operations bounded. The backend will disable that connection.

The registry `controls` tuple declares which of `center_nm`, `grating`, `slit_um`
the instrument can change. Unsupported controls are greyed out; the backend also
rejects them before sending a command. Manual adapters use the same `configure`
interface to record the operator's settings, but do not implement hardware readback
and never claim to be electronically connected.

## Registering a new model

Implement the adapter in a new file under `backend/drivers`, then add its factory
and registration inside `default_registry()`. Import optional vendor modules only
inside the factory/adapter, so an unplugged or uninstalled instrument cannot block
application startup.

```python
def make_vendor_model(config):
    from .vendor_model import VendorSpectrometer
    return VendorSpectrometer(config)

registry.register(Driver(
    id='vendor_model',
    family='spectrometer',
    label='Vendor Model',
    transports=('serial',),
    factory=make_vendor_model,
    controls=('center_nm', 'grating', 'slit_um'),
    defaults=dict(transport='serial', baudrate=9600),
    note='Model-specific connection instructions.',
))
```

No frontend or acquisition-engine changes are required for a spectrometer using
these standard settings. Additional device functions require an explicit extension
of the contract and API; they are not passed through as arbitrary device commands.

## Data and calibration

- Camera and stage availability do not depend on an electronic spectrometer connection.
- Connection identity, driver selection, manual state and actual controller readback
  are included in experiment metadata. Manual values remain operator records.
- A new model/configuration or changed optical settings invalidates the live
  wavelength mapping, cached calibration frame and background.
- Before changing motorized settings, the old mapping is invalidated even if a
  subsequent communication failure leaves the physical position uncertain.
- Installed mappings are stamped with the driver/model identity. Startup will not
  silently restore a mapping for another selected instrument. Old files from the
  original Oriel-only version remain compatible with that default profile.
- Simulation and hardware calibration files remain separate.

## PI C-663.12 notes

USB uses PI's official PIPython/GCS2 library; serial uses the same validated GCS2
command path. Both verify `*IDN?`, `SAI?`, `CST?`, `TMN?`, `TMX?`, `SVO?`, `FRF?`,
and `POS?` before enabling motion. Software never runs `SVO`, `FRF`, stage-parameter
loading or nonvolatile writes automatically.

For M-414.32S, position is the open-loop profile-generator step count, not encoder
feedback. Targets use the controller's reported limits. Cancelling a motion sends
`HLT` and consumes the controller's expected stop error 10 before allowing another
move; inability to confirm a stop is reported as a device error.

Actual devices were unavailable during development. Validate each new driver on
its intended controller/instrument before collecting experimental data.
