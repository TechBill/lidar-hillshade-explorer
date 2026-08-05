# LiDAR Hillshade Explorer – User Guide (v3.0)

## Overview
**LiDAR Hillshade Explorer** is a lightweight desktop app for exploring terrain using LiDAR data.  
You enter coordinates, click one button, and the app automatically finds available LiDAR online, processes it, generates a hillshade image, and opens an interactive viewer.

This tool is designed for research, visualization, and exploration.

It is **not survey-grade GIS**.

---

## Security Notice (macOS + Windows)

Because LiDAR Hillshade Explorer is distributed as a downloadable app (not from the official Apple App Store or Microsoft Store), your computer may show a security warning the first time you run it. This is normal for many independent and open-source apps.

### macOS (Apple Silicon)

Version 3.0 for macOS is an ARM64 build for Apple Silicon MacBook Air, MacBook
Pro, iMac, Mac mini, Mac Studio, and Mac Pro systems. It runs natively on M1,
M2, M3, M4, and newer Apple Silicon chips. Intel Macs require a separately
built x86_64 version.

When opening LiDAR Hillshade Explorer for the first time, macOS may block it and show a message like:

"LiDAR Hillshade Explorer can't be opened because Apple cannot check it for malicious software."

If that happens:

1. Try opening the app once (so macOS registers it)
2. Go to **System Settings**
3. Click **Privacy & Security**
4. Scroll down to the **Security** section
5. You should see a message about LiDAR Hillshade Explorer being blocked → click **Open Anyway**
6. Confirm again if prompted

After you approve it once, the app should open normally in the future.

---

### Windows

When running LiDAR Hillshade Explorer for the first time, Windows may show a warning such as:

"Windows protected your PC"

If that happens:

1. Click **More info**
2. Click **Run anyway**

This is common for smaller apps that are not digitally signed.

**Note:** On Windows, the first launch may take a few seconds as the app extracts its bundled files to a temporary directory. Subsequent launches will be faster.

---

## License / Disclaimer
LiDAR Hillshade Explorer is free to use and open source.

This project was originally developed for personal terrain research and exploration, and it is being shared freely for anyone who would like to try it.

LiDAR Hillshade Explorer is provided **as-is**, without warranty of any kind. The author is not responsible for any damage, data loss, or other issues resulting from the use of this software.

---

# Quick Start Workflow

1. **Launch the app**
2. **Enter your coordinates** (latitude and longitude)
3. **Select area size** (Small, Medium, or Large)
4. Click **Generate Hillshade**
5. Wait while the app:
   - downloads LiDAR data automatically
   - processes the elevation surface
   - generates a hillshade
6. The app opens the hillshade in the **interactive viewer**
7. **Explore** with pan, zoom, and different hillshade styles
8. **Export** to GeoTIFF or KMZ when finished

---

# Main Features

## 1) One-Click Hillshade Generation
The goal of this app is simplicity:
- no GIS setup
- no manual downloading
- no special file formats needed

You enter coordinates, and the app handles the rest.

---

## 2) Paste Coordinates Feature
In the coordinate entry fields, you can paste coordinates copied from other tools.

It accepts common formats such as:

**Decimal degrees (comma)**
```
38.627003, -90.199402
```

**Decimal degrees (space)**
```
38.627003 -90.199402
```

**Google Earth / KML-style order**
```
-90.199402,38.627003,0
```

Tip:
- If your pasted text contains multiple numbers, the app will attempt to detect the correct latitude and longitude automatically.

---

## 3) Area Size Selection

Choose from three area sizes:

| Size | Coverage | Best for |
|------|----------|----------|
| **Small** (0.25 sq mi) | ~160 acres | Focused site investigation |
| **Medium** (0.5 sq mi) | ~320 acres | Moderate area exploration |
| **Large** (1.0 sq mi) | ~640 acres | Wide area overview |

Smaller areas process faster and produce higher detail. Start with Small and increase if you need more coverage.

---

## 4) Terrain Style

Choose how the app handles areas without ground-classified LiDAR returns:

- **Continuous Terrain (Recommended)** fills building footprints and small gaps for a continuous bare-earth hillshade.
- **Preserve Large Gaps** avoids interpolating across larger missing areas, which may remain black.
- **Custom** uses the detailed TIN and fill values from Advanced Settings.

---

## 5) Smart Dataset Selection

When **Smart Selection** is enabled (default), the app automatically:
- Finds all available LiDAR datasets for your location
- Uses cached official USGS acquisition metadata to try the newest collection first
- Uses LiDAR quality level as a tie-breaker (QL1 is higher quality than QL2)
- Falls back to older datasets if the newest has insufficient data coverage

When disabled, you can manually choose which dataset to use from a list.

---

# Coordinate Tips

## Recommended Format
The best format to use is:

- Latitude first  
- Longitude second  
- Decimal degrees  

Example:
```
37.143921, -93.292299
```

## Common Mistakes
- Longitude should be negative in most of the USA
- Latitude should always be between **-90 and 90**
- Longitude should always be between **-180 and 180**

---

# Viewer Controls

Once the hillshade viewer opens, you can explore the terrain using several controls.

The **LiDAR Dataset** section shows authoritative USGS metadata for the selected
work unit: collection dates, quality level, source DEM resolution, publication
date, horizontal and vertical CRS, and geoid. Point spacing and generated DEM
resolution remain below it as processing-output statistics.

## Navigation

