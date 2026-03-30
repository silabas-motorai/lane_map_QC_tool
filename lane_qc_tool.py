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
from collections import defaultdict, Counter
import os, sys, random, inspect

# -----------------------------------------------------------------------------
# Constants & Configuration
# -----------------------------------------------------------------------------

REVERSE_WAY_IDS = {100, 101, 102, 400, 401, 402, 403, 500}

ARROW_LAYER_NAME          = "Driving Direction"
YIELD_TO_LAYER_NAME       = "Yield To"
REG_ALL_LAYER_NAME        = "Regulatory Elements"
REG_SEL_LAYER_NAME        = "Related Regulatory Elements"
INTEGRITY_LAYER_NAME      = "Integrity Issues"
ROAD_ID_ISSUES_LAYER_NAME = "Lanelet Issues"
GROUP_NAME                = "Lane Map Analysis"
ATTRIBUTE_ISSUES_LAYER_NAME = "Attribute Issues"

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    script_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))

icon_folder = os.path.join(script_dir, "style_images")

# Defines valid topological scenarios for lanelets.
# Key: (total_border_count, centerline_way_id)
# Value: (list of valid border way_id pairs, lane_numbers)
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

# -----------------------------------------------------------------------------
# Utility Helpers
# -----------------------------------------------------------------------------

def log(msg, level=Qgis.Info):
    iface.messageBar().pushMessage("QC Tool", msg, level=level, duration=5)

def layer_has_fields(layer, field_names):
    names = {f.name() for f in layer.fields()}
    return all(n in names for n in field_names)

def fld(feat, name, default=""):
    try:
        val = feat[name]
        return val if val is not None else default
    except KeyError:
        return default

def needs_reversal(way_id, line):
    in_reverse = way_id in REVERSE_WAY_IDS
    return (in_reverse and line[0].x() > line[-1].x()) or \
           (not in_reverse and line[0].x() < line[-1].x())

def get_polyline(feat):
    geom = feat.geometry()
    if not geom:
        return None
    try:
        return geom.asPolyline() if not geom.isMultipart() else geom.asMultiPolyline()[0]
    except Exception:
        return None

def add_layer_to_group(layer, visible=False):
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

# -----------------------------------------------------------------------------
# Topology & Connectivity Checks
# -----------------------------------------------------------------------------

def get_border_way_ids_for_centerline(roads_with_ref, cl_way_id):
    for (rwr, ref_wid), (pairs, _) in WAY_PAIRS_MAP.items():
        if rwr != roads_with_ref:
            continue
        for right_w, left_w in pairs:
            if min(right_w, left_w) * 100 + 12 == cl_way_id:
                return (right_w, left_w)
    return None

def check_lane_integrity(snap_tol=1e-15, graph_tol=1e-5):
    """
    Checks physical connectivity (snapping), flow direction continuity,
    and stop line intersections across the lane network.
    """
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

    if stop_wait_lines:
        lane_index, lane_by_id = QgsSpatialIndex(), {}
        for bf in all_lines:
            lane_index.insertFeature(bf)
            lane_by_id[bf.id()] = bf

        endpoint_r = 0.00005

        for f in stop_wait_lines:
            sl_geom = f.geometry()
            if not sl_geom: continue
            sl_line = get_polyline(f)
            if not sl_line: continue
            wid, rid = fld(f, 'way_id'), fld(f, 'road_id')

            bbox = sl_geom.boundingBox()
            bbox.grow(endpoint_r * 3)
            nearby_ids_list = lane_index.intersects(bbox)

            for lid in nearby_ids_list:
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
            return
    temp = QgsVectorLayer(f"Point?crs={source_layer.crs().authid()}", INTEGRITY_LAYER_NAME, "memory")
    pr = temp.dataProvider()
    pr.addAttributes([QgsField("road_id",    QVariant.String),
                      QgsField("way_id",     QVariant.String),
                      QgsField("issue_type", QVariant.String)])
    temp.updateFields()
    seen = set()
    added_count = 0
    for iss in issues:
        sig = (round(iss['point'].x(), 5), round(iss['point'].y(), 5), iss['type'])
        if sig in seen: continue
        seen.add(sig)
        feat = QgsFeature(); feat.setGeometry(QgsGeometry.fromPointXY(iss['point']))
        feat.setAttributes([str(iss['road_id']), str(iss['way_id']), iss['type']])
        pr.addFeature(feat)
        added_count += 1
    if added_count == 0:
            return
    temp.updateExtents()
    symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': 'transparent',
                                           'outline_color': 'blue', 'outline_width': '0.6', 'size': '4.5'})
    temp.setRenderer(QgsSingleSymbolRenderer(symbol))
    add_layer_to_group(temp, visible=True)
    log(f"{temp.featureCount()} snapping / integrity issues found.", Qgis.Warning)

