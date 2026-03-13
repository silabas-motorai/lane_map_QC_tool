import os
import re
from pathlib import Path
from collections import defaultdict

from qgis.PyQt.QtCore import Qt, QTimer, QPoint, QSettings
from qgis.PyQt.QtGui import QPixmap, QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QSizePolicy, QScrollArea,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QFileDialog
)
from qgis.gui import QgsMapToolEmitPoint
from qgis.core import (
    QgsProject, QgsPointXY, QgsCoordinateTransform, QgsCoordinateReferenceSystem
)

# =============================================================================
# PRECISION CONSTANTS  (no paths here — paths are saved per-user via QSettings)
# =============================================================================

NEAREST_SEARCH_METERS = 10
POLYLINE_STRIDE       = 3
GRID_CELL_M           = 150
PLAY_INTERVAL_MS      = 120

_SETTINGS_KEY_HTML   = "lane_map_QC_tool/dashcam_html_path"
_SETTINGS_KEY_FRAMES = "lane_map_QC_tool/dashcam_frames_root"

# =============================================================================
# PATH SETTINGS DIALOG
# =============================================================================

class DashcamPathDialog(QDialog):
    """Asks the user for HTML overview map and frames root folder.
    Pre-fills from QSettings if previously saved."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dashcam — Set Data Paths")
        self.setMinimumWidth(520)

        s = QSettings()
        layout = QFormLayout(self)
        layout.setSpacing(8)

        html_row = QHBoxLayout()
        self.html_edit = QLineEdit(s.value(_SETTINGS_KEY_HTML, ""))
        self.html_edit.setPlaceholderText("Select the overview .html file …")
        btn_html = QPushButton("Browse…")
        btn_html.setFixedWidth(80)
        btn_html.clicked.connect(self._browse_html)
        html_row.addWidget(self.html_edit)
        html_row.addWidget(btn_html)
        layout.addRow("Overview HTML:", html_row)

        frames_row = QHBoxLayout()
        self.frames_edit = QLineEdit(s.value(_SETTINGS_KEY_FRAMES, ""))
        self.frames_edit.setPlaceholderText("Select frames folder or its parent …")
        btn_frames = QPushButton("Browse…")
        btn_frames.setFixedWidth(80)
        btn_frames.clicked.connect(self._browse_frames)
        frames_row.addWidget(self.frames_edit)
        frames_row.addWidget(btn_frames)
        layout.addRow("Frames Root:", frames_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _browse_html(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Overview HTML", self.html_edit.text(),
            "HTML files (*.html *.htm);;All files (*)"
        )
        if path:
            self.html_edit.setText(path)

    def _browse_frames(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Frames Root Folder", self.frames_edit.text()
        )
        if path:
            self.frames_edit.setText(path)

    def _on_accept(self):
        html   = self.html_edit.text().strip()
        frames = self.frames_edit.text().strip()
        if not html or not frames:
            QMessageBox.warning(self, "Missing paths", "Please set both paths before continuing.")
            return
        if not os.path.isfile(html):
            QMessageBox.warning(self, "File not found", f"HTML file not found:\n{html}")
            return
        if not os.path.isdir(frames):
            QMessageBox.warning(self, "Folder not found", f"Frames folder not found:\n{frames}")
            return
        s = QSettings()
        s.setValue(_SETTINGS_KEY_HTML,   html)
        s.setValue(_SETTINGS_KEY_FRAMES, frames)
        self.accept()

    @staticmethod
    def get_paths_silent():
        """Returns (html_path, frames_root) from QSettings without any dialog.
        Returns (None, None) if paths are not configured or invalid."""
        s = QSettings()
        html   = s.value(_SETTINGS_KEY_HTML,   "")
        frames = s.value(_SETTINGS_KEY_FRAMES, "")
        if html and frames and os.path.isfile(html) and os.path.isdir(frames):
            return html, frames
        return None, None

    @staticmethod
    def get_paths(parent=None):
        """Returns (html_path, frames_root) from QSettings, or asks if not set.
        Returns (None, None) if user cancels."""
        s = QSettings()
        html   = s.value(_SETTINGS_KEY_HTML,   "")
        frames = s.value(_SETTINGS_KEY_FRAMES, "")
        if html and frames and os.path.isfile(html) and os.path.isdir(frames):
            return html, frames
        dlg = DashcamPathDialog(parent)
        if dlg.exec_() == QDialog.Accepted:
            return (s.value(_SETTINGS_KEY_HTML, ""),
                    s.value(_SETTINGS_KEY_FRAMES, ""))
        return None, None

    @staticmethod
    def reconfigure(parent=None):
        """Force re-open the dialog even if paths are already saved."""
        DashcamPathDialog(parent).exec_()

# =============================================================================
# REGEX PATTERNS
# =============================================================================

IMG_RE = re.compile(r"frame_(\d+)\.(jpg|jpeg|png)$", re.IGNORECASE)
FOLDER_A = re.compile(r"^(\d{8}_\d{6})_")             
FOLDER_B = re.compile(r"^frames-(\d{8})T(\d{6})Z-")   

MARKER_DEF_RE = re.compile(r"var\s+(marker_[A-Za-z0-9_]+)\s*=\s*L\.marker\(\s*\[\s*([0-9\.\-]+)\s*,\s*([0-9\.\-]+)\s*\]", re.MULTILINE)
POLY_DEF_RE = re.compile(r"var\s+(poly_line_[A-Za-z0-9_]+)\s*=\s*L\.polyline\(\s*\[\[(.*?)\]\]\s*,\s*\{", re.DOTALL)
PAIR_RE = re.compile(r"\[\s*([0-9\.\-]+)\s*,\s*([0-9\.\-]+)\s*\]")
TOOLTIP_RE = re.compile(r"([A-Za-z0-9_]+)\.bindTooltip\(\s*`[^`]*<b>([^<]+)</b>", re.MULTILINE)

# =============================================================================
# CUSTOM UI COMPONENT: ZOOMABLE & PANNABLE LABEL
# =============================================================================

class ZoomLabel(QLabel):
    """Subclass of QLabel to handle mouse wheel zooming and left-click panning."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom_factor = 1.0
        self.last_mouse_pos = QPoint()
        self.setAlignment(Qt.AlignCenter)

    def wheelEvent(self, event):
        """Handle zooming with the mouse wheel."""
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_factor = min(10.0, self.zoom_factor + 0.2)
        else:
            self.zoom_factor = max(1.0, self.zoom_factor - 0.2)
        
        # Access the DashcamDock to update the UI
        try:
            # Hierarchy: Label -> Viewport -> ScrollArea -> Container -> Dock
            dock = self.parent().parent().parent().parent()
            if hasattr(dock, 'render'):
                dock.render()
        except:
            pass

    def mousePressEvent(self, event):
        """Start panning when the left mouse button is pressed."""
        if event.button() == Qt.LeftButton:
            self.last_mouse_pos = event.globalPos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        """Pan the image by moving the scrollbars of the QScrollArea."""
        if event.buttons() == Qt.LeftButton:
            delta = event.globalPos() - self.last_mouse_pos
            try:
                scroll_area = self.parent().parent()
                h_bar = scroll_area.horizontalScrollBar()
                v_bar = scroll_area.verticalScrollBar()
                
                h_bar.setValue(h_bar.value() - delta.x())
                v_bar.setValue(v_bar.value() - delta.y())
                
                self.last_mouse_pos = event.globalPos()
            except:
                pass

    def mouseReleaseEvent(self, event):
        """Reset cursor after panning."""
        self.setCursor(Qt.ArrowCursor)

