# LiDAR Hillshade Explorer 6.5

LiDAR Hillshade Explorer is a lightweight desktop app for exploring terrain using LiDAR elevation data.  
Enter coordinates, click one button, and the app downloads available LiDAR, processes it, generates a hillshade image, and opens an interactive viewer.

This tool is designed for research and visualization. It is **not survey-grade GIS**.

---

## What's New in 6.5

- Manual catalog discovery now runs outside the Tk interface thread, keeping
  the app responsive while providers are searched.
- The manual-selection result is reused by processing instead of performing
  the same catalog search twice.
- USGS, AWS EPT, NOAA/USIEI, and OpenTopography searches run independently;
  one unavailable provider no longer prevents results from the others.
- Added provider-specific status reporting, bounded HTTP/GDAL requests, and a
  short metadata cache for NOAA/USIEI and OpenTopography results.
- Added opt-in live-network integration checks separately from fast unit tests.
- The Hillshade Viewer control sidebar now supports mouse-wheel scrolling when
  its controls overflow, while the wheel continues to zoom over the image.
- **Export GeoTIFF** now writes a lossless Cloud Optimized GeoTIFF with 512 x
  512 tiles, DEFLATE compression, predictor 2, and internal average-resampled
  overviews while preserving the original grayscale pixels and georeferencing.

### Catalog additions carried forward from 6.3.5

- Added NOAA Coastal LiDAR discovery through official USIEI footprints and
  direct public NOAA EPT delivery. Normal terrain mode admits only completed,
  public topographic records with class-2 ground data and complete AOI coverage.
- Added USIEI as a national discovery and metadata fallback. Supported USGS
  EPT links can supply data when the normal availability index misses them.
- USIEI project records enrich matching USGS work units without replacing
  authoritative WESM metadata or showing the same survey twice.
- Added NOAA/USIEI provider labels, citation, license, quality, collection,
  datum, point-spacing, and source-record metadata to selection and results.

## What's New in 6.3.2

- Split the former 1,949-line LiDAR operations module into focused USGS,
  OpenTopography, EPT, point-cloud delivery, raster-DEM delivery, and ranking
  modules. The original import path remains as a compatibility facade.
- Preserved the existing catalog ranking and delivery behavior while making
  each provider easier to test and improve independently.
- Made the Hillshade Viewer open tall enough to show its complete sidebar when
  the display permits, while retaining sidebar scrolling on smaller screens.
- The opaque NoData preference now uses the same style-matched neutral fill in
  the viewer and KMZ export, while preserving GeoTIFF NoData metadata.

---

## What's New in 6.3.1

- Added automatic discovery of public high-resolution lidar DTMs from the
  OpenTopography catalog alongside USGS WESM/TNM and AWS EPT results.
- OpenTopography catalog and STAC discovery requires no API key. Public
  bare-earth Cloud Optimized GeoTIFF tiles are read directly and cropped to
  the requested area without submitting a processing job.
- Best Detail now compares native source resolution before validation status,
  allowing a public 0.25 m research DTM to outrank an overlapping 1 m USGS
  product while still considering coverage, validation, quality, and date.
- Dataset metadata now identifies OpenTopography delivery and preserves its
  citation, license, coordinate system, survey date, and publication date.
- Renamed the viewer's preset group to **Terrain Presets** and removed the
  former domain-specific wording from every preset name.
- Added **Combined Detail** to the preset list using z=2.0, altitude=25°,
  azimuth=315°, and GDAL Combined lighting.
- Increased the main window's default height and made the complete form
  vertically scrollable, keeping Generate and Advanced Settings reachable on
  smaller laptop displays and after resizing the window.

---

## What's New in 3.5.2

- Added GDAL Combined shading to the Custom hillshade controls. Combined
  blends slope and oblique shading for a higher-contrast terrain view.
- Combined and Multidirectional are mutually exclusive. Selecting either
  option automatically clears the other; Azimuth remains available for
  Combined shading and is disabled for Multidirectional shading.
- Custom output filenames now identify Combined shading separately so cached
  Directional, Multidirectional, and Combined results cannot be confused.
- Fixed the Custom Z-factor, Altitude, and Azimuth fields sometimes refusing
  keyboard edits in the packaged macOS app. Focusing a field now selects its
  current value so it can be replaced or cleared immediately.

## What's New in 3.5

- Dataset discovery is now WESM-first. The app joins the daily official WESM
  work-unit catalog to spatial results from the official TNM product API, so
  work units no longer disappear merely because HoBu has not converted them
  to EPT.
- **Best Detail** is the default ranking and prioritizes complete coverage,
  native source resolution, validation status, quality level, collection
  date, and update date. **Newest Survey** is available when recency matters
  more than resolution.