# -----------------------------------------------------------------------------
# Scenario Integrity Check (Road ID & Way ID valid combinations)
# -----------------------------------------------------------------------------

def check_road_id_way_integrity(layer):
    """
    Validates logical group relationships: missing or duplicate way_id/road_ids,
    isolated lines, lanelet scenarios, and ordering of way_ids wrt. lane scenarios.
    """
    features = list(layer.getFeatures())
    if not features: 
        return []

    road_id_groups = defaultdict(list)
    issues = []

    def _is_border(f):
        lt = str(fld(f, 'lane_type')).lower()
        at = str(fld(f, 'area_type')).strip().lower()
        return lt in ('road', 'cycle', 'road_cycle') and at in ('', 'none', 'null')

    def _is_centerline(f):
        return str(fld(f, 'lane_type')).lower() == 'centerline'

    def safe_int_way_id(f):
        val = str(fld(f, 'way_id')).strip()
        if not val or val.lower() == 'null': 
            return None
        try: 
            return int(val)
        except ValueError: 
            return None

    # --- STEP 1: Missing IDs and Grouping ---
    for f in features:
        rid = str(fld(f, 'road_id')).strip()
        lt = str(fld(f, 'lane_type')).lower()
        
        if _is_border(f) or _is_centerline(f):
            if not rid or rid.lower() == 'null':
                issues.append({'feat': f, 'road_id': 'NULL', 'way_id': str(fld(f, 'way_id')), 'issue_type': 'Missing Road ID'})
                continue
            if safe_int_way_id(f) is None:
                issues.append({'feat': f, 'road_id': rid, 'way_id': 'NULL', 'issue_type': 'Missing Way ID'})
                continue

        if lt != 'pedestrian_marking':
            road_id_groups[rid].append(f)

    # --- STEP 2, 3, 4 & 5: Core Logical and Spatial Validations ---
    for rid, feats in road_id_groups.items():
        if not rid or rid.lower() == 'null': 
            continue

        group_has_errors = False

        way_id_counts = Counter()
        for f in feats:
            wid = safe_int_way_id(f)
            if wid: 
                way_id_counts[wid] += 1

        border_feats = [f for f in feats if _is_border(f)]
        cl_feats = [f for f in feats if _is_centerline(f)]
        
        actual_b_wids = {safe_int_way_id(f) for f in border_feats if safe_int_way_id(f)}
        actual_cl_wids = {safe_int_way_id(f) for f in cl_feats if safe_int_way_id(f)}

        border_lt_by_wid = {safe_int_way_id(f): str(fld(f, 'lane_type')).lower() for f in border_feats if safe_int_way_id(f)}

        def _is_cycle_wid(wid):
            return border_lt_by_wid.get(wid, '') in ('cycle', 'road_cycle')

        # A. Duplicate Way ID Check
        for f in feats:
            wid = safe_int_way_id(f)
            if wid and way_id_counts[wid] > 1:
                issues.append({
                    'feat': f, 'road_id': rid, 'way_id': str(wid),
                    'issue_type': f'Lanelet relation could not be created (Duplicate way_id:{wid} in road_id:{rid})'
                })
                group_has_errors = True

        # B. Centerline Partner Check
        for f in cl_feats:
            cl_wid = safe_int_way_id(f)
            if not cl_wid: continue
            
            found_match = False
            for (rwr, mapping_wid), (border_pairs, _) in WAY_PAIRS_MAP.items():
                for p in border_pairs:
                    if cl_wid == int(f"{min(p)}12") and set(p).issubset(actual_b_wids):
                        found_match = True
                        break
                if found_match: break
            
            if not found_match:
                issues.append({
                    'feat': f, 'road_id': rid, 'way_id': str(cl_wid),
                    'issue_type': f'Lanelet relation could not be created (Centerline with way_id:{cl_wid} lacks required borders in road_id:{rid})'
                })
                group_has_errors = True

        # C. Border Partner Check
        for f in border_feats:
            b_wid = safe_int_way_id(f)
            if not b_wid: continue

            found_match = False
            possible_partners = set()
            
            for (rwr, mapping_wid), (border_pairs, _) in WAY_PAIRS_MAP.items():
                for p in border_pairs:
                    if b_wid not in p: continue
                    
                    partner_wid = next((w for w in p if w != b_wid), None)
                    if partner_wid:
                        possible_partners.add(partner_wid)
                        
                    if partner_wid not in actual_b_wids: continue
                    
                    if _is_cycle_wid(b_wid) or _is_cycle_wid(partner_wid):
                        found_match = True
                        break
                    
                    expected_cl = int(f"{min(p)}12")
                    if expected_cl in actual_cl_wids:
                        found_match = True
                        break
                
                if found_match: break

            if not found_match:
                partners_str = "[" + ", ".join(map(str, sorted(possible_partners))) + "]" if possible_partners else "None"
                
                if len(actual_b_wids) > 1:
                    issue_msg = f'Lanelet relation could not be created (Isolated border with way_id:{b_wid}, does not fit the scenario of road_id:{rid})'
                else:
                    issue_msg = f'Lanelet relation could not be created (Isolated border with way_id:{b_wid}, lacks a valid partner or centerline in road_id:{rid}. Expected one of partners: {partners_str})'
                
                issues.append({
                    'feat': f, 'road_id': rid, 'way_id': str(b_wid),
                    'issue_type': issue_msg
                })
                group_has_errors = True

        # --- STEP 4: Lane Scenario Check ---
        if not group_has_errors and actual_b_wids:
            n_borders = len(actual_b_wids)
            allowed_pairs = []
            for (bc, _), (border_pairs, _) in WAY_PAIRS_MAP.items():
                if bc == n_borders:
                    allowed_pairs.extend(border_pairs)
            
            internal_edges = [p for p in allowed_pairs if p[0] in actual_b_wids and p[1] in actual_b_wids]
            
            adj = defaultdict(list)
            for u, v in internal_edges:
                adj[u].append(v)
                adj[v].append(u)
            
            start_node = next(iter(actual_b_wids))
            visited = set()
            queue = [start_node]
            
            while queue:
                curr = queue.pop(0)
                if curr not in visited:
                    visited.add(curr)
                    queue.extend(adj[curr])
            
            if len(visited) != n_borders:
                comp_adj = defaultdict(list)
                for u, v in allowed_pairs:
                    comp_adj[u].append(v)
                    comp_adj[v].append(u)
                
                all_scenarios = []
                seen = set()
                for node in comp_adj:
                    if node not in seen:
                        comp = set()
                        q = [node]
                        while q:
                            c = q.pop(0)
                            if c not in comp:
                                comp.add(c)
                                seen.add(c)
                                q.extend(comp_adj[c])
                        all_scenarios.append(comp)
                
                best_match = None
                max_intersect = -1
                for sc in all_scenarios:
                    intersect = len(actual_b_wids.intersection(sc))
                    if intersect > max_intersect:
                        max_intersect = intersect
                        best_match = sc
                
                actual_str = "-".join(map(str, sorted(actual_b_wids)))
                expected_str = "-".join(map(str, sorted(best_match))) if best_match else "None"
                
                n_lanes = n_borders - 1
                lane_desc = "single lane" if n_lanes == 1 else f"{n_lanes}-lane"
                
                for f in border_feats:
                    b_wid = safe_int_way_id(f)
                    if b_wid:
                        safe_rid = str(fld(f, 'road_id')) 
                        if best_match and b_wid not in best_match:
                            issues.append({
                                'feat': f, 'road_id': safe_rid, 'way_id': str(b_wid),
                                'issue_type': f'Lanelet relation could not be created (Invalid scenario, line with way_id:{b_wid} is incorrect for a {lane_desc} road. Found [{actual_str}], expected [{expected_str}])'
                            })
                            group_has_errors = True
                        elif not best_match:
                            issues.append({
                                'feat': f, 'road_id': safe_rid, 'way_id': str(b_wid),
                                'issue_type': f'Lanelet relation could not be created (Invalid scenario, no valid {lane_desc} road configuration matches your group [{actual_str}])'
                            })
                            group_has_errors = True

