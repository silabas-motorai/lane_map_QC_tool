# =============================================================================
#  LANE MAP QC TOOL  ·  integrity check + visual analysis
# =============================================================================

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsWkbTypes, QgsLineSymbol, QgsArrowSymbolLayer, QgsFeatureRequest,
    QgsExpression, QgsRendererCategory, QgsSymbol, QgsCategorizedSymbolRenderer,
    QgsMessageLog, QgsPointXY, QgsMarkerSymbol, QgsRasterMarkerSymbolLayer,
    QgsTextFormat, QgsTextBufferSettings, QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling, QgsRuleBasedRenderer, QgsSimpleMarkerSymbolLayer,
    QgsSimpleLineSymbolLayer, QgsSpatialIndex, QgsRectangle, QgsSingleSymbolRenderer
)
from qgis.utils import iface
from collections import defaultdict
import os, sys, random, inspect

# =============================================================================
#  CONSTANTS
# =============================================================================

# Way IDs whose geometry runs east→west (need reversal for flow direction)
REVERSE_WAY_IDS = {100, 101, 102, 400, 401, 402, 403, 500}

ARROW_LAYER_NAME    = "Driving Direction"
YIELD_TO_LAYER_NAME = "Yield To"
REG_ALL_LAYER_NAME  = "Regulatory Elements"
REG_SEL_LAYER_NAME  = "Related Regulatory Elements"
INTEGRITY_LAYER_NAME = "Integrity_Issues"
GROUP_NAME          = "Lane Map Analysis"

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    script_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))

icon_folder = os.path.join(script_dir, "style_images")

# centerline way_id → (right_border_way_id, left_border_way_id) lookup
WAY_PAIRS_MAP = {
    (2, 100): ([[100, 500]], [-1]),
    (2, 300): ([[300, 700]], [1]),
    (3, 200): ([[100, 200], [300, 200]], [-1, 1]),
    (3, 400): ([[100, 400], [400, 500]], [-1, -2]),
    (3, 800): ([[300, 800], [800, 700]], [2, 1]),
    (4, 101): ([[100, 101], [101, 202]], [2, 1]),
    (4, 202): ([[300, 202]], [-1]),
    (4, 200): ([[100, 200], [201, 200]], [-1, 1]),
    (4, 201): ([[300, 201]], [2]),
    (4, 400): ([[100, 400], [400, 401]], [-1, -2]),
    (4, 401): ([[401, 500]], [-3]),
    (4, 800): ([[300, 800], [800, 801]], [1, 2]),
    (4, 801): ([[801, 700]], [3]),
    (5, 101): ([[100, 101]], [-1]),
    (5, 200): ([[101, 200], [201, 200]], [-2, 1]),
    (5, 201): ([[300, 201]], [2]),
    (5, 400): ([[100, 400], [400, 401]], [-1, -2]),
    (5, 402): ([[401, 402], [402, 500]], [-3, -4]),
    (5, 800): ([[300, 800], [800, 801]], [1, 2]),
    (5, 802): ([[801, 802], [802, 700]], [3, 4]),
    (6, 101): ([[100, 101], [101, 102]], [-1, -2]),
    (6, 200): ([[102, 200], [201, 200]], [-3, 1]),
    (6, 201): ([[300, 201]], [2]),
    (6, 202): ([[203, 202]], [1]),
    (6, 204): ([[300, 204], [204, 203]], [3, 2]),
    (6, 1010): ([[100, 1010], [1010, 200]], [-1, -2]),
    (6, 2010): ([[2010, 200], [2020, 2010]], [1, 2]),
    (6, 2020): ([[300, 2020]], [3]),
    (6, 400): ([[100, 400], [400, 401]], [-1, -2]),
    (6, 402): ([[401, 402], [402, 403]], [-3, -4]),
    (6, 403): ([[403, 500]], [-5]),
    (6, 800): ([[300, 800], [800, 801]], [1, 2]),
    (6, 802): ([[801, 802], [802, 803]], [3, 4]),
    (6, 803): ([[803, 700]], [5]),
    (7, 101): ([[100, 101], [101, 102]], [-1, -2]),
    (7, 200): ([[102, 200], [201, 200]], [-3, 1]),
    (7, 202): ([[300, 202], [202, 201]], [2, 3]),
    (7, 400): ([[100, 400], [400, 401]], [-1, -2]),
    (7, 402): ([[401, 402], [402, 403]], [-3, -4]),
    (7, 404): ([[403, 404], [404, 500]], [-5, -6]),
    (7, 800): ([[300, 800], [800, 801]], [1, 2]),
    (7, 802): ([[801, 802], [802, 803]], [3, 4]),
    (7, 804): ([[803, 804], [804, 700]], [5, 6]),
}

# =============================================================================
#  SHARED HELPERS
# =============================================================================

def log(msg, level=Qgis.Info):
    iface.messageBar().pushMessage("QC Tool", msg, level=level, duration=5)

def layer_has_fields(layer, field_names):
    names = {f.name() for f in layer.fields()}
    return all(n in names for n in field_names)

def fld(feat, name, default=""):
    """Safe field accessor for QgsFeature — returns default if field missing or NULL."""
    try:
        val = feat[name]
        return val if val is not None else default
    except KeyError:
        return default

def needs_reversal(way_id, line):
    """Returns True if the line vertices run opposite to the logical flow direction."""
    in_reverse = way_id in REVERSE_WAY_IDS
    return (in_reverse and line[0].x() > line[-1].x()) or \
           (not in_reverse and line[0].x() < line[-1].x())

