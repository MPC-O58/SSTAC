"""
gui_handlers_skymap.py
──────────────────────
SkyMapMixin — Group 2 methods for SkySurveyApp.
Handles: Show Sky Map, Show History Coverage, Save Map, Export N.I.N.A CSV.
Extracted from gui.py for independent maintenance.
"""

import traceback

from tkinter import messagebox
import matplotlib.pyplot as plt

from astro_utils import utc_to_local_dt, format_ra_dec
from sky_map import build_sky_map_figure, save_sky_map, build_history_coverage_figure
from history_utils import load_observed_history_rows


class SkyMapMixin:
    """Mixin that provides Group 2 – Sky Map & Export actions for SkySurveyApp."""

    # ══════════════════════════════════════════
    #  Group 2 – SKY MAP & EXPORT
    # ══════════════════════════════════════════

    def on_show_map(self):
        if not self.last_selected:
            return messagebox.showwarning('Warning', 'Please generate a plan first!')
        try:
            loc_name = self.loc_var.get()
            self.log_write(f"Rendering Full Sky Map for {loc_name}...")
            fig, ax = build_sky_map_figure(
                self.last_selected, self.last_mode, self.last_date,
                self.utc_offset, self.fov_deg, self.neocp_objects,
                self.location, location_name=loc_name)
            self.current_fig = fig
            plt.show()
        except Exception:
            self.log_write('=== MAP RENDER ERROR ===')
            self.log_write(traceback.format_exc())
            messagebox.showerror('Map Error', 'Failed to render Sky Map. Check System Console.')

    def on_show_history_coverage(self):
        """Display a Mollweide map of OBSERVED fields from performance_log only."""
        try:
            rows = load_observed_history_rows(self.output_dir)
            if not rows:
                return messagebox.showinfo(
                    "History Coverage",
                    "No OBSERVED fields found in the performance log yet.\n\n"
                    "Tip: Use '📋 Input Field Performance' after each night\n"
                    "to build up your coverage history.")
            fig, ax = build_history_coverage_figure(
                rows,
                current_selected=None,
                mode_label=f"SSTAC History Coverage — {len(rows)} Observed Field(s)",
                fov_deg=self.fov_deg)
            self.current_fig = fig
            plt.show()
            self.log_write(f"Rendered history coverage map ({len(rows)} observed fields)")
        except Exception:
            self.log_write("=== HISTORY MAP ERROR ===")
            self.log_write(traceback.format_exc())
            messagebox.showerror("History Coverage Error",
                                 "Failed to render observed history coverage.")

    def on_save_map(self):
        if not self.last_selected:
            return messagebox.showwarning('Warning', 'Please generate a plan first!')
        try:
            if self.current_fig is None:
                fig, ax = build_sky_map_figure(
                    self.last_selected, self.last_mode, self.last_date,
                    self.utc_offset, self.fov_deg, self.neocp_objects,
                    self.location, location_name=self.loc_var.get())
                self.current_fig = fig
            filename = save_sky_map(self.current_fig, self.last_mode, self.last_date)
            self.log_write(f"Saved sky map: {filename}")
            messagebox.showinfo('Saved', f"Sky Map saved as:\n{filename}")
        except Exception:
            self.log_write('=== MAP SAVE ERROR ===')
            self.log_write(traceback.format_exc())
            messagebox.showerror('Map Save Error', 'Failed to save Sky Map.')

    def on_export_csv(self):
        if not self.last_selected:
            return messagebox.showerror('Export Error', 'Generate a plan first.')
        out_path = (self.output_dir /
                    f"nightly_targets_{self.last_mode.replace(' ', '_')}_{self.last_date}.csv")
        from io_utils import export_nina_csv
        export_nina_csv(out_path, self.last_mode, self.last_date, self.utc_offset,
                        self.last_selected, utc_to_local_dt, format_ra_dec)
        self.log_write(f"Exported successfully: {out_path.name}")
        messagebox.showinfo('Export Successful', f"N.I.N.A CSV saved to:\n{out_path}")
