#!/usr/bin/env python3
"""
Hillshade generation for LiDAR Hillshade Explorer.

Generates hillshades from DEMs using GDAL's gdaldem.
Includes classic, 5 archaeology presets, and custom parameters.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from shutil import which
from typing import Dict, Any, Optional

from utils.config import load_config, get_output_dir
from utils.subprocess_utils import get_creation_flags, get_startupinfo


# Standard hillshade parameters
STD_Z = 1.0
STD_ALT = 45.0
STD_AZ = 315.0

# 5 Archaeology presets (reduced from original 12)
ARCH_PRESETS: Dict[str, Dict[str, Any]] = {
    "Archaeology – Soft (Multi)": {
        "z": 1.0,
        "alt": 25.0,
        "multi": True,
    },
    "Archaeology – Very Low Sun (Multi)": {
        "z": 1.0,
        "alt": 20.0,
        "multi": True,
    },
    "Archaeology – Higher Sun (Multi)": {
        "z": 1.0,
        "alt": 35.0,
        "multi": True,
    },
    "Archaeology – Strong Relief (Multi)": {
        "z": 1.5,
        "alt": 30.0,
        "multi": True,
    },
    "Directional Check – NW Low Sun": {
        "z": 1.0,
        "alt": 30.0,
        "az": 315.0,
        "multi": False,
    },
}

LogFunc = Optional[callable]


def _log(log: LogFunc, msg: str):
    """Helper to log message if logger provided."""
    if log:
        log(msg)


def _run_gdaldem(
    cmd: list,
    output_path: Path,
    cancel_check: Optional[callable] = None,
    log: LogFunc = None,
) -> None:
    """
    Run a gdaldem command with cancel support.

    Raises RuntimeError if the process fails, is cancelled, or the output
    file is not created.
    """
    import time
    from utils.binary_paths import get_bundled_lib_env

    env = get_bundled_lib_env()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        creationflags=get_creation_flags(), startupinfo=get_startupinfo()
    )

    while proc.poll() is None:
        if cancel_check and cancel_check():
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise RuntimeError("Cancelled by user")
        time.sleep(0.1)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Hillshade generation failed:\n{stderr or stdout}")
    if not output_path.is_file():
        raise RuntimeError(f"Hillshade file was not created: {output_path}")

    _log(log, f"Hillshade created: {output_path.stat().st_size:,} bytes")


def find_gdaldem(config: dict = None) -> str:
    """
    Find gdaldem executable.

    Checks:
    1. Bundled binary (in .app or bundle_bins/)
    2. Config file paths
    3. Environment variable GDALDEM_BIN
    4. Common installation paths
    5. System PATH

    Returns:
        Path to gdaldem executable

    Raises:
        FileNotFoundError if not found
    """
    import sys

    # Add src to path if needed for imports
    if __file__:
        src_dir = Path(__file__).parent.parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

    from utils.binary_paths import find_bundled_binary

    # Check for bundled binary first
    bundled = find_bundled_binary("gdaldem")
    if bundled:
        return bundled

    # Check config
    if config and config.get("paths", {}).get("gdaldem"):
        path = config["paths"]["gdaldem"]
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # Check environment variable
    if "GDALDEM_BIN" in os.environ:
        path = os.environ["GDALDEM_BIN"]
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    # Check common paths
    system = platform.system()
    candidates = []

    if system == "Darwin":  # macOS
        candidates = [
            "/opt/homebrew/bin/gdaldem",  # Homebrew (Apple Silicon) - prioritized
            "/usr/local/bin/gdaldem",  # Homebrew (Intel)
            "/opt/local/bin/gdaldem",  # MacPorts
            "/Applications/MacPorts/QGIS3.app/Contents/MacOS/bin/gdaldem",
            "/Applications/QGIS.app/Contents/MacOS/bin/gdaldem",
        ]
    elif system == "Windows":
        # Check conda environment
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            candidates.append(str(Path(conda_prefix) / "Library" / "bin" / "gdaldem.exe"))
        # Check common conda env locations
        home = Path.home()
        for env_name in ["lidar", "geo", "pdal"]:
            for base in [home / ".conda" / "envs", Path("C:/ProgramData/miniconda3/envs")]:
                candidate = base / env_name / "Library" / "bin" / "gdaldem.exe"
                candidates.append(str(candidate))
        # Check QGIS
        for ver in ["3.36", "3.34", "3.32", "3.30"]:
            candidates.append(f"C:\\Program Files\\QGIS {ver}\\bin\\gdaldem.exe")
        candidates.append(r"C:\OSGeo4W64\bin\gdaldem.exe")
        candidates.append(r"C:\OSGeo4W\bin\gdaldem.exe")
    else:  # Linux
        candidates = [
            "/usr/bin/gdaldem",
            "/usr/local/bin/gdaldem",
        ]

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    # Check PATH
    found = which("gdaldem")
    if found:
        return found

    raise FileNotFoundError(
        "Could not find 'gdaldem'.\n\n"
        "Please install GDAL (typically via QGIS installation) or set GDALDEM_BIN environment variable."
    )


def generate_classic_hillshade(
    dem_path: Path,
    log: LogFunc = None,
    cancel_check: Optional[callable] = None
) -> Path:
    """Generate classic hillshade (z=1.0, alt=45°, az=315°)."""
    hillshades_dir = get_output_dir() / "hillshades"
    hillshades_dir.mkdir(parents=True, exist_ok=True)
    output_path = hillshades_dir / f"{dem_path.stem}_classic.tif"

    _log(log, "=== Generating Classic Hillshade ===")
    _log(log, f"Z-factor: {STD_Z}, Altitude: {STD_ALT}°, Azimuth: {STD_AZ}°")

    config = load_config()
    try:
        gdaldem = find_gdaldem(config)
    except FileNotFoundError as e:
        raise RuntimeError(str(e))
    _log(log, f"Using gdaldem: {gdaldem}")

    cmd = [
        gdaldem, "hillshade",
        "-z", str(STD_Z), "-alt", str(STD_ALT), "-az", str(STD_AZ),
        "-compute_edges",
        str(dem_path), str(output_path),
    ]
    _log(log, "Running gdaldem...")
    _run_gdaldem(cmd, output_path, cancel_check, log)
    return output_path


def generate_archaeology_hillshade(
    dem_path: Path,
    preset_name: str,
    log: LogFunc = None,
    cancel_check: Optional[callable] = None
) -> Path:
    """Generate archaeology hillshade using one of the 5 presets."""
    if preset_name not in ARCH_PRESETS:
        raise ValueError(f"Unknown preset: {preset_name}")

    preset = ARCH_PRESETS[preset_name]
    hillshades_dir = get_output_dir() / "hillshades"
    hillshades_dir.mkdir(parents=True, exist_ok=True)

    safe_name = preset_name.replace(" ", "_").replace("–", "").replace("(", "").replace(")", "")
    output_path = hillshades_dir / f"{dem_path.stem}_arch_{safe_name}.tif"

    _log(log, f"=== Generating Archaeology Hillshade: {preset_name} ===")
    _log(log, f"Z-factor: {preset['z']}, Altitude: {preset['alt']}°")
    _log(log, "Multidirectional: Yes" if preset["multi"] else f"Azimuth: {preset.get('az', 'N/A')}°")

    config = load_config()
    try:
        gdaldem = find_gdaldem(config)
    except FileNotFoundError as e:
        raise RuntimeError(str(e))
    _log(log, f"Using gdaldem: {gdaldem}")

    cmd = [gdaldem, "hillshade", "-z", str(preset["z"]), "-alt", str(preset["alt"])]
    if preset["multi"]:
        cmd.append("-multidirectional")
    elif "az" in preset:
        cmd.extend(["-az", str(preset["az"])])
    cmd.extend(["-compute_edges", str(dem_path), str(output_path)])

    _log(log, "Running gdaldem...")
    _run_gdaldem(cmd, output_path, cancel_check, log)
    return output_path


def custom_hillshade_path(dem_path: Path, z_factor: float, altitude: float,
                           azimuth: float, multidirectional: bool) -> Path:
    """Return the output path for a custom hillshade with the given parameters."""
    hillshades_dir = get_output_dir() / "hillshades"
    if multidirectional:
        params = f"z{z_factor:.1f}_alt{altitude:.0f}_multi"
    else:
        params = f"z{z_factor:.1f}_alt{altitude:.0f}_az{azimuth:.0f}"
    return hillshades_dir / f"{dem_path.stem}_custom_{params}.tif"


def generate_custom_hillshade(
    dem_path: Path,
    z_factor: float,
    altitude: float,
    azimuth: float,
    multidirectional: bool,
    log: LogFunc = None,
    cancel_check: Optional[callable] = None
) -> Path:
    """Generate custom hillshade with user-defined parameters."""
    hillshades_dir = get_output_dir() / "hillshades"
    hillshades_dir.mkdir(parents=True, exist_ok=True)
    output_path = custom_hillshade_path(dem_path, z_factor, altitude, azimuth, multidirectional)

    _log(log, "=== Generating Custom Hillshade ===")
    _log(log, f"Z-factor: {z_factor}, Altitude: {altitude}°")
    _log(log, "Multidirectional: Yes" if multidirectional else f"Azimuth: {azimuth}°")

    config = load_config()
    try:
        gdaldem = find_gdaldem(config)
    except FileNotFoundError as e:
        raise RuntimeError(str(e))
    _log(log, f"Using gdaldem: {gdaldem}")

    cmd = [gdaldem, "hillshade", "-z", str(z_factor), "-alt", str(altitude)]
    if multidirectional:
        cmd.append("-multidirectional")
    else:
        cmd.extend(["-az", str(azimuth)])
    cmd.extend(["-compute_edges", str(dem_path), str(output_path)])

    _log(log, "Running gdaldem...")
    _run_gdaldem(cmd, output_path, cancel_check, log)
    return output_path