def get_polyline(feat):
    """Returns the first polyline of a feature, or None."""
    geom = feat.geometry()
    if not geom:
        return None
    try:
        return geom.asPolyline() if not geom.isMultipart() else geom.asMultiPolyline()[0]
    except Exception:
        return None

def add_layer_to_group(layer, visible=False):
    """Adds layer inside the analysis group, restores active layer."""
    active = iface.activeLayer()
    root  = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(GROUP_NAME) or root.insertGroup(0, GROUP_NAME)
    QgsProject.instance().addMapLayer(layer, False)
    node = group.addLayer(layer)
    node.setItemVisibilityChecked(visible)
    node.setExpanded(False)
    if active:
        iface.setActiveLayer(active)

def add_layer_outside_group(layer, visible=True, insert_position=0):
    """Adds layer above the analysis group at the given relative position."""
    active = iface.activeLayer()
    root   = QgsProject.instance().layerTreeRoot()
    group  = root.findGroup(GROUP_NAME)
    base   = root.children().index(group) if group else 0
    QgsProject.instance().addMapLayer(layer, False)
    root.insertLayer(base + insert_position, layer)
    node = root.findLayer(layer.id())
    if node:
        node.setItemVisibilityChecked(visible)
    if active:
        iface.setActiveLayer(active)

def remove_layer_by_name(name):
    for lyr in list(QgsProject.instance().mapLayers().values()):
        if lyr.name() == name:
            QgsProject.instance().removeMapLayer(lyr)
            break

# =============================================================================
#  INTEGRITY CHECK  (snapping · routing · stop lines)
# =============================================================================

def get_border_way_ids_for_centerline(roads_with_ref, cl_way_id):
    for (rwr, ref_wid), (pairs, _) in WAY_PAIRS_MAP.items():
        if rwr != roads_with_ref:
            continue
        for right_w, left_w in pairs:
            if min(right_w, left_w) * 100 + 12 == cl_way_id:
                return (right_w, left_w)
    return None

