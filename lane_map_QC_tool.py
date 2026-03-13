# =============================================================================
#  QC SUITE  —  lane_map_QC_tool.py
#  Toolbar + StreetViewDock + UnifiedMapTool
# =============================================================================

import os
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSizePolicy, QMessageBox
)
from qgis.PyQt.QtWebKitWidgets import QWebView, QWebPage
from qgis.PyQt.QtWebKit import QWebSettings
from qgis.PyQt.QtGui import QDesktopServices

from qgis.gui import QgsMapToolEmitPoint
from qgis.core import (
    QgsProject, QgsPointXY, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, QgsVectorLayer
)
from qgis.utils import iface as _iface

# =============================================================================
#  STREET VIEW DOCK
# =============================================================================

class StreetViewDock(QDockWidget):
    """Embedded street-level imagery: Google Street View + Mapillary.
    Tries to embed directly; external links open in system browser."""

    CBK_URL         = "https://cbk0.google.com/cbk?output=json&ll={lat},{lng}&radius=50"
    GSV_BROWSER_URL = "https://maps.google.com/?layer=c&cbll={lat},{lng}"
    MLY_BROWSER_URL = "https://www.mapillary.com/app/?lat={lat}&lng={lng}&z=17&focus=photo"

    def __init__(self, parent=None):
        super().__init__("🌍 Street View", parent)
        self.setObjectName("QcStreetViewDock")
        self.setFeatures(QDockWidget.AllDockWidgetFeatures)
        self.setAllowedAreas(Qt.AllDockWidgetAreas)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(3, 3, 3, 3)
        v.setSpacing(4)

        row = QHBoxLayout()
        self.btn_gsv = QPushButton("Google Street View")
        self.btn_mly = QPushButton("Mapillary")
        for btn in (self.btn_gsv, self.btn_mly):
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            row.addWidget(btn)
        self.btn_gsv.setChecked(True)
        self.btn_gsv.clicked.connect(lambda: self._switch("gsv"))
        self.btn_mly.clicked.connect(lambda: self._switch("mapillary"))

        self.btn_browser = None  # removed

        self.coords_label = QLabel("Click the map to load street-level imagery.")
        self.coords_label.setWordWrap(True)

        self.web = QWebView()
        self.web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.web.setMinimumHeight(260)

        ws = self.web.settings()
        ws.setAttribute(QWebSettings.JavascriptEnabled,               True)
        ws.setAttribute(QWebSettings.LocalContentCanAccessRemoteUrls, True)
        ws.setAttribute(QWebSettings.LocalStorageEnabled,             True)

        from qgis.PyQt.QtNetwork import QNetworkDiskCache
        import tempfile
        cache = QNetworkDiskCache()
        cache.setCacheDirectory(os.path.join(tempfile.gettempdir(), "lane_map_QC_tool_webcache"))
        self.web.page().networkAccessManager().setCache(cache)
        self.web.page().setLinkDelegationPolicy(QWebPage.DelegateAllLinks)
        self.web.linkClicked.connect(lambda url: QDesktopServices.openUrl(url))
        self.web.load(QUrl("about:blank"))

        v.addLayout(row)
        v.addWidget(self.web, 1)
        self.setWidget(container)

        self._source  = "gsv"
        self._lat     = self._lng = None
        self._ext_url = ""
        self._thread  = None

    GSV_BROWSER_URL = "https://maps.google.com/?layer=c&cbll={lat},{lng}"
    MLY_BROWSER_URL = "https://www.mapillary.com/app/?lat={lat}&lng={lng}&z=17"

    def _switch(self, source):
        self._source = source
        self.btn_gsv.setChecked(source == "gsv")
        self.btn_mly.setChecked(source == "mapillary")
        if self._lat is not None:
            self._load()

    def _load(self):
        lat, lng = self._lat, self._lng
        if self._source == "gsv":
            self._ext_url = self.GSV_BROWSER_URL.format(lat=lat, lng=lng)
            color, label = "#1a73e8", "Google Street View"
        else:
            self._ext_url = self.MLY_BROWSER_URL.format(lat=lat, lng=lng)
            color, label = "#05CB63", "Mapillary"

        ext_url = self._ext_url
        html = f"""<!DOCTYPE html><html>
<head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{margin:0;padding:0;box-sizing:border-box}}
  body {{display:flex;flex-direction:column;height:100vh;
         font-family:Arial,sans-serif;background:#1a1a1a}}
  #map {{flex:1}}
  .tip {{color:#aaa;font-size:11px;text-align:center;padding:5px 0;background:#222}}
  .leaflet-popup-content a {{
    color:{color};font-weight:bold;text-decoration:none;font-size:13px;
  }}
  .leaflet-popup-content a:hover {{text-decoration:underline}}
</style>
</head><body>
<div class="tip">📍 {lat:.6f}, {lng:.6f} — click the marker to open in browser</div>
<div id="map"></div>
<script>
var map = L.map('map').setView([{lat},{lng}], 18);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{attribution:'© OSM', maxZoom:19}}).addTo(map);
var marker = L.circleMarker([{lat},{lng}],
  {{radius:11, color:'{color}', fillColor:'{color}',
    fillOpacity:0.9, weight:3}}).addTo(map);
marker.bindPopup(
  '<a href="{ext_url}" target="_blank">🔗 Open {label}</a>'
).openPopup();
marker.on('click', function() {{
  window.open('{ext_url}', '_blank');
}});
</script>
</body></html>"""
        self.web.setHtml(html, QUrl("https://unpkg.com"))

    def _open_in_browser(self):
        if self._ext_url:
            QDesktopServices.openUrl(QUrl(self._ext_url))

    def load_location(self, lat, lng):
        self._lat, self._lng = lat, lng
        self._load()
        self.show()
        self.raise_()

