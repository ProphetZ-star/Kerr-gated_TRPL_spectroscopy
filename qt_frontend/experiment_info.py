'''Experiment details: everything the experiment file records about a run, opened from the Analysis page.

Read-only facts are plain text in the dialog-card style of the device dialogs (muted key, normal value). The
one action, "Use these scan settings", hands the run's scan request back to the Acquisition page.'''
from datetime import datetime
import math
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QScrollArea

STATUS = {'completed': 'Completed', 'stopped': 'Stopped early', 'error': 'Failed', 'partial': 'Incomplete (the file was not closed)'}
MODES = {'scan': 'Scan', 'single': 'Single delay', 'repeat': 'Repeat'}
READOUT = {'fvb': 'Full vertical binning', 'roi': 'Region of interest'}
POSITION_SOURCES = {'controller': 'Stage controller', 'simulated_controller': 'Simulated controller', 'unavailable': 'Not available'}


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def number(value, unit='', digits=6):
    value = _finite(value)
    if value is None:
        return None
    return f'{value:.{digits}g}' + (f' {unit}' if unit else '')


def local_time(text):
    try:
        return datetime.fromisoformat(str(text)).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None


def duration(seconds):
    seconds = _finite(seconds)
    if seconds is None or seconds < 0:
        return None
    if seconds < 60:
        return f'{seconds:.1f} s'
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f'{minutes} min {rest:02d} s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours} h {minutes:02d} min'


def yes_no(value):
    return None if value is None else 'Yes' if value else 'No'