# --- STEP 5: WAY ID ORDERING CHECK  ---
        if not group_has_errors and len(actual_b_wids) > 1:
            valid_b_feats = [f for f in border_feats if safe_int_way_id(f) is not None]
            if len(valid_b_feats) > 1:
                valid_b_feats.sort(key=lambda f: safe_int_way_id(f))
                ref_feat = valid_b_feats[0]
                ref_geom = ref_feat.geometry()
                
                length = ref_geom.length()
                mid_dist = length / 2.0
                delta = length * 0.05 if length > 0 else 0
                
                pt_A = ref_geom.interpolate(max(0, mid_dist - delta)).asPoint()
                pt_B = ref_geom.interpolate(min(length, mid_dist + delta)).asPoint()
                
                if pt_A != pt_B:
                    spatial_order = []
                    for f in valid_b_feats:
                        geom = f.geometry()
                        pt = geom.interpolate(geom.length() / 2.0).asPoint()
                        signed_dist = (pt.x() - pt_A.x()) * (pt_B.y() - pt_A.y()) - (pt.y() - pt_A.y()) * (pt_B.x() - pt_A.x())
                        spatial_order.append((signed_dist, safe_int_way_id(f), f))
                    
                    spatial_order.sort(key=lambda x: x[0])
                    physical_wids = [x[1] for x in spatial_order]
                    
                    n_borders = len(actual_b_wids)
                    allowed_pairs = []
                    for (bc, _), (border_pairs, _) in WAY_PAIRS_MAP.items():
                        if bc == n_borders:
                            allowed_pairs.extend(border_pairs)
                    
                    scenario_edges = [p for p in allowed_pairs if p[0] in actual_b_wids and p[1] in actual_b_wids]
                    
                    sc_adj = defaultdict(list)
                    for u, v in scenario_edges:
                        sc_adj[u].append(v)
                        sc_adj[v].append(u)
                        
                    endpoints = [node for node, neighbors in sc_adj.items() if len(neighbors) == 1]
                    
                    expected_seq = []
                    if endpoints:
                        start = endpoints[0]
                        curr = start
                        prev = None
                        expected_seq.append(curr)
                        while len(expected_seq) < len(actual_b_wids):
                            next_nodes = [n for n in sc_adj[curr] if n != prev]
                            if not next_nodes: break
                            prev = curr
                            curr = next_nodes[0]
                            expected_seq.append(curr)
                    else:
                        expected_seq = sorted(list(actual_b_wids))
                        
                    expected_seq_rev = list(reversed(expected_seq))
                    
                    if physical_wids != expected_seq and physical_wids != expected_seq_rev:
                        actual_str = "-".join(map(str, physical_wids))
                        best_expected = expected_seq if physical_wids[0] == expected_seq[0] else expected_seq_rev
                        expected_str = "-".join(map(str, best_expected))
                        
                        valid_cl_feats = [cf for cf in cl_feats if safe_int_way_id(cf) is not None]
                        for f in valid_b_feats + valid_cl_feats:
                            issues.append({
                                'feat': f, 
                                'road_id': rid, 
                                'way_id': str(safe_int_way_id(f)),
                                'issue_type': f'Lanelet relation could not be created (Order mismatch, order of border lines [{actual_str}] does not match logical way_id order [{expected_str}])'
                            })



    # --- STEP 6: Yield To Valid Minimum ID Check ---
    if 'yield_to' in [field.name() for field in layer.fields()]:
        actual_b_wids_by_rid = defaultdict(set)
        border_lt_map = {}
        for f in features:
            rid = str(fld(f, 'road_id')).strip()
            wid = safe_int_way_id(f)
            if _is_border(f) and rid and rid.lower() != 'null' and wid is not None:
                actual_b_wids_by_rid[rid].add(wid)
                border_lt_map[(rid, wid)] = str(fld(f, 'lane_type')).lower()
        
        for f in features:
            yt = str(fld(f, 'yield_to')).strip()
            if not yt or yt.lower() == 'null': 
                continue
            
            my_rid = str(fld(f, 'road_id', 'NULL'))
            my_wid = str(fld(f, 'way_id', 'NULL'))

            for t in yt.split(","):
                t = t.strip()
                if "_" in t:
                    parts = t.split("_")
                    if len(parts) >= 2:
                        ref_rid = parts[0]
                        try:
                            ref_wid = int(parts[1])
                            if (ref_rid, ref_wid) in border_lt_map:
                                lt = border_lt_map[(ref_rid, ref_wid)]
                                possible_partners = set()
                                for _, (border_pairs, _) in WAY_PAIRS_MAP.items():
                                    for p in border_pairs:
                                        if ref_wid in p:
                                            partner_wid = p[0] if p[1] == ref_wid else p[1]
                                            if partner_wid in actual_b_wids_by_rid[ref_rid]:
                                                partner_lt = border_lt_map.get((ref_rid, partner_wid), "")
                                                if lt in ('cycle', 'road_cycle') or partner_lt in ('cycle', 'road_cycle'):
                                                    possible_partners.add(partner_wid)
                                
                                if possible_partners:
                                    is_already_min = any(ref_wid < p for p in possible_partners)
                                    if not is_already_min:
                                        best_partner = min(possible_partners, key=lambda x: abs(x - ref_wid))
                                        issues.append({
                                            'feat': f, 'road_id': my_rid, 'way_id': my_wid,
                                            'issue_type': f'Lanelet relation could not be created (Invalid yield_to: {ref_rid}_{ref_wid} forms a cycle lane with way_id {best_partner}. Must use min way_id: {best_partner})'
                                        })
                        except ValueError:
                            pass

    return issues

