# LiDAR Hillshade Explorer 3.2.4

A simplified desktop LiDAR hillshade application for non-technical users.

## Overview

User enters coordinates, clicks one button, and the app automatically finds LiDAR data online, processes it, generates a hillshade, and opens an interactive viewer.

## Features

- **Simple Input**: Enter latitude/longitude and area size (0.25, 0.5, or 1.0 sq mi)
- **Paste Coordinates**: One-click paste from clipboard (supports Google Maps, Google Earth KML, comma/space-separated)
- **Automatic Discovery**: Finds and downloads LiDAR from AWS/USGS datasets
- **Smart Selection**: Uses cached official USGS acquisition dates and quality
  levels to rank datasets, then tries older collections if coverage is insufficient
- **Terrain Styles**: Continuous Terrain, Preserve Large Gaps, and Custom TIN settings
- **Auto-Resolution DEM**: Analyzes point spacing and selects optimal DEM resolution
- **Interactive Viewer**: Pillow-based viewer with smooth zoom/pan controls
  - Zoom +/- buttons
  - Mouse wheel zoom
  - Left mouse drag to pan
  - Fit to window button
  - Official work-unit metadata, collection dates, QL, source DEM resolution,
    publication date, horizontal/vertical CRS, and geoid
  - Viewport-only rendering for responsive navigation on large rasters
- **Multiple Styles**:
  - Classic hillshade
  - 5 archaeology presets
  - Custom parameters (z-factor, altitude, azimuth, multidirectional)
- **KMZ Export**: Export to Google Earth format

## Installation

### Prerequisites

Development mode requires system-level tools (install via QGIS/Homebrew/etc.):
- PDAL (LiDAR processing + DEM generation)
- GDAL (hillshade generation and KMZ export)

The standalone 3.2.4 release bundles these tools and does not require users to
install Python, PDAL, GDAL, PROJ, Homebrew, or QGIS.

### Python Dependencies

```bash
pip install -r requirements.txt
```

## Usage

### Run the Application

```bash
python app.py
```

### Workflow

1. Enter location (latitude/longitude)
   - Type coordinates manually OR
   - Click "Paste Coordinates" to paste from clipboard
2. Select area size (Small/Medium/Large)
3. Enable/disable smart selection
4. Click "Generate Hillshade"
5. Wait for processing (2-10 minutes)
6. View and style hillshade in built-in viewer
   - Use zoom +/- buttons or mouse wheel
   - Drag with left mouse to pan
   - Change hillshade style (Classic/Archaeology/Custom)
   - Click "Apply" to regenerate with new style
7. Export to KMZ if desired

### Example Test Location

- Latitude: 37.10483036184007
- Longitude: -90.45131384376958
- Size: Small (0.25 sq mi)
- Smart Selection: Enabled

### Viewer Controls

**Navigation:**
- **Zoom +/- Buttons**: Click to zoom in/out
- **Mouse Wheel**: Scroll to zoom in/out
- **Left Mouse Drag**: Click and drag to pan around the image
- **Fit to Window**: Reset zoom to show entire hillshade

**Style Selection:**
- **Classic**: Standard hillshade (z=1.0, alt=45°, az=315°)
- **Archaeology Presets**: Choose from 5 archaeological visualization presets
- **Custom**: Set your own z-factor, altitude, azimuth, and multidirectional options

After changing style, click **Apply** to regenerate the hillshade with new parameters (uses existing DEM, no re-download needed).

**Export:**
- Click **Export to KMZ** to save hillshade as KMZ file for viewing in Google Earth

### Paste Coordinates Feature

The "Paste Coordinates" button supports multiple formats:

1. **Google Maps URL**: Copy coordinates from Google Maps
2. **Google Earth KML**: Copy a placemark from Google Earth
3. **Comma-separated**: `37.1032, -90.4558`
4. **Space-separated**: `37.1032 -90.4558`

Simply copy coordinates from any source, then click the button to automatically populate the input fields.

## File Structure

```
LiDARHillshadeExplorer/
├── app.py                     # Entry point
├── requirements.txt           # Python dependencies
├── lidar_explorer.spec       # PyInstaller build config
├── src/
│   ├── main_gui.py           # Main application window
│   ├── hillshade_viewer.py   # Pillow-based interactive viewer
│   ├── processing.py         # Workflow orchestrator
│   ├── lidar_core/
│   │   ├── aws_operations.py     # Dataset discovery & download
│   │   ├── dem_generator.py      # TIN DEM creation
│   │   ├── hillshade_engine.py   # Hillshade generation
│   │   └── kmz_export.py         # KMZ export
│   └── utils/
│       ├── config.py         # Configuration management
│       └── progress.py       # Progress dialog
└── output/                   # Generated files
    ├── laz/                  # Downloaded LiDAR
    ├── dem/                  # Generated DEMs
    ├── hillshades/           # Hillshade outputs
    ├── kmz/                  # KMZ exports
    └── logs/                 # Error logs
```

## Archaeology Presets

1. **Archaeology – Soft (Multi)**: z=1.0, alt=25°, multidirectional
2. **Archaeology – Very Low Sun (Multi)**: z=1.0, alt=20°, multidirectional
3. **Archaeology – Higher Sun (Multi)**: z=1.0, alt=35°, multidirectional
4. **Archaeology – Strong Relief (Multi)**: z=1.5, alt=30°, multidirectional
5. **Directional Check – NW Low Sun**: z=1.0, alt=30°, az=315°

## Building Executable

```bash
pyinstaller lidar_explorer.spec
```

Output:
- **macOS**: `dist/LiDAR Hillshade Explorer.app`
- **Windows**: `dist/LiDARHillshadeExplorer/LiDARHillshadeExplorer.exe`
- **Linux**: `dist/LiDARHillshadeExplorer/LiDARHillshadeExplorer`

## Configuration

Config file location:
- **macOS**: `~/Library/Application Support/LiDARHillshadeExplorer/config.json`
- **Windows**: `%APPDATA%/LiDARHillshadeExplorer/config.json`
- **Linux**: `~/.config/LiDARHillshadeExplorer/config.json`

## Troubleshooting

### PDAL Not Found
Install PDAL (QGIS/conda/Homebrew), or set `PDAL_BIN` environment variable.

### GDAL Not Found
Install QGIS which includes GDAL, or set `GDALDEM_BIN` environment variable.

### No LiDAR Coverage
Try a different location within the United States. The app uses USGS/AWS LiDAR datasets which primarily cover the US.

### Low Point Density
Enable "Smart Selection" to automatically try older datasets with better coverage.

## Credits

Based on LiDAR Studio codebase, redesigned for non-technical users.

## Implementation Date

Created: February 1, 2026