# =============================================================================
# HELPERS
# =============================================================================

def _status(msg: str, timeout_ms: int = 4000):
    try: iface.mainWindow().statusBar().showMessage(msg, timeout_ms)
    except: pass

def _warn(title: str, msg: str): QMessageBox.warning(None, title, msg)

def _key_from_runid(run_id: str) -> str:
    m = re.search(r"(\d{8})_(\d{6})", run_id)
    return f"{m.group(1)}_{m.group(2)}" if m else ""

def build_frames_index(frames_root: str) -> dict:
    root = Path(frames_root)
    if not root.exists(): raise FileNotFoundError("Frames root not found")
    idx = {}

    # Check if the given folder itself matches the naming pattern
    # (user selected the frames folder directly instead of parent)
    name = root.name
    mA, mB = FOLDER_A.match(name), FOLDER_B.match(name)
    if mA:
        idx.setdefault(mA.group(1), []).append(str(root))
    elif mB:
        idx.setdefault(f"{mB.group(1)}_{mB.group(2)}", []).append(str(root))

    # Also scan subdirectories (standard case: parent folder given)
    for p in root.rglob("*"):
        if not p.is_dir(): continue
        name = p.name
        mA, mB = FOLDER_A.match(name), FOLDER_B.match(name)
        if mA: idx.setdefault(mA.group(1), []).append(str(p))
        elif mB: idx.setdefault(f"{mB.group(1)}_{mB.group(2)}", []).append(str(p))
    return idx

