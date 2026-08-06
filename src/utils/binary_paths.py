#!/usr/bin/env python3
"""
Helper module to find bundled binaries in standalone builds and .app bundles.

For PyInstaller one-dir builds, binaries are in the _internal/bin/ directory.
For PyInstaller one-file builds, binaries are extracted to sys._MEIPASS/bin/.

For development, they can be in:
  - bundle_bins/{platform}/ (local development with bundled bins)
  - System paths (Homebrew, conda, MacPorts, etc.)
"""

import os
import sys
import platform
from pathlib import Path
from typing import Optional, Dict


def get_platform_dir() -> str:
    """
    Get the platform-specific directory name for bundled binaries.

    Returns:
        Directory name like 'macos-arm64', 'macos-x86_64', or 'windows-x86_64'
    """
    system = sys.platform
    machine = platform.machine().lower()

    if system == "darwin":
        # macOS
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        else:
            return "macos-x86_64"
    elif system == "win32":
        # Windows
        return "windows-x86_64"
    elif system.startswith("linux"):
        # Linux (if needed in future)
        if machine in ("arm64", "aarch64"):
            return "linux-arm64"
        else:
            return "linux-x86_64"
    else:
        # Fallback
        return f"{system}-{machine}"


def get_bundle_bin_dir() -> Optional[Path]:
    """
    Get the bundled bin directory if running from a PyInstaller bundle or with bundled bins.

    For one-dir builds (.app bundle / Windows folder), binaries are in:
      - Contents/Frameworks/bin/ (macOS via COLLECT)
      - _internal/bin/ (Windows via COLLECT)

    For one-file builds, PyInstaller extracts to sys._MEIPASS at runtime.

    Returns:
        Path to bin directory if found, None otherwise
    """
    platform_dir = get_platform_dir()

    # Check if we're running as a PyInstaller bundle
    if getattr(sys, 'frozen', False):
        # Running in PyInstaller bundle
        bundle_dir = Path(sys._MEIPASS)

        # Check various possible locations for bundled binaries
        possible_locations = [
            # One-file mode: binaries in _MEIPASS/bin/
            bundle_dir / "bin",
        ]

        # For macOS .app bundles (one-dir mode)
        if sys.platform == "darwin":
            exe_path = Path(sys.executable)
            possible_locations.extend([
                exe_path.parent / "bin",  # Contents/MacOS/bin
                exe_path.parent.parent / "Frameworks" / "bin",
                exe_path.parent.parent / "Resources" / "bin",
                bundle_dir.parent / "Frameworks" / "bin",
                bundle_dir.parent / "Resources" / "bin",
            ])

        # For Windows one-dir mode
        elif sys.platform == "win32":
            exe_path = Path(sys.executable)
            possible_locations.extend([
                exe_path.parent / "_internal" / "bin",
                exe_path.parent / "bin",
            ])

        for bin_dir in possible_locations:
            if bin_dir.exists():
                return bin_dir

        return None

    # Check for local bundle_bins directory (development)
    if __file__:
        # Get project root (parent of src/utils/)
        project_root = Path(__file__).parent.parent.parent
        platform_bundle = project_root / "bundle_bins" / platform_dir
        if platform_bundle.exists():
            return platform_bundle

    return None


def find_bundled_binary(binary_name: str) -> Optional[str]:
    """
    Find a bundled binary (e.g., pdal, gdaldem).

    On Windows, automatically appends .exe if not present.
    Checks for user-provided overrides first, then falls back to bundled binaries.

    Args:
        binary_name: Name of binary to find (e.g., "pdal", "gdaldem")

    Returns:
        Full path to binary if found, None otherwise
    """
    # Check for user-provided override first
    try:
        from utils.config import load_config
        config = load_config()
        binary_overrides = config.get("binary_overrides", {})

        if binary_name in binary_overrides:
            override = binary_overrides[binary_name]
            if override.get("enabled", False):
                custom_path = override.get("path", "")
                if custom_path and Path(custom_path).exists():
                    print(f"Using custom {binary_name} binary: {custom_path}")
                    return custom_path
    except Exception:
        # If config loading fails, continue with bundled binaries
        pass

    # Fall back to bundled binaries
    bin_dir = get_bundle_bin_dir()

    if bin_dir:
        # Try exact name first
        binary_path = bin_dir / binary_name
        if binary_path.exists() and os.access(binary_path, os.X_OK):
            return str(binary_path)

        # On Windows, try with .exe extension
        if sys.platform == "win32" and not binary_name.endswith(".exe"):
            binary_path = bin_dir / f"{binary_name}.exe"
            if binary_path.exists():
                return str(binary_path)

    return None


