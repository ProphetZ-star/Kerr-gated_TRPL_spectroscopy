'''Native Kerr Studio main window with Acquisition, Analysis and Calibration pages.'''
import csv
import io
import json
import math
import os
import uuid
from pathlib import Path
from typing import get_args
import numpy as np
from PySide6.QtCore import Qt, QTimer, QSignalBlocker, QSize, QPoint
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QScrollArea, QFrame, QStackedWidget, QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QDoubleSpinBox, QSpinBox, QFileDialog, QMessageBox, QInputDialog, QApplication
from .widgets import ArrowComboBox as QComboBox,CheckBox as QCheckBox,WaveLogo,ThemeButton,NativeSpinBox as QSpinBox,NativeDoubleSpinBox as QDoubleSpinBox
from .controller import Controller
from .theme import apply_to_app, palette, current_mode, map_color
from .widgets import Panel, field, number_edit, number_value, int_spin, Segmented, CollapsibleSection, Legend, GradientBar, StartupOverlay, MetricsOverlay, fmt_value, device_availability, set_prop, add_outline, run_icon, ElidedLabel, install_focus_ring, apply_pointer_cursors, InlineValueEdit, parse_count
from .plots import SpectrumPlot, MapPlot, DecayPlot, AnalysisCurvePlot, ResidualPlot, CalibrationImagePlot, CalibrationProfilePlot, CalibrationMappingPlot, CalibrationResidualPlot, fmt, format_duration
from .dialogs import DeviceDialog, DetectorDialog

DEFAULT_SAMPLE_NAME = 'Sample 01 TRPL'


def payload_message(payload):
    if isinstance(payload, dict):
        return str(payload.get('message') or payload.get('error') or payload)
    return str(payload)


def _set_line(edit, text):
    if edit is None or edit.hasFocus():
        return
    edit.setText(str(text))


def _set_combo(combo, text):
    if combo is None or combo.hasFocus():
        return
    index = combo.findText(str(text))
    if index >= 0:
        combo.setCurrentIndex(index)


def _filter_fields(fields, candidate):
    out = {}
    for name, item in fields.items():
        if name in candidate:
            out[name] = candidate[name]
            continue
        alias = getattr(item, 'alias', None)
        if alias and alias in candidate:
            out[name] = candidate[alias]
    return out


def _model_payload(module_name, model_name, candidate, nested=()):
    try:
        import importlib
        module = importlib.import_module(module_name)
        model = getattr(module, model_name)
        fields = getattr(model, 'model_fields', None)
        if not fields:
            return dict(candidate)
        out = _filter_fields(fields, candidate)
        for key in nested:
            if key not in out:
                continue
            field_item = fields.get(key)
            annotation = getattr(field_item, 'annotation', None)
            args = get_args(annotation) if annotation is not None else ()
            if not args:
                continue
            inner = args[0]
            inner_fields = getattr(inner, 'model_fields', None)
            if inner_fields:
                if isinstance(out[key], list):
                    out[key] = [_filter_fields(inner_fields, item) for item in out[key]]
                elif isinstance(out[key], dict):
                    out[key] = _filter_fields(inner_fields, out[key])
        return out
    except Exception:
        return dict(candidate)


