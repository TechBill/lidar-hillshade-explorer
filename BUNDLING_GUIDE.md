# Bundling Guide for LiDAR Hillshade Explorer 3.2.3

## Summary

Your app is configured to bundle all required binaries and libraries into a standalone distribution for both macOS and Windows.

### What We've Done

1. **Collected all binaries**:
   - `pdal` / `pdal.exe` (from Homebrew on macOS, conda on Windows)
   - `gdaldem` / `gdaldem.exe` (from Homebrew on macOS, conda on Windows)

2. **Bundled dynamic libraries**:
   - **macOS**: 187 `.dylib` files (~165 MB) with fixed library paths
   - **Windows**: ~130 `.dll` files (~205 MB) traced from conda environment

3. **Bundled GDAL/PROJ data** (Windows):
   - `gdal_data/` - 93 GDAL data files (~2.2 MB)
   - `proj_data/` - 16 PROJ data files (~9.4 MB)

4. **Updated code to check bundled binaries first**:
   - Created [`src/utils/binary_paths.py`](src/utils/binary_paths.py) - Helper to find bundled binaries (cross-platform)
   - Updated [`src/lidar_core/aws_operations.py`](src/lidar_core/aws_operations.py) - pdal finder with conda paths
   - Updated [`src/lidar_core/dem_generator.py`](src/lidar_core/dem_generator.py) - PDAL DEM pipeline
   - Updated [`src/lidar_core/hillshade_engine.py`](src/lidar_core/hillshade_engine.py) - gdaldem finder with conda paths

5. **Updated PyInstaller spec** ([`lidar_explorer.spec`](lidar_explorer.spec))
   - Automatically detects platform and includes appropriate binaries/libraries
   - Includes GDAL/PROJ data files on Windows

## Directory Structure

### macOS
```
bundle_bins/macos-arm64/  (or macos-x86_64/)
├── pdal                          # ~269 KB
├── gdaldem                       # ~236 KB
└── libs/                         # ~187 dynamic libraries
    ├── libpdalcpp.19.dylib
    ├── libgdal.38.dylib
    ├── libproj.25.dylib
    └── ... (184 more)
```

### Windows
```
bundle_bins/windows-x86_64/
├── pdal.exe                      # ~397 KB
├── gdaldem.exe                   # ~216 KB
├── libs/                         # ~130 DLLs
│   ├── pdalcpp.dll
│   ├── gdal.dll
│   ├── proj_9.dll
│   ├── libpdal_plugin_*.dll      # PDAL plugins
│   └── ... (120+ more DLLs)
├── gdal_data/                    # ~2.2 MB
│   ├── gdalvrt.xsd
│   ├── gcs.csv
│   └── ... (93 files)
└── proj_data/                    # ~9.4 MB
    ├── proj.db
    ├── proj.ini
    └── ... (16 files/dirs)
```

## Building

### macOS

#### Prerequisites
```bash
brew install pdal gdal proj
pip install -r requirements.txt
pip install pyinstaller
```

#### Bundle binaries
```bash
python3 bundle_dependencies.py
```

#### Build the .app
```bash
pyinstaller lidar_explorer.spec
```

Output: `dist/LiDAR Hillshade Explorer.app`

### Windows

#### Prerequisites
```powershell
# Create and activate a conda environment with PDAL and GDAL
conda create -n lidar -c conda-forge python pdal gdal proj
conda activate lidar

# Install Python dependencies
pip install -r requirements.txt
pip install pyinstaller pefile
```

#### Bundle binaries
```powershell
# From the project root, with conda 'lidar' env active:
python bundle_dependencies_windows.py

# Or specify the conda env path explicitly:
python bundle_dependencies_windows.py --conda-env C:\Users\youruser\.conda\envs\lidar
```

This will:
- Copy `pdal.exe` and `gdaldem.exe` from the conda environment
- Recursively trace all DLL dependencies using PE import analysis
- Copy only required DLLs (skipping system DLLs and large unnecessary ones like MKL)
- Copy PDAL plugins
- Copy GDAL and PROJ data files
- Place everything in `bundle_bins/windows-x86_64/`

