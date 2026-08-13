#!/usr/bin/env python3
"""
LiDAR Hillshade Explorer
Main application entry point

A simplified desktop LiDAR hillshade application for non-technical users.
User enters coordinates, clicks one button, and the app automatically finds
LiDAR data online, processes it, generates a hillshade, and opens an interactive viewer.

This app is designed to run as a standalone application with all binaries
(PDAL and GDAL) and required libraries bundled.
"""

import os
import sys
import tkinter as tk
from pathlib import Path

# Add src directory to Python path
# This must happen before importing from src modules
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    src_dir = Path(sys._MEIPASS)
else:
    # Running in development
    src_dir = Path(__file__).parent / "src"
sys.path.insert(0, str(src_dir))

# Set up bundled environment (GDAL_DATA, PROJ_LIB) for standalone builds
# This must happen before any GDAL imports
from utils.binary_paths import setup_bundled_environment
setup_bundled_environment()

# Fallback for development mode - set GDAL/PROJ paths from system installs
if not getattr(sys, 'frozen', False):
    if sys.platform == "darwin":
        # macOS: check Homebrew paths
        if "GDAL_DATA" not in os.environ:
            gdal_data_paths = [
                "/opt/homebrew/share/gdal",  # Homebrew Apple Silicon
                "/usr/local/share/gdal",  # Homebrew Intel
            ]
            for gdal_data in gdal_data_paths:
                if Path(gdal_data).exists():
                    os.environ["GDAL_DATA"] = gdal_data
                    break

        if "PROJ_LIB" not in os.environ:
            proj_lib_paths = [
                "/opt/homebrew/share/proj",  # Homebrew Apple Silicon
                "/usr/local/share/proj",  # Homebrew Intel
            ]
            for proj_lib in proj_lib_paths:
                if Path(proj_lib).exists():
                    os.environ["PROJ_LIB"] = proj_lib
                    break

    elif sys.platform == "win32":
        # Windows: check conda environment paths
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            conda_gdal_data = Path(conda_prefix) / "Library" / "share" / "gdal"
            conda_proj_data = Path(conda_prefix) / "Library" / "share" / "proj"

            if "GDAL_DATA" not in os.environ and conda_gdal_data.exists():
                os.environ["GDAL_DATA"] = str(conda_gdal_data)

            if "PROJ_LIB" not in os.environ and conda_proj_data.exists():
                os.environ["PROJ_LIB"] = str(conda_proj_data)
                os.environ["PROJ_DATA"] = str(conda_proj_data)

            # Also add conda Library\bin to PATH for DLL resolution in dev mode
            conda_bin = str(Path(conda_prefix) / "Library" / "bin")
            current_path = os.environ.get("PATH", "")
            if conda_bin not in current_path:
                os.environ["PATH"] = f"{conda_bin};{current_path}"

from main_gui import HillshadeExplorerApp

APP_VERSION = "3.2.4"
WINDOWS_APP_ID = "com.techbill.lidar-hillshade-explorer"


def main():
    """Launch the LiDAR Hillshade Explorer application"""
    # Give Windows a stable application identity before creating any windows.
    # Without this, the taskbar can group the app under Python and show the
    # Python icon even though the executable has our icon embedded.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                WINDOWS_APP_ID
            )
        except (AttributeError, OSError):
            pass

    root = tk.Tk()
    root.title(f"LiDAR Hillshade Explorer {APP_VERSION}")

    # Set window icon for taskbar and title bar
    try:
        if getattr(sys, 'frozen', False):
            # PyInstaller bundle - icon is bundled as data
            icon_path = Path(sys._MEIPASS) / "assets" / "icon.ico"
            if not icon_path.exists():
                icon_path = Path(sys.executable).parent / "_internal" / "assets" / "icon.ico"
        else:
            icon_path = Path(__file__).parent / "assets" / "icon.ico"

        if sys.platform == "win32":
            png_path = icon_path.with_suffix(".png")
            if png_path.exists():
                # iconphoto preserves the PNG alpha channel and supplies the
                # window/taskbar icon. Keep a reference for the Tk lifetime.
                root._app_icon_image = tk.PhotoImage(file=str(png_path))
                root.iconphoto(True, root._app_icon_image)
            elif icon_path.exists():
                root.iconbitmap(str(icon_path))
        elif icon_path.exists():
            root.iconbitmap(str(icon_path))
    except Exception:
        pass  # Icon not critical

    # Center window on screen
    window_width = 500
    window_height = 760
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    center_x = int((screen_width - window_width) / 2)
    center_y = int((screen_height - window_height) / 2)
    root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

    root.resizable(True, True)

    # Initialize the application
    app = HillshadeExplorerApp(root)

    # Start the Tkinter event loop
    root.mainloop()


if __name__ == "__main__":
    main()
