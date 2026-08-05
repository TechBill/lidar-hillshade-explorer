# LiDAR Hillshade Explorer 3.0

LiDAR Hillshade Explorer is a lightweight desktop app for exploring terrain using LiDAR elevation data.  
Enter coordinates, click one button, and the app downloads available LiDAR, processes it, generates a hillshade image, and opens an interactive viewer.

This tool is designed for research and visualization. It is **not survey-grade GIS**.

---

## What's New in 3.0

- Terrain Style presets use TIN interpolation and configurable gap filling to
  reduce black building footprints and small holes.
- Dataset ranking uses official USGS WESM collection dates, quality levels, and
  source resolution instead of relying on years guessed from filenames.
- USGS metadata is cached for seven days, with stale-cache and estimated-year
  fallbacks when the metadata service is unavailable.
- The viewer displays the exact work unit, acquisition dates, quality level,
  source DEM resolution, publication date, CRS, geoid, point spacing, and
  generated DEM resolution.
- Zooming and panning use viewport-only rendering for much faster navigation of
  large hillshades.
- The viewer has larger system fonts, a wider scrollable sidebar, and sizing
  that adapts to shorter Mac displays.

---

## Features

- One-click hillshade generation
- Paste coordinates from Google Earth, Google Maps, or plain text
- Interactive viewer with pan, zoom, and multiple hillshade styles
- View official LiDAR work-unit metadata: acquisition dates, quality level,
  source DEM resolution, publication date, horizontal/vertical CRS, and geoid
- Viewport-based rendering for smooth zooming and panning on large hillshades
- 5 archaeology presets for feature detection (low sun, multidirectional)
- Custom hillshade parameters (z-factor, altitude, azimuth, multidirectional)
- Export to GeoTIFF or KMZ (Google Earth)
- Smart dataset selection using authoritative USGS acquisition dates and quality levels
- Official USGS WESM metadata cached locally and refreshed every seven days
- Main-screen terrain styles for continuous output or preserving large gaps
- Custom TIN/DEM settings (buffer, edge factor, fill distance, smoothing)
- Processing log window for troubleshooting
- **Standalone app** — no dependencies to install (all binaries bundled)

---

## Security Notice (macOS + Windows)

Because this app is distributed as a downloadable app (not from the official Apple App Store or Microsoft Store), your computer may show a security warning the first time you run it. This is normal for many independent and open-source apps.

### macOS (Apple Silicon)

The current 3.0 macOS release is built for Apple Silicon and runs natively on
M1, M2, M3, M4, and newer ARM64 Macs. It does not require Rosetta. An Intel Mac
requires a separately built x86_64 release.

If macOS blocks the app with a message like:

"LiDAR Hillshade Explorer can't be opened because Apple cannot check it for malicious software."

Do this:

1. Try opening the app once (so macOS registers it)
2. Go to **System Settings**
3. Click **Privacy & Security**
4. Scroll down to the **Security** section
5. Click **Open Anyway**
6. Confirm again if prompted

After you approve it once, the app should open normally in the future.

### Install the macOS 3.0 Release

1. Extract `LiDAR-Hillshade-Explorer-3.0-macOS-arm64.zip`.
2. Drag **LiDAR Hillshade Explorer.app** into **Applications**.
3. Open the app. If macOS blocks the first launch, follow the steps above.

The `.app` is the complete standalone application. Users do not need the
neighboring PyInstaller staging folder and do not need to install Python,
Homebrew, PDAL, GDAL, PROJ, or QGIS. Internet access is required to discover and
download USGS LiDAR data.

### Windows

If Windows shows:

"Windows protected your PC"

Do this:

1. Click **More info**
2. Click **Run anyway**

This is common for smaller apps that are not digitally signed.

---

## Quick Start

1. Launch the app
2. Enter coordinates (Latitude, Longitude)
3. Select area size (Small / Medium / Large)
4. Click **Generate Hillshade**
5. Wait for processing to finish
6. The hillshade viewer opens automatically
7. Switch styles, zoom/pan, and export when ready