# -----------------------------------------------------------------------------
# Attribute Completeness & Rule Checks
# -----------------------------------------------------------------------------

def check_attribute_completeness(layer):
    """
    Checks specific attribute rules: mandatory fields, conditional requirements,
    unique constraints, valid tag values (typo checks), and relational linkages.
    """
    features = list(layer.getFeatures())
    if not features:
        return []

    issues = []

    def is_empty(val):
        if val is None: return True
        s = str(val).strip().lower()
        return s in ('', 'null', 'none')

    re_id_counts = Counter()
    all_re_ids = set()
    referenced_re_ids = set()

    valid_lane_types = {'road', 'cycle', 'road_cycle', 'regulatory_element', 'pedestrian_marking', 'centerline'}
    valid_line_types = {'line_thin', 'road_border', 'traffic_sign', 'traffic_light', 'right_of_way'}
    valid_morphs     = {'straight', 'curve', 'intersection', 'split', 'merge', 'roundabout'}
    valid_area_types = {'parking', 'exit', 'mai_bus_stop'}
    valid_oneway     = {'yes', 'no'}

    # Pre-process: Collect re_ids and traffic_rule linkages
    for f in features:
        re_val = str(fld(f, 're_id')).strip()
        if not is_empty(re_val) and str(fld(f, 'lane_type')).strip().lower() == 'regulatory_element':
            re_id_counts[re_val] += 1
            all_re_ids.add(re_val)

        tr_val = str(fld(f, 'traffic_rule')).strip()
        if not is_empty(tr_val):
            for t in tr_val.split(","):
                referenced_re_ids.add(t.strip())

    for f in features:
        geom = f.geometry()
        if not geom or geom.isEmpty():
            continue
            
        pt = geom.centroid().asPoint()
        fid = f.id()

        # Reading and standardizing values to lowercase for robust checks
        line_type       = str(fld(f, 'line_type')).strip().lower()
        line_sub        = str(fld(f, 'line_sub')).strip()
        lane_type       = str(fld(f, 'lane_type')).strip().lower()
        area_type       = str(fld(f, 'area_type')).strip().lower()
        road_id         = str(fld(f, 'road_id')).strip()
        way_id          = str(fld(f, 'way_id')).strip()
        speed_limit     = str(fld(f, 'speed_limit')).strip()
        lane_morph      = str(fld(f, 'lane_morphology')).strip().lower()
        closest_lane    = str(fld(f, 'closest_lane')).strip()
        name            = str(fld(f, 'name')).strip()
        re_id           = str(fld(f, 're_id')).strip()
        area_id         = str(fld(f, 'area_id')).strip()
        one_way         = str(fld(f, 'one_way')).strip().lower()
        traffic_rule    = str(fld(f, 'traffic_rule')).strip()

        # Rule 1: Typo checks for base classifications
        if not is_empty(lane_type) and lane_type not in valid_lane_types:
            issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"typo/invalid lane_type: '{lane_type}'. Expected one of: {valid_lane_types}"})
        if not is_empty(line_type) and line_type not in valid_line_types:
            issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"typo/invalid line_type: '{line_type}'. Expected one of: {valid_line_types}"})
        if not is_empty(area_type) and area_type not in valid_area_types:
            issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"typo/invalid area_type: '{area_type}'. Expected one of: {valid_area_types}"})

        # Rule 2: line_type, line_sub and lane_type must be filled
        if is_empty(line_type) or is_empty(line_sub) or is_empty(lane_type):
            issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': 'missing basic attributes: line_type, line_sub, or lane_type'})

        # Rule 3: centerline, cycle, road, road_cycle must have filled road_id and way_id values
        if lane_type in ['centerline', 'cycle', 'road', 'road_cycle']:
            # except area roads
            is_road_with_area = (lane_type == 'road' and not is_empty(area_type))
            if not is_road_with_area:
                if is_empty(road_id) or is_empty(way_id):
                    issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f'{lane_type} requires road_id and way_id'})

        # Rule 4: centerline must have speed_limit, lane_morphology (one_way is optional but checked for typos if filled)
        if lane_type == 'centerline':
            if is_empty(speed_limit):
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': 'centerline missing speed_limit'})
            
            if is_empty(lane_morph):
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': 'centerline missing lane_morphology'})
            elif lane_morph not in valid_morphs:
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"typo/invalid lane_morphology: '{lane_morph}'. Expected one of: {valid_morphs}"})
                
            # one_way NULL bırakılabilir, ama eğer doluysa sadece 'yes' veya 'no' olmalıdır
            if not is_empty(one_way) and one_way not in valid_oneway:
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"typo/invalid one_way tag: '{one_way}'. Must be 'yes' or 'no'"})

        # Rule 5: area_type 'exit', 'parking', 'mai_bus_stop' -> NULL road_id
        if area_type in ['exit', 'parking', 'mai_bus_stop']:
            if not is_empty(road_id):
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"road_id must be NULL for area_type '{area_type}'"})

        # Rule 6: closest_lane must be filled for area_types 'exit', 'mai_bus_stop' 
        if area_type in ['exit', 'mai_bus_stop']:
            if is_empty(closest_lane):
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"closest_lane is required for area_type '{area_type}'"})

        # Rule 7: name must be filled for area_type 'mai_bus_stop' 
        if area_type == 'mai_bus_stop':
            if is_empty(name):
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"name is required for area_type '{area_type}'"})

        # Rule 8: if the area_type is filled then area_id must be filled
        if area_type in ['mai_bus_stop', 'exit', 'parking']:
            if is_empty(area_id):
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': "area_id is required when area_type is filled"})

        # Rule 9: regulatory_elements must have unique re_ids and be linked to a centerline
        if lane_type == 'regulatory_element':
            if is_empty(re_id):
                issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': 're_id is required for regulatory_element'})
            else:
                if re_id_counts[re_id] > 1:
                    issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"duplicate re_id: {re_id} is not unique"})
                
                # Orphan Check: Ignore if line_sub contains 'de294' or 'de341'
                if re_id not in referenced_re_ids:
                    if 'de294' not in line_sub.lower() and 'de341' not in line_sub.lower():
                        issues.append({'point': pt, 'fid': fid, 'lane_type': lane_type, 'road_id': road_id, 're_id': re_id, 'issue': f"orphan regulatory element: {re_id} is not linked to any centerline"})

        # Rule 10: traffic_rule must point to a valid re_id
        '''
        if lane_type == 'centerline' and not is_empty(traffic_rule):
            for t in traffic_rule.split(","):
                t_clean = t.strip()
                if t_clean and t_clean not in all_re_ids:
                    issues.append({'point': pt, 'fid': fid, 'issue': f"invalid traffic_rule: {t_clean} does not match any existing re_id"})
        '''
    return issues

    return issues