- Delivery now follows EPT → official source DEM → official LAZ. Source DEMs
  are cropped at their original delivered resolution and used directly for
  hillshade generation. This includes the 0.25 m `SD_BlackHills_1_D23` data.
- Added an Advanced Settings option to fill KMZ NoData areas with opaque
  neutral gray. This prevents satellite imagery from showing through water
  and other areas without ground returns while leaving DEMs and GeoTIFFs
  unchanged. The setting is off by default.

## What's New in 3.2.5

- Fixed Smart Selection treating a dataset's rectangular envelope as actual
  LiDAR coverage. The app now intersects the requested area with the real
  GeoJSON coverage polygons, avoiding empty downloads from irregular project
  footprints and gaps between coverage islands.
- Smart Selection now tries every genuinely intersecting dataset instead of
  stopping after five, so an older valid collection cannot be skipped merely
  because newer candidates have insufficient point coverage.

## What's New in 3.2.4

- Restored authoritative LiDAR work-unit information after the former USGS
  metadata URL began returning a truncated export. The app now uses the
  official USGS S3 source and rejects incomplete metadata before caching it.
- The GeoTIFF selection dialog now labels its action **Export** and closes
  after a successful export, matching the KMZ workflow. Cancelling the Save
  dialog or encountering an error keeps the selection dialog open.

## What's New in 3.2.3

- Fixed the Export GeoTIFF/KMZ file-selection lists sometimes showing what
  looked like duplicate entries (two "Classic.tif", two of the same
  terrain preset, etc). These lists were including every hillshade left
  in the shared output folder, including ones from a previously viewed
  location if you generated more than one hillshade without returning to
  the main menu in between — and the checkbox labels drop the location so
  two different files could display identically. Both dialogs now only
  list files belonging to the location currently open in the viewer.

## What's New in 3.2.2

- Fixed the actual remaining cause of KMZ export failing with
  `CRSError: ... Cannot find proj.db` (including on the machine the app is
  built on). Root-caused with an in-app self-test: `rasterio`'s CRS/GDAL
  layer — the one KMZ export's coordinate transform depends on — does not
  reliably auto-locate its bundled PROJ database once PyInstaller relocates
  everything inside the `.app`, even though `pyproj`'s own transformer finds
  its copy fine on its own. The app now points `GDAL_DATA`/`PROJ_LIB` at the
  data already bundled alongside the app's PDAL/GDAL binaries so that lookup
  always succeeds, verified against a real exported hillshade end to end.

## What's New in 3.2.1

- Fixed a second cause of KMZ export failing on some Macs: the LiDAR
  download/clip step no longer redirects `pyproj` at a system Homebrew PROJ
  install when one happens to be present. That redirect stuck for the rest
  of the run and could leave `rasterio`'s coordinate transform (used only by
  KMZ export) working against a mismatched PROJ database on machines whose
  Homebrew PROJ version didn't match what the app was built against.
- KMZ export failures that still occur now report the actual per-file reason
  (not just "No valid TIF files could be processed") so a failure can be
  diagnosed from the error dialog alone.

## What's New in 3.2

