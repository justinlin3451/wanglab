# gui/main_window.py
"""
CVD Controller – Main Window
Clean, purpose-built GUI for 2D material growth.
"""

import json, logging, sys, time
from pathlib import Path
from collections import deque

import pyqtgraph as pg
from PyQt6.QtCore    import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui     import QColor, QFont, QBrush, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QFileDialog, QGroupBox,
    QSplitter, QToolBar, QStatusBar, QMessageBox, QDoubleSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QFrame, QGridLayout, QSizePolicy, QTabWidget
)

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.devices.manager  import DeviceManager
from core.devices.base     import DeviceReading, DeviceStatus
from core.recipe_engine    import RecipeEngine, Recipe, RecipeStep, StepType, RunStatus, RunProgress
from core.safety           import SafetyEngine, Alarm, AlarmSeverity
from core.data_logger      import DataLogger

logger = logging.getLogger(__name__)

# ── Catppuccin Mocha palette ──────────────────────────────────────────────────
C = {
    "base":    "#1e1e2e", "mantle":  "#181825", "crust":   "#11111b",
    "surface0":"#313244", "surface1":"#45475a",  "surface2":"#585b70",
    "overlay0":"#6c7086", "text":    "#cdd6f4",  "subtext": "#a6adc8",
    "red":     "#f38ba8", "green":   "#a6e3a1",  "yellow":  "#f9e2af",
    "blue":    "#89b4fa", "mauve":   "#cba6f7",  "teal":    "#94e2d5",
    "peach":   "#fab387",
}

CHART_HISTORY = 600  # max data points per chart

# ── Thread-safe signal bridge ─────────────────────────────────────────────────
class Bridge(QObject):
    reading  = pyqtSignal(str, str, float)
    alarm    = pyqtSignal(str, str)
    progress = pyqtSignal(object)
    finished = pyqtSignal(str)
    step     = pyqtSignal(int, str)


# ── Helpers ───────────────────────────────────────────────────────────────────
def btn(label, color=None, min_w=None):
    b = QPushButton(label)
    b.setFixedHeight(32)
    if min_w: b.setMinimumWidth(min_w)
    if color:
        b.setStyleSheet(
            f"QPushButton{{background:{color};color:#1e1e2e;font-weight:bold;"
            f"border-radius:5px;border:none;}}"
            f"QPushButton:hover{{opacity:0.85;}}"
            f"QPushButton:disabled{{background:{C['surface1']};color:{C['overlay0']};}}"
        )
    return b


class ValueCard(QFrame):
    def __init__(self, label, units, color, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{C['surface0']};border-radius:8px;"
            f"border:1px solid {C['surface1']};}}"
        )
        lay = QVBoxLayout(self); lay.setContentsMargins(10,8,10,8); lay.setSpacing(2)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{C['subtext']};font-size:9pt;background:transparent;border:none;")
        self._val = QLabel("—")
        self._val.setFont(QFont("Consolas", 20, QFont.Weight.Bold))
        self._val.setStyleSheet(f"color:{color};background:transparent;border:none;")
        u = QLabel(units)
        u.setStyleSheet(f"color:{C['overlay0']};font-size:8pt;background:transparent;border:none;")
        lay.addWidget(lbl); lay.addWidget(self._val); lay.addWidget(u)

    def set(self, v, fmt=".1f"):
        self._val.setText(f"{v:{fmt}}")


# ── Recipe Table ──────────────────────────────────────────────────────────────
HEADERS = ["#", "Min", "Sec", "Temp °C", "Ar sccm", "H₂ sccm", "Motor spd", "Rail pos", "Type", "Notes"]
CI = {h: i for i, h in enumerate(HEADERS)}
WIDTHS = [32, 52, 52, 82, 82, 82, 82, 90, 68, 0]

