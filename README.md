# Kerr-gated TRPL Spectroscopy

Python software for controlling a **CCD-based spectrometer** and a **motorized optical delay stage** to perform **optical Kerr-gated time-resolved photoluminescence (TRPL) spectroscopy**.

> **Project status:** Under development. Hardware drivers, acquisition scripts, and command-line interfaces will be added as they are implemented. The workflows below describe the intended functionality, not currently available commands.

## Overview

Optical Kerr gating enables ultrafast, time-resolved photoluminescence measurements by using a short gate pulse to induce transient birefringence in a Kerr medium. A pair of crossed polarizers suppresses the ungated photoluminescence, while the gate pulse briefly opens the optical gate. By scanning the relative delay between the gate pulse and the photoluminescence, a spectrometer and CCD record the time evolution of the emission spectrum.

This project aims to automate **delay-stage positioning**, **CCD/spectrometer acquisition**, and **data storage** in a single Python workflow.

## Planned Features

- Initialize and communicate with the CCD camera, spectrometer, and motorized delay stage.
- Configure CCD acquisition settings, such as exposure time, number of accumulations, and readout parameters (where supported by the hardware).
- Scan a user-defined range of pump–gate delays with configurable step size and settling time.
- Acquire and average photoluminescence spectra at each delay.
- Record dark/background spectra and optionally gate-off reference spectra.
- Display spectra and a time–wavelength intensity map during acquisition.
- Save raw measurements, experimental settings, and acquisition metadata for later analysis.
- Handle hardware errors and provide a safe stop procedure.

## Experimental Setup

```text
Excitation laser ──> Sample ──> PL collection optics ──> Polarizer 1 ──┐
                                                                      │
Gate laser ──> Motorized delay stage ───────────────────────────────> Kerr medium
                                                                      │
                                                               Polarizer 2
                                                                      │
                                                                 Spectrometer
                                                                      │
                                                                     CCD
                                                                      │
                                                                   Python
```

*The diagram is schematic. The actual beam geometry, delay-arm location, and optical components depend on the experiment.*

## Hardware and Software

**Hardware interfaces to be configured:**

- **CCD camera:** manufacturer, model, SDK, and connection interface to be specified.
- **Spectrometer:** wavelength calibration and any motorized controls to be specified.
- **Delay stage:** controller, communication protocol, travel limits, and homing procedure to be specified.

**Software:** Python 3.10+ is a suggested development target. Actual dependencies will depend on the selected camera SDK and delay-stage controller; installation instructions will be added once the interfaces are implemented.

## Intended Measurement Workflow

1. Connect to the CCD, spectrometer, and delay-stage controller.
2. Validate hardware status, stage limits, and acquisition parameters.
3. Configure CCD exposure, readout settings, and spectral acquisition parameters.
4. Set or measure the time-zero position, then define the delay scan.
5. At each delay point, move the stage, wait for stabilization, and acquire the requested spectra.
6. Save the raw spectra together with wavelength, delay, stage position, acquisition settings, and timestamps.
7. Optionally apply background subtraction and plot the wavelength–time TRPL map.
8. Stop acquisition safely and close hardware connections.

### Delay Calibration

For a delay arm with a retroreflector in a **double-pass** configuration, a stage displacement \(\Delta x\) changes the optical delay by

\[
\Delta t = \frac{2\Delta x}{c},
\]

where \(c\) is the speed of light in vacuum. Thus, **1 mm of stage travel corresponds to approximately 6.67 ps** of optical delay in air. For a single-pass geometry, the factor of 2 does not apply. The sign of the delay and the time-zero offset must be established experimentally.

## Proposed Data Organization

Each scan should retain both raw data and sufficient metadata for reproducibility. For example:

```text
data/
└── YYYYMMDD_HHMMSS_scan/
    ├── spectra.csv       # Rows: delay points; columns: wavelengths
    ├── delays.csv        # Delay values and corresponding stage positions
    ├── metadata.json     # Instrument settings and scan parameters
    └── background.csv    # Optional dark/background spectrum
```

The output format and folder structure are **proposed conventions** and may change during development. Raw data should be preserved; processed results should be stored separately.

## Safety and Measurement Notes

- Confirm the stage is homed and its travel limits are valid before moving it.
- Avoid stage collisions and check that the selected scan range is physically accessible.
- Follow laser-safety procedures and protect the CCD against excessive optical power.
- Keep the CCD acquisition conditions consistent across a delay scan.
- Measure backgrounds and periodically verify the optical time zero.
- Distinguish optical delay sign conventions from the recorded stage coordinate.

## Roadmap

- [ ] Implement camera communication and spectral acquisition.
- [ ] Implement delay-stage control and position feedback.
- [ ] Integrate synchronized stage scanning and CCD acquisition.
- [ ] Add configuration, logging, and error handling.
- [ ] Add live plotting and standardized data export.
- [ ] Validate timing calibration, background subtraction, and measurement repeatability.

## License

A license has not yet been selected.