def check_lane_integrity(snap_tol=1e-15, graph_tol=1e-5):
    layer = iface.activeLayer()
    if not layer:
        return []

    features = list(layer.getFeatures())
    if not features:
        return []

    all_lines, stop_wait_lines = [], []
    for f in features:
        l_type = str(fld(f, 'lane_type')).lower()
        a_type = str(fld(f, 'area_type')).lower()
        l_sub  = str(fld(f, 'line_sub')).lower()
        if l_sub in ['de294', 'de341']:
            stop_wait_lines.append(f)
        elif l_type == 'centerline' or (l_type in ['road', 'cycle', 'road_cycle'] and a_type in ['', 'none', 'null']):
            all_lines.append(f)

    feat_by_id = {f.id(): f for f in all_lines}

    def get_group_type(feat):
        return 'cycle' if 'cycle' in str(fld(feat, 'lane_type')).lower() else 'road_group'

    def get_flow_info(feat):
        line = get_polyline(feat)
        if not line:
            return None, None, None
        try:    way_id = int(str(fld(feat, "way_id"))[:3])
        except: way_id = 0
        is_east = way_id in REVERSE_WAY_IDS
        if (is_east and line[0].x() > line[-1].x()) or (not is_east and line[0].x() < line[-1].x()):
            return line[-1], line[0], is_east
        return line[0], line[-1], is_east

    spatial_index = QgsSpatialIndex()
    for f in all_lines:
        spatial_index.insertFeature(f)

    def nearby_ids(point, radius):
        r = QgsRectangle(point.x()-radius, point.y()-radius, point.x()+radius, point.y()+radius)
        return spatial_index.intersects(r)

    flow_cache  = {f.id(): get_flow_info(f)  for f in all_lines}
    group_cache = {f.id(): get_group_type(f) for f in all_lines}

    def cycle_endpoint_on_road(point, feat_id, tol=0.5):
        pt_geom = QgsGeometry.fromPointXY(point)
        for fid in nearby_ids(point, tol):
            if fid == feat_id or fid not in feat_by_id:
                continue
            if group_cache[fid] == 'road_group' and pt_geom.distance(feat_by_id[fid].geometry()) < tol:
                return True
        return False

    # ── Build connection graph ──
    successors, predecessors = defaultdict(set), defaultdict(set)
    raw_succ = defaultdict(lambda: defaultdict(list))
    raw_pred = defaultdict(lambda: defaultdict(list))
    group_counts = defaultdict(int)

    for f in all_lines:
        f_entry, f_exit, f_east = flow_cache[f.id()]
        if not f_entry:
            continue
        rid, f_group = fld(f, 'road_id'), group_cache[f.id()]
        key = (rid, f_east, f_group)
        group_counts[key] += 1

        for oid in nearby_ids(f_exit, graph_tol * 10):
            if oid == f.id() or oid not in feat_by_id: continue
            o_entry, _, o_east = flow_cache[oid]
            if not o_entry or f_east != o_east or f_group != group_cache[oid]: continue
            if f_exit.distance(o_entry) < graph_tol:
                raw_succ[key][fld(feat_by_id[oid], 'road_id')].append(f.id())

        for oid in nearby_ids(f_entry, graph_tol * 10):
            if oid == f.id() or oid not in feat_by_id: continue
            _, o_exit, o_east = flow_cache[oid]
            if not o_exit or f_east != o_east or f_group != group_cache[oid]: continue
            if f_entry.distance(o_exit) < graph_tol:
                raw_pred[key][fld(feat_by_id[oid], 'road_id')].append(f.id())

    def confirm(key, f_ids):
        if len(f_ids) > 1 or group_counts[key] == 1: return True
        l_type = str(feat_by_id[f_ids[0]]['lane_type']).lower()
        return l_type == 'centerline' or key[2] == 'cycle'

    for key, targets in raw_succ.items():
        for trid, fids in targets.items():
            if confirm(key, fids): successors[key].add(trid)
    for key, targets in raw_pred.items():
        for trid, fids in targets.items():
            if confirm(key, fids): predecessors[key].add(trid)

    issues = []

    # ── 1. Snapping ──
    for f in all_lines:
        f_entry, f_exit, f_east = flow_cache[f.id()]
        if not f_entry: continue
        rid, wid  = fld(f, 'road_id'), fld(f, 'way_id')
        my_ltype  = str(fld(f, 'lane_type')).lower()
        l_label   = my_ltype.upper()
        f_group   = group_cache[f.id()]
        is_cycle  = (f_group == 'cycle')
        key       = (rid, f_east, f_group)

        for endpoint, is_entry in ((f_entry, True), (f_exit, False)):
            snapped = False
            for oid in nearby_ids(endpoint, snap_tol + graph_tol):
                if oid == f.id() or oid not in feat_by_id: continue
                o_lt = str(fld(feat_by_id[oid], 'lane_type')).lower()
                if (my_ltype == 'road' and o_lt == 'cycle') or (my_ltype == 'cycle' and o_lt == 'road'): continue
                o_e, o_x, _ = flow_cache[oid]
                if (o_e and endpoint.distance(o_e) < snap_tol) or (o_x and endpoint.distance(o_x) < snap_tol):
                    snapped = True; break

            if snapped: continue
            if is_cycle and cycle_endpoint_on_road(endpoint, f.id()): continue

            # Check proximity gap
            is_prox = False
            for oid in nearby_ids(endpoint, 0.0001):
                if oid == f.id() or oid not in feat_by_id: continue
                other = feat_by_id[oid]
                if fld(other, 'road_id') == rid and fld(other, 'lane_type') == fld(f, 'lane_type'):
                    if QgsGeometry.fromPointXY(endpoint).distance(other.geometry()) < 0.00001:
                        is_prox = True; break
            if is_prox:
                issues.append({"way_id": wid, "road_id": rid, "point": endpoint, "type": f"{l_label}_GAP"})
                continue

            # Check graph-based gap
            connected_rids = predecessors[key] if is_entry else successors[key]
            if not connected_rids: continue
            for oid in nearby_ids(endpoint, graph_tol * 200):
                if oid == f.id() or oid not in feat_by_id: continue
                if group_cache[oid] != f_group: continue
                o_e, o_x, o_east = flow_cache[oid]
                if o_east != f_east: continue
                compare_pt = o_x if is_entry else o_e
                if compare_pt and endpoint.distance(compare_pt) < graph_tol * 100:
                    issues.append({"way_id": wid, "road_id": rid, "point": endpoint, "type": f"{l_label}_GAP"})
                    break

    # ── 2. Border routing consistency ──
    roads_with_ref_map  = defaultdict(int)
    borders_by_rid_wid  = defaultdict(list)
    for f in all_lines:
        if str(fld(f, 'lane_type')).lower() in ['road', 'cycle', 'road_cycle']:
            roads_with_ref_map[str(fld(f, 'road_id'))] += 1
            try: borders_by_rid_wid[(str(fld(f, 'road_id')), int(fld(f, 'way_id')))].append(f)
            except: pass

    border_constraints = defaultdict(list)
    for cl in (f for f in all_lines if str(fld(f, 'lane_type')).lower() == 'centerline'):
        cl_entry, cl_exit, cl_east = flow_cache[cl.id()]
        if not cl_entry: continue
        try:
            cl_rid, cl_wid = str(fld(cl, 'road_id')), int(fld(cl, 'way_id'))
        except: continue

        succ_rids, pred_rids = set(), set()
        for oid in nearby_ids(cl_exit, graph_tol * 10):
            if oid == cl.id() or oid not in feat_by_id: continue
            other = feat_by_id[oid]
            if str(fld(other, 'lane_type')).lower() != 'centerline': continue
            o_e, _, o_east = flow_cache[oid]
            if o_e and o_east == cl_east and cl_exit.distance(o_e) < graph_tol:
                succ_rids.add(str(fld(other, 'road_id')))
        for oid in nearby_ids(cl_entry, graph_tol * 10):
            if oid == cl.id() or oid not in feat_by_id: continue
            other = feat_by_id[oid]
            if str(fld(other, 'lane_type')).lower() != 'centerline': continue
            _, o_x, o_east = flow_cache[oid]
            if o_x and o_east == cl_east and cl_entry.distance(o_x) < graph_tol:
                pred_rids.add(str(fld(other, 'road_id')))

        if not succ_rids and not pred_rids: continue
        rwr = roads_with_ref_map.get(cl_rid, 0)
        if not rwr: continue
        border_pair = get_border_way_ids_for_centerline(rwr, cl_wid)
        if not border_pair: continue
        right_w, left_w = border_pair
        for bwid in (right_w, left_w):
            border_constraints[(cl_rid, bwid)].append((succ_rids, pred_rids, {right_w, left_w}, cl_east))

    for (cl_rid, border_wid), constraints in border_constraints.items():
        for bf in borders_by_rid_wid.get((cl_rid, border_wid), []):
            b_entry, b_exit, b_east = flow_cache[bf.id()]
            if not b_entry: continue
            b_wid = fld(bf, 'way_id')
            all_succ, all_pred, all_pair_wids = set(), set(), set()
            for s, p, pw, ce in constraints:
                if ce != b_east: continue
                all_succ |= s; all_pred |= p; all_pair_wids |= pw
            all_succ = {str(r) for r in all_succ}
            all_pred = {str(r) for r in all_pred}

            for pt, expected, is_exit in ((b_exit, all_succ, True), (b_entry, all_pred, False)):
                if not expected: continue
                found_rids = set()
                for oid in nearby_ids(pt, graph_tol * 10):
                    if oid == bf.id() or oid not in feat_by_id: continue
                    other = feat_by_id[oid]
                    o_e, o_x, o_east = flow_cache[oid]
                    cmp = o_e if is_exit else o_x
                    if not cmp or o_east != b_east or pt.distance(cmp) >= graph_tol: continue
                    if str(fld(other, 'lane_type')).lower() == 'cycle': continue
                    o_rid, o_wid2 = str(fld(other, 'road_id')), int(fld(other, 'way_id'))
                    if o_wid2 in all_pair_wids and o_rid not in expected: continue
                    found_rids.add(o_rid)
                if found_rids and not found_rids.intersection(expected):
                    issues.append({
                        "way_id": b_wid, "road_id": cl_rid, "point": pt,
                        "type": f"BORDER_MISMATCH → road_id {sorted(str(r) for r in found_rids)} (expected: {sorted(str(r) for r in expected)})"
                    })

    # ── 3. Stop/Wait line hanging ──
    if stop_wait_lines:
        lane_index, lane_by_id = QgsSpatialIndex(), {}
        for bf in all_lines:
            lane_index.insertFeature(bf)
            lane_by_id[bf.id()] = bf

        endpoint_r = 0.00005  # search radius to find nearby lanes

        for f in stop_wait_lines:
            sl_geom = f.geometry()
            if not sl_geom: continue
            sl_line = get_polyline(f)
            if not sl_line: continue
            wid, rid = fld(f, 'way_id'), fld(f, 'road_id')

            bbox = sl_geom.boundingBox()
            bbox.grow(endpoint_r * 3)
            nearby_ids = lane_index.intersects(bbox)

            # ── 1. Crossing check: every lane that crosses stop line
            #       must have its start or end node exactly at the intersection ──
            for lid in nearby_ids:
                if lid not in lane_by_id: continue
                lane_f    = lane_by_id[lid]
                lane_geom = lane_f.geometry()
                if not lane_geom: continue
                if not sl_geom.crosses(lane_geom) and not sl_geom.intersects(lane_geom):
                    continue
                intersection = sl_geom.intersection(lane_geom)
                if not intersection or intersection.isEmpty(): continue
                wkb = intersection.wkbType()
                if wkb in (1, 0x80000001):
                    int_pts = [intersection.asPoint()]
                elif wkb in (4, 0x80000004):
                    int_pts = intersection.asMultiPoint()
                else:
                    continue
                l_line = get_polyline(lane_f)
                if not l_line: continue
                endpoints = [l_line[0], l_line[-1]]
                for int_pt in int_pts:
                    int_geom = QgsGeometry.fromPointXY(int_pt)
                    if not any(int_geom.distance(QgsGeometry.fromPointXY(ep)) == 0
                               for ep in endpoints):
                        issues.append({"way_id": fld(lane_f, 'way_id'),
                                       "road_id": fld(lane_f, 'road_id'),
                                       "point": int_pt, "type": "STOP_LINE_GAP"})

            # ── 2. Endpoint hanging check: stop line endpoints must have
            #       distance == 0 to a lane line ──
            for pt in (sl_line[0], sl_line[-1]):
                pt_geom = QgsGeometry.fromPointXY(pt)
                r = QgsRectangle(pt.x()-endpoint_r, pt.y()-endpoint_r,
                                 pt.x()+endpoint_r, pt.y()+endpoint_r)
                snapped = close_miss = False
                for bid in lane_index.intersects(r):
                    if bid not in lane_by_id: continue
                    d = pt_geom.distance(lane_by_id[bid].geometry())
                    if d == 0:           snapped = True; break
                    elif d < endpoint_r: close_miss = True
                if not snapped and close_miss:
                    issues.append({"way_id": wid, "road_id": rid,
                                   "point": pt, "type": "STOP_LINE_GAP"})

    return issues

