#!/usr/bin/env python3
"""
Progress dialog for LiDAR Hillshade Explorer.

Shows user-friendly step names, progress bars, elapsed time, and cancel button.
Thread-safe updates via tkinter's .after() method.
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox


class ProgressDialog:
    """
    Modal progress dialog for pipeline processing.

    Shows user-friendly step names, progress bars, elapsed time, and cancel button.
    Thread-safe updates via tkinter's .after() method.
    """

    def __init__(self, parent: tk.Tk, title: str = "Generating Hillshade",
                 initial_step: int = 1, total_steps: int = 4,
                 initial_message: str = "Finding LiDAR datasets..."):
        self.parent = parent
        self.cancelled = threading.Event()
        self.start_time = None

        # Create modal dialog
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # Center on parent
        self.dialog.geometry("500x250")
        self._center_on_parent()

        # Prevent window close via X button (must use Cancel)
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel_clicked)

        # Main frame with padding
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Current step label
        self.step_label = ttk.Label(
            main_frame,
            text=initial_message,
            font=("", 11, "bold")
        )
        self.step_label.pack(pady=(0, 10))

        # Step progress bar (indeterminate animation)
        self.step_progress = ttk.Progressbar(
            main_frame,
            mode="indeterminate",
            length=450
        )
        self.step_progress.pack(pady=(0, 15))

        # Overall progress label
        self.overall_label = ttk.Label(
            main_frame,
            text=f"Step {initial_step} of {total_steps}"
        )
        self.overall_label.pack(pady=(0, 5))

        # Overall progress bar (determinate 0-100%)
        self.overall_progress = ttk.Progressbar(
            main_frame,
            mode="determinate",
            length=450,
            maximum=100
        )
        self.overall_progress.pack(pady=(0, 15))

        # Time elapsed label
        self.time_label = ttk.Label(
            main_frame,
            text="Elapsed: 0s"
        )
        self.time_label.pack(pady=(0, 15))

        # Cancel button
        self.cancel_btn = ttk.Button(
            main_frame,
            text="Cancel",
            command=self._on_cancel_clicked,
            width=15
        )
        self.cancel_btn.pack()

        # Start indeterminate progress animation
        self.step_progress.start(10)

        # Start elapsed time counter
        self.start_time = threading.Event()
        self._update_elapsed_time()

    def _center_on_parent(self):
        """Center dialog on parent window."""
        self.dialog.update_idletasks()
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_w = self.parent.winfo_width()
        parent_h = self.parent.winfo_height()

        dialog_w = self.dialog.winfo_width()
        dialog_h = self.dialog.winfo_height()

        x = parent_x + (parent_w - dialog_w) // 2
        y = parent_y + (parent_h - dialog_h) // 2

        self.dialog.geometry(f"+{x}+{y}")

    def _update_elapsed_time(self):
        """Update elapsed time display (called periodically)."""
        if not self.start_time.is_set():
            # Not started yet
            self.dialog.after(100, self._update_elapsed_time)
            return

        if not hasattr(self, '_start_timestamp'):
            # First update after start
            import time
            self._start_timestamp = time.time()

        try:
            if self.dialog.winfo_exists():
                import time
                elapsed = int(time.time() - self._start_timestamp)
                minutes = elapsed // 60
                seconds = elapsed % 60
                if minutes > 0:
                    self.time_label.config(text=f"Elapsed: {minutes}m {seconds}s")
                else:
                    self.time_label.config(text=f"Elapsed: {seconds}s")

                # Schedule next update
                self.dialog.after(1000, self._update_elapsed_time)
        except tk.TclError:
            # Dialog was destroyed
            pass

    def start(self):
        """Mark processing as started (begins elapsed time counter)."""
        self.start_time.set()

    def update_step(self, step_num: int, total_steps: int, message: str):
        """
        Update progress display (thread-safe).

        Args:
            step_num: Current step number (1-based)
            total_steps: Total number of steps
            message: User-friendly message for current step
        """
        def _update():
            try:
                if self.dialog.winfo_exists():
                    self.step_label.config(text=message)
                    self.overall_label.config(text=f"Step {step_num} of {total_steps}")
                    progress_pct = (step_num - 1) / total_steps * 100
                    self.overall_progress.config(value=progress_pct)
            except tk.TclError:
                pass

        self.dialog.after(0, _update)

    def complete_step(self, step_num: int, total_steps: int):
        """Mark current step as complete."""
        def _update():
            try:
                if self.dialog.winfo_exists():
                    progress_pct = step_num / total_steps * 100
                    self.overall_progress.config(value=progress_pct)
            except tk.TclError:
                pass

        self.dialog.after(0, _update)

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        result = messagebox.askyesno(
            "Cancel Processing",
            "Are you sure you want to cancel?\n\nPartial results may be incomplete.",
            parent=self.dialog
        )
        if result:
            self.cancelled.set()
            self.close()

    def is_cancelled(self) -> bool:
        """Check if user cancelled operation."""
        return self.cancelled.is_set()

    def close(self):
        """Close the dialog (thread-safe)."""
        def _close():
            try:
                if self.dialog.winfo_exists():
                    self.step_progress.stop()
                    self.dialog.grab_release()
                    self.dialog.destroy()
            except tk.TclError:
                pass

        self.dialog.after(0, _close)