def render_attribute_issues(issues, source_layer):
    remove_layer_by_name(ATTRIBUTE_ISSUES_LAYER_NAME)
    
    if not issues:
        log("Attributes: All required fields are properly filled.", Qgis.Success)
        return

    temp = QgsVectorLayer(f"Point?crs={source_layer.crs().authid()}", ATTRIBUTE_ISSUES_LAYER_NAME, "memory")
    pr = temp.dataProvider()
    pr.addAttributes([
        QgsField("id", QVariant.String),
        QgsField("issue_type", QVariant.String)
    ])
    temp.updateFields()

    new_feats = []
    for iss in issues:
        nf = QgsFeature(temp.fields())
        nf.setGeometry(QgsGeometry.fromPointXY(iss['point']))
        if iss.get('lane_type', '').strip().lower() == 'regulatory_element':
            nf['id'] = f"re_id: {iss.get('re_id', '')}"
        else:
            nf['id'] = f"road_id: {iss.get('road_id', '')}"
        nf['issue_type'] = iss['issue']
        new_feats.append(nf)

    pr.addFeatures(new_feats)
    temp.updateExtents()

    symbol = QgsMarkerSymbol.createSimple({
        'name': 'triangle', 'color': '#FFA500', 'outline_color': 'black', 'size': '4.0'
    })
    temp.setRenderer(QgsSingleSymbolRenderer(symbol))

    lbl = QgsPalLayerSettings()
    lbl.fieldName = "issue_type"
    lbl.enabled = True
    lbl.placement = QgsPalLayerSettings.Placement.OverPoint
    lbl.yOffset = -5
    
    tf = QgsTextFormat()
    tf.setSize(8)
    fnt = QFont("Arial"); fnt.setBold(True); tf.setFont(fnt)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(1.0); tf.setBuffer(buf)
    lbl.setFormat(tf)

    temp.setLabeling(QgsVectorLayerSimpleLabeling(lbl))
    temp.setLabelsEnabled(True)

    add_layer_to_group(temp, visible=True)
    log(f"{len(new_feats)} Attribute missing/invalid.", Qgis.Warning)

