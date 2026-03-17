"""
Dashcam Locator  v3
=====================
"""
import os
import re
import math
from pathlib import Path
from collections import defaultdict

from qgis.PyQt.QtCore    import Qt, QTimer, QPoint, QSettings, QSize
from qgis.PyQt.QtGui     import QPixmap, QIcon, QColor, QFont
from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QScrollArea,
    QDialog, QDialogButtonBox, QFileDialog, QListWidget, QListWidgetItem,
    QGroupBox, QLineEdit, QAbstractItemView, QToolButton, QStackedWidget,
    QSpinBox
)
from qgis.gui  import QgsMapToolEmitPoint
from qgis.core import (
    QgsProject, QgsPointXY, QgsCoordinateTransform, QgsCoordinateReferenceSystem,
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsLineSymbol, QgsMarkerSymbol,
    QgsSimpleMarkerSymbolLayer, QgsSimpleLineSymbolLayer,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat,
    QgsTextBufferSettings, QgsRuleBasedRenderer, QgsProperty,
    QgsUnitTypes, QgsMarkerLineSymbolLayer
)
from qgis.PyQt.QtCore import QVariant

# =============================================================================
# SETTINGS KEYS
# =============================================================================
SETTINGS_GROUP   = "DashcamViewer"
KEY_HTML         = f"{SETTINGS_GROUP}/html_path"
KEY_FRAMES_ROOTS = f"{SETTINGS_GROUP}/frames_roots"
KEY_RADIUS       = f"{SETTINGS_GROUP}/pick_radius_m"

# =============================================================================
# CONSTANTS
# =============================================================================
DEFAULT_RADIUS_M = 15
PLAY_INTERVAL_MS = 120

DEG_PER_METRE = 1.0 / 111_320.0

_PALETTE = [
    "#e6194b","#3cb44b","#4363d8","#f58231","#911eb4",
    "#42d4f4","#f032e6","#bfef45","#469990","#dcbeff",
    "#9A6324","#800000","#aaffc3","#808000","#ffd8b1",
    "#000075","#a9a9a9","#fffac8","#fabed4","#00ced1",
]
def _route_color(i): return _PALETTE[i % len(_PALETTE)]

# =============================================================================
# REGEX
# =============================================================================
IMG_RE        = re.compile(r"frame_(\d+)\.(jpg|jpeg|png)$", re.IGNORECASE)
FOLDER_A      = re.compile(r"^(\d{8}_\d{6})_")
FOLDER_B      = re.compile(r"^frames-(\d{8})T(\d{6})Z-")
MARKER_DEF_RE = re.compile(
    r"var\s+(marker_[A-Za-z0-9_]+)\s*=\s*L\.marker\(\s*\[\s*([0-9.\-]+)\s*,\s*([0-9.\-]+)\s*\]",
    re.MULTILINE)
POLY_DEF_RE   = re.compile(
    r"var\s+(poly_line_[A-Za-z0-9_]+)\s*=\s*L\.polyline\(\s*\[\[(.*?)\]\]\s*,\s*\{",
    re.DOTALL)
PAIR_RE       = re.compile(r"\[\s*([0-9.\-]+)\s*,\s*([0-9.\-]+)\s*\]")
TOOLTIP_RE    = re.compile(
    r"([A-Za-z0-9_]+)\.bindTooltip\(\s*`[^`]*<b>([^<]+)</b>",
    re.MULTILINE)

# =============================================================================
# CRS UNIT HELPER
# =============================================================================

def _crs_uses_degrees(crs):
    try:
        unit = crs.mapUnits()
        return unit in (
            QgsUnitTypes.DistanceDegrees,
            QgsUnitTypes.DistanceUnknownUnit,
        )
    except Exception:
        return "degree" in crs.toProj().lower()

def _metres_to_crs(metres, crs):
    if _crs_uses_degrees(crs):
        return metres * DEG_PER_METRE
    return metres

# =============================================================================
# SETTINGS HELPERS
# =============================================================================

def _load_settings():
    s      = QSettings()
    html   = s.value(KEY_HTML, "")
    raw    = s.value(KEY_FRAMES_ROOTS, "")
    roots  = [r for r in raw.split("|") if r.strip()] if raw else []
    radius = int(s.value(KEY_RADIUS, DEFAULT_RADIUS_M))
    return html, roots, radius

def _save_settings(html, roots, radius=None):
    s = QSettings()
    s.setValue(KEY_HTML,         html)
    s.setValue(KEY_FRAMES_ROOTS, "|".join(roots))
    if radius is not None:
        s.setValue(KEY_RADIUS, radius)
    s.sync()

# =============================================================================
# SETUP DIALOG
# =============================================================================