def render_integrity_issues(issues, source_layer):
    remove_layer_by_name(INTEGRITY_LAYER_NAME)
    if not issues:
        log("All Lane Groups are Perfectly Snapped & Routed!", Qgis.Success)
        return
    temp = QgsVectorLayer(f"Point?crs={source_layer.crs().authid()}", INTEGRITY_LAYER_NAME, "memory")
    pr = temp.dataProvider()
    pr.addAttributes([QgsField("road_id", QVariant.String),
                      QgsField("way_id",  QVariant.String),
                      QgsField("issue_type", QVariant.String)])
    temp.updateFields()
    seen = set()
    for iss in issues:
        sig = (round(iss['point'].x(), 5), round(iss['point'].y(), 5), iss['type'])
        if sig in seen: continue
        seen.add(sig)
        feat = QgsFeature(); feat.setGeometry(QgsGeometry.fromPointXY(iss['point']))
        feat.setAttributes([str(iss['road_id']), str(iss['way_id']), iss['type']])
        pr.addFeature(feat)
    temp.updateExtents()
    symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': 'transparent',
                                           'outline_color': 'blue', 'outline_width': '0.6', 'size': '4.5'})
    temp.setRenderer(QgsSingleSymbolRenderer(symbol))
    add_layer_to_group(temp, visible=False)
    log(f"{temp.featureCount()} Lane Topology Issues Found.", Qgis.Warning)

# =============================================================================
#  VISUAL LAYERS
# =============================================================================