---

## Coordinate Input

Recommended format (decimal degrees):

```
37.143921, -93.292299
```

### Paste Coordinates

The app accepts common coordinate formats such as:

```
38.627003, -90.199402
38.627003 -90.199402
-90.199402,38.627003,0
```

Tip: Longitude is negative for most locations in the USA.

---

## Viewer Controls

The viewer's **LiDAR Dataset** panel displays the exact USGS work-unit name,
collection start/end dates, quality level, source DEM resolution, publication
date, horizontal and vertical CRS, and geoid. **LiDAR Points & Output** retains
the measured point spacing and generated DEM resolution.

| Control | Action |
|---------|--------|
| Left-click drag | Pan |
| Mouse wheel | Smooth zoom at the cursor (capped at 1000%) |
| Zoom +/- buttons | Zoom centered on canvas |
| Fit to Window | Reset zoom to show full image |

### Hillshade Styles

- **Classic** — Standard hillshade (z=1.0, alt=45°, az=315°)
- **Archaeology Presets** — 5 presets with low sun angles and multidirectional lighting
- **Custom** — Set your own z-factor, altitude, azimuth, and multidirectional options

Click **Apply Style** to regenerate (reuses existing DEM, no re-download).

The viewer renders only the visible portion of the hillshade while navigating,
then performs a sharper redraw after motion stops. Export resolution is not
reduced.

### Export

- **Export GeoTIFF** — Save georeferenced hillshade for GIS software
- **Export KMZ** — Save for Google Earth with optional description

---

## Advanced Settings

Accessed via the **Advanced Settings** button on the main screen:

- **Custom Binary Paths** — Override bundled PDAL/GDAL with your own binaries
- **Custom Terrain Settings** — Control TIN buffer and edge limits, IDW fallback, fill distance, smoothing, and deterministic mode
- **Log Window** — Real-time processing log for troubleshooting

---

## Building from Source

This section explains how to build LiDAR Hillshade Explorer as a **standalone application** with all binaries and libraries bundled.

### Prerequisites

#### macOS

1. **Python 3.9+** — Install via Homebrew or python.org
2. **Homebrew** — Package manager for macOS
3. **PDAL and GDAL** — Geospatial processing tools

```bash
# Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install required binaries
brew install pdal gdal proj

# Verify installation
which pdal gdaldem
```

#### Windows

1. **Miniconda or Anaconda** — Package manager
2. **PDAL and GDAL** — Installed via conda

```powershell
# Create conda environment with PDAL and GDAL
conda create -n lidar -c conda-forge python pdal gdal proj
conda activate lidar

# Verify installation
where pdal
where gdaldem
```

### Build Steps

#### Step 1: Clone and Set Up Environment

**macOS:**
```bash
git clone https://github.com/yourusername/LiDARHillshadeExplorer.git
cd LiDARHillshadeExplorer

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install pyinstaller
```

**Windows (with conda env active):**
```powershell
git clone https://github.com/yourusername/LiDARHillshadeExplorer.git
cd LiDARHillshadeExplorer

conda activate lidar
pip install -r requirements.txt
pip install pyinstaller pefile
```

#### Step 2: Bundle Binaries and Libraries

**macOS:**
```bash
python3 bundle_dependencies.py
```

**Windows:**
```powershell
python bundle_dependencies_windows.py
```

This copies `pdal`/`gdaldem` binaries, traces their DLL/dylib dependencies, and copies GDAL/PROJ data files into `bundle_bins/{platform}/`.

#### Step 3: Build the Application

```bash
pyinstaller lidar_explorer.spec --noconfirm
```

The spec file automatically:
- Detects your platform (macOS ARM64/x86_64, Windows x86_64)
- Bundles all binaries, libraries, and data files
- **Windows**: Builds a single standalone `.exe` (onefile mode, ~345 MB)
- **macOS**: Builds a standalone `.app` bundle (current ARM64 build: ~488 MB)