- Fixed KMZ export failing on some Macs (e.g. machines without Homebrew's GDAL/PROJ installed). The packaged app no longer overrides GDAL/PROJ library resolution for the whole process — `rasterio`/`pyproj` now use their own bundled, version-matched data as intended, which is what the KMZ export's coordinate transform depends on.
- Fixed the Advanced Settings dialog so the Save/Close/Cancel buttons can no longer be pushed off-screen on shorter displays: the button row is now pinned in place and the settings above it scroll independently.
- KMZ export failures now always show a real error message instead of a blank dialog.

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
- View LiDAR dataset metadata: acquisition dates, quality level,
  source DEM resolution, publication date, horizontal/vertical CRS, and geoid
- Viewport-based rendering for smooth zooming and panning on large hillshades
- 6 terrain-visualization presets with directional, multidirectional, and
  Combined lighting
- Custom hillshade parameters (z-factor, altitude, azimuth, multidirectional,
  and combined shading)
- Export to GeoTIFF or KMZ (Google Earth)
- Combined USGS, OpenTopography, NOAA, and USIEI selection with Best Detail and Newest Survey ranking
- Public OpenTopography high-resolution DTM discovery and direct COG cropping
- Public NOAA Coastal topographic EPT discovery and streaming
- USIEI national discovery, metadata enrichment, and supported EPT fallback
- AWS EPT streaming with official USGS source-DEM and LAZ fallbacks
- Official USGS WESM metadata cached locally and refreshed every seven days
- Main-screen terrain styles for continuous output or preserving large gaps
- Custom TIN/DEM settings (buffer, edge factor, fill distance, smoothing)
- Processing log window for troubleshooting
- **Standalone app** — no dependencies to install (all binaries bundled)

---

## Security Notice (macOS + Windows)

Because this app is distributed as a downloadable app (not from the official Apple App Store or Microsoft Store), your computer may show a security warning the first time you run it. This is normal for many independent and open-source apps.

### macOS (Apple Silicon)

The current 6.5 macOS release is built for Apple Silicon and runs natively on
M1, M2, M3, M4, and newer ARM64 Macs. It does not require Rosetta. An Intel Mac
requires a separately built x86_64 release.

This free, open-source hobby release is **ad-hoc signed but not Apple-notarized**.
Apple requires a paid Developer Program membership for Developer ID signing and
notarization, so macOS may show **Move to Trash** or a message like:

"LiDAR Hillshade Explorer can't be opened because Apple cannot check it for malicious software."

Do this:

1. If the warning offers **Move to Trash**, close the warning without moving the app.
2. Open **System Settings → Privacy & Security**.
3. Scroll down to the **Security** section.
4. Find the message that LiDAR Hillshade Explorer was blocked.
5. Click **Open Anyway**, authenticate if requested, and confirm **Open**.

After you approve it once, the app should open normally in the future.
Only override this protection for a copy obtained from the project release you
trust. The warning is expected for this unnotarized build, but a warning by
itself is not proof that an arbitrary download is safe.

### Install the macOS 6.5 Release

1. Extract `LiDAR-Hillshade-6.5-Mac-ARM64.zip`.
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

The viewer's **LiDAR Dataset** panel displays the selected dataset/work-unit name,
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
- **Terrain Presets** — 6 presets with low-sun, multidirectional, directional,
  and Combined lighting
- **Custom** — Set your own z-factor, altitude, azimuth, multidirectional, and
  combined shading options. Multidirectional and Combined cannot be enabled
  together.

Click **Apply Style** to regenerate (reuses existing DEM, no re-download).

The viewer renders only the visible portion of the hillshade while navigating,
then performs a sharper redraw after motion stops. Export resolution is not
reduced.

### Export

- **Export GeoTIFF** — Save a lossless Cloud Optimized GeoTIFF (COG) for GIS
  software, with internal tiling and overviews for efficient viewing
- **Export KMZ** — Save for Google Earth with optional description

---

## Advanced Settings

Accessed via the **Advanced Settings** button on the main screen:

- **Custom Binary Paths** — Override bundled PDAL/GDAL with your own binaries
- **Custom Terrain Settings** — Control TIN buffer and edge limits, IDW fallback, fill distance, smoothing, and deterministic mode
- **Export Settings** — Optionally render Viewer and KMZ NoData/water gaps as
  the same opaque neutral gray without changing the DEM or GeoTIFF
- **Log Window** — Real-time processing log for troubleshooting

---

## Building from Source

This section explains how to build LiDAR Hillshade Explorer as a **standalone application** with all binaries and libraries bundled.

### GitHub Repository and Cross-Platform Builds

The GitHub repository intentionally contains source code, documentation, icons,
tests, and build scripts only. Generated applications, release archives, LiDAR
outputs, virtual environments, and platform-specific PDAL/GDAL binaries are
excluded by `.gitignore`.

Each release must be assembled on its target platform because native binaries
cannot be shared between architectures:

| Target | Bundle directory | Prepare dependencies |
|--------|------------------|----------------------|
| Apple Silicon Mac | `bundle_bins/macos-arm64/` | `python3 bundle_dependencies.py` on an ARM64 Mac |
| Intel Mac | `bundle_bins/macos-x86_64/` | `python3 bundle_dependencies.py` on an Intel Mac |
| Windows x86_64 | `bundle_bins/windows-x86_64/` | `python bundle_dependencies_windows.py` on Windows |

Only `.gitkeep` placeholders from these directories are committed. After
cloning, run the appropriate dependency collector and PyInstaller build on the
target operating system.

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
venv/bin/python3 -m PyInstaller lidar_explorer.spec --noconfirm
```

The spec file automatically:
- Detects your platform (macOS ARM64/x86_64, Windows x86_64)
- Bundles all binaries, libraries, and data files
- **Windows**: Builds a single standalone `.exe` (onefile mode, ~345 MB)
- **macOS**: Builds a standalone `.app` bundle (current ARM64 build: ~488 MB)

#### Step 4: Test the Build

Run the fast offline suite:

```bash
venv/bin/python3 -m unittest discover -s tests -v
```

Optionally verify the current public catalog services over the network:

```bash
LHE_RUN_NETWORK_TESTS=1 venv/bin/python3 -m unittest discover -s tests/integration -v
```

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
  "LiDAR-Hillshade-6.5-Mac-ARM64.zip"
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
  "LiDAR-Hillshade-Explorer-6.5-macOS-arm64.dmg" \
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