def create_arrow_layer(name, color):
    for lyr in QgsProject.instance().mapLayers().values():
        if lyr.name() == name: return lyr
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", name, "memory")
    pr = layer.dataProvider()
    pr.addAttributes([QgsField("orig_fid", QVariant.Int)])
    layer.updateFields()
    arrow_sym = QgsLineSymbol()
    arrow_sym.deleteSymbolLayer(0)
    al = QgsArrowSymbolLayer.create({'arrow_width':'0.4','head_length':'4','head_thickness':'1.2',
                                     'arrow_type':'0','is_curved':'0','is_repeated':'1',
                                     'interval':'20','placement':'2','offset':'0'})
    al.setColor(QColor(color))
    arrow_sym.appendSymbolLayer(al)
    layer.renderer().setSymbol(arrow_sym)
    pos = 1 if name == ARROW_LAYER_NAME else 2
    add_layer_outside_group(layer, visible=True, insert_position=pos)
    return layer

def update_arrows(layer):
    arrow_layer = create_arrow_layer(ARROW_LAYER_NAME, "blue")
    arrow_layer.startEditing(); arrow_layer.dataProvider().truncate()
    for feat in layer.selectedFeatures():
        fields = feat.fields().names()
        lt = str(feat["lane_type"]).lower().strip() if "lane_type" in fields else ""
        at = str(feat["area_type"]).lower().strip() if "area_type" in fields else ""

        if lt == "regulatory_element":
            continue
        if lt == "road" and at in ["mai_bus_stop", "parking", "exit"]:
            continue
        line = get_polyline(feat)
        if not line: continue
        try:    wid = int(str(feat["way_id"])[:3])
        except: wid = 0
        if needs_reversal(wid, line): line = list(reversed(line))
        nf = QgsFeature(arrow_layer.fields())
        nf.setGeometry(QgsGeometry.fromPolylineXY(line)); nf["orig_fid"] = feat.id()
        arrow_layer.addFeature(nf)
    arrow_layer.commitChanges(); arrow_layer.triggerRepaint()

def update_yield_to_highlights(layer):
    yield_layer = create_arrow_layer(YIELD_TO_LAYER_NAME, "red")
    yield_layer.startEditing(); yield_layer.dataProvider().truncate()
    for feat in layer.selectedFeatures():
        yt = feat["yield_to"]
        if not yt: continue
        for t in str(yt).split(","):
            t = t.strip()
            if not t: continue
            if "_" not in t:
                try:    road_id, way_id = int(t), None
                except: continue
            else:
                try:    road_id, way_id = map(int, t.split("_"))
                except: continue
            expr = QgsExpression(f'"road_id"={road_id} AND "way_id"={way_id}' if way_id is not None
                                 else f'"road_id"={road_id}')
            for tf in layer.getFeatures(QgsFeatureRequest(expr)):
                line = get_polyline(tf)
                if not line: continue
                try:    wid = int(str(tf["way_id"])[:3])
                except: wid = 0
                if needs_reversal(wid, line): line = list(reversed(line))
                nf = QgsFeature(yield_layer.fields())
                nf.setGeometry(QgsGeometry.fromPolylineXY(line)); nf["orig_fid"] = tf.id()
                yield_layer.addFeature(nf)
    yield_layer.commitChanges(); yield_layer.triggerRepaint()

def create_oneway_layer(input_layer):
    if not layer_has_fields(input_layer, ["lane_type", "one_way"]): return
    crs = input_layer.crs()
    out = QgsVectorLayer(f"LineString?crs={crs.authid()}", "One-way / Bidirectional Way", "memory")
    pr = out.dataProvider()
    pr.addAttributes([QgsField("orig_fid", QVariant.Int), QgsField("direction_status", QVariant.String)])
    out.updateFields()
    oneway_feats, bidir_feats = [], []
    for feat in input_layer.getFeatures():
        if str(feat["lane_type"]).strip().lower() != "centerline": continue
        one_way = str(feat["one_way"]).strip().lower() if feat["one_way"] is not None else ""
        status  = "bi-directional" if one_way == "no" else "one-way"
        geom = feat.geometry()
        if geom.isEmpty(): continue
        nf = QgsFeature(out.fields()); nf.setGeometry(geom)
        nf["orig_fid"] = feat.id(); nf["direction_status"] = status
        (bidir_feats if status == "bi-directional" else oneway_feats).append(nf)
    out.startEditing(); out.addFeatures(bidir_feats); out.addFeatures(oneway_feats); out.commitChanges()
    bidir_sym = QgsLineSymbol()
    bidir_sym.appendSymbolLayer(QgsSimpleLineSymbolLayer.create({'color':'#1E90FF','width':'2'}))
    renderer = QgsCategorizedSymbolRenderer("direction_status", [
        QgsRendererCategory("bi-directional", bidir_sym, "Bi-directional"),
        QgsRendererCategory("one-way", QgsLineSymbol.createSimple({'color':'#FFA500','width':'2'}), "One-way")
    ])
    out.setRenderer(renderer); add_layer_to_group(out)