#### Step 4: Test the Build

**macOS:**
```bash
open "dist/LiDAR Hillshade Explorer.app"
```

**Windows:**
```powershell
dist\LiDARHillshadeExplorer.exe
```

### Build Output

| Platform | Output | Size |
|----------|--------|------|
| **macOS ARM64** | `dist/LiDAR Hillshade Explorer.app` | ~488 MB |
| **Windows** | `dist/LiDARHillshadeExplorer.exe` | ~345 MB (single file) |

### Bundled Components

| Component | Purpose | Source |
|-----------|---------|--------|
| `pdal` / `pdal.exe` | LiDAR point cloud processing | Homebrew (macOS) / conda (Windows) |
| `gdaldem` / `gdaldem.exe` | DEM and hillshade generation | Homebrew (macOS) / conda (Windows) |
| Dynamic libraries | ~130–190 DLLs/dylibs | Traced from binaries |
| GDAL data | Coordinate reference definitions | Homebrew/conda share directory |
| PROJ data | Projection database | Homebrew/conda share directory |

At runtime, the app looks for binaries in this order:
1. Bundled location (inside the app / extracted temp directory)
2. User config overrides (Advanced Settings)
3. Environment variables (`PDAL_BIN`, `GDALDEM_BIN`)
4. Conda environment (Windows: `CONDA_PREFIX/Library/bin/`)
5. Common installation paths (Homebrew, QGIS, OSGeo4W)
6. System PATH

### Distribution

#### macOS

Release only `dist/LiDAR Hillshade Explorer.app`. The neighboring
`dist/LiDARHillshadeExplorer/` directory is a PyInstaller staging product and is
not needed by users. Preserve the app bundle's permissions and internal links by
distributing it inside a ZIP or DMG.

Create a ZIP:

```bash
ditto -c -k --sequesterRsrc --keepParent \
  "dist/LiDAR Hillshade Explorer.app" \
  "LiDAR-Hillshade-Explorer-3.0-macOS-arm64.zip"
```

Or create a DMG:

```bash
brew install create-dmg

create-dmg \
  --volname "LiDAR Hillshade Explorer" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --app-drop-link 450 185 \
  "LiDAR-Hillshade-Explorer-3.0-macOS-arm64.dmg" \
  "dist/LiDAR Hillshade Explorer.app"
```

#### Windows

The single `.exe` file can be distributed directly — just zip it or share the file. No installer needed.

---

## Troubleshooting Build Issues

### Binary not found errors

- **macOS**: Verify Homebrew installation: `which pdal gdaldem`
- **Windows**: Verify conda installation: `where pdal` (with conda env active)
- Re-run the bundle script to re-collect binaries

### Library loading errors

- **macOS**: Re-run `bundle_dependencies.py`, then rebuild the app
- **Windows**: Re-run `bundle_dependencies_windows.py` with `--all-dlls` flag

### PROJ/GDAL data errors

If coordinate transformations fail:
- Verify data directories exist in `bundle_bins/{platform}/gdal_data/` and `proj_data/`
- On Windows dev mode, ensure the conda environment is activated

---

## Documentation

- [`USER_GUIDE.md`](USER_GUIDE.md) — Detailed usage instructions
- [`BUILD.md`](BUILD.md) — Additional build documentation
- [`BUNDLING_GUIDE.md`](BUNDLING_GUIDE.md) — Bundling details for both platforms

---

## License / Disclaimer

This project is free to use and open source.

LiDAR Hillshade Explorer is provided **as-is**, without warranty of any kind. The author is not responsible for any damage, data loss, or other issues resulting from the use of this software.

---

## Author

Bill Fleming (TechBill)

Donations (optional)  
PayPal: https://www.paypal.com/paypalme/techbill  
Buy Me a Coffee: https://buymeacoffee.com/techbill
