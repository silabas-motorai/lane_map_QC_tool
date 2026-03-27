# Lane Map QC Tool — QGIS Plugin

QGIS plugin for comprehensive HD lane map quality control. Combines visual QC layers, topological connectivity checks (snapping & border match), logical lane scenario validation, attribute completeness check, along with dashcam frame viewer and street-level imagery (Google Street View / Mapillary).

<img width="1911" height="873" alt="Screenshot from 2026-03-16 16-08-22" src="https://github.com/user-attachments/assets/2649eb40-bb5e-469a-82b5-494009cefbf5" />

---

## Installation

### Option A — Install from ZIP 

1. Download the plugin ZIP: [lane_map_QC_tool.zip](will add the link of latest version)
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded ZIP and click **Install Plugin**
4. Restart QGIS

### Option B — Clone from GitHub (recommended)

1. Clone the repository into QGIS plugins folder

```bash
# Linux / macOS
cd ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
git clone https://github.com/silabas-motorai/lane_map_QC_tool.git
```

```bash
# Windows
cd %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\
git clone https://github.com/silabas-motorai/lane_map_QC_tool.git
```

2. Restart QGIS
3. The plugin toolbar should appear automatically. If not, go to:
**Plugins → Manage and Install Plugins → Installed** and enable **Lane Map QC Tool**.

<img width="1036" height="529" alt="Screenshot from 2026-03-16 16-17-28" src="https://github.com/user-attachments/assets/b803c094-dbae-4c70-ba34-5d04be141eeb" />

---

## Usage

### Lane Map Quality Check

Click **🗺 Lane Map Quality Check** in the toolbar and select an HD lane map layer to run QC analysis. Results are added as layers under the *Lane Map Analysis* group:

| Layer | Type | Description |
|---|---|---|
| **Driving Direction** | Line | Blue arrow overlays showing driving direction of the selected lane centerline |
| **Yield To** | Line | Red arrow overlays showing yielding centerlines of the selected lane centerline |
| **One-way / Bidirectional Way** | Line | Lane centerlines colored by one-way vs bidirectional designation |
| **Stop Zones** | Line | Boundaries flagged as stop zone areas and related closest centerline |
| **Lane Morphology** | Line | Lane centerline geometry classified by lane morphology types |
| **Speed Limit** | Line | Lane centerlines colored by assigned speed limit value |
| **Passable/Non-Passable Regions** | Line | Lane borders marked as passable, non-passable and physically non-passable |
| **Regulatory Elements** | Point | Regulatory elements with icons |
| **Related Regulatory Elements** | Point | Regulatory elements linked to a selected lane centerline |
| **Integrity Issues** | Point | Snapping gaps, stop-line connectivity, and routing errors |
| **Lanelet Issues** | Line | Logical topology errors, Road ID / Way ID mismatches, missing partners, spatial ordering mismatches, and invalid yield_to constraints |
| **Attribute Issues** | Point | Missing mandatory fields, invalid tags (typos), conditional attribute errors, and orphaned regulatory elements |

*(Note: Issue layers such as Integrity Issues, Lanelet Issues, and Attribute Issues will automatically become visible if any errors are detected.)*

### Dashcam / Street View

Click **🎥 Dashcam / Street View** to activate the map tool. Click anywhere on the map to:
- Load the nearest dashcam frame in the **Dashcam Viewer** panel
- Load the location in the **Street View** panel (Google Street View or Mapillary)

Click the marker in the Street View panel to open the location in your browser.

### Set Dashcam Paths

Click **📂 Set Dashcam Paths** to configure:
- **Overview HTML** — the `geolocated_videos.html` file inside your dashcam data folder
- **Frames Root** — the same dashcam folder containing the frame subfolders

Both paths should point to the same parent folder, for example:

```text
dashcam/
├── geolocated_videos.html   ← Overview HTML
├── 20251104_142838_0357_N_A/
├── 20251104_142738_0356_N_A-.../
└── ...
```

---

## Updating the Plugin

When a new version is available, run this in the **QGIS Python Console**:

**Linux / macOS:**
```python
import subprocess, os, qgis.utils
path = os.path.expanduser("~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/lane_map_QC_tool")
r = subprocess.run(["git", "pull"], cwd=path, capture_output=True, text=True)
print(r.stdout or r.stderr)
qgis.utils.reloadPlugin("lane_map_QC_tool")
print("Plugin reloaded.")
```

**Windows:**
```python
import subprocess, os, qgis.utils
path = os.path.join(os.environ["APPDATA"], "QGIS", "QGIS3", "profiles", "default", "python", "plugins", "lane_map_QC_tool")
r = subprocess.run(["git", "pull"], cwd=path, capture_output=True, text=True)
print(r.stdout or r.stderr)
qgis.utils.reloadPlugin("lane_map_QC_tool")
print("Plugin reloaded.")
```
---

## Requirements

- QGIS 3.x
- Python 3.10+
- Internet connection (for Street View / Mapillary and OSM map tiles)