def render_road_id_issues(issues, source_layer):
    remove_layer_by_name(ROAD_ID_ISSUES_LAYER_NAME)
    if not issues:
            return
    temp = QgsVectorLayer(
        f"LineString?crs={source_layer.crs().authid()}",
        ROAD_ID_ISSUES_LAYER_NAME, "memory"
    )
    pr = temp.dataProvider()
    pr.addAttributes([
        QgsField("road_id",    QVariant.String),
        QgsField("way_id",     QVariant.String),
        QgsField("issue_type", QVariant.String),
    ])
    temp.updateFields()

    new_feats = []
    seen_keys = set()

    for iss in issues:
        orig = iss['feat']
        key  = (orig.id(), iss['issue_type'])
        if key in seen_keys:
            continue
        seen_keys.add(key)

        geom = orig.geometry()
        if not geom or geom.isEmpty():
            continue

        if geom.isMultipart():
            parts = geom.asMultiPolyline()
            if not parts:
                continue
            geom = QgsGeometry.fromPolylineXY(parts[0])

        nf = QgsFeature(temp.fields())
        nf.setGeometry(QgsGeometry(geom))
        nf['road_id']    = str(iss['road_id'])
        nf['way_id']     = str(iss['way_id'])
        nf['issue_type'] = iss['issue_type']
        new_feats.append(nf)
    if not new_feats:
            return
    pr.addFeatures(new_feats)
    temp.updateExtents()

    symbol = QgsLineSymbol.createSimple({'color': '#FF0000', 'width': '1.8', 'line_style': 'solid'})
    temp.setRenderer(QgsSingleSymbolRenderer(symbol))

    lbl = QgsPalLayerSettings()
    lbl.fieldName    = 'concat(\'road_id: \', "road_id", \'   way_id: \', "way_id")'
    lbl.isExpression = True
    lbl.enabled      = True
    lbl.placement    = QgsPalLayerSettings.Placement.Line

    tf  = QgsTextFormat()
    tf.setSize(7)
    fnt = QFont("Arial"); fnt.setBold(True); tf.setFont(fnt)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True); buf.setSize(0.8); tf.setBuffer(buf)
    lbl.setFormat(tf)

    temp.setLabeling(QgsVectorLayerSimpleLabeling(lbl))
    temp.setLabelsEnabled(True)

    add_layer_to_group(temp, visible=True)
    log(f"{temp.featureCount()} lanelet issues found.", Qgis.Warning)

