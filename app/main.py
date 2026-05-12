
import csv, os, sys, time
from dataclasses import dataclass
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QGridLayout,QLabel,QLineEdit,QPushButton,QTableWidget,QTableWidgetItem,QMessageBox,QSpinBox,QTextEdit,QGroupBox,QCheckBox,QFileDialog,QTabWidget,QComboBox
try:
    from pymodbus.client import ModbusTcpClient
except Exception: ModbusTcpClient=None
try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill
except Exception: Workbook=load_workbook=None
CATEGORIES=['Drive Identification','Communication','Command and Reference','Monitoring','Faults and Diagnostics','Motor Setup','Application Functions','Protection and Limits','Ramp and Motion Profile','Input Output Configuration','Maintenance and Service']
def resource_path(p): return os.path.join(getattr(sys,'_MEIPASS',os.path.abspath(os.path.join(os.path.dirname(__file__),'..'))),p)
def sf(v,d=0.0):
    try: return d if v is None or str(v).strip()=='' else float(v)
    except Exception: return d
def si(v,d=0):
    try: return int(float(v))
    except Exception: return d
@dataclass
class P:
    code:str; name:str; address:int; datatype:str; scale:float; default:float; min:float; max:float; unit:str; access:str; monitor:bool; wp:bool; group:str; sub:str; policy:str; value:Optional[float]=None; online:Optional[float]=None; mod:bool=False
    @property
    def eff(self): return self.value if self.value is not None else self.default