# =============================================================================
#  UNIFIED MAP TOOL  —  one click → dashcam + street view
# =============================================================================

class UnifiedMapTool(QgsMapToolEmitPoint):
    """On map click: transforms to WGS84 → updates StreetViewDock,
    then finds nearest dashcam frame → updates DashcamDock."""

    _WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

    def __init__(self, canvas, grid, f_idx, dashcam_dock, sv_dock,
                 grid_cell_m, search_m):
        super().__init__(canvas)
        self.canvas       = canvas
        self.grid         = grid
        self.f_idx        = f_idx
        self.dashcam_dock = dashcam_dock
        self.sv_dock      = sv_dock
        self.grid_cell_m  = grid_cell_m
        self.search_m     = search_m
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, e):
        from .dashcam_locator import list_frames_in_folder, pick_best_folder

        pt = self.toMapCoordinates(e.pos())
        project_crs = QgsProject.instance().crs()

        # ── Street View (always) ──────────────────────────────────────────
        try:
            xf = QgsCoordinateTransform(project_crs, self._WGS84, QgsProject.instance())
            wgs = xf.transform(pt)
            self.sv_dock.load_location(wgs.y(), wgs.x())
        except Exception as ex:
            _iface.mainWindow().statusBar().showMessage(f"SV error: {ex}", 4000)

        # ── Dashcam (nearest point) ───────────────────────────────────────
        if not self.grid or not self.f_idx:
            return  # no dashcam data configured, Street View already updated
        ix = int(pt.x() // self.grid_cell_m)
        iy = int(pt.y() // self.grid_cell_m)
        best, best_d = None, 1e18
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for p in self.grid.get((ix+dx, iy+dy), []):
                    d = ((p["x"] - pt.x())**2 + (p["y"] - pt.y())**2) ** 0.5
                    if d < best_d:
                        best_d, best = d, p

        if best and best_d <= self.search_m:
            dirs = self.f_idx.get(best["key"], [])
            if dirs:
                frames = list_frames_in_folder(pick_best_folder(dirs))
                idx    = int(best["t"] * (len(frames) - 1))
                self.dashcam_dock.set_frames(frames, idx, best["run_id"])
                self.dashcam_dock.show()
        else:
            _iface.mainWindow().statusBar().showMessage(
                f"No dashcam data within {self.search_m} m — Street View updated.", 4000
            )

# =============================================================================
#  MAIN PLUGIN CLASS
# =============================================================================

class QcSuitePlugin:

    def __init__(self, iface):
        self.iface        = iface
        self.toolbar      = None
        self.actions      = []
        self.dashcam_dock = None
        self.sv_dock      = None
        self.map_tool     = None
        self._act_cam     = None

    # ── QGIS lifecycle ───────────────────────────────────────────────────────

    def initGui(self):
        self.toolbar = self.iface.addToolBar("QC Suite")
        self.toolbar.setObjectName("QcSuiteToolbar")

        # Run QC
        act_qc = QAction("🗺  Lane Map Quality Check", self.iface.mainWindow())
        act_qc.setToolTip("Run integrity check + build analysis layers on active layer")
        act_qc.triggered.connect(self.run_lane_qc)
        self.toolbar.addAction(act_qc)
        self.actions.append(act_qc)

        self.toolbar.addSeparator()

        # Dashcam + Street View toggle
        act_cam = QAction("🎥  Dashcam / Street View", self.iface.mainWindow())
        act_cam.setToolTip("Click on the map to show dashcam frame + street-level imagery")
        act_cam.setCheckable(True)
        act_cam.toggled.connect(self._on_cam_toggled)
        self.toolbar.addAction(act_cam)
        self.actions.append(act_cam)
        self._act_cam = act_cam

        # Change dashcam paths
        act_paths = QAction("📂  Set Dashcam Paths", self.iface.mainWindow())
        act_paths.setToolTip("Change the HTML overview map and frames root folder")
        act_paths.triggered.connect(self._reconfigure_paths)
        self.toolbar.addAction(act_paths)
        self.actions.append(act_paths)

        # Build docks (hidden until first use)
        self._build_docks()

    def unload(self):
        if self.map_tool:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
        for dock in (self.dashcam_dock, self.sv_dock):
            if dock:
                dock.close()
                self.iface.removeDockWidget(dock)
        for act in self.actions:
            self.toolbar.removeAction(act)
        if self.toolbar:
            del self.toolbar

    # ── Dock setup ───────────────────────────────────────────────────────────

    def _build_docks(self):
        from .dashcam_locator import DashcamDock

        self.dashcam_dock = DashcamDock(self.iface.mainWindow())
        self.dashcam_dock.setObjectName("QcDashcamDock")
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dashcam_dock)
        self.dashcam_dock.hide()

        self.sv_dock = StreetViewDock(self.iface.mainWindow())
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.sv_dock)
        self.sv_dock.hide()

        # Stack vertically on the right: dashcam on top, street view below
        self.iface.mainWindow().splitDockWidget(
            self.dashcam_dock, self.sv_dock, Qt.Vertical
        )

    # ── Lane QC ──────────────────────────────────────────────────────────────

    def run_lane_qc(self):
        from . import lane_qc_tool
        layer = self.iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            self.iface.messageBar().pushMessage(
                "QC Suite", "Please select a vector layer first.", level=1, duration=4
            )
            return
        lane_qc_tool.run_qc(layer)

    # ── Dashcam / Street View toggle ─────────────────────────────────────────

    def _reconfigure_paths(self):
        from .dashcam_locator import DashcamPathDialog
        DashcamPathDialog.reconfigure(self.iface.mainWindow())

    def _on_cam_toggled(self, checked):
        if checked:
            # Open both panels immediately — no path dialog at this point
            self.dashcam_dock.show()
            self.sv_dock.show()
            self._activate_map_tool()
        else:
            if self.map_tool:
                self.iface.mapCanvas().unsetMapTool(self.map_tool)
            self.map_tool = None

    def _activate_map_tool(self):
        from .dashcam_locator import (
            DashcamPathDialog,
            build_frames_index, parse_html_objects, build_click_index,
            NEAREST_SEARCH_METERS, GRID_CELL_M
        )

        # Try to load dashcam data if paths are already configured
        html_path, frames_root = DashcamPathDialog.get_paths_silent()
        f_idx = grid = None

        if html_path:
            try:
                f_idx             = build_frames_index(frames_root)
                obj_map, mks, ply = parse_html_objects(html_path)
                grid              = build_click_index(
                    obj_map, mks, ply, QgsProject.instance().crs()
                )
            except Exception:
                f_idx = grid = None  # dashcam unavailable, Street View still works

        self.map_tool = UnifiedMapTool(
            self.iface.mapCanvas(),
            grid, f_idx,
            self.dashcam_dock, self.sv_dock,
            GRID_CELL_M, NEAREST_SEARCH_METERS
        )
        self.iface.mapCanvas().setMapTool(self.map_tool)
        self.iface.mainWindow().statusBar().showMessage(
            "Click the map → street view updates. Set dashcam paths for dashcam frames.", 5000
        )