# -----------------------------------------------------------------------------
# Visual Feedback Layers
# -----------------------------------------------------------------------------

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

        if lt in ["regulatory_element", "pedestrian_marking"]:
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
        poly = geom.asPolyline() if not geom.isEmpty() else []
        if not poly: continue

        nf = QgsFeature(out.fields()); nf.setGeometry(geom)
        nf["orig_fid"] = feat.id()

        if len(poly) == 5 and poly[0] == poly[-1]:
            nf["feature_type"] = "zone"
            nf["name"] = feat["name"] or ""
        else:
            nf["feature_type"] = "invalid stop zone"
            nf["name"] = f"INVALID ({len(poly) - 1} nodes, expected 4)"

        feats.append(nf)
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

    if not feats:
        return

    out.startEditing(); out.addFeatures(feats); out.commitChanges()
    categories = [
        QgsRendererCategory("zone",       QgsLineSymbol.createSimple({'color':'#0f18f6','width':'3'}), "Stop Zone"),
        QgsRendererCategory("centerline", QgsLineSymbol.createSimple({'color':'#FF3333','width':'1.0','line_style':'dash'}), "Related Centerline"),
    ]
    if any(nf["feature_type"] == "invalid stop zone" for nf in feats):
        categories.insert(1, QgsRendererCategory("invalid stop zone", QgsLineSymbol.createSimple({'color':'#ff00ff','width':'3','line_style':'dot'}), "Invalid Stop Zone"))
    renderer = QgsCategorizedSymbolRenderer("feature_type", categories)
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
        morph = str(feat["lane_morphology"]).strip().lower()
        lt    = str(feat["lane_type"]).strip().lower() if "lane_type" in input_layer.fields().names() else ""
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
        lt    = str(feat["lane_type"]).strip().lower() if feat["lane_type"] else ""
        ltype = str(feat["line_type"]).strip().lower() if feat["lane_type"] else ""
        lsub  = str(feat["line_sub"]).strip().lower()  if feat["line_sub"]  else ""
        at    = feat["area_type"]
        if lt not in ["road","cycle","road_cycle"] or at not in [None,""]: continue
        if ltype == "line_thin":
            status = "passable" if lsub == "dashed" else "non-passable" if lsub == "solid" else None
        elif ltype == "road_border":
            status = "physically non-passable"
        else:
            status = None
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
    pr.addAttributes([QgsField("line_sub",  QVariant.String),
                      QgsField("icon_code", QVariant.String),
                      QgsField("has_icon",  QVariant.Bool)])
    lbl_layer.updateFields(); lbl_layer.startEditing()
    V_OFF     = 0.01 if "4326" not in layer.crs().authid() else 0.0000001
    type_idx  = {t:i for i,t in enumerate(sorted({f["line_sub"] for f in reg_feats if f["line_sub"]}))}

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
            has_icon  = os.path.exists(os.path.join(icon_folder, f"{icon_code}.png"))
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
        pr.addAttributes([QgsField("re_id",     QVariant.String),
                          QgsField("line_sub",  QVariant.String),
                          QgsField("icon_code", QVariant.String),
                          QgsField("has_icon",  QVariant.Bool)])
        mem_layer.updateFields()
        add_layer_outside_group(mem_layer, visible=True, insert_position=3)

    mem_layer.startEditing(); mem_layer.dataProvider().truncate()
    if not layer_has_fields(layer, ["line_type","line_sub","re_id"]):
        mem_layer.commitChanges(); return

    reg_feats = [f for f in layer.getFeatures()
                 if f["line_type"] and str(f["line_type"]).strip().lower() in ["traffic_sign","traffic_light"]]

    V_OFF    = 0.01 if "4326" not in layer.crs().authid() else 0.0000001
    type_idx = {t:i for i,t in enumerate(sorted({str(f["line_sub"]).strip() for f in reg_feats if f["line_sub"]}))}

    feats = []
    for tr in traffic_rules:
        for f in [x for x in reg_feats if str(x["re_id"]).strip() == tr]:
            geom = f.geometry()
            if geom.isEmpty(): continue
            centroid  = geom.centroid().asPoint()
            lsub      = str(f["line_sub"]).strip() if f["line_sub"] else "default_icon"
            icon_code = (lsub[2:] if lsub.startswith("de") else lsub).strip()
            has_icon  = os.path.exists(os.path.join(icon_folder, f"{icon_code}.png"))
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