class MainWindow(QMainWindow):
    def __init__(self, controller=None, auto_start=False):
        super().__init__()
        self.setObjectName('MainWindow')
        self.setWindowTitle('Kerr Studio · TRPL Acquisition')
        self.resize(1440, 940)
        self.setMinimumSize(1000, 700)
        self.controller = controller or Controller(parent=self)
        self.widgets = {}
        self._snapshot = {}
        self._mode = 'scan'
        self._selected_source = 'hardware'
        self._selected_delay = None
        self._picked_delay = None
        self._current_page = 'acquisition'
        self._updating_controls = False
        self._map_dirty = True
        self._map_key = None
        self._fit_running = False
        self._spectrum_overlays = []
        self._cal_frame = None
        self._cal_profile = None
        self._cal_peaks = []
        self._cal_solution = None
        self._cal_selected_line = None
        self._cal_row_widgets = []
        self._cal_table_updating = False
        self._cal_capture_id = None
        self._cal_signature = None
        self._reference_source = 'synthetic'
        self._reference_imported = False
        self._reference_name = 'Illustrative demo lines'
        self._allow_close = False
        self._closing_started = False
        self.output_dir = Path.cwd() / 'data'
        self._build_ui()
        # Plots that share a wavelength / pixel axis zoom together and share one undo history.
        self.spectrum_plot.sync_x_with(self.map_plot)
        self.cal_image.sync_x_with(self.cal_spectrum)
        if QApplication.instance() is not None:
            install_focus_ring(QApplication.instance())
        apply_pointer_cursors(self)
        self._connect_controller()
        self._map_timer = QTimer(self)
        self._map_timer.setInterval(100)
        self._map_timer.timeout.connect(self._flush_map)
        self._label_timer = QTimer(self)
        self._label_timer.setInterval(150)
        self._label_timer.timeout.connect(self._flush_labels)
        self._cal_timer = QTimer(self)
        self._cal_timer.setInterval(300)
        self._cal_timer.timeout.connect(self._poll_calibration_capture)
        self._load_demo_reference(silent=True)
        self._apply_theme()
        self._apply_visual_geometry()
        self._schedule_refresh()
        if auto_start:
            QTimer.singleShot(0, self.start_backend)
    def _reg(self, key, widget, name=True):
        self.widgets[key] = widget
        if name:
            try:
                widget.setObjectName(key)
            except Exception:
                pass
        return widget
    def _section(self):
        frame = QFrame()
        frame.setObjectName('section')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        return frame, layout
    def _heading(self, title, control=None):
        row = QWidget()
        row.setObjectName('sectionHeading');row.setFixedHeight(27 if title=='Acquisition' else 26)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        text = QLabel(title)
        text.setProperty('h2', True)
        layout.addWidget(text)
        layout.addStretch(1)
        if control is not None:
            layout.addWidget(control)
        return row
    def _set_run_button(self, kind, text):
        if self._run_kind != kind or self.run_button.text() != text:
            self._run_kind = kind
            self.run_button.setText(text)
            self._refresh_run_icons()
    def _refresh_run_icons(self):
        colors = palette()
        self.run_button.setIcon(run_icon(self._run_kind, colors['bg'] if self.run_button.isEnabled() else '#ccffffff'))
        self.stop_button.setIcon(run_icon('stop', colors['bg'] if self.stop_button.isEnabled() else colors['muted']))
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._build_top()
        self._build_subnav()
        self.stack = QStackedWidget()
        self.stack.setObjectName('centralStack')
        self._acquisition_page = self._build_acquisition_page()
        self._analysis_page = self._build_analysis_page()
        self._calibration_page = self._build_calibration_page()
        self._pages = {'acquisition': self._acquisition_page, 'analysis': self._analysis_page, 'calibration': self._calibration_page}
        self.stack.addWidget(self._acquisition_page)
        self.stack.addWidget(self._analysis_page)
        self.stack.addWidget(self._calibration_page)
        self._build_footer()
        root.addWidget(self.top)
        root.addWidget(self.subnav)
        root.addWidget(self.stack, 1)
        self.footer_host=QWidget();self.footer_host.setObjectName('footerHost')
        foot_layout=QVBoxLayout(self.footer_host);foot_layout.setContentsMargins(14,10,14,12);foot_layout.addWidget(self.footer)
        root.addWidget(self.footer_host)
        self.startup = StartupOverlay(self)
        self._reg('startupOverlay', self.startup, False)
        self._reg('startupMessage', self.startup.message, False)
        self._reg('startupRetry', self.startup.retry, False)
        self.startup.retry.clicked.connect(self._retry_startup)
        self.toast = QLabel('', self)
        self.toast.setObjectName('toast')
        self.toast.setWordWrap(True)
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)
        self.startup.setGeometry(self.rect())
        self.startup.raise_()
    def _build_top(self):
        self.top = QWidget()
        self.top.setObjectName('top')
        self.top.setFixedHeight(62)
        layout = QHBoxLayout(self.top)
        layout.setContentsMargins(16, 0, 14, 0)
        layout.setSpacing(14)
        brand = QWidget()
        brand.setFixedWidth(160)
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(10)
        logo = WaveLogo()
        logo.setObjectName('logo');logo.setFixedWidth(17)
        logo.setStyleSheet('font:25px Consolas;')
        brand_text = QWidget()
        brand_text.setFixedHeight(47)
        text_layout = QVBoxLayout(brand_text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        title = QLabel('Kerr Studio')
        title.setObjectName('brandTitle');title.setFixedHeight(29)
        title.setStyleSheet('font-size:19px;font-weight:600;')
        self.run_status = QLabel('● Ready')
        self.run_status.setObjectName('runStatus')
        self.run_status.setFixedHeight(18)
        text_layout.addWidget(title)
        text_layout.addWidget(self.run_status)
        brand_layout.addWidget(logo)
        brand_layout.addWidget(brand_text)
        layout.addWidget(brand)
        run_info = QWidget()
        run_info.setFixedHeight(29)
        info_layout = QVBoxLayout(run_info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(7)
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        self.progress_text = QLabel('Ready to start')
        self.progress_text.setObjectName('progressText')
        self.remaining = QLabel('111 points')
        self.remaining.setObjectName('remaining')
        self.progress_text.setFixedHeight(17);self.remaining.setFixedHeight(17)
        info_row.addWidget(self.progress_text)
        info_row.addStretch(1)
        info_row.addWidget(self.remaining)
        self.progress_fill = QProgressBar()
        self.progress_fill.setObjectName('progressFill')
        self.progress_fill.setRange(0, 100)
        self.progress_fill.setValue(0)
        self.progress_fill.setTextVisible(False)
        info_layout.addLayout(info_row)
        info_layout.addWidget(self.progress_fill)
        layout.addWidget(run_info, 1)
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(7)
        self.run_button = QPushButton('Start')
        self.run_button.setProperty('role', 'primary')
        self.run_button.setObjectName('run')
        self.run_button.setFixedSize(96,34)
        self.run_button.setIconSize(QSize(11,11))
        self._run_kind = 'play'
        self.stop_button = QPushButton('Stop')
        self.stop_button.setObjectName('stop')
        self.stop_button.setFixedSize(80,34)
        self.stop_button.setIconSize(QSize(11,11))
        self.stop_button.setEnabled(False)
        self._refresh_run_icons()
        action_layout.addWidget(self.run_button)
        action_layout.addWidget(self.stop_button)
        layout.addWidget(actions)
        self._reg('run', self.run_button, False)
        self._reg('stop', self.stop_button, False)
        self._reg('runStatus', self.run_status, False)
        self._reg('progressText', self.progress_text, False)
        self._reg('remaining', self.remaining, False)
        self._reg('progressFill', self.progress_fill, False)
        self.run_button.clicked.connect(self.start_pause_resume)
        self.stop_button.clicked.connect(self.stop_active)
    def _build_subnav(self):
        self.subnav = QWidget()
        self.subnav.setObjectName('subnav')
        self.subnav.setFixedHeight(39)
        layout = QHBoxLayout(self.subnav)
        layout.setContentsMargins(16, 0, 14, 0)
        layout.setSpacing(12)
        self.nav_buttons = {}
        nav=QWidget();nav_layout=QHBoxLayout(nav);nav_layout.setContentsMargins(0,0,0,0);nav_layout.setSpacing(20)
        for key, text in [('acquisition', 'Acquisition'), ('analysis', 'Analysis'), ('calibration', 'Spectral Calibration')]:
            button = QPushButton(text)
            button.setObjectName(key)
            button.setProperty('nav', True)
            button.setProperty('active', key == 'acquisition')
            button.clicked.connect(lambda checked=False, page=key: self.show_page(page))
            self.nav_buttons[key] = button
            self._reg(key, button, False)
            nav_layout.addWidget(button)
        layout.addWidget(nav)
        layout.addStretch(1)
        # Mirrors the footer file name (kept in sync in _sync_sample_name).
        self.sample_name = ElidedLabel(DEFAULT_SAMPLE_NAME)
        self.sample_name.setObjectName('sampleName')
        self.sample_name.setMaximumWidth(320)
        self._reg('sampleName', self.sample_name, False)
        layout.addWidget(self.sample_name)
        self.hardware_mode = QComboBox()
        self.hardware_mode.setObjectName('sourceMode')
        self.hardware_mode.setProperty('ownTextColor', True)
        self.hardware_mode.setProperty('centerText', True)
        self.hardware_mode.addItem('HARDWARE', 'hardware')
        self.hardware_mode.addItem('SIMULATION · hardware offline', 'simulation')
        for index in range(self.hardware_mode.count()):
            self.hardware_mode.setItemData(index, Qt.AlignCenter, Qt.TextAlignmentRole)
        self.hardware_mode.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.hardware_mode.setToolTip('Acquisition source')
        self._reg('hardwareMode', self.hardware_mode, False)
        self.hardware_mode.activated.connect(lambda index: self._choose_source(self.hardware_mode.itemData(index)))
        layout.addWidget(self.hardware_mode)
        self.theme_button = ThemeButton()
        self.theme_button.setObjectName('theme')
        self.theme_button.setFixedSize(27,27)  # square, same height as the source dropdown
        self._reg('theme', self.theme_button, False)
        self.theme_button.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_button)
    def _build_footer(self):
        self.footer = QFrame()
        self.footer.setObjectName('foot')
        add_outline(self.footer)
        self.footer.setFixedHeight(52)
        layout = QHBoxLayout(self.footer)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(10)
        save_label = QLabel('File name')
        save_label.setObjectName('save-label')
        layout.addWidget(save_label)
        self.save_path = QLineEdit(DEFAULT_SAMPLE_NAME)
        self.save_path.setObjectName('savePath')
        self.save_path.setMaxLength(180)
        self.save_path.setProperty('mono', True)
        self.save_path.setPlaceholderText(DEFAULT_SAMPLE_NAME)
        self.save_path.textChanged.connect(self._sync_sample_name)
        self._reg('savePath', self.save_path, False)
        self.save_path.setFixedHeight(34)
        layout.addWidget(self.save_path, 1)
        self.path_button = QPushButton('Choose folder…')
        self.path_button.setObjectName('path')
        self._reg('path', self.path_button, False)
        self.path_button.clicked.connect(self._choose_output_folder)
        self.path_button.setFixedHeight(34)
        layout.addWidget(self.path_button)
        self.autosave = QCheckBox('Autosave')
        self.autosave.setObjectName('autosave')
        self.autosave.setChecked(True)
        self._reg('autosave', self.autosave, False)
        layout.addWidget(self.autosave)
        self.save_status = ElidedLabel('Not saved to disk')
        self.save_status.setObjectName('saveStatus')
        self._reg('saveStatus', self.save_status, False)
        layout.addWidget(self.save_status)
        self.export_button = QPushButton('↓ Export CSV')
        self.export_button.setObjectName('export')
        self.export_button.setProperty('role', 'accent')
        self._reg('export', self.export_button, False)
        self.export_button.clicked.connect(self.export_current_csv)
        self.export_button.setFixedHeight(34);self.export_button.setEnabled(False)
        layout.addWidget(self.export_button)
    def _build_acquisition_page(self):
        page = QWidget()
        page.setObjectName('acquisitionPage')
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 0)
        layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(280)
        scroll.setMaximumWidth(340)
        self.acquisition_scroll=scroll
        inner = QFrame()
        inner.setObjectName('sidebar')
        add_outline(inner)
        side_layout = QVBoxLayout(inner)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        self._build_stage_section(side_layout)
        self._build_detection_section(side_layout)
        self._build_scan_section(side_layout)
        side_layout.setStretch(2,1)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        data = QWidget()
        data_layout = QVBoxLayout(data)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)
        data_layout.addWidget(self._build_live_spectrum_panel(), 112)
        data_layout.addWidget(self._build_map_panel(), 100)
        layout.addWidget(data, 1)
        self._reg('acquisitionPage', page, False)
        return page
    def _build_stage_section(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(14,13,14,14)
        self.stage_connection = QPushButton('○ Connect…')
        self.stage_connection.setObjectName('stageConnection')
        self._reg('stageConnection', self.stage_connection, False)
        self.stage_connection.clicked.connect(self._open_stage_dialog)
        layout.addWidget(self._heading('Delay Stage', self.stage_connection))
        self.stage_fields = QWidget()
        stage_layout = QVBoxLayout(self.stage_fields)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(8)
        delay_row = QHBoxLayout()
        delay_row.setContentsMargins(0, 0, 0, 0)
        delay_row.setSpacing(7)
        self.delay_edit = number_edit('0.350')
        self.delay_edit.setObjectName('delay')
        self._reg('delay', self.delay_edit, False)
        delay_row.addWidget(self.delay_edit, 1)
        unit = QLabel('ps')
        unit.setProperty('muted', True)
        delay_row.addWidget(unit)
        self.move_absolute = QPushButton('Move')
        self.move_absolute.setObjectName('moveAbsolute')
        self._reg('moveAbsolute', self.move_absolute, False)
        self.move_absolute.clicked.connect(self._move_absolute)
        delay_row.addWidget(self.move_absolute)
        stage_layout.addLayout(delay_row)
        self.stage_status = QLabel('● In position')
        self.stage_status.setObjectName('stageStatus')
        self._reg('stageStatus', self.stage_status, False)
        stage_layout.addWidget(self.stage_status)
        nudge = QWidget()
        nudge_layout = QGridLayout(nudge)
        nudge_layout.setContentsMargins(0, 0, 0, 0)
        nudge_layout.setSpacing(5)
        for column, value in enumerate([-1, -0.1, 0.1, 1]):
            text = '-1' if value == -1 else '-0.1' if value == -0.1 else '+0.1' if value == 0.1 else '+1'
            button = QPushButton(text + ' ps')
            button.setToolTip(f'Move the delay by {text} ps')
            button.clicked.connect(lambda checked=False, step=value: self.move_stage(step, relative=True))
            nudge_layout.addWidget(button, 0, column)
        stage_layout.addWidget(nudge)
        self.zero_button = QPushButton('Set zero t₀')
        self.zero_button.setObjectName('zero')
        self.zero_button.setProperty('role', 'accent')
        self.zero_button.clicked.connect(self.set_zero)
        self._reg('zero', self.zero_button, False)
        self.return_zero = QPushButton('Go to t₀')
        self.return_zero.setObjectName('returnZero')
        self.return_zero.setProperty('role', 'accent')
        self.return_zero.clicked.connect(lambda: self.move_stage(0, relative=False))
        self._reg('returnZero', self.return_zero, False)
        zero_row=QHBoxLayout();zero_row.setSpacing(8);zero_row.setContentsMargins(0,0,0,0)
        self.zero_button.setFixedHeight(32);self.return_zero.setFixedHeight(32)
        zero_row.addWidget(self.zero_button,1);zero_row.addWidget(self.return_zero,1);stage_layout.addLayout(zero_row)
        layout.addWidget(self.stage_fields)
        parent_layout.addWidget(frame)
    def _build_detection_section(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(14,13,14,15);layout.setSpacing(11)
        self.detector_connection = QPushButton('CCD off · Manual ▾')
        self.detector_connection.setObjectName('detectorConnection')
        self._reg('detectorConnection', self.detector_connection, False)
        self.detector_connection.clicked.connect(self._open_detector_dialog)
        layout.addWidget(self._heading('Detection', self.detector_connection))
        self.detector_fields = QWidget()
        detector_layout = QVBoxLayout(self.detector_fields)
        detector_layout.setContentsMargins(0, 0, 0, 0)
        detector_layout.setSpacing(9)
        pair = QWidget()
        pair_layout = QGridLayout(pair)
        pair_layout.setContentsMargins(0, 0, 0, 0)
        pair_layout.setHorizontalSpacing(10)
        pair_layout.setVerticalSpacing(0)
        self.exposure_edit = number_edit('1.000')
        self.exposure_edit.setObjectName('exposure')
        self._reg('exposure', self.exposure_edit, False)
        self.exposure_edit.editingFinished.connect(self._on_exposure_changed)
        self.center_edit = number_edit('600.0')
        self.center_edit.setObjectName('center')
        self._reg('center', self.center_edit, False)
        self.center_edit.editingFinished.connect(self._apply_spectrograph)
        pair_layout.addWidget(field('Exposure', self.exposure_edit, 's'), 0, 0)
        pair_layout.addWidget(field('Center wavelength', self.center_edit, 'nm'), 0, 1)
        detector_layout.addWidget(pair)
        self.advanced = CollapsibleSection('Advanced · Grating / Slit / Readout')
        advanced_widget = QWidget()
        advanced_layout = QVBoxLayout(advanced_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)
        spectro_pair = QWidget()
        spectro_grid = QGridLayout(spectro_pair)
        spectro_grid.setContentsMargins(0, 0, 0, 0)
        spectro_grid.setHorizontalSpacing(10)
        spectro_grid.setVerticalSpacing(0)
        self.grating_combo = QComboBox()
        self.grating_combo.addItems(['300', '600', '1200'])
        self.grating_combo.setObjectName('grating')
        self.grating_combo.activated.connect(self._apply_spectrograph)
        self._reg('grating', self.grating_combo, False)
        self.slit_edit = number_edit('200')
        self.slit_edit.setObjectName('slit')
        self.slit_edit.editingFinished.connect(self._apply_spectrograph)
        self._reg('slit', self.slit_edit, False)
        spectro_grid.addWidget(field('Grating / g·mm⁻¹', self.grating_combo), 0, 0)
        spectro_grid.addWidget(field('Slit', self.slit_edit, 'μm'), 0, 1)
        spectro_grid.setColumnStretch(0, 1)
        spectro_grid.setColumnStretch(1, 1)
        advanced_layout.addWidget(spectro_pair)
        # Scans always read the CCD in full vertical binning; show the fixed geometry instead of dead inputs.
        readout_info = QLabel('Readout  FVB · H bin 1\nROI  X 0–1024 · Y 0–255 (full chip)')
        readout_info.setObjectName('advancedInfo')
        readout_info.setToolTip('Scan acquisition uses full vertical binning over the whole chip.\nRow selection is available on the Spectral Calibration page.')
        advanced_layout.addWidget(readout_info)
        self.advanced.add(advanced_widget)
        detector_layout.addWidget(self.advanced)
        layout.addWidget(self.detector_fields)
        parent_layout.addWidget(frame)
    def _build_scan_section(self, parent_layout):
        frame, layout = self._section()
        layout.setAlignment(Qt.AlignTop);layout.setSpacing(13)
        self.preview_button = QPushButton('Live preview')
        self.preview_button.setObjectName('preview')
        self.preview_button.setProperty('role','accent');self.preview_button.setFixedHeight(27)
        self._reg('preview', self.preview_button, False)
        self.preview_button.clicked.connect(self.toggle_preview)
        layout.addWidget(self._heading('Acquisition', self.preview_button))
        self.mode_seg = Segmented([('Scan', 'scan'), ('Single', 'single'), ('Repeat', 'repeat')])
        self.mode_seg.set_selected('scan')
        self.mode_seg.setObjectName('mode');self.mode_seg.setFixedHeight(33);self.mode_seg.layout.setContentsMargins(3,3,3,3)
        for button in self.mode_seg.buttons:button.setFixedHeight(27)
        self.mode_seg.changed.connect(self._mode_changed)
        self._reg('mode', self.mode_seg, False)
        layout.addWidget(self.mode_seg)
        self.scan_params = QWidget()
        scan_layout = QVBoxLayout(self.scan_params)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.setSpacing(6)
        scan_layout.setAlignment(Qt.AlignTop)
        segment_head = QWidget();segment_head.setFixedHeight(28)
        head_layout = QHBoxLayout(segment_head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(4)
        segment_title = QLabel('Scan segments / ps')
        segment_title.setProperty('muted', True)
        head_layout.addWidget(segment_title)
        head_layout.addStretch(1)
        self.add_segment = QPushButton('＋ Add')
        self.add_segment.setObjectName('addSegment')
        self._reg('addSegment', self.add_segment, False)
        self.add_segment.clicked.connect(lambda: self._add_segment_row())
        head_layout.addWidget(self.add_segment)
        scan_layout.addWidget(segment_head)
        labels = QWidget();labels.setFixedHeight(16)
        header_cells = []
        for text in ['Start', 'End', 'Step']:
            item = QLabel(text)
            item.setProperty('muted', True)
            item.setContentsMargins(10, 0, 0, 0)
            header_cells.append(item)
        remove_column = QWidget();remove_column.setFixedWidth(self._SEGMENT_REMOVE_WIDTH)
        self._segment_cells(labels, header_cells, remove_column, False)
        scan_layout.addWidget(labels)
        # Rows grow with the sidebar (which scrolls as a whole) so the column grid never shifts under a nested scrollbar.
        self.segments_host = QWidget()
        self.segments_host.setObjectName('segments')
        self.segments_layout = QVBoxLayout(self.segments_host)
        self.segments_layout.setContentsMargins(0, 0, 0, 0)
        self.segments_layout.setSpacing(6)
        self._reg('segments', self.segments_host, False)
        scan_layout.addWidget(self.segments_host)
        self.repeat_wrap = QWidget()
        repeat_layout = QHBoxLayout(self.repeat_wrap)
        repeat_layout.setContentsMargins(0, 6, 0, 0)
        repeat_layout.setSpacing(10)
        repeat_label = QLabel('Repeat count')
        repeat_label.setProperty('muted', True)
        repeat_layout.addWidget(repeat_label)
        repeat_layout.addStretch(1)
        self.repeats_input = int_spin(3, 1, 100, 1)
        self.repeats_input.setObjectName('repeats')
        self.repeats_input.setFixedSize(96, 30)
        self._reg('repeats', self.repeats_input, False)
        repeat_layout.addWidget(self.repeats_input)
        self.repeat_wrap.hide()
        scan_layout.addWidget(self.repeat_wrap)
        layout.addWidget(self.scan_params)
        self.validation = QLabel('')
        self.validation.setObjectName('validation')
        self._reg('validation', self.validation, False)
        layout.addWidget(self.validation)
        self._segment_rows = []
        self._add_segment_row('-2','0','0.1');self._add_segment_row('0','2','0.05');self._add_segment_row('2','20','0.5')
        self._mode_changed('scan')
        parent_layout.addWidget(frame)
    def _build_live_spectrum_panel(self):
        panel = Panel('Live Spectrum')
        self._reg('liveSpectrumPanel', panel, False)
        self.saturation = QLabel('')
        self.saturation.setObjectName('saturation')
        self.saturation.hide()
        self._reg('saturation', self.saturation, False)
        panel.title_layout.addWidget(self.saturation)
        legend = Legend()
        # Reads "Current" for the live frame, or "Δt = … ps" for a delay picked on the map after a scan.
        self._spectrum_legend_current = legend.add('Current').findChild(QLabel)
        legend.add('Background', dashed=True)
        panel.add_tool(legend)
        self.capture_background = QPushButton('Acquire background')
        self.capture_background.setObjectName('captureBackground')
        self._reg('captureBackground', self.capture_background, False)
        self.capture_background.clicked.connect(self._capture_background)
        panel.add_tool(self.capture_background)
        self.spectrum_scale = Segmented([('Linear', 'linear'), ('Log Y', 'log')])
        self.spectrum_scale.set_selected('linear')
        self.spectrum_scale.changed.connect(lambda value: self._schedule_refresh())
        self._reg('spectrumScale', self.spectrum_scale, False)
        panel.add_tool(self.spectrum_scale)
        self.spectrum_plot = SpectrumPlot(grid=True)
        self.spectrum_plot.wavePicked.connect(self._map_wave_picked)
        self.spectrum_plot.setObjectName('spectrum')
        self._reg('spectrum', self.spectrum_plot, False)
        panel.add_body(self.spectrum_plot, 1)
        return panel
    def _build_map_panel(self):
        panel = Panel('TRPL Map & Decay')
        self._reg('mapPanel', panel, False)
        color_key = QWidget()
        key_layout = QHBoxLayout(color_key)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        # Colour range ends: click to type a count (k / M accepted); empty returns that end to auto.
        self.color_min = InlineValueEdit('0')
        self.color_min.committed.connect(lambda text: self._color_range_edited(0, text))
        self._reg('colorLabel', self.color_min, False)
        self.map_levels_auto = QPushButton('Auto')
        self.map_levels_auto.setObjectName('colorAuto')
        self.map_levels_auto.setToolTip('Return the colour range to the data range')
        self.map_levels_auto.clicked.connect(self._color_range_auto)
        self.map_levels_auto.hide()
        self.gradient = GradientBar()
        self.gradient.setFixedSize(130,6)
        self.gradient.setObjectName('mapGradient')
        self._reg('mapGradient', self.gradient, False)
        self.color_max = InlineValueEdit('1k')
        self.color_max.committed.connect(lambda text: self._color_range_edited(1, text))
        self._reg('colorMax', self.color_max, False)
        for edit in (self.color_min, self.color_max):
            edit.setToolTip('Click to set the colour range in counts · empty = auto')
        # Shown once any acquired pixel reached CCD full scale; matches the colour painted on the map.
        self.map_saturated_key = QWidget();self.map_saturated_key.setObjectName('saturatedKey')
        saturated_layout = QHBoxLayout(self.map_saturated_key);saturated_layout.setContentsMargins(0,0,8,0);saturated_layout.setSpacing(4)
        swatch = QFrame();swatch.setObjectName('saturatedSwatch');swatch.setFixedSize(9,9)
        saturated_text = QLabel('Saturated');saturated_text.setObjectName('saturatedText')
        saturated_layout.addWidget(swatch);saturated_layout.addWidget(saturated_text)
        self.map_saturated_key.setToolTip('Pixels that reached CCD full scale in at least one frame')
        self.map_saturated_key.hide()
        key_layout.addWidget(self.map_levels_auto)
        key_layout.addWidget(self.map_saturated_key)
        key_layout.addWidget(self.color_min)
        key_layout.addWidget(self.gradient)
        key_layout.addWidget(self.color_max)
        color_key.setParent(panel.header);color_key.setFixedHeight(20);self.map_color_key=color_key
        self._map_levels = None  # (low, high) counts; None at either end = follow the data
        self.map_scale = Segmented([('Linear', 'linear'), ('Log', 'log')])
        self.map_scale.set_selected('log')
        self.map_scale.changed.connect(self._scale_changed)
        self._reg('mapScale', self.map_scale, False)
        panel.add_tool(self.map_scale)
        charts = QWidget()
        charts_layout = QHBoxLayout(charts)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(0)
        self.map_plot = MapPlot()
        self.map_plot.setObjectName('map')
        self._reg('map', self.map_plot, False)
        self.decay_plot = DecayPlot();self.decay_plot.set_y_label('');self.decay_plot.left_axis.setStyle(showValues=False)
        self.decay_plot.setObjectName('decay')
        self._reg('decay', self.decay_plot, False)
        try:
            self.decay_plot.link_axis('y',self.map_plot)
        except Exception:
            pass
        # Map and decay share the y axis: the map keeps no right gutter, the decay keeps a narrow one on its left.
        self.map_plot.set_plot_margins(68, 0, 10, 46)
        charts_layout.addWidget(self.map_plot, 4)
        charts_layout.addWidget(self.decay_plot, 1)
        self.map_plot.delayPicked.connect(self._map_delay_picked)
        self.map_plot.wavePicked.connect(self._map_wave_picked)
        self.decay_plot.delayPicked.connect(self._map_delay_picked)
        panel.add_body(charts, 1)
        return panel
    def _build_analysis_page(self):
        page = QWidget()
        page.setObjectName('analysisPage')
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(280)
        scroll.setMaximumWidth(340)
        self.analysis_scroll=scroll
        inner = QFrame()
        inner.setObjectName('analysisControls')
        add_outline(inner)
        controls = QVBoxLayout(inner)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        self._build_analysis_experiment(controls)
        self._build_analysis_view(controls)
        self._build_analysis_spectrum_controls(controls)
        self._build_analysis_fit_controls(controls)
        controls.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        results = QWidget()
        results.setObjectName('analysisResults')
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)
        results_layout.addWidget(self._build_analysis_curve(), 1)
        self.analysis_bottom=self._build_analysis_bottom()
        results_layout.addWidget(self.analysis_bottom)
        layout.addWidget(results, 1)
        self._reg('analysisPage', page, False)
        return page
    def _build_analysis_experiment(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(17,17,17,17)
        self.analysis_fit_state = QLabel('No data')
        self.analysis_fit_state.setObjectName('analysisFitState')
        self._reg('analysisFitState', self.analysis_fit_state, False)
        layout.addWidget(self._heading('Experiment', self.analysis_fit_state))
        self.analysis_source = QLabel('No experiment loaded')
        self.analysis_source.setObjectName('analysisSource')
        self.analysis_source.setWordWrap(True)
        self.analysis_source.setProperty('mono', True)
        self._reg('analysisSource', self.analysis_source, False)
        layout.addWidget(self.analysis_source)
        meta = QWidget()
        meta_layout = QHBoxLayout(meta)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(8)
        self.analysis_points = QLabel('0 delays')
        self.analysis_points.setObjectName('analysisPoints')
        self.analysis_frames = QLabel('0 repeats')
        self.analysis_frames.setObjectName('analysisFrames')
        self._reg('analysisPoints', self.analysis_points, False)
        self._reg('analysisFrames', self.analysis_frames, False)
        meta_layout.addWidget(self.analysis_points)
        meta_layout.addWidget(self.analysis_frames)
        meta_layout.addStretch(1)
        layout.addWidget(meta)
        open_label = QLabel('Open HDF5 file')
        open_label.setProperty('muted', True)
        layout.addSpacing(14)
        layout.addWidget(open_label)
        open_row = QWidget()
        open_layout = QHBoxLayout(open_row)
        open_layout.setContentsMargins(0, 0, 0, 0)
        open_layout.setSpacing(7)
        self.open_path = QLineEdit('')
        self.open_path.setObjectName('openPath')
        self.open_path.setReadOnly(True)
        self.open_path.setPlaceholderText('Choose an HDF5 file')
        self._reg('openPath', self.open_path, False)
        self.open_run = QPushButton('Open…')
        self.open_run.setObjectName('openRun');self.open_run.setFixedHeight(32)
        self._reg('openRun', self.open_run, False)
        self.open_run.clicked.connect(lambda: self.open_analysis_file())
        open_layout.addWidget(self.open_path, 1)
        open_layout.addWidget(self.open_run)
        layout.addWidget(open_row)
        self.open_file = QLineEdit('')
        self.open_file.setObjectName('openFile')
        self.open_file.hide()
        self._reg('openFile', self.open_file, False)
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(7)
        self.save_run = QPushButton('Save HDF5')
        self.save_run.setObjectName('saveRun');self.save_run.setFixedHeight(32)
        self._reg('saveRun', self.save_run, False)
        self.save_run.clicked.connect(self._save_analysis_run)
        self.export_fit = QPushButton('Export fit CSV')
        self.export_fit.setObjectName('exportFit');self.export_fit.setFixedHeight(32)
        self.export_fit.setEnabled(False)
        self._reg('exportFit', self.export_fit, False)
        self.export_fit.clicked.connect(self._export_fit_csv)
        action_layout.addWidget(self.save_run)
        action_layout.addWidget(self.export_fit)
        layout.addWidget(actions)
        details = CollapsibleSection('Experiment actions')
        self.discard_run = QPushButton('Discard in-memory run…')
        self.discard_run.setObjectName('discardRun')
        self.discard_run.setProperty('role', 'dangerText')
        self._reg('discardRun', self.discard_run, False)
        self.discard_run.clicked.connect(self._discard_run)
        details.add(self.discard_run)
        layout.addWidget(details)
        parent_layout.addWidget(frame)
    def _build_analysis_view(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(17,17,17,17);layout.setSpacing(16)
        label = QLabel('View')
        label.setProperty('muted', True)
        layout.addWidget(label)
        self.analysis_view = QComboBox()
        self.analysis_view.setObjectName('analysisView')
        self.analysis_view.addItem('Decay & fit', 'decay')
        self.analysis_view.addItem('Spectrum', 'spectrum')
        self.analysis_view.activated.connect(lambda index: self._analysis_view_changed())
        self._reg('analysisView', self.analysis_view, False)
        layout.addWidget(self.analysis_view)
        parent_layout.addWidget(frame)
    def _build_analysis_spectrum_controls(self, parent_layout):
        frame, layout = self._section()
        self.analysis_spectrum_controls = frame
        self.analysis_spectrum_controls.setObjectName('analysisSpectrumControls')
        self._reg('analysisSpectrumControls', self.analysis_spectrum_controls, False)
        layout.addWidget(self._heading('Spectrum controls'))
        label = QLabel('Delay / ps')
        label.setProperty('muted', True)
        layout.addWidget(label)
        self.analysis_delay = QComboBox()
        self.analysis_delay.setObjectName('analysisDelay')
        self._reg('analysisDelay', self.analysis_delay, False)
        layout.addWidget(self.analysis_delay)
        buttons = QWidget()
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(7)
        self.analysis_add_delay = QPushButton('＋ Overlay')
        self.analysis_add_delay.setObjectName('analysisAddDelay')
        self._reg('analysisAddDelay', self.analysis_add_delay, False)
        self.analysis_add_delay.clicked.connect(self._add_spectrum_overlay)
        self.analysis_clear_delays = QPushButton('Clear')
        self.analysis_clear_delays.setObjectName('analysisClearDelays')
        self._reg('analysisClearDelays', self.analysis_clear_delays, False)
        self.analysis_clear_delays.clicked.connect(self._clear_spectrum_overlays)
        button_layout.addWidget(self.analysis_add_delay)
        button_layout.addWidget(self.analysis_clear_delays)
        layout.addWidget(buttons)
        self.analysis_spectrum_status = QLabel('')
        self.analysis_spectrum_status.setObjectName('analysisSpectrumStatus')
        self.analysis_spectrum_status.setWordWrap(True)
        self.analysis_spectrum_status.setProperty('muted', True)
        self._reg('analysisSpectrumStatus', self.analysis_spectrum_status, False)
        layout.addWidget(self.analysis_spectrum_status)
        frame.hide()
        parent_layout.addWidget(frame)
    def _build_analysis_fit_controls(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(17,17,17,17)
        self.analysis_fit_controls = frame
        self.analysis_fit_controls.setObjectName('analysisFitControls')
        self._reg('analysisFitControls', self.analysis_fit_controls, False)
        layout.addWidget(self._heading('Fit controls'))
        grid = QWidget()
        grid_layout = QGridLayout(grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(12)
        grid_layout.setVerticalSpacing(8)
        self.analysis_wave = number_edit('600')
        self.analysis_wave.setObjectName('analysisWave')
        self.analysis_wave.editingFinished.connect(self._analysis_selection_changed)
        self._reg('analysisWave', self.analysis_wave, False)
        self.analysis_window = number_edit('5')
        self.analysis_window.setObjectName('analysisWindow')
        self.analysis_window.editingFinished.connect(self._analysis_selection_changed)
        self._reg('analysisWindow', self.analysis_window, False)
        grid_layout.addWidget(field('Wavelength', self.analysis_wave, 'nm'), 0, 0)
        grid_layout.addWidget(field('Half-window ±', self.analysis_window, 'nm'), 0, 1)
        layout.addWidget(grid)
        model_label = QLabel('Model')
        model_label.setProperty('muted', True)
        layout.addWidget(model_label)
        self.fit_model = QComboBox()
        self.fit_model.setObjectName('fitModel')
        self.fit_model.addItem('Double exponential', 'double')
        self.fit_model.addItem('Single exponential', 'single')
        self._reg('fitModel', self.fit_model, False)
        layout.addWidget(self.fit_model)
        irf_label = QLabel('Gaussian IRF · FWHM')
        irf_label.setProperty('muted', True)
        layout.addWidget(irf_label)
        self.irf_width = number_edit('.25')
        self.irf_width.setObjectName('irfWidth')
        self.irf_width.setFixedHeight(32)
        self._reg('irfWidth', self.irf_width, False)
        layout.addWidget(field('', self.irf_width, 'ps'))
        range_grid = QWidget()
        range_layout = QGridLayout(range_grid)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setHorizontalSpacing(12)
        self.fit_start = number_edit('')
        self.fit_start.setObjectName('fitStart')
        self.fit_start.setPlaceholderText('Auto')
        self._reg('fitStart', self.fit_start, False)
        self.fit_end = number_edit('')
        self.fit_end.setObjectName('fitEnd')
        self.fit_end.setPlaceholderText('Auto')
        self._reg('fitEnd', self.fit_end, False)
        range_layout.addWidget(field('Fit start', self.fit_start, 'ps'), 0, 0)
        range_layout.addWidget(field('Fit end', self.fit_end, 'ps'), 0, 1)
        layout.addWidget(range_grid)
        self.run_fit_button = QPushButton('▶ Run fit')
        self.run_fit_button.setObjectName('runFit');self.run_fit_button.setFixedHeight(36)
        self.run_fit_button.setProperty('role', 'primary')
        self.run_fit_button.setEnabled(False)
        self._reg('runFit', self.run_fit_button, False)
        self.run_fit_button.clicked.connect(self.run_fit)
        layout.addWidget(self.run_fit_button)
        self.fit_status = QLabel('')
        self.fit_status.setObjectName('fitStatus')
        self.fit_status.setWordWrap(True)
        self._reg('fitStatus', self.fit_status, False)
        layout.addWidget(self.fit_status)
        parent_layout.addWidget(frame)
    def _build_analysis_curve(self):
        panel = Panel('Decay & fit')
        panel.title_label.hide();panel.header.setFixedHeight(57)
        self._reg('analysisPlotPanel', panel, False)
        self.analysis_plot_title = QLabel('Decay & fit')
        self.analysis_plot_title.setObjectName('analysisPlotTitle');self.analysis_plot_title.setProperty('h2',True)
        self._reg('analysisPlotTitle', self.analysis_plot_title, False)
        panel.title_layout.addWidget(self.analysis_plot_title)
        self.analysis_window_label = QLabel('600 ± 5 nm')
        self.analysis_window_label.setObjectName('analysisWindowLabel')
        self._reg('analysisWindowLabel', self.analysis_window_label, False)
        panel.title_layout.addWidget(self.analysis_window_label)
        self.analysis_point = QLabel('')
        self.analysis_point.setObjectName('analysisPoint')
        self.analysis_point.hide()
        self._reg('analysisPoint', self.analysis_point, False)
        panel.add_tool(self.analysis_point)
        self.analysis_legend = Legend()
        self.analysis_legend.add('Observed', dot=True)
        self.analysis_legend.add('Fit',color=palette()['amber'])
        self.analysis_legend.setObjectName('analysisDecayLegend')
        self._reg('analysisDecayLegend', self.analysis_legend, False)
        panel.add_tool(self.analysis_legend)
        self.analysis_time_key = QWidget()
        time_layout = QHBoxLayout(self.analysis_time_key)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(5)
        self.analysis_time_min = QLabel('0 ps')
        self.analysis_time_min.setObjectName('analysisTimeMin')
        self.analysis_time_max = QLabel('0 ps')
        self.analysis_time_max.setObjectName('analysisTimeMax')
        self._reg('analysisTimeMin', self.analysis_time_min, False)
        self._reg('analysisTimeMax', self.analysis_time_max, False)
        gradient = GradientBar()
        gradient.setObjectName('analysisTimeGradient');gradient.setFixedSize(100,7)
        self._reg('analysisTimeGradient', gradient, False)
        time_layout.addWidget(self.analysis_time_min)
        time_layout.addWidget(gradient)
        time_layout.addWidget(self.analysis_time_max);time_layout.addWidget(QLabel('ps'))
        self.analysis_time_key.setObjectName('analysisTimeKey')
        self.analysis_time_key.hide()
        self._reg('analysisTimeKey', self.analysis_time_key, False)
        panel.add_tool(self.analysis_time_key)
        self.analysis_scale = Segmented([('Linear', 'linear'), ('Log Y', 'log')])
        self.analysis_scale.set_selected('linear')
        self.analysis_scale.changed.connect(lambda value: self._redraw_analysis())
        self._reg('analysisScale', self.analysis_scale, False)
        panel.add_tool(self.analysis_scale)
        self.analysis_curve = AnalysisCurvePlot()
        self.analysis_curve.setObjectName('analysisCurve')
        self._reg('analysisCurve', self.analysis_curve, False)
        self.analysis_curve.delayPicked.connect(self._analysis_delay_picked)
        panel.add_body(self.analysis_curve, 1)
        self.analysis_residual_wrap = QWidget()
        self.analysis_residual_wrap.setFixedHeight(90)
        self.analysis_residual_wrap.setObjectName('analysisResidualWrap')
        residual_layout = QVBoxLayout(self.analysis_residual_wrap)
        residual_layout.setContentsMargins(0, 0, 0, 0)
        residual_layout.setSpacing(0)
        self.analysis_residual = ResidualPlot()
        self.analysis_residual.link_axis('x',self.analysis_curve)
        self.analysis_residual.set_x_label('')
        self.analysis_residual.bottom_axis.setStyle(showValues=False)
        self.analysis_residual.setObjectName('analysisResidual')
        self._reg('analysisResidual', self.analysis_residual, False)
        self.analysis_residual.delayPicked.connect(self._analysis_delay_picked)
        residual_layout.addWidget(self.analysis_residual)
        panel.add_body(self.analysis_residual_wrap, 0)
        self.analysis_spectrum_legend = QWidget()
        self.analysis_spectrum_legend.setObjectName('analysisSpectrumLegend')
        spectrum_layout = QHBoxLayout(self.analysis_spectrum_legend)
        spectrum_layout.setContentsMargins(17, 9, 17, 9)
        spectrum_layout.setSpacing(7)
        self.analysis_spectrum_legend.setFixedHeight(48)
        self.analysis_spectrum_legend.hide()
        self._reg('analysisSpectrumLegend', self.analysis_spectrum_legend, False)
        panel.add_body(self.analysis_spectrum_legend, 0)
        return panel
    def _build_analysis_bottom(self):
        outer = QWidget()
        outer.setFixedHeight(240)
        outer.setObjectName('analysisBottom')
        layout = QHBoxLayout(outer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        table_panel = Panel('Fit parameters')
        table_panel.add_tool(QLabel('Value ± local SE'))
        self.fit_table = QTableWidget(0, 4)
        self.fit_table.setObjectName('fitParameters');self.fit_table.setShowGrid(False)
        self.fit_table.setHorizontalHeaderLabels(['Parameter', 'Value', 'Std. error', 'Unit'])
        self.fit_table.verticalHeader().setVisible(False)
        self.fit_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fit_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.fit_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fit_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self._reg('fitParameters', self.fit_table, False)
        table_panel.add_body(self.fit_table, 1)
        layout.addWidget(table_panel, 1)
        diagnostics = Panel('Fit diagnostics');diagnostics.setFixedWidth(250)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 8, 14, 12)
        body_layout.setSpacing(8)
        self.fit_sample_count = QLabel('No fit result')
        self.fit_sample_count.setObjectName('fitSampleCount')
        self.fit_rmse = QLabel('-')
        self.fit_rmse.setObjectName('fitRmse')
        self.fit_condition = QLabel('-')
        self.fit_condition.setObjectName('fitCondition')
        self._reg('fitSampleCount', self.fit_sample_count, False)
        self._reg('fitRmse', self.fit_rmse, False)
        self._reg('fitCondition', self.fit_condition, False)
        for label_text, value in [('Samples', self.fit_sample_count), ('RMSE / counts·s⁻¹', self.fit_rmse), ('Jacobian condition', self.fit_condition)]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            caption = QLabel(label_text)
            caption.setProperty('muted', True)
            row_layout.addWidget(caption)
            row_layout.addStretch(1)
            row_layout.addWidget(value)
            body_layout.addWidget(row)
        note = QLabel('Local Jacobian standard errors; not confidence intervals.')
        note.setWordWrap(True)
        note.setProperty('muted', True)
        body_layout.addWidget(note)
        self.fit_warning = QLabel('')
        self.fit_warning.setObjectName('fitWarning')
        self.fit_warning.setWordWrap(True)
        self.fit_warning.hide()
        self._reg('fitWarning', self.fit_warning, False)
        body_layout.addWidget(self.fit_warning)
        body_layout.addStretch(1)
        diagnostics.add_body(body, 1)
        layout.addWidget(diagnostics)
        return outer
    def _build_calibration_page(self):
        page = QWidget()
        page.setObjectName('calibrationPage')
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(280)
        scroll.setMaximumWidth(340)
        self.calibration_scroll=scroll
        inner = QFrame()
        inner.setObjectName('calibrationControls')
        add_outline(inner)
        controls = QVBoxLayout(inner)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(0)
        self._build_cal_ccd(controls)
        self._build_cal_extraction(controls)
        self._build_cal_reference(controls)
        self._build_cal_model(controls)
        controls.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll)
        results = QWidget()
        results.setObjectName('calResults')
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)
        results_layout.addWidget(self._build_cal_image_panel(), 1)
        results_layout.addWidget(self._build_cal_spectrum_panel(), 1)
        results_layout.addWidget(self._build_cal_lower(), 1)
        layout.addWidget(results, 1)
        self._reg('calibrationPage', page, False)
        return page
    def _build_cal_ccd(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(17,17,17,19)
        self.cal_source = QLabel('HARDWARE')
        self.cal_source.setObjectName('calSource')
        self.cal_source.setProperty('warning', True)
        self._reg('calSource', self.cal_source, False)
        layout.addWidget(self._heading('CCD acquisition', self.cal_source))
        grid = QWidget()
        grid_layout = QGridLayout(grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(12)
        grid_layout.setVerticalSpacing(8);grid_layout.setColumnStretch(0,1);grid_layout.setColumnStretch(1,1)
        self.cal_exposure = number_edit('1.0')
        self.cal_exposure.setObjectName('calExposure')
        self._reg('calExposure', self.cal_exposure, False)
        self.cal_readout = QComboBox()
        self.cal_readout.setMinimumWidth(110)
        self.cal_readout.setObjectName('calReadout')
        self.cal_readout.addItem('Full image', 'full_image')
        self.cal_readout.addItem('FVB', 'fvb')
        self.cal_readout.activated.connect(lambda index: self._cal_readout_changed())
        self._reg('calReadout', self.cal_readout, False)
        grid_layout.addWidget(field('Exposure', self.cal_exposure, 's'), 0, 0)
        grid_layout.addWidget(field('Readout', self.cal_readout), 0, 1)
        layout.addWidget(grid)
        dim_row = QWidget()
        dim_layout = QHBoxLayout(dim_row)
        dim_layout.setContentsMargins(0, 0, 0, 0)
        cap = QLabel('CCD dimensions')
        cap.setProperty('muted', True)
        self.cal_dimensions = QLabel('1024 × 255 px')
        self.cal_dimensions.setObjectName('calDimensions')
        self.cal_dimensions.setProperty('mono', True)
        self._reg('calDimensions', self.cal_dimensions, False)
        dim_layout.addWidget(cap)
        dim_layout.addStretch(1)
        dim_layout.addWidget(self.cal_dimensions)
        layout.addWidget(dim_row)
        self.cal_capture = QPushButton('Acquire frame')
        self.cal_capture.setObjectName('calCapture')
        self.cal_capture.setProperty('role', 'primary')
        self.cal_capture.clicked.connect(self.capture_calibration)
        self._reg('calCapture', self.cal_capture, False)
        self.cal_capture.setFixedHeight(36);self.cal_capture.setStyleSheet('min-height:34px;max-height:34px;')
        layout.addSpacing(9)
        layout.addWidget(self.cal_capture)
        self.cal_connection_note = QLabel('Connect a CCD to acquire a frame.')
        self.cal_connection_note.setObjectName('calConnectionNote');self.cal_connection_note.setMinimumHeight(18)
        self.cal_connection_note.setWordWrap(True)
        self.cal_connection_note.setProperty('muted', True)
        self._reg('calConnectionNote', self.cal_connection_note, False)
        layout.addWidget(self.cal_connection_note)
        parent_layout.addWidget(frame)
    def _build_cal_extraction(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(17,17,17,20)
        layout.addWidget(self._heading('Spectrum extraction'))
        grid = QWidget()
        grid_layout = QGridLayout(grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(12)
        grid_layout.setVerticalSpacing(8)
        self.cal_row_start = QSpinBox()
        self.cal_row_start.setObjectName('calRowStart')
        self.cal_row_start.setRange(0, 254);self.cal_row_start.setFixedHeight(32);self.cal_row_start.setButtonSymbols(QSpinBox.NoButtons)
        self.cal_row_start.setValue(85)
        self.cal_row_start.valueChanged.connect(lambda value: self._cal_roi_spin_changed())
        self._reg('calRowStart', self.cal_row_start, False)
        self.cal_row_end = QSpinBox()
        self.cal_row_end.setObjectName('calRowEnd')
        self.cal_row_end.setRange(0, 254);self.cal_row_end.setFixedHeight(32);self.cal_row_end.setButtonSymbols(QSpinBox.NoButtons)
        self.cal_row_end.setValue(165)
        self.cal_row_end.valueChanged.connect(lambda value: self._cal_roi_spin_changed())
        self._reg('calRowEnd', self.cal_row_end, False)
        grid_layout.addWidget(field('First row', self.cal_row_start), 0, 0)
        grid_layout.addWidget(field('Last row', self.cal_row_end), 0, 1)
        layout.addWidget(grid)
        self.cal_extraction_note = QLabel('Mean of selected rows · Click the CCD image to move the band.')
        self.cal_extraction_note.setObjectName('calExtractionNote')
        self.cal_extraction_note.setWordWrap(True);self.cal_extraction_note.setFixedHeight(36)
        self.cal_extraction_note.setProperty('muted', True)
        self._reg('calExtractionNote', self.cal_extraction_note, False)
        layout.addWidget(self.cal_extraction_note)
        self.cal_find_peaks = QPushButton('Find peaks')
        self.cal_find_peaks.setObjectName('calFindPeaks')
        self.cal_find_peaks.clicked.connect(self.extract_calibration)
        self._reg('calFindPeaks', self.cal_find_peaks, False)
        self.cal_find_peaks.setFixedHeight(36);self.cal_find_peaks.setStyleSheet('min-height:34px;max-height:34px;')
        layout.addWidget(self.cal_find_peaks)
        parent_layout.addWidget(frame)
    def _build_cal_reference(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(17,17,17,17)
        layout.addWidget(self._heading('Reference spectrum'))
        self.cal_reference_name = QLabel('Illustrative demo lines')
        self.cal_reference_name.setObjectName('calReferenceName')
        self.cal_reference_name.setWordWrap(True)
        self.cal_reference_name.setProperty('mono', True)
        self._reg('calReferenceName', self.cal_reference_name, False)
        self.cal_reference_name.setStyleSheet('font:12px Consolas;');self.cal_reference_name.setFixedHeight(18)
        layout.addWidget(self.cal_reference_name)
        self.cal_reference_file = QPushButton('Import reference CSV')
        self.cal_reference_file.setObjectName('calReferenceFile')
        self.cal_reference_file.clicked.connect(self._import_reference_csv)
        self._reg('calReferenceFile', self.cal_reference_file, False)
        layout.addSpacing(3)
        self.cal_reference_file.setFixedHeight(34);self.cal_reference_file.setStyleSheet('min-height:32px;max-height:32px;');layout.addWidget(self.cal_reference_file)
        demo_row = QWidget()
        demo_layout = QHBoxLayout(demo_row)
        demo_layout.setContentsMargins(0, 0, 0, 0)
        demo_layout.setSpacing(8)
        caption = QLabel('Demo reference')
        caption.setProperty('muted', True)
        self.cal_demo_reference = QPushButton('Reset')
        self.cal_demo_reference.setObjectName('calDemoReference')
        self.cal_demo_reference.clicked.connect(self._reset_demo_reference)
        self._reg('calDemoReference', self.cal_demo_reference, False)
        demo_layout.addWidget(caption)
        demo_layout.addStretch(1)
        demo_layout.addWidget(self.cal_demo_reference)
        layout.addSpacing(4)
        self.cal_demo_reference.setFixedSize(47,32);self.cal_demo_reference.setStyleSheet('padding:0 8px;');layout.addWidget(demo_row)
        conv_label = QLabel('Wavelength convention')
        conv_label.setProperty('muted', True)
        layout.addWidget(conv_label)
        self.cal_convention = QComboBox()
        self.cal_convention.setObjectName('calConvention')
        self.cal_convention.addItem('Air wavelength', 'air')
        self.cal_convention.addItem('Vacuum wavelength', 'vacuum')
        self._reg('calConvention', self.cal_convention, False)
        layout.addWidget(self.cal_convention)
        note = QLabel('wavelength_nm,relative_intensity. Intensity is optional. No air/vacuum conversion is performed.')
        note.setWordWrap(True)
        note.setProperty('muted', True)
        format_section=CollapsibleSection('Reference format');format_section.add(note);layout.addWidget(format_section)
        parent_layout.addWidget(frame)
    def _build_cal_model(self, parent_layout):
        frame, layout = self._section()
        layout.setContentsMargins(17,17,17,17)
        self.cal_fit_state = QLabel('Draft')
        self.cal_fit_state.setObjectName('calFitState')
        self._reg('calFitState', self.cal_fit_state, False)
        layout.addWidget(self._heading('Calibration model', self.cal_fit_state))
        order_label = QLabel('Polynomial order')
        order_label.setProperty('muted', True)
        layout.addWidget(order_label)
        self.cal_order = QComboBox()
        self.cal_order.setObjectName('calOrder')
        self.cal_order.addItem('Linear · 1st order', 1)
        self.cal_order.addItem('Quadratic · 2nd order', 2)
        self.cal_order.addItem('Cubic · 3rd order', 3)
        self.cal_order.setCurrentIndex(1)
        self._reg('calOrder', self.cal_order, False)
        layout.addWidget(self.cal_order)
        self.cal_fit_mapping = QPushButton('Fit pixel → wavelength')
        self.cal_fit_mapping.setObjectName('calFitMapping')
        self.cal_fit_mapping.setProperty('role', 'primary')
        self.cal_fit_mapping.clicked.connect(self.fit_calibration)
        self._reg('calFitMapping', self.cal_fit_mapping, False)
        self.cal_fit_mapping.setFixedHeight(36)
        layout.addWidget(self.cal_fit_mapping)
        self.cal_feedback = QLabel('')
        self.cal_feedback.setObjectName('calFeedback')
        self.cal_feedback.setWordWrap(True)
        self._reg('calFeedback', self.cal_feedback, False)
        layout.addWidget(self.cal_feedback)
        self.cal_export = QPushButton('Export calibration JSON')
        self.cal_export.setObjectName('calExport')
        self.cal_export.setEnabled(False)
        self.cal_export.clicked.connect(self._export_calibration_json)
        self._reg('calExport', self.cal_export, False)
        self.cal_export.setFixedHeight(36)
        layout.addWidget(self.cal_export)
        self.cal_apply = QPushButton('Apply to acquisition')
        self.cal_apply.setObjectName('calApply')
        self.cal_apply.setEnabled(False)
        self.cal_apply.clicked.connect(self.apply_calibration)
        self._reg('calApply', self.cal_apply, False)
        self.cal_apply.setFixedHeight(36)
        layout.addWidget(self.cal_apply)
        note = QLabel('Apply updates future acquisitions; existing experiments keep their recorded calibration.')
        note.setWordWrap(True)
        note.setProperty('muted', True)
        layout.addWidget(note)
        details = CollapsibleSection('Installed calibration')
        self.cal_open_file = QPushButton('Open file…')
        self.cal_open_file.setObjectName('calOpenFile')
        self.cal_open_file.clicked.connect(self._open_calibration_json)
        self._reg('calOpenFile', self.cal_open_file, False)
        details.add(self.cal_open_file)
        self.cal_installed_file = QLineEdit('')
        self.cal_installed_file.setObjectName('calInstalledFile')
        self.cal_installed_file.hide()
        self._reg('calInstalledFile', self.cal_installed_file, False)
        details.add(self.cal_installed_file)
        self.calibration_state = QLabel('Waiting for backend calibration')
        self.calibration_state.setObjectName('calibrationState')
        self.calibration_state.setWordWrap(True)
        self.calibration_state.setProperty('muted', True)
        self._reg('calibrationState', self.calibration_state, False)
        details.add(self.calibration_state)
        layout.addWidget(details)
        parent_layout.addWidget(frame)
    def _build_cal_image_panel(self):
        panel = Panel('CCD frame / pixel')
        panel.header.setFixedHeight(52)
        self.cal_roi_label = QLabel('')
        self.cal_roi_label.setObjectName('calRoiLabel')
        self.cal_roi_label.hide()
        self._reg('calRoiLabel', self.cal_roi_label, False)
        panel.title_layout.addWidget(self.cal_roi_label)
        self.cal_frame_state = QLabel('No frame')
        self.cal_frame_state.setObjectName('calFrameState')
        self._reg('calFrameState', self.cal_frame_state, False)
        panel.add_tool(self.cal_frame_state)
        self.cal_image = CalibrationImagePlot()
        self.cal_image.setObjectName('calImage')
        self._reg('calImage', self.cal_image, False)
        self.cal_image.roiChanged.connect(self._cal_roi_changed)
        self.cal_image.rowPicked.connect(self._cal_image_row_clicked)
        panel.add_body(self.cal_image, 1)
        return panel
    def _build_cal_spectrum_panel(self):
        panel = Panel('Extracted spectrum /')
        panel.header.setFixedHeight(52)
        self.cal_spectrum_axis = QLabel('pixel')
        self.cal_spectrum_axis.setObjectName('calSpectrumAxis')
        self._reg('calSpectrumAxis', self.cal_spectrum_axis, False)
        # Read as one title, "Extracted spectrum / pixel", with the switchable unit muted.
        self.cal_spectrum_axis.setProperty('h2', True)
        self.cal_spectrum_axis.setProperty('muted', True)
        panel.title_layout.setSpacing(5)
        panel.title_layout.addWidget(self.cal_spectrum_axis)
        legend = Legend()
        legend.add('CCD')
        legend.add('Reference lines', dashed=True)
        panel.add_tool(legend)
        self.cal_axis = Segmented([('Pixel', 'pixel'), ('nm', 'wavelength')])
        self.cal_axis.set_selected('pixel')
        self.cal_axis.changed.connect(lambda value: self._update_cal_plots())
        self._reg('calAxis', self.cal_axis, False)
        panel.add_tool(self.cal_axis)
        self.cal_nm_axis = self.cal_axis.button('wavelength')
        if self.cal_nm_axis is not None:
            self.cal_nm_axis.setObjectName('calNmAxis')
            self.cal_nm_axis.setEnabled(False)
            self._reg('calNmAxis', self.cal_nm_axis, False)
        self.cal_spectrum = CalibrationProfilePlot()
        self.cal_spectrum.setObjectName('calSpectrum')
        self._reg('calSpectrum', self.cal_spectrum, False)
        self.cal_spectrum.pixelPicked.connect(self._cal_pixel_picked)
        panel.add_body(self.cal_spectrum, 1)
        return panel
    def _build_cal_lower(self):
        outer = QWidget()
        self.cal_lower=outer
        outer.setFixedHeight(240)
        outer.setObjectName('calLower')
        layout = QHBoxLayout(outer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        table_panel = Panel('Line assignments');table_panel.header.setFixedHeight(46)
        self.cal_line_count = QLabel('0 enabled lines')
        self.cal_line_count.setObjectName('calLineCount')
        self.cal_line_count.setProperty('muted', True)
        self._reg('calLineCount', self.cal_line_count, False)
        table_panel.add_tool(self.cal_line_count)
        self.cal_add_line = QPushButton('＋ Add')
        self.cal_add_line.setObjectName('calAddLine')
        self.cal_add_line.clicked.connect(self._cal_add_line)
        self._reg('calAddLine', self.cal_add_line, False)
        table_panel.add_tool(self.cal_add_line)
        self.cal_table = QTableWidget(0, 5)
        self.cal_table.setObjectName('calPairs');self.cal_table.setShowGrid(False)
        self.cal_table.setHorizontalHeaderLabels(['', 'Peak / px', 'Reference / nm', 'Δ / nm', ''])
        self.cal_table.verticalHeader().setVisible(False)
        self.cal_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cal_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cal_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cal_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.cal_table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        for column,size in ((0,30),(4,36)):
            self.cal_table.horizontalHeader().setSectionResizeMode(column,QHeaderView.Fixed);self.cal_table.setColumnWidth(column,size)
        self.cal_table.horizontalHeader().setMinimumSectionSize(20);self.cal_table.horizontalHeader().setFixedHeight(34);self.cal_table.verticalHeader().setDefaultSectionSize(37)
        self.cal_table.itemSelectionChanged.connect(self._cal_selection_changed)
        self.cal_table.cellClicked.connect(lambda row, column: self.cal_table.selectRow(row))
        self._reg('calPairs', self.cal_table, False)
        table_panel.add_body(self.cal_table, 1)
        note=QLabel('Δ = reference − model · Demo lines are illustrative.');note.setProperty('muted',True);note.setFixedHeight(28);note.setContentsMargins(12,0,8,0);note.setStyleSheet('font-size:11px;');table_panel.add_body(note)
        layout.addWidget(table_panel, 19)
        mapping_panel = Panel('Pixel → wavelength');mapping_panel.header.setFixedHeight(46)
        mapping_panel.add_tool(QLabel('Mapping & residuals'))
        self.cal_mapping_holder = QWidget()
        self.cal_mapping_holder.setObjectName('calMappingPlot')
        holder_layout = QVBoxLayout(self.cal_mapping_holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(0)
        self._reg('calMappingPlot', self.cal_mapping_holder, False)
        self.cal_mapping = CalibrationMappingPlot()
        self.cal_mapping.setObjectName('calMapping')
        self._reg('calMapping', self.cal_mapping, False)
        holder_layout.addWidget(self.cal_mapping, 1)
        self.cal_metrics = MetricsOverlay(self.cal_mapping)
        self._reg('calMetrics', self.cal_metrics, False)
        self.cal_residual = CalibrationResidualPlot()
        self.cal_residual.link_axis('x',self.cal_mapping)
        self.cal_residual.set_x_label('')
        self.cal_residual.bottom_axis.setStyle(showValues=False)
        self.cal_residual.setObjectName('calResidual')
        self._reg('calResidual', self.cal_residual, False)
        self.cal_residual.setFixedHeight(57)
        holder_layout.addWidget(self.cal_residual)
        mapping_panel.add_body(self.cal_mapping_holder, 1)
        layout.addWidget(mapping_panel, 21)
        return outer
    def _connect_controller(self):
        self.cal_order.currentIndexChanged.connect(self._invalidate_cal_solution)
        self.cal_convention.currentIndexChanged.connect(self._invalidate_cal_solution)
        self.fit_model.currentIndexChanged.connect(self._analysis_options_changed)
        for edit in (self.irf_width,self.fit_start,self.fit_end):
            edit.editingFinished.connect(self._analysis_options_changed)
        self.controller.snapshotChanged.connect(self._on_snapshot)
        self.controller.stateChanged.connect(self._on_state)
        self.controller.dataChanged.connect(self._on_data)
        self.controller.analysisChanged.connect(self._on_analysis)
        self.controller.error.connect(self._on_error)
        self.controller.readyChanged.connect(self._on_ready)
        if hasattr(self.controller, 'fitReady'):
            self.controller.fitReady.connect(self._on_fit_ready)
        if hasattr(self.controller, 'fitFailed'):
            self.controller.fitFailed.connect(self._on_fit_failed)
        if hasattr(self.controller, 'fitStarted'):
            self.controller.fitStarted.connect(self._on_fit_started)
        if hasattr(self.controller, 'closingFinished'):
            self.controller.closingFinished.connect(self._on_closing_finished)
    def start_backend(self):
        if self.controller is not None:
            self.controller.start()
    def _on_ready(self, ready):
        if ready:
            self.startup.set_ready()
        else:
            if not self.startup.isVisible():
                self.startup.show()
            self.startup.show_message('Waiting for the local acquisition service. Your workbench will be ready shortly.', False)
    def _retry_startup(self):
        self.startup.show_message('Retrying connection…', False)
        try:
            if hasattr(self.controller, 'retry'):
                self.controller.retry()
            else:
                self.controller.request_status()
        except Exception as error:
            self.startup.show_message(str(error), True)
    def _on_error(self, message):
        self.show_toast(str(message))
        if not getattr(self.controller, 'online', False):
            self.startup.show_message(str(message), True)
    def _on_closing_finished(self):
        self._allow_close = True
    def show_toast(self, message):
        self.toast.setText(str(message))
        self.toast.adjustSize()
        width = min(max(260, self.toast.width()), max(320, self.width() - 80))
        self.toast.setFixedWidth(width)
        self.toast.adjustSize()
        self.toast.move(max(10, (self.width() - self.toast.width()) // 2), max(10, self.height() - self.toast.height() - 24))
        self.toast.show()
        self.toast.raise_()
        self._toast_timer.start(4200)
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_visual_geometry()
        if hasattr(self, 'startup'):
            self.startup.setGeometry(self.rect())
        if hasattr(self, 'cal_metrics'):
            self.cal_metrics.place()
        if hasattr(self, 'toast') and self.toast.isVisible():
            self.toast.move(max(10, (self.width() - self.toast.width()) // 2), max(10, self.height() - self.toast.height() - 24))
    def _apply_visual_geometry(self):
        width=self.width();left=round(max(272,min(340,width*.2125)))
        for name in ('acquisition_scroll','analysis_scroll','calibration_scroll'):
            scroll=getattr(self,name,None)
            if scroll is not None:scroll.setFixedWidth(260 if name=='acquisition_scroll' and 1000<=width<=1150 else left)
        summary=round(max(190,min(240,self.height()*.27)))
        if hasattr(self,'analysis_bottom'):self.analysis_bottom.setFixedHeight(summary)
        if hasattr(self,'cal_lower'):self.cal_lower.setFixedHeight(summary)
        if hasattr(self,'map_color_key'):
            # Right end of the colour key lines up with the right edge of the map image.
            panel=self.widgets['mapPanel'];self.map_color_key.adjustSize()
            edge=self.map_plot.viewport().mapToGlobal(QPoint(self.map_plot._view_rect().right()+1,0))
            right=panel.header.mapFromGlobal(edge).x()
            self.map_color_key.move(right-self.map_color_key.width(),14)
    def _schedule_refresh(self):
        if not self._map_timer.isActive():
            self._map_timer.start()
        if not self._label_timer.isActive():
            self._label_timer.start()
    def _on_snapshot(self, snapshot):
        if not isinstance(snapshot, dict):
            return
        self._snapshot = dict(snapshot)
        self.export_button.setEnabled(bool(self.controller.acquisition.records))
        self._update_from_snapshot()
        self._schedule_refresh()
    def _on_state(self):
        self._update_from_snapshot()
        self._schedule_refresh()
    def _on_data(self):
        self._schedule_refresh()
        self._analysis_dirty=True
        self._update_analysis_labels()
        self._update_cal_controls()
    def _on_analysis(self):
        self._update_analysis_labels()
        self._redraw_analysis()
    def _on_fit_started(self, scope):
        if scope == 'analysis':
            self._fit_running = True
            self.fit_status.setText('Fitting…')
            self.analysis_fit_state.setText('Fitting')
            self.analysis_fit_state.setProperty('data-state', 'warning')
            set_prop(self.analysis_fit_state, 'data-state', 'warning')
            self.run_fit_button.setEnabled(False)
    def _on_fit_ready(self, scope, result):
        if scope == 'analysis':
            self._fit_running = False
            self.fit_status.setText('Fit complete')
            self._update_analysis_labels()
            self._redraw_analysis()
            self.export_fit.setEnabled(True)
            self.show_toast('Fit complete')
    def _on_fit_failed(self, scope, message):
        if scope == 'analysis':
            self._fit_running = False
            self.fit_status.setText(str(message))
            self.analysis_fit_state.setText('Error')
            set_prop(self.analysis_fit_state, 'data-state', 'error')
            self.run_fit_button.setEnabled(True)
            self.show_toast(str(message))
    def _update_from_snapshot(self):
        snapshot = self._snapshot or {}
        self._updating_controls = True
        try:
            source_mode = str(snapshot.get('source_mode') or 'hardware')
            self._selected_source = source_mode
            devices = snapshot.get('devices') or {}
            stage_connected = bool((devices.get('stage') or {}).get('connected'))
            camera_connected = bool((devices.get('camera') or {}).get('connected'))
            connected_count = int(stage_connected) + int(camera_connected)
            mode_label = source_mode.upper()
            self.hardware_mode.setItemText(0, f'HARDWARE · {connected_count}/2 connected')
            index = self.hardware_mode.findData(source_mode)
            if index >= 0 and index != self.hardware_mode.currentIndex():
                self.hardware_mode.setCurrentIndex(index)
            self.stage_connection.setText('● Simulated ▾' if source_mode=='simulation' else '● C-663.12 ▾' if stage_connected else '○ Connect…')
            detector_text = '● Simulated ▾' if source_mode=='simulation' else 'CCD on · Manual ▾' if camera_connected else 'CCD off · Manual ▾'
            self.detector_connection.setText(detector_text)
            stage = snapshot.get('stage') or {}
            delay = stage.get('delay_ps')
            if delay is None:
                delay = stage.get('controller_reported_delay_ps')
            if delay is not None and not self.delay_edit.hasFocus():
                self.delay_edit.setText(f'{float(delay):.3f}')
            moving = bool(stage.get('moving'))
            if moving:
                self.stage_status.setText('● Moving…')
                self.stage_status.setStyleSheet('color:'+palette()['warning']+';')
            elif stage_connected:
                self.stage_status.setText('<span style="color:'+palette()['success']+'">●</span> In position')
                self.stage_status.setStyleSheet('color:'+palette()['muted']+';')
            else:
                self.stage_status.setText('○ Stage disconnected')
                self.stage_status.setStyleSheet('color:'+palette()['muted']+';')
            camera = snapshot.get('camera') or {}
            exposure = camera.get('exposure_s')
            if exposure is not None and not self.exposure_edit.hasFocus():
                self.exposure_edit.setText(f'{float(exposure):g}')
            spectrograph = snapshot.get('spectrograph') or {}
            if spectrograph.get('center_nm') is not None and not self.center_edit.hasFocus():
                self.center_edit.setText(f'{float(spectrograph.get("center_nm")):.6g}')
            if spectrograph.get('grating') is not None and not self.grating_combo.hasFocus():
                _set_combo(self.grating_combo, str(int(spectrograph.get('grating'))))
            if spectrograph.get('slit_um') is not None and not self.slit_edit.hasFocus():
                self.slit_edit.setText(f'{float(spectrograph.get("slit_um")):.6g}')
            if self._cal_signature is None:
                self._cal_signature = self._calibration_signature(snapshot)
            else:
                signature = self._calibration_signature(snapshot)
                if signature != self._cal_signature:
                    self._cal_signature = signature
                    self._clear_calibration_frame(clear_pixels=True)
            if source_mode == 'hardware' and self._reference_source != 'measured':
                if self._cal_lines:
                    self._cal_lines = []
                    self._reference_name = 'Import measured reference wavelengths'
                    self.cal_reference_name.setText(self._reference_name)
                    self._repopulate_cal_table()
            elif source_mode == 'simulation' and not self._cal_lines:
                self._load_demo_reference(silent=True)
            availability = device_availability(snapshot, getattr(self.controller, 'online', True))
            busy = snapshot.get('busy')
            self.stage_fields.setEnabled(bool(getattr(self.controller, 'online', False) and (stage_connected or availability.get('stage'))) and busy not in ('scan', 'move', 'calibration'))
            camera_ready = bool(getattr(self.controller, 'online', False) and (camera_connected or availability.get('camera')))
            self.exposure_edit.setEnabled(camera_ready and busy in (None, 'preview'))
            spectrograph_ready = bool(getattr(self.controller, 'online', False) and (availability.get('spectrograph') or (devices.get('spectrometer') or {}).get('connected')))
            controls = availability.get('spectralControls') or []
            self.center_edit.setEnabled(spectrograph_ready and (not controls or 'center_nm' in controls) and busy in (None, 'preview'))
            self.grating_combo.setEnabled(spectrograph_ready and (not controls or 'grating' in controls) and busy in (None, 'preview'))
            self.slit_edit.setEnabled(spectrograph_ready and (not controls or 'slit_um' in controls) and busy in (None, 'preview'))
            locked_tip = '' if spectrograph_ready else 'Select a spectrometer from the Detection menu to edit'
            for control in (self.center_edit, self.grating_combo, self.slit_edit):
                control.setToolTip('' if control.isEnabled() else (locked_tip or 'Locked while the devices are busy'))
            self.preview_button.setEnabled(bool(getattr(self.controller, 'online', False) and availability.get('preview') and busy not in ('scan', 'move', 'calibration')))
            if busy == 'preview':
                self.preview_button.setEnabled(True)
                self.preview_button.setText('Stop preview')
            else:
                self.preview_button.setText('Live preview')
            self.capture_background.setEnabled(bool(getattr(self.controller, 'online', False) and availability.get('background') and available_background(busy)))
            self._update_header(snapshot)
            self._update_cal_controls()
            self._update_output_status()
        finally:
            self._updating_controls = False
    def _calibration_signature(self, snapshot):
        try:
            return (snapshot.get('worker_session_id'), snapshot.get('device_generation'), snapshot.get('source_mode'), json.dumps(snapshot.get('spectrograph') or {}, sort_keys=True))
        except Exception:
            return (snapshot.get('worker_session_id'), snapshot.get('device_generation'), snapshot.get('source_mode'))
    def _update_header(self, snapshot):
        online = bool(getattr(self.controller, 'online', False))
        busy = snapshot.get('busy')
        run = snapshot.get('run') or {}
        status = str(run.get('status') or '')
        total = None
        completed = None
        plan = run.get('plan') or {}
        if plan:
            total = plan.get('total_points')
        if run.get('completed_points') is not None:
            completed = run.get('completed_points')
        if busy == 'scan':
            if status in ('paused', 'pausing'):
                self.run_status.setText('● Paused')
                self._set_run_button('play', 'Resume')
            elif status in ('stopping', 'finalizing'):
                self.run_status.setText('● Stopping')
                self._set_run_button('play', 'Start')
            else:
                self.run_status.setText('● Scanning')
                self._set_run_button('pause', 'Pause')
        elif busy == 'preview':
            self.run_status.setText('● Live preview')
            self._set_run_button('play', 'Start')
        elif busy == 'calibration':
            self.run_status.setText('● Calibration')
            self._set_run_button('play', 'Start')
        elif busy == 'moving':
            self.run_status.setText('● Moving')
            self._set_run_button('play', 'Start')
        elif busy:
            self.run_status.setText(f'● {str(busy).capitalize()}')
            self._set_run_button('play', 'Start')
        else:
            self.run_status.setText(status.capitalize() if status else 'Ready' if online else 'Starting…')
            self._set_run_button('play', 'Start')
        if total and completed is not None:
            total_value = int(total)
            completed_value = int(completed)
            percent = int(round(100.0 * completed_value / total_value)) if total_value else 0
            self.progress_fill.setValue(max(0, min(100, percent)))
            self.progress_text.setText(f'{completed_value} / {total_value} points · {run.get("committed_frames",0)} frames')
            self.remaining.setText(str(run.get('step') if busy=='scan' else status))
        else:
            self.progress_fill.setValue(0)
            if busy == 'preview':
                self.progress_text.setText('Live preview')
            elif busy == 'calibration':
                self.progress_text.setText('Calibration acquisition')
            elif busy:
                self.progress_text.setText(str(busy).capitalize())
            else:
                self.progress_text.setText('Ready to start') if not getattr(self,'_plan_display_key',None) else None
            if online:
                self._update_point_estimate()
            else:
                self.remaining.setText('111 points')
        can_start = bool(online and not busy and (snapshot.get('available') or {}).get('scan'))
        if busy == 'scan':
            can_start = status in ('running', 'paused', 'pausing')
        locked = self._current_page == 'calibration' and not busy
        if locked:can_start=False
        self.progress_fill.setEnabled(not locked)
        set_prop(self.progress_fill, 'locked', locked)
        self.run_button.setEnabled(can_start)
        self.stop_button.setEnabled(bool(online and busy))
        self._refresh_run_icons()
        if not online:
            self.run_status.setText('● Starting…')
    def _update_point_estimate(self):
        self._mark_segment_rows()
        try:
            values = []
            for row in self._segment_rows:
                start = number_value(row['start'])
                end = number_value(row['end'])
                step = number_value(row['step'])
                if start is None or end is None or step is None or step <= 0 or end < start:
                    raise ValueError
                count = int(math.floor((end - start) / step + 1e-9)) + 1
                for index in range(max(0, count)):
                    values.append(round(start + index * step, 9))
            unique = len(set(values))
            repeats = int(self.repeats_input.value()) if self._mode == 'repeat' else 1
            self.remaining.setText(f'{unique * repeats} points')
            if hasattr(self,'save_path') and self.controller.online:
                request=self.collect_run_request();identity=dict(request);identity.pop('request_id',None)
                key=(self._selected_source,json.dumps(identity,sort_keys=True))
                if key!=getattr(self,'_plan_display_key',None):
                    self._plan_display_key=key
                    def planned(plan):
                        if key!=self._plan_display_key or self.controller.acquisition.run:return
                        if not plan.get('delays_ps'):return
                        self._last_plan=plan
                        self.progress_text.setText(f"{len(plan['delays_ps'])} points / scan · {plan['rounds']} scans")
                        duration="Estimated "+format_duration(plan['estimated_seconds'])
                        self.remaining.setText(duration+f" / ×{request['speedup']:g} simulation" if self._selected_source=='simulation' else duration)
                    self.controller.dispatch('plan',request,success=planned,failure=lambda error:None)
                elif hasattr(self,'_last_plan'):
                    plan=self._last_plan
                    self.progress_text.setText(f"{len(plan['delays_ps'])} points / scan · {plan['rounds']} scans")
                    duration="Estimated "+format_duration(plan['estimated_seconds'])
                    self.remaining.setText(duration+f" / ×{request['speedup']:g} simulation" if self._selected_source=='simulation' else duration)
        except Exception:
            self.remaining.setText('- points')
    def _flush_labels(self):
        if self._current_page == 'acquisition':
            self._update_live_spectrum()
            self._update_header(self._snapshot or {})
        if self._current_page == 'analysis':
            self._update_analysis_labels()
            if getattr(self,'_analysis_dirty',True):
                self._redraw_analysis();self._analysis_dirty=False
        self._update_output_status()
        self._apply_visual_geometry()
    def _flush_map(self):
        if self._current_page != 'acquisition':
            return
        dataset = self.controller.acquisition
        scale = self.map_scale.current()
        key = (id(dataset), dataset.revision, current_mode(), scale, dataset.wave, dataset.half_window)
        if not self._map_dirty and key == self._map_key:
            return
        if getattr(self,'_map_dataset',None) is not dataset:
            self._map_dataset=dataset;self._map_spectra_cache={}
        for delay,group in dataset.groups.items():
            cached=self._map_spectra_cache.get(delay)
            if cached is None or cached[0]!=group.count:
                self._map_spectra_cache[delay]=(group.count,group.raw)
        spectra={delay:entry[1] for delay,entry in self._map_spectra_cache.items()}
        saturated={delay:group.saturated for delay,group in dataset.groups.items() if group.saturated.any()}
        planned=(dataset.run or {}).get('plan',{}).get('delays_ps',[-2,20])
        vmin, vmax = self.map_plot.set_map(dataset.axis, spectra, scale, planned, saturated, self._map_levels)
        self.map_saturated_key.setVisible(bool(saturated))
        self.color_min.set_value_text(_format_map_value(vmin))
        self.color_max.set_value_text(_format_map_value(vmax))
        self._apply_visual_geometry()  # key width follows the value texts; keep its right edge on the map edge
        if self._selected_delay is None and dataset.delays:
            self._selected_delay = dataset.delays[0]
        delays, intensities = self._raw_decay_points(dataset)
        self.decay_plot.set_decay(delays, intensities, 'linear')
        self.map_plot.set_marker(self._selected_delay, dataset.wave if dataset.records else None, show_delay=self._picked_spectrum_active())
        self.decay_plot.set_marker(self._selected_delay)
        self._map_key = key
        self._map_dirty = False
    def _update_live_spectrum(self):
        dataset = self.controller.acquisition
        snapshot = self._snapshot or {}
        latest = snapshot.get('latest') or dataset.latest_record
        current = None
        saturated = 0
        if latest is not None:
            current = np.asarray(latest.get('spectrum') or [], dtype=float)
            saturated = int(latest.get('saturated_pixels') or 0)
        else:
            snapshot_latest = snapshot.get('latest') or {}
            if snapshot_latest.get('spectrum'):
                current = np.asarray(snapshot_latest.get('spectrum'), dtype=float)
            saturated = int(snapshot_latest.get('saturated_pixels') or 0)
        picked = self._picked_spectrum_active()
        if picked:
            current = np.asarray(dataset.groups[self._picked_delay].raw, dtype=float)
            saturated = int(np.count_nonzero(dataset.groups[self._picked_delay].saturated))
        legend_text = f'Δt = {fmt(self._picked_delay, 4)} ps' if picked else 'Current'
        if self._spectrum_legend_current.text() != legend_text:
            self._spectrum_legend_current.setText(legend_text)
        axis = dataset.axis or snapshot.get('live_axis')
        if axis and len(axis):
            x = np.asarray(axis[:len(current)] if current is not None else axis, dtype=float)
            x_label = 'Wavelength / nm'
        else:
            x = np.arange(0, len(current) if current is not None else 1024, dtype=float)
            x_label = 'Pixel'
        background = snapshot.get('background') or {}
        bg = background.get('spectrum')
        bg_array = np.asarray(bg, dtype=float) if bg else None
        self.spectrum_plot.set_spectrum(x, current, bg_array, x_label, self.spectrum_scale.current())
        self.spectrum_plot.set_axis_bounds('x',float(np.min(x)),float(np.max(x)))
        full_scale=float(snapshot.get('camera',{}).get('full_scale',65535));logarithmic=self.spectrum_scale.current()=='log'
        self.spectrum_plot.set_axis_bounds('y',0,math.log10(full_scale)*1.05 if logarithmic else full_scale*1.05)
        self.spectrum_plot.set_y_label('CCD / counts')  # log axes label decades in counts
        self.spectrum_plot.set_marker(dataset.wave,dataset.half_window,visible=bool(axis) and current is not None)
        if saturated > 0:
            self.saturation.setText(f'⚠ {saturated} saturated pixels')
            self.saturation.show()
        else:
            self.saturation.hide()
    def _raw_decay_points(self, dataset):
        axis = dataset.axis
        wave = dataset.wave
        half = dataset.half_window
        indices = None
        if axis:
            indices = [index for index, value in enumerate(axis) if abs(value - wave) <= half]
        delays = []
        intensities = []
        for delay in dataset.delays:
            group = dataset.groups.get(delay)
            if group is None:
                continue
            values = group.raw
            if indices:
                valid = [index for index in indices if index < values.size]
                if valid:
                    intensities.append(float(np.mean(values[valid])))
                else:
                    intensities.append(float(np.mean(values)))
            else:
                intensities.append(float(np.mean(values)))
            delays.append(float(delay))
        return delays, intensities
    def _picked_spectrum_active(self):
        # After a scan (or while idle) a delay picked on the map / decay replaces the live trace.
        busy = (self._snapshot or {}).get('busy')
        dataset = self.controller.acquisition
        return self._picked_delay is not None and busy not in ('scan', 'preview') and dataset.groups.get(self._picked_delay) is not None
    def _map_delay_picked(self, delay):
        self._selected_delay = float(delay)
        dataset = self.controller.acquisition
        if dataset.delays:
            self._selected_delay = min(dataset.delays, key=lambda value: abs(value - float(delay)))
            self._picked_delay = self._selected_delay
        active = self._picked_spectrum_active()
        self.map_plot.set_marker(self._selected_delay, dataset.wave, show_delay=active)
        self.decay_plot.set_marker(self._selected_delay)
        if active:
            self._update_live_spectrum()
        if self._current_page == 'analysis':
            self.analysis_delay.setCurrentIndex(self.analysis_delay.findData(self._selected_delay))
    def _map_wave_picked(self, wave):
        dataset = self.controller.acquisition
        dataset.wave = float(wave)
        self.map_plot.set_marker(self._selected_delay, dataset.wave)
        self.spectrum_plot.set_marker(dataset.wave,dataset.half_window,visible=bool(dataset.axis or self._snapshot.get('live_axis')))
        self._map_dirty = True
        self._schedule_refresh()
    def _color_range_edited(self, end, text):
        edit = self.color_max if end else self.color_min
        try:
            value = parse_count(text)
        except ValueError:
            edit.reject(); return
        levels = list(self._map_levels or (None, None))
        levels[end] = value
        auto = self.map_plot.auto_levels()
        low = levels[0] if levels[0] is not None else auto[0]
        high = levels[1] if levels[1] is not None else auto[1]
        if self.map_scale.current() == 'log':
            low = max(low, 1.0)
        if not high > low:
            edit.reject(); return
        self._map_levels = None if levels == [None, None] else tuple(levels)
        edit.accept()
        self.map_levels_auto.setVisible(self._map_levels is not None)
        self._map_dirty = True
        self._schedule_refresh()
    def _color_range_auto(self):
        self._map_levels = None
        self.map_levels_auto.hide()
        self._map_dirty = True
        self._schedule_refresh()
    def _scale_changed(self, value):
        self._map_dirty = True
        self._schedule_refresh()
    def _update_analysis_labels(self):
        dataset = self.controller.analysis_context
        if getattr(self,'_analysis_dataset',None) is not dataset:
            self._analysis_dataset=dataset;self._spectrum_overlays=[];self._spectrum_initialized=False
        if dataset.delays and not getattr(self,'_spectrum_initialized',False):
            self._spectrum_overlays=[min(dataset.delays,key=abs)];self._spectrum_initialized=True
        imported = self.controller.analysis_imported
        run = dataset.run if dataset is not None else None
        if imported is not None and run:
            path = run.get('path') or run.get('run_id') or 'Imported experiment'
            self.analysis_source.setText(f'Imported · {path}')
        elif run:
            self.analysis_source.setText(Path(run.get('path') or 'Current experiment').name)
        else:
            self.analysis_source.setText('No experiment loaded')
        delays = dataset.delays if dataset is not None else []
        records = len(dataset.records) if dataset is not None else 0
        repeat = records / len(delays) if delays else 0
        self.analysis_points.setText(f'{len(delays)} delays')
        self.analysis_frames.setText(f'{repeat:.2f} repeats' if repeat and abs(repeat - round(repeat)) > 1e-9 else f'{int(round(repeat))} repeat' + ('' if int(round(repeat)) == 1 else 's'))
        if dataset is not None:
            if not self.analysis_wave.hasFocus():
                self.analysis_wave.setText(f'{dataset.wave:g}')
            if not self.analysis_window.hasFocus():
                self.analysis_window.setText(f'{dataset.half_window:g}')
        fit = dataset.fit if dataset is not None else None
        if fit is not None:
            self.analysis_fit_state.setText('● Viewing spectra' if self.analysis_view.currentData()=='spectrum' else '● Fitted')
            set_prop(self.analysis_fit_state, 'data-state', 'success')
        elif self._fit_running:
            self.analysis_fit_state.setText('Fitting')
            set_prop(self.analysis_fit_state, 'data-state', 'warning')
        elif run is not None:
            self.analysis_fit_state.setText('Ready')
            set_prop(self.analysis_fit_state, 'data-state', 'neutral')
        else:
            self.analysis_fit_state.setText('No data')
            set_prop(self.analysis_fit_state, 'data-state', 'neutral')
        ready = bool(dataset is not None and run is not None and dataset.records and dataset.axis)
        self.run_fit_button.setEnabled(bool(self.controller.online and ready and not self._fit_running and (imported is not None or not self._snapshot.get('busy'))))
        self.export_fit.setEnabled(fit is not None and self.analysis_view.currentData()!='spectrum')
        self.save_run.setEnabled(bool(imported is None and run is not None and not run.get('saved')))
        self._refresh_analysis_delay_combo(delays)
        self._refresh_spectrum_legend()
    def _refresh_analysis_delay_combo(self, delays):
        key=(id(self.controller.analysis_context),tuple(delays))
        if getattr(self,'_delay_combo_key',None)==key:return
        self._delay_combo_key=key
        current = self.analysis_delay.currentData()
        self.analysis_delay.blockSignals(True)
        self.analysis_delay.clear()
        for delay in delays:
            self.analysis_delay.addItem(f'{delay:g} ps', float(delay))
        if current is not None:
            index = self.analysis_delay.findData(float(current))
            if index >= 0:
                self.analysis_delay.setCurrentIndex(index)
        self.analysis_delay.blockSignals(False)
    def _analysis_view_changed(self):
        self.analysis_bottom.setVisible(self.analysis_view.currentData()!='spectrum')
        self.analysis_window_label.setVisible(self.analysis_view.currentData()!='spectrum')
        view = self.analysis_view.currentData()
        if view == 'spectrum':
            self.analysis_spectrum_controls.show()
            self.analysis_fit_controls.hide()
            self.analysis_residual_wrap.hide()
            self.analysis_legend.hide()
            self.analysis_time_key.show()
        else:
            self.analysis_spectrum_controls.hide()
            self.analysis_fit_controls.show()
            self.analysis_residual_wrap.show()
            self.analysis_legend.show()
            self.analysis_time_key.hide()
        self._update_analysis_labels();self._redraw_analysis()
    def _add_spectrum_overlay(self):
        value = self.analysis_delay.currentData()
        if value is None:
            return
        if float(value) not in self._spectrum_overlays:
            self._spectrum_overlays.append(float(value))
        self._refresh_spectrum_legend()
        self._redraw_analysis()
    def _clear_spectrum_overlays(self):
        self._spectrum_overlays = []
        self._refresh_spectrum_legend()
        self._redraw_analysis()
    def _refresh_spectrum_legend(self):
        dataset=self.controller.analysis_context
        key=(id(dataset),tuple(self._spectrum_overlays),len(dataset.records) if dataset else 0,self.analysis_view.currentData(),current_mode())
        if key==getattr(self,'_spectrum_legend_key',None):return
        self._spectrum_legend_key=key
        while self.analysis_spectrum_legend.layout().count():
            item = self.analysis_spectrum_legend.layout().takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide();widget.deleteLater()
        dataset = self.controller.analysis_context
        delays = dataset.delays if dataset is not None else []
        self._spectrum_overlays = [delay for delay in self._spectrum_overlays if delay in delays]
        for delay in sorted(self._spectrum_overlays):
            group=dataset.groups.get(delay)
            count=len(group.records) if hasattr(group,'records') else len([r for r in dataset.records if float(r.get('delay_ps',0))==delay])
            button = QPushButton(f'━  {delay:g} ps · {count} frames  ×');button.setFixedHeight(30)
            button.setProperty('small', True)
            color = map_color(_delay_fraction(delay, delays))
            button.setStyleSheet(f'font:11px Consolas;color:{palette()["text"]};')
            button.clicked.connect(lambda checked=False, value=delay: self._remove_spectrum_overlay(value))
            self.analysis_spectrum_legend.layout().addWidget(button)
        self.analysis_spectrum_legend.layout().addStretch(1)
        self.analysis_spectrum_legend.setVisible(bool(self._spectrum_overlays) and self.analysis_view.currentData() == 'spectrum')
        self.analysis_spectrum_status.setText(f'{len(self._spectrum_overlays)} overlays · {len(delays)} acquired delays' if self._spectrum_overlays else 'No overlays')
    def _remove_spectrum_overlay(self, value):
        self._spectrum_overlays = [delay for delay in self._spectrum_overlays if delay != value]
        self._refresh_spectrum_legend()
        self._redraw_analysis()
    def _redraw_analysis(self):
        dataset = self.controller.analysis_context
        if dataset is None:
            return
        view = self.analysis_view.currentData()
        scale = self.analysis_scale.current()
        delays = dataset.delays
        if view == 'spectrum':
            curves = []
            axis = dataset.axis
            for delay in self._spectrum_overlays:
                group = dataset.groups.get(delay)
                if group is None:
                    continue
                values = group.normalized
                if axis and len(axis):
                    x = np.asarray(axis[:values.size], dtype=float)
                    x_label = 'Wavelength / nm'
                else:
                    x = np.arange(values.size, dtype=float)
                    x_label = 'CCD pixel'
                color = map_color(_delay_fraction(delay, delays))
                curves.append((x, values, color, False, 1.7))
            self.analysis_fit_state.setText('● Viewing spectra');self.analysis_plot_title.setText('Spectrum')
            self.analysis_window_label.setText(f'{len(self._spectrum_overlays)} delay(s)')
            self.analysis_curve.set_spectra(curves, x_label if self._spectrum_overlays else 'Wavelength / nm', scale)
            self.analysis_residual.set_points([])
            if delays:
                self.analysis_time_min.setText(f'{min(delays):g}')
                self.analysis_time_max.setText(f'{max(delays):g}')
            self.analysis_spectrum_status.setText(f'{len(self._spectrum_overlays)} overlays · {len(delays)} acquired delays' if self._spectrum_overlays else 'Choose a delay and press Overlay');self.analysis_add_delay.setEnabled(self.analysis_delay.currentData() not in self._spectrum_overlays)
            return
        fit = dataset.fit
        observed = list(zip(fit['delays_ps'],fit['observed'])) if fit else dataset.observed()
        fit_points = self._fit_curve_points(fit)
        self.analysis_plot_title.setText('Decay & fit')
        self.analysis_window_label.setText(f'{dataset.wave:g} ± {dataset.half_window:g} nm')
        self.analysis_curve.set_decay(observed, fit_points, scale)
        residuals = self._fit_residual_points(fit, observed, fit_points)
        self.analysis_residual.set_points(residuals)
        self._update_fit_panel(fit, observed)
        if observed:
            self.analysis_curve.set_marker(observed[0][0])
            self.analysis_residual.set_marker(observed[0][0])
            if not getattr(self,'_analysis_point_selected',False):
                self.analysis_curve.marker.hide();self.analysis_curve.point.hide();self.analysis_residual.marker.hide()
    def _analysis_selection_changed(self):
        dataset = self.controller.analysis_context
        if dataset is None:
            return
        wave = number_value(self.analysis_wave)
        half = number_value(self.analysis_window)
        if wave is None or half is None or half <= 0:
            return
        if abs(dataset.wave - wave) > 1e-12 or abs(dataset.half_window - half) > 1e-12:
            dataset.wave = float(wave)
            dataset.half_window = float(half)
            self.controller.invalidate_fit('analysis')
            self._redraw_analysis()
            self._map_dirty = True
            self._schedule_refresh()
    def _analysis_options_changed(self):
        self.controller.invalidate_fit('analysis')
        self._fit_running=False
        self._update_analysis_labels();self._redraw_analysis()
    def _analysis_delay_picked(self, delay):
        self._analysis_point_selected=True
        dataset = self.controller.analysis_context
        observed = dataset.observed() if dataset is not None else []
        if not observed:
            return
        nearest = min(observed, key=lambda item: abs(item[0] - float(delay)))
        self.analysis_curve.set_marker(nearest[0])
        self.analysis_residual.set_marker(nearest[0])
        self.analysis_point.setText(f'{nearest[0]:.4g} ps · {nearest[1]:.4g}')
        self.analysis_point.show()
    def _fit_curve_points(self, fit):
        if not isinstance(fit, dict):
            return []
        for key in ('curve', 'fit_curve', 'fitted_curve', 'model_curve'):
            value = fit.get(key)
            if isinstance(value, list) and value:
                points = []
                for item in value:
                    if isinstance(item, dict):
                        x = item.get('delay_ps', item.get('x'))
                        y = item.get('intensity', item.get('counts', item.get('y')))
                        if x is not None and y is not None:
                            points.append((float(x), float(y)))
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        points.append((float(item[0]), float(item[1])))
                if points:
                    return points
        x_values = fit.get('delays_ps') or fit.get('curve_delay_ps') or fit.get('x')
        y_values = fit.get('predicted') or fit.get('fit_counts') or fit.get('curve_counts') or fit.get('fit_values') or fit.get('y')
        if isinstance(x_values, list) and isinstance(y_values, list) and len(x_values) == len(y_values):
            return [(float(x), float(y)) for x, y in zip(x_values, y_values)]
        return []
    def _fit_residual_points(self, fit, observed, fit_points):
        if not isinstance(fit, dict):
            return []
        if fit.get('delays_ps') and fit.get('residuals'):
            return list(zip(fit['delays_ps'],fit['residuals']))
        residuals = fit.get('residuals')
        if isinstance(residuals, list) and residuals:
            points = []
            for index, item in enumerate(residuals):
                if isinstance(item, dict):
                    delay = item.get('delay_ps', item.get('x'))
                    value = item.get('residual', item.get('y'))
                    if delay is not None and value is not None:
                        points.append((float(delay), float(value)))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    points.append((float(item[0]), float(item[1])))
                elif index < len(observed):
                    points.append((float(observed[index][0]), float(item)))
            if points:
                return points
        if fit_points and observed:
            xs = np.asarray([item[0] for item in fit_points], dtype=float)
            ys = np.asarray([item[1] for item in fit_points], dtype=float)
            output = []
            for delay, intensity in observed:
                model = float(np.interp(delay, xs, ys))
                output.append((float(delay), float(intensity) - model))
            return output
        return []
    def _fit_parameters(self,fit):
        if not isinstance(fit,dict):return []
        names={'tau_ps':('τ','ps'),'tau_1_ps':('τ₁','ps'),'tau_2_ps':('τ₂','ps'),'amplitude':('Amplitude','counts/s'),'amplitude_1':('Amplitude 1','counts/s'),'amplitude_2':('Amplitude 2','counts/s'),'t0_ps':('t₀','ps'),'offset_counts_s':('Offset','counts/s')}
        return [(label,fit['parameters'][key],fit.get('standard_errors',{}).get(key),unit) for key,(label,unit) in names.items() if key in fit.get('parameters',{})]
    def _update_fit_panel(self, fit, observed):
        self.fit_table.clearSpans()
        self.fit_table.setRowCount(0)
        parameters = self._fit_parameters(fit)
        if not parameters:
            self.fit_table.setRowCount(1)
            item = QTableWidgetItem('Run a fit to inspect lifetimes and parameter uncertainty.')
            item.setForeground(Qt.gray);item.setTextAlignment(Qt.AlignCenter)
            self.fit_table.setItem(0, 0, item)
            self.fit_table.setSpan(0, 0, 1, 4)
            self.fit_sample_count.setText('No fit result')
            self.fit_rmse.setText('-')
            self.fit_condition.setText('-')
            self.fit_warning.hide()
            return
        self.fit_table.setRowCount(len(parameters))
        for row, parameter in enumerate(parameters):
            name, value, error, unit = parameter
            for column, text in enumerate((str(name), fmt(value, 6), fmt(error, 6), str(unit or ''))):
                cell = QTableWidgetItem(text); cell.setTextAlignment(Qt.AlignCenter)
                self.fit_table.setItem(row, column, cell)
        samples = len(fit.get('delays_ps') or [])
        rmse = fit.get('rmse', fit.get('rmse_counts_s')) if isinstance(fit, dict) else None
        if rmse is None and fit.get('residuals'):
            rmse=float(np.sqrt(np.mean(np.square(fit['residuals']))))
        condition = fit.get('condition_number', fit.get('jacobian_condition')) if isinstance(fit, dict) else None
        self.fit_sample_count.setText(str(samples))
        self.fit_rmse.setText(fmt(rmse, 5))
        self.fit_condition.setText(fmt(condition, 5))
        warnings = fit.get('warnings') if isinstance(fit, dict) else None
        warning_text = '; '.join(str(item) for item in warnings) if isinstance(warnings, list) else str(warnings or '')
        if warning_text:
            self.fit_warning.setText(warning_text)
            self.fit_warning.show()
        else:
            self.fit_warning.hide()
    def run_fit(self):
        dataset = self.controller.analysis_context
        if dataset is None or dataset.run is None or not dataset.records:
            self.show_toast('No experiment is available for fitting')
            return
        wave = number_value(self.analysis_wave)
        half = number_value(self.analysis_window)
        irf = number_value(self.irf_width)
        if wave is None or half is None or half <= 0 or irf is None or irf <= 0:
            self.fit_status.setText('Enter a finite positive wavelength window and IRF width')
            return
        start = number_value(self.fit_start)
        end = number_value(self.fit_end)
        candidate = {
            'model': self.fit_model.currentData() or 'double',
            'wavelength_nm': float(wave),
            'center_nm': float(wave),
            'half_window_nm': float(half),
            'irf_fwhm_ps': float(irf),
            'irf_width_ps': float(irf),
            'start_ps': start,
            'end_ps': end,
            'fit_start_ps': start,
            'fit_end_ps': end,
            'fit_start': start,
            'fit_end': end,
            'min_delay_ps': start,
            'max_delay_ps': end,
        }
        options = _model_payload('backend.models', 'FitRequest', candidate)
        self.fit_status.setText('Requesting fit…')
        self._fit_running = True
        self.run_fit_button.setEnabled(False)
        self.controller.run_fit(options, 'analysis', success=self._fit_requested, failure=self._fit_request_failed)
    def _fit_requested(self, task_id):
        self.fit_status.setText(f'Fit queued · {task_id}')
    def _fit_request_failed(self, message):
        self._fit_running = False
        self.fit_status.setText(str(message))
        self.run_fit_button.setEnabled(True)
        self.show_toast(str(message))
    def _export_fit_csv(self):
        fit = self.controller.analysis_context.fit
        if fit is None:
            return
        default = str(self.output_dir / 'fit.csv')
        path, selected = QFileDialog.getSaveFileName(self, 'Export fit CSV', default, 'CSV files (*.csv);;All files (*)')
        if not path:
            return
        dataset = self.controller.analysis_context
        observed = dataset.observed()
        fit_points = self._fit_curve_points(fit)
        residuals = self._fit_residual_points(fit, observed, fit_points)
        try:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(['fit_export'])
            writer.writerow(['wavelength_nm', dataset.wave])
            writer.writerow(['half_window_nm', dataset.half_window])
            writer.writerow([])
            writer.writerow(['parameter', 'value', 'std_error', 'unit'])
            for item in self._fit_parameters(fit):
                writer.writerow([item[0], item[1], item[2], item[3]])
            writer.writerow([])
            writer.writerow(['delay_ps', 'observed', 'fit', 'residual'])
            fit_x = np.asarray([item[0] for item in fit_points], dtype=float) if fit_points else np.asarray([], dtype=float)
            fit_y = np.asarray([item[1] for item in fit_points], dtype=float) if fit_points else np.asarray([], dtype=float)
            residual_map = {item[0]: item[1] for item in residuals}
            for delay, intensity in observed:
                fitted = float(np.interp(delay, fit_x, fit_y)) if fit_x.size else None
                writer.writerow([delay, intensity, fitted, residual_map.get(delay)])
            Path(path).write_text(buffer.getvalue(), encoding='utf-8')
            self.show_toast('Fit CSV exported')
        except Exception as error:
            self.show_toast(str(error))
    def _open_stage_dialog(self):
        dialog = DeviceDialog(self.controller, 'stage', self._snapshot, self)
        apply_pointer_cursors(dialog)
        dialog.exec()
        self.controller.request_status()
    def _open_detector_dialog(self):
        dialog = DetectorDialog(self.controller, self._snapshot, self)
        apply_pointer_cursors(dialog)
        dialog.exec()
        self.controller.request_status()
    def _choose_source(self, value):
        current = str((self._snapshot or {}).get('source_mode') or 'hardware')
        if value and value != current:
            self.select_mode(value)
    def _sync_sample_name(self, text=None):
        name = (self.save_path.text() if text is None else text).strip()
        for suffix in ('.h5', '.hdf5'):
            if name.lower().endswith(suffix):
                name = name[:-len(suffix)]
        self.sample_name.setText(name or DEFAULT_SAMPLE_NAME)
    def select_mode(self, mode):
        mode = str(mode)
        if mode in ('hardware', 'simulation'):
            self._selected_source = mode
            self.controller.dispatch('connect', {'mode': mode}, success=lambda result: self.controller.request_status(), failure=lambda payload: self.show_toast(payload_message(payload)))
            return
        if mode in ('scan', 'single', 'repeat'):
            self.mode_seg.set_selected(mode)
            self._mode_changed(mode)
    def _mode_changed(self, mode):
        self._mode = mode
        self.scan_params.setVisible(mode != 'single')
        self.repeat_wrap.setVisible(mode == 'repeat')
        self._update_point_estimate()
    _SEGMENT_REMOVE_WIDTH = 32
    def _segment_cells(self, host, cells, trailing, dividers):
        # Shared column geometry for the Start / End / Step header and every joined segment row:
        # three equal cells, a 1 px divider before each following cell, then the fixed remove column.
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for index, cell in enumerate(list(cells) + [trailing]):
            if index:
                divider = QFrame() if dividers else QWidget()
                divider.setFixedSize(1, 16)
                if dividers:
                    divider.setObjectName('segmentDivider')
                layout.addWidget(divider, 0, Qt.AlignVCenter)
            layout.addWidget(cell, 0 if cell is trailing else 1)
        return layout
    def _add_segment_row(self, start='0.0', end='1.0', step='0.1'):
        row = QFrame()
        row.setObjectName('segmentRow')
        row.setFixedHeight(30)
        start_edit = number_edit(start)
        end_edit = number_edit(end)
        step_edit = number_edit(step)
        for edit, name in ((start_edit, 'Start'), (end_edit, 'End'), (step_edit, 'Step')):
            edit.setFixedHeight(30)
            edit.setMinimumWidth(0)
            edit.setAccessibleName(f'Segment {name.lower()} / ps')
            edit.textChanged.connect(lambda text: self._update_point_estimate())
        remove = QPushButton('×')
        remove.setObjectName('segmentRemove')
        remove.setToolTip('Remove segment')
        remove.setCursor(Qt.PointingHandCursor)
        remove.setFixedSize(self._SEGMENT_REMOVE_WIDTH, 30)
        record = {'widget': row, 'start': start_edit, 'end': end_edit, 'step': step_edit, 'remove': remove}
        remove.clicked.connect(lambda checked=False, item=record: self._remove_segment_row(item))
        self._segment_cells(row, (start_edit, end_edit, step_edit), remove, True)
        self.segments_layout.addWidget(row)
        self._segment_rows.append(record)
        self._update_point_estimate()
        return record
    def _remove_segment_row(self, record):
        if record in self._segment_rows and len(self._segment_rows) > 1:
            self._segment_rows.remove(record)
            record['widget'].setParent(None)
            record['widget'].deleteLater()
        self._update_point_estimate()
    def _mark_segment_rows(self):
        # Tint rows that would be rejected instead of only reporting the first problem in text.
        for row in self._segment_rows:
            start, end, step = number_value(row['start']), number_value(row['end']), number_value(row['step'])
            if start is None or end is None or step is None:
                problem = 'Enter start, end and step'
            elif step <= 0:
                problem = 'Step must be positive'
            elif end < start:
                problem = 'End must not be before start'
            else:
                problem = ''
            if bool(row['widget'].property('invalid')) != bool(problem):
                set_prop(row['widget'], 'invalid', bool(problem))
            row['widget'].setToolTip(problem)
            row['remove'].setEnabled(len(self._segment_rows) > 1)
    def _segments(self):
        output = []
        for row in self._segment_rows:
            start = number_value(row['start'])
            end = number_value(row['end'])
            step = number_value(row['step'])
            if start is None or end is None or step is None:
                raise ValueError('Every scan segment needs finite start, end and step values')
            if step <= 0:
                raise ValueError('Scan steps must be positive')
            if end < start:
                raise ValueError('Scan segment end must not be before start')
            output.append({'start_ps': float(start), 'end_ps': float(end), 'step_ps': float(step), 'start': float(start), 'end': float(end), 'step': float(step)})
        if not output:
            raise ValueError('Add at least one scan segment')
        return output
    def _full_save_path(self):
        name = self.save_path.text().strip()
        validate_filename(name)
        if name.lower().endswith('.h5') or name.lower().endswith('.hdf5'):
            filename = name
        else:
            filename = name + '.h5'
        return str(self.output_dir / filename)
    def collect_run_request(self):
        exposure = number_value(self.exposure_edit)
        if exposure is None or exposure <= 0:
            raise ValueError('Exposure must be a positive finite number')
        if self._mode == 'scan' or self._mode == 'repeat':
            segments = self._segments()
        else:
            delay = number_value(self.delay_edit)
            if delay is None:
                delay = 0.0
            segments = [{'start': float(delay), 'end': float(delay)+1.0, 'step': 1.0}]
        repeats = int(self.repeats_input.value()) if self._mode == 'repeat' else 1
        path = self._full_save_path()
        candidate = {
            'request_id': uuid.uuid4().hex,
            'mode': self._mode,
            'segments': segments,
            'repeats': repeats,
            'frames_per_point': 1,
            'exposure_s': float(exposure),
            'settle_s': 0.05,
            'seed': 42,
            'speedup': 50.0,
            'fault': {'kind': 'none', 'point_index': 0},
            'autosave': bool(self.autosave.isChecked()),
            'save_path': path,
            'overwrite_token': None,
        }
        return _model_payload('backend.models', 'RunRequest', candidate, nested=('segments', 'fault'))
    def start_pause_resume(self):
        snapshot = self._snapshot or {}
        busy = snapshot.get('busy')
        run = snapshot.get('run') or {}
        status = run.get('status')
        if busy == 'scan' and run.get('run_id'):
            run_id = run.get('run_id')
            if status in ('running', 'pausing'):
                self.controller.dispatch('pause', {'run_id': run_id}, failure=lambda payload: self.show_toast(payload_message(payload)))
            elif status in ('paused', 'pausing'):
                self.controller.dispatch('resume', {'run_id': run_id}, failure=lambda payload: self.show_toast(payload_message(payload)))
            return
        if busy:
            self.show_toast(f'Devices are busy: {busy}')
            return
        if not getattr(self.controller, 'online', False):
            self.show_toast('The local service is not ready')
            return
        try:
            request = self.collect_run_request()
        except Exception as error:
            self.validation.setText(str(error))
            self.show_toast(str(error))
            return
        self.validation.setText('')
        self._preflight_and_call('run', request.get('save_path'), request, self._run_started, self._run_failed)
    def _preflight_and_call(self, command, path, data, success, failure):
        autosave = bool(data.get('autosave')) if command == 'run' else True
        if not autosave:
            data['overwrite_token'] = None
            self.controller.dispatch(command, data, success=success, failure=failure)
            return
        def checked(info):
            info = info if isinstance(info, dict) else {}
            exists = bool(info.get('conflicts'))
            if info.get('writable') is False:
                failure({'message': info.get('error') or 'Output location is not writable'})
                return
            token = info.get('overwrite_token') or info.get('token')
            if exists:
                if not self._confirm_overwrite(path):
                    self.save_status.setText('Save cancelled')
                    return
                if token is None:
                    failure({'message': 'Backend did not issue an overwrite token'})
                    return
            data['overwrite_token'] = token
            self.controller.dispatch(command, data, success=success, failure=failure)
        self.controller.dispatch('output_check', {'path': str(path)}, success=checked, failure=failure)
    def _confirm_overwrite(self, path):
        answer = QMessageBox.question(self, 'Overwrite existing file', f'{path} already exists. Overwrite it?', QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        return answer == QMessageBox.Yes
    def _run_started(self, result):
        self.validation.setText('')
        self.save_status.setText('Saving acquisition…')
        self._selected_delay = None
        self._picked_delay = None
        self._map_dirty = True
        self._schedule_refresh()
    def _run_failed(self, payload):
        message = payload_message(payload)
        self.validation.setText(message)
        self.show_toast(message)
    def stop_active(self):
        snapshot = self._snapshot or {}
        busy = snapshot.get('busy')
        run = snapshot.get('run') or {}
        if busy == 'scan' and run.get('run_id'):
            self.controller.dispatch('stop', {'run_id': run.get('run_id')}, failure=lambda payload: self.show_toast(payload_message(payload)))
            return
        command = {'preview': 'preview_stop', 'move': 'stage_stop', 'background': 'background_stop', 'calibration': 'calibration_stop'}.get(busy)
        if command:
            self.controller.dispatch(command, {}, failure=lambda payload: self.show_toast(payload_message(payload)))
    def toggle_preview(self):
        snapshot = self._snapshot or {}
        busy = snapshot.get('busy')
        if busy == 'preview':
            self.controller.dispatch('preview_stop', {}, failure=lambda payload: self.show_toast(payload_message(payload)))
            return
        if busy:
            self.show_toast(f'Devices are busy: {busy}')
            return
        self.controller.dispatch('preview_start', {}, failure=lambda payload: self.show_toast(payload_message(payload)))
    def move_stage(self, delay, relative=False):
        value = float(delay)
        if not math.isfinite(value):
            self.show_toast('Delay must be finite')
            return
        self.controller.dispatch('move', {'delay_ps': value, 'relative': bool(relative), 'request_id': uuid.uuid4().hex}, failure=lambda payload: self.show_toast(payload_message(payload)))
    def _move_absolute(self):
        value = number_value(self.delay_edit)
        if value is None:
            self.show_toast('Enter a finite delay')
            return
        self.move_stage(value, relative=False)
    def set_zero(self):
        self.controller.dispatch('zero', {}, success=lambda result: self.controller.request_status(), failure=lambda payload: self.show_toast(payload_message(payload)))
    def _on_exposure_changed(self):
        if self._updating_controls:
            return
        value = number_value(self.exposure_edit)
        if value is None or value <= 0:
            self.show_toast('Exposure must be positive')
            return
        self.controller.dispatch('configure', {'exposure_s': float(value)}, success=lambda result: self.controller.request_status(), failure=lambda payload: self.show_toast(payload_message(payload)))
    def _apply_spectrograph(self):
        if self._updating_controls:
            return
        center = number_value(self.center_edit)
        slit = number_value(self.slit_edit)
        try:
            grating = int(self.grating_combo.currentText())
        except Exception:
            grating = 300
        if center is None or center <= 0 or slit is None or slit <= 0:
            self.show_toast('Center wavelength and slit must be positive')
            return
        self.controller.dispatch('spectrograph', {'center_nm': float(center), 'grating': grating, 'slit_um': float(slit)}, success=lambda result: self.controller.request_status(), failure=lambda payload: self.show_toast(payload_message(payload)))
    def _capture_background(self):
        self.controller.dispatch('background', {'kind': 'optical_background', 'condition': 'manual'}, failure=lambda payload: self.show_toast(payload_message(payload)))
    def _choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Save folder', str(self.output_dir))
        if folder:
            self.output_dir = Path(folder)
            self.save_status.setText(f'Folder: {self.output_dir}')
    def _update_output_status(self):
        snapshot = self._snapshot or {}
        run = snapshot.get('run') or {}
        if run.get('saved') and run.get('path'):
            self.save_status.setText(f'Saved: {run.get("path")}')
        elif run:
            self.save_status.setText('Memory-only · not saved to disk')
        else:
            self.save_status.setText('No experiment saved')
    def export_current_csv(self):
        run = self.controller.acquisition.run
        if not run:
            self.show_toast('No acquisition to export')
            return
        default = str(self.output_dir / ((self.save_path.text().strip() or 'acquisition') + '.csv'))
        path, selected = QFileDialog.getSaveFileName(self, 'Export CSV', default, 'CSV files (*.csv);;All files (*)')
        if not path:
            return
        center = number_value(self.center_edit) or 600.0
        half = 5.0
        data = {'run_id': run.get('run_id'), 'center_nm': float(center), 'half_window_nm': float(half)}
        def success(result):
            content = result if isinstance(result, str) else result.get('csv') or result.get('content') if isinstance(result, dict) else str(result)
            try:
                Path(path).write_text(content, encoding='utf-8')
                self.show_toast('CSV exported')
            except Exception as error:
                self.show_toast(str(error))
        self.controller.dispatch('export', data, success=success, failure=lambda payload: self.show_toast(payload_message(payload)))
    def _choose_analysis_file(self):
        path, selected = QFileDialog.getOpenFileName(self, 'Open HDF5 file', str(self.output_dir), 'HDF5 files (*.h5 *.hdf5);;All files (*)')
        if path:
            self.open_analysis_file(path)
    def open_analysis_file(self, path=None):
        if path is None:
            path, selected = QFileDialog.getOpenFileName(self, 'Open HDF5 file', str(self.output_dir), 'HDF5 files (*.h5 *.hdf5);;All files (*)')
            if not path:
                return
        self.open_path.setText(str(path))
        self.fit_status.setText('Loading Analysis file…')
        def success(run):
            self.fit_status.setText('Analysis file loaded')
            self.show_page('analysis')
            self._update_analysis_labels()
            self._redraw_analysis()
        def failure(message):
            self.fit_status.setText(str(message))
            self.show_toast(str(message))
        self.controller.open_analysis_file(str(path), success=success, failure=failure)
    def _save_analysis_run(self):
        dataset = self.controller.acquisition
        run = dataset.run
        if not run:
            self.show_toast('No current run to save')
            return
        try:
            path = self._full_save_path()
        except Exception as error:
            self.show_toast(str(error))
            return
        data = {'run_id': run.get('run_id'), 'path': path}
        self._preflight_and_call('save', path, data, lambda result: self._saved(result), lambda payload: self.show_toast(payload_message(payload)))
    def _saved(self, result):
        self.show_toast('HDF5 saved')
        self.save_status.setText(f'Saved: {result.get("path") if isinstance(result, dict) else ""}')
        self.controller.request_status()
    def _discard_run(self):
        if self.controller.analysis_imported is not None:
            self.controller.close_analysis()
            return
        run = self.controller.acquisition.run
        if not run:
            self.show_toast('No in-memory run to discard')
            return
        answer = QMessageBox.question(self, 'Discard in-memory run', 'Discard the current in-memory run?', QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        self.controller.dispatch('discard', {'run_id': run.get('run_id')}, success=lambda result: self.controller.request_status(), failure=lambda payload: self.show_toast(payload_message(payload)))
    def show_page(self, name):
        name = str(name)
        if name not in self._pages:
            return
        self._current_page = name
        self.stack.setCurrentWidget(self._pages[name])
        for key, button in self.nav_buttons.items():
            set_prop(button, 'active', key == name)
        self.footer_host.setVisible(name == 'acquisition')
        self._update_header(self._snapshot or {})
        if name == 'analysis':
            self._update_analysis_labels()
            self._redraw_analysis()
            self._analysis_view_changed()
        elif name == 'calibration':
            self._update_cal_plots()
    def _toggle_theme(self):
        mode = 'dark' if current_mode() == 'light' else 'light'
        apply_to_app(mode)
        self._refresh_run_icons()
        for plot in (self.spectrum_plot, self.map_plot, self.decay_plot, self.analysis_curve, self.analysis_residual, self.cal_image, self.cal_spectrum, self.cal_mapping, self.cal_residual):
            plot.update_theme()
        self._map_dirty = True
        self._schedule_refresh()
        self._redraw_analysis()
        self._update_cal_plots()
    def _apply_theme(self):
        try:
            apply_to_app('light')
        except Exception:
            pass
        for plot in (self.spectrum_plot, self.map_plot, self.decay_plot, self.analysis_curve, self.analysis_residual, self.cal_image, self.cal_spectrum, self.cal_mapping, self.cal_residual):
            plot.update_theme()
    def capture_calibration(self):
        exposure = number_value(self.cal_exposure)
        if exposure is None or exposure <= 0:
            self.cal_feedback.setText('Exposure must be positive')
            return
        readout = self.cal_readout.currentData() or 'full_image'
        self.cal_feedback.setText('Acquiring calibration frame…')
        self.cal_capture.setEnabled(False)
        data = {'exposure_s': float(exposure), 'seed': 7, 'readout': readout}
        self.controller.dispatch('calibration_capture', data, success=self._cal_capture_result, failure=self._cal_capture_failed)
    def _cal_capture_result(self, result):
        if not isinstance(result, dict):
            self._cal_capture_failed({'message': 'Backend returned no calibration frame'})
            return
        if result.get('pending') and result.get('capture_id'):
            self._cal_capture_id = result.get('capture_id')
            self._cal_timer.start()
            self.cal_frame_state.setText('Acquiring…')
            self.cal_feedback.setText('CCD exposure pending; use Stop to cancel.')
            return
        self._cal_capture_id = None
        self._cal_timer.stop()
        self._set_calibration_frame(result)
    def _cal_capture_failed(self, payload):
        self.cal_capture.setEnabled(True)
        self.cal_frame_state.setText('No frame')
        self.cal_feedback.setText(payload_message(payload))
    def _poll_calibration_capture(self):
        if not self._cal_capture_id:
            self._cal_timer.stop()
            return
        capture_id = self._cal_capture_id
        def success(result):
            if capture_id != self._cal_capture_id:
                return
            status = result.get('status') if isinstance(result, dict) else None
            if status == 'running':
                return
            self._cal_timer.stop()
            self._cal_capture_id = None
            if status == 'completed' and result.get('frame'):
                self._set_calibration_frame(result.get('frame'))
            else:
                self.cal_capture.setEnabled(True)
                self.cal_frame_state.setText('No frame')
                self.cal_feedback.setText(str(result.get('error') or 'Calibration capture stopped'))
        def failure(payload):
            self._cal_timer.stop()
            self._cal_capture_id = None
            self.cal_capture.setEnabled(True)
            self.cal_feedback.setText(payload_message(payload))
        self.controller.dispatch('calibration_capture_status', {'capture_id': capture_id}, success=success, failure=failure)
    def _set_calibration_frame(self, frame):
        if not isinstance(frame, dict):
            return
        counts = frame.get('image_counts')
        if counts is None:
            image_data = frame.get('image')
            array = np.asarray(image_data) if image_data is not None else None
        else:
            shape = frame.get('shape') or [255, 1024]
            try:
                array = np.asarray(counts, dtype=np.int32).reshape(tuple(shape))
            except Exception:
                array = None
        if array is None:
            self.cal_feedback.setText('Calibration frame has invalid image data')
            return
        self._cal_frame = dict(frame)
        self._cal_frame['image'] = array
        self._cal_solution = None
        self._cal_profile = None
        self._cal_peaks = []
        rows = array.shape[0]
        self.cal_row_start.blockSignals(True)
        self.cal_row_end.blockSignals(True)
        self.cal_row_start.setMaximum(max(0, rows - 1))
        self.cal_row_end.setMaximum(max(0, rows - 1))
        if rows <= 1:
            self.cal_row_start.setValue(0)
            self.cal_row_end.setValue(0)
        else:
            start = min(85, rows - 1)
            end = min(165, rows - 1)
            self.cal_row_start.setValue(start)
            self.cal_row_end.setValue(end)
        self.cal_row_start.blockSignals(False)
        self.cal_row_end.blockSignals(False)
        self.cal_image.set_frame(array, float(frame.get('full_scale') or 65535))
        self.cal_image.reset_view()  # a new frame always opens in full view (Backspace restores the old zoom)
        self.cal_image.set_roi(self.cal_row_start.value(), self.cal_row_end.value())
        source = frame.get('source') or frame.get('source_mode') or 'unknown'
        measured = str(source).lower() == 'measured'
        self.cal_frame_state.setText('● '+('Measured frame' if measured else 'Simulated frame')+' 1')
        self.cal_source.setText(str(frame.get('source_mode') or 'HARDWARE').upper())
        self.cal_roi_label.hide()
        self.cal_feedback.setText('Frame acquired. Extracting spectrum…')
        self._update_cal_controls()
        self._update_cal_plots()
        self.extract_calibration()
    def extract_calibration(self):
        if self._cal_frame is None or not self._cal_frame.get('frame_id'):
            self.cal_feedback.setText('Acquire a calibration frame first')
            return
        start = int(self.cal_row_start.value())
        end = int(self.cal_row_end.value())
        if end < start:
            start, end = end, start
        data = {'frame_id': self._cal_frame.get('frame_id'), 'row_start': start, 'row_end': end}
        signature=(data['frame_id'],start,end)
        def extracted(result):
            current=(self._cal_frame or {}).get('frame_id'),*self._cal_rows_pair()
            if current==signature:self._cal_extracted(result)
        self.controller.dispatch('calibration_extract', data, success=extracted, failure=lambda payload: self._cal_feedback_error(payload))
    def _invalidate_cal_solution(self,*args):
        self._cal_solution=None
        self.cal_fit_state.setText('Draft')
        self.cal_axis.set_selected('pixel')
        self._update_cal_plots()
    def _cal_feedback_error(self, payload):
        message = payload_message(payload)
        self.cal_feedback.setText(message)
        self.show_toast(message)
    def _cal_extracted(self, result):
        if not isinstance(result, dict):
            return
        self._cal_profile = list(result.get('profile_counts') or [])
        self._cal_peaks = [float(value) for value in result.get('peaks_pixels') or []]
        saturated = int(result.get('saturated_pixels') or 0)
        if saturated:
            self.cal_roi_label.setText(f'{saturated} saturated pixels in ROI')
            self.cal_roi_label.show()
        else:
            self.cal_roi_label.hide()
        self._assign_peaks_to_lines()
        self.cal_feedback.setText(f'Extracted {len(self._cal_profile)} pixels · {len(self._cal_peaks)} peak(s)')
        self._update_cal_plots()
        self._update_cal_controls()
    def _assign_peaks_to_lines(self):
        if not self._cal_peaks or not self._cal_lines:
            return
        indices = sorted([index for index, line in enumerate(self._cal_lines) if line.get('enabled') and line.get('pixel') is None], key=lambda index: float(self._cal_lines[index].get('wavelength_nm') or 0))
        if not indices:
            return
        peaks = sorted(self._cal_peaks)
        for index, peak in zip(indices, peaks[:len(indices)]):
            self._cal_lines[index]['pixel'] = float(peak)
        self._refresh_line_widgets()
    def _cal_rows_pair(self):
        start = int(self.cal_row_start.value())
        end = int(self.cal_row_end.value())
        if end < start:
            start, end = end, start
        return start, end
    def fit_calibration(self):
        if self._cal_frame is None or not self._cal_frame.get('frame_id'):
            self.cal_feedback.setText('Acquire a calibration frame first')
            return
        if not self._cal_profile:
            self.extract_calibration()
            return
        if self._snapshot.get('source_mode') == 'hardware' and self._reference_source != 'measured':
            self.cal_feedback.setText('Import measured reference wavelengths before fitting a real CCD frame')
            return
        pairs = []
        for line in self._cal_lines:
            if not line.get('enabled'):
                continue
            pixel = line.get('pixel')
            wavelength = line.get('wavelength_nm')
            if pixel is None or wavelength is None:
                continue
            pairs.append({'enabled': True, 'pixel': float(pixel), 'wavelength_nm': float(wavelength)})
            if len(pairs) >= len(self._cal_lines):
                pass
        enabled_count = sum(1 for line in self._cal_lines if line.get('enabled'))
        for line in self._cal_lines:
            if line.get('enabled') and line.get('pixel') is not None and line.get('wavelength_nm') is not None:
                pass
        pairs = []
        for line in self._cal_lines:
            if not line.get('enabled'):
                continue
            if line.get('pixel') is None or line.get('wavelength_nm') is None:
                self.cal_feedback.setText('Assign a peak pixel to every enabled reference line')
                return
            pairs.append({'enabled': True, 'pixel': float(line.get('pixel')), 'wavelength_nm': float(line.get('wavelength_nm'))})
        degree = int(self.cal_order.currentData() or 2)
        if len(pairs) < degree + 2:
            self.cal_feedback.setText(f'At least {degree + 2} enabled reference lines are required')
            return
        start, end = self._cal_rows_pair()
        data = {'frame_id': self._cal_frame.get('frame_id'), 'row_start': start, 'row_end': end, 'degree': degree, 'convention': self.cal_convention.currentData() or 'air', 'reference_source': self._reference_source, 'reference_name': self._reference_name, 'pairs': pairs}
        self.cal_feedback.setText('Fitting polynomial mapping…')
        signature=json.dumps(data,sort_keys=True)
        def fitted(solution):
            current_pairs=[{'enabled':True,'pixel':float(line['pixel']),'wavelength_nm':float(line['wavelength_nm'])} for line in self._cal_lines if line.get('enabled') and line.get('pixel') is not None]
            current=dict(data,frame_id=(self._cal_frame or {}).get('frame_id'),row_start=self.cal_row_start.value(),row_end=self.cal_row_end.value(),degree=self.cal_order.currentData(),convention=self.cal_convention.currentData(),pairs=current_pairs,reference_source=self._reference_source,reference_name=self._reference_name)
            if json.dumps(current,sort_keys=True)==signature:self._cal_fitted(solution)
        self.controller.dispatch('calibration_fit', data, success=fitted, failure=lambda payload: self._cal_feedback_error(payload))
    def _cal_fitted(self, solution):
        if not isinstance(solution, dict):
            return
        self._cal_solution = dict(solution)
        self.cal_fit_state.setText('Applied' if solution.get('applied') else 'Fitted')
        set_prop(self.cal_fit_state, 'data-state', 'success')
        self.cal_export.setEnabled(True)
        self.cal_apply.setEnabled(True)
        if self.cal_nm_axis is not None:
            self.cal_nm_axis.setEnabled(True)
        warnings = solution.get('warnings') or []
        self.cal_feedback.setText('; '.join(str(item) for item in warnings) if warnings else 'Calibration mapping fitted')
        self.cal_metrics.set_values(_metric(solution.get('rms_nm')) + ' nm', _metric(solution.get('max_error_nm')) + ' nm', _range_text(solution.get('wavelength_range_nm')))
        self._update_deltas()
        self._update_cal_plots()
        self._update_cal_controls()
    def apply_calibration(self):
        if not self._cal_solution or not self._cal_solution.get('solution_id'):
            self.show_toast('Fit a calibration before applying it')
            return
        solution_id = self._cal_solution.get('solution_id')
        self.controller.dispatch('calibration_apply', {'solution_id': solution_id}, success=self._cal_applied, failure=lambda payload: self._cal_feedback_error(payload))
    def _cal_applied(self, result):
        if self._cal_solution:
            self._cal_solution['applied'] = True
        self.cal_fit_state.setText('Applied')
        self.cal_feedback.setText('Calibration applies to future acquisitions')
        self.show_toast('Calibration applied to future acquisitions')
        self.controller.request_status()
    def _export_calibration_json(self):
        if not self._cal_solution:
            return
        default = str(self.output_dir / 'calibration.json')
        path, selected = QFileDialog.getSaveFileName(self, 'Export calibration JSON', default, 'JSON files (*.json);;All files (*)')
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self._cal_solution, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            self.show_toast('Calibration JSON exported')
        except Exception as error:
            self.show_toast(str(error))
    def _open_calibration_json(self):
        path, selected = QFileDialog.getOpenFileName(self, 'Open wavelength calibration JSON', str(self.output_dir), 'JSON files (*.json);;All files (*)')
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding='utf-8'))
            calibration = None
            solution = None
            if isinstance(payload, dict):
                if isinstance(payload.get('calibration'), dict):
                    calibration = dict(payload.get('calibration'))
                if isinstance(payload.get('solution'), dict):
                    solution = dict(payload.get('solution'))
                if calibration is None and solution is None and 'wavelengths_nm' in payload:
                    calibration = dict(payload)
            if calibration is None and solution is not None:
                calibration = dict(solution)
            if calibration is None:
                raise ValueError('JSON does not contain a calibration')
            wavelengths = calibration.get('wavelengths_nm')
            if wavelengths is None and solution is not None:
                wavelengths = solution.get('wavelengths_nm')
            if not isinstance(wavelengths, list) or len(wavelengths) < 2:
                raise ValueError('Calibration wavelengths_nm is missing or too short')
            values = [float(item) for item in wavelengths]
            if any((not math.isfinite(item) or item <= 0) for item in values):
                raise ValueError('Calibration wavelengths must be positive finite numbers')
            source = calibration.get('source')
            if source is None and solution is not None:
                source = solution.get('source')
            if source is None:
                provenance = calibration.get('provenance') or {}
                source = provenance.get('source') or provenance.get('source_mode')
            if source is None:
                frame = calibration.get('frame') or {}
                source = 'measured' if frame.get('source') == 'measured' else 'synthetic'
            calibration_id = calibration.get('calibration_id') or calibration.get('solution_id') or ('imported-' + Path(path).stem)
            candidate = {'calibration_id': str(calibration_id), 'source': str(source), 'wavelengths_nm': values, 'convention': calibration.get('convention'), 'installed_utc': calibration.get('installed_utc'), 'provenance': calibration.get('provenance')}
            data = _model_payload('backend.models', 'CalibrationRequest', candidate)
            self.cal_installed_file.setText(str(path))
            self.controller.dispatch('calibration', data, success=self._calibration_loaded, failure=lambda payload: self._cal_feedback_error(payload))
        except Exception as error:
            self._cal_feedback_error({'message': str(error)})
    def _calibration_loaded(self, result):
        self.calibration_state.setText('Installed calibration loaded')
        self.show_toast('Wavelength calibration installed')
        self.controller.request_status()
    def _cal_readout_changed(self):
        rows = 255 if self.cal_readout.currentData() == 'full_image' else 1
        self.cal_dimensions.setText(f'1024 × {rows} px')
        maximum = max(0, rows - 1)
        self.cal_row_start.setMaximum(maximum)
        self.cal_row_end.setMaximum(maximum)
        if rows <= 1:
            self.cal_row_start.setValue(0)
            self.cal_row_end.setValue(0)
            self.cal_extraction_note.setText('FVB mode sums vertical charge into one row.')
        else:
            self.cal_extraction_note.setText('Mean of selected rows · Click the CCD image to move the band.')
    def _cal_roi_spin_changed(self):
        if self._cal_frame is None:
            return
        self._invalidate_cal_solution()
        start = int(self.cal_row_start.value())
        end = int(self.cal_row_end.value())
        if end < start:
            end = start
            self.cal_row_end.blockSignals(True)
            self.cal_row_end.setValue(end)
            self.cal_row_end.blockSignals(False)
        self.cal_image.set_roi(start, end)
    def _cal_roi_changed(self, start, end):
        self._invalidate_cal_solution()
        self.cal_row_start.blockSignals(True)
        self.cal_row_end.blockSignals(True)
        self.cal_row_start.setValue(int(start))
        self.cal_row_end.setValue(int(end))
        self.cal_row_start.blockSignals(False)
        self.cal_row_end.blockSignals(False)
    def _cal_image_row_clicked(self, row):
        if self._cal_frame is None:
            return
        rows = self._cal_frame['image'].shape[0]
        if rows <= 1:
            return
        start, end = self._cal_rows_pair()
        half = max(1, (end - start) // 2)
        new_start = max(0, min(rows - 1, int(row) - half))
        new_end = max(0, min(rows - 1, int(row) + half))
        self.cal_row_start.setValue(new_start)
        self.cal_row_end.setValue(new_end)
        self.cal_image.set_roi(new_start, new_end)
        self.extract_calibration()
    def _reset_demo_reference(self, silent=False):
        if self._snapshot.get('source_mode') == 'hardware' and not silent:
            self.cal_feedback.setText('Import measured reference wavelengths for hardware calibration')
            return
        wavelengths = [490+.24*p+.00002*p*p for p in (115.23,282.65,449.38,655.14,851.72)]
        self._cal_solution=None
        self.cal_fit_state.setText('Draft')
        self._cal_lines = [{'enabled':True,'pixel':round(p+offset,3),'wavelength_nm':round(value,3)} for p,offset,value in zip((115.23,282.65,449.38,655.14,851.72),(.07,-.11,.09,-.05,.08),wavelengths)]
        self._reference_source = 'synthetic'
        self._reference_imported = False
        self._reference_name = 'Illustrative demo lines'
        self.cal_reference_name.setText(self._reference_name)
        self._repopulate_cal_table()
        self._assign_peaks_to_lines()
        self._update_cal_plots()
    def _load_demo_reference(self, silent=True):
        self._reset_demo_reference(silent=True)
    def _import_reference_csv(self):
        path, selected = QFileDialog.getOpenFileName(self, 'Import reference spectrum CSV', str(self.output_dir), 'CSV files (*.csv);;All files (*)')
        if not path:
            return
        try:
            rows = []
            with open(path, 'r', encoding='utf-8', newline='') as stream:
                reader = csv.reader(stream)
                for row in reader:
                    if not row:
                        continue
                    first = row[0].strip()
                    if not first or first.startswith('#') or first.lower().startswith('wavelength'):
                        continue
                    try:
                        wavelength = float(row[0])
                    except Exception:
                        continue
                    if math.isfinite(wavelength) and wavelength > 0:
                        rows.append(wavelength)
            if not rows:
                raise ValueError('No reference wavelengths were found')
            if len(rows)>100:raise ValueError('Import at most 100 reference lines')
            if len(set(round(item, 9) for item in rows)) != len(rows):
                raise ValueError('Reference wavelengths must be distinct')
            rows.sort()
            self._cal_solution=None
            self.cal_fit_state.setText('Draft')
            self._cal_lines = [{'enabled': True, 'pixel': None, 'wavelength_nm': float(value)} for value in rows]
            self._reference_source = 'measured'
            self._reference_imported = True
            self._reference_name = Path(path).name
            self.cal_reference_name.setText(self._reference_name)
            self._repopulate_cal_table()
            self._assign_peaks_to_lines()
            self._update_cal_plots()
            self.cal_feedback.setText(f'Imported {len(rows)} measured reference line(s)')
        except Exception as error:
            self._cal_feedback_error({'message': str(error)})
    def _cal_add_line(self):
        self._cal_solution=None
        self.cal_fit_state.setText('Draft')
        if not self._cal_lines:
            wavelength = 500.0
        else:
            wavelength = max(float(line.get('wavelength_nm') or 0) for line in self._cal_lines) + 10.0
        self._cal_lines.append({'enabled': True, 'pixel': None, 'wavelength_nm': wavelength})
        self._repopulate_cal_table()
        self._update_cal_plots()
    def _cal_remove_line(self, line):
        self._cal_solution=None
        self.cal_fit_state.setText('Draft')
        if line in self._cal_lines:
            self._cal_lines.remove(line)
        self._repopulate_cal_table()
        self._update_cal_plots()
    def _repopulate_cal_table(self):
        self._cal_table_updating = True
        self.cal_table.blockSignals(True)
        self.cal_table.clearContents()
        self.cal_table.setRowCount(len(self._cal_lines))
        self._cal_row_widgets = []
        for row, line in enumerate(self._cal_lines):
            check = QCheckBox()
            check.setChecked(bool(line.get('enabled')))
            check_container = QWidget()
            check_layout = QHBoxLayout(check_container)
            check_layout.setContentsMargins(0, 0, 0, 0)
            check_layout.addWidget(check, 0, Qt.AlignCenter)
            check.toggled.connect(lambda checked, item=line: self._line_enabled_changed(item, checked))
            pixel = QDoubleSpinBox()
            pixel.setRange(-1.0, 1023.0)
            pixel.setDecimals(3);pixel.setButtonSymbols(QDoubleSpinBox.NoButtons);pixel.setAlignment(Qt.AlignCenter);pixel.setFixedHeight(28)
            pixel.setSingleStep(0.01)
            pixel.setSpecialValueText('-')
            pixel.setValue(float(line.get('pixel')) if line.get('pixel') is not None else -1.0)
            pixel.valueChanged.connect(lambda value, item=line: self._line_pixel_changed(item, value))
            wavelength = QDoubleSpinBox()
            wavelength.setRange(0.001, 1000000.0)
            wavelength.setDecimals(3);wavelength.setButtonSymbols(QDoubleSpinBox.NoButtons);wavelength.setAlignment(Qt.AlignCenter);wavelength.setFixedHeight(28)
            wavelength.setSingleStep(0.1)
            wavelength.setValue(float(line.get('wavelength_nm') or 0.001))
            wavelength.valueChanged.connect(lambda value, item=line: self._line_wavelength_changed(item, value))
            delta = QTableWidgetItem('')
            remove = QPushButton('×')
            remove.setFixedSize(28, 28)
            remove.clicked.connect(lambda checked=False, item=line: self._cal_remove_line(item))
            self.cal_table.setCellWidget(row, 0, check_container)
            self.cal_table.setCellWidget(row, 1, pixel)
            self.cal_table.setCellWidget(row, 2, wavelength)
            delta.setTextAlignment(Qt.AlignCenter);self.cal_table.setItem(row, 3, delta)
            self.cal_table.setCellWidget(row, 4, remove)
            self._cal_row_widgets.append({'check': check, 'pixel': pixel, 'wavelength': wavelength, 'delta': delta, 'line': line})
        self.cal_table.blockSignals(False)
        self._cal_table_updating = False
        self._update_deltas()
        self._update_line_count()
        self._update_cal_controls()
    def _refresh_line_widgets(self):
        self._cal_table_updating = True
        for row, item in enumerate(self._cal_row_widgets):
            line = item['line']
            item['check'].blockSignals(True)
            item['pixel'].blockSignals(True)
            item['wavelength'].blockSignals(True)
            item['check'].setChecked(bool(line.get('enabled')))
            item['pixel'].setValue(float(line.get('pixel')) if line.get('pixel') is not None else -1.0)
            item['wavelength'].setValue(float(line.get('wavelength_nm') or 0.001))
            item['check'].blockSignals(False)
            item['pixel'].blockSignals(False)
            item['wavelength'].blockSignals(False)
        self._cal_table_updating = False
        self._update_deltas()
        self._update_line_count()
        self._update_cal_controls()
    def _line_enabled_changed(self, line, checked):
        if self._cal_table_updating:
            return
        line['enabled'] = bool(checked)
        self._invalidate_cal_solution()
        self._update_line_count()
        self._update_cal_plots()
    def _line_pixel_changed(self, line, value):
        if self._cal_table_updating:
            return
        line['pixel'] = None if value < -0.5 else float(value)
        self._invalidate_cal_solution()
        self._update_deltas()
        self._update_cal_plots()
    def _line_wavelength_changed(self, line, value):
        if self._cal_table_updating:
            return
        line['wavelength_nm'] = float(value)
        self._invalidate_cal_solution()
        self._update_deltas()
        self._update_cal_plots()
    def _update_line_count(self):
        enabled = sum(1 for line in self._cal_lines if line.get('enabled'))
        self.cal_line_count.setText(f'{enabled} enabled lines')
        self._update_cal_controls()
    def _update_deltas(self):
        solution = self._cal_solution
        for item in self._cal_row_widgets:
            line = item['line']
            if solution and line.get('pixel') is not None and solution.get('coefficients'):
                pixel = float(line.get('pixel'))
                origin = float(solution.get('pixel_origin') or 511.5)
                scale = float(solution.get('pixel_scale') or 511.5)
                x = (pixel - origin) / scale
                model = 0.0
                for coefficient in reversed(solution.get('coefficients') or []):
                    model = model * x + float(coefficient)
                delta = float(line.get('wavelength_nm') or 0.0) - model
                item['delta'].setText(f'{delta:+.5g}')
            else:
                item['delta'].setText('')
    def _cal_selection_changed(self):
        rows = self.cal_table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self._cal_lines):
            self._cal_selected_line = self._cal_lines[rows[0].row()]
    def _cal_pixel_picked(self, pixel):
        if self._snapshot.get('busy'):return
        self._invalidate_cal_solution()
        if not self._cal_lines:
            return
        line = self._cal_selected_line
        if line is None or line not in self._cal_lines:
            line = self._cal_lines[0]
            self.cal_table.selectRow(0)
            self._cal_selected_line = line
        line['pixel'] = float(pixel)
        self._refresh_line_widgets()
        self._update_cal_plots()
    def _update_cal_plots(self):
        axis_mode = self.cal_axis.current()
        solution = self._cal_solution
        self.cal_mapping.set_data(self._cal_lines, solution)
        self.cal_residual.set_data(self._cal_lines, solution)
        if solution:
            self.cal_metrics.set_values(_metric(solution.get('rms_nm')) + ' nm', _metric(solution.get('max_error_nm')) + ' nm', _range_text(solution.get('wavelength_range_nm')))
            self.cal_metrics.place()
        else:
            self.cal_metrics.set_values('-', '-', '-')
        if self._cal_profile:
            values = np.asarray(self._cal_profile, dtype=float)
            if axis_mode == 'wavelength' and solution and solution.get('wavelengths_nm') and len(solution.get('wavelengths_nm')) == len(values):
                x_values = np.asarray(solution.get('wavelengths_nm'), dtype=float)
                refs = [float(line.get('wavelength_nm')) for line in self._cal_lines if line.get('enabled') and line.get('wavelength_nm') is not None]
                self.cal_spectrum_axis.setText('nm')
                self.cal_spectrum.set_profile(values, x_values, 'wavelength', refs)
            else:
                self.cal_spectrum_axis.setText('pixel')
                refs = [float(line.get('pixel')) for line in self._cal_lines if line.get('enabled') and line.get('pixel') is not None]
                self.cal_spectrum.set_profile(values, None, 'pixel', refs)
        else:
            self.cal_spectrum.set_profile(None)
        self._update_deltas()
        self._update_cal_controls()
        self._update_calibration_state()
    def _update_cal_controls(self):
        snapshot = self._snapshot or {}
        busy = snapshot.get('busy')
        online = bool(getattr(self.controller, 'online', False))
        available = device_availability(snapshot, online)
        frozen = bool(busy)
        self.cal_capture.setEnabled(bool(online and available.get('calibration') and not frozen and self._cal_capture_id is None))
        self.cal_readout.setEnabled(online and available.get('calibration') and not frozen)
        self.cal_exposure.setEnabled(online and available.get('calibration') and not frozen)
        image_rows=(self._cal_frame or {}).get('shape',[0])[0]
        rows_enabled=self._cal_frame is not None and image_rows>1 and self.cal_readout.currentData()=='full_image' and not frozen
        self.cal_row_start.setEnabled(rows_enabled)
        self.cal_row_end.setEnabled(rows_enabled)
        self.cal_find_peaks.setEnabled(self._cal_frame is not None and not frozen)
        self.cal_reference_file.setEnabled(not frozen)
        self.cal_demo_reference.setEnabled(not frozen and snapshot.get('source_mode') != 'hardware')
        self.cal_convention.setEnabled(not frozen)
        self.cal_order.setEnabled(not frozen)
        self.cal_fit_mapping.setEnabled(self._cal_frame is not None and bool(self._cal_lines) and not frozen)
        self.cal_export.setEnabled(self._cal_solution is not None and not frozen)
        self.cal_apply.setEnabled(self._cal_solution is not None and not frozen)
        self.cal_add_line.setEnabled(not frozen)
        self.cal_table.setEnabled(online and not frozen)
        self.cal_axis.setEnabled(online and not frozen)
        self.cal_image.setEnabled(online and not frozen)
        self.cal_spectrum.setEnabled(online and not frozen)
        self.cal_open_file.setEnabled(not frozen and online)
        if self.cal_nm_axis is not None:
            self.cal_nm_axis.setEnabled(self._cal_solution is not None)
        if not online:
            self.cal_connection_note.setText('Waiting for the local acquisition service.')
        elif not available.get('calibration') and busy != 'calibration':
            self.cal_connection_note.setText('Connect a CCD to acquire a frame.')
        elif frozen:
            self.cal_connection_note.setText('Calibration controls are frozen while an acquisition is active. Stop remains available.')
        else:
            self.cal_connection_note.setText('Backend simulated frame' if snapshot.get('source_mode')=='simulation' else 'CCD connected · SDK2 acquisition')
    def _update_calibration_state(self):
        snapshot = self._snapshot or {}
        calibration = snapshot.get('calibration') or {}
        warning = snapshot.get('calibration_warning')
        if calibration.get('wavelengths_nm'):
            identifier = calibration.get('calibration_id') or 'installed'
            source = calibration.get('source') or 'unknown'
            self.calibration_state.setText(f'Installed: {identifier} · {source}')
        else:
            self.calibration_state.setText('No installed wavelength calibration')
        if warning:
            self.calibration_state.setText(str(warning))
        self.cal_source.setText(str(snapshot.get('source_mode') or 'HARDWARE').upper())
    def _clear_calibration_frame(self, clear_pixels=False):
        self._cal_frame = None
        self._cal_profile = None
        self._cal_peaks = []
        self._cal_solution = None
        self._cal_capture_id = None
        self._cal_timer.stop()
        self.cal_image.set_frame(None)
        self.cal_spectrum.set_profile(None)
        self.cal_mapping.set_data(self._cal_lines, None)
        self.cal_residual.set_data(self._cal_lines, None)
        self.cal_metrics.set_values('-', '-', '-')
        self.cal_frame_state.setText('No frame')
        self.cal_fit_state.setText('Draft')
        self.cal_feedback.setText('')
        self.cal_roi_label.hide()
        self.cal_export.setEnabled(False)
        self.cal_apply.setEnabled(False)
        if self.cal_nm_axis is not None:
            self.cal_nm_axis.setEnabled(False)
        if clear_pixels:
            for line in self._cal_lines:
                line['pixel'] = None
            self._refresh_line_widgets()
        self._update_cal_controls()
        self._update_cal_plots()
    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        if self._closing_started:
            event.ignore()
            return
        if self._has_active_or_unsaved():
            answer = QMessageBox.question(self, 'Close Kerr Studio', 'Stop any active acquisition and close? Unsaved memory-only data will be discarded after writer finalization.', QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self._closing_started = True
        event.ignore()
        QTimer.singleShot(0, lambda: self.controller.begin_close(self._on_close_ready))
    def _on_close_ready(self):
        self._allow_close = True
        self.close()
    def _has_active_or_unsaved(self):
        snapshot = self._snapshot or {}
        if snapshot.get('busy'):
            return True
        run = self.controller.acquisition.run
        return bool(run is not None and not run.get('saved') and self.controller.acquisition.records)


def validate_filename(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('File name is required')
    if '/' in name or chr(92) in name:
        raise ValueError('File name must not contain path separators')
    if len(name)>180 or any(character in '<>:"|?*' for character in name):
        raise ValueError('File name contains reserved Windows characters or is too long')
    if name.endswith('.') or name.endswith(' '):
        raise ValueError('File name must not end with a dot or space')
    if any(ord(character) < 32 for character in name):
        raise ValueError('File name contains control characters')
    stem = name.split('.')[0].upper()
    reserved = {'CON', 'PRN', 'AUX', 'NUL'}
    reserved.update(f'COM{index}' for index in range(1, 10))
    reserved.update(f'LPT{index}' for index in range(1, 10))
    if stem in reserved:
        raise ValueError('File name is a reserved Windows device name')


def _format_map_value(value):
    try:
        value = float(value)
    except Exception:
        return '-'
    if not math.isfinite(value):
        return '-'
    if value >= 1000:
        return f'{value / 1000:.3g}k'
    return f'{value:.3g}'


def _range_text(values):
    if not isinstance(values, list) or len(values) < 2:
        return '-'
    try:
        return f'{float(values[0]):.4g}-{float(values[1]):.4g} nm'
    except Exception:
        return '-'


def _delay_fraction(delay, delays):
    if not delays:
        return 0.5
    low = min(delays)
    high = max(delays)
    if high <= low:
        return 0.5
    return (float(delay) - low) / (high - low)


def available_background(busy):
    return busy in (None, 'background')

def _metric(value):
    return f"{float(value):.4f}".rstrip("0").rstrip(".") if value is not None else "—"