def _span(start, end):
    try:
        return (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds()
    except (TypeError, ValueError):
        return None


def segments_text(segments):
    parts = []
    for segment in segments or []:
        try:
            parts.append(f"{float(segment['start']):g} → {float(segment['end']):g} ps, step {float(segment['step']):g}")
        except (KeyError, TypeError, ValueError):
            continue
    return '\n'.join(parts) or None


def pauses(events):
    '''(number of pauses, seconds spent paused) from the run's state changes.'''
    count = 0; paused = 0.0; since = None
    for event in events or []:
        status = event.get('status'); t = _finite(event.get('t_s'))
        if status == 'paused':
            count += 1; since = t
        elif since is not None and t is not None:
            paused += t - since; since = None
    return count, paused


def sections(run, quality=None):
    '''[(title, [(key, value or None)])] describing the run; None values are shown as a dash.'''
    metadata = (run or {}).get('metadata') or {}
    root = metadata.get('root') or {}; request = metadata.get('request') or run.get('request') or {}
    acquisition = metadata.get('acquisition') or {}; instrument = metadata.get('instrument') or {}
    camera = instrument.get('camera') or {}; stage = instrument.get('stage') or {}; spectrometer = instrument.get('spectrometer') or {}
    coordinates = metadata.get('coordinates') or {}; calibration = metadata.get('calibration') or {}
    background = metadata.get('background') or {}; events = metadata.get('events') or []
    quality = quality or {}
    status = run.get('status') or root.get('terminal_status')
    started = root.get('created_utc') or run.get('created_utc'); ended = root.get('ended_utc') or run.get('ended_utc')
    pause_count, paused = pauses(events)
    path = run.get('path')
    out = []
    out.append(('Run', [
        ('File', Path(str(path)).name if path else 'Not saved'),
        ('Run ID', root.get('run_id') or run.get('run_id')),
        ('Source', {'simulation': 'Simulation', 'hardware': 'Hardware'}.get(root.get('source_mode'), root.get('source_mode'))),
        ('Status', STATUS.get(status, status)),
        ('Started', local_time(started)),
        ('Ended', local_time(ended)),
        ('Duration', duration(_span(started, ended))),
        ('Pauses', f'{pause_count} · {duration(paused)} paused' if pause_count else 'None'),
    ]))
    total = acquisition.get('total_frames'); recorded = run.get('committed_frames')
    requested = _finite(request.get('exposure_s')); actual = _finite(acquisition.get('exposure_s'))
    exposure = number(actual, 's')
    if exposure and requested is not None and actual is not None and abs(requested - actual) > 1e-12 * max(1.0, abs(requested)):
        exposure += f' (requested {requested:g} s)'
    delays = acquisition.get('delays_ps') or []
    until = request.get('mode') == 'repeat' and request.get('repeats') == 0
    done = root.get('completed_rounds') if root.get('completed_rounds') is not None else run.get('completed_rounds')
    planned = acquisition.get('rounds')
    if until:
        rounds = f'{done} complete · repeated until stopped' if done is not None else 'Repeated until stopped'
    elif planned is None:
        rounds = None
    else:
        rounds = f'{done} of {planned} complete' if done is not None and status in ('stopped', 'error') else str(planned)
    if isinstance(recorded, int) and isinstance(total, int):
        frames = f'{recorded:,}' if until else f'{recorded:,} of {total:,}'
        if run.get('dropped_frames'):
            frames += f" · {int(run['dropped_frames']):,} of the unfinished round discarded"
    else:
        frames = None
    out.append(('Scan', [
        ('Mode', MODES.get(request.get('mode'), request.get('mode'))),
        ('Segments', segments_text(request.get('segments')) if request.get('mode') != 'single' else (f'{float(delays[0]):g} ps' if delays else None)),
        ('Delay points', str(len(delays)) if delays else None),
        ('Rounds', rounds),
        ('Frames per point', None if request.get('frames_per_point') is None else str(request['frames_per_point'])),
        ('Frames recorded', frames),
        ('Exposure', exposure),
        ('Settle time', number(request.get('settle_s'), 's')),
    ]))
    roi = acquisition.get('readout_roi') or []
    readout = READOUT.get(acquisition.get('readout_mode'), acquisition.get('readout_mode'))
    if readout and len(roi) == 4:
        height = roi[3] - roi[2] + 1; vbin = acquisition.get('vbin')
        rows = f'rows {roi[2]}–{roi[3]} ' + ('summed' if vbin in (None, height) else f'binned by {vbin}')
        readout += f'\ncolumns {roi[0]}–{roi[1]}, H bin {acquisition.get("hbin")}; {rows}'
    cooling = None
    if camera.get('cooler_on') is not None:
        cooling = ('On' if camera['cooler_on'] else 'Off') + (f" · target {float(camera['target_temperature_c']):g} °C" if _finite(camera.get('target_temperature_c')) is not None else '')
    out.append(('CCD camera', [
        ('Model', camera.get('model')),
        ('Serial number', camera.get('serial_number')),
        ('Readout', readout),
        ('Pixels per spectrum', None if acquisition.get('pixels') is None else str(acquisition['pixels'])),
        ('Full scale', number(camera.get('full_scale'), 'counts', 8)),
        ('Pre-amp gain', None if _finite(camera.get('preamp_gain')) is None else f"{float(camera['preamp_gain']):g}×"),
        ('Sensitivity', number(camera.get('electrons_per_count'), 'e⁻ / count')),
        ('Read noise', number(camera.get('read_noise_e'), 'e⁻ rms')),
        ('Cooling', cooling),
    ]))
    direction = {1: 'delay grows with stage position', -1: 'delay shrinks with stage position'}.get(coordinates.get('delay_sign'))
    out.append(('Delay stage', [
        ('Stage', stage.get('model')),
        ('Controller', ' · '.join(str(v) for v in (stage.get('controller_model'), stage.get('controller_identity')) if v) or None),
        ('Velocity', number(stage.get('velocity_mm_s'), 'mm/s')),
        ('Referenced', yes_no(stage.get('referenced'))),
        ('Time zero', number(coordinates.get('zero_mm'), 'mm', 9)),
        ('Optical path', (f"× {float(coordinates['path_multiplier']):g}" if _finite(coordinates.get('path_multiplier')) else '') + (f' · {direction}' if direction else '') or None),
        ('Position reported by', POSITION_SOURCES.get(stage.get('position_source'), str(stage.get('position_source') or '').replace('_', ' ').capitalize() or None)),
    ]))
    out.append(('Spectrometer', [
        ('Model', spectrometer.get('model')),
        ('Control', None if spectrometer.get('manual') is None else 'Manual (settings entered by hand)' if spectrometer['manual'] else spectrometer.get('identity') or 'Connected'),
        ('Centre wavelength', number(acquisition.get('center_nm'), 'nm')),
        ('Grating', number(acquisition.get('grating'), 'g/mm')),
        ('Slit', number(acquisition.get('slit_um'), 'µm')),
    ]))
    mapping = None
    if calibration.get('degree') is not None:
        lines = len(calibration.get('line_pairs') or [])
        mapping = f"Polynomial, degree {calibration['degree']} · {lines} reference line{'s' if lines != 1 else ''}"
        if _finite(calibration.get('rms_nm')) is not None:
            mapping += f"\nRMS {float(calibration['rms_nm']):.3g} nm, max {float(calibration.get('max_error_nm') or 0):.3g} nm"
    axis = calibration.get('wavelengths_nm') or []
    out.append(('Wavelength calibration', [
        ('Calibration', calibration.get('calibration_id')),
        ('Source', {'measured': 'Measured', 'synthetic': 'Synthetic (not a real calibration)'}.get(calibration.get('source'), calibration.get('source'))),
        ('Range', f'{min(axis):.2f}–{max(axis):.2f} nm' if axis else None),
        ('Mapping', mapping),
        ('Reference', calibration.get('reference_name')),
        ('Wavelengths', {'air': 'Air', 'vacuum': 'Vacuum'}.get(calibration.get('convention'), calibration.get('convention'))),
        ('Installed', local_time(calibration.get('installed_utc'))),
    ]))
    response = calibration.get('response') or []
    grid = calibration.get('response_wavelengths_nm') or []
    defined = [w for w, v in zip(grid, response) if v is not None]
    out.append(('Intensity response', [
        ('Response', calibration.get('response_id') or 'None · spectra are raw counts'),
        ('Source', {'measured': 'Measured with a lamp of known spectrum', 'imported': 'Imported curves'}.get(calibration.get('response_source'), calibration.get('response_source'))),
        ('Reference', calibration.get('response_reference')),
        ('Defined', f'{min(defined):.1f}–{max(defined):.1f} nm' if defined else None),
        ('Installed', local_time(calibration.get('response_installed_utc'))),
        ('Applied as', '(raw − background) ÷ R(λ); frames stored as raw counts' if calibration.get('response_id') else None),
    ]))
    present = background.get('present')
    out.append(('Background', [
        ('Recorded', yes_no(present) if present is not None else None),
    ] + ([
        ('Frames averaged', None if background.get('frames') is None else str(background['frames'])),
        ('Condition', background.get('condition')),
        ('Acquired', local_time(background.get('acquired_utc'))),
        ('CCD temperature', number(background.get('temperature_c'), '°C', 4)),
    ] if present else [])))
    t_low, t_high = quality.get('temperature_min'), quality.get('temperature_max')
    temperature = None
    if t_low is not None:
        temperature = f'{t_low:.2f} °C' if abs(t_high - t_low) < 5e-3 else f'{t_low:.2f} to {t_high:.2f} °C'
    unstable = quality.get('unstable_frames')
    error = quality.get('position_error_max_mm')
    out.append(('Recorded frames', [
        ('CCD temperature', temperature),
        ('Not stabilized', None if unstable is None else f'{unstable:,} frame' + ('' if unstable == 1 else 's')),
        ('Saturated', None if quality.get('saturated_frames') is None else f"{quality['saturated_frames']:,} frame" + ('' if quality['saturated_frames'] == 1 else 's')),
        ('Stage position error', None if error is None else f'{error * 1000:.3g} µm at most (reported vs planned)'),
        ('Time per frame', duration((quality['last_end_s'] - quality['first_start_s']) / recorded)
         if quality.get('first_start_s') is not None and isinstance(recorded, int) and recorded else None),
    ]))
    if events:
        lines = []
        for event in events:
            t = _finite(event.get('t_s'))
            text = f"{'—' if t is None else duration(t)} · {STATUS.get(event.get('status'), event.get('status')) if event.get('status') != 'running' else 'Running'}"
            if event.get('message'):
                text += f" · {event['message']}"
            lines.append(text)
        out.append(('State changes', [('Since start', '\n'.join(lines))]))
    fits = metadata.get('analysis') or {}
    stored = sum(1 for fit in fits.values() if fit.get('stored'))
    out.append(('Analysis', [('Lifetime fits', f'{len(fits)}' + (f' ({stored} in the file)' if stored != len(fits) else '') if fits else 'None')]))
    return out


class ExperimentInfoDialog(QDialog):
    def __init__(self, run, quality=None, use_settings=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('Experiment details')
        self.setMinimumWidth(520); self.resize(560, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(14)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(); body.setObjectName('experimentInfo')
        body_layout = QVBoxLayout(body); body_layout.setContentsMargins(0, 0, 8, 0); body_layout.setSpacing(18)
        for title, rows in sections(run, quality):
            section = QWidget(); section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0); section_layout.setSpacing(8)
            heading = QLabel(title); heading.setProperty('h2', True)
            section_layout.addWidget(heading)
            grid = QGridLayout(); grid.setHorizontalSpacing(16); grid.setVerticalSpacing(6); grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(0, 150)
            for row, (key, value) in enumerate(rows):
                key_label = QLabel(key); key_label.setObjectName('infoKey'); key_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
                value_label = QLabel('—' if value in (None, '') else str(value)); value_label.setObjectName('infoValue')
                value_label.setWordWrap(True); value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(key_label, row, 0); grid.addWidget(value_label, row, 1)
            section_layout.addLayout(grid)
            body_layout.addWidget(section)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        divider = QFrame(); divider.setObjectName('dialogDivider'); divider.setFixedHeight(1); layout.addWidget(divider)
        buttons = QHBoxLayout(); buttons.setSpacing(8)
        request = ((run or {}).get('metadata') or {}).get('request') or {}
        self.use_button = QPushButton('Use these scan settings')
        self.use_button.setToolTip('Fill the Acquisition page with this run\'s mode, segments, repeats and exposure')
        self.use_button.setEnabled(bool(use_settings and request.get('mode') and request.get('segments')))
        self.use_button.clicked.connect(lambda: (use_settings(request), self.accept()))
        close = QPushButton('Close'); close.setMinimumWidth(80); close.setProperty('role', 'primary')
        close.clicked.connect(self.accept)
        buttons.addWidget(self.use_button); buttons.addStretch(1); buttons.addWidget(close)
        layout.addLayout(buttons)