If dependency tracing misses DLLs, use the `--all-dlls` flag to copy all non-system DLLs:
```powershell
python bundle_dependencies_windows.py --all-dlls
```

#### Build the .exe
```powershell
pyinstaller lidar_explorer.spec --noconfirm
```

Output: `dist\LiDARHillshadeExplorer\LiDARHillshadeExplorer.exe`

## Testing

### macOS
```bash
open "dist/LiDAR Hillshade Explorer.app"
# Or from command line (to see console output)
"dist/LiDAR Hillshade Explorer.app/Contents/MacOS/LiDARHillshadeExplorer"
```

### Windows
```powershell
dist\LiDARHillshadeExplorer\LiDARHillshadeExplorer.exe
```

The app should run completely standalone without requiring:
- Conda installations
- QGIS installations
- Any system binaries except standard OS libraries

## Distribution

### macOS
Release only `dist/LiDAR Hillshade Explorer.app`. The neighboring
`dist/LiDARHillshadeExplorer/` directory is PyInstaller staging output and is
not needed by users.

The `.app` bundle can be distributed as:
1. **DMG image** (recommended)
2. **ZIP archive**

```bash
ditto -c -k --sequesterRsrc --keepParent \
  "dist/LiDAR Hillshade Explorer.app" \
  "LiDAR-Hillshade-3.2.3-Mac-ARM64.zip"
```

### Windows
The `LiDARHillshadeExplorer/` folder can be distributed as:
1. **ZIP archive** (simplest - zip the entire `dist\LiDARHillshadeExplorer\` folder)
2. **Installer** (using NSIS or Inno Setup)

## App Size

### macOS
- **Current Apple Silicon standalone bundle**: ~488 MB

### Windows
- **Python + Dependencies**: ~750 MB (includes numpy MKL, etc.)
- **Binaries + Libraries**: ~205 MB
- **GDAL/PROJ data**: ~12 MB
- **Total**: ~984 MB

## Binary Search Order

The app searches for binaries in this order:
1. **Bundled location** (in `.app`, `_internal/bin/`, or `bundle_bins/`)
2. Config file paths (user overrides)
3. Environment variables (`PDAL_BIN`, `GDALDEM_BIN`)
4. **Conda environment** (Windows: `CONDA_PREFIX/Library/bin/`)
5. Common installation paths (Homebrew, QGIS, OSGeo4W, conda envs)
6. System PATH

This ensures the bundled versions are always used when available.

## Troubleshooting

### Binary not found errors
1. Verify `bundle_bins/` exists and contains the binaries
2. On Windows, check `bundle_bins/windows-x86_64/` has `.exe` files
3. On macOS, check that binaries have execute permissions (`chmod +x`)

### DLL loading errors (Windows)
1. Re-run `bundle_dependencies_windows.py` with `--all-dlls` flag
2. Check that all `.dll` files are in `bundle_bins/windows-x86_64/libs/`
3. Verify GDAL_DATA and PROJ_DATA directories exist

### Library loading errors (macOS)
1. Run `bundle_dependencies.py` again to fix library paths
2. Rebuild with `pyinstaller --noconfirm lidar_explorer.spec`
3. Verify paths with: `otool -L bundle_bins/macos-arm64/pdal`

### PROJ/GDAL data errors
On Windows, if you see coordinate transformation errors:
1. Verify `gdal_data/` and `proj_data/` exist in `bundle_bins/windows-x86_64/`
2. Check that `proj.db` exists in the proj_data directory
3. In development mode, ensure the conda environment is activated

## Files

| File | Description |
|------|-------------|
| `bundle_dependencies.py` | macOS: Collect binaries from Homebrew |
| `bundle_dependencies_windows.py` | Windows: Collect binaries from conda |
| `lidar_explorer.spec` | PyInstaller spec (cross-platform) |
| `src/utils/binary_paths.py` | Runtime binary/library path helper |
| `tools/fix_pdal_libs.py` | Legacy macOS diagnostic/repair helper |
| `BUNDLING_GUIDE.md` | This file |
