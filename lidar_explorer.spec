# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for LiDAR Hillshade Explorer
# Multi-platform support with automatic platform detection
#
# Windows: builds a single .exe file (--onefile mode)
# macOS: builds a .app bundle (--onedir + BUNDLE)
#
# Uses pre-bundled binaries from bundle_bins/
# Run bundle_dependencies.py (macOS) or bundle_dependencies_windows.py (Windows) first

block_cipher = None

import os
import sys
import platform
from pathlib import Path


def get_platform_dir():
    """
    Detect platform and return the corresponding bundle directory name.

    Returns:
        str: Directory name like 'macos-arm64', 'macos-x86_64', or 'windows-x86_64'
    """
    system = sys.platform
    machine = platform.machine().lower()

    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        else:
            return "macos-x86_64"
    elif system == "win32":
        return "windows-x86_64"
    elif system.startswith("linux"):
        if machine in ("arm64", "aarch64"):
            return "linux-arm64"
        else:
            return "linux-x86_64"
    else:
        raise RuntimeError(f"Unsupported platform: {system}-{machine}")


# Detect current platform
platform_dir = get_platform_dir()
is_windows = sys.platform == "win32"
is_macos = sys.platform == "darwin"

print(f"Building for platform: {platform_dir}")
print(f"Build mode: {'onefile (single EXE)' if is_windows else 'onedir (app bundle)'}")

# Collect platform-specific bundled binaries and libraries
bundle_bins_dir = Path('bundle_bins') / platform_dir
binaries_to_bundle = []
datas_to_bundle = [
    ('assets/icon.ico', 'assets'),
    ('assets/icon.png', 'assets'),
]

if bundle_bins_dir.exists():
    print(f"Found platform-specific bundle directory: {bundle_bins_dir}")

    # Add binaries (pdal, gdaldem / pdal.exe, gdaldem.exe)
    if is_windows:
        for binary in bundle_bins_dir.glob('*.exe'):
            binaries_to_bundle.append((str(binary), 'bin'))
            print(f"  Including binary: {binary.name}")
    else:
        for binary in bundle_bins_dir.glob('*'):
            if binary.is_file() and os.access(binary, os.X_OK) and binary.suffix not in ('.py', '.txt'):
                if binary.name.startswith("pdal_wrench"):
                    continue
                binaries_to_bundle.append((str(binary), 'bin'))
                print(f"  Including binary: {binary.name}")

    # Add all dynamic libraries
    libs_dir = bundle_bins_dir / 'libs'
    if libs_dir.exists():
        if is_macos:
            lib_pattern = '*.dylib'
        elif is_windows:
            lib_pattern = '*.dll'
        else:
            lib_pattern = '*.so'

        for lib in libs_dir.glob(lib_pattern):
            binaries_to_bundle.append((str(lib), 'bin/libs'))

        print(f"  Including {len(list(libs_dir.glob(lib_pattern)))} libraries from libs/")

    # Add GDAL data files
    gdal_data_dir = bundle_bins_dir / 'gdal_data'
    if gdal_data_dir.exists():
        for data_file in gdal_data_dir.rglob('*'):
            if data_file.is_file():
                rel_path = data_file.relative_to(gdal_data_dir)
                dest_dir = str(Path('gdal_data') / rel_path.parent) if rel_path.parent != Path('.') else 'gdal_data'
                datas_to_bundle.append((str(data_file), dest_dir))
        print(f"  Including GDAL data files from gdal_data/")

    # Add PROJ data files
    proj_data_dir = bundle_bins_dir / 'proj_data'
    if proj_data_dir.exists():
        for data_file in proj_data_dir.rglob('*'):
            if data_file.is_file():
                rel_path = data_file.relative_to(proj_data_dir)
                dest_dir = str(Path('proj_data') / rel_path.parent) if rel_path.parent != Path('.') else 'proj_data'
                datas_to_bundle.append((str(data_file), dest_dir))
        print(f"  Including PROJ data files from proj_data/")

else:
    print(f"WARNING: Platform-specific bundle directory not found: {bundle_bins_dir}")
    if is_windows:
        print("Run 'python bundle_dependencies_windows.py' first to collect binaries.")
    else:
        print("Run 'python3 bundle_dependencies.py' first to collect binaries.")

print(f"Total binaries to bundle: {len(binaries_to_bundle)}")
print(f"Total data files to bundle: {len(datas_to_bundle)}")

a = Analysis(
    ['app.py'],
    pathex=['src'],
    binaries=binaries_to_bundle,
    datas=datas_to_bundle,
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'matplotlib.backends.backend_tkagg',
        'matplotlib.figure',
        'PIL._tkinter_finder',
        'rasterio',
        'rasterio._shim',
        'rasterio._env',
        'rasterio._features',
        'rasterio._base',
        'rasterio._err',
        'rasterio._io',
        'rasterio.control',
        'rasterio.crs',
        'rasterio.features',
        'rasterio.sample',
        'rasterio.vrt',
        'numpy',
        'main_gui',
        'hillshade_viewer',
        'processing',
        'lidar_core.aws_operations',
        'lidar_core.dem_generator',
        'lidar_core.hillshade_engine',
        'lidar_core.kmz_export',
        'utils.config',
        'utils.progress',
        'utils.cleanup',
        'utils.binary_paths',
        'utils.subprocess_utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if is_windows:
    # =========================================================================
    # WINDOWS: Single-file EXE (onefile mode)
    # Everything is packed into one .exe that auto-extracts to temp at runtime.
    # pdal.exe, gdaldem.exe, DLLs, GDAL/PROJ data all extracted to sys._MEIPASS
    # =========================================================================
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,        # Include all binaries IN the exe
        a.zipfiles,        # Include all zip files IN the exe
        a.datas,           # Include all data files IN the exe
        [],
        name='LiDARHillshadeExplorer',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,         # Disable UPX for onefile (can cause issues with large bundles)
        console=False,     # No console window (windowed app)
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon='assets/icon.ico',
    )

else:
    # =========================================================================
    # macOS / Linux: One-directory mode (needed for .app bundle on macOS)
    # =========================================================================
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='LiDARHillshadeExplorer',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon='assets/icon.icns' if is_macos else None,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='LiDARHillshadeExplorer'
    )

    # macOS app bundle
    if is_macos:
        app = BUNDLE(
            coll,
            name='LiDAR Hillshade Explorer.app',
            icon='assets/icon.icns',
            bundle_identifier='com.techbill.lidar-hillshade-explorer',
            info_plist={
                'NSPrincipalClass': 'NSApplication',
                'NSHighResolutionCapable': 'True',
                'CFBundleShortVersionString': '3.2.3',
                'CFBundleVersion': '3.2.3',
                'CFBundleDisplayName': 'LiDAR Hillshade Explorer',
            },
        )
