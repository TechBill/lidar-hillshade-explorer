#!/usr/bin/env python3
"""
Hillshade viewer for LiDAR Hillshade Explorer.

Pillow-based viewer with zoom/pan controls and style switching.
Based on geoimage-kmz viewer pattern.
"""

import math
import threading
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from typing import Optional
import webbrowser

from PIL import Image, ImageTk
import rasterio

from lidar_core.hillshade_engine import (
    ARCH_PRESETS,
    custom_hillshade_path,
    generate_classic_hillshade,
    generate_archaeology_hillshade,
    generate_custom_hillshade,
)


class HillshadeViewer:
    """
    Separate window for viewing and styling hillshades.

    Features:
    - Pillow canvas with zoom/pan
    - Zoom +/- buttons
    - Mouse wheel zoom
    - Left drag to pan
    - Style selector with dynamic controls
    - KMZ export
    """

    def __init__(self, parent: tk.Tk, dem_path: Path, initial_hillshade: Path, metadata: dict, on_close_callback=None):
        self.parent = parent
        self.dem_path = dem_path
        self.current_hillshade = initial_hillshade
        self.metadata = metadata
        self.on_close_callback = on_close_callback

        # Create window
        self.window = tk.Toplevel(parent)
        self.window.title(f"Hillshade Viewer - {dem_path.stem}")

        # Set window icon for taskbar and title bar
        try:
            import sys
            if getattr(sys, 'frozen', False):
                icon_path = Path(sys._MEIPASS) / "assets" / "icon.ico"
                if not icon_path.exists():
                    icon_path = Path(sys.executable).parent / "_internal" / "assets" / "icon.ico"
            else:
                icon_path = Path(__file__).parent.parent / "assets" / "icon.ico"

            if sys.platform == "win32":
                png_path = icon_path.with_suffix(".png")
                if png_path.exists():
                    self._app_icon_image = tk.PhotoImage(file=str(png_path))
                    self.window.iconphoto(True, self._app_icon_image)
                elif icon_path.exists():
                    self.window.iconbitmap(str(icon_path))
            elif icon_path.exists():
                self.window.iconbitmap(str(icon_path))
        except Exception:
            pass  # Icon not critical

        # Fit the viewer to the current display instead of extending beyond
        # shorter laptop screens.
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        window_width = min(1200, max(700, screen_width - 40))
        window_height = min(800, max(600, screen_height - 80))
        center_x = int((screen_width - window_width) / 2)
        center_y = int((screen_height - window_height) / 2)
        self.window.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

        # Override window close to show confirmation
        self.window.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # Image state
        self._pil_image: Optional[Image.Image] = None
        self._tk_image: Optional[ImageTk.PhotoImage] = None

        # Canvas transform state
        self.scale = 1.0
        self.min_scale = 0.1
        self.max_scale = 10.0  # 1000% max zoom
        self.offset_x = 0.0
        self.offset_y = 0.0

        # Zoom throttling
        self._pending_zoom_id: Optional[str] = None
        self._quality_redraw_id: Optional[str] = None

        # Pan state
        self._left_drag_start: Optional[tuple[int, int]] = None
        self._panning = False

        # Processing state
        self.is_processing = False

        # Build UI
        self._build_ui()

        # Load initial hillshade
        self._load_and_display_hillshade(initial_hillshade)

    def _build_ui(self):
        """Build the viewer UI layout."""
        # Create PanedWindow for resizable panels
        paned = tk.PanedWindow(self.window, orient=tk.HORIZONTAL, sashwidth=5, bg="#cccccc")
        paned.pack(fill=tk.BOTH, expand=True)

        # Scrollable left panel keeps metadata and styling controls accessible
        # on shorter displays and when Custom style controls are expanded.
        sidebar = tk.Frame(paned, bd=1, relief="groove", width=315)
        paned.add(sidebar, minsize=295, width=315)

        sidebar_canvas = tk.Canvas(sidebar, highlightthickness=0, width=295)
        sidebar_scrollbar = ttk.Scrollbar(
            sidebar, orient=tk.VERTICAL, command=sidebar_canvas.yview
        )
        sidebar_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        sidebar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sidebar_canvas.configure(yscrollcommand=sidebar_scrollbar.set)

        left_panel = tk.Frame(sidebar_canvas)
        sidebar_window = sidebar_canvas.create_window(
            (0, 0), window=left_panel, anchor=tk.NW
        )
        left_panel.bind(
            "<Configure>",
            lambda event: sidebar_canvas.configure(
                scrollregion=sidebar_canvas.bbox("all")
            ),
        )
        sidebar_canvas.bind(
            "<Configure>",
            lambda event: sidebar_canvas.itemconfigure(
                sidebar_window, width=event.width
            ),
        )

        # LiDAR data information at top
        info_frame = tk.Frame(left_panel)
        info_frame.pack(fill=tk.X, pady=(8, 4), padx=10)

        # Get metadata
        meta = self.metadata.get('metadata', {})
        point_spacing = meta.get('point_spacing', 0)
        dem_resolution = meta.get('dem_resolution', 0)

        # Display authoritative dataset metadata above generated-output statistics.
        tk.Label(
            info_frame,
            text="LiDAR Dataset",
            font=("", 11, "bold")
        ).pack(anchor=tk.W)

        dataset_rows = [
            ("Work unit", meta.get("workunit") or meta.get("dataset_id")),
            ("Collected", self._format_date_range(
                meta.get("collection_start"), meta.get("collection_end")
            )),
            ("Quality", self._format_quality_level(meta.get("quality_level"))),
            ("USGS DEM", self._format_meters(meta.get("dem_gsd_meters"))),
            ("Published", self._format_date(meta.get("lpc_publication_date"))),
            ("Horizontal CRS", self._format_crs(meta.get("horizontal_crs"), meta.get("epsg"))),
            ("Vertical CRS", self._format_crs(meta.get("vertical_crs"))),
            ("Geoid", meta.get("geoid")),
        ]
        for label, value in dataset_rows:
            self._add_metadata_row(info_frame, label, value)

        tk.Frame(info_frame, height=1, bg="#cccccc").pack(fill=tk.X, pady=(6, 5))

        tk.Label(
            info_frame,
            text="LiDAR Points & Output",
            font=("", 11, "bold")
        ).pack(anchor=tk.W)

        if point_spacing > 0:
            tk.Label(
                info_frame,
                text=f"Point spacing: {point_spacing:.3f} m",
                font=("", 10)
            ).pack(anchor=tk.W)

        if dem_resolution > 0:
            tk.Label(
                info_frame,
                text=f"Generated DEM: {dem_resolution:.2f} m",
                font=("", 10)
            ).pack(anchor=tk.W)

        # Zoom section
        tk.Label(left_panel, text="Zoom", font=("Helvetica", 10, "bold")).pack(pady=(4, 3))
        tk.Button(left_panel, text="Zoom +", command=lambda: self._zoom_button(1.2), width=14).pack(pady=3, padx=5)
        tk.Button(left_panel, text="Zoom -", command=lambda: self._zoom_button(1 / 1.2), width=14).pack(pady=3, padx=5)
        tk.Button(left_panel, text="Fit to Window", command=self._reset_view_to_fit, width=14).pack(pady=3, padx=5)

        tk.Label(left_panel, text="", height=1).pack()  # Spacer

        # Style section
        tk.Label(left_panel, text="Hillshade Style", font=("Helvetica", 10, "bold")).pack(pady=(5, 5))

        self.style_var = tk.StringVar(value="Classic")
        ttk.Radiobutton(left_panel, text="Classic", variable=self.style_var, value="Classic",
                       command=self._on_style_changed).pack(anchor=tk.W, padx=20, pady=2)
        ttk.Radiobutton(left_panel, text="Archaeology Presets", variable=self.style_var, value="Archaeology",
                       command=self._on_style_changed).pack(anchor=tk.W, padx=20, pady=2)
        ttk.Radiobutton(left_panel, text="Custom", variable=self.style_var, value="Custom",
                       command=self._on_style_changed).pack(anchor=tk.W, padx=20, pady=2)

        # Dynamic controls container
        self.dynamic_frame = tk.Frame(left_panel)
        self.dynamic_frame.pack(fill=tk.X, padx=10, pady=(10, 0))

        # Apply button
        self.apply_btn = tk.Button(
            left_panel,
            text="Apply Style",
            command=self._on_apply_clicked,
            width=14,
            state=tk.DISABLED
        )
        self.apply_btn.pack(pady=(10, 5))

        tk.Label(left_panel, text="", height=1).pack()  # Spacer

        # Export section
        tk.Label(left_panel, text="Export", font=("Helvetica", 10, "bold")).pack(pady=(5, 5))
        tk.Button(left_panel, text="Export GeoTIFF", command=self._on_export_tif, width=14).pack(pady=3, padx=5)
        tk.Button(left_panel, text="Export KMZ", command=self._on_export_kmz, width=14).pack(pady=3, padx=5)

        tk.Label(left_panel, text="", height=1).pack()  # Spacer

        # Controls help
        controls_help = tk.Label(
            left_panel,
            justify="left",
            anchor="nw",
            text=(
                "Controls:\n"
                "• Left drag: pan\n"
                "• Mouse wheel: zoom\n"
                "• Zoom buttons: zoom in/out"
            ),
            font=("Helvetica", 9)
        )
        controls_help.pack(fill="x", padx=8, pady=(10, 10))

        # Donate button at bottom (above close button)
        donation_frame = tk.Frame(left_panel)
        donation_frame.pack(pady=(5, 0))

        ttk.Label(
            donation_frame,
            text="If you find this useful, please donate!",
            font=("", 9)
        ).pack(pady=(0, 5))

        # PayPal button (Frame + Label approach for macOS compatibility)
        paypal_frame = tk.Frame(donation_frame, bg="#0070BA", bd=1, relief="raised", cursor="hand2")
        paypal_frame.pack()

        paypal_label = tk.Label(
            paypal_frame,
            text="Donate on PayPal",
            bg="#0070BA",
            fg="white",
            font=("", 9, "bold"),
            padx=15,
            pady=6
        )
        paypal_label.pack()

        # Bind click events to both frame and label
        paypal_frame.bind("<Button-1>", lambda e: webbrowser.open("https://www.paypal.com/paypalme/techbill"))
        paypal_label.bind("<Button-1>", lambda e: webbrowser.open("https://www.paypal.com/paypalme/techbill"))

        # Hover effects
        def on_enter(e):
            paypal_frame.config(bg="#005EA6")
            paypal_label.config(bg="#005EA6")

        def on_leave(e):
            paypal_frame.config(bg="#0070BA")
            paypal_label.config(bg="#0070BA")

        paypal_frame.bind("<Enter>", on_enter)
        paypal_label.bind("<Enter>", on_enter)
        paypal_frame.bind("<Leave>", on_leave)
        paypal_label.bind("<Leave>", on_leave)

        # Close button at bottom
        tk.Button(left_panel, text="Close", command=self._on_window_close, width=14).pack(pady=10)

        # Center panel for canvas
        center_frame = tk.Frame(paned, bg="#222222")
        paned.add(center_frame, minsize=600)

        # Status bar
        self.status_var = tk.StringVar(value="Drag to pan. Wheel to zoom.")
        tk.Label(center_frame, textvariable=self.status_var, anchor="w", bg="#333333", fg="white").pack(
            side="top", fill="x", padx=5, pady=5
        )

        # Canvas for image display
        self.canvas = tk.Canvas(center_frame, bg="#222222", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Canvas bindings
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_at(e.x, e.y, 1.2))
        self.canvas.bind("<Button-5>", lambda e: self.zoom_at(e.x, e.y, 1 / 1.2))
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Build initial dynamic controls
        self._rebuild_dynamic_controls()

    @staticmethod
    def _format_date(value) -> str:
        """Return a compact ISO date or a consistent missing-value label."""
        if value is None or str(value).strip() == "":
            return "Not available"
        return str(value).strip().replace("/", "-")

    @classmethod
    def _format_date_range(cls, start, end) -> str:
        start_text = cls._format_date(start)
        end_text = cls._format_date(end)
        if start_text == "Not available" and end_text == "Not available":
            return "Not available"
        if start_text == "Not available":
            return end_text
        if end_text == "Not available" or end_text == start_text:
            return start_text
        return f"{start_text} – {end_text}"

    @staticmethod
    def _format_quality_level(value) -> str:
        if value is None or str(value).strip() == "":
            return "Not available"
        text = str(value).strip()
        return text if text.upper().startswith("QL") else f"QL{text}"

    @staticmethod
    def _format_meters(value) -> str:
        if value is None or str(value).strip() == "":
            return "Not available"
        try:
            return f"{float(value):g} m"
        except (TypeError, ValueError):
            return f"{value} m"

    @staticmethod
    def _format_epsg(value) -> str:
        if value is None or str(value).strip() == "":
            return "Not available"
        return f"EPSG:{value}"

    @classmethod
    def _format_crs(cls, value, fallback=None) -> str:
        """Format numeric WESM CRS identifiers explicitly as EPSG codes."""
        selected = value if value is not None and str(value).strip() else fallback
        if selected is None or str(selected).strip() == "":
            return "Not available"
        text = str(selected).strip()
        if text.isdigit():
            return cls._format_epsg(text)
        return text

    @staticmethod
    def _add_metadata_row(parent, label: str, value) -> None:
        """Add a compact wrapping label/value row to the dataset summary."""
        value_text = str(value).strip() if value is not None else ""
        if not value_text:
            value_text = "Not available"
        row = tk.Frame(parent)
        row.pack(fill=tk.X, anchor=tk.W)
        tk.Label(
            row,
            text=f"{label}:",
            font=("", 10, "bold"),
            width=12,
            anchor=tk.NW,
        ).pack(side=tk.LEFT)
        tk.Label(
            row,
            text=value_text,
            font=("", 10),
            justify=tk.LEFT,
            anchor=tk.NW,
            wraplength=190,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _on_style_changed(self):
        """Handle style selection change."""
        self._rebuild_dynamic_controls()

    def _rebuild_dynamic_controls(self):
        """Rebuild dynamic controls based on selected style."""
        # Clear existing controls
        for widget in self.dynamic_frame.winfo_children():
            widget.destroy()

        style = self.style_var.get()

        if style == "Classic":
            # No additional controls for classic
            ttk.Label(
                self.dynamic_frame,
                text="Standard hillshade:\nz=1.0, alt=45°, az=315°",
                foreground="gray",
                font=("Helvetica", 9)
            ).pack(anchor=tk.W)
            self.apply_btn.config(state=tk.NORMAL)

        elif style == "Archaeology":
            # Preset dropdown
            ttk.Label(self.dynamic_frame, text="Preset:", font=("Helvetica", 9, "bold")).pack(anchor=tk.W, pady=(5, 2))

            self.preset_var = tk.StringVar(value=list(ARCH_PRESETS.keys())[0])
            preset_list = ttk.Combobox(
                self.dynamic_frame,
                textvariable=self.preset_var,
                values=list(ARCH_PRESETS.keys()),
                state="readonly",
                width=25
            )
            preset_list.pack(fill=tk.X, pady=2)
            preset_list.bind("<<ComboboxSelected>>", self._on_preset_changed)

            # Preset info
            self.preset_info_label = ttk.Label(
                self.dynamic_frame,
                text="",
                foreground="gray",
                font=("Helvetica", 8),
                wraplength=180
            )
            self.preset_info_label.pack(anchor=tk.W, pady=(5, 0))

            self._update_preset_info()
            self.apply_btn.config(state=tk.NORMAL)

        elif style == "Custom":
            # Z-factor
            ttk.Label(self.dynamic_frame, text="Z-factor:", font=("Helvetica", 9)).pack(anchor=tk.W, pady=(5, 2))
            self.z_var = tk.StringVar(value="1.0")
            ttk.Entry(self.dynamic_frame, textvariable=self.z_var, width=12).pack(anchor=tk.W, pady=2)

            # Altitude
            ttk.Label(self.dynamic_frame, text="Altitude (°):", font=("Helvetica", 9)).pack(anchor=tk.W, pady=(5, 2))
            self.alt_var = tk.StringVar(value="45")
            ttk.Entry(self.dynamic_frame, textvariable=self.alt_var, width=12).pack(anchor=tk.W, pady=2)

            # Azimuth
            ttk.Label(self.dynamic_frame, text="Azimuth (°):", font=("Helvetica", 9)).pack(anchor=tk.W, pady=(5, 2))
            self.az_var = tk.StringVar(value="315")
            self.az_entry = ttk.Entry(self.dynamic_frame, textvariable=self.az_var, width=12)
            self.az_entry.pack(anchor=tk.W, pady=2)

            # Multidirectional
            self.multi_var = tk.BooleanVar(value=False)
            multi_check = ttk.Checkbutton(
                self.dynamic_frame,
                text="Multidirectional",
                variable=self.multi_var,
                command=self._on_multi_changed
            )
            multi_check.pack(anchor=tk.W, pady=(5, 0))

            self.apply_btn.config(state=tk.NORMAL)

    def _on_preset_changed(self, event=None):
        """Handle preset selection change."""
        self._update_preset_info()

    def _update_preset_info(self):
        """Update preset information label."""
        preset_name = self.preset_var.get()
        if preset_name in ARCH_PRESETS:
            preset = ARCH_PRESETS[preset_name]
            info = f"Z={preset['z']}, Alt={preset['alt']}°"
            if preset['multi']:
                info += "\nMultidirectional"
            else:
                info += f"\nAz={preset.get('az', 'N/A')}°"
            self.preset_info_label.config(text=info)

    def _on_multi_changed(self):
        """Handle multidirectional checkbox change."""
        if self.multi_var.get():
            self.az_entry.config(state=tk.DISABLED)
        else:
            self.az_entry.config(state=tk.NORMAL)

    def _get_hillshade_path(self) -> Optional[Path]:
        """
        Get the expected path for the currently selected hillshade style.

        Returns:
            Path to hillshade file (may or may not exist)
        """
        from utils.config import get_output_dir

        output_dir = get_output_dir()
        hillshades_dir = output_dir / "hillshades"
        stem = self.dem_path.stem

        style = self.style_var.get()

        if style == "Classic":
            return hillshades_dir / f"{stem}_classic.tif"
        elif style == "Archaeology":
            preset_name = self.preset_var.get()
            # Use same sanitization as in hillshade_engine.py
            safe_name = preset_name.replace(" ", "_").replace("–", "").replace("(", "").replace(")", "")
            return hillshades_dir / f"{stem}_arch_{safe_name}.tif"
        elif style == "Custom":
            try:
                z = float(self.z_var.get())
                alt = float(self.alt_var.get())
                az = float(self.az_var.get()) if not self.multi_var.get() else 0.0
                multi = self.multi_var.get()
            except (ValueError, AttributeError):
                return None
            return custom_hillshade_path(self.dem_path, z, alt, az, multi)

        return None

    def _on_apply_clicked(self):
        """Handle Apply button click."""
        if self.is_processing:
            messagebox.showwarning(
                "Processing",
                "Processing is already in progress.",
                parent=self.window
            )
            return

        style = self.style_var.get()

        # Check if hillshade already exists
        expected_path = self._get_hillshade_path()
        if expected_path and expected_path.exists():
            # Hillshade already exists, just load it
            print(f"Loading existing {style} hillshade: {expected_path.name}")
            self._load_and_display_hillshade(expected_path)
            self.current_hillshade = expected_path
            self.status_var.set("Hillshade loaded! Drag to pan. Wheel to zoom.")
            return

        # Hillshade doesn't exist, generate it
        try:
            # Simple logger for console output (visible when running from terminal)
            def log_msg(msg):
                print(msg)

            if style == "Classic":
                self._regenerate_hillshade_async(
                    lambda: generate_classic_hillshade(
                        self.dem_path,
                        log=log_msg
                    )
                )
            elif style == "Archaeology":
                preset_name = self.preset_var.get()
                self._regenerate_hillshade_async(
                    lambda: generate_archaeology_hillshade(
                        self.dem_path,
                        preset_name,
                        log=log_msg
                    )
                )
            elif style == "Custom":
                z_factor = float(self.z_var.get())
                altitude = float(self.alt_var.get())
                azimuth = float(self.az_var.get())
                multidirectional = self.multi_var.get()

                self._regenerate_hillshade_async(
                    lambda: generate_custom_hillshade(
                        self.dem_path,
                        z_factor,
                        altitude,
                        azimuth,
                        multidirectional,
                        log=log_msg
                    )
                )
        except ValueError as e:
            messagebox.showerror(
                "Invalid Input",
                f"Please check your input values:\n\n{e}",
                parent=self.window
            )

    def _regenerate_hillshade_async(self, generator_func):
        """Regenerate hillshade in background thread."""
        self.is_processing = True
        self.apply_btn.config(state=tk.DISABLED, text="Generating...")
        self.status_var.set("Generating hillshade...")

        def worker():
            try:
                new_hillshade = generator_func()
                self.window.after(0, lambda hs=new_hillshade: self._on_regeneration_complete(hs))
            except Exception as e:
                self.window.after(0, lambda error=e: self._on_regeneration_error(error))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_regeneration_complete(self, hillshade_path: Path):
        """Handle successful hillshade regeneration."""
        self.is_processing = False
        self.apply_btn.config(state=tk.NORMAL, text="Apply Style")

        self.current_hillshade = hillshade_path
        self._load_and_display_hillshade(hillshade_path)

        self.status_var.set("Hillshade updated! Drag to pan. Wheel to zoom.")

    def _on_regeneration_error(self, error: Exception):
        """Handle hillshade regeneration error."""
        self.is_processing = False
        self.apply_btn.config(state=tk.NORMAL, text="Apply Style")
        self.status_var.set("Error generating hillshade")

        messagebox.showerror(
            "Error",
            f"Failed to generate hillshade:\n\n{error}",
            parent=self.window
        )

    def _load_and_display_hillshade(self, hillshade_path: Path):
        """Load hillshade from file and display."""
        try:
            # Read GeoTIFF with rasterio and convert to PIL Image
            with rasterio.open(hillshade_path) as src:
                data = src.read(1)

            # Normalize to 0-255 for display
            import numpy as np
            data_min = data.min()
            data_max = data.max()
            if data_max > data_min:
                data_normalized = ((data - data_min) / (data_max - data_min) * 255).astype(np.uint8)
            else:
                data_normalized = np.zeros_like(data, dtype=np.uint8)

            # Convert to PIL Image
            self._pil_image = Image.fromarray(data_normalized, mode='L')

            # Reset view to fit
            self._reset_view_to_fit()
            self.redraw()

        except Exception as e:
            messagebox.showerror(
                "Display Error",
                f"Could not display hillshade:\n\n{e}",
                parent=self.window
            )

    def _show_kmz_export_dialog(self) -> tuple[list[Path], str] | None:
        """
        Show dialog for selecting TIF files and entering description for KMZ export.

        Returns:
            Tuple of (list of selected TIF paths, description) or None if cancelled
        """
        from utils.config import get_output_dir

        # Get TIF files from hillshades directory belonging to the AOI
        # currently open in this viewer. The folder is shared across
        # sessions and isn't cleared until the viewer window is closed
        # (or the app quits), so it can still hold hillshades from a
        # previous location if this one was reached without going back to
        # the main menu in between. Filtering by the current DEM's stem
        # keeps those out of the list - otherwise two unrelated locations'
        # "Classic.tif" (etc.) are indistinguishable in the checkbox list
        # since the display name strips the location prefix.
        output_dir = get_output_dir()
        hillshades_dir = output_dir / "hillshades"

        if not hillshades_dir.exists():
            return None

        current_stem = self.dem_path.stem
        tif_files = sorted(
            (p for p in hillshades_dir.glob("*.tif") if p.stem.startswith(f"{current_stem}_")),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if not tif_files:
            messagebox.showwarning(
                "No Files",
                "No hillshade TIF files found in output directory.",
                parent=self.window
            )
            return None

        # Create dialog window
        dialog = tk.Toplevel(self.window)
        dialog.title("Export to KMZ")
        dialog.geometry("700x500")
        dialog.transient(self.window)
        dialog.grab_set()

        # Center dialog on parent window
        dialog.update_idletasks()
        parent_x = self.window.winfo_x()
        parent_y = self.window.winfo_y()
        parent_width = self.window.winfo_width()
        parent_height = self.window.winfo_height()
        dialog_width = dialog.winfo_width()
        dialog_height = dialog.winfo_height()
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        dialog.geometry(f"+{x}+{y}")

        # Result variables
        selected_files = None
        description_text = None

        # Main frame with padding
        main_frame = ttk.Frame(dialog, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title label
        ttk.Label(
            main_frame,
            text="Select hillshade files to export:",
            font=("", 11, "bold")
        ).pack(anchor=tk.W, pady=(0, 10))

        # Frame for checkboxes with scrollbar
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        # Canvas for scrollable checkbox area with border and white background
        canvas = tk.Canvas(
            list_frame,
            height=100,
            bg='white',
            highlightthickness=1,
            highlightbackground='gray',
            relief=tk.SUNKEN,
            borderwidth=2
        )
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg='white')

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Create checkbox for each TIF file
        checkbox_vars = []
        for idx, tif_file in enumerate(tif_files):
            var = tk.BooleanVar(value=(tif_file == self.current_hillshade))
            checkbox_vars.append((var, tif_file))

            # Extract clean display name from TIF filename
            tif_name = tif_file.stem
            if '_classic' in tif_name:
                display_name = "Classic.tif"
            elif '_arch_' in tif_name:
                # Extract everything after _arch_ and clean it up
                style_part = tif_name.split('_arch_')[-1].replace('_', ' ')
                display_name = f"{style_part}.tif"
            elif '_custom' in tif_name:
                display_name = "Custom.tif"
            else:
                # Fallback to original name
                display_name = tif_file.name

            cb = tk.Checkbutton(
                scrollable_frame,
                text=display_name,
                variable=var,
                bg='white',
                activebackground='white',
                highlightthickness=0
            )
            cb.pack(anchor=tk.W, padx=5, pady=2)

        # Select/Deselect All buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(0, 15))

        def select_all():
            for var, _ in checkbox_vars:
                var.set(True)

        def deselect_all():
            for var, _ in checkbox_vars:
                var.set(False)

        ttk.Button(button_frame, text="Select All", command=select_all, width=12).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="Deselect All", command=deselect_all, width=12).pack(side=tk.LEFT)

        # Description section
        desc_frame = ttk.LabelFrame(main_frame, text="Description (optional)", padding=10)
        desc_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(
            desc_frame,
            text="Enter a custom description for the KMZ file(s):",
            font=("", 9)
        ).pack(anchor=tk.W, pady=(0, 5))

        # Text widget for description with placeholder
        desc_text = tk.Text(desc_frame, height=3, font=("", 10), wrap=tk.WORD)
        desc_text.pack(fill=tk.X)

        # Placeholder text
        placeholder = "Leave blank or enter description here..."
        desc_text.insert("1.0", placeholder)
        desc_text.config(foreground="gray")

        # Placeholder behavior
        def on_focus_in(event):
            if desc_text.get("1.0", "end-1c") == placeholder:
                desc_text.delete("1.0", tk.END)
                desc_text.config(foreground="black")

        def on_focus_out(event):
            if not desc_text.get("1.0", "end-1c").strip():
                desc_text.insert("1.0", placeholder)
                desc_text.config(foreground="gray")

        desc_text.bind("<FocusIn>", on_focus_in)
        desc_text.bind("<FocusOut>", on_focus_out)

        # OK/Cancel buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(0, 0))

        def on_ok():
            nonlocal selected_files, description_text

            # Get selected files from checkboxes
            selected_files = [tif_file for var, tif_file in checkbox_vars if var.get()]

            if not selected_files:
                messagebox.showwarning(
                    "No Selection",
                    "Please select at least one file to export.",
                    parent=dialog
                )
                return

            # Get description (empty if placeholder still showing)
            desc = desc_text.get("1.0", "end-1c").strip()
            if desc == placeholder:
                description_text = ""
            else:
                description_text = desc

            dialog.destroy()

        def on_cancel():
            nonlocal selected_files, description_text
            selected_files = None
            description_text = None
            dialog.destroy()

        ttk.Button(btn_frame, text="Export", command=on_ok, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel, width=12).pack(side=tk.LEFT, padx=5)

        # Handle window close
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)

        # Wait for dialog to close
        dialog.wait_window()

        if selected_files is not None:
            return (selected_files, description_text)
        return None

    def _show_tif_export_dialog(self) -> Path | None:
        """
        Show dialog for selecting a single hillshade TIF to export.

        Returns:
            Selected TIF path or None if cancelled.
        """
        from utils.config import get_output_dir

        output_dir = get_output_dir()
        hillshades_dir = output_dir / "hillshades"

        if not hillshades_dir.exists():
            return None

        # Only this viewer's current AOI - see the matching comment in
        # _show_kmz_export_dialog for why (the folder can hold leftover
        # files from a previous, unrelated location).
        current_stem = self.dem_path.stem
        tif_files = sorted(
            (p for p in hillshades_dir.glob("*.tif") if p.stem.startswith(f"{current_stem}_")),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if not tif_files:
            messagebox.showwarning(
                "No Files",
                "No hillshade TIF files found in output directory.",
                parent=self.window
            )
            return None

        dialog = tk.Toplevel(self.window)
        dialog.title("Export to TIF")
        dialog.geometry("520x420")
        dialog.transient(self.window)
        dialog.grab_set()

        dialog.update_idletasks()
        parent_x = self.window.winfo_x()
        parent_y = self.window.winfo_y()
        parent_width = self.window.winfo_width()
        parent_height = self.window.winfo_height()
        dialog_width = dialog.winfo_width()
        dialog_height = dialog.winfo_height()
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        dialog.geometry(f"+{x}+{y}")

        selected_path: Path | None = None

        main_frame = ttk.Frame(dialog, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            main_frame,
            text="Select a hillshade to export:",
            font=("", 11, "bold")
        ).pack(anchor=tk.W, pady=(0, 10))

        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        canvas = tk.Canvas(
            list_frame,
            height=120,
            bg="white",
            highlightthickness=1,
            highlightbackground="gray",
            relief=tk.SUNKEN,
            borderwidth=2
        )
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="white")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        current_str = str(self.current_hillshade)
        if not any(str(p) == current_str for p in tif_files):
            current_str = str(tif_files[0])
        selected_var = tk.StringVar(value=current_str)

        for tif_file in tif_files:
            tif_name = tif_file.stem
            if "_classic" in tif_name:
                display_name = "Classic.tif"
            elif "_arch_" in tif_name:
                style_part = tif_name.split("_arch_")[-1].replace("_", " ")
                display_name = f"{style_part}.tif"
            elif "_custom" in tif_name:
                display_name = "Custom.tif"
            else:
                display_name = tif_file.name

            rb = tk.Radiobutton(
                scrollable_frame,
                text=display_name,
                variable=selected_var,
                value=str(tif_file),
                bg="white",
                activebackground="white",
                highlightthickness=0
            )
            rb.pack(anchor=tk.W, padx=5, pady=2)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(0, 0))

        def on_export():
            nonlocal selected_path
            selected_path = Path(selected_var.get())
            if not selected_path.exists():
                messagebox.showwarning(
                    "Missing File",
                    "Selected hillshade file no longer exists.",
                    parent=dialog
                )
                return

            save_path = filedialog.asksaveasfilename(
                parent=dialog,
                title="Save Hillshade TIF",
                defaultextension=".tif",
                filetypes=[("GeoTIFF files", "*.tif"), ("All files", "*.*")],
                initialfile=selected_path.name
            )
            if not save_path:
                return

            try:
                shutil.copy2(selected_path, save_path)
            except Exception as e:
                messagebox.showerror(
                    "Export Failed",
                    f"Failed to export TIF:\n\n{e}",
                    parent=dialog
                )
                return

            dialog.destroy()
            messagebox.showinfo(
                "Export Complete",
                f"Hillshade saved to:\n{save_path}",
                parent=self.window
            )

        def on_close():
            dialog.destroy()

        ttk.Button(btn_frame, text="Export", command=on_export, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=on_close, width=12).pack(side=tk.LEFT, padx=5)

        dialog.protocol("WM_DELETE_WINDOW", on_close)
        dialog.wait_window()

        return selected_path

    def _on_export_kmz(self):
        """Handle Export to KMZ button click."""
        # Show file selection dialog
        result = self._show_kmz_export_dialog()
        if not result:
            return

        selected_files, description = result

        # Generate default filename from coordinates and area
        # Format: lat_lon_sqmi.kmz
        # Metadata is nested in result dict
        meta = self.metadata.get('metadata', {})
        lat = meta.get('center_lat', 0)
        lon = meta.get('center_lon', 0)
        sqmi = meta.get('size_sqmi', 0)

        # Format coordinates to 4 decimal places
        default_name = f"{lat:.4f}_{lon:.4f}_{sqmi}sqmi.kmz"

        # Ask for save location
        save_path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Save KMZ File",
            defaultextension=".kmz",
            filetypes=[("KMZ files", "*.kmz"), ("All files", "*.*")],
            initialfile=default_name
        )

        if not save_path:
            return

        # Create progress dialog
        progress_dialog = tk.Toplevel(self.window)
        progress_dialog.title("Exporting KMZ")
        progress_dialog.geometry("450x150")
        progress_dialog.transient(self.window)
        progress_dialog.grab_set()
        progress_dialog.resizable(False, False)

        # Center on parent
        progress_dialog.update_idletasks()
        parent_x = self.window.winfo_x()
        parent_y = self.window.winfo_y()
        parent_width = self.window.winfo_width()
        parent_height = self.window.winfo_height()
        dialog_width = progress_dialog.winfo_width()
        dialog_height = progress_dialog.winfo_height()
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        progress_dialog.geometry(f"+{x}+{y}")

        # Progress dialog contents
        frame = ttk.Frame(progress_dialog, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        status_label = ttk.Label(frame, text="Preparing export...", font=("", 10))
        status_label.pack(pady=(0, 10))

        progress_var = tk.DoubleVar()
        progress_bar = ttk.Progressbar(
            frame,
            variable=progress_var,
            maximum=100,
            mode='determinate',
            length=400
        )
        progress_bar.pack(pady=(0, 10))

        file_label = ttk.Label(frame, text="", font=("", 9), foreground="gray")
        file_label.pack()

        # Export files in background thread
        import threading

        error_message = [None]  # Use list to allow modification in nested function

        def export_worker():
            from lidar_core.kmz_export import tifs_to_kmz

            def progress_callback(current, total):
                # Update UI with progress
                if total > 0:
                    pct = (current / total) * 100
                    if current < len(selected_files):
                        current_file = selected_files[current].name
                    else:
                        current_file = "Finalizing..."

                    progress_dialog.after(0, lambda: (
                        status_label.config(text=f"Processing {current + 1} of {total}..."),
                        file_label.config(text=current_file),
                        progress_var.set(pct)
                    ))

            try:
                # Create document name with coordinates
                meta = self.metadata.get('metadata', {})
                doc_lat = meta.get('center_lat', 0)
                doc_lon = meta.get('center_lon', 0)
                doc_sqmi = meta.get('size_sqmi', 0)
                document_name = f"{doc_lat:.4f}, {doc_lon:.4f} {doc_sqmi}sqmi"

                # Export all files to single KMZ
                tifs_to_kmz(
                    selected_files,
                    Path(save_path),
                    description=description,
                    document_name=document_name,
                    progress_callback=progress_callback
                )

                # Update to 100%
                progress_dialog.after(0, lambda: (
                    status_label.config(text="Export complete!"),
                    file_label.config(text=f"{len(selected_files)} layer(s) exported"),
                    progress_var.set(100)
                ))

            except Exception as e:
                # str(e) can be empty for some exceptions (e.g. bare OSError
                # subclasses raised by native GDAL/PROJ bindings), which
                # would otherwise show a blank, undiagnosable error dialog.
                # Fall back to the exception type name and include a
                # traceback in the log so reported failures are actionable.
                import traceback
                detail = str(e).strip() or repr(e)
                error_message[0] = f"{type(e).__name__}: {detail}"
                traceback.print_exc()

            # Close dialog after brief delay
            import time
            time.sleep(0.5)
            progress_dialog.after(0, progress_dialog.destroy)

        thread = threading.Thread(target=export_worker, daemon=True)
        thread.start()

        # Wait for dialog to close
        self.window.wait_window(progress_dialog)

        # Show results
        if error_message[0]:
            messagebox.showerror(
                "Export Failed",
                f"Failed to export KMZ:\n\n{error_message[0]}",
                parent=self.window
            )
        else:
            messagebox.showinfo(
                "Success",
                f"KMZ file with {len(selected_files)} layer(s) saved to:\n{save_path}",
                parent=self.window
            )

    def _on_export_tif(self):
        """Handle Export to TIF button click."""
        self._show_tif_export_dialog()

    def _on_window_close(self):
        """Handle window close event with confirmation dialog."""
        # Show confirmation dialog
        result = messagebox.askyesno(
            "Return to Main Menu",
            "Return to coordinate input?\n\n"
            "Note: Generated hillshades will be deleted unless exported to KMZ.",
            parent=self.window
        )

        if result:
            # User confirmed - close viewer
            self.window.destroy()

            # Call callback to restore main window
            if self.on_close_callback:
                self.on_close_callback()

    # ============================================================================
    # Canvas Transform & Display
    # ============================================================================

    def canvas_to_image(self, cx: float, cy: float) -> tuple[float, float]:
        """Convert canvas coordinates to image pixel coordinates."""
        x = (cx - self.offset_x) / self.scale
        y = (cy - self.offset_y) / self.scale
        return (x, y)

    def image_to_canvas(self, px: float, py: float) -> tuple[float, float]:
        """Convert image pixel coordinates to canvas coordinates."""
        cx = self.offset_x + px * self.scale
        cy = self.offset_y + py * self.scale
        return (cx, cy)

    def _reset_view_to_fit(self):
        """Center and fit the image in the canvas."""
        if not self._pil_image or not self.canvas:
            return

        # Force geometry update
        self.window.update_idletasks()

        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()

        # Fallback if canvas not sized yet
        if cw <= 1 or ch <= 1:
            cw = 800
            ch = 600

        # Ensure minimum reasonable size
        cw = max(cw, 400)
        ch = max(ch, 300)

        iw, ih = self._pil_image.size

        # Calculate scale to fit
        sx = cw / iw
        sy = ch / ih
        self.scale = max(self.min_scale, min(self.max_scale, min(sx, sy) * 0.95))

        # Center the image
        self.offset_x = (cw - iw * self.scale) / 2
        self.offset_y = (ch - ih * self.scale) / 2

        self.redraw()

    def redraw(self, resample=Image.Resampling.BILINEAR):
        """Render only the image region visible inside the canvas viewport."""
        if not self.canvas:
            return

        self.canvas.delete("all")

        if not self._pil_image:
            # Show message
            cw = self.canvas.winfo_width() if self.canvas.winfo_width() > 1 else 800
            ch = self.canvas.winfo_height() if self.canvas.winfo_height() > 1 else 600
            self.canvas.create_text(
                cw / 2, ch / 2,
                text="Loading hillshade...",
                fill="white",
                anchor="center",
                font=("Helvetica", 18)
            )
            return

        iw, ih = self._pil_image.size
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())

        # Convert the canvas viewport into source-image coordinates. One source
        # pixel of padding avoids a visible seam from interpolation at the edge.
        source_left = max(0, int(math.floor((0 - self.offset_x) / self.scale)) - 1)
        source_top = max(0, int(math.floor((0 - self.offset_y) / self.scale)) - 1)
        source_right = min(iw, int(math.ceil((cw - self.offset_x) / self.scale)) + 1)
        source_bottom = min(ih, int(math.ceil((ch - self.offset_y) / self.scale)) + 1)

        if source_right <= source_left or source_bottom <= source_top:
            self._tk_image = None
            return

        render_width = max(1, int(round((source_right - source_left) * self.scale)))
        render_height = max(1, int(round((source_bottom - source_top) * self.scale)))
        visible = self._pil_image.resize(
            (render_width, render_height),
            resample=resample,
            box=(source_left, source_top, source_right, source_bottom),
        )
        self._tk_image = ImageTk.PhotoImage(visible)

        canvas_x = self.offset_x + source_left * self.scale
        canvas_y = self.offset_y + source_top * self.scale
        self.canvas.create_image(canvas_x, canvas_y, image=self._tk_image, anchor="nw")

    def _schedule_interactive_redraw(self, delay_ms: int = 16):
        """Coalesce rapid zoom/pan events into approximately one render per frame."""
        if self._pending_zoom_id is None:
            self._pending_zoom_id = self.window.after(delay_ms, self._run_interactive_redraw)

        if self._quality_redraw_id is not None:
            self.window.after_cancel(self._quality_redraw_id)
        self._quality_redraw_id = self.window.after(140, self._run_quality_redraw)

    def _run_interactive_redraw(self):
        self._pending_zoom_id = None
        self.redraw(Image.Resampling.BILINEAR)

    def _run_quality_redraw(self):
        self._quality_redraw_id = None
        if self._pending_zoom_id is None and not self._panning:
            self.redraw(Image.Resampling.LANCZOS)

    def _on_canvas_configure(self, event):
        """Handle canvas resize."""
        if hasattr(self, '_resize_after_id'):
            self.window.after_cancel(self._resize_after_id)
        self._resize_after_id = self.window.after(100, self.redraw)

    # ============================================================================
    # Zoom & Pan
    # ============================================================================

    def _zoom_button(self, factor: float):
        """Zoom centered on canvas."""
        if not self.canvas or not self._pil_image:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        self.zoom_at(cw / 2, ch / 2, factor)

    def _on_mousewheel(self, event):
        """Update the transform immediately and render at most once per frame."""
        if event.delta == 0:
            return

        factor = 1.12 if event.delta > 0 else 1 / 1.12
        self.zoom_at(event.x, event.y, factor)

    def zoom_at(self, cx: float, cy: float, factor: float):
        """Zoom at specific canvas position."""
        if not self._pil_image or not self.canvas:
            return

        new_scale = self.scale * factor
        new_scale = max(self.min_scale, min(self.max_scale, new_scale))
        if abs(new_scale - self.scale) < 1e-9:
            return

        # Keep point under cursor stationary
        ix, iy = self.canvas_to_image(cx, cy)
        self.scale = new_scale
        self.offset_x = cx - ix * self.scale
        self.offset_y = cy - iy * self.scale
        self._schedule_interactive_redraw()

    def _on_left_press(self, event):
        """Handle left mouse press."""
        if not self.canvas:
            return
        self._left_drag_start = (event.x, event.y)
        self._panning = True
        self.canvas.config(cursor="fleur")

    def _on_left_drag(self, event):
        """Handle left mouse drag (pan)."""
        if not self.canvas or self._left_drag_start is None or not self._panning:
            return

        sx, sy = self._left_drag_start
        dx = event.x - sx
        dy = event.y - sy

        self.offset_x += dx
        self.offset_y += dy
        self.canvas.move("all", dx, dy)
        self._schedule_interactive_redraw(delay_ms=32)
        self._left_drag_start = (event.x, event.y)

    def _on_left_release(self, event):
        """Handle left mouse release."""
        if not self.canvas:
            return
        self.canvas.config(cursor="")
        self._left_drag_start = None
        self._panning = False
        if self._pending_zoom_id is not None:
            self.window.after_cancel(self._pending_zoom_id)
            self._pending_zoom_id = None
        if self._quality_redraw_id is not None:
            self.window.after_cancel(self._quality_redraw_id)
            self._quality_redraw_id = None
        self.redraw(Image.Resampling.LANCZOS)
