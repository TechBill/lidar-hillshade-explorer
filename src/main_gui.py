#!/usr/bin/env python3
"""
Main GUI for LiDAR Hillshade Explorer.

Simple input form for coordinates, AOI size, and smart selection.
One-button workflow to generate hillshade and open viewer.
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import webbrowser

from utils.config import (
    TERRAIN_STYLE_CONTINUOUS,
    TERRAIN_STYLE_CUSTOM,
    TERRAIN_STYLE_LABELS,
    TERRAIN_STYLE_PRESERVE,
    get_default_config,
    load_config,
    normalize_terrain_style,
    save_config,
)
from utils.progress import ProgressDialog
from processing import ProcessingOrchestrator


class HillshadeExplorerApp:
    """Main application window for LiDAR Hillshade Explorer."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()

        # Create processing orchestrator
        self.orchestrator = ProcessingOrchestrator()

        # Processing state
        self.is_processing = False
        self.progress_dialog = None
        self.worker_thread = None

        # Log window state
        self.log_window = None
        self.show_log_var = tk.BooleanVar(
            value=self.config.get("preferences", {}).get("show_log", False)
        )
        self.log_messages = []

        # Build UI
        self._build_ui()

        # Load last location from config
        self._load_last_location()

        if self.show_log_var.get():
            self._show_log_window()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """Build the main UI layout."""
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Donation section
        donation_frame = ttk.Frame(main_frame)
        donation_frame.pack(pady=(0, 20))

        ttk.Label(
            donation_frame,
            text="If you find this useful, please consider donating!",
            font=("", 10)
        ).pack(pady=(0, 8))

        # PayPal button (Frame + Label approach for macOS compatibility)
        paypal_frame = tk.Frame(donation_frame, bg="#0070BA", bd=1, relief="raised", cursor="hand2")
        paypal_frame.pack(pady=(0, 0))

        paypal_label = tk.Label(
            paypal_frame,
            text="Donate on PayPal",
            bg="#0070BA",
            fg="white",
            font=("", 10, "bold"),
            padx=20,
            pady=8
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

        # Location section
        location_frame = ttk.LabelFrame(main_frame, text="Enter Location", padding=10)
        location_frame.pack(fill=tk.X, pady=(0, 10))

        # Latitude
        lat_frame = ttk.Frame(location_frame)
        lat_frame.pack(fill=tk.X, pady=5)
        ttk.Label(lat_frame, text="Latitude:", width=12).pack(side=tk.LEFT)
        self.lat_var = tk.StringVar(value="")
        lat_entry = ttk.Entry(lat_frame, textvariable=self.lat_var, width=20)
        lat_entry.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(lat_frame, text="(e.g., 37.1032)", font=("", 9)).pack(side=tk.LEFT, padx=(5, 0))

        # Longitude
        lon_frame = ttk.Frame(location_frame)
        lon_frame.pack(fill=tk.X, pady=5)
        ttk.Label(lon_frame, text="Longitude:", width=12).pack(side=tk.LEFT)
        self.lon_var = tk.StringVar(value="")
        lon_entry = ttk.Entry(lon_frame, textvariable=self.lon_var, width=20)
        lon_entry.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(lon_frame, text="(e.g., -90.4558)", font=("", 9)).pack(side=tk.LEFT, padx=(5, 0))

        # Paste Coordinates button
        paste_btn = ttk.Button(location_frame, text="Paste Coordinates", command=self._paste_coordinates)
        paste_btn.pack(pady=(10, 5))

        # Area Size section
        size_frame = ttk.LabelFrame(main_frame, text="Area Size", padding=10)
        size_frame.pack(fill=tk.X, pady=(0, 10))

        self.size_var = tk.StringVar(value="0.25")
        self.custom_size_var = tk.StringVar(value="")

        ttk.Radiobutton(
            size_frame,
            text="Small (0.25 sq mi)",
            variable=self.size_var,
            value="0.25"
        ).pack(anchor=tk.W, pady=2)

        ttk.Radiobutton(
            size_frame,
            text="Medium (0.5 sq mi)",
            variable=self.size_var,
            value="0.5"
        ).pack(anchor=tk.W, pady=2)

        ttk.Radiobutton(
            size_frame,
            text="Large (1.0 sq mi)",
            variable=self.size_var,
            value="1.0"
        ).pack(anchor=tk.W, pady=2)

        custom_size_row = ttk.Frame(size_frame)
        custom_size_row.pack(fill=tk.X, pady=(4, 0))

        ttk.Radiobutton(
            custom_size_row,
            text="Custom",
            variable=self.size_var,
            value="custom",
            command=self._on_area_size_changed
        ).pack(side=tk.LEFT)

        self.custom_size_entry = ttk.Entry(
            custom_size_row,
            textvariable=self.custom_size_var,
            width=12,
            state=tk.DISABLED
        )
        self.custom_size_entry.pack(side=tk.LEFT, padx=(10, 5))

        ttk.Label(custom_size_row, text="sq mi", font=("", 9)).pack(side=tk.LEFT)

        # Terrain Style section
        terrain_frame = ttk.LabelFrame(main_frame, text="Terrain Style", padding=10)
        terrain_frame.pack(fill=tk.X, pady=(0, 10))

        self._terrain_label_to_key = {
            label: key for key, label in TERRAIN_STYLE_LABELS.items()
        }
        initial_style = normalize_terrain_style(
            self.config.get("preferences", {}).get("terrain_style")
        )
        self.terrain_style_var = tk.StringVar(
            value=TERRAIN_STYLE_LABELS[initial_style]
        )
        terrain_combo = ttk.Combobox(
            terrain_frame,
            textvariable=self.terrain_style_var,
            values=list(TERRAIN_STYLE_LABELS.values()),
            state="readonly",
            width=34,
        )
        terrain_combo.pack(anchor=tk.W)

        self.terrain_style_help = ttk.Label(
            terrain_frame,
            text="",
            font=("", 9),
            foreground="gray",
            wraplength=430,
            justify=tk.LEFT,
        )
        self.terrain_style_help.pack(anchor=tk.W, pady=(5, 0))
        terrain_combo.bind("<<ComboboxSelected>>", self._on_terrain_style_changed)
        self._on_terrain_style_changed()

        # Dataset Selection section
        dataset_frame = ttk.LabelFrame(main_frame, text="Dataset Selection", padding=10)
        dataset_frame.pack(fill=tk.X, pady=(0, 10))

        self.smart_select_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            dataset_frame,
            text="Smart Selection (automatically find best dataset)",
            variable=self.smart_select_var
        ).pack(anchor=tk.W)

        ttk.Label(
            dataset_frame,
            text="When enabled, the app will try older datasets if the newest has insufficient data.",
            font=("", 9),
            foreground="gray"
        ).pack(anchor=tk.W, pady=(5, 0))

        # Generate button
        self.generate_btn = ttk.Button(
            main_frame,
            text="Generate Hillshade",
            command=self._on_generate_clicked,
            width=30
        )
        self.generate_btn.pack(pady=20)

        # Advanced Settings button (bottom left)
        settings_btn = ttk.Button(
            main_frame,
            text="Advanced Settings",
            command=self._show_advanced_settings
        )
        settings_btn.pack(side=tk.LEFT, pady=(0, 0))

        self.size_var.trace_add("write", self._on_area_size_changed)
        self._on_area_size_changed()

    def _on_area_size_changed(self, *args):
        """Enable or disable the custom area size entry."""
        custom_selected = self.size_var.get() == "custom"
        self.custom_size_entry.config(state=tk.NORMAL if custom_selected else tk.DISABLED)

    def _selected_terrain_style(self) -> str:
        """Return the configuration key for the selected terrain style."""
        return self._terrain_label_to_key.get(
            self.terrain_style_var.get(), TERRAIN_STYLE_CONTINUOUS
        )

    def _on_terrain_style_changed(self, *_args):
        """Update terrain-style help and persist the user's selection."""
        style = self._selected_terrain_style()
        help_text = {
            TERRAIN_STYLE_CONTINUOUS: (
                "Fills building footprints and small gaps for a continuous "
                "bare-earth hillshade."
            ),
            TERRAIN_STYLE_PRESERVE: (
                "Preserves larger areas without reliable ground returns, which "
                "may appear black."
            ),
            TERRAIN_STYLE_CUSTOM: (
                "Uses the TIN and fill values configured in Advanced Settings."
            ),
        }
        self.terrain_style_help.config(text=help_text[style])
        self.config.setdefault("preferences", {})["terrain_style"] = style

    def _load_last_location(self):
        """Load last used location from config."""
        # Never load coordinates - always start with blank inputs
        # Only load size preference and other settings
        last_loc = self.config.get("last_location", {})
        if last_loc:
            size_sqmi = last_loc.get("size_sqmi", 0.25)
            if size_sqmi in [0.25, 0.5, 1.0]:
                self.size_var.set(str(size_sqmi))
            elif size_sqmi:
                self.size_var.set("custom")
                self.custom_size_var.set(str(size_sqmi))

        # Always start with Smart Selection enabled (ignore saved preference)
        self.smart_select_var.set(True)

    def _get_selected_size_sqmi(self) -> float:
        """Return the currently selected area size in square miles."""
        selected = self.size_var.get()

        if selected == "custom":
            custom_value = self.custom_size_var.get().strip()
            if not custom_value:
                raise ValueError("Enter a custom area size in square miles")

            size_sqmi = float(custom_value)
            if size_sqmi <= 0:
                raise ValueError("Custom area size must be greater than 0")
            return size_sqmi

        size_sqmi = float(selected)
        if size_sqmi <= 0:
            raise ValueError("Area size must be greater than 0")
        return size_sqmi

    def _save_current_location(self):
        """Save current location to config."""
        try:
            lat = float(self.lat_var.get())
            lon = float(self.lon_var.get())
            size_sqmi = self._get_selected_size_sqmi()

            self.config["last_location"] = {
                "lat": lat,
                "lon": lon,
                "size_sqmi": size_sqmi
            }
            self.config["preferences"]["smart_select"] = self.smart_select_var.get()
            self.config["preferences"]["terrain_style"] = self._selected_terrain_style()

            save_config(self.config)
        except Exception as e:
            print(f"Warning: Could not save location ({e})")

    def _paste_coordinates(self):
        """Parse coordinates from clipboard and populate lat/lon fields."""
        import re

        try:
            # Get text from clipboard
            clipboard_text = self.root.clipboard_get().strip()

            if not clipboard_text:
                messagebox.showinfo("Empty clipboard", "Clipboard is empty.", parent=self.root)
                return

            # Check if it's KML/XML data (Google Earth placemark)
            if '<coordinates>' in clipboard_text and '</coordinates>' in clipboard_text:
                # Extract coordinates from KML
                coord_match = re.search(r'<coordinates>([-\d.,\s]+)</coordinates>', clipboard_text)
                if coord_match:
                    coords = coord_match.group(1).strip()
                    # KML format is: longitude,latitude,altitude
                    parts = coords.split(',')
                    if len(parts) >= 2:
                        try:
                            lon = float(parts[0].strip())
                            lat = float(parts[1].strip())
                            # Set the values (note: KML has lon first, then lat)
                            self.lat_var.set(str(lat))
                            self.lon_var.set(str(lon))
                            return
                        except ValueError:
                            pass

            # Try to parse coordinates - handle both comma and space separators
            # Remove multiple spaces and normalize
            text = clipboard_text.replace(',', ' ')
            parts = text.split()

            if len(parts) >= 2:
                # First part is latitude, second is longitude
                lat_str = parts[0].strip()
                lon_str = parts[1].strip()

                # Validate they are numbers
                try:
                    float(lat_str)
                    float(lon_str)

                    # Set the values
                    self.lat_var.set(lat_str)
                    self.lon_var.set(lon_str)

                except ValueError:
                    messagebox.showerror("Invalid format",
                        "Could not parse coordinates.\n\n"
                        "Expected format:\n"
                        "37.1032, -90.4558\n"
                        "or\n"
                        "37.1032 -90.4558\n"
                        "or Google Earth KML placemark",
                        parent=self.root)
            else:
                messagebox.showerror("Invalid format",
                    "Could not find two coordinate values.\n\n"
                    "Expected format:\n"
                    "37.1032, -90.4558\n"
                    "or\n"
                    "37.1032 -90.4558\n"
                    "or Google Earth KML placemark",
                    parent=self.root)

        except tk.TclError:
            messagebox.showerror("Clipboard error", "Could not access clipboard.", parent=self.root)
        except Exception as e:
            messagebox.showerror("Error", f"Could not paste coordinates: {e}", parent=self.root)

    def _show_dataset_selection_dialog(self, datasets: list[dict]) -> int | None:
        """
        Show dialog for user to select from available datasets.

        Args:
            datasets: List of dataset dicts with keys: id, props

        Returns:
            Selected dataset rank (index), or None if cancelled
        """
        # Create dialog window
        dialog = tk.Toplevel(self.root)
        dialog.title("Select LiDAR Dataset")
        dialog.geometry("600x400")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center dialog on parent window
        dialog.update_idletasks()
        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()
        dialog_width = dialog.winfo_width()
        dialog_height = dialog.winfo_height()
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        dialog.geometry(f"+{x}+{y}")

        # Result variable
        selected_rank = None

        # Label
        ttk.Label(
            dialog,
            text="Multiple LiDAR datasets found. Please select one:",
            font=("", 10, "bold")
        ).pack(pady=(10, 5), padx=10)

        # Listbox with scrollbar
        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, font=("", 10))
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=listbox.yview)

        # Populate listbox with datasets (ID only, no year)
        for idx, ds in enumerate(datasets):
            ds_id = ds.get('id', 'Unknown')
            props = ds.get("props", {})
            details = []
            year = props.get("collection_year")
            if year:
                year_label = f"~{year}" if props.get("year_estimated") else str(year)
                details.append(f"Collected {year_label}")
            quality = props.get("quality_level")
            if quality:
                details.append(f"QL{quality}")
            resolution = props.get("dem_gsd_meters")
            if resolution:
                details.append(f"{resolution} m")
            detail_text = f" — {' — '.join(details)}" if details else ""
            display_text = f"{idx + 1}. {ds_id}{detail_text}"
            listbox.insert(tk.END, display_text)

        # Select first item by default
        listbox.selection_set(0)

        # Button frame
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)

        def on_ok():
            nonlocal selected_rank
            selection = listbox.curselection()
            if selection:
                selected_rank = selection[0]
                dialog.destroy()

        def on_cancel():
            nonlocal selected_rank
            selected_rank = None
            dialog.destroy()

        ttk.Button(btn_frame, text="OK", command=on_ok, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel, width=10).pack(side=tk.LEFT, padx=5)

        # Handle window close
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)

        # Wait for dialog to close
        dialog.wait_window()

        return selected_rank

    def _on_generate_clicked(self):
        """Handle Generate Hillshade button click."""
        if self.is_processing:
            messagebox.showwarning(
                "Processing",
                "Processing is already in progress. Please wait.",
                parent=self.root
            )
            return

        # Validate inputs
        lat_str = self.lat_var.get().strip()
        lon_str = self.lon_var.get().strip()

        # Check if coordinates are empty
        if not lat_str or not lon_str:
            messagebox.showerror(
                "Missing Coordinates",
                "Please enter or paste your latitude and longitude coordinates.\n\n"
                "You can use the 'Paste Coordinates' button to quickly fill both fields.",
                parent=self.root
            )
            return

        try:
            lat = float(lat_str)
            lon = float(lon_str)
            size_sqmi = self._get_selected_size_sqmi()

            # Validate ranges
            if not (-90 <= lat <= 90):
                raise ValueError("Latitude must be between -90 and 90")
            if not (-180 <= lon <= 180):
                raise ValueError("Longitude must be between -180 and 180")
            if size_sqmi <= 0:
                raise ValueError("Area size must be greater than 0")

        except ValueError as e:
            messagebox.showerror(
                "Invalid Input",
                f"Please check your input:\n\n{e}",
                parent=self.root
            )
            return

        # Save location for next time
        self._save_current_location()

        # Get smart selection preference
        smart_select = self.smart_select_var.get()

        # If smart selection is OFF, let user choose from available datasets
        selected_dataset_rank = 0
        if not smart_select:
            try:
                datasets = self.orchestrator.get_available_datasets(lat, lon, size_sqmi)

                if not datasets:
                    messagebox.showerror(
                        "No Data Available",
                        "No LiDAR data available for this location.\n\n"
                        "Try a different area or check that you're within the United States.",
                        parent=self.root
                    )
                    return

                if len(datasets) > 1:
                    # Show selection dialog
                    selected_dataset_rank = self._show_dataset_selection_dialog(datasets)
                    if selected_dataset_rank is None:
                        # User cancelled
                        return
            except Exception as e:
                messagebox.showerror(
                    "Error",
                    f"Failed to find datasets:\n\n{e}",
                    parent=self.root
                )
                return

        # Lock UI
        self.is_processing = True
        self.generate_btn.config(state=tk.DISABLED)

        # Create progress dialog
        self.progress_dialog = ProgressDialog(
            self.root,
            title="Generating Hillshade",
            initial_step=1,
            total_steps=4,
            initial_message="Finding LiDAR datasets..."
        )

        # Start processing in background thread
        self.worker_thread = threading.Thread(
            target=self._worker_process,
            args=(lat, lon, size_sqmi, smart_select, selected_dataset_rank),
            daemon=True
        )
        self.worker_thread.start()

    def _worker_process(self, lat: float, lon: float, size_sqmi: float, smart_select: bool, selected_dataset_rank: int = 0):
        """
        Background worker thread for processing.

        Args:
            lat: Latitude
            lon: Longitude
            size_sqmi: Area size in square miles
            smart_select: Enable smart dataset selection with fallback
            selected_dataset_rank: Dataset rank to use (0=newest, 1=second-newest, etc.)
        """
        try:
            # Mark processing as started
            self.progress_dialog.start()

            # Clear log messages for new run
            self.log_messages = []

            # Run the workflow with real-time log callback
            result = self.orchestrator.run_workflow(
                lat=lat,
                lon=lon,
                size_sqmi=size_sqmi,
                smart_select=smart_select,
                progress_callback=self._progress_callback,
                cancel_check=lambda: self.progress_dialog.is_cancelled(),
                selected_dataset_rank=selected_dataset_rank,
                log_callback=self._log_callback
            )

            # Check if cancelled
            if self.progress_dialog.is_cancelled():
                self.root.after(0, self._on_processing_cancelled)
                return

            # Success - open viewer - use default argument to capture result properly
            self.root.after(0, lambda res=result: self._on_processing_complete(res))

        except Exception as e:
            # Error occurred - use default argument to capture e properly
            self.root.after(0, lambda error=e: self._on_processing_error(error))

    def _progress_callback(self, step: int, total_steps: int, message: str):
        """
        Progress callback for orchestrator.

        Args:
            step: Current step number (1-based)
            total_steps: Total number of steps
            message: Progress message
        """
        if self.progress_dialog:
            self.progress_dialog.update_step(step, total_steps, message)

    def _log_callback(self, message: str):
        """
        Log callback for orchestrator - called from worker thread.

        Args:
            message: Log message to add
        """
        # Schedule update on main thread
        self.root.after(0, lambda: self._add_log_message(message))

    def _on_processing_complete(self, result: dict):
        """
        Handle successful processing completion.

        Args:
            result: Processing result dictionary
        """
        # Close progress dialog
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        # Unlock UI
        self.is_processing = False
        self.generate_btn.config(state=tk.NORMAL)

        # Open viewer
        self._open_viewer(result)

    def _on_processing_cancelled(self):
        """Handle processing cancellation."""
        # Close progress dialog
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        # Unlock UI
        self.is_processing = False
        self.generate_btn.config(state=tk.NORMAL)

    def _on_processing_error(self, error: Exception):
        """
        Handle processing error.

        Args:
            error: Exception that occurred
        """
        # Close progress dialog
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        # Unlock UI
        self.is_processing = False
        self.generate_btn.config(state=tk.NORMAL)

        # Show error message
        error_msg = str(error)
        if "No LiDAR coverage" in error_msg or "No data" in error_msg:
            messagebox.showerror(
                "No Data Available",
                "No LiDAR data available for this location.\n\n"
                "Try a different area or check that you're within the United States.",
                parent=self.root
            )
        elif "network" in error_msg.lower() or "connection" in error_msg.lower():
            messagebox.showerror(
                "Network Error",
                "Cannot download data. Please check your internet connection and try again.",
                parent=self.root
            )
        elif "pdal" in error_msg.lower():
            messagebox.showerror(
                "PDAL Not Found",
                "PDAL is required but not found.\n\n"
                "Please install PDAL (for example via QGIS or Homebrew) and set it in Advanced Settings if needed.",
                parent=self.root
            )
        elif "gdal" in error_msg.lower():
            messagebox.showerror(
                "GDAL Not Found",
                "GDAL is required but not found.\n\n"
                "Please install GDAL (typically via QGIS installation).",
                parent=self.root
            )
        else:
            messagebox.showerror(
                "Processing Error",
                f"An error occurred during processing:\n\n{error_msg}",
                parent=self.root
            )

    def _open_viewer(self, result: dict):
        """
        Open hillshade viewer with result.

        Args:
            result: Processing result dictionary with paths and metadata
        """
        try:
            from hillshade_viewer import HillshadeViewer

            # Hide main window while viewer is open
            self.root.withdraw()

            viewer = HillshadeViewer(
                parent=self.root,
                dem_path=Path(result['dem']),
                initial_hillshade=Path(result['hillshade']),
                metadata=result,
                on_close_callback=self._on_viewer_closed
            )
        except Exception as e:
            # Show main window again if viewer fails to open
            self.root.deiconify()
            messagebox.showerror(
                "Viewer Error",
                f"Could not open viewer:\n\n{e}",
                parent=self.root
            )

    def _on_viewer_closed(self):
        """Handle viewer window close - restore main window and clean up files."""
        # Clean up output files for fresh start
        from utils.cleanup import cleanup_output_folder
        cleanup_output_folder()

        self.root.deiconify()

    def _on_close(self):
        """Handle window close event."""
        if self.is_processing:
            result = messagebox.askyesno(
                "Processing in Progress",
                "Processing is still running. Are you sure you want to quit?",
                parent=self.root
            )
            if not result:
                return

        # Save config
        save_config(self.config)

        # Clean up all output files
        from utils.cleanup import cleanup_output_folder
        cleanup_output_folder()

        # Close log window if open
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.destroy()

        # Close window
        self.root.destroy()

    def _show_advanced_settings(self):
        """Show advanced settings dialog for custom binary paths and log settings."""
        # Create dialog window
        dialog = tk.Toplevel(self.root)
        dialog.title("Advanced Settings")
        dialog.geometry("650x760")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        # Center dialog on parent window
        dialog.update_idletasks()
        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()
        dialog_width = dialog.winfo_width()
        dialog_height = dialog.winfo_height()
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        dialog.geometry(f"+{x}+{y}")

        # Main frame
        main_frame = ttk.Frame(dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        ttk.Label(
            main_frame,
            text="Advanced Settings",
            font=("", 12, "bold")
        ).pack(pady=(0, 5))

        # Explanation text
        explanation = ttk.Label(
            main_frame,
            text="Configure custom binary paths and logging options.\n"
                 "When override is checked, the application will use your custom binary\n"
                 "instead of the bundled version. Useful for troubleshooting or testing.",
            font=("", 9),
            foreground="gray",
            justify="center"
        )
        explanation.pack(pady=(0, 15))

        # Binary overrides section
        binary_frame = ttk.LabelFrame(main_frame, text="Custom Binary Paths", padding=15)
        binary_frame.pack(fill=tk.X, pady=(0, 15))

        # Get current settings
        binary_overrides = self.config.get("binary_overrides", {})

        # Store variables for each binary
        binary_vars = {}

        binaries = [
            ("gdaldem", "GDAL DEM"),
            ("pdal", "PDAL")
        ]

        for binary_key, binary_label in binaries:
            # Frame for this binary
            bin_frame = ttk.Frame(binary_frame)
            bin_frame.pack(fill=tk.X, pady=(0, 10))

            # Checkbox and label
            enabled_var = tk.BooleanVar(
                value=binary_overrides.get(binary_key, {}).get("enabled", False)
            )
            path_var = tk.StringVar(
                value=binary_overrides.get(binary_key, {}).get("path", "")
            )

            cb = ttk.Checkbutton(
                bin_frame,
                text=f"Override {binary_label}:",
                variable=enabled_var
            )
            cb.pack(anchor=tk.W, pady=(0, 5))

            # Path entry frame
            path_frame = ttk.Frame(bin_frame)
            path_frame.pack(fill=tk.X, padx=(20, 0))

            path_entry = ttk.Entry(path_frame, textvariable=path_var, width=50)
            path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

            def browse_binary(pvar=path_var, label=binary_label):
                from tkinter import filedialog
                filepath = filedialog.askopenfilename(
                    parent=dialog,
                    title=f"Select {label} Binary"
                )
                if filepath:
                    pvar.set(filepath)

            ttk.Button(
                path_frame,
                text="Browse...",
                command=browse_binary,
                width=10
            ).pack(side=tk.LEFT)

            binary_vars[binary_key] = {"enabled": enabled_var, "path": path_var}

        # Log settings section
        log_frame = ttk.LabelFrame(main_frame, text="Logging", padding=15)
        log_frame.pack(fill=tk.X, pady=(0, 15))

        show_log_cb = ttk.Checkbutton(
            log_frame,
            text="Show Log Window (for troubleshooting)",
            variable=self.show_log_var,
            command=self._toggle_log_window
        )
        show_log_cb.pack(anchor=tk.W)

        ttk.Label(
            log_frame,
            text="When enabled, a log window will display processing details that can help with troubleshooting.",
            font=("", 9),
            foreground="gray"
        ).pack(anchor=tk.W, pady=(5, 0))

        # DEM fill settings section
        dem_frame = ttk.LabelFrame(main_frame, text="Custom Terrain Settings", padding=15)
        dem_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(
            dem_frame,
            text="These values are used when Terrain Style is set to Custom.",
            font=("", 9),
            foreground="gray",
        ).pack(anchor=tk.W, pady=(0, 10))

        dem_fill = self.config.get("dem_fill", {})

        def _row(label_text: str, var: tk.StringVar, hint: str):
            row = ttk.Frame(dem_frame)
            row.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(row, text=label_text, width=18).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var, width=10).pack(side=tk.LEFT, padx=(5, 10))
            ttk.Label(row, text=hint, font=("", 9), foreground="gray").pack(side=tk.LEFT)

        idw_var = tk.StringVar(value=str(dem_fill.get("idw_window_size", 12)))
        tin_buffer_var = tk.StringVar(value=str(dem_fill.get("tin_buffer_m", 20)))
        tin_edge_var = tk.StringVar(value=str(dem_fill.get("tin_max_edge_multiplier", 12)))
        fill_max_var = tk.StringVar(value=str(dem_fill.get("fill_max_search", 16)))
        smooth_var = tk.StringVar(value=str(dem_fill.get("fill_smoothing", 4)))
        det_var = tk.BooleanVar(value=bool(dem_fill.get("deterministic", False)))

        _row("TIN Buffer:", tin_buffer_var, "0–100 metres (improves AOI edges)")
        _row("TIN Edge Factor:", tin_edge_var, "4–40 × point spacing")
        _row("IDW Window:", idw_var, "3–32 (higher = fewer voids, more smoothing)")
        _row("Fill Max Dist:", fill_max_var, "3–64 (pixels)")
        _row("Fill Smoothing:", smooth_var, "0–10 (iterations)")

        det_row = ttk.Frame(dem_frame)
        det_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Checkbutton(
            det_row,
            text="Deterministic DEM (consistent results, slower)",
            variable=det_var
        ).pack(anchor=tk.W)

        reset_row = ttk.Frame(dem_frame)
        reset_row.pack(fill=tk.X, pady=(6, 0))

        def reset_dem_defaults():
            defaults = get_default_config().get("dem_fill", {})
            tin_buffer_var.set(str(defaults.get("tin_buffer_m", 20)))
            tin_edge_var.set(str(defaults.get("tin_max_edge_multiplier", 12)))
            idw_var.set(str(defaults.get("idw_window_size", 12)))
            fill_max_var.set(str(defaults.get("fill_max_search", 16)))
            smooth_var.set(str(defaults.get("fill_smoothing", 4)))
            det_var.set(bool(defaults.get("deterministic", False)))

        ttk.Button(
            reset_row,
            text="Reset to Defaults",
            command=reset_dem_defaults,
            width=18
        ).pack(anchor=tk.CENTER)

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(10, 0))

        def save_settings():
            # Save binary overrides
            overrides = {}
            for key, vars_dict in binary_vars.items():
                path_value = vars_dict["path"].get().strip()
                if vars_dict["enabled"].get():
                    if not path_value:
                        messagebox.showerror(
                            "Invalid Binary Path",
                            f"Override is enabled for {key}, but no path was provided.",
                            parent=dialog
                        )
                        return False
                    binary_path = Path(path_value)
                    if not binary_path.exists():
                        messagebox.showerror(
                            "Invalid Binary Path",
                            f"The selected path does not exist:\n\n{path_value}",
                            parent=dialog
                        )
                        return False

                overrides[key] = {
                    "enabled": vars_dict["enabled"].get(),
                    "path": path_value
                }
            self.config["binary_overrides"] = overrides

            # Save DEM fill settings with safe fallbacks
            def _int_or(default: int, v: str) -> int:
                try:
                    return int(v)
                except Exception:
                    return default

            dem_fill = {
                "tin_buffer_m": _int_or(20, tin_buffer_var.get()),
                "tin_max_edge_multiplier": _int_or(12, tin_edge_var.get()),
                "idw_window_size": _int_or(12, idw_var.get()),
                "fill_max_search": _int_or(16, fill_max_var.get()),
                "fill_smoothing": _int_or(4, smooth_var.get()),
                "deterministic": bool(det_var.get()),
            }
            self.config["dem_fill"] = dem_fill
            self.config["preferences"]["show_log"] = self.show_log_var.get()
            save_config(self.config)
            return True

        def on_save():
            if not save_settings():
                return
            dialog.destroy()

            messagebox.showinfo(
                "Settings Saved",
                "Advanced settings have been saved.\n\nChanges will take effect on the next processing run.",
                parent=self.root
            )

        def on_close():
            # Save settings when closing with X button or Close button
            if not save_settings():
                return
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        ttk.Button(btn_frame, text="Save", command=on_save, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Close", command=on_close, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel, width=12).pack(side=tk.LEFT, padx=5)

        # Handle window close (X button) - saves settings
        dialog.protocol("WM_DELETE_WINDOW", on_close)

    def _toggle_log_window(self):
        """Toggle log window visibility."""
        if self.show_log_var.get():
            self._show_log_window()
        else:
            self._hide_log_window()

    def _show_log_window(self):
        """Show the log window."""
        if self.log_window and self.log_window.winfo_exists():
            # Window already exists, just raise it
            self.log_window.lift()
            return

        # Create log window
        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("Processing Log")
        self.log_window.geometry("700x500")

        # Position to the right of main window
        self.root.update_idletasks()
        main_x = self.root.winfo_x()
        main_y = self.root.winfo_y()
        main_width = self.root.winfo_width()
        self.log_window.geometry(f"+{main_x + main_width + 10}+{main_y}")

        # Main frame
        frame = ttk.Frame(self.log_window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # Title
        ttk.Label(
            frame,
            text="Processing Log",
            font=("", 11, "bold")
        ).pack(pady=(0, 10))

        # Text widget with scrollbar (white background with border)
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            yscrollcommand=scrollbar.set,
            bg="white",
            fg="black",
            font=("Courier", 11),
            relief=tk.SUNKEN,
            borderwidth=2
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.log_text.yview)

        # Add existing log messages
        for msg in self.log_messages:
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)

        # Buttons frame
        btn_frame = ttk.Frame(frame)
        btn_frame.pack()

        def clear_log():
            self.log_messages = []
            self.log_text.delete("1.0", tk.END)

        ttk.Button(
            btn_frame,
            text="Clear Log",
            command=clear_log,
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="Close",
            command=self._on_log_window_close,
            width=12
        ).pack(side=tk.LEFT, padx=5)

        # Handle window close
        self.log_window.protocol("WM_DELETE_WINDOW", self._on_log_window_close)

    def _hide_log_window(self):
        """Hide the log window."""
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.destroy()
        self.log_window = None

    def _on_log_window_close(self):
        """Handle log window close - uncheck the checkbox."""
        self.show_log_var.set(False)
        self._hide_log_window()

    def _add_log_message(self, message: str):
        """Add a message to the log."""
        self.log_messages.append(message)

        # Update log window if it's open
        if self.log_window and self.log_window.winfo_exists():
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
