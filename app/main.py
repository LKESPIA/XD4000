import csv
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QSpinBox, QTextEdit, QGroupBox, QCheckBox, QFileDialog
)

try:
    from pymodbus.client import ModbusTcpClient
except Exception:
    ModbusTcpClient = None


def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, '_MEIPASS', os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    return os.path.join(base_path, relative_path)


def safe_float(value, default=0.0):
    try:
        if value is None or str(value).strip() == '':
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


@dataclass
class Parameter:
    model: str
    reference: str
    code: str
    name: str
    address: int
    datatype: str
    scale: float
    default: float
    min: float
    max: float
    unit: str
    access: str
    monitor: bool
    notes: str = ''
    value: Optional[float] = None
    online_value: Optional[float] = None
    user_modified: bool = False
    
    @property
    def effective_value(self):
        return self.value if self.value is not None else self.default

    @property
    def modified(self):
        try:
            return abs(float(self.effective_value) - float(self.default)) > 1e-9
        except Exception:
            return False


class ParameterDB:
    def __init__(self):
        self.params: List[Parameter] = []

    def load_csv(self, path: str):
        self.params = []
        with open(path, newline='', encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                self.params.append(Parameter(
                    model=(r.get('model') or 'XD4000').strip(),
                    reference=(r.get('reference') or 'ALL').strip(),
                    code=(r.get('code') or '').strip(),
                    name=(r.get('name') or '').strip(),
                    address=safe_int(r.get('address'), 0),
                    datatype=(r.get('datatype') or 'uint16').strip().lower(),
                    scale=safe_float(r.get('scale'), 1.0),
                    default=safe_float(r.get('default'), 0.0),
                    min=safe_float(r.get('min'), -32768.0),
                    max=safe_float(r.get('max'), 65535.0),
                    unit=(r.get('unit') or '').strip(),
                    access=(r.get('access') or 'RO').strip().upper(),
                    monitor=str(r.get('monitor') or 'FALSE').strip().upper() in ('TRUE','1','YES','Y'),
                    notes=(r.get('notes') or '').strip()
                ))

    def filtered(self, search_text: str = ''):
        s = (search_text or '').strip().lower()
        if not s:
            return list(self.params)
        return [p for p in self.params if s in p.code.lower() or s in p.name.lower() or s in str(p.address)]


class ModbusGateway:
    def __init__(self):
        self.client = None
        self.unit_id = 1
        self.address_offset = 0

    def connect_tcp(self, host: str, port: int, unit_id: int, zero_based: bool = False):
        if ModbusTcpClient is None:
            raise RuntimeError('pymodbus is not installed')
        self.unit_id = unit_id
        self.address_offset = -1 if zero_based else 0
        self.client = ModbusTcpClient(host=host, port=port, timeout=3)
        if not self.client.connect():
            raise RuntimeError('Could not connect to Modbus TCP device')

    def close(self):
        if self.client:
            self.client.close()
        self.client = None

    def is_connected(self):
        return self.client is not None

    def _addr(self, address: int):
        final_address = address + self.address_offset
        if final_address < 0:
            raise RuntimeError(f'Invalid Modbus address after offset: {final_address}')
        return final_address

    def read_registers(self, address: int, count: int = 1):
        address = self._addr(address)
        # Compatible with pymodbus variants: slave, unit, device_id, or no unit keyword.
        attempts = [
            {'slave': self.unit_id},
            {'unit': self.unit_id},
            {'device_id': self.unit_id},
            {},
        ]
        last_error = None
        for kwargs in attempts:
            try:
                rr = self.client.read_holding_registers(address=address, count=count, **kwargs)
                if rr.isError():
                    raise RuntimeError(str(rr))
                return rr.registers
            except TypeError as e:
                last_error = e
                continue
        raise RuntimeError(f'Could not call read_holding_registers with installed pymodbus API: {last_error}')

    def write_register(self, address: int, value: int):
        address = self._addr(address)
        attempts = [
            {'slave': self.unit_id},
            {'unit': self.unit_id},
            {'device_id': self.unit_id},
            {},
        ]
        last_error = None
        for kwargs in attempts:
            try:
                wr = self.client.write_register(address=address, value=value, **kwargs)
                if wr.isError():
                    raise RuntimeError(str(wr))
                return
            except TypeError as e:
                last_error = e
                continue
        raise RuntimeError(f'Could not call write_register with installed pymodbus API: {last_error}')

    def read_param(self, p: Parameter):
        count = 2 if p.datatype in ('uint32','int32') else 1
        regs = self.read_registers(p.address, count)
        if p.datatype == 'int16':
            raw = regs[0] if regs[0] < 32768 else regs[0] - 65536
        elif p.datatype == 'uint32':
            raw = (regs[0] << 16) + regs[1]
        elif p.datatype == 'int32':
            raw = (regs[0] << 16) + regs[1]
            if raw >= 2147483648:
                raw -= 4294967296
        else:
            raw = regs[0]
        return raw * p.scale

    def write_param(self, p: Parameter, engineering_value: float):
        if p.access != 'RW':
            raise RuntimeError(f'{p.code} is read-only')
        raw = int(round(engineering_value / (p.scale if p.scale else 1)))
        if p.datatype == 'int16' and raw < 0:
            raw = 65536 + raw
        if p.datatype in ('uint32','int32'):
            self.write_register(p.address, (raw >> 16) & 0xFFFF)
            self.write_register(p.address + 1, raw & 0xFFFF)
        else:
            self.write_register(p.address, raw & 0xFFFF)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('LK XD4000 Basic Modbus TCP Tester')
        self.resize(1180, 720)
        self.db = ParameterDB()
        self.gateway = ModbusGateway()
        self.params: List[Parameter] = []
        self._build_ui()
        self._load_default_db()

    def log(self, message: str):
        self.logbox.append(f'[{time.strftime("%H:%M:%S")}] {message}')

    def _load_default_db(self):
        path = resource_path(os.path.join('data', 'xd4000_basic_parameters.csv'))
        if not os.path.exists(path):
            self.log('Built-in CSV database not found')
            return
        self.db.load_csv(path)
        self.refresh_params()
        self.log(f'Loaded XD4000 basic database: {len(self.db.params)} parameters')

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)

        top = QGroupBox('XD4000 / ATV930 - Basic Modbus TCP Communication Test')
        grid = QGridLayout(top)
        self.host = QLineEdit('192.168.1.10')
        self.tcp_port = QSpinBox(); self.tcp_port.setRange(1, 65535); self.tcp_port.setValue(502)
        self.unit = QSpinBox(); self.unit.setRange(1, 255); self.unit.setValue(1)
        self.zero_based = QCheckBox('Use zero-based address (-1)')
        self.search = QLineEdit(); self.search.setPlaceholderText('Search code/name/address, e.g. 3201 or ETA')
        self.search.textChanged.connect(self.refresh_params)
        fields = [
            ('Drive IP', self.host), ('TCP Port', self.tcp_port), ('Unit ID', self.unit),
            ('Address option', self.zero_based), ('Search', self.search)
        ]
        for i, (label, widget) in enumerate(fields):
            grid.addWidget(QLabel(label), 0, i)
            grid.addWidget(widget, 1, i)
        self.connect_btn = QPushButton('Connect')
        self.connect_btn.clicked.connect(self.connect_drive)
        self.disconnect_btn = QPushButton('Disconnect')
        self.disconnect_btn.clicked.connect(self.disconnect_drive)
        self.upload_btn = QPushButton('Upload visible parameters')
        self.upload_btn.clicked.connect(self.upload_visible)
        self.download_btn = QPushButton('Download modified RW parameters')
        self.download_btn.clicked.connect(self.download_modified)
        self.save_btn = QPushButton('Save project')
        self.save_btn.clicked.connect(self.save_project)
        for i, b in enumerate([self.connect_btn, self.disconnect_btn, self.upload_btn, self.download_btn, self.save_btn]):
            grid.addWidget(b, 2, i)
        main.addWidget(top)

        self.table = QTableWidget()
        main.addWidget(self.table, 1)
        self.logbox = QTextEdit(); self.logbox.setReadOnly(True)
        main.addWidget(QLabel('Event Log'))
        main.addWidget(self.logbox, 0)

    def refresh_params(self):
        self.params = self.db.filtered(self.search.text() if hasattr(self, 'search') else '')
        self.populate_table()

    def populate_table(self):
        headers = ['Code','Name','Address','Type','Scale','Default','Offline Value','Online Value','Unit','Access','Monitor','Notes']
        self.table.blockSignals(True)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.params))
        for r, p in enumerate(self.params):
            vals = [p.code, p.name, p.address, p.datatype, p.scale, p.default, p.effective_value,
                    '' if p.online_value is None else round(p.online_value, 4), p.unit, p.access,
                    'Yes' if p.monitor else 'No', p.notes]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 6 and p.access == 'RW':
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        try:
            self.table.itemChanged.disconnect()
        except Exception:
            pass
        self.table.itemChanged.connect(self.on_value_edited)
        self.table.resizeColumnsToContents()

    def on_value_edited(self, item):
        if item.column() != 6 or item.row() >= len(self.params):
            return
        p = self.params[item.row()]
        try:
            val = float(item.text())
            if not (p.min <= val <= p.max):
                raise ValueError(f'Allowed range: {p.min} to {p.max}')
            p.value = val
            self.log(f'Offline value changed: {p.code} = {val}')
        except Exception as e:
            QMessageBox.warning(self, 'Invalid value', str(e))

    def connect_drive(self):
        try:
            self.gateway.connect_tcp(self.host.text().strip(), self.tcp_port.value(), self.unit.value(), self.zero_based.isChecked())
            self.log(f'Connected successfully to {self.host.text().strip()}:{self.tcp_port.value()}, Unit ID={self.unit.value()}, zero_based={self.zero_based.isChecked()}')
        except Exception as e:
            self.log(f'Connection failed: {e}')
            QMessageBox.critical(self, 'Connection failed', str(e))

    def disconnect_drive(self):
        self.gateway.close()
        self.log('Disconnected')

    def upload_visible(self):
        if not self.gateway.is_connected():
            QMessageBox.warning(self, 'Not connected', 'Connect first')
            return
        ok = 0; fail = 0
        for p in self.params:
            try:
                p.online_value = self.gateway.read_param(p)
                p.value = p.online_value
                self.log(f'Upload OK {p.code}@{p.address} = {p.online_value} {p.unit}')
                ok += 1
            except Exception as e:
                self.log(f'Upload failed {p.code}@{p.address}: {e}')
                fail += 1
        self.populate_table()
        self.log(f'Upload complete. OK={ok}, Failed={fail}')

    def download_modified(self):
        if not self.gateway.is_connected():
            QMessageBox.warning(self, 'Not connected', 'Connect first')
            return
        reply = QMessageBox.question(
            self,
            'Confirm parameter download',
            'This will write modified RW parameters to the drive. Use only on bench/safe setup. Continue?'
        )
        if reply != QMessageBox.Yes:
            self.log('Download cancelled by user')
            return
        ok = 0; fail = 0
        for p in self.params:
            if p.access == 'RW' and p.modified:
                try:
                    self.gateway.write_param(p, p.effective_value)
                    self.log(f'Download OK {p.code}@{p.address} = {p.effective_value} {p.unit}')
                    ok += 1
                except Exception as e:
                    self.log(f'Download failed {p.code}@{p.address}: {e}')
                    fail += 1
        self.log(f'Download complete. OK={ok}, Failed={fail}')

    def save_project(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save project', '', 'JSON Files (*.json)')
        if not path:
            return
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([asdict(p) for p in self.params], f, indent=2)
        self.log(f'Saved project: {path}')


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