| Control | Action |
|---------|--------|
| **Left-click drag** | Pan (move around the image) |
| **Mouse wheel** | Zoom in/out |
| **Zoom + button** | Zoom in (centered on canvas) |
| **Zoom - button** | Zoom out (centered on canvas) |
| **Fit to Window** | Reset zoom to show entire hillshade |

**Zoom limits:** The viewer caps zoom at 1000%. Zooming and panning render only
the visible part of large hillshades, keeping navigation responsive without
reducing exported image resolution.

---

## Hillshade Styles

The viewer lets you switch between different hillshade rendering styles without re-downloading data:

### Classic
Standard hillshade with default parameters:
- Z-factor: 1.0
- Altitude: 45°
- Azimuth: 315°

### Archaeology Presets
Five presets optimized for archaeological feature detection:

| Preset | Z-factor | Altitude | Type |
|--------|----------|----------|------|
| Soft (Multi) | 1.0 | 25° | Multidirectional |
| Very Low Sun (Multi) | 1.0 | 20° | Multidirectional |
| Higher Sun (Multi) | 1.0 | 35° | Multidirectional |
| Strong Relief (Multi) | 1.5 | 30° | Multidirectional |
| Directional Check – NW Low Sun | 1.0 | 30° | Directional (315°) |

### Custom
Set your own parameters:
- **Z-factor**: Vertical exaggeration (higher = more dramatic terrain)
- **Altitude**: Sun angle above horizon (0–90°, lower = longer shadows)
- **Azimuth**: Sun direction (0–360°, 315° = northwest)
- **Multidirectional**: Illuminate from multiple directions at once

After selecting a style, click **Apply Style** to regenerate the hillshade. The app reuses the existing DEM data — no re-download needed.

---

## Exporting

### Export GeoTIFF
Save the hillshade as a georeferenced GeoTIFF file. You can choose which hillshade style(s) to export. GeoTIFF files can be opened in GIS software like QGIS.

### Export KMZ
Export one or more hillshade styles as a KMZ file for viewing in Google Earth. The export dialog lets you:
- Select which hillshade files to include
- Add an optional description
- Choose where to save the KMZ

---

# Advanced Settings

Access advanced settings via the **Advanced Settings** button on the main screen.

## Custom Binary Paths

If you need to use a different version of PDAL or GDAL (for troubleshooting or testing):

1. Check the **Override** checkbox for the binary you want to change
2. Enter the full path to the binary or click **Browse** to find it
3. Click **Save**

When override is enabled, the app uses your custom binary instead of the bundled version.

## Custom Terrain Settings

These values apply when the main-screen Terrain Style is set to **Custom**:

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| **IDW Window** | 12 | 3–32 | Inverse Distance Weighting window size. Higher values produce smoother surfaces with fewer gaps, but may over-smooth fine details. |
| **Fill Max Dist** | 16 | 3–64 | Maximum distance (in pixels) to search when filling gaps in the DEM. |
| **Fill Smoothing** | 4 | 0–10 | Number of smoothing passes applied after gap-filling. |
| **Deterministic** | Off | On/Off | When enabled, forces single-threaded processing for consistent results across runs (slower). |

Click **Reset to Defaults** to restore the original values.

## Log Window

Enable **Show Log Window** to open a real-time log of processing details. This is useful for:
- Troubleshooting failed downloads
- Checking point spacing and DEM resolution
- Verifying which dataset was selected
- Diagnosing PDAL/GDAL errors

---

# Output Files and Saved Data

The app creates temporary working files during processing:

- **Downloaded LiDAR** (LAZ format) - raw point cloud data
- **DEM** (GeoTIFF) - processed elevation surface
- **Hillshade** (GeoTIFF) - rendered terrain visualization

These files are automatically cleaned up when you close the viewer and return to the main screen. **Export your results before closing the viewer** if you want to keep them.

---

# Best Practices for Better Results

## Choose good coordinates
For best results:
- use coordinates near the center of the area you want to view
- use stable locations like:
  - ridge lines
  - creek junctions
  - road crossings
  - hilltops

## Try multiple nearby points
If the hillshade looks "too flat" or "not detailed enough," try:
- moving 0.001–0.010 degrees away
- testing a nearby ridge or valley

## Try different styles
The archaeology presets with low sun angles (20–30°) and multidirectional lighting are excellent for revealing subtle terrain features that are invisible in the classic hillshade.

---

# Troubleshooting

## The app opens but nothing happens
Possible causes:
- internet connection is offline
- LiDAR source is temporarily unavailable
- the coordinate area has no LiDAR coverage

Try:
- a different coordinate location
- running again after a few minutes

---

## The app runs slowly
Processing LiDAR can be heavy depending on:
- how much data is downloaded
- your CPU speed
- your available memory

Tip:
If you want faster performance, try smaller areas first (Small = 0.25 sq mi).

---

## No LiDAR Coverage
The app uses USGS/AWS LiDAR datasets which primarily cover the United States. If you get "No data available," try a different location within the US.

Enable **Smart Selection** to automatically try older datasets that may have better coverage.

---

## macOS Standalone Release

The version 3.0 `.app` includes PDAL, GDAL, PROJ, and their required libraries.
Users do not need Homebrew, Python, QGIS, PDAL, or GDAL. Internet access is still
required to locate and download USGS LiDAR data and periodically refresh USGS
metadata.

---

# Credits and Support
LiDAR Hillshade Explorer is provided as-is for personal and research use.

Generated by LiDAR Hillshade Explorer  
Author: Bill Fleming (TechBill)

Donations (optional)  
PayPal: https://www.paypal.com/paypalme/techbill  
Buy Me a Coffee: https://buymeacoffee.com/techbill
