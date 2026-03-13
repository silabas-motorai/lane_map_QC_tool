# Lane Map QC Tool — QGIS Plugin

QGIS plugin for lane map quality control. Combines lane visual lane map analysis with temporary layers, lane integrity checking, dashcam frame viewer, and street-level imagery (Google Street View / Mapillary).

---

## Installation

### 1. Clone the repository into QGIS plugins folder

```bash
cd ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
git clone https://github.com/silabas-motorai/lane_map_QC_tool.git
```

### 2. Restart QGIS

The plugin toolbar should appear automatically. If not, go to:
**Plugins → Manage and Install Plugins → Installed** and enable **Lane Map QC Tool**.

---

## Usage

### Lane Map Quality Check
Click **🗺 Lane Map Quality Check** in the toolbar and select an HD lane map layer to run QC analysis. Results are added as layers under the *Lane Map Analysis* group.

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
```
dashcam/
├── geolocated_videos.html   ← Overview HTML
├── 20251104_142838_0357_N_A/
├── 20251104_142738_0356_N_A-.../
└── ...
```

---

## Updating the Plugin

When a new version is available, run this in the **QGIS Python Console**:

```python
import subprocess, os, qgis.utils
path = os.path.expanduser("~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/lane_map_QC_tool")
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
