'''Native independent device connection dialogs.'''
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox, QTabWidget, QMessageBox


def payload_message(payload):
    if isinstance(payload, dict):
        return str(payload.get('message') or payload.get('error') or payload)
    return str(payload)

def combo_value(combo):
    data=combo.currentData()
    return data if data is not None and combo.currentText()==combo.itemText(combo.currentIndex()) else combo.currentText().strip()


class DeviceCard(QWidget):
    changed = Signal()
    def __init__(self, controller, family, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.family = family
        self.drivers = []
        self.setObjectName('deviceCard')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel({'stage':'Delay stage','camera':'CCD camera','spectrometer':'Spectrometer'}.get(family, family))
        title.setProperty('h2', True)
        layout.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.driver_combo = QComboBox()
        self.transport_combo = QComboBox()
        self.model_edit = QLineEdit()
        self.resource_edit = QLineEdit()
        self.usb_combo = QComboBox()
        self.usb_combo.setEditable(True)
        self.pi_dll_edit = QLineEdit()
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(['38400', '9600', '19200', '115200'])
        self.axis_edit = QLineEdit('1')
        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 63)
        self.dll_directory = QLineEdit()
        self.message = QLabel('')
        self.message.setWordWrap(True)
        self.message.setProperty('muted', True)
        row = 0
        grid.addWidget(QLabel('Driver'), row, 0)
        grid.addWidget(self.driver_combo, row, 1)
        row += 1
        grid.addWidget(QLabel('Transport'), row, 0)
        grid.addWidget(self.transport_combo, row, 1)
        row += 1
        grid.addWidget(QLabel('Model'), row, 0)
        grid.addWidget(self.model_edit, row, 1)
        row += 1
        if family == 'stage':
            grid.addWidget(QLabel('Resource / serial'), row, 0)
            grid.addWidget(self.resource_edit, row, 1)
            row += 1
            grid.addWidget(QLabel('USB controller'), row, 0)
            grid.addWidget(self.usb_combo, row, 1)
            row += 1
            grid.addWidget(QLabel('PI DLL'), row, 0)
            grid.addWidget(self.pi_dll_edit, row, 1)
            row += 1
            grid.addWidget(QLabel('COM port'), row, 0)
            grid.addWidget(self.port_combo, row, 1)
            row += 1
            grid.addWidget(QLabel('Baudrate'), row, 0)
            grid.addWidget(self.baud_combo, row, 1)
            row += 1
            grid.addWidget(QLabel('Axis'), row, 0)
            grid.addWidget(self.axis_edit, row, 1)
            row += 1
        elif family == 'camera':
            grid.addWidget(QLabel('SDK2 DLL directory'), row, 0)
            grid.addWidget(self.dll_directory, row, 1)
            row += 1
            grid.addWidget(QLabel('Camera index'), row, 0)
            grid.addWidget(self.camera_index, row, 1)
            row += 1
        else:
            grid.addWidget(QLabel('Resource'), row, 0)
            grid.addWidget(self.resource_edit, row, 1)
            row += 1
            grid.addWidget(QLabel('COM port'),row,0);grid.addWidget(self.port_combo,row,1);row+=1
            grid.addWidget(QLabel('Baudrate'),row,0);grid.addWidget(self.baud_combo,row,1);row+=1
        layout.addLayout(grid)
        buttons = QHBoxLayout()
        self.find_button = QPushButton('Find controllers' if family != 'spectrometer' else 'Refresh drivers')
        self.connect_button = QPushButton('Connect')
        self.connect_button.setProperty('role', 'primary')
        self.disconnect_button = QPushButton('Disconnect')
        buttons.addWidget(self.find_button)
        buttons.addStretch(1)
        buttons.addWidget(self.disconnect_button)
        buttons.addWidget(self.connect_button)
        layout.addLayout(buttons)
        layout.addWidget(self.message)
        self.find_button.clicked.connect(self._find)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.driver_combo.currentIndexChanged.connect(self._driver_changed)
        self._load()
    def _request(self, name, data, success, failure=None):
        if self.controller is None:
            self.message.setText('Controller is not available')
            return
        self.controller.dispatch(name, data, success=success, failure=failure or self._failed)
    def _failed(self, payload):
        self.message.setText(payload_message(payload))
    def _load(self):
        self._request('device_drivers', {}, self._drivers_loaded)
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
        self.driver_combo.blockSignals(False)
        config=((self.controller.snapshot.get('devices') or {}).get(self.family) or {}).get('config') or {}
        index=self.driver_combo.findData(config.get('driver'))
        if index>=0:
            self.driver_combo.blockSignals(True);self.driver_combo.setCurrentIndex(index);self.driver_combo.blockSignals(False)
        self._driver_changed()
        for key,edit in [('model',self.model_edit),('resource',self.resource_edit),('pi_dll',self.pi_dll_edit),('dll_directory',self.dll_directory),('axis',self.axis_edit)]:
            if config.get(key):edit.setText(str(config[key]))
        for key,combo in [('port',self.port_combo),('usb_device',self.usb_combo),('baudrate',self.baud_combo)]:
            if config.get(key):combo.setCurrentText(str(config[key]))
        if config.get('transport'):
            index=self.transport_combo.findData(config['transport'])
            if index>=0:self.transport_combo.setCurrentIndex(index)
        self.camera_index.setValue(int(config.get('camera_index',0)))
    def _ports_loaded(self, result):
        selection=self.port_combo.currentData() or self.port_combo.currentText()
        ports = result.get('ports', []) if isinstance(result, dict) else (result or [])
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for item in ports:
            if isinstance(item, dict):
                self.port_combo.addItem(item.get('label') or item.get('port'), item.get('port'))
            else:
                self.port_combo.addItem(str(item), str(item))
        self.port_combo.blockSignals(False)
        if selection:self.port_combo.setCurrentText(selection)
        error = result.get('error') if isinstance(result, dict) else None
        if error:
            self.message.setText(str(error))
    def _driver_changed(self):
        index = self.driver_combo.currentIndex()
        if index < 0 or index >= len(self.drivers):
            return
        driver = self.drivers[index]
        transports = driver.get('transports') or []
        self.transport_combo.clear()
        for transport in transports:
            self.transport_combo.addItem(str(transport), str(transport))
        defaults = driver.get('defaults') or {}
        transport = defaults.get('transport')
        if transport:
            at = self.transport_combo.findData(transport)
            if at >= 0:
                self.transport_combo.setCurrentIndex(at)
        self.model_edit.setText(str(defaults.get('model') or driver.get('label') or ''))
        self.axis_edit.setText(str(defaults.get('axis') or '1'))
        self.baud_combo.setCurrentText(str(defaults.get('baudrate') or '38400'))
        self.message.setText(str(driver.get('note') or ''))
    def _find(self):
        if self.family == 'stage':
            self._request('device_pi_usb', {'pi_dll': self.pi_dll_edit.text().strip()}, self._usb_loaded)
        elif self.family == 'camera':
            self._request('device_cameras', {'dll_directory': self.dll_directory.text().strip()}, self._cameras_loaded)
        else:
            self._load()
    def _usb_loaded(self, result):
        devices = result.get('devices', []) if isinstance(result, dict) else (result or [])
        self.usb_combo.clear()
        for item in devices:
            if isinstance(item, dict):
                self.usb_combo.addItem(item.get('label') or item.get('serial') or str(item), item.get('serial') or item.get('id'))
            else:
                self.usb_combo.addItem(str(item), str(item))
        self.message.setText(f'Found {self.usb_combo.count()} USB controller(s)')
    def _cameras_loaded(self, result):
        cameras = result.get('cameras', []) if isinstance(result, dict) else (result or [])
        if isinstance(cameras, list) and cameras:
            self.camera_index.setMaximum(max(0, len(cameras) - 1))
        self.message.setText(f'Found {len(cameras) if cameras else 0} SDK2 camera(s)')
    def _payload(self):
        driver = self.driver_combo.currentData()
        if not driver:
            raise ValueError('Select a driver first')
        transport = self.transport_combo.currentData() or self.transport_combo.currentText() or 'usb'
        data = {'device': self.family, 'driver': str(driver), 'transport': str(transport)}
        if self.model_edit.text().strip():
            data['model'] = self.model_edit.text().strip()
        if self.resource_edit.text().strip():
            data['resource'] = self.resource_edit.text().strip()
        if self.family == 'stage':
            data['usb_device'] = combo_value(self.usb_combo)
            data['pi_dll'] = self.pi_dll_edit.text().strip()
            data['port'] = combo_value(self.port_combo)
            try:
                data['baudrate'] = int(self.baud_combo.currentText())
            except Exception:
                data['baudrate'] = 38400
            data['axis'] = self.axis_edit.text().strip() or '1'
        elif self.family == 'camera':
            data['dll_directory'] = self.dll_directory.text().strip()
            data['camera_index'] = int(self.camera_index.value())
        elif self.family=='spectrometer' and transport=='serial':
            data['port']=combo_value(self.port_combo)
            data['baudrate']=int(self.baud_combo.currentText())
        return data
    def _connect(self):
        try:
            data = self._payload()
        except Exception as error:
            self.message.setText(str(error))
            return
        self.connect_button.setEnabled(False)
        self._request('device_connect', data, self._connected, self._connect_failed)
    def _connected(self, result):
        self.connect_button.setEnabled(True)
        self.message.setText('Connected')
        self.changed.emit()
        if self.controller is not None:
            self.controller.request_status()
    def _connect_failed(self, payload):
        self.connect_button.setEnabled(True)
        self.message.setText(payload_message(payload))
        self.changed.emit()
    def _disconnect(self):
        self.disconnect_button.setEnabled(False)
        self._request('device_disconnect', {'device': self.family}, self._disconnected, self._disconnect_failed)
    def _disconnected(self, result):
        self.disconnect_button.setEnabled(True)
        self.message.setText('Disconnected')
        self.changed.emit()
        if self.controller is not None:
            self.controller.request_status()
    def _disconnect_failed(self, payload):
        self.disconnect_button.setEnabled(True)
        self.message.setText(payload_message(payload))


class DeviceDialog(QDialog):
    def __init__(self, controller, family, snapshot=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle({'stage':'Delay stage connection','camera':'CCD camera connection','spectrometer':'Spectrometer connection'}.get(family, 'Device connection'))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self.card = DeviceCard(controller, family)
        layout.addWidget(self.card)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)


class DetectorDialog(QDialog):
    def __init__(self, controller, snapshot=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Detection connections')
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.camera_card = DeviceCard(controller, 'camera')
        self.spectrometer_card = DeviceCard(controller, 'spectrometer')
        tabs.addTab(self.camera_card, 'CCD camera')
        tabs.addTab(self.spectrometer_card, 'Spectrometer')
        layout.addWidget(tabs)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