class SetupDialog(QDialog):
    def __init__(self, html_path="", frames_roots=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dashcam Viewer – Path Setup")
        self.setMinimumWidth(620); self.setMinimumHeight(430)
        frames_roots = frames_roots or []
        lay = QVBoxLayout(self)

        banner = QLabel(
            "Configure the HTML overview file and frames folders below."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background:#1a6e9e;color:white;padding:10px;border-radius:4px;")
        lay.addWidget(banner)

        hg = QGroupBox("Geolocated HTML file")
        hl = QHBoxLayout(hg)
        self.html_edit = QLineEdit(html_path)
        self.html_edit.setPlaceholderText("Click Browse…")
        btn_h = QPushButton("Browse…"); btn_h.setFixedWidth(90)
        btn_h.clicked.connect(self._browse_html)
        hl.addWidget(self.html_edit); hl.addWidget(btn_h)
        lay.addWidget(hg)

        fg = QGroupBox("Frames root folders  (one per region)")
        fl = QVBoxLayout(fg)
        note = QLabel(
            "ℹ  "Only routes with frames in these folders will appear on the map""
        )
        note.setStyleSheet("color:#555;font-size:11px;")
        fl.addWidget(note)
        self.list_w = QListWidget()
        self.list_w.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for r in frames_roots:
            self.list_w.addItem(QListWidgetItem(r))
        fl.addWidget(self.list_w)
        br = QHBoxLayout()
        ba  = QPushButton("＋ Add")
        brm = QPushButton("－ Remove")
        ba.clicked.connect(self._add); brm.clicked.connect(self._remove)
        br.addWidget(ba); br.addWidget(brm); br.addStretch()
        fl.addLayout(br)
        lay.addWidget(fg)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._ok); btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _browse_html(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select HTML", "", "HTML (*.html *.htm);;All (*)")
        if p: self.html_edit.setText(p)

    def _add(self):
        f = QFileDialog.getExistingDirectory(self, "Select Frames Root Folder")
        if f and f not in self.get_roots():
            self.list_w.addItem(QListWidgetItem(f))

    def _remove(self):
        for it in self.list_w.selectedItems():
            self.list_w.takeItem(self.list_w.row(it))

    def _ok(self):
        html = self.html_edit.text().strip()
        if not html:
            QMessageBox.warning(self, "Missing", "Please select the HTML file."); return
        if not os.path.isfile(html):
            QMessageBox.warning(self, "Not found", f"File not found:\n{html}"); return
        self.accept()

    def get_html(self):  return self.html_edit.text().strip()
    def get_roots(self): return [self.list_w.item(i).text()
                                 for i in range(self.list_w.count())]

# =============================================================================
# ZOOM LABEL
# =============================================================================

class ZoomLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom_factor    = 1.0
        self.last_mouse_pos = QPoint()
        self.setAlignment(Qt.AlignCenter)
        from qgis.PyQt.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _sa(self):
        from qgis.PyQt.QtWidgets import QScrollArea
        w = self.parent()
        for _ in range(10):
            if w is None: break
            if isinstance(w, QScrollArea): return w
            try: w = w.parent()
            except: break
        return None

    def _dock(self):
        try: return self.window().findChild(QDockWidget, "DashcamViewerDock")
        except: return None

    def wheelEvent(self, event):
        old = self.zoom_factor
        d   = event.angleDelta().y()
        new = min(10.0, old + 0.15) if d > 0 else max(1.0, old - 0.15)
        if abs(new - old) < 0.001:
            return
        sa = self._sa()
        if sa:
            hb = sa.horizontalScrollBar()
            vb = sa.verticalScrollBar()
            cx = hb.value() + event.pos().x()
            cy = vb.value() + event.pos().y()
        self.zoom_factor = new
        dk = self._dock()
        if dk:
            dk.render_frame()
        if sa:
            sc = new / old
            hb.setValue(int(cx * sc) - event.pos().x())
            vb.setValue(int(cy * sc) - event.pos().y())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.last_mouse_pos = e.globalPos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton:
            d  = e.globalPos() - self.last_mouse_pos
            sa = self._sa()
            if sa:
                sa.horizontalScrollBar().setValue(
                    sa.horizontalScrollBar().value() - d.x())
                sa.verticalScrollBar().setValue(
                    sa.verticalScrollBar().value() - d.y())
            self.last_mouse_pos = e.globalPos()

    def mouseReleaseEvent(self, e):
        self.setCursor(Qt.ArrowCursor)

# =============================================================================
# ROUTE PICKER PANEL
# =============================================================================

class RoutePicker(QWidget):
    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock        = dock
        self.candidates  = []
        self._route_coords = {}
        self._crs          = None
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4)

        hdr = QHBoxLayout()
        lbl = QLabel(
            "<b>Dashcam data routes near the clicked point</b><br>"
            "<i>Single-click</i> to preview the selected route on the map<br>"
            "<i>Double-click</i> to view the selected route's frames"
        )
        lbl.setWordWrap(True)
        hdr.addWidget(lbl, 1)
        btn_clr = QPushButton("✕ Clear map")
        btn_clr.setFixedWidth(100); btn_clr.setStyleSheet("color:#c00;")
        btn_clr.clicked.connect(self._clear)
        hdr.addWidget(btn_clr)
        lay.addLayout(hdr)

        self.list_w = QListWidget()
        self.list_w.setSelectionMode(QListWidget.SingleSelection)
        self.list_w.itemClicked.connect(self._single_click)
        self.list_w.itemDoubleClicked.connect(self._double_click)
        lay.addWidget(self.list_w)

        foot = QLabel(
            "White arrow = START of the route  ·   "
            "Coloured circle = END of the route"
        )
        foot.setStyleSheet("color:#555;font-size:10px;")
        lay.addWidget(foot)

    def populate(self, candidates, route_coords=None, crs=None):
        self.candidates    = candidates
        self._route_coords = route_coords or {}
        self._crs          = crs
        self.list_w.clear()
        for c in candidates:
            px = QPixmap(14, 14); px.fill(QColor(c["color_hex"]))
            text = (f"  {c['run_id']}"
                    f"   [{c['dist_m']:.0f} m away · {len(c['frames'])} frames]")
            item = QListWidgetItem(QIcon(px), text)
            item.setData(Qt.UserRole, c)
            self.list_w.addItem(item)

    def mousePressEvent(self, event):
        item = self.list_w.itemAt(self.list_w.mapFrom(self, event.pos()))
        if item is None:
            self.list_w.clearSelection()
            self._restore_all()
        super().mousePressEvent(event)

    def _single_click(self, item):
        c = item.data(Qt.UserRole)
        if not c or not self._crs: return
        if self.dock.highlight_mgr:
            self.dock.highlight_mgr.clear()
        self.dock.highlight_mgr = RouteHighlightManager(self._crs)
        self.dock.highlight_mgr.draw([c], self._route_coords)

    def _double_click(self, item):
        c = item.data(Qt.UserRole)
        if not c: return
        fi = c.get("frame_index", 0)
        if self.dock.highlight_mgr:
            self.dock.highlight_mgr.clear()
        self.dock.set_frames(c["frames"], fi, c["run_id"])
        self.dock.show_frame_page()

    def _restore_all(self):
        if not self.candidates or not self._crs: return
        if self.dock.highlight_mgr:
            self.dock.highlight_mgr.clear()
        self.dock.highlight_mgr = RouteHighlightManager(self._crs)
        self.dock.highlight_mgr.draw(self.candidates, self._route_coords)

    def _clear(self):
        if self.dock.highlight_mgr:
            self.dock.highlight_mgr.clear()
        self.dock.show_frame_page()

# =============================================================================
# ROUTE HIGHLIGHT MANAGER
# =============================================================================

class RouteHighlightManager:
    LAYER_NAME = "Dashcam Routes"

    def __init__(self, crs):
        self.crs      = crs
        self.line_lyr = None

    def draw(self, candidates, route_coords):
        self.clear()
        crs_str = self.crs.authid()

        self.line_lyr = QgsVectorLayer(
            f"LineString?crs={crs_str}", self.LAYER_NAME, "memory")
        lp = self.line_lyr.dataProvider()
        lp.addAttributes([QgsField("run_id", QVariant.String),
                           QgsField("color",  QVariant.String)])
        self.line_lyr.updateFields()

        lfeats = []
        for cand in candidates:
            coords = route_coords.get(cand["key"], [])
            if len(coords) < 2: continue
            pts = [QgsPointXY(x, y) for x, y in coords]
            col = cand["color_hex"]; rid = cand["run_id"]

            lf = QgsFeature(self.line_lyr.fields())
            lf.setGeometry(QgsGeometry.fromPolylineXY(pts))
            lf["run_id"] = rid; lf["color"] = col
            lfeats.append(lf)

        self.line_lyr.startEditing()
        self.line_lyr.dataProvider().addFeatures(lfeats)
        self.line_lyr.commitChanges()

        self._style_lines()
        self._add_labels()
        self._add_to_map()

    def clear(self):
        # Remove the single route layer
        for lyr in list(QgsProject.instance().mapLayers().values()):
            if lyr.name() == self.LAYER_NAME:
                QgsProject.instance().removeMapLayer(lyr)
        self.line_lyr = None

    def _style_lines(self):
        from qgis.core import (QgsMarkerLineSymbolLayer,
                                QgsSimpleMarkerSymbolLayer as _SML)
        root_rule = QgsRuleBasedRenderer.Rule(None)
        seen = set()
        
        for feat in self.line_lyr.getFeatures():
            c = feat["color"]
            if c in seen: continue
            seen.add(c)
            
            sym = QgsLineSymbol()

            # White outline for contrast
            outline = QgsSimpleLineSymbolLayer.create({"color": "#ffffff", "width": "5.0"})
            sym.changeSymbolLayer(0, outline)

            # Main colored line
            sl = QgsSimpleLineSymbolLayer.create({"color": c, "width": "3.0"})
            sym.appendSymbolLayer(sl)

            # Interval directional arrows
            dir_sym = QgsMarkerSymbol()
            dir_sl  = _SML()
            dir_sl.setShape(_SML.Shape.ArrowHead)
            dir_sl.setColor(QColor("#ffffff"))
            dir_sl.setStrokeColor(QColor("#ffffff"))
            dir_sl.setSize(5.0)
            dir_sl.setStrokeWidth(0.5)
            dir_sym.changeSymbolLayer(0, dir_sl)

            mll_dir = QgsMarkerLineSymbolLayer()
            mll_dir.setSubSymbol(dir_sym)
            mll_dir.setPlacement(QgsMarkerLineSymbolLayer.Placement.Interval)
            mll_dir.setInterval(20.0)
            mll_dir.setOffsetAlongLine(5.0)
            sym.appendSymbolLayer(mll_dir)

            # Start marker
            start_sym = QgsMarkerSymbol()
            start_sl  = _SML()
            start_sl.setShape(_SML.Shape.ArrowHead)
            start_sl.setColor(QColor(c))
            start_sl.setStrokeColor(QColor("#ffffff"))
            start_sl.setStrokeWidth(0.8)
            start_sl.setSize(7.0)
            start_sym.changeSymbolLayer(0, start_sl)

            mll_start = QgsMarkerLineSymbolLayer()
            mll_start.setSubSymbol(start_sym)
            mll_start.setPlacement(QgsMarkerLineSymbolLayer.Placement.FirstVertex)
            sym.appendSymbolLayer(mll_start)

            # End marker
            end_sym = QgsMarkerSymbol()
            end_sl  = _SML()
            end_sl.setShape(_SML.Shape.Circle)
            end_sl.setColor(QColor(c))
            end_sl.setStrokeColor(QColor("#ffffff"))
            end_sl.setStrokeWidth(0.6)
            end_sl.setSize(3.5)
            end_sym.changeSymbolLayer(0, end_sl)

            mll_end = QgsMarkerLineSymbolLayer()
            mll_end.setSubSymbol(end_sym)
            mll_end.setPlacement(QgsMarkerLineSymbolLayer.Placement.LastVertex)
            sym.appendSymbolLayer(mll_end)

            rule = QgsRuleBasedRenderer.Rule(sym)
            rule.setFilterExpression(f'"color" = \'{c}\'')
            root_rule.appendChild(rule)
            
        self.line_lyr.setRenderer(QgsRuleBasedRenderer(root_rule))

    def _add_labels(self):
        pal = QgsPalLayerSettings()
        pal.fieldName = "run_id"; pal.enabled = True
        pal.placement = QgsPalLayerSettings.Placement.Line
        tf = QgsTextFormat()
        tf.setSize(9); tf.setColor(QColor("#ffffff"))
        fnt = QFont("Arial"); fnt.setBold(True); tf.setFont(fnt)
        buf = QgsTextBufferSettings()
        buf.setEnabled(True); buf.setSize(1.2); buf.setColor(QColor("#000000"))
        tf.setBuffer(buf); pal.setFormat(tf)
        self.line_lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        self.line_lyr.setLabelsEnabled(True)

    def _add_to_map(self):
        # Add directly as a single layer without groups
        QgsProject.instance().addMapLayer(self.line_lyr)

# =============================================================================
# DATA HELPERS
# =============================================================================

def build_frames_index(frames_roots):
    idx = {}
    for root_str in frames_roots:
        root = Path(root_str)
        if not root.exists(): continue
        # Support direct folder or parent folder
        name = root.name
        mA = FOLDER_A.match(name); mB = FOLDER_B.match(name)
        if mA:   idx.setdefault(mA.group(1), []).append(str(root))
        elif mB: idx.setdefault(f"{mB.group(1)}_{mB.group(2)}", []).append(str(root))
        for p in root.rglob("*"):
            if not p.is_dir(): continue
            mA = FOLDER_A.match(p.name); mB = FOLDER_B.match(p.name)
            if mA:   idx.setdefault(mA.group(1), []).append(str(p))
            elif mB: idx.setdefault(f"{mB.group(1)}_{mB.group(2)}", []).append(str(p))
    return idx


def parse_html_objects(html_path):
    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        txt = f.read()
    obj_map   = {m.group(1).strip(): m.group(2).strip()
                 for m in TOOLTIP_RE.finditer(txt)}
    markers   = [(m.group(1), float(m.group(3)), float(m.group(2)))
                 for m in MARKER_DEF_RE.finditer(txt)]
    polylines = []
    for m in POLY_DEF_RE.finditer(txt):
        pairs  = PAIR_RE.findall(m.group(2))
        coords = [(float(ln), float(lt)) for lt, ln in pairs]
        if len(coords) >= 2:
            polylines.append((m.group(1), coords))
    return obj_map, markers, polylines


def build_click_index(obj_map, markers, polylines, crs):
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    xf    = QgsCoordinateTransform(wgs84, crs, QgsProject.instance())

    CELL_M    = 20.0
    cell_size = _metres_to_crs(CELL_M, crs)

    grid          = defaultdict(list)
    route_coords  = {}
    route_run_ids = {}
    total_routes  = 0

    for obj, crds in polylines:
        rid = obj_map.get(obj, "")
        m   = re.search(r"(\d{8})_(\d{6})", rid)
        k   = f"{m.group(1)}_{m.group(2)}" if m else ""
        if not k: continue
        total_routes += 1
        route_run_ids[k] = rid

        proj_pts = []
        n = len(crds)
        for i, c in enumerate(crds):
            try:
                p = xf.transform(QgsPointXY(*c))
                proj_pts.append((p.x(), p.y()))
                cell = (int(p.x() // cell_size), int(p.y() // cell_size))
                grid[cell].append({
                    "x": p.x(), "y": p.y(),
                    "run_id": rid, "key": k,
                    "seq": i, "n": n
                })
            except Exception:
                continue
        route_coords[k] = proj_pts

    return grid, total_routes, route_coords, route_run_ids, cell_size


def _load_frames_for_key(key, f_idx):
    dirs = f_idx.get(key, [])
    if not dirs:
        return [], []
    best_dir = max(dirs, key=lambda d: sum(
        1 for f in Path(d).iterdir() if IMG_RE.search(f.name)))
    pairs = sorted(
        [(int(IMG_RE.search(f.name).group(1)), str(f))
         for f in Path(best_dir).iterdir() if IMG_RE.search(f.name)],
        key=lambda x: x[0]
    )
    if not pairs:
        return [], []
    frame_numbers = [p[0] for p in pairs]
    frames        = [p[1] for p in pairs]
    return frames, frame_numbers


def _seq_to_frame_index(seq, n_gps, frame_numbers):
    if not frame_numbers:
        return 0
    n_frames = len(frame_numbers)
    if n_gps <= 1 or n_frames == 1:
        return 0
    t          = seq / (n_gps - 1)
    first_num  = frame_numbers[0]
    last_num   = frame_numbers[-1]
    target     = first_num + t * (last_num - first_num)
    lo, hi     = 0, n_frames - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if frame_numbers[mid] < target: lo = mid + 1
        else: hi = mid
    if lo > 0 and (target - frame_numbers[lo-1]) < (frame_numbers[lo] - target):
        return lo - 1
    return lo

# =============================================================================
# MAP TOOL
# =============================================================================

class DashcamMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, grid, f_idx, dock,
                 route_coords, route_run_ids, crs, radius_m, cell_size):
        super().__init__(canvas)
        self.canvas        = canvas
        self.grid          = grid
        self.f_idx         = f_idx
        self.dock          = dock
        self.route_coords  = route_coords
        self.route_run_ids = route_run_ids
        self.crs           = crs
        self.radius_m      = radius_m
        self.cell_size     = cell_size
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, e):
        pt = self.toMapCoordinates(e.pos())
        px, py = pt.x(), pt.y()

        radius_crs = _metres_to_crs(self.radius_m, self.crs)
        cell_r     = int(math.ceil(radius_crs / self.cell_size)) + 1
        cx0        = int(px // self.cell_size)
        cy0        = int(py // self.cell_size)

        best_for_key = {}
        for dcx in range(-cell_r, cell_r + 1):
            for dcy in range(-cell_r, cell_r + 1):
                cell = (cx0 + dcx, cy0 + dcy)
                for p in self.grid.get(cell, []):
                    dist_crs = math.hypot(p["x"] - px, p["y"] - py)
                    if dist_crs > radius_crs:
                        continue
                    dist_m = (dist_crs / DEG_PER_METRE
                               if _crs_uses_degrees(self.crs)
                               else dist_crs)
                    k = p["key"]
                    if k not in best_for_key or dist_m < best_for_key[k]["dist_m"]:
                        best_for_key[k] = {**p, "dist_m": dist_m}

        if not best_for_key:
            self.dock.title.setText(
                f"Nothing found within {self.radius_m} m of your click. "
                "Try increasing the search radius."
            )
            self.dock.found_lbl.setText("0 routes found")
            return

        candidates = []
        for color_i, (key, p) in enumerate(best_for_key.items()):
            frames, frame_numbers = _load_frames_for_key(key, self.f_idx)
            if not frames:
                continue

            coords   = self.route_coords.get(key, [])
            n_gps    = len(coords)
            best_seq = p["seq"]
            best_d2  = float("inf")
            for si, (cx, cy) in enumerate(coords):
                d2 = (cx - px)**2 + (cy - py)**2
                if d2 < best_d2:
                    best_d2 = d2
                    best_seq = si

            fi = _seq_to_frame_index(best_seq, n_gps, frame_numbers)

            candidates.append({
                "run_id":        p["run_id"],
                "key":           key,
                "frame_index":   fi,
                "dist_m":        p["dist_m"],
                "color_hex":     _route_color(color_i),
                "frames":        frames,
                "frame_numbers": frame_numbers,
            })

        if not candidates:
            n_nearby = len(best_for_key)
            self.dock.title.setText(
                f"{n_nearby} route(s) pass within {self.radius_m} m "
                "but none have local frames. Add folders via ⚙."
            )
            self.dock.found_lbl.setText(
                f"{n_nearby} route(s) nearby – 0 with local frames")
            return

        candidates.sort(key=lambda c: c["dist_m"])
        n = len(candidates)
        self.dock.found_lbl.setText(
            f"{n} route{'s' if n > 1 else ''} with frames near this point")

        if n == 1:
            c = candidates[0]
            if self.dock.highlight_mgr:
                self.dock.highlight_mgr.clear()
            self.dock.set_frames(c["frames"], c["frame_index"], c["run_id"])
            self.dock.show_frame_page(); self.dock.show()
        else:
            self.dock.show_route_picker(candidates, self.route_coords, self.crs)
            self.dock.show()

# =============================================================================
# DOCK WIDGET
# =============================================================================

class DashcamDock(QDockWidget):
    def __init__(self, controller, parent=None):
        super().__init__("Dashcam Viewer", parent)
        self.setObjectName("DashcamViewerDock")
        self.setFeatures(QDockWidget.AllDockWidgetFeatures)
        self.controller    = controller
        self.highlight_mgr = None
        self.frame_files, self.i = [], 0

        container = QWidget()
        v = QVBoxLayout(container); v.setContentsMargins(5, 5, 5, 5)

        # Top bar
        top = QHBoxLayout()
        from qgis.PyQt.QtWidgets import QLineEdit as _QLE
        self.title = _QLE("")
        self.title.setReadOnly(True)
        self.title.setFrame(False)
        self.title.setStyleSheet(
            "QLineEdit { background: transparent; font-weight: bold; "
            "font-size: 12px; border: none; }")
        top.addWidget(self.title, 1)
        self.btn_cfg = QToolButton()
        self.btn_cfg.setText("⚙"); self.btn_cfg.setToolTip("Edit paths")
        self.btn_cfg.setFixedSize(QSize(28, 28))
        self.btn_cfg.clicked.connect(self._open_settings)
        top.addWidget(self.btn_cfg)
        v.addLayout(top)

        # Radius row
        rad_row = QHBoxLayout()
        rad_row.addWidget(QLabel("Search radius:"))
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(5, 500); self.radius_spin.setSuffix(" m")
        self.radius_spin.setFixedWidth(75)
        _, _, saved_r = _load_settings()
        self.radius_spin.setValue(saved_r)
        self.radius_spin.valueChanged.connect(self._on_radius_changed)
        rad_row.addWidget(self.radius_spin)
        self.found_lbl = QLabel("")
        self.found_lbl.setStyleSheet("color:#1a6e9e;font-weight:bold;")
        rad_row.addWidget(self.found_lbl)
        rad_row.addStretch()
        v.addLayout(rad_row)

        # Stacked pages
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)

        page0 = QWidget(); p0v = QVBoxLayout(page0); p0v.setContentsMargins(0,0,0,0)
        self.img = ZoomLabel(); self.img.setStyleSheet("background-color:#111;")
        self.scroll = QScrollArea(); self.scroll.setWidget(self.img)
        self.scroll.setAlignment(Qt.AlignCenter); self.scroll.setMinimumHeight(200)
        p0v.addWidget(self.scroll, 1)
        self.stack.addWidget(page0)

        self.route_picker = RoutePicker(dock=self)
        self.stack.addWidget(self.route_picker)

        # Slider
        self.slider = QSlider(Qt.Horizontal)
        v.addWidget(self.slider)

        # Speed row
        sp = QHBoxLayout()
        sp.addWidget(QLabel("Slow"))
        self.speed_sl = QSlider(Qt.Horizontal)
        self.speed_sl.setRange(20, 500)
        self.speed_sl.setValue(PLAY_INTERVAL_MS)
        self.speed_sl.setInvertedAppearance(True)
        self.speed_sl.setFixedWidth(110)
        self.speed_sl.valueChanged.connect(self._upd_speed)
        sp.addWidget(self.speed_sl)
        sp.addWidget(QLabel("Fast"))
        self.speed_lbl = QLabel(f"{1000/PLAY_INTERVAL_MS:.1f} fps")
        self.speed_lbl.setFixedWidth(55)
        sp.addWidget(self.speed_lbl)
        sp.addStretch()
        from qgis.PyQt.QtWidgets import QLineEdit as _QLE3
        self.info = _QLE3("")
        self.info.setReadOnly(True)
        self.info.setFrame(False)
        self.info.setStyleSheet(
            "QLineEdit { background: transparent; color: #333; "
            "font-size: 11px; border: none; }")
        self.info.setMinimumWidth(250)
        sp.addWidget(self.info)
        v.addLayout(sp)

        # Buttons
        br = QHBoxLayout()
        self.btn_rw  = QPushButton("◀◀ Rewind")
        self.btn_pv  = QPushButton("◀ Prev")
        self.btn_nx  = QPushButton("Next ▶")
        self.btn_pl  = QPushButton("▶▶ Play")
        self.btn_st  = QPushButton("■ Stop")
        self.btn_clr = QPushButton("✕ Dashcam Routes")
        self.btn_clr.setStyleSheet("color:#c00;")
        self.btn_clr.setToolTip("Remove dashcam route layers from the map")
        for b in [self.btn_rw, self.btn_pv, self.btn_nx,
                  self.btn_pl, self.btn_st, self.btn_clr]:
            br.addWidget(b)
        v.addLayout(br)
        self.setWidget(container)

        self.btn_rw.clicked.connect(self._start_rw)
        self.btn_pv.clicked.connect(self.prev)
        self.btn_nx.clicked.connect(self.next)
        self.btn_pl.clicked.connect(self._start_pl)
        self.btn_st.clicked.connect(self.stop_all)
        self.btn_clr.clicked.connect(self._clr_hl)
        self.slider.valueChanged.connect(self.on_slider)

        self.timer  = QTimer(self, interval=PLAY_INTERVAL_MS, timeout=self._next_a)
        self.rtimer = QTimer(self, interval=PLAY_INTERVAL_MS, timeout=self._prev_a)

    def _on_radius_changed(self, val):
        _save_settings(*_load_settings()[:2], radius=val)
        if self.controller.tool:
            self.controller.tool.radius_m = val

    def show_route_picker(self, candidates, route_coords, crs):
        self.highlight_mgr = RouteHighlightManager(crs)
        self.highlight_mgr.draw(candidates, route_coords)
        self.route_picker.populate(candidates, route_coords=route_coords, crs=crs)
        self.stack.setCurrentIndex(1)

    def show_frame_page(self): self.stack.setCurrentIndex(0)

    def _clr_hl(self):
        if self.highlight_mgr: self.highlight_mgr.clear()

    def _open_settings(self):
        html, roots, radius = _load_settings()
        dlg = SetupDialog(html, roots, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            _save_settings(dlg.get_html(), dlg.get_roots(), radius)
            self.controller.reload()

    def _upd_speed(self, v):
        self.timer.setInterval(v); self.rtimer.setInterval(v)
        self.speed_lbl.setText(f"{1000/v:.1f} fps")

    def stop_all(self):  self.timer.stop(); self.rtimer.stop()
    def _start_pl(self): self.stop_all(); self.timer.start()
    def _start_rw(self): self.stop_all(); self.rtimer.start()

    def _next_a(self):
        if self.i < len(self.frame_files) - 1:
            self.i += 1; self.img.zoom_factor = 1.0; self.render_frame()
        else: self.stop_all()

    def _prev_a(self):
        if self.i > 0:
            self.i -= 1; self.img.zoom_factor = 1.0; self.render_frame()
        else: self.stop_all()

    def prev(self): self.stop_all(); self._prev_a()
    def next(self): self.stop_all(); self._next_a()

    def set_frames(self, frames, idx=0, title=""):
        self.frame_files, self.i = frames or [], idx
        self.img.zoom_factor = 1.0
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self.frame_files) - 1))
        self.slider.setValue(self.i)
        self.slider.blockSignals(False)
        if title:
            self.title.setText(title)
            self.title.setCursorPosition(0)
        self.render_frame()

    def render_frame(self):
        if not self.frame_files: return
        pix = QPixmap(self.frame_files[self.i])
        if not pix.isNull():
            from qgis.PyQt.QtWidgets import QApplication
            QApplication.processEvents()
            vw = max(self.scroll.viewport().width(),  4)
            vh = max(self.scroll.viewport().height(), 4)
            base = pix.scaled(vw, vh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            nw   = int(base.width()  * self.img.zoom_factor)
            nh   = int(base.height() * self.img.zoom_factor)
            self.img.setFixedSize(max(nw, vw), max(nh, vh))
            self.img.setPixmap(
                pix.scaled(nw, nh, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        import re as _re
        frame_tag = ""
        if self.frame_files:
            _m = _re.search(r"frame_([0-9]+)", self.frame_files[self.i], _re.IGNORECASE)
            if _m: frame_tag = f"  |  frame_{_m.group(1)}"
        self.info.setText(
            f"Frame {self.i+1}/{len(self.frame_files)}"
            f"{frame_tag}"
            f"  |  Zoom {self.img.zoom_factor:.1f}x")
        self.info.setCursorPosition(0)
        self.slider.blockSignals(True); self.slider.setValue(self.i)
        self.slider.blockSignals(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.render_frame()

    def on_slider(self, v): self.i = v; self.render_frame()

# =============================================================================
# CONTROLLER
# =============================================================================

class DashcamController:
    def __init__(self, iface_ref):
        self.iface         = iface_ref
        self.dock          = None
        self.action        = None
        self.tool          = None
        self.grid          = defaultdict(list)
        self.f_idx         = {}
        self.route_coords  = {}
        self.route_run_ids = {}
        self.project_crs   = None
        self.cell_size     = 1.0

    def start(self):
        self._ensure_dock()
        html, roots, radius = _load_settings()
        if not html or not os.path.isfile(html):
            dlg = SetupDialog(html, roots, parent=self.iface.mainWindow())
            dlg.setWindowTitle("Dashcam Viewer – First-Time Setup")
            if dlg.exec_() != QDialog.Accepted: return
            html = dlg.get_html(); roots = dlg.get_roots()
            _save_settings(html, roots, radius)
        self._load_data(html, roots)
        self._ensure_action()

    def reload(self):
        html, roots, radius = _load_settings()
        if not html or not os.path.isfile(html):
            QMessageBox.warning(None, "Dashcam Viewer", "HTML path invalid.")
            return
        self._load_data(html, roots)
        if self.action: self.action.setChecked(False)
        self.dock.title.setText("Paths updated – click the camera tool.")

    def _ensure_dock(self):
        if not self.dock:
            self.dock = DashcamDock(controller=self, parent=self.iface.mainWindow())

    def _ensure_action(self):
        if not self.action:
            icon = QIcon(":/images/themes/default/mIconCamera.svg")
            self.action = QAction(icon, "Dashcam Viewer", self.iface.mainWindow())
            self.action.setCheckable(True)
            self.action.setToolTip(
                "Dashcam Viewer\n"
                "Click to activate, then click on a GPS route on the map.\n"
                "Only routes with local frames are shown.\n"
                "Arrow = driving direction  ·  Circle = end of route")
            self.action.toggled.connect(self._toggled)

    def _load_data(self, html, roots):
        try:
            self.project_crs = QgsProject.instance().crs()
            obj_map, mks, ply = parse_html_objects(html)
            (self.grid, total,
             self.route_coords,
             self.route_run_ids,
             self.cell_size) = build_click_index(obj_map, mks, ply, self.project_crs)
            self.f_idx = build_frames_index(roots)
        except Exception as exc:
            QMessageBox.critical(
                None, "Dashcam Viewer – Load Error",
                f"Failed to load:\n\n{exc}\n\nCheck paths via ⚙.")

    def _toggled(self, checked):
        _, _, radius = _load_settings()
        if checked:
            self.tool = DashcamMapTool(
                self.iface.mapCanvas(), self.grid, self.f_idx, self.dock,
                self.route_coords, self.route_run_ids,
                self.project_crs, radius, self.cell_size
            )
            self.iface.mapCanvas().setMapTool(self.tool)
            self.dock.show()
        else:
            if self.tool:
                self.iface.mapCanvas().unsetMapTool(self.tool)
            self.tool = None
            if self.dock:
                if self.dock.highlight_mgr:
                    self.dock.highlight_mgr.clear()
                self.dock.stop_all()
                self.dock.hide()

    # ── Public methods called from lane_map_QC_tool.py ────────────────────────
    def get_paths_silent(self):
        html, roots, _ = _load_settings()
        if html and os.path.isfile(html) and roots:
            return html, roots
        return None, None

    def reconfigure(self, parent=None):
        html, roots, radius = _load_settings()
        dlg = SetupDialog(html, roots, parent=parent)
        if dlg.exec_() == QDialog.Accepted:
            _save_settings(dlg.get_html(), dlg.get_roots(), radius)
            self.reload()