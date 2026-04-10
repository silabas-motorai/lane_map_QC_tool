from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsWkbTypes, QgsLineSymbol, QgsArrowSymbolLayer,
    QgsPointXY, QgsTextFormat, QgsTextBufferSettings, QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling, QgsSimpleLineSymbolLayer, QgsSpatialIndex, 
    QgsRectangle, QgsSingleSymbolRenderer
)
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.utils import iface
from collections import defaultdict, deque

# --- CONFIGURATION ---
REVERSE_WAY_IDS  = {100, 101, 102, 400, 401, 402, 403, 500}
ROUTE_LAYER_NAME = "Route Simulation"
DEBUG_LAYER_NAME = "DISCONNECTED_PATH_DEBUG"
GROUP_NAME       = "Lane Map Analysis"
MAX_ROUTES       = 20
MAX_DEPTH        = 200

_PALETTE = ["#e6194b","#3cb44b","#4363d8","#f58231","#911eb4","#42d4f4","#f032e6"]
def _color(i): return _PALETTE[i % len(_PALETTE)]

# --- UTILITIES ---
def _log(msg, level=Qgis.Info):
    iface.messageBar().pushMessage("Routing", msg, level=level, duration=4)

def _fld(feat, name, default=""):
    try: return feat[name] if feat[name] is not None else default
    except: return default

def _get_polyline(feat):
    geom = feat.geometry()
    if not geom: return None
    return geom.asPolyline() if not geom.isMultipart() else geom.asMultiPolyline()[0]

def _clear_previous_layers():
    to_remove = [lyr for lyr in QgsProject.instance().mapLayers().values()
                 if lyr.name().startswith(ROUTE_LAYER_NAME) or lyr.name() == DEBUG_LAYER_NAME]
    if to_remove:
        QgsProject.instance().removeMapLayers([l.id() for l in to_remove])

# --- CORE ENGINE ---
def build_graph(layer):
    """Parses layer features into a directed adjacency list with STRICT snapping requirement."""
    features = [f for f in layer.getFeatures() if str(_fld(f, 'lane_type')).strip().lower() == 'centerline']
    if not features: return {}, {}, None, defaultdict(list), {}
    
    feat_by_id = {f.id(): f for f in features}
    road_id_cache = {f.id(): str(_fld(f, 'road_id')).strip() for f in features}

    def _flow(feat):
        line = _get_polyline(feat)
        if not line: return None, None, None
        try: way_id = int(str(_fld(feat, 'way_id'))[:3])
        except: way_id = 0
        is_reverse = way_id in REVERSE_WAY_IDS
        if (is_reverse and line[0].x() > line[-1].x()) or (not is_reverse and line[0].x() < line[-1].x()):
            return line[-1], line[0], is_reverse
        return line[0], line[-1], is_reverse

    flow_cache = {f.id(): _flow(f) for f in features}
    spatial_idx = QgsSpatialIndex()
    for f in features: spatial_idx.insertFeature(f)

    adj = defaultdict(list)
    for fid, (_, f_exit, f_rev) in flow_cache.items():
        if not f_exit: continue
        
        # Use a minimal search area to find potential candidates
        search_rect = QgsRectangle(f_exit.x()-0.001, f_exit.y()-0.001, f_exit.x()+0.001, f_exit.y()+0.001)
        for oid in spatial_idx.intersects(search_rect):
            if oid == fid or oid not in feat_by_id: continue
            
            o_entry, _, o_rev = flow_cache[oid]
            
            # STRICT CHECK: Exit and Entry points must be EXACTLY identical
            if not o_entry or f_exit != o_entry:
                continue
            
            if road_id_cache.get(fid) == road_id_cache.get(oid) and o_rev != f_rev:
                continue
                
            adj[fid].append(oid)
            
    return feat_by_id, flow_cache, spatial_idx, adj, road_id_cache

