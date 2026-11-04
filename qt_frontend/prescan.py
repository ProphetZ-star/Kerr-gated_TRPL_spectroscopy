'''Checks before a scan starts: what would make the data less useful, and a chance to fix it first.

Nothing here blocks a scan. Each finding says what will happen if the scan starts anyway; the person chooses
to start, to take a background first (when that is the finding), or to cancel.'''
import math
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from .widgets import CheckBox

UNSETTLED = {'not_reached': 'still cooling down', 'not_stabilized': 'settling at its target', 'drift': 'drifting away from its target'}


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _size(nbytes):
    return f'{nbytes / 1024 ** 3:.1f} GB' if nbytes >= 1024 ** 3 else f'{nbytes / 1024 ** 2:.0f} MB'


def prescan_issues(snapshot, request, plan=None):
    '''[(kind, title, detail)] for a scan about to start with this request (plan: its latest scan plan, if any).'''
    snapshot = snapshot or {}; issues = []
    exposure = _number(request.get('exposure_s'))
    background = snapshot.get('background') or {}
    taken_at = _number(background.get('exposure_s'))
    if not background.get('spectrum'):
        issues.append(('background', 'No background recorded',
                       'The scan is saved without a background, so Analysis cannot subtract one.'))
    elif exposure is not None and taken_at is not None and abs(taken_at - exposure) > 1e-9 * max(1.0, exposure):
        issues.append(('background', 'Background taken at another exposure',
                       f'The background was taken at {taken_at:g} s and this scan uses {exposure:g} s. Starting the scan discards it.'))
    camera = snapshot.get('camera') or {}
    temperature = _number(camera.get('temperature_c')); target = _number(camera.get('target_temperature_c'))
    state = camera.get('temperature_state')
    now = '' if temperature is None else f' (now {temperature:.1f} °C' + ('' if target is None else f', target {target:g} °C') + ')'
    if camera.get('cooler_on') is False:
        issues.append(('temperature', 'CCD cooling is off', f'The chip runs warm{now}, so its dark current is higher than with the cooler on.'))
    elif state in UNSETTLED:
        issues.append(('temperature', 'CCD temperature not stable',
                       f'The chip is {UNSETTLED[state]}{now}. Frames taken before it settles are marked as unstable; Analysis can leave them out.'))
    plan = plan or {}
    need = _number(plan.get('estimated_memory_bytes')); usable = _number(plan.get('usable_memory_bytes'))
    if need and usable is not None and need > usable:
        issues.append(('memory', 'Not enough free memory for the whole scan',
                       f'The frames take about {_size(need)} and {_size(usable)} is free. The scan will stop by itself when memory runs low, '
                       'keeping every complete point.'))
    if snapshot.get('source_mode') == 'hardware':
        calibration = snapshot.get('calibration') or {}
        if not calibration.get('wavelengths_nm') or calibration.get('source') != 'measured':
            issues.append(('calibration', 'No measured wavelength calibration',
                           'The scan is saved without a wavelength axis; lifetimes cannot be fitted until a calibration is applied.'))
    return issues


class PrescanDialog(QDialog):
    '''choice is 'start', 'background' or 'cancel'; skip_checks is the "don't ask again" box.'''
    def __init__(self, issues, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Before the scan starts')
        self.setMinimumWidth(460)
        self.choice = 'cancel'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(14)
        heading = QLabel('Check before scanning'); heading.setProperty('h2', True)
        layout.addWidget(heading)
        for _, title, detail in issues:
            item = QWidget(); item_layout = QVBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0); item_layout.setSpacing(3)
            name = QLabel('● ' + title); name.setObjectName('prescanTitle')
            text = QLabel(detail); text.setObjectName('infoKey'); text.setWordWrap(True)
            item_layout.addWidget(name); item_layout.addWidget(text)
            layout.addWidget(item)
        self.skip = CheckBox("Don't check again until Kerr Studio restarts")
        self.skip.setMinimumHeight(24)
        layout.addWidget(self.skip)
        divider = QFrame(); divider.setObjectName('dialogDivider'); divider.setFixedHeight(1); layout.addWidget(divider)
        buttons = QHBoxLayout(); buttons.setSpacing(8)
        cancel = QPushButton('Cancel'); cancel.setMinimumWidth(80)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel); buttons.addStretch(1)
        if any(kind == 'background' for kind, _, _ in issues):
            first = QPushButton('Take background first')
            first.setToolTip('Acquire a background at the scan exposure, then start the scan')
            first.clicked.connect(lambda: self._choose('background'))
            buttons.addWidget(first)
        start = QPushButton('Start anyway'); start.setProperty('role', 'primary'); start.setMinimumWidth(110)
        start.clicked.connect(lambda: self._choose('start'))
        buttons.addWidget(start)
        layout.addLayout(buttons)
    @property
    def skip_checks(self):
        return self.skip.isChecked()
    def _choose(self, choice):
        self.choice = choice; self.accept()
