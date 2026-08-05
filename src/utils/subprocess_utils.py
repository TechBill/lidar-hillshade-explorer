#!/usr/bin/env python3
"""
Subprocess utilities for cross-platform compatibility.

On Windows, subprocess.Popen and subprocess.run create visible console
windows by default when running .exe files. This module provides helpers
to suppress those console windows.
"""

import sys
import subprocess


def get_creation_flags() -> int:
    """
    Get the appropriate process creation flags for the current platform.

    On Windows, returns CREATE_NO_WINDOW to prevent console black boxes
    from popping up when running pdal.exe, gdaldem.exe, etc.

    On other platforms, returns 0 (no flags).

    Returns:
        Process creation flags integer
    """
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def get_startupinfo():
    """
    Get StartupInfo to hide console windows on Windows.

    Returns:
        subprocess.STARTUPINFO on Windows (with hidden window), None on other platforms
    """
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    return None