def get_bundled_data_dir(data_type: str) -> Optional[Path]:
    """
    Get the path to bundled data directory (gdal_data or proj_data).

    Args:
        data_type: 'gdal' or 'proj'

    Returns:
        Path to data directory if found, None otherwise
    """
    dir_name = f"{data_type}_data"

    if getattr(sys, 'frozen', False):
        bundle_dir = Path(sys._MEIPASS)

        # Check various possible locations
        possible_locations = [
            bundle_dir / dir_name,
            bundle_dir / "share" / data_type,
        ]

        if sys.platform == "win32":
            exe_path = Path(sys.executable)
            possible_locations.extend([
                exe_path.parent / "_internal" / dir_name,
                exe_path.parent / dir_name,
            ])
        elif sys.platform == "darwin":
            exe_path = Path(sys.executable)
            possible_locations.extend([
                exe_path.parent.parent / "Frameworks" / dir_name,
                exe_path.parent.parent / "Resources" / dir_name,
            ])

        for loc in possible_locations:
            if loc.exists() and loc.is_dir():
                return loc

    # Development mode - check bundle_bins
    bin_dir = get_bundle_bin_dir()
    if bin_dir:
        data_dir = bin_dir / dir_name
        if data_dir.exists():
            return data_dir

    return None


def get_bundled_lib_env() -> Dict[str, str]:
    """
    Get environment variables to use bundled libraries.

    For Windows: adds bin/libs/ to PATH so DLLs can be found.
    For macOS: sets DYLD_LIBRARY_PATH to prioritize bundled libraries.
    Also sets GDAL_DATA and PROJ_LIB/PROJ_DATA.

    Returns:
        Dictionary of environment variables to set for subprocess calls
    """
    env = os.environ.copy()

    # Check if running as PyInstaller bundle
    if getattr(sys, 'frozen', False):
        bundle_dir = Path(sys._MEIPASS)

        if sys.platform == "win32":
            # On Windows, add lib paths to PATH so DLLs are found
            lib_paths = []

            primary_libs = bundle_dir / "bin" / "libs"
            if primary_libs.exists():
                lib_paths.append(str(primary_libs))

            bin_dir = bundle_dir / "bin"
            if bin_dir.exists():
                lib_paths.append(str(bin_dir))

            if lib_paths:
                existing_path = env.get("PATH", "")
                new_paths = ";".join(lib_paths)
                env["PATH"] = f"{new_paths};{existing_path}"

            # Set GDAL_DATA from bundled location
            for gdal_candidate in [
                bundle_dir / "gdal_data",
                bundle_dir / "share" / "gdal",
            ]:
                if gdal_candidate.exists():
                    env["GDAL_DATA"] = str(gdal_candidate)
                    break

            # Set PROJ_LIB/PROJ_DATA from bundled location
            for proj_candidate in [
                bundle_dir / "proj_data",
                bundle_dir / "share" / "proj",
            ]:
                if proj_candidate.exists():
                    env["PROJ_LIB"] = str(proj_candidate)
                    env["PROJ_DATA"] = str(proj_candidate)
                    break

        elif sys.platform == "darwin":
            # macOS - use DYLD_LIBRARY_PATH
            lib_paths = []

            primary_libs = bundle_dir / "bin" / "libs"
            if primary_libs.exists():
                lib_paths.append(str(primary_libs))

            if bundle_dir.exists():
                lib_paths.append(str(bundle_dir))

            # Set GDAL_DATA and PROJ_LIB for the pdal/gdaldem subprocess.
            # Prefer the data bundled alongside those binaries (works on any
            # Mac, no Homebrew required) and only fall back to a system
            # Homebrew install for local/dev runs.
            for candidate in [bundle_dir / "gdal_data", Path("/opt/homebrew/share/gdal"), Path("/usr/local/share/gdal")]:
                if candidate.exists():
                    env["GDAL_DATA"] = str(candidate)
                    break

            for candidate in [bundle_dir / "proj_data", Path("/opt/homebrew/share/proj"), Path("/usr/local/share/proj")]:
                if candidate.exists():
                    env["PROJ_LIB"] = str(candidate)
                    env["PROJ_DATA"] = str(candidate)
                    break

            # Set library path - our paths FIRST to override rasterio's bundled libs
            if lib_paths:
                existing_path = env.get("DYLD_LIBRARY_PATH", "")
                our_paths = ":".join(lib_paths)
                if existing_path:
                    env["DYLD_LIBRARY_PATH"] = f"{our_paths}:{existing_path}"
                else:
                    env["DYLD_LIBRARY_PATH"] = our_paths

                existing_fallback = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
                if existing_fallback:
                    env["DYLD_FALLBACK_LIBRARY_PATH"] = f"{our_paths}:{existing_fallback}"
                else:
                    env["DYLD_FALLBACK_LIBRARY_PATH"] = our_paths

        elif sys.platform.startswith("linux"):
            lib_paths = []
            primary_libs = bundle_dir / "bin" / "libs"
            if primary_libs.exists():
                lib_paths.append(str(primary_libs))
            if lib_paths:
                existing_path = env.get("LD_LIBRARY_PATH", "")
                our_paths = ":".join(lib_paths)
                if existing_path:
                    env["LD_LIBRARY_PATH"] = f"{our_paths}:{existing_path}"
                else:
                    env["LD_LIBRARY_PATH"] = our_paths

        return env

    # Development mode - check bundle_bins
    bin_dir = get_bundle_bin_dir()
    if bin_dir:
        libs_dir = bin_dir / "libs"

        if sys.platform == "win32":
            # Add libs directory to PATH for DLL resolution
            paths_to_add = []
            if libs_dir.exists():
                paths_to_add.append(str(libs_dir))
            paths_to_add.append(str(bin_dir))

            existing_path = env.get("PATH", "")
            new_paths = ";".join(paths_to_add)
            env["PATH"] = f"{new_paths};{existing_path}"

            # Set GDAL_DATA from bundled location
            gdal_data = bin_dir / "gdal_data"
            if gdal_data.exists():
                env["GDAL_DATA"] = str(gdal_data)

            # Set PROJ_LIB/PROJ_DATA from bundled location
            proj_data = bin_dir / "proj_data"
            if proj_data.exists():
                env["PROJ_LIB"] = str(proj_data)
                env["PROJ_DATA"] = str(proj_data)

        elif sys.platform == "darwin":
            if libs_dir.exists():
                existing_path = env.get("DYLD_LIBRARY_PATH", "")
                if existing_path:
                    env["DYLD_LIBRARY_PATH"] = f"{libs_dir}:{existing_path}"
                else:
                    env["DYLD_LIBRARY_PATH"] = str(libs_dir)

        elif sys.platform.startswith("linux"):
            if libs_dir.exists():
                existing_path = env.get("LD_LIBRARY_PATH", "")
                if existing_path:
                    env["LD_LIBRARY_PATH"] = f"{libs_dir}:{existing_path}"
                else:
                    env["LD_LIBRARY_PATH"] = str(libs_dir)

    # Fall back to Homebrew/conda paths for GDAL/PROJ data if not set
    if "GDAL_DATA" not in env:
        gdal_data_paths = [
            # Homebrew (macOS)
            "/opt/homebrew/share/gdal",
            "/usr/local/share/gdal",
        ]

        # Add conda env paths on Windows
        if sys.platform == "win32":
            conda_prefix = os.environ.get("CONDA_PREFIX", "")
            if conda_prefix:
                gdal_data_paths.insert(0, str(Path(conda_prefix) / "Library" / "share" / "gdal"))

        for gdal_data in gdal_data_paths:
            if Path(gdal_data).exists():
                env["GDAL_DATA"] = gdal_data
                break

    if "PROJ_LIB" not in env:
        proj_lib_paths = [
            # Homebrew (macOS)
            "/opt/homebrew/share/proj",
            "/usr/local/share/proj",
        ]

        # Add conda env paths on Windows
        if sys.platform == "win32":
            conda_prefix = os.environ.get("CONDA_PREFIX", "")
            if conda_prefix:
                proj_lib_paths.insert(0, str(Path(conda_prefix) / "Library" / "share" / "proj"))

        for proj_lib in proj_lib_paths:
            if Path(proj_lib).exists():
                env["PROJ_LIB"] = proj_lib
                env["PROJ_DATA"] = proj_lib
                break

    return env


