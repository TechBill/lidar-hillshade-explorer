#!/usr/bin/env python3
"""
Configuration management for LiDAR Hillshade Explorer.

Handles loading and saving user preferences to platform-appropriate directories.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any


TERRAIN_STYLE_CONTINUOUS = "continuous"
TERRAIN_STYLE_PRESERVE = "preserve_gaps"
TERRAIN_STYLE_CUSTOM = "custom"

TERRAIN_STYLE_LABELS = {
    TERRAIN_STYLE_CONTINUOUS: "Continuous Terrain (Recommended)",
    TERRAIN_STYLE_PRESERVE: "Preserve Large Gaps",
    TERRAIN_STYLE_CUSTOM: "Custom",
}

TERRAIN_STYLE_PROFILES = {
    TERRAIN_STYLE_CONTINUOUS: {
        "tin_max_edge_multiplier": 40,
        "fill_max_search": 64,
        "fill_smoothing": 2,
    },
    TERRAIN_STYLE_PRESERVE: {
        "tin_max_edge_multiplier": 12,
        "fill_max_search": 16,
        "fill_smoothing": 2,
    },
}


def normalize_terrain_style(value: Any) -> str:
    """Return a supported terrain style key, defaulting to continuous."""
    if value in TERRAIN_STYLE_LABELS:
        return str(value)
    return TERRAIN_STYLE_CONTINUOUS


def get_effective_dem_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve the selected terrain preset over the saved custom settings."""
    settings = dict(config.get("dem_fill", {}))
    style = normalize_terrain_style(
        config.get("preferences", {}).get("terrain_style")
    )
    settings.update(TERRAIN_STYLE_PROFILES.get(style, {}))
    settings["terrain_style"] = style
    return settings


def get_config_dir() -> Path:
    """
    Get platform-appropriate config directory.

    Returns:
        Path to config directory:
        - macOS: ~/Library/Application Support/LiDARHillshadeExplorer
        - Windows: %APPDATA%/LiDARHillshadeExplorer
        - Linux: ~/.config/LiDARHillshadeExplorer
    """
    system = platform.system()

    if system == "Darwin":  # macOS
        config_dir = Path.home() / "Library" / "Application Support" / "LiDARHillshadeExplorer"
    elif system == "Windows":
        import os
        appdata = os.environ.get("APPDATA")
        if appdata:
            config_dir = Path(appdata) / "LiDARHillshadeExplorer"
        else:
            config_dir = Path.home() / "AppData" / "Roaming" / "LiDARHillshadeExplorer"
    else:  # Linux/Unix
        config_dir = Path.home() / ".config" / "LiDARHillshadeExplorer"

    # Create directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_file() -> Path:
    """Get path to user config file."""
    return get_config_dir() / "config.json"


def get_cache_dir() -> Path:
    """
    Get platform-appropriate cache directory for AWS index and other cached data.

    Returns:
        Path to cache directory (same as config dir for simplicity)
    """
    cache_dir = get_config_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_output_dir() -> Path:
    """
    Get the writable output directory for generated LAZ, DEM, and hillshades.

    Returns:
        Path to output directory
    """
    # A signed app installed in /Applications must not write inside its own
    # bundle. Keep generated files beside the user's configuration instead.
    output_dir = get_config_dir() / "output"

    # Create output subdirectories
    (output_dir / "laz").mkdir(parents=True, exist_ok=True)
    (output_dir / "dem").mkdir(parents=True, exist_ok=True)
    (output_dir / "hillshades").mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)

    return output_dir


def get_default_config() -> dict[str, Any]:
    """
    Get default configuration values.

    Returns:
        Dictionary with default settings
    """
    return {
        "version": 1,
        "last_location": {
            "lat": None,
            "lon": None,
            "size_sqmi": 0.25
        },
        "preferences": {
            "smart_select": True,
            "show_log": False,
            "terrain_style": TERRAIN_STYLE_CONTINUOUS
        },
        "ui": {
            "window_geometry": "500x550"
        },
        "paths": {
            "pdal": None,
            "gdaldem": None
        },
        "binary_overrides": {
            "pdal": {
                "enabled": False,
                "path": ""
            },
            "gdaldem": {
                "enabled": False,
                "path": ""
            }
        },
        "dem_fill": {
            "tin_buffer_m": 20,
            "tin_max_edge_multiplier": 12,
            "idw_window_size": 12,
            "fill_max_search": 16,
            "fill_smoothing": 4,
            "deterministic": False
        },
        "aws_renewal_days": 30,
        "wesm_renewal_days": 7
    }


def load_config() -> dict[str, Any]:
    """
    Load user configuration from file.

    If config file doesn't exist or is corrupt, returns default config.

    Returns:
        Dictionary with configuration settings
    """
    config_file = get_config_file()
    defaults = get_default_config()

    if not config_file.exists():
        return defaults

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        # Merge loaded config with defaults (in case new settings added)
        config = defaults.copy()
        _deep_merge(config, loaded)
        return config
    except Exception as e:
        # If config file is corrupt, log and return defaults
        print(f"Warning: Could not load config ({e}), using defaults")
        return defaults


def save_config(config: dict[str, Any]) -> bool:
    """
    Save user configuration to file.

    Args:
        config: Configuration dictionary to save

    Returns:
        True if save successful, False otherwise
    """
    config_file = get_config_file()

    try:
        # Ensure config directory exists
        config_file.parent.mkdir(parents=True, exist_ok=True)

        # Write config file with pretty formatting
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return True
    except Exception as e:
        # Don't crash app if save fails, just log error
        print(f"Warning: Could not save config ({e})")
        return False


def _deep_merge(base: dict, updates: dict) -> None:
    """
    Deep merge updates into base dictionary (modifies base in-place).

    Args:
        base: Base dictionary to merge into
        updates: Dictionary with updates to apply
    """
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
