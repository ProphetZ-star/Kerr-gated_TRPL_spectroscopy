'''Native independent device connection dialogs.

Each device card reads like the main window: fixed facts (model, interface, identity) are plain
read-only text on the card surface, and only the things the user chooses sit in filled fields.
USB scanning is the primary path; serial / DLL overrides live under "Advanced".'''
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit, QStackedWidget
from .widgets import Segmented, CollapsibleSection, ArrowComboBox as QComboBox


def payload_message(payload):
    if isinstance(payload, dict):
        return str(payload.get('message') or payload.get('error') or payload)
    return str(payload)

def combo_value(combo):
    data=combo.currentData()
    return data if data is not None and combo.currentText()==combo.itemText(combo.currentIndex()) else combo.currentText().strip()

def set_prop(widget, name, value):
    if widget.property(name) != value:
        widget.setProperty(name, value); widget.style().unpolish(widget); widget.style().polish(widget)

TITLES = {'stage': 'Delay stage', 'camera': 'CCD camera', 'spectrometer': 'Spectrometer'}
INTERFACES = {'usb': 'USB', 'serial': 'RS-232 (COM port)', 'manual': 'Manual — no controller', 'tcpip': 'TCP/IP', 'visa': 'VISA'}


class DeviceCard(QWidget):
    changed = Signal()
    def __init__(self, controller, family, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.family = family
        self.drivers = []
        self._scanned = False
        self.setObjectName('deviceCard')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        head = QHBoxLayout(); head.setSpacing(10)
        title = QLabel(TITLES.get(family, family)); title.setProperty('h2', True)
        self.state_label = QLabel(''); self.state_label.setObjectName('deviceState')
        head.addWidget(title); head.addStretch(1); head.addWidget(self.state_label)
        layout.addLayout(head)
        # Read-only facts: plain text, no field background.
        self.info = QGridLayout(); self.info.setHorizontalSpacing(16); self.info.setVerticalSpacing(6); self.info.setColumnStretch(1, 1)
        self.model_value = self._info_row(0, 'Model')
        self.interface_value = self._info_row(1, 'Interface')
        self.identity_key = QLabel('Connected as'); self.identity_key.setObjectName('infoKey')
        self.identity_value = QLabel(''); self.identity_value.setObjectName('infoValue')
        self.info.addWidget(self.identity_key, 2, 0); self.info.addWidget(self.identity_value, 2, 1)
        layout.addLayout(self.info)
        divider = QFrame(); divider.setObjectName('dialogDivider'); divider.setFixedHeight(1); layout.addWidget(divider)
        # The choices the user makes.
        self.driver_combo = QComboBox()
        self.usb_combo = QComboBox(); self.usb_combo.setPlaceholderText('Scan USB to list controllers')
        self.camera_combo = QComboBox(); self.camera_combo.setPlaceholderText('Scan USB to list cameras')
        self.port_combo = QComboBox(); self.port_combo.setEditable(True)
        self.baud_combo = QComboBox(); self.baud_combo.setEditable(True); self.baud_combo.addItems(['38400', '9600', '19200', '115200'])
        self.axis_edit = QLineEdit('1')
        self.pi_dll_edit = QLineEdit(); self.pi_dll_edit.setPlaceholderText('Default PI GCS DLL search path')
        self.dll_directory = QLineEdit(); self.dll_directory.setPlaceholderText('Default Andor SDK2 install folder')
        self.transport = Segmented([('USB', 'usb'), ('RS-232', 'serial')])
        self.scan_button = QPushButton('Scan USB' if family != 'spectrometer' else 'Refresh')
        self.scan_button.setObjectName('scanButton')
        self.choose = QGridLayout(); self.choose.setHorizontalSpacing(8); self.choose.setVerticalSpacing(8); self.choose.setColumnStretch(1, 1)
        self.advanced = CollapsibleSection('Advanced')
        advanced = QWidget(); grid = QGridLayout(advanced); grid.setContentsMargins(0, 0, 0, 0); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(8); grid.setColumnStretch(1, 1)
        if family == 'stage':
            self.device_key = self._field_label('USB controller')
            self.choose.addWidget(self.device_key, 0, 0); self.choose.addWidget(self.usb_combo, 0, 1); self.choose.addWidget(self.port_combo, 0, 1); self.choose.addWidget(self.scan_button, 0, 2)
            self.baud_key = self._field_label('Baudrate')
            self.choose.addWidget(self.baud_key, 1, 0); self.choose.addWidget(self.baud_combo, 1, 1, 1, 2)
            for row, (text, widget) in enumerate((('Connect via', self.transport), ('Axis', self.axis_edit), ('PI DLL', self.pi_dll_edit))):
                grid.addWidget(self._field_label(text), row, 0); grid.addWidget(widget, row, 1)
            self.transport.changed.connect(self._transport_changed)
        elif family == 'camera':
            self.choose.addWidget(self._field_label('Camera'), 0, 0); self.choose.addWidget(self.camera_combo, 0, 1); self.choose.addWidget(self.scan_button, 0, 2)
            grid.addWidget(self._field_label('SDK2 folder'), 0, 0); grid.addWidget(self.dll_directory, 0, 1)
        else:
            self.choose.addWidget(self._field_label('Instrument'), 0, 0); self.choose.addWidget(self.driver_combo, 0, 1, 1, 2)
            self.port_key = self._field_label('COM port'); self.baud_key = self._field_label('Baudrate')
            self.choose.addWidget(self.port_key, 1, 0); self.choose.addWidget(self.port_combo, 1, 1); self.choose.addWidget(self.scan_button, 1, 2)
            self.choose.addWidget(self.baud_key, 2, 0); self.choose.addWidget(self.baud_combo, 2, 1, 1, 2)
        self.choose.setColumnMinimumWidth(0, 92); grid.setColumnMinimumWidth(0, 84)
        layout.addLayout(self.choose)
        if family != 'spectrometer':
            self.advanced.add(advanced); layout.addWidget(self.advanced)
        self.message = QLabel(''); self.message.setObjectName('deviceMessage'); self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.note = QLabel(''); self.note.setObjectName('deviceNote'); self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch(1)
        buttons = QHBoxLayout(); buttons.setSpacing(8)
        self.disconnect_button = QPushButton('Disconnect'); self.disconnect_button.setProperty('role', 'dangerText')
        self.connect_button = QPushButton('Connect'); self.connect_button.setProperty('role', 'primary'); self.connect_button.setMinimumWidth(96)
        buttons.addWidget(self.disconnect_button); buttons.addStretch(1); buttons.addWidget(self.connect_button)
        layout.addLayout(buttons)
        self.scan_button.clicked.connect(self._find)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.driver_combo.currentIndexChanged.connect(self._driver_changed)
        if controller is not None:
            controller.snapshotChanged.connect(self.refresh_state)
        self._load()
        self.refresh_state()
    def _info_row(self, row, text):
        key = QLabel(text); key.setObjectName('infoKey')
        value = QLabel('—'); value.setObjectName('infoValue'); value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.addWidget(key, row, 0); self.info.addWidget(value, row, 1)
        return value
    def _field_label(self, text):
        label = QLabel(text); label.setObjectName('fieldKey'); return label
    # --- backend state -------------------------------------------------
    def _snapshot(self):
        return (getattr(self.controller, 'snapshot', None) or {}) if self.controller is not None else {}
    def _link(self):
        return ((self._snapshot().get('devices') or {}).get(self.family)) or {}
    def _simulated(self):
        return str(self._snapshot().get('source_mode') or 'hardware') == 'simulation'
    def _connected(self):
        return bool(self._link().get('connected')) and not self._simulated()
    def refresh_state(self, *args):
        link = self._link(); state = str(link.get('state') or 'disconnected')
        if self._simulated():
            text, kind = '● Simulated', 'simulated'
        elif link.get('connected'):
            text, kind = '● Connected', 'connected'
        elif state == 'manual':
            text, kind = '● Manual', 'connected'
        elif state == 'error':
            text, kind = '● Error', 'error'
        else:
            text, kind = '○ Not connected', 'off'
        self.state_label.setText(text); set_prop(self.state_label, 'state', kind)
        identity = link.get('identity') if (link.get('connected') or state == 'manual') and not self._simulated() else None
        if identity and str(identity) == self.model_value.text():
            identity = None  # a manual instrument reports its model name; don't repeat it
        self.identity_key.setVisible(bool(identity)); self.identity_value.setVisible(bool(identity)); self.identity_value.setText(str(identity or ''))
        connected = self._connected(); simulated = self._simulated(); busy = bool(self._snapshot().get('busy'))
        editable = not connected and not simulated
        for widget in (self.driver_combo, self.usb_combo, self.camera_combo, self.port_combo, self.baud_combo, self.axis_edit, self.pi_dll_edit, self.dll_directory, self.transport, self.scan_button):
            widget.setEnabled(editable and not busy)
        self.connect_button.setEnabled(editable and not busy)
        self.connect_button.setText('Use' if self._manual_driver() else 'Connect')
        self.disconnect_button.setVisible(connected)
        if simulated:
            self._show_message('Simulation mode: this device is simulated. Switch the source to Hardware to connect a real one.')
        elif link.get('error') and state == 'error' and not self.message.text():
            self._show_message(str(link['error']), error=True)
    def _show_message(self, text, error=False):
        self.message.setText(text); set_prop(self.message, 'error', bool(error))
    def _request(self, name, data, success, failure=None):
        if self.controller is None:
            self._show_message('Controller is not available', error=True)
            return
        self.controller.dispatch(name, data, success=success, failure=failure or self._failed)
    def _failed(self, payload):
        self.scan_button.setEnabled(not self._connected() and not self._simulated())
        message = payload_message(payload)
        if 'dll' in message.lower() and self.family in ('stage', 'camera'):
            message += '. Install the vendor software, or set its location under Advanced.'
        self._show_message(message, error=True)
    def _load(self):
        self._request('device_drivers', {}, self._drivers_loaded)
        if self.family != 'camera':
            self._request('device_ports', {}, self._ports_loaded)
    def _drivers_loaded(self, result):
        drivers = result.get('drivers', []) if isinstance(result, dict) else (result or [])
        self.drivers = [item for item in drivers if item.get('family') == self.family]
        self.driver_combo.blockSignals(True)
        self.driver_combo.clear()
        for item in self.drivers:
            label = item.get('label') or item.get('id')
            if not item.get('implemented'):
                label = label + ' / interface'
            self.driver_combo.addItem(label, item.get('id'))
        config = self._link().get('config') or {}
        index = self.driver_combo.findData(config.get('driver'))
        if index >= 0:
            self.driver_combo.setCurrentIndex(index)
        self.driver_combo.blockSignals(False)
        self._driver_changed()
        for key, edit in (('pi_dll', self.pi_dll_edit), ('dll_directory', self.dll_directory)):
            if config.get(key): edit.setText(str(config[key]))
        if config.get('axis'): self.axis_edit.setText(str(config['axis']))
        if config.get('baudrate'): self.baud_combo.setCurrentText(str(config['baudrate']))
        if config.get('port'): self.port_combo.setCurrentText(str(config['port']))
        if config.get('usb_device'):
            self.usb_combo.addItem(str(config['usb_device']), str(config['usb_device'])); self.usb_combo.setCurrentIndex(0)
        if self.family == 'camera' and self._connected():
            self.camera_combo.addItem(f"Andor SDK2 camera {int(config.get('camera_index', 0))}", int(config.get('camera_index', 0))); self.camera_combo.setCurrentIndex(0)
        if self.family == 'stage' and config.get('transport') in ('usb', 'serial'):
            self.transport.set_selected(config['transport'])
        self._transport_changed()
        self.refresh_state()
        # USB scanning is the main path: run it once on open while nothing is connected.
        if self.family in ('stage', 'camera') and not self._scanned and not self._connected() and not self._simulated() and not self._snapshot().get('busy') and self._transport() == 'usb':
            self._find()
    def _ports_loaded(self, result):
        selection = self.port_combo.currentData() or self.port_combo.currentText()
        ports = result.get('ports', []) if isinstance(result, dict) else (result or [])
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for item in ports:
            if isinstance(item, dict):
                self.port_combo.addItem(item.get('label') or item.get('port'), item.get('port'))
            else:
                self.port_combo.addItem(str(item), str(item))
        self.port_combo.blockSignals(False)
        if selection: self.port_combo.setCurrentText(selection)
        error = result.get('error') if isinstance(result, dict) else None
        if error:
            self._show_message(str(error), error=True)
        elif self._transport() == 'serial' and self.scan_button.text() == 'Refresh':
            self._show_message(f'Found {self.port_combo.count()} COM port(s)')
    def _driver(self):
        index = self.driver_combo.currentIndex()
        return self.drivers[index] if 0 <= index < len(self.drivers) else {}
    def _manual_driver(self):
        return bool(self._driver().get('manual'))
    def _transport(self):
        if self.family == 'stage':
            return self.transport.current() or 'usb'
        transports = self._driver().get('transports') or []
        defaults = self._driver().get('defaults') or {}
        return defaults.get('transport') or (transports[0] if transports else 'usb')
    def _driver_changed(self):
        driver = self._driver()
        if not driver:
            return
        defaults = driver.get('defaults') or {}
        self.model_value.setText(str(defaults.get('model') or driver.get('label') or '—'))
        self.axis_edit.setText(str(defaults.get('axis') or '1'))
        self.baud_combo.setCurrentText(str(defaults.get('baudrate') or '38400'))
        self.note.setText(str(driver.get('note') or ''))
        self.note.setVisible(bool(driver.get('note')))
        self._transport_changed()
    def _transport_changed(self, *args):
        transport = self._transport()
        self.interface_value.setText(INTERFACES.get(transport, str(transport).upper()))
        if self.family == 'stage':
            serial = transport == 'serial'
            if args and not self._simulated():
                self._show_message('')  # the last scan result belonged to the other interface
            self.device_key.setText('COM port' if serial else 'USB controller')
            self.usb_combo.setVisible(not serial); self.port_combo.setVisible(serial)
            self.baud_key.setVisible(serial); self.baud_combo.setVisible(serial)
            self.scan_button.setText('Refresh' if serial else 'Scan USB')
        elif self.family == 'spectrometer':
            serial = transport == 'serial'
            for widget in (self.port_key, self.port_combo, self.scan_button, self.baud_key, self.baud_combo):
                widget.setVisible(serial)
        if hasattr(self, 'connect_button'):
            self.connect_button.setText('Use' if self._manual_driver() else 'Connect')
    # --- actions ---------------------------------------------------------
    def _find(self):
        self._scanned = True
        if self.family == 'stage' and self._transport() == 'usb':
            self._show_message('Scanning USB for C-663 controllers…')
            self.scan_button.setEnabled(False)
            self._request('device_pi_usb', {'pi_dll': self.pi_dll_edit.text().strip()}, self._usb_loaded)
        elif self.family == 'camera':
            self._show_message('Scanning USB for Andor SDK2 cameras…')
            self.scan_button.setEnabled(False)
            self._request('device_cameras', {'dll_directory': self.dll_directory.text().strip()}, self._cameras_loaded)
        else:
            self._request('device_ports', {}, self._ports_loaded)
    def _usb_loaded(self, result):
        self.scan_button.setEnabled(not self._connected() and not self._simulated())
        devices = result.get('devices', []) if isinstance(result, dict) else (result or [])
        self.usb_combo.clear()
        for item in devices:
            if isinstance(item, dict):
                self.usb_combo.addItem(item.get('label') or item.get('serial') or str(item), item.get('serial') or item.get('id'))
            else:
                self.usb_combo.addItem(str(item), str(item))
        count = self.usb_combo.count()
        if count: self.usb_combo.setCurrentIndex(0)  # a placeholder combo does not auto-select
        self._show_message(f'Found {count} USB controller' + ('s' if count != 1 else '') if count else 'No C-663 controller found. Check the USB cable and controller power, then scan again.', error=not count)
    def _cameras_loaded(self, result):
        self.scan_button.setEnabled(not self._connected() and not self._simulated())
        cameras = result.get('cameras', []) if isinstance(result, dict) else (result or [])
        self.camera_combo.clear()
        for item in cameras or []:
            if isinstance(item, dict):
                self.camera_combo.addItem(item.get('label') or f"Andor SDK2 camera {item.get('index', 0)}", int(item.get('index', 0)))
        if isinstance(result, dict) and result.get('dll_directory') and not self.dll_directory.text().strip():
            self.dll_directory.setPlaceholderText(str(result['dll_directory']))
        count = self.camera_combo.count()
        if count: self.camera_combo.setCurrentIndex(0)
        self._show_message(f'Found {count} camera' + ('s' if count != 1 else '') if count else 'No Andor camera found. Check the USB cable and camera power, then scan again.', error=not count)
    def _payload(self):
        driver = self.driver_combo.currentData()
        if not driver:
            raise ValueError('Driver list is not loaded yet')
        transport = self._transport()
        data = {'device': self.family, 'driver': str(driver), 'transport': str(transport)}
        if self.family == 'stage':
            if transport == 'usb':
                if not self.usb_combo.count():
                    raise ValueError('Scan USB and select a controller first')
                data['usb_device'] = combo_value(self.usb_combo)
            else:
                data['port'] = combo_value(self.port_combo)
                try:
                    data['baudrate'] = int(self.baud_combo.currentText())
                except Exception:
                    raise ValueError('Baudrate must be a whole number')
            data['pi_dll'] = self.pi_dll_edit.text().strip()
            data['axis'] = self.axis_edit.text().strip() or '1'
        elif self.family == 'camera':
            data['dll_directory'] = self.dll_directory.text().strip()
            index = self.camera_combo.currentData()
            data['camera_index'] = int(index) if index is not None else 0
        elif transport == 'serial':
            data['port'] = combo_value(self.port_combo)
            data['baudrate'] = int(self.baud_combo.currentText())
        return data
    def _connect(self):
        try:
            data = self._payload()
        except Exception as error:
            self._show_message(str(error), error=True)
            return
        self.connect_button.setEnabled(False)
        self._show_message('Connecting…')
        self._request('device_connect', data, self._connected_ok, self._connect_failed)
    def _connected_ok(self, result):
        self._show_message('Ready' if self._manual_driver() else 'Connected')
        self.changed.emit()
        if self.controller is not None:
            self.controller.request_status()
        self.refresh_state()
    def _connect_failed(self, payload):
        self._show_message(payload_message(payload), error=True)
        self.changed.emit()
        self.refresh_state()
    def _disconnect(self):
        self.disconnect_button.setEnabled(False)
        self._request('device_disconnect', {'device': self.family}, self._disconnected, self._disconnect_failed)
    def _disconnected(self, result):
        self.disconnect_button.setEnabled(True)
        self._show_message('Disconnected')
        self.changed.emit()
        if self.controller is not None:
            self.controller.request_status()
        self.refresh_state()
    def _disconnect_failed(self, payload):
        self.disconnect_button.setEnabled(True)
        self._show_message(payload_message(payload), error=True)


def _dialog_frame(dialog, body):
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 16)
    layout.setSpacing(14)
    layout.addWidget(body, 1)
    divider = QFrame(); divider.setObjectName('dialogDivider'); divider.setFixedHeight(1); layout.addWidget(divider)
    close = QPushButton('Close'); close.setMinimumWidth(80)
    close.clicked.connect(dialog.accept)
    row = QHBoxLayout(); row.addStretch(1); row.addWidget(close)
    layout.addLayout(row)
    return layout


class DeviceDialog(QDialog):
    def __init__(self, controller, family, snapshot=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle({'stage':'Delay stage connection','camera':'CCD camera connection','spectrometer':'Spectrometer connection'}.get(family, 'Device connection'))
        self.setMinimumWidth(480)
        self.card = DeviceCard(controller, family)
        _dialog_frame(self, self.card)


class DetectorDialog(QDialog):
    def __init__(self, controller, snapshot=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Detection connections')
        self.setMinimumWidth(480)
        body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(16)
        self.pages = Segmented([('CCD camera', 'camera'), ('Spectrometer', 'spectrometer')])
        layout.addWidget(self.pages)
        self.stack = QStackedWidget()
        self.camera_card = DeviceCard(controller, 'camera')
        self.spectrometer_card = DeviceCard(controller, 'spectrometer')
        self.stack.addWidget(self.camera_card); self.stack.addWidget(self.spectrometer_card)
        layout.addWidget(self.stack, 1)
        self.pages.changed.connect(self.set_page)
        _dialog_frame(self, body)
    def set_page(self, page):
        self.pages.set_selected(page)
        self.stack.setCurrentWidget(self.spectrometer_card if page == 'spectrometer' else self.camera_card)