class RoutingMapTool(QgsMapTool):
    def __init__(self, canvas, layer):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self._markers = [] 
        self._start_fid = None
        self.feat_by_id, self.flow_cache, self.spatial_idx, self.adj, self.road_id_cache = build_graph(layer)
        _log("Graph built with STRICT connectivity. Select start point.", Qgis.Success)

    def _snap(self, map_point):
        candidates = self.spatial_idx.nearestNeighbor(map_point, 5)
        best_fid, best_d = None, float('inf')
        pt_geom = QgsGeometry.fromPointXY(map_point)
        for fid in candidates:
            d = pt_geom.distance(self.feat_by_id[fid].geometry())
            if d < best_d: best_d, best_fid = d, fid
        return best_fid

    def _draw_marker(self, pt, color):
        rb = QgsRubberBand(self.canvas, QgsWkbTypes.PointGeometry)
        rb.setColor(QColor(color))
        rb.setIcon(QgsRubberBand.ICON_CIRCLE)
        rb.setIconSize(14)
        rb.addPoint(pt)
        self._markers.append(rb)

    def _clear_markers(self):
        for rb in self._markers: self.canvas.scene().removeItem(rb)
        self._markers.clear()

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._reset_all()
            return

        map_point = self.toMapCoordinates(event.pos())
        snapped = self._snap(map_point)
        if snapped is None: return

        if self._start_fid is None:
            _clear_previous_layers()
            self._clear_markers()
            self._start_fid = snapped
            self._draw_marker(map_point, "#00FF00")
            _log("Start point set. Select destination.")
        else:
            self._draw_marker(map_point, "#FF0000")
            self._process_routing(self._start_fid, snapped)
            self._start_fid = None

    def _process_routing(self, start_fid, end_fid):
            # Case: Selection on the same segment
            if start_fid == end_fid:
                render_all_routes([[start_fid]], self.feat_by_id, self.flow_cache, self.layer)
                return

            paths = find_all_routes(start_fid, end_fid, self.adj)
            
            if not paths:
                # inform the user 
                iface.messageBar().pushMessage(
                    "Info", 
                    "No logical route found between these segments.", 
                    level=Qgis.Info, 
                    duration=3
                )
            else:
                render_all_routes(paths, self.feat_by_id, self.flow_cache, self.layer)

    def _visualize_disconnection(self, start_fid):
        reachable = set()
        queue = deque([start_fid])
        while queue:
            cur = queue.popleft()
            if cur not in reachable:
                reachable.add(cur)
                queue.extend(self.adj.get(cur, []))
        
        lyr = QgsVectorLayer(f"LineString?crs={self.layer.crs().authid()}", DEBUG_LAYER_NAME, "memory")
        feats = []
        for fid in reachable:
            f = QgsFeature(); f.setGeometry(self.feat_by_id[fid].geometry()); feats.append(f)
        lyr.dataProvider().addFeatures(feats)
        sym = QgsLineSymbol.createSimple({'color': '255,140,0', 'width': '0.7', 'line_style': 'dash'})
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        QgsProject.instance().addMapLayer(lyr)

    def _reset_all(self):
        self._clear_markers()
        self._start_fid = None
        _clear_previous_layers()

def find_all_routes(start_fid, end_fid, adj):
    all_paths = []
    stack = [(start_fid, [start_fid], {start_fid})]
    while stack and len(all_paths) < MAX_ROUTES:
        current, path, visited = stack.pop()
        if len(path) > MAX_DEPTH: continue
        for nxt in adj.get(current, []):
            if nxt == end_fid:
                all_paths.append(path + [nxt])
            elif nxt not in visited:
                stack.append((nxt, path + [nxt], visited | {nxt}))
    all_paths.sort(key=len)
    return all_paths

def render_all_routes(all_paths, feat_by_id, flow_cache, source_layer):
    for i, path in enumerate(all_paths):
        color = _color(i)
        lyr = QgsVectorLayer(f"LineString?crs={source_layer.crs().authid()}", f"{ROUTE_LAYER_NAME} {i+1}", "memory")
        pr = lyr.dataProvider()
        pr.addAttributes([QgsField("seq", QVariant.Int)])
        lyr.updateFields()
        
        new_feats = []
        for seq, fid in enumerate(path):
            feat = feat_by_id[fid]
            line = _get_polyline(feat)
            entry, _, _ = flow_cache.get(fid, (None, None, None))
            geom = feat.geometry()
            if line and entry and entry.distance(line[0]) > entry.distance(line[-1]):
                geom = QgsGeometry.fromPolylineXY(list(reversed(line)))
            
            f = QgsFeature(lyr.fields())
            f.setGeometry(geom); f['seq'] = seq + 1; new_feats.append(f)
        
        pr.addFeatures(new_feats)
        
        symbol = QgsLineSymbol()
        symbol.deleteSymbolLayer(0)
        base = QgsSimpleLineSymbolLayer()
        base.setColor(QColor(color)); base.setWidth(1.2); symbol.appendSymbolLayer(base)
        
        arrow = QgsArrowSymbolLayer.create({
            'arrow_width': '0.3', 'head_length': '3', 'head_thickness': '1',
            'arrow_type': '0', 'is_curved': '0', 'is_repeated': '1',
            'interval': '18', 'placement': '2'
        })
        arrow.setColor(QColor(color)); symbol.appendSymbolLayer(arrow)
        
        lyr.setRenderer(QgsSingleSymbolRenderer(symbol))
        QgsProject.instance().addMapLayer(lyr)

def run_routing():
    lyr = iface.activeLayer()
    if not lyr: return
    tool = RoutingMapTool(iface.mapCanvas(), lyr)
    iface.mapCanvas().setMapTool(tool)