class DB:
    def __init__(self): self.params=[]
    def load(self,path):
        self.params=[]
        with open(path,newline='',encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                self.params.append(P(r.get('code',''),r.get('name',''),si(r.get('address')),r.get('datatype','uint16'),sf(r.get('scale'),1),sf(r.get('default')),sf(r.get('min')),sf(r.get('max'),65535),r.get('unit',''),r.get('access','RO'),str(r.get('monitor','')).upper()=='TRUE',str(r.get('write_protect','')).upper()=='TRUE',r.get('group','Monitoring'),r.get('subcategory',''),r.get('write_policy','')))
    def filt(self,s='',g='All',mon=False):
        s=(s or '').lower(); out=[]
        for p in self.params:
            if mon and not p.monitor: continue
            if g!='All' and p.group!=g: continue
            if s and not (s in p.code.lower() or s in p.name.lower() or s in str(p.address) or s in p.group.lower()): continue
            out.append(p)
        return out
class GW:
    def __init__(self): self.client=None; self.unit=1; self.off=0
    def connect(self,host,port,unit,zero):
        if ModbusTcpClient is None: raise RuntimeError('pymodbus not installed')
        self.unit=unit; self.off=-1 if zero else 0; self.client=ModbusTcpClient(host=host,port=port,timeout=3)
        if not self.client.connect(): raise RuntimeError('Could not connect')
    def close(self):
        if self.client: self.client.close()
        self.client=None
    def ok(self): return self.client is not None
    def kw(self): return [{'slave':self.unit},{'unit':self.unit},{'device_id':self.unit},{}]
    def rr(self,a,c=1):
        last=None
        for k in self.kw():
            try:
                r=self.client.read_holding_registers(address=a+self.off,count=c,**k)
                if r.isError(): raise RuntimeError(str(r))
                return r.registers
            except TypeError as e: last=e
        raise RuntimeError(last)
    def wr(self,a,v):
        last=None
        for k in self.kw():
            try:
                r=self.client.write_register(address=a+self.off,value=int(v)&0xffff,**k)
                if r.isError(): raise RuntimeError(str(r))
                return
            except TypeError as e: last=e
        raise RuntimeError(last)
    def readp(self,p):
        regs=self.rr(p.address,2 if p.datatype in ('uint32','int32') else 1)
        if p.datatype=='int16': raw=regs[0] if regs[0]<32768 else regs[0]-65536
        elif p.datatype=='uint32': raw=(regs[0]<<16)+regs[1]
        elif p.datatype=='int32':
            raw=(regs[0]<<16)+regs[1]; raw=raw-4294967296 if raw>=2147483648 else raw
        else: raw=regs[0]
        return raw*p.scale
    def writep(self,p,val):
        raw=int(round(val/(p.scale or 1)))
        if p.datatype=='int16' and raw<0: raw=65536+raw
        self.wr(p.address,raw)
class W(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('LK XD4000 Phase-5D Full Parameter Manager'); self.resize(1550,900); self.db=DB(); self.gw=GW(); self.params=[]; self.ui(); self.db.load(resource_path('data/xd4000_phase5d_full_parameters.csv')); self.refresh(); self.log(f'Loaded full parameter database: {len(self.db.params)} parameters')
    def ui(self):
        c=QWidget(); self.setCentralWidget(c); root=QVBoxLayout(c); box=QGroupBox('XD4000 Full Parameter Manager - Modbus TCP'); grid=QGridLayout(box)
        self.host=QLineEdit('192.168.1.10'); self.port=QSpinBox(); self.port.setRange(1,65535); self.port.setValue(502); self.unit=QSpinBox(); self.unit.setRange(1,255); self.unit.setValue(1); self.zero=QCheckBox('Zero-based address (-1)')
        self.search=QLineEdit(); self.search.setPlaceholderText('Search code/name/address/group'); self.search.textChanged.connect(self.refresh); self.group=QComboBox(); self.group.addItems(['All']+CATEGORIES); self.group.currentTextChanged.connect(self.refresh); self.mon=QCheckBox('Monitor only'); self.mon.stateChanged.connect(self.refresh)
        for i,(l,w) in enumerate([('Drive IP',self.host),('Port',self.port),('Unit ID',self.unit),('Address',self.zero),('Search',self.search),('Group',self.group),('Filter',self.mon)]): grid.addWidget(QLabel(l),0,i); grid.addWidget(w,1,i)
        for i,(t,f) in enumerate([('Connect',self.connect),('Disconnect',self.disconnect),('Upload visible',self.upload),('Download selected row',self.download),('Export project Excel',self.export_xlsx),('Import project Excel',self.import_xlsx)]):
            b=QPushButton(t); b.clicked.connect(f); grid.addWidget(b,2,i)
        root.addWidget(box); self.tabs=QTabWidget(); root.addWidget(self.tabs,1); self.table=QTableWidget(); self.tabs.addTab(self.table,'Parameters'); self.logbox=QTextEdit(); self.logbox.setReadOnly(True); self.tabs.addTab(self.logbox,'Event Log')
        self.setStyleSheet('QPushButton{background:#008CD7;color:white;border-radius:7px;min-height:32px;font-weight:bold} QGroupBox{background:white;border:1px solid #ccc;border-radius:8px;margin-top:8px;padding:8px} QMainWindow{background:#EFEFEF}')
    def log(self,m): self.logbox.append(f'[{time.strftime("%H:%M:%S")}] {m}')
    def refresh(self): self.params=self.db.filt(self.search.text() if hasattr(self,'search') else '', self.group.currentText() if hasattr(self,'group') else 'All', self.mon.isChecked() if hasattr(self,'mon') else False); self.populate()
    def populate(self):
        heads=['Code','Group','Subcategory','Name','Address','Type','Scale','Offline','Online','Unit','Access','Protect','Policy']; self.table.blockSignals(True); self.table.setColumnCount(len(heads)); self.table.setHorizontalHeaderLabels(heads); self.table.setRowCount(len(self.params))
        for r,p in enumerate(self.params):
            vals=[p.code,p.group,p.sub,p.name,p.address,p.datatype,p.scale,p.eff,'' if p.online is None else p.online,p.unit,p.access,'Yes' if p.wp else 'No',p.policy]
            for col,v in enumerate(vals):
                it=QTableWidgetItem(str(v)); it.setFlags((it.flags()|Qt.ItemIsEditable) if col==7 and p.access=='RW' and not p.wp else (it.flags()&~Qt.ItemIsEditable)); self.table.setItem(r,col,it)
        self.table.blockSignals(False)
        try: self.table.itemChanged.disconnect()
        except Exception: pass
        self.table.itemChanged.connect(self.edit); self.table.resizeColumnsToContents()
    def edit(self,item):
        if item.column()!=7: return
        p=self.params[item.row()]
        try:
            v=float(item.text());
            if not(p.min<=v<=p.max): raise ValueError(f'Allowed range {p.min} to {p.max}')
            p.value=v; p.mod=True; self.log(f'Offline changed {p.code}={v}')
        except Exception as e: QMessageBox.warning(self,'Invalid value',str(e))
    def connect(self):
        try: self.gw.connect(self.host.text().strip(),self.port.value(),self.unit.value(),self.zero.isChecked()); self.log('Connected successfully')
        except Exception as e: QMessageBox.critical(self,'Connection failed',str(e)); self.log(f'Connection failed: {e}')
    def disconnect(self): self.gw.close(); self.log('Disconnected')
    def upload(self):
        if not self.gw.ok(): QMessageBox.warning(self,'Not connected','Connect first'); return
        ok=fail=0
        for p in self.params:
            try: p.online=self.gw.readp(p); p.value=p.online if not p.mod else p.value; ok+=1; self.log(f'Upload OK {p.code}@{p.address}={p.online} {p.unit}')
            except Exception as e: fail+=1; self.log(f'Upload failed {p.code}@{p.address}: {e}')
        self.populate(); self.log(f'Upload complete. OK={ok}, Failed={fail}')
    def download(self):
        if not self.gw.ok(): QMessageBox.warning(self,'Not connected','Connect first'); return
        r=self.table.currentRow();
        if r<0: QMessageBox.warning(self,'No row','Select row'); return
        p=self.params[r]
        if p.access!='RW' or p.wp: QMessageBox.warning(self,'Blocked',f'{p.code} is read-only or write-protected'); return
        if QMessageBox.question(self,'Confirm write',f'Write {p.code}@{p.address}={p.eff} {p.unit}?')!=QMessageBox.Yes: return
        try: self.gw.writep(p,p.eff); p.online=self.gw.readp(p); p.value=p.online; p.mod=False; self.populate(); self.log(f'Download/readback OK {p.code}={p.online}')
        except Exception as e: self.log(f'Download failed {p.code}: {e}')
    def export_xlsx(self):
        if Workbook is None: return
        path,_=QFileDialog.getSaveFileName(self,'Export Excel','XD4000_Phase5D_Project.xlsx','Excel Files (*.xlsx)')
        if not path: return
        wb=Workbook(); ws=wb.active; ws.title='Parameters'; ws.append(['code','name','address','datatype','scale','offline_value','online_value','unit','access','write_protect','group','subcategory','policy'])
        for p in self.db.params: ws.append([p.code,p.name,p.address,p.datatype,p.scale,p.eff,'' if p.online is None else p.online,p.unit,p.access,p.wp,p.group,p.sub,p.policy])
        for cell in ws[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='008CD7')
        wb.save(path); self.log(f'Excel exported: {path}')
    def import_xlsx(self):
        if load_workbook is None: return
        path,_=QFileDialog.getOpenFileName(self,'Import Excel','','Excel Files (*.xlsx)')
        if not path: return
        wb=load_workbook(path,data_only=True); ws=wb['Parameters']; hdr=[c.value for c in ws[1]]; idx={h:i for i,h in enumerate(hdr)}; n=0
        for row in ws.iter_rows(min_row=2, values_only=True):
            code=row[idx.get('code',0)]
            for p in self.db.params:
                if p.code==code and 'offline_value' in idx and row[idx['offline_value']] not in (None,''):
                    try: p.value=float(row[idx['offline_value']]); p.mod=True; n+=1
                    except: pass
        self.refresh(); self.log(f'Excel imported. Offline values updated: {n}')
if __name__=='__main__': app=QApplication(sys.argv); w=W(); w.show(); sys.exit(app.exec())