def create_stop_zone(input_layer):
    required = ["area_type", "name", "closest_lane", "road_id", "way_id", "lane_type"]
    if not layer_has_fields(input_layer, required): return
    crs = input_layer.crs()
    out = QgsVectorLayer(f"LineString?crs={crs.authid()}", "Stop Zones", "memory")
    pr = out.dataProvider()
    pr.addAttributes([QgsField("orig_fid", QVariant.Int), QgsField("feature_type", QVariant.String),
                      QgsField("name", QVariant.String)])
    out.updateFields()
    feats, related_refs = [], set()
    for feat in input_layer.getFeatures():
        if str(feat["area_type"]) != "MAI_bus_stop": continue
        geom = feat.geometry()
        if geom.isEmpty() or len(geom.asPolyline()) != 5: continue
        nf = QgsFeature(out.fields()); nf.setGeometry(geom)
        nf["orig_fid"] = feat.id(); nf["feature_type"] = "zone"
        nf["name"] = feat["name"] or ""; feats.append(nf)
        if feat["closest_lane"]: related_refs.add(str(feat["closest_lane"]).strip())
    for feat in input_layer.getFeatures():
        if str(feat["lane_type"]).strip().lower() != "centerline": continue
        ref = f"{feat['road_id']}_{feat['way_id']}"
        if ref not in related_refs: continue
        geom = feat.geometry()
        if geom.isEmpty(): continue
        nf = QgsFeature(out.fields()); nf.setGeometry(geom)
        nf["orig_fid"] = feat.id(); nf["feature_type"] = "centerline"; nf["name"] = ""
        feats.append(nf)
    out.startEditing(); out.addFeatures(feats); out.commitChanges()
    renderer = QgsCategorizedSymbolRenderer("feature_type", [
        QgsRendererCategory("zone",       QgsLineSymbol.createSimple({'color':'#0f18f6','width':'3'}), "Stop Zone"),
        QgsRendererCategory("centerline", QgsLineSymbol.createSimple({'color':'#FF3333','width':'1.0','line_style':'dash'}), "Related Centerline"),
    ])
    out.setRenderer(renderer)
    lbl = QgsPalLayerSettings(); lbl.fieldName = "name"; lbl.enabled = True
    lbl.placement = QgsPalLayerSettings.Placement.Line
    tf = QgsTextFormat(); tf.setSize(20); f = QFont("Arial"); f.setBold(True); tf.setFont(f)
    lbl.setFormat(tf); out.setLabeling(QgsVectorLayerSimpleLabeling(lbl)); out.setLabelsEnabled(True)
    add_layer_to_group(out)

def create_lane_morphology_layer(input_layer):
    if not layer_has_fields(input_layer, ["lane_morphology"]): return
    color_map = {"curve":"#ffa500","straight":"#00ff00","intersection":"#ff0000",
                 "split":"#ff69b4","merge":"#0000ff","roundabout":"#f18f6"}
    crs = input_layer.crs()
    out = QgsVectorLayer(f"LineString?crs={crs.authid()}", "Lane Morphology", "memory")
    pr = out.dataProvider()
    pr.addAttributes([QgsField("orig_fid", QVariant.Int), QgsField("lane_morphology", QVariant.String)])
    out.updateFields()
    renderer = QgsCategorizedSymbolRenderer("lane_morphology", [
        QgsRendererCategory(m, QgsLineSymbol.createSimple({'color':c,'width':'2'}), m)
        for m, c in color_map.items()
    ])
    out.setRenderer(renderer)
    out.startEditing()
    for feat in input_layer.getFeatures():
        morph    = str(feat["lane_morphology"]).strip().lower()
        lt       = str(feat["lane_type"]).strip().lower() if "lane_type" in input_layer.fields().names() else ""
        if morph not in color_map or lt in ["cycle","road_cycle"]: continue
        geom = feat.geometry()
        if geom.isEmpty(): continue
        nf = QgsFeature(out.fields()); nf.setGeometry(geom)
        nf["orig_fid"] = feat.id(); nf["lane_morphology"] = morph; out.addFeature(nf)
    out.commitChanges(); add_layer_to_group(out)

def create_speed_limit_layer(input_layer):
    def safe_int(v):
        try: return int(v)
        except: return None
    if not layer_has_fields(input_layer, ["speed_limit"]): return
    crs = input_layer.crs()
    out = QgsVectorLayer(f"LineString?crs={crs.authid()}", "Speed Limit", "memory")
    pr = out.dataProvider()
    pr.addAttributes([QgsField("orig_fid", QVariant.Int), QgsField("speed_limit", QVariant.Int)])
    out.updateFields()
    feats = list(input_layer.getFeatures())
    unique_speeds = {safe_int(f["speed_limit"]) for f in feats if safe_int(f["speed_limit"]) is not None}
    predefined = {30:"#FFA500", 50:"#FF0000"}
    random.seed(42)
    renderer = QgsCategorizedSymbolRenderer("speed_limit", [
        QgsRendererCategory(s, QgsLineSymbol.createSimple({'color': predefined.get(s,"#{:06x}".format(random.randint(0,0xFFFFFF))), 'width':'2'}), str(s))
        for s in sorted(unique_speeds)
    ])
    out.setRenderer(renderer)
    out.startEditing()
    for feat in feats:
        v = safe_int(feat["speed_limit"])
        if v is None: continue
        geom = feat.geometry()
        if geom.isEmpty(): continue
        nf = QgsFeature(out.fields()); nf.setGeometry(geom)
        nf["orig_fid"] = feat.id(); nf["speed_limit"] = v; out.addFeature(nf)
    out.commitChanges(); add_layer_to_group(out)