def list_frames_in_folder(folder_path: str):
    folder = Path(folder_path)
    files = [str(f) for f in folder.rglob("*") if f.is_file() and IMG_RE.search(f.name)]
    files.sort(key=lambda fp: int(IMG_RE.search(Path(fp).name).group(1)) if IMG_RE.search(Path(fp).name) else 10**18)
    return files

def pick_best_folder(folder_paths):
    best, bc = folder_paths[0], -1
    for fp in folder_paths:
        try: count = sum(1 for f in Path(fp).rglob("*") if f.is_file() and IMG_RE.search(f.name))
        except: count = -1
        if count > bc: best, bc = fp, count
    return best

def parse_html_objects(html_path: str):
    with open(html_path, "r", encoding="utf-8", errors="ignore") as f: txt = f.read()
    obj_map = {m.group(1).strip(): m.group(2).strip() for m in TOOLTIP_RE.finditer(txt)}
    markers = [(m.group(1), float(m.group(3)), float(m.group(2))) for m in MARKER_DEF_RE.finditer(txt)]
    polylines = []
    for m in POLY_DEF_RE.finditer(txt):
        pairs = PAIR_RE.findall(m.group(2))
        coords = [(float(ln), float(lt)) for lt, ln in pairs]
        if len(coords) >= 2: polylines.append((m.group(1), coords))
    return obj_map, markers, polylines