def setup_bundled_environment():
    """
    Set up environment variables for bundled app at startup.

    Call this early in app initialization to ensure GDAL/PROJ
    can find their data files and DLLs can be found on Windows.
    """
    if getattr(sys, 'frozen', False):
        bundle_dir = Path(sys._MEIPASS)

        if sys.platform == "win32":
            # Add bundled lib paths to PATH for DLL resolution
            lib_paths = []

            libs_dir = bundle_dir / "bin" / "libs"
            if libs_dir.exists():
                lib_paths.append(str(libs_dir))

            bin_dir = bundle_dir / "bin"
            if bin_dir.exists():
                lib_paths.append(str(bin_dir))

            if lib_paths:
                existing = os.environ.get("PATH", "")
                new_paths = ";".join(lib_paths)
                if new_paths not in existing:
                    os.environ["PATH"] = f"{new_paths};{existing}"

            # Also use os.add_dll_directory on Python 3.8+ for DLL search
            if hasattr(os, "add_dll_directory"):
                for lib_path in lib_paths:
                    try:
                        os.add_dll_directory(lib_path)
                    except OSError:
                        pass

            # Set GDAL_DATA
            if "GDAL_DATA" not in os.environ:
                for candidate in [
                    bundle_dir / "gdal_data",
                    bundle_dir / "share" / "gdal",
                ]:
                    if candidate.exists():
                        os.environ["GDAL_DATA"] = str(candidate)
                        break

            # Set PROJ_LIB and PROJ_DATA
            if "PROJ_LIB" not in os.environ:
                for candidate in [
                    bundle_dir / "proj_data",
                    bundle_dir / "share" / "proj",
                ]:
                    if candidate.exists():
                        os.environ["PROJ_LIB"] = str(candidate)
                        os.environ["PROJ_DATA"] = str(candidate)
                        break

        elif sys.platform == "darwin":
            # Point GDAL_DATA / PROJ_LIB / PROJ_DATA at the data bundled at
            # the app root (bundle_dir / "gdal_data" and "proj_data" - the
            # same ones get_bundled_lib_env() uses for the pdal/gdaldem
            # subprocess).
            #
            # Root-caused with a self-test hook run inside the actual
            # frozen .app (see git history / AGENTS.md for the harness):
            # pyproj's own `Transformer` resolves its bundled proj.db fine
            # on its own, with or without this env var. But rasterio's CRS
            # layer goes through GDAL's *separate* OSR/PROJ context (e.g.
            # `rasterio.crs.CRS.from_epsg`, used inside
            # `rasterio.warp.transform_bounds` - the one call KMZ export
            # needs), and that context's auto-detection does not reliably
            # find pyproj's bundled proj.db once PyInstaller has relocated
            # everything under Contents/Frameworks. Without this env var it
            # fails with "PROJ: internal_proj_create_from_database: Cannot
            # find proj.db" - reproduced even on the machine the app was
            # built on, not just on other users' Macs.
            #
            # Explicitly setting PROJ_DATA/GDAL_DATA to a real, always-
            # bundled proj.db/gdal_data sidesteps that. A newer/older
            # proj.db than the one pyproj was built against is fine in
            # practice - PROJ's on-disk database layout is designed to be
            # read across nearby PROJ versions (verified: a PROJ
            # 9.8-derived proj.db loads and transforms fine under pyproj's
            # bundled 9.5 library). We do NOT set DYLD_LIBRARY_PATH here -
            # only the *data* lookup needs pointing, not which
            # libgdal/libproj actually gets loaded, and forcing that is a
            # separate, unnecessary risk.
            gdal_data = bundle_dir / "gdal_data"
            if gdal_data.exists():
                os.environ["GDAL_DATA"] = str(gdal_data)

            proj_data = bundle_dir / "proj_data"
            if proj_data.exists():
                os.environ["PROJ_LIB"] = str(proj_data)
                os.environ["PROJ_DATA"] = str(proj_data)

    else:
        # Development mode - set up from bundled bins or conda
        bin_dir = get_bundle_bin_dir()
        if bin_dir:
            if sys.platform == "win32":
                libs_dir = bin_dir / "libs"
                paths_to_add = []
                if libs_dir.exists():
                    paths_to_add.append(str(libs_dir))
                paths_to_add.append(str(bin_dir))

                existing = os.environ.get("PATH", "")
                new_paths = ";".join(paths_to_add)
                if new_paths not in existing:
                    os.environ["PATH"] = f"{new_paths};{existing}"

                if hasattr(os, "add_dll_directory"):
                    for p in paths_to_add:
                        try:
                            os.add_dll_directory(p)
                        except OSError:
                            pass

                # Set GDAL_DATA from bundled
                gdal_data = bin_dir / "gdal_data"
                if gdal_data.exists() and "GDAL_DATA" not in os.environ:
                    os.environ["GDAL_DATA"] = str(gdal_data)

                # Set PROJ data from bundled
                proj_data = bin_dir / "proj_data"
                if proj_data.exists() and "PROJ_LIB" not in os.environ:
                    os.environ["PROJ_LIB"] = str(proj_data)
                    os.environ["PROJ_DATA"] = str(proj_data)