def create_passable_layer(input_layer):
    if not layer_has_fields(input_layer, ["line_type","line_sub","lane_type","area_type"]): return
    color_map = {"passable":"#32CD32","non-passable":"#FF6600","physically non-passable":"#FF0033"}
    crs = input_layer.crs()
    out = QgsVectorLayer(f"LineString?crs={crs.authid()}", "Passable/Non-Passable Regions", "memory")
    pr = out.dataProvider()
    pr.addAttributes([QgsField("orig_fid", QVariant.Int), QgsField("passable_status", QVariant.String)])
    out.updateFields()
    renderer = QgsCategorizedSymbolRenderer("passable_status", [
        QgsRendererCategory(s, QgsLineSymbol.createSimple({'color':c,'width':'2'}), s)
        for s, c in color_map.items()
    ])
    out.setRenderer(renderer)
    buckets = {"passable":[], "non-passable":[], "physically non-passable":[]}
    for feat in input_layer.getFeatures():
        lt = str(feat["lane_type"]).strip().lower() if feat["lane_type"] else ""
        ltype = str(feat["line_type"]).strip().lower() if feat["lane_type"] else ""
        lsub  = str(feat["line_sub"]).strip().lower()  if feat["line_sub"]  else ""
        at    = feat["area_type"]
        if lt not in ["road","cycle","road_cycle"] or at not in [None,""]: continue
        if ltype == "line_thin":
            status = "passable" if lsub == "dashed" else "non-passable" if lsub == "solid" else None
        elif ltype == "road_border":
            status = "physically non-passable"
        else: status = None
        if not status: continue
        geom = feat.geometry()
        if geom.isEmpty(): continue
        nf = QgsFeature(out.fields()); nf.setGeometry(geom)
        nf["orig_fid"] = feat.id(); nf["passable_status"] = status; buckets[status].append(nf)
    out.startEditing()
    for feats in buckets.values(): out.addFeatures(feats)
    out.commitChanges(); add_layer_to_group(out)

def add_traffic_elements(layer):
    ICON_SIZE = 10
    if not layer_has_fields(layer, ["line_type","line_sub"]): return
    reg_feats = [f for f in layer.getFeatures()
                 if str(f["line_type"]).strip().lower() in ("traffic_sign","traffic_light")
                 and str(f["line_sub"]).strip() not in ["de294", "de341"]]
    if not reg_feats: return

    lbl_layer = QgsVectorLayer(f"Point?crs={layer.crs().authid()}", REG_ALL_LAYER_NAME, "memory")
    pr = lbl_layer.dataProvider()
    pr.addAttributes([QgsField("line_sub",QVariant.String),QgsField("icon_code",QVariant.String),
                      QgsField("has_icon",QVariant.Bool)])
    lbl_layer.updateFields(); lbl_layer.startEditing()
    V_OFF = 0.01 if "4326" not in layer.crs().authid() else 0.0000001
    type_idx = {t:i for i,t in enumerate(sorted({f["line_sub"] for f in reg_feats if f["line_sub"]}))}

    for feat in reg_feats:
        lsub = feat["line_sub"]
        if not isinstance(lsub, str): continue
        codes = [c.strip() for c in lsub.split(",") if c.strip()]
        if not codes: continue
        geom = feat.geometry()
        if geom.isEmpty(): continue
        centroid = geom.centroid().asPoint()
        for code in codes:
            icon_code = (code[2:] if code.startswith("de") else code).strip()
            if not icon_code: continue
            has_icon = os.path.exists(os.path.join(icon_folder, f"{icon_code}.png"))
            stack_pos = type_idx.get(lsub, 0)
            pt = QgsPointXY(centroid.x(), centroid.y() + stack_pos * V_OFF)
            pf = QgsFeature(lbl_layer.fields()); pf.setGeometry(QgsGeometry.fromPointXY(pt))
            pf["line_sub"] = lsub; pf["icon_code"] = icon_code; pf["has_icon"] = has_icon
            lbl_layer.addFeature(pf)
    lbl_layer.commitChanges()

    root_rule = QgsRuleBasedRenderer.Rule(None)
    for icon_code, has_icon in {(f["icon_code"], f["has_icon"]) for f in lbl_layer.getFeatures()}:
        symbol = QgsMarkerSymbol()
        if has_icon:
            path = os.path.join(icon_folder, f"{icon_code}.png")
            if not os.path.exists(path): continue
            img = QgsRasterMarkerSymbolLayer(path)
            img.setSize(ICON_SIZE * 0.5 if icon_code == "red_yellow_green" else ICON_SIZE)
            symbol.changeSymbolLayer(0, img)
        else:
            sl = QgsSimpleMarkerSymbolLayer(); sl.setColor(QColor("blue")); sl.setSize(ICON_SIZE/2)
            symbol.changeSymbolLayer(0, sl)
        rule = QgsRuleBasedRenderer.Rule(symbol)
        rule.setFilterExpression(f'"icon_code"=\'{icon_code}\' AND "has_icon"={"true" if has_icon else "false"}')
        rule.setLabel(icon_code if has_icon else f"[NO ICON] {icon_code}")
        root_rule.appendChild(rule)
    lbl_layer.setRenderer(QgsRuleBasedRenderer(root_rule))
    lbl_layer.setLabelsEnabled(False)
    add_layer_outside_group(lbl_layer, visible=True, insert_position=3)

