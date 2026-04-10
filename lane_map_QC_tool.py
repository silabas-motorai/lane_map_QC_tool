# =============================================================================
#  QC SUITE  —  lane_map_QC_tool.py
#  Toolbar + StreetViewDock + UnifiedMapTool
# =============================================================================
# -*- coding: utf-8 -*-
import os
from qgis.core import Qgis, QgsVectorLayer
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
    then delegates dashcam lookup to DashcamController."""

    _WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

    def __init__(self, canvas, dashcam_ctrl, sv_dock):
        super().__init__(canvas)
        self.canvas       = canvas
        self.dashcam_ctrl = dashcam_ctrl
        self.sv_dock      = sv_dock
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, e):
        from .dashcam_locator import DashcamMapTool as _DCTool, _load_settings

        pt = self.toMapCoordinates(e.pos())
        project_crs = QgsProject.instance().crs()

        # ── Street View (always) ──────────────────────────────────────────
        try:
            xf  = QgsCoordinateTransform(project_crs, self._WGS84, QgsProject.instance())
            wgs = xf.transform(pt)
            self.sv_dock.load_location(wgs.y(), wgs.x())
        except Exception as ex:
            _iface.mainWindow().statusBar().showMessage(f"SV error: {ex}", 4000)

        # ── Dashcam — delegate to controller's map tool ───────────────────
        ctrl = self.dashcam_ctrl
        if not ctrl.grid or not ctrl.f_idx:
            return
        _, _, radius = _load_settings()
        tmp_tool = _DCTool(
            self.canvas, ctrl.grid, ctrl.f_idx, ctrl.dock,
            ctrl.route_coords, ctrl.route_run_ids,
            ctrl.project_crs, radius, ctrl.cell_size
        )
        tmp_tool.canvasReleaseEvent(e)

# =============================================================================
#  MAIN PLUGIN CLASS
# =============================================================================

class QcSuitePlugin:

    def __init__(self, iface):
        self.iface          = iface
        self.toolbar        = None
        self.actions        = []
        self.dashcam_ctrl   = None
        self.sv_dock        = None
        self.map_tool       = None
        self._act_cam       = None

    # ── QGIS lifecycle ───────────────────────────────────────────────────────

    def initGui(self):
        self.toolbar = self.iface.addToolBar("QC Suite")
        self.toolbar.setObjectName("QcSuiteToolbar")

        act_qc = QAction("\U0001F5FA  Lane Map Quality Check", self.iface.mainWindow())
        act_qc.setToolTip("Run integrity check + build analysis layers on active layer")
        act_qc.triggered.connect(self.run_lane_qc)
        self.toolbar.addAction(act_qc)
        self.actions.append(act_qc)

        self.toolbar.addSeparator()

        act_cam = QAction("\U0001F3A5  Dashcam / Street View", self.iface.mainWindow())
        act_cam.setToolTip("Click on the map to show dashcam frame + street-level imagery")
        act_cam.setCheckable(True)
        act_cam.toggled.connect(self._on_cam_toggled)
        self.toolbar.addAction(act_cam)
        self.actions.append(act_cam)
        self._act_cam = act_cam

        act_paths = QAction("\U0001F4C2 Set Dashcam Paths", self.iface.mainWindow())
        act_paths.setToolTip("Change the HTML overview map and frames root folder")
        act_paths.triggered.connect(self._reconfigure_paths)
        self.toolbar.addAction(act_paths)
        self.actions.append(act_paths)
        
        self.toolbar.addSeparator()
        
        act_routing = QAction("\U0001F697 Lane Routing Simulation", self.iface.mainWindow())
        act_routing.setToolTip("Select start and end points to simulate routing on centerlines")
        act_routing.triggered.connect(self.run_lane_routing)
        self.toolbar.addAction(act_routing)
        self.actions.append(act_routing)

        self._build_docks()

    def unload(self):
        if self.map_tool:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
        if self.dashcam_ctrl and self.dashcam_ctrl.dock:
            self.dashcam_ctrl.dock.close()
            self.iface.removeDockWidget(self.dashcam_ctrl.dock)
        if self.sv_dock:
            self.sv_dock.close()
            self.iface.removeDockWidget(self.sv_dock)
        for act in self.actions:
            self.toolbar.removeAction(act)
        if self.toolbar:
            del self.toolbar

    # ── Dock setup ───────────────────────────────────────────────────────────

    def _build_docks(self):
        from .dashcam_locator import DashcamController

        self.dashcam_ctrl = DashcamController(self.iface)
        self.dashcam_ctrl._ensure_dock()
        dock = self.dashcam_ctrl.dock
        self.iface.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.hide()

        self.sv_dock = StreetViewDock(self.iface.mainWindow())
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.sv_dock)
        self.sv_dock.hide()

        self.iface.mainWindow().splitDockWidget(dock, self.sv_dock, Qt.Vertical)

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

    # ── Lane Routing ──────────────────────────────────────────────────────────

    def run_lane_routing(self):
        try:
            # Import inside function to avoid startup issues
            from . import routing_tool
            
            layer = self.iface.activeLayer()
            if not layer or not isinstance(layer, QgsVectorLayer):
                self.iface.messageBar().pushMessage(
                    "Routing", "Please select a vector layer first.", 
                    level=Qgis.Warning, duration=4
                )
                return

            # Trigger the tool. 
            # Note: Ensure run_routing in routing_tool.py accepts the layer argument.
            routing_tool.run_routing(layer)

        except ImportError:
            self.iface.messageBar().pushMessage(
                "Error", "routing_tool.py file not found in plugin folder!", 
                level=Qgis.Critical, duration=5
            )
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Error", f"An unexpected error occurred: {str(e)}", 
                level=Qgis.Critical, duration=5
            )
    # ── Dashcam / Street View toggle ─────────────────────────────────────────

    def _reconfigure_paths(self):
        self.dashcam_ctrl.reconfigure(self.iface.mainWindow())

    def _on_cam_toggled(self, checked):
        if checked:
            self.dashcam_ctrl.dock.show()
            self.sv_dock.show()
            self._activate_map_tool()
        else:
            if self.map_tool:
                self.iface.mapCanvas().unsetMapTool(self.map_tool)
            self.map_tool = None

    def _activate_map_tool(self):
        from .dashcam_locator import (
            build_frames_index, parse_html_objects, build_click_index,
            _metres_to_crs, DEG_PER_METRE
        )
        from collections import defaultdict

        html_path, roots = self.dashcam_ctrl.get_paths_silent()
        grid = cell_size = f_idx = None
        project_crs = QgsProject.instance().crs()

        if html_path:
            try:
                obj_map, mks, ply = parse_html_objects(html_path)
                grid_data = build_click_index(obj_map, mks, ply, project_crs)
                grid, _, route_coords, route_run_ids, cell_size = grid_data
                f_idx = build_frames_index(roots)
                self.dashcam_ctrl.grid          = grid
                self.dashcam_ctrl.f_idx         = f_idx
                self.dashcam_ctrl.route_coords  = route_coords
                self.dashcam_ctrl.route_run_ids = route_run_ids
                self.dashcam_ctrl.project_crs   = project_crs
                self.dashcam_ctrl.cell_size     = cell_size
            except Exception:
                grid = f_idx = None

        self.map_tool = UnifiedMapTool(
            self.iface.mapCanvas(),
            self.dashcam_ctrl, self.sv_dock,
        )
        self.iface.mapCanvas().setMapTool(self.map_tool)
        self.iface.mainWindow().statusBar().showMessage(
            "Click the map → street view updates. Set dashcam paths for dashcam frames.", 5000
        )