class RecipeTable(QWidget):
    recipe_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fp = None; self._name = "Untitled"; self._building = False
        self._setup()

    def _setup(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)

        # Toolbar
        tb = QHBoxLayout(); tb.setSpacing(6)
        for label, tip, fn in [
            ("📂 Load", "Ctrl+O", self.load),
            ("💾 Save", "Ctrl+S", self.save),
            ("+ Step",  "Add step after selection", self.add_row),
            ("− Step",  "Delete selected", self.remove_row),
            ("↑",       "Move up",   self.move_up),
            ("↓",       "Move down", self.move_down),
        ]:
            b = btn(label); b.setToolTip(tip); b.clicked.connect(fn); tb.addWidget(b)
        tb.addStretch()
        self._name_lbl = QLabel(f"  {self._name}")
        self._name_lbl.setStyleSheet(f"color:{C['blue']};font-weight:bold;font-size:10pt;")
        tb.addWidget(self._name_lbl)
        lay.addLayout(tb)

        # Table
        self._t = QTableWidget(0, len(HEADERS))
        self._t.setHorizontalHeaderLabels(HEADERS)
        self._t.verticalHeader().setVisible(False)
        self._t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._t.setAlternatingRowColors(True)
        self._t.setFont(QFont("Consolas", 10))
        self._t.setStyleSheet(f"""
            QTableWidget{{background:{C['mantle']};gridline-color:{C['surface1']};
                color:{C['text']};border:1px solid {C['surface1']};border-radius:6px;}}
            QTableWidget::item:selected{{background:{C['surface1']};}}
            QTableWidget::item:alternate{{background:{C['base']};}}
            QHeaderView::section{{background:{C['surface0']};color:{C['blue']};
                border:none;border-bottom:1px solid {C['surface1']};padding:4px;font-weight:bold;}}
        """)
        hdr = self._t.horizontalHeader()
        for i, w in enumerate(WIDTHS):
            if w: self._t.setColumnWidth(i, w)
        hdr.setSectionResizeMode(CI["Notes"], QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(CI["Type"],  QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._t.itemChanged.connect(self._changed)
        lay.addWidget(self._t)

        # Footer
        foot = QHBoxLayout()
        self._total = QLabel("Total: 0m 00s")
        self._total.setStyleSheet(f"color:{C['green']};font-weight:bold;")
        foot.addStretch(); foot.addWidget(self._total)
        lay.addLayout(foot)

    # ── Operations ────────────────────────────────────────────────────────
    def add_row(self, d=None):
        self._building = True
        row = self._after_selection()
        self._t.insertRow(row)
        d = d or {}

        num = QTableWidgetItem(str(row+1))
        num.setFlags(Qt.ItemFlag.ItemIsEnabled)
        num.setBackground(QBrush(QColor(C["surface0"])))
        num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._t.setItem(row, 0, num)

        defs = {"Min":"0","Sec":"0","Temp °C":"25","Ar sccm":"0","H₂ sccm":"0","Motor spd":"5000","Rail pos":"0","Notes":""}
        keys = {"Min":"min","Sec":"sec","Temp °C":"temp","Ar sccm":"ar","H₂ sccm":"h2","Motor spd":"motor_speed","Rail pos":"rail","Notes":"notes"}
        for h, k in keys.items():
            val = str(d.get(k, defs[h]))
            item = QTableWidgetItem(val)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter if h != "Notes"
                else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self._t.setItem(row, CI[h], item)

        combo = QComboBox()
        combo.addItems(["HOLD","RAMP"])
        combo.setCurrentText(d.get("type","HOLD"))
        combo.setStyleSheet(f"background:{C['surface0']};color:{C['text']};border:none;padding:2px;")
        combo.currentTextChanged.connect(lambda _: self._changed(None))
        self._t.setCellWidget(row, CI["Type"], combo)
        self._t.setRowHeight(row, 28)

        self._building = False
        self._renumber(); self._update_total()

    def remove_row(self):
        rows = sorted({i.row() for i in self._t.selectedItems()}, reverse=True)
        for r in rows: self._t.removeRow(r)
        self._renumber(); self._update_total(); self._emit()

    def move_up(self):
        r = self._sel()
        if r is None or r == 0: return
        self._swap(r, r-1); self._t.selectRow(r-1); self._renumber(); self._emit()

    def move_down(self):
        r = self._sel()
        if r is None or r >= self._t.rowCount()-1: return
        self._swap(r, r+1); self._t.selectRow(r+1); self._renumber(); self._emit()

    def highlight_row(self, idx: int):
        for r in range(self._t.rowCount()):
            for c in range(len(HEADERS)):
                item = self._t.item(r, c)
                if item:
                    item.setBackground(QBrush(QColor(
                        C["surface1"] if r == idx else
                        (C["mantle"] if r % 2 == 0 else C["base"])
                    )))

    # ── IO ────────────────────────────────────────────────────────────────
    def load(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self,"Load Recipe","recipes","Recipe (*.json)")
        if not path: return
        try:
            recipe = Recipe.from_file(path)
            self._fp = Path(path); self._name = recipe.name
            self._name_lbl.setText(f"  {self._name}")
            self._populate(recipe); self._emit()
            return recipe
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def save(self, path=None):
        if not path:
            path = str(self._fp) if self._fp else \
                QFileDialog.getSaveFileName(self,"Save Recipe",f"recipes/{self._name}.json","Recipe (*.json)")[0]
        if not path: return
        r = self.to_recipe()
        if r: r.save(path); self._fp = Path(path)

    def to_recipe(self):
        steps = []
        for row in range(self._t.rowCount()):
            try:
                duration = float(self._cell(row,"Min") or 0)*60 + float(self._cell(row,"Sec") or 0)
                combo = self._t.cellWidget(row, CI["Type"])
                stype = StepType(combo.currentText() if combo else "HOLD")
                sp = {}
                for key, h in [("furnace.temp","Temp °C"),("ar.flow","Ar sccm"),
                                ("h2.flow","H₂ sccm"),("rail.speed","Motor spd"),("rail.position","Rail pos")]:
                    v = self._cell(row, h)
                    if v and v.strip(): sp[key] = float(v)
                notes = self._cell(row,"Notes") or ""
                steps.append(RecipeStep(
                    name=notes or f"Step {row+1}", step_type=stype,
                    duration_s=duration, setpoints=sp, index=row
                ))
            except Exception as e:
                logger.warning(f"Row {row}: {e}")
        return Recipe(name=self._name, description="", version=1, steps=steps)

    # ── Internals ─────────────────────────────────────────────────────────
    def _populate(self, recipe: Recipe):
        self._building = True; self._t.setRowCount(0); self._building = False
        for s in recipe.steps:
            sp = s.setpoints
            self.add_row({
                "min":  str(int(s.duration_s)//60), "sec": str(int(s.duration_s)%60),
                "temp": str(sp.get("furnace.temp",25)), "ar":  str(sp.get("ar.flow",0)),
                "h2":   str(sp.get("h2.flow",0)),       "rail":str(sp.get("rail.position",0)),
                "type": s.step_type.value,
                "notes": s.name if s.name != f"Step {s.index+1}" else "",
            })

    def _cell(self, row, header):
        item = self._t.item(row, CI[header])
        return item.text() if item else None

    def _sel(self):
        items = self._t.selectedItems()
        return items[0].row() if items else None

    def _after_selection(self):
        r = self._sel(); return (r+1) if r is not None else self._t.rowCount()

    def _swap(self, r1, r2):
        text_headers = ["Min","Sec","Temp °C","Ar sccm","H₂ sccm","Motor spd","Rail pos","Notes"]
        for h in text_headers:
            t1 = self._cell(r1,h) or ""; t2 = self._cell(r2,h) or ""
            if self._t.item(r1,CI[h]): self._t.item(r1,CI[h]).setText(t2)
            if self._t.item(r2,CI[h]): self._t.item(r2,CI[h]).setText(t1)
        c1 = self._t.cellWidget(r1,CI["Type"]); c2 = self._t.cellWidget(r2,CI["Type"])
        if c1 and c2: t1,t2 = c1.currentText(),c2.currentText(); c1.setCurrentText(t2); c2.setCurrentText(t1)

    def _renumber(self):
        for r in range(self._t.rowCount()):
            item = self._t.item(r,0)
            if item: item.setText(str(r+1))

    def _update_total(self):
        total = sum(
            float(self._cell(r,"Min") or 0)*60 + float(self._cell(r,"Sec") or 0)
            for r in range(self._t.rowCount()) if True
        )
        h,rem = divmod(int(total),3600); m,s = divmod(rem,60)
        self._total.setText(f"Total: {h}h {m:02d}m {s:02d}s" if h else f"Total: {m}m {s:02d}s")

    def _changed(self, item):
        if self._building: return
        self._update_total(); self._emit()

    def _emit(self):
        r = self.to_recipe()
        if r: self.recipe_changed.emit(r)


# ── Main Window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):

    def __init__(self, workspace_path="config/workspace.json"):
        super().__init__()
        self.workspace_path = workspace_path
        self.setWindowTitle("CVD Controller  —  Wang Lab")
        self.resize(1400, 860); self.setMinimumSize(1100, 700)

        self._bridge = Bridge()
        self._dm: DeviceManager    = None
        self._engine: RecipeEngine = None
        self._logger: DataLogger   = None
        self._safety: SafetyEngine = None
        self._recipe: Recipe       = None
        self._connected            = False
        self._run_start: float     = 0

        self._t0 = time.time()
        self._cdata = {k: deque(maxlen=CHART_HISTORY) for k in ("temp","ar","h2")}

        pg.setConfigOption("background", C["mantle"])
        pg.setConfigOption("foreground", C["subtext"])

        self._build_ui()
        self._wire()
        self._style()

        self._safety_timer = QTimer(); self._safety_timer.setInterval(2000)
        self._safety_timer.timeout.connect(self._eval_safety)
        self._chart_timer  = QTimer(); self._chart_timer.setInterval(1000)
        self._chart_timer.timeout.connect(self._refresh_charts)
        self._elapsed_timer = QTimer(); self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self.statusBar().showMessage("Ready  —  click Connect to initialize devices")

    # ── Build UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        # Toolbar
        tb = QToolBar(); tb.setMovable(False); self.addToolBar(tb)

        self._btn_connect = btn("⚡  Connect",  C["blue"],   110)
        self._btn_start   = btn("▶  Start",     C["green"],  100)
        self._btn_pause   = btn("⏸  Pause",     C["yellow"], 100)
        self._btn_estop   = btn("⛔  STOP",      C["red"],    100)

        self._btn_start.setEnabled(False); self._btn_pause.setEnabled(False)
        self._btn_connect.clicked.connect(self._on_connect)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause.clicked.connect(self._on_pause_resume)
        self._btn_estop.clicked.connect(self._on_estop)

        for w in [self._btn_connect, self._btn_start, self._btn_pause, self._btn_estop]:
            tb.addWidget(w); tb.addSeparator()

        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        self._lbl_step = QLabel("—")
        self._lbl_step.setStyleSheet(f"color:{C['blue']};font-weight:bold;padding:0 8px;")
        self._lbl_time = QLabel("00:00:00")
        self._lbl_time.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
        self._lbl_time.setStyleSheet(f"color:{C['green']};padding:0 8px;")
        for w in [QLabel("Step:"), self._lbl_step, QLabel("Elapsed:"), self._lbl_time]:
            tb.addWidget(w)

        # Central
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(8,6,8,6); root.setSpacing(6)

        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane{{border:1px solid {C['surface1']};border-radius:6px;}}
            QTabBar::tab{{background:{C['surface0']};color:{C['subtext']};
                padding:6px 20px;border-radius:4px 4px 0 0;margin-right:2px;}}
            QTabBar::tab:selected{{background:{C['surface1']};color:{C['text']};font-weight:bold;}}
        """)

        # ── Dashboard tab ─────────────────────────────────────────────
        dash = QWidget()
        dl = QHBoxLayout(dash); dl.setSpacing(8)

        # Left panel
        left = QVBoxLayout(); left.setSpacing(8)

        # Live value cards
        cbox = self._group("Live Readings")
        cg = QGridLayout(); cg.setSpacing(6)
        self._vc_temp = ValueCard("Furnace Temp", "°C",    C["red"])
        self._vc_ar   = ValueCard("Ar Flow",      "sccm",  C["blue"])
        self._vc_h2   = ValueCard("H₂ Flow",      "sccm",  C["green"])
        self._vc_rail = ValueCard("Rail Position", "steps", C["mauve"])
        cg.addWidget(self._vc_temp, 0,0); cg.addWidget(self._vc_ar,   0,1)
        cg.addWidget(self._vc_h2,  1,0); cg.addWidget(self._vc_rail, 1,1)
        cbox.layout().addLayout(cg)
        left.addWidget(cbox)

        # Manual setpoints
        mbox = self._group("Manual Setpoints")
        mg = QGridLayout(); mg.setSpacing(6); mg.setColumnStretch(1,1)
        self._sp_temp = self._spinbox(0,   1400, 0, "°C")
        self._sp_ar   = self._spinbox(0,   500,  1, "sccm")
        self._sp_h2   = self._spinbox(0,   100,  1, "sccm")

        for row_i, (label, spin, dev, ctrl) in enumerate([
            ("Temp",    self._sp_temp, "furnace", "temp"),
            ("Ar Flow", self._sp_ar,   "ar",      "flow"),
            ("H₂ Flow", self._sp_h2,   "h2",      "flow"),
        ]):
            lbl = QLabel(label); lbl.setStyleSheet(f"color:{C['subtext']};")
            b = btn("Set"); b.setFixedWidth(48)
            b.clicked.connect(lambda _, d=dev, c=ctrl, s=spin: self._manual_set(d,c,s.value()))
            mg.addWidget(lbl,  row_i, 0)
            mg.addWidget(spin, row_i, 1)
            mg.addWidget(b,    row_i, 2)
        mbox.layout().addLayout(mg)
        left.addWidget(mbox)

        # Rail control
        rbox = self._group("Furnace Rail")
        rl = QHBoxLayout(); rl.setSpacing(8)
        self._btn_open  = btn("🔓  Open",  C["green"], 90)
        self._btn_close = btn("🔒  Close", C["red"],   90)
        self._btn_open.clicked.connect(lambda: self._rail_move("open"))
        self._btn_close.clicked.connect(lambda: self._rail_move("close"))
        self._lbl_rail  = QLabel("pos: —")
        self._lbl_rail.setStyleSheet(f"color:{C['subtext']};")
        rl.addWidget(self._btn_open); rl.addWidget(self._btn_close)
        rl.addStretch(); rl.addWidget(self._lbl_rail)
        rbox.layout().addLayout(rl)
        left.addWidget(rbox)
        left.addStretch()

        lw = QWidget(); lw.setLayout(left); lw.setFixedWidth(310)
        dl.addWidget(lw)

        # Charts
        ch_box = self._group("Live Data")
        ch_lay = QVBoxLayout(); ch_lay.setSpacing(4)
        self._pw_temp, self._cv_temp = self._chart("Furnace Temperature", C["red"],   "°C")
        self._pw_ar,   self._cv_ar   = self._chart("Ar Flow",             C["blue"],  "sccm")
        self._pw_h2,   self._cv_h2   = self._chart("H₂ Flow",             C["green"], "sccm")
        for pw in [self._pw_temp, self._pw_ar, self._pw_h2]:
            ch_lay.addWidget(pw)
        ch_box.layout().addLayout(ch_lay)
        dl.addWidget(ch_box)
        tabs.addTab(dash, "📊  Dashboard")

        # ── Recipe Editor tab ─────────────────────────────────────────
        self._recipe_editor = RecipeTable()
        self._recipe_editor.recipe_changed.connect(self._on_recipe_changed)
        tabs.addTab(self._recipe_editor, "🧪  Recipe Editor")

        root.addWidget(tabs, stretch=5)

        # Log
        log_box = self._group("Events & Alarms")
        ll = QHBoxLayout()
        self._log = QTextEdit(); self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9)); self._log.setMaximumHeight(110)
        self._log.setStyleSheet(f"background:{C['mantle']};color:{C['text']};border:none;border-radius:4px;")
        clr = btn("Clear"); clr.setFixedWidth(60); clr.clicked.connect(self._log.clear)
        ll.addWidget(self._log); ll.addWidget(clr, alignment=Qt.AlignmentFlag.AlignTop)
        log_box.layout().addLayout(ll)
        root.addWidget(log_box, stretch=1)

        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self._recipe_editor.load)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._recipe_editor.save)

    def _group(self, title):
        box = QGroupBox(title); box.setLayout(QVBoxLayout())
        box.layout().setContentsMargins(8,12,8,8); box.layout().setSpacing(6)
        return box

    def _spinbox(self, lo, hi, dec, suffix):
        sp = QDoubleSpinBox(); sp.setRange(lo,hi); sp.setDecimals(dec)
        sp.setSuffix(f"  {suffix}"); sp.setFixedHeight(28)
        sp.setStyleSheet(
            f"QDoubleSpinBox{{background:{C['surface0']};color:{C['text']};"
            f"border:1px solid {C['surface1']};border-radius:4px;padding:2px 6px;}}"
        )
        return sp

    def _chart(self, title, color, ylabel):
        pw = pg.PlotWidget()
        pw.setLabel("left", ylabel); pw.setLabel("bottom", "Time (s)")
        pw.showGrid(x=True, y=True, alpha=0.15); pw.setMinimumHeight(130)
        pw.setTitle(title, color=C["subtext"], size="10pt")
        pw.getPlotItem().getAxis("left").setTextPen(pg.mkPen(C["subtext"]))
        pw.getPlotItem().getAxis("bottom").setTextPen(pg.mkPen(C["subtext"]))
        curve = pw.plot(pen=pg.mkPen(color=color, width=2))
        return pw, curve

    # ── Wiring ────────────────────────────────────────────────────────────
    def _wire(self):
        b = self._bridge
        b.reading.connect(self._on_reading)
        b.alarm.connect(self._on_alarm)
        b.finished.connect(self._on_finished)
        b.step.connect(self._on_step)

    # ── Actions ───────────────────────────────────────────────────────────
    def _on_connect(self):
        try:
            with open(self.workspace_path) as f: ws = json.load(f)
        except Exception as e:
            QMessageBox.critical(self,"Config Error", str(e)); return

        self._dm     = DeviceManager.from_dict(ws)
        self._safety = SafetyEngine(ws.get("safety", {}))
        self._logger = DataLogger()
        self._engine = RecipeEngine(self._dm)

        self._safety.add_alarm_callback(
            lambda a: self._bridge.alarm.emit(a.severity.name, a.message)
        )
        def on_r(r: DeviceReading):
            try:
                self._bridge.reading.emit(r.device_id, r.control, float(r.value))
                if self._logger and self._logger._run_id: self._logger.log_reading(r)
            except: pass

        self._dm.subscribe_all(on_r)
        self._engine.on_step_change = lambda i,n: self._bridge.step.emit(i, n)
        self._engine.on_finished    = lambda s:   self._bridge.finished.emit(s.name)

        results = self._dm.connect_all()
        self._connected = True
        sim = all(self._dm.get_device(d).status.name == "SIMULATED" for d in results)
        self._safety_timer.start(); self._chart_timer.start(); self._t0 = time.time()
        self._btn_connect.setText("✓  Connected"); self._btn_connect.setEnabled(False)
        if self._recipe: self._btn_start.setEnabled(True)
        mode = "SIMULATION" if sim else "HARDWARE"
        self._log_event(f"Connected — {mode} mode ({sum(results.values())}/{len(results)} devices)")
        self.statusBar().showMessage(f"Connected [{mode}]")

    def _on_recipe_changed(self, recipe: Recipe):
        self._recipe = recipe
        if self._engine: self._engine.load(recipe)
        if self._connected: self._btn_start.setEnabled(True)

    def _on_start(self):
        if not self._engine or not self._recipe or self._engine.status == RunStatus.RUNNING: return
        if self._logger: self._logger.start_run(self._recipe.name, self._recipe.to_dict())
        self._run_start = time.time()
        self._engine.start()
        self._btn_start.setEnabled(False); self._btn_pause.setEnabled(True)
        self._elapsed_timer.start()
        self._log_event(f"▶ Started: {self._recipe.name}")

    def _on_pause_resume(self):
        if not self._engine: return
        if self._engine.status == RunStatus.RUNNING:
            self._engine.pause(); self._btn_pause.setText("▶  Resume")
            self._log_event("⏸ Paused")
        elif self._engine.status == RunStatus.PAUSED:
            self._engine.resume(); self._btn_pause.setText("⏸  Pause")
            self._log_event("▶ Resumed")

    def _on_estop(self):
        if QMessageBox.question(self,"Emergency Stop",
            "Cut all setpoints to zero immediately?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes: return
        if self._engine:  self._engine.abort()
        if self._safety and self._dm: self._safety.emergency_stop(self._dm)
        if self._logger:  self._logger.end_run("ABORTED")
        self._elapsed_timer.stop()
        self._btn_start.setEnabled(True); self._btn_pause.setEnabled(False)
        self._btn_pause.setText("⏸  Pause")
        self._log_event("⛔ EMERGENCY STOP", alarm=True)

    def _manual_set(self, dev, ctrl, value):
        if not self._dm: self._log_event("Not connected"); return
        if self._dm.set_value(dev, ctrl, value):
            self._log_event(f"Set {dev}.{ctrl} = {value}")
        else:
            self._log_event(f"Failed: {dev}.{ctrl}", alarm=True)

    def _rail_move(self, direction):
        if not self._dm: self._log_event("Not connected"); return
        try:
            with open(self.workspace_path) as f: ws = json.load(f)
            rc  = ws.get("devices",{}).get("rail",{})
            pos = rc.get("open_pos", 0) if direction=="open" else rc.get("close_pos", 30000)
        except: pos = 0 if direction=="open" else 30000
        self._dm.set_value("rail","position", pos)
        self._log_event(f"Rail → {'OPEN' if direction=='open' else 'CLOSED'} (pos={pos})")

    # ── Signal handlers ───────────────────────────────────────────────────
    def _on_reading(self, device_id, control, value):
        t = time.time() - self._t0
        if   device_id=="furnace" and control=="temp":
            self._vc_temp.set(value); self._cdata["temp"].append((t,value))
        elif device_id=="ar"      and control=="flow":
            self._vc_ar.set(value);   self._cdata["ar"].append((t,value))
        elif device_id=="h2"      and control=="flow":
            self._vc_h2.set(value);   self._cdata["h2"].append((t,value))
        elif device_id=="rail"    and control=="position":
            self._vc_rail.set(value,".0f"); self._lbl_rail.setText(f"pos: {int(value)}")

    def _on_alarm(self, severity, message):
        self._log_event(f"[{severity}] {message}", alarm=True)

    def _on_finished(self, status_name):
        self._elapsed_timer.stop()
        self._btn_start.setEnabled(True); self._btn_pause.setEnabled(False)
        self._btn_pause.setText("⏸  Pause")
        if self._logger: self._logger.end_run(status_name)
        self._lbl_step.setText("Done")
        self._log_event(f"✓ Finished: {status_name}")

    def _on_step(self, index, name):
        self._lbl_step.setText(f"{index+1}: {name}")
        self._recipe_editor.highlight_row(index)
        self._log_event(f"→ Step {index+1}: {name}")

    def _update_elapsed(self):
        if not self._run_start: return
        e = int(time.time()-self._run_start)
        h,r = divmod(e,3600); m,s = divmod(r,60)
        self._lbl_time.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _refresh_charts(self):
        for key, curve in [("temp",self._cv_temp),("ar",self._cv_ar),("h2",self._cv_h2)]:
            pts = self._cdata[key]
            if len(pts) >= 2:
                curve.setData([p[0] for p in pts], [p[1] for p in pts])

    def _eval_safety(self):
        if not self._safety or not self._dm: return
        self._safety.evaluate({
            "furnace_temp":  self._dm.get_value("furnace","temp") or 0,
            "ar_flow":       self._dm.get_value("ar","flow") or 0,
            "h2_flow":       self._dm.get_value("h2","flow") or 0,
            "rail_position": self._dm.get_value("rail","position") or 0,
        }, self._dm)

    def _log_event(self, msg, alarm=False):
        ts = time.strftime("%H:%M:%S")
        color = C["red"] if alarm else C["text"]
        self._log.append(
            f'<span style="color:{C["overlay0"]}">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>'
        )

    def _style(self):
        self.setStyleSheet(f"""
            QMainWindow,QWidget{{background:{C['base']};color:{C['text']};
                font-family:"Segoe UI",sans-serif;font-size:10pt;}}
            QGroupBox{{border:1px solid {C['surface1']};border-radius:8px;
                margin-top:8px;padding-top:8px;font-weight:bold;color:{C['blue']};}}
            QGroupBox::title{{subcontrol-origin:margin;left:10px;color:{C['blue']};}}
            QToolBar{{background:{C['mantle']};border:none;spacing:6px;padding:4px 8px;}}
            QStatusBar{{background:{C['mantle']};color:{C['subtext']};}}
            QLabel{{background:transparent;}}
            QScrollBar:vertical{{background:{C['surface0']};width:8px;border-radius:4px;}}
            QScrollBar::handle:vertical{{background:{C['surface2']};border-radius:4px;min-height:20px;}}
        """)

    def closeEvent(self, event):
        if self._dm:     self._dm.disconnect_all()
        if self._logger: self._logger.close()
        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────────
def run_app():
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/cvd_controller.log")]
    )
    app = QApplication(sys.argv)
    app.setApplicationName("CVD Controller")
    app.setStyle("Fusion")
    win = MainWindow("config/workspace.json")
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_app()