# -----------------------------------------------------------------------------
# Event Handlers & Entry Point
# -----------------------------------------------------------------------------

def on_selection_changed():
    layer = iface.activeLayer()
    if not layer or not isinstance(layer, QgsVectorLayer): return
    update_arrows(layer)
    update_yield_to_highlights(layer)
    show_selected_regulatory_elements(layer)

def run_qc(layer=None):
    if layer is None:
        layer = iface.activeLayer()
    if not layer or not isinstance(layer, QgsVectorLayer):
        log("No active vector layer found.", Qgis.Critical)
        return

    if not os.path.isdir(icon_folder):
        log(f"style_images not found at: {icon_folder} — traffic sign icons will be missing.", Qgis.Warning)

    for name in ["Lane Morphology", "Speed Limit", "Passable/Non-Passable Regions",
                 "One-way / Bidirectional Way", "Stop Zones",
                 ARROW_LAYER_NAME, YIELD_TO_LAYER_NAME,
                 REG_ALL_LAYER_NAME, REG_SEL_LAYER_NAME,
                 INTEGRITY_LAYER_NAME, ROAD_ID_ISSUES_LAYER_NAME,
                 ATTRIBUTE_ISSUES_LAYER_NAME]:
        remove_layer_by_name(name)

    attr_issues = check_attribute_completeness(layer)
    render_attribute_issues(attr_issues, layer)
    
    
    render_integrity_issues(check_lane_integrity(), layer)
    render_road_id_issues(check_road_id_way_integrity(layer), layer)
    create_oneway_layer(layer)
    create_stop_zone(layer)
    create_lane_morphology_layer(layer)
    create_speed_limit_layer(layer)
    create_passable_layer(layer)
    add_traffic_elements(layer)

    iface.setActiveLayer(layer)
    try:   layer.selectionChanged.disconnect()
    except Exception: pass
    layer.selectionChanged.connect(on_selection_changed)

    log("QC Tool ready. Select a centerline to inspect driving direction, yield_to and related traffic rules.", Qgis.Success)

if __name__ == "__console__":
    run_qc()