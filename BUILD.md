# Building LiDAR Hillshade Explorer 3.0

This document provides instructions for building platform-specific binaries of LiDAR Hillshade Explorer.

## Platform-Specific Bundles

The application supports multiple platforms with platform-specific binary bundles:

- **macOS ARM64** (Apple Silicon): `bundle_bins/macos-arm64/`
- **macOS x86_64** (Intel): `bundle_bins/macos-x86_64/`
- **Windows x86_64**: `bundle_bins/windows-x86_64/`

The build process automatically detects your platform and uses the appropriate bundle directory.

Only `.gitkeep` placeholders in these directories belong in Git. Executables,
dynamic libraries, GDAL/PROJ data, generated apps, release archives, and test
outputs are intentionally ignored and must be regenerated on the target OS.

## Prerequisites

### All Platforms

1. **Python 3.9+**
2. **Git**
3. **Virtual environment** (recommended)

### macOS

```bash
# Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install dependencies
brew install pdal gdal proj

# Clone repository
git clone https://github.com/yourusername/LiDARHillshadeExplorer.git
cd LiDARHillshadeExplorer

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

### Windows

```powershell
# Install Python 3.9+ from python.org
# Install Git from git-scm.com

# Clone repository
git clone https://github.com/yourusername/LiDARHillshadeExplorer.git
cd LiDARHillshadeExplorer

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt

# Windows binaries (PDAL, GDAL) need to be compiled or obtained from OSGeo4W
```

## Preparing Platform-Specific Binaries

### macOS (ARM64 or x86_64)

The binaries should be placed in the appropriate directory based on your Mac's architecture:

- **Apple Silicon (M1/M2/M3)**: `bundle_bins/macos-arm64/`
- **Intel**: `bundle_bins/macos-x86_64/`

Each directory should contain:
```
bundle_bins/macos-arm64/  (or macos-x86_64/)
├── pdal
├── gdaldem
└── libs/
    ├── libgdal.dylib
    ├── libproj.dylib
    └── ... (other shared libraries)
```

To build the binaries:

```bash
# Create the platform-specific directory
# For Apple Silicon:
mkdir -p bundle_bins/macos-arm64

# For Intel:
mkdir -p bundle_bins/macos-x86_64

# Copy Homebrew binaries
cp $(which pdal) bundle_bins/macos-arm64/
cp $(which gdaldem) bundle_bins/macos-arm64/

# Copy shared libraries
mkdir -p bundle_bins/macos-arm64/libs

# Find and copy GDAL libraries
GDAL_LIB=$(otool -L $(which gdaldem) | grep libgdal | awk '{print $1}')
cp $GDAL_LIB bundle_bins/macos-arm64/libs/

# Find and copy PDAL libraries
for lib in $(otool -L $(which pdal) | grep -E 'libpdal|libgdal|libproj|libtiff|libgeotiff|libzstd|liblzma|libpng|libjpeg' | awk '{print $1}'); do
    if [ -f "$lib" ]; then
        cp "$lib" bundle_bins/macos-arm64/libs/
    fi
done

# Update library paths (optional, for portability)
# See: https://developer.apple.com/library/archive/documentation/DeveloperTools/Conceptual/DynamicLibraries/100-Articles/RunpathDependentLibraries.html
```

### Windows

Windows binaries should be placed in `bundle_bins/windows-x86_64/`:

```
bundle_bins/windows-x86_64/
├── pdal.exe
├── gdaldem.exe
└── libs/
    ├── gdal.dll
    ├── proj.dll
    └── ... (other DLLs)
```

The easiest way to obtain Windows binaries:

1. **OSGeo4W**: Download from [https://trac.osgeo.org/osgeo4w/](https://trac.osgeo.org/osgeo4w/)
2. Install PDAL and GDAL
3. Copy binaries from `C:\OSGeo4W\bin\`
4. Copy required DLLs from `C:\OSGeo4W\bin\` to `libs/`

## Building the Application

### macOS

```bash
# Activate virtual environment
source venv/bin/activate

# Build with PyInstaller
pyinstaller --noconfirm lidar_explorer.spec

# The .app bundle will be in dist/
# dist/LiDAR Hillshade Explorer.app
```

The spec file automatically detects your Mac's architecture and bundles the appropriate binaries.

### Windows

```powershell
# Activate virtual environment
venv\Scripts\activate

# Build with PyInstaller
pyinstaller lidar_explorer.spec

# The executable will be in dist\LiDARHillshadeExplorer\
```

## Testing the Build

### macOS

```bash
# Run the app bundle
open "dist/LiDAR Hillshade Explorer.app"

# Or from command line (to see console output)
"dist/LiDAR Hillshade Explorer.app/Contents/MacOS/LiDARHillshadeExplorer"
```

### Windows

```powershell
# Run the executable
dist\LiDARHillshadeExplorer\LiDARHillshadeExplorer.exe
```

## Distribution

### macOS

The current release artifact is the Apple Silicon ARM64 `.app`. Release only
the `.app`; `dist/LiDARHillshadeExplorer/` is staging output and is not required.

Create a ZIP that preserves the bundle's links and permissions:

```bash
ditto -c -k --sequesterRsrc --keepParent \
  "dist/LiDAR Hillshade Explorer.app" \
  "LiDAR-Hillshade-3.0-Mac-ARM64.zip"
```

The built `.app` can be distributed as:
1. **DMG image** (recommended)
2. **ZIP archive**
3. **Notarized and signed** (for distribution outside Mac App Store)

To create a DMG:
```bash
# Install create-dmg
brew install create-dmg

# Create DMG
create-dmg \
  --volname "LiDAR Hillshade Explorer" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --app-drop-link 425 120 \
  "LiDAR-Hillshade-Explorer-3.0-macOS-arm64.dmg" \
  "dist/LiDAR Hillshade Explorer.app"
```

### Windows

The built executable can be distributed as:
1. **ZIP archive** (simplest)
2. **Installer** (using NSIS or Inno Setup)

## Troubleshooting

### "Binary not found" errors

If the application can't find binaries:
1. Verify binaries are in the correct platform directory
2. Check that binaries have execute permissions (`chmod +x`)
3. Verify the platform detection is correct

### Library loading errors (macOS)

If you see dylib loading errors:
```bash
# Check library dependencies
otool -L bundle_bins/macos-arm64/pdal

# Verify libraries exist
ls -la bundle_bins/macos-arm64/libs/
```

### Missing Python modules

```bash
# Reinstall requirements
pip install -r requirements.txt --force-reinstall
```

## Building for Multiple Platforms

To build for all platforms:

1. **macOS ARM64**: Build on Apple Silicon Mac
2. **macOS x86_64**: Build on Intel Mac (or use cross-compilation)
3. **Windows x86_64**: Build on Windows PC or VM

Each build should:
1. Clone the repo
2. Set up the platform-specific binaries in `bundle_bins/{platform}/`
3. Run PyInstaller with `lidar_explorer.spec`
4. Upload the built application to GitHub Releases

## Contributing

When contributing platform-specific binaries:

1. Fork the repository
2. Add binaries to the appropriate `bundle_bins/{platform}/` directory
3. Test the build on that platform
4. Submit a pull request

**Note**: Binaries themselves are not tracked in git (see `.gitignore`). Document how to obtain/build the binaries for your platform.