def show_selected_regulatory_elements(layer):
    ICON_SIZE = 10
    if not layer: return
    mem_layer = next((l for l in QgsProject.instance().mapLayers().values()
                      if l.name() == REG_SEL_LAYER_NAME), None)
    selected = layer.selectedFeatures()
    if not selected:
        if mem_layer: QgsProject.instance().removeMapLayer(mem_layer)
        return
    sel = selected[0]
    lt = str(sel["lane_type"]).lower().strip() if "lane_type" in sel.fields().names() else ""
    if lt not in ["centerline","pedestrian_marking","cycle","road_cycle"]:
        if mem_layer: QgsProject.instance().removeMapLayer(mem_layer)
        return
    tr_raw = sel["traffic_rule"] if "traffic_rule" in sel.fields().names() else None
    if not tr_raw or not str(tr_raw).strip():
        if mem_layer: QgsProject.instance().removeMapLayer(mem_layer)
        return
    traffic_rules = [t.strip() for t in str(tr_raw).split(",") if t.strip()]
    if not traffic_rules:
        if mem_layer: QgsProject.instance().removeMapLayer(mem_layer)
        return

    if not mem_layer:
        mem_layer = QgsVectorLayer(f"Point?crs={layer.crs().authid()}", REG_SEL_LAYER_NAME, "memory")
        pr = mem_layer.dataProvider()
        pr.addAttributes([QgsField("re_id",QVariant.String),QgsField("line_sub",QVariant.String),
                          QgsField("icon_code",QVariant.String),QgsField("has_icon",QVariant.Bool)])
        mem_layer.updateFields()
        add_layer_outside_group(mem_layer, visible=True, insert_position=3)

    mem_layer.startEditing(); mem_layer.dataProvider().truncate()
    if not layer_has_fields(layer, ["line_type","line_sub","re_id"]):
        mem_layer.commitChanges(); return

    reg_feats = [f for f in layer.getFeatures()
                 if f["line_type"] and str(f["line_type"]).strip().lower() in ["traffic_sign","traffic_light"]]

    V_OFF = 0.01 if "4326" not in layer.crs().authid() else 0.0000001
    type_idx = {t:i for i,t in enumerate(sorted({str(f["line_sub"]).strip() for f in reg_feats if f["line_sub"]}))}

    feats = []
    for tr in traffic_rules:
        for f in [x for x in reg_feats if str(x["re_id"]).strip() == tr]:
            geom = f.geometry()
            if geom.isEmpty(): continue
            centroid = geom.centroid().asPoint()
            lsub = str(f["line_sub"]).strip() if f["line_sub"] else "default_icon"
            icon_code = (lsub[2:] if lsub.startswith("de") else lsub).strip()
            has_icon = os.path.exists(os.path.join(icon_folder, f"{icon_code}.png"))
            stack_pos = type_idx.get(lsub, 0)
            pt = QgsPointXY(centroid.x(), centroid.y() + stack_pos * V_OFF)
            pf = QgsFeature(mem_layer.fields()); pf.setGeometry(QgsGeometry.fromPointXY(pt))
            pf["re_id"] = tr; pf["line_sub"] = lsub; pf["icon_code"] = icon_code; pf["has_icon"] = has_icon
            feats.append(pf)
    mem_layer.dataProvider().addFeatures(feats); mem_layer.commitChanges()

    root_rule = QgsRuleBasedRenderer.Rule(None)
    for icon_code, has_icon in {(f["icon_code"], f["has_icon"]) for f in feats}:
        symbol = QgsMarkerSymbol()
        if has_icon:
            path = os.path.join(icon_folder, f"{icon_code}.png")
            if not os.path.exists(path): continue
            img = QgsRasterMarkerSymbolLayer(path)
            img.setSize(ICON_SIZE * 0.5 if icon_code == "red_yellow_green" else ICON_SIZE)
            symbol.changeSymbolLayer(0, img)
        else:
            sl = QgsSimpleMarkerSymbolLayer(); sl.setColor(QColor("blue")); sl.setSize(ICON_SIZE/2)
            symbol.changeSymbolLayer(0, sl)
        rule = QgsRuleBasedRenderer.Rule(symbol)
        rule.setFilterExpression(f'"icon_code"=\'{icon_code}\' AND "has_icon"={"true" if has_icon else "false"}')
        rule.setLabel(icon_code if has_icon else f"[NO ICON] {icon_code}")
        root_rule.appendChild(rule)
    mem_layer.setRenderer(QgsRuleBasedRenderer(root_rule)); mem_layer.triggerRepaint()

# =============================================================================
#  SELECTION HANDLER
# =============================================================================

def on_selection_changed():
    layer = iface.activeLayer()
    if not layer or not isinstance(layer, QgsVectorLayer): return
    update_arrows(layer)
    update_yield_to_highlights(layer)
    show_selected_regulatory_elements(layer)

# =============================================================================
#  PUBLIC ENTRY POINT  (called by QcSuitePlugin or run standalone)
# =============================================================================

def run_qc(layer=None):
    """Run the full QC pipeline on *layer* (defaults to active layer)."""
    if layer is None:
        layer = iface.activeLayer()
    if not layer or not isinstance(layer, QgsVectorLayer):
        log("No active vector layer found.", Qgis.Critical)
        return

    # Verify style_images folder exists and warn if not
    if not os.path.isdir(icon_folder):
        log(f"style_images not found at: {icon_folder} — traffic sign icons will be missing.", Qgis.Warning)

    # Clean previous outputs
    for name in ["Lane Morphology", "Speed Limit", "Passable/Non-Passable Regions",
                 "One-way / Bidirectional Way", "Stop Zones",
                 ARROW_LAYER_NAME, YIELD_TO_LAYER_NAME,
                 REG_ALL_LAYER_NAME, REG_SEL_LAYER_NAME, INTEGRITY_LAYER_NAME]:
        remove_layer_by_name(name)

    # ── Integrity check ──
    render_integrity_issues(check_lane_integrity(), layer)

    # ── Visual analysis layers ──
    create_oneway_layer(layer)
    create_stop_zone(layer)
    create_lane_morphology_layer(layer)
    create_speed_limit_layer(layer)
    create_passable_layer(layer)
    add_traffic_elements(layer)

    # ── Selection-driven overlays ──
    iface.setActiveLayer(layer)
    try:   layer.selectionChanged.disconnect()
    except Exception: pass
    layer.selectionChanged.connect(on_selection_changed)

    log("QC Tool ready. Select a centerline to inspect driving direction, yield_to and related traffic rules.", Qgis.Success)


# Allow running directly as a standalone script from QGIS console
if __name__ == "__console__":
    run_qc()