def build_click_index(obj_map, markers, polylines, crs):
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    xf = QgsCoordinateTransform(wgs84, crs, QgsProject.instance())
    grid = defaultdict(list)
    for obj, crds in polylines:
        rid = obj_map.get(obj, ""); k = _key_from_runid(rid)
        if not k: continue
        for i, c in enumerate(crds):
            if i % POLYLINE_STRIDE != 0: continue
            try:
                p = xf.transform(QgsPointXY(*c))
                grid[(int(p.x()//GRID_CELL_M), int(p.y()//GRID_CELL_M))].append(
                    {"x": p.x(), "y": p.y(), "run_id": rid, "key": k, "t": i/(len(crds)-1) if len(crds)>1 else 0}
                )
            except: continue
    return grid

# =============================================================================
# UI: DASHCAM DOCK WITH AUTO-RESET ZOOM
# =============================================================================

class DashcamDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Dashcam Viewer", parent)
        self.setObjectName("DashcamViewerDock")
        self.setFeatures(QDockWidget.AllDockWidgetFeatures)
        self.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.setFloating(False) 

        self.frame_files, self.i = [], 0
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(2, 2, 2, 2)

        self.title = QLabel("Ready. Select Camera -> Click Map. Zoom (Wheel) & Pan (Left Click).")
        self.title.setWordWrap(True)

        self.img = ZoomLabel()
        self.img.setStyleSheet("background-color: #000;")
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False) 
        self.scroll.setWidget(self.img)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setMinimumHeight(200)

        self.slider = QSlider(Qt.Horizontal)
        self.info = QLabel("")

        row = QHBoxLayout()
        self.btns = [
            ("◀ Prev", self.prev), ("Next ▶", self.next), 
            ("▶ Play", lambda: self.timer.start()), ("Stop", lambda: self.timer.stop())
        ]
        for text, func in self.btns:
            b = QPushButton(text)
            b.clicked.connect(func)
            row.addWidget(b)

        v.addWidget(self.title)
        v.addWidget(self.scroll, 1)
        v.addWidget(self.slider)
        v.addWidget(self.info)
        v.addLayout(row)

        self.setWidget(container)
        self.slider.valueChanged.connect(self.on_slider)
        self.timer = QTimer(self, interval=PLAY_INTERVAL_MS, timeout=self.next)

    def set_frames(self, frames, idx=0, title=""):
        self.frame_files, self.i = frames or [], idx
        self.img.zoom_factor = 1.0 
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self.frame_files)-1))
        self.slider.setValue(self.i)
        self.slider.blockSignals(False)
        if title: self.title.setText(title)
        self.render()

    def render(self):
        """Render frame. Zoom is reset on frame change by prev/next methods."""
        if not self.frame_files: return
        pix = QPixmap(self.frame_files[self.i])
        if not pix.isNull():
            base_w, base_h = self.scroll.viewport().width(), self.scroll.viewport().height()
            new_w, new_h = int(base_w * self.img.zoom_factor), int(base_h * self.img.zoom_factor)
            
            self.img.setFixedSize(new_w, new_h)
            self.img.setPixmap(pix.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            
        self.info.setText(f"Frame: {self.i + 1}/{len(self.frame_files)} | Zoom: {self.img.zoom_factor:.1f}x")
        self.slider.blockSignals(True)
        self.slider.setValue(self.i)
        self.slider.blockSignals(False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(20, self.render)

    # Updated navigation methods to reset zoom on every change
    def prev(self): 
        self.img.zoom_factor = 1.0
        self.i = max(0, self.i-1)
        self.render()

    def next(self):
        self.img.zoom_factor = 1.0
        if self.i < len(self.frame_files)-1: 
            self.i += 1
            self.render()
        else: 
            self.timer.stop()

    def on_slider(self, v): 
        self.img.zoom_factor = 1.0
        self.i = v
        self.render()

# =============================================================================
# MAP TOOL & CONTROLLER
# =============================================================================

class DashcamMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, grid, f_idx, dock):
        super().__init__(canvas)
        self.canvas, self.grid, self.f_idx, self.dock = canvas, grid, f_idx, dock
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, e):
        pt = self.toMapCoordinates(e.pos())
        ix, iy = int(pt.x()//GRID_CELL_M), int(pt.y()//GRID_CELL_M)
        best, best_d = None, 1e18
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for p in self.grid.get((ix+dx, iy+dy), []):
                    d = ((p["x"]-pt.x())**2 + (p["y"]-pt.y())**2)**.5
                    if d < best_d: best_d, best = d, p
        
        if not best or best_d > NEAREST_SEARCH_METERS:
            _status(f"Not found within {NEAREST_SEARCH_METERS} degrees."); return

        self.canvas.setCenter(pt)
        dirs = self.f_idx.get(best["key"], [])
        if not dirs: return
        frames = list_frames_in_folder(pick_best_folder(dirs))
        idx = int(best["t"]*(len(frames)-1))
        self.dock.set_frames(frames, idx, best["run_id"])
        self.dock.show()

class DashcamController:
    def __init__(self, iface):
        self.iface, self.dock, self.action, self.tool = iface, None, None, None

    def start(self):
        try:
            self.f_idx = build_frames_index(FRAMES_ROOT)
            obj_map, mks, ply = parse_html_objects(HTML_PATH)
            self.grid = build_click_index(obj_map, mks, ply, QgsProject.instance().crs())
        except Exception as e: _warn("Error", str(e)); return

        if not self.dock:
            self.dock = DashcamDock(self.iface.mainWindow())
            self.iface.addDockWidget(Qt.BottomDockWidgetArea, self.dock)

        icon = QIcon(":/images/themes/default/mIconCamera.svg")
        self.action = QAction(icon, "Dashcam Viewer", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self.on_toggled)
        self.iface.addToolBarIcon(self.action)
        _status("Ready. Scroll to Zoom, Left Click to Pan. Zoom resets on frame change.")

    def on_toggled(self, chk):
        if chk:
            self.tool = DashcamMapTool(self.iface.mapCanvas(), self.grid, self.f_idx, self.dock)
            self.iface.mapCanvas().setMapTool(self.tool); self.dock.show()
        else:
            if self.tool: self.iface.mapCanvas().unsetMapTool(self.tool)
            self.tool = None

# Module is imported by QcSuitePlugin — no auto-run here.
# Can still be run standalone from the QGIS console:
#   from lane_map_QC_tool import dashcam_locator; dashcam_locator.standalone_start()

def standalone_start():
    global _DASHCAM_V
    if "_DASHCAM_V" in globals():
        try: iface.removeToolBarIcon(_DASHCAM_V.action); _DASHCAM_V.dock.close()
        except: pass
    _DASHCAM_V = DashcamController(iface)
    _DASHCAM_V.start()