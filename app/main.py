import csv
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QSpinBox, QTextEdit, QGroupBox, QCheckBox, QFileDialog,
    QTabWidget
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
    write_protect: bool = False
    notes: str = ''
    value: Optional[float] = None
    online_value: Optional[float] = None
    user_modified: bool = False

    @property
    def effective_value(self):
        return self.value if self.value is not None else self.default


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
                    write_protect=str(r.get('write_protect') or 'FALSE').strip().upper() in ('TRUE','1','YES','Y'),
                    notes=(r.get('notes') or '').strip()
                ))

    def filtered(self, search_text: str = '', monitor_only: bool = False):
        s = (search_text or '').strip().lower()
        out = []
        for p in self.params:
            if monitor_only and not p.monitor:
                continue
            if s and not (s in p.code.lower() or s in p.name.lower() or s in str(p.address)):
                continue
            out.append(p)
        return out

    def by_code(self, code: str):
        code = code.upper()
        for p in self.params:
            if p.code.upper() == code:
                return p
        return None


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

    def _unit_kwargs(self):
        return [{'slave': self.unit_id}, {'unit': self.unit_id}, {'device_id': self.unit_id}, {}]

    def read_registers(self, address: int, count: int = 1):
        address = self._addr(address)
        last_error = None
        for kwargs in self._unit_kwargs():
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
        last_error = None
        # FC06 first
        for kwargs in self._unit_kwargs():
            try:
                wr = self.client.write_register(address=address, value=value, **kwargs)
                if wr.isError():
                    raise RuntimeError(str(wr))
                return
            except TypeError as e:
                last_error = e
                continue
            except Exception as e:
                last_error = e
                break
        # FC16 fallback for one register
        for kwargs in self._unit_kwargs():
            try:
                wr = self.client.write_registers(address=address, values=[value], **kwargs)
                if wr.isError():
                    raise RuntimeError(str(wr))
                return
            except TypeError as e:
                last_error = e
                continue
            except Exception as e:
                last_error = e
                break
        raise RuntimeError(f'Write failed using FC06 and FC16: {last_error}')

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
        self.setWindowTitle('LK XD4000 Phase-2 Safety + Monitoring Tester')
        self.resize(1320, 800)
        self.db = ParameterDB()
        self.gateway = ModbusGateway()
        self.params: List[Parameter] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.monitor_tick)
        self._build_ui()
        self._load_default_db()

    def log(self, message: str):
        self.logbox.append(f'[{time.strftime("%H:%M:%S")}] {message}')

    def _load_default_db(self):
        path = resource_path(os.path.join('data', 'xd4000_phase2_parameters.csv'))
        if not os.path.exists(path):
            self.log('Built-in CSV database not found')
            return
        self.db.load_csv(path)
        self.refresh_params()
        self.log(f'Loaded XD4000 Phase-2 database: {len(self.db.params)} parameters')

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        comm = QGroupBox('XD4000 / ATV930 - Modbus TCP')
        grid = QGridLayout(comm)
        self.host = QLineEdit('192.168.1.10')
        self.tcp_port = QSpinBox(); self.tcp_port.setRange(1, 65535); self.tcp_port.setValue(502)
        self.unit = QSpinBox(); self.unit.setRange(1, 255); self.unit.setValue(1)
        self.zero_based = QCheckBox('Use zero-based address (-1)')
        self.search = QLineEdit(); self.search.setPlaceholderText('Search code/name/address')
        self.search.textChanged.connect(self.refresh_params)
        self.monitor_only = QCheckBox('Monitor only')
        self.monitor_only.stateChanged.connect(self.refresh_params)
        fields = [('Drive IP', self.host), ('TCP Port', self.tcp_port), ('Unit ID', self.unit), ('Address option', self.zero_based), ('Search', self.search), ('Filter', self.monitor_only)]
        for i, (label, widget) in enumerate(fields):
            grid.addWidget(QLabel(label), 0, i)
            grid.addWidget(widget, 1, i)
        for i, (txt, fn) in enumerate([
            ('Connect', self.connect_drive), ('Disconnect', self.disconnect_drive),
            ('Upload visible', self.upload_visible), ('Download selected row', self.download_selected),
            ('Download modified RW', self.download_modified), ('Export Event Log', self.export_log)
        ]):
            b = QPushButton(txt); b.clicked.connect(fn); grid.addWidget(b, 2, i)
        root.addWidget(comm)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.table = QTableWidget()
        self.tabs.addTab(self.table, 'Parameters')

        cmd = QWidget()
        cmd_layout = QVBoxLayout(cmd)
        self.expert = QCheckBox('Expert test mode: bench setup confirmed, output terminals safe')
        cmd_layout.addWidget(self.expert)
        cmd_layout.addWidget(QLabel('Safety note: raw CMD@8501 writes are locked in this Phase-2 safety build. Use this tab for status checks and safe reference validation only.'))
        btnrow = QHBoxLayout()
        for txt, fn in [('Read Status ETA/RFR/FRH/LFR', self.read_command_status), ('Set LFR to 0.0 Hz', self.set_lfr_zero), ('Start Monitor', self.start_monitor), ('Stop Monitor', self.stop_monitor)]:
            b = QPushButton(txt); b.clicked.connect(fn); btnrow.addWidget(b)
        btnrow.addStretch()
        cmd_layout.addLayout(btnrow)
        self.command_status = QTextEdit(); self.command_status.setReadOnly(True)
        cmd_layout.addWidget(self.command_status)
        self.tabs.addTab(cmd, 'Command Safety Test')

        self.logbox = QTextEdit(); self.logbox.setReadOnly(True)
        self.tabs.addTab(self.logbox, 'Event Log')

    def refresh_params(self):
        self.params = self.db.filtered(self.search.text() if hasattr(self, 'search') else '', self.monitor_only.isChecked() if hasattr(self, 'monitor_only') else False)
        self.populate_table()

    def populate_table(self):
        headers = ['Code','Name','Address','Type','Scale','Default','Offline Value','Online Value','Unit','Access','Write Protect','Monitor','Notes']
        self.table.blockSignals(True)
        self.table.setColumnCount(len(headers)); self.table.setHorizontalHeaderLabels(headers); self.table.setRowCount(len(self.params))
        for r, p in enumerate(self.params):
            vals = [p.code, p.name, p.address, p.datatype, p.scale, p.default, p.effective_value,
                    '' if p.online_value is None else round(p.online_value, 4), p.unit, p.access,
                    'Yes' if p.write_protect else 'No', 'Yes' if p.monitor else 'No', p.notes]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 6 and p.access == 'RW' and not p.write_protect:
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
            p.user_modified = True
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
        self.stop_monitor()
        self.gateway.close()
        self.log('Disconnected')

    def upload_one(self, p: Parameter):
        p.online_value = self.gateway.read_param(p)
        p.value = p.online_value
        p.user_modified = False
        self.log(f'Upload OK {p.code}@{p.address} = {p.online_value} {p.unit}')
        return p.online_value

    def upload_visible(self):
        if not self.gateway.is_connected():
            QMessageBox.warning(self, 'Not connected', 'Connect first')
            return
        ok = 0; fail = 0
        for p in self.params:
            try:
                self.upload_one(p); ok += 1
            except Exception as e:
                self.log(f'Upload failed {p.code}@{p.address}: {e}'); fail += 1
        self.populate_table()
        self.log(f'Upload complete. OK={ok}, Failed={fail}')

    def write_with_retry_and_readback(self, p: Parameter):
        if p.write_protect and not self.expert.isChecked():
            raise RuntimeError(f'{p.code} is write-protected. Expert mode required.')
        if p.code.upper() == 'CMD':
            raise RuntimeError('CMD raw command writes are disabled in this safety build.')
        written = False; last_error = None
        for attempt in range(1, 4):
            try:
                self.gateway.write_param(p, p.effective_value)
                written = True
                self.log(f'Download OK {p.code}@{p.address} = {p.effective_value} {p.unit} on attempt {attempt}')
                break
            except Exception as retry_error:
                last_error = retry_error
                self.log(f'Download retry {attempt} failed {p.code}@{p.address}: {retry_error}')
                time.sleep(0.75 * attempt)
        if not written:
            raise last_error
        readback = self.gateway.read_param(p)
        p.online_value = readback
        p.value = readback
        p.user_modified = False
        self.log(f'Readback OK {p.code}@{p.address} = {readback} {p.unit}')

    def selected_param(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.params):
            return None
        return self.params[row]

    def download_selected(self):
        if not self.gateway.is_connected():
            QMessageBox.warning(self, 'Not connected', 'Connect first'); return
        p = self.selected_param()
        if not p:
            QMessageBox.warning(self, 'No row selected', 'Select one parameter row first'); return
        if p.access != 'RW':
            QMessageBox.warning(self, 'Read-only', f'{p.code} is read-only'); return
        reply = QMessageBox.question(self, 'Confirm selected write', f'Write {p.code}@{p.address} = {p.effective_value} {p.unit}?')
        if reply != QMessageBox.Yes:
            self.log('Selected write cancelled'); return
        try:
            self.write_with_retry_and_readback(p)
            self.populate_table()
        except Exception as e:
            self.log(f'Selected write failed {p.code}@{p.address}: {e}')

    def download_modified(self):
        if not self.gateway.is_connected():
            QMessageBox.warning(self, 'Not connected', 'Connect first'); return
        reply = QMessageBox.question(self, 'Confirm parameter download', 'This will write modified RW parameters. Continue?')
        if reply != QMessageBox.Yes:
            self.log('Download cancelled by user'); return
        ok = 0; fail = 0
        for p in self.params:
            offline_differs_from_online = (p.online_value is not None and abs(float(p.effective_value) - float(p.online_value)) > 1e-9)
            if p.access == 'RW' and (p.user_modified or offline_differs_from_online):
                try:
                    self.write_with_retry_and_readback(p); ok += 1
                except Exception as e:
                    self.log(f'Download failed {p.code}@{p.address}: {e}'); fail += 1
        self.populate_table()
        self.log(f'Download complete. OK={ok}, Failed={fail}')
        if ok == 0 and fail == 0:
            self.log('No user-modified RW parameter found for download')

    def read_command_status(self):
        if not self.gateway.is_connected():
            QMessageBox.warning(self, 'Not connected', 'Connect first'); return
        codes = ['ETA','RFR','FRH','LFR']
        lines=[]
        for code in codes:
            p = self.db.by_code(code)
            if p:
                try:
                    v = self.gateway.read_param(p)
                    p.online_value = v; p.value = v
                    lines.append(f'{code}@{p.address} = {v} {p.unit}')
                except Exception as e:
                    lines.append(f'{code}@{p.address} failed: {e}')
        text='\n'.join(lines)
        self.command_status.append(f'[{time.strftime("%H:%M:%S")}]\n{text}\n')
        self.log('Command status read completed')
        self.refresh_params()

    def set_lfr_zero(self):
        p = self.db.by_code('LFR')
        if not p:
            return
        p.value = 0.0
        p.user_modified = True
        self.search.setText('LFR')
        self.refresh_params()
        self.log('Prepared LFR offline value = 0.0 Hz. Use Download selected row to write if safe.')

    def start_monitor(self):
        if not self.gateway.is_connected():
            QMessageBox.warning(self, 'Not connected', 'Connect first'); return
        self.timer.start(1000)
        self.log('Status monitor started at 1 s interval')

    def stop_monitor(self):
        if self.timer.isActive():
            self.timer.stop()
            self.log('Status monitor stopped')

    def monitor_tick(self):
        self.read_command_status()

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export event log', 'xd4000_event_log.txt', 'Text Files (*.txt)')
        if not path:
            return
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.logbox.toPlainText())
        self.log(f'Event log exported: {path}')


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
