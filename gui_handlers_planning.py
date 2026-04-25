"""
gui_handlers_planning.py
────────────────────────
PlanningMixin — Group 1 methods for SkySurveyApp.
Handles: NEOCP update, plan generation worker, archive, and load.
Extracted from gui.py for independent maintenance.
"""

import threading
import queue
import traceback
from pathlib import Path

from tkinter import messagebox

from astro_utils import utc_to_local_dt, format_ra_dec
from planner import generate_plan
from io_utils import load_or_fetch_neocp
from history_utils import archive_plan, load_archived_plan


class PlanningMixin:
    """Mixin that provides Group 1 – Planning actions for SkySurveyApp."""

    # ══════════════════════════════════════════
    #  Group 1 – PLANNING
    # ══════════════════════════════════════════

    def on_update_neocp(self):
        try:
            self.log_write("Initiating NEOCP Data Fetch...")
            self.neocp_objects, msg, err = load_or_fetch_neocp()
            if err:
                self.log_write(msg)
                messagebox.showerror("NEOCP Error", msg)
                return
            self.log_write(msg)
        except Exception:
            self.log_write("=== NEOCP ERROR ===")
            self.log_write(traceback.format_exc())
            messagebox.showerror("NEOCP Error", "Failed to fetch NEOCP data.")

    def on_gen(self):
        if self.location is None:
            return messagebox.showerror('Error', 'No Observatory Selected.')
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.c_total.update_val('0')
        self.c_alt.update_val('0.0')
        self.c_moon.update_val('0.0')
        self.btn_gen.configure(state='disabled', text='⌛ Computing...')
        self.prog.set(0)
        self.prog_pct_label.configure(text='0%')
        threading.Thread(target=self._worker, daemon=True).start()
        if not self._is_polling:
            self._is_polling = True
            self.after(100, self._poll_worker)

    def _worker(self):
        try:
            use_nc = self.use_neocp_var.get()
            if use_nc and not self.neocp_objects:
                self.neocp_objects, msg, _ = load_or_fetch_neocp(
                    progress_cb=lambda d, t, s: self._q.put(
                        ('prog', (d / t) * 100 if t > 0 else 0, s)))
            res = generate_plan(
                date_str_local=self.date_var.get(),
                mode=self.mode_var.get(),
                location=self.location,
                utc_offset=self.utc_offset,
                min_moon_deg=self._safe_floatvar(self.moon_var, 20.0),
                min_alt_deg=self._safe_floatvar(self.alt_var, 25.0),
                max_fields=self._safe_intvar(self.max_var, 10),
                use_history=self.hist_var.get(),
                avoid_galactic=self.avoid_gal_var.get(),
                gal_b_min_deg=self._safe_floatvar(self.gal_b_var, 12.0),
                use_overlap=self.use_overlap_var.get(),
                overlap_percent=self._safe_intvar(self.overlap_var, 10),
                use_neocp_weighting=use_nc,
                avoid_mba_zone=self.mba_avoid_var.get(),
                neocp_objects=self.neocp_objects,
                use_atlas_gap=self.use_atlas_gap_var.get(),
                atlas_gap_map=self._atlas_gap_map if self._atlas_gap_map else None,
                atlas_png_paths=self._atlas_png_paths,
                progress_cb=lambda d, t, s: self._q.put(
                    ('prog', (d / t) * 100 if t > 0 else 0, s)),
                fov_deg=self.fov_deg)
            self._q.put(('ok', res))
        except Exception as ex:
            self._q.put(('err', str(ex), traceback.format_exc()))

    def _poll_worker(self):
        try:
            item = self._q.get_nowait()
            if item[0] == 'prog':
                p_val = min(1.0, max(0.0, item[1] / 100.0))
                self.prog.set(p_val)
                self.prog_pct_label.configure(text=f"{int(item[1])}%")
                self.status_label.configure(
                    text=f"{item[2]}... {item[3]}/{item[4]}"
                    if len(item) > 3 and item[4] > 0 else item[2])
            elif item[0] == 'err':
                self.btn_gen.configure(state='normal', text='🚀 Generate Plan')
                self.log_write('=== CRITICAL ERROR ===')
                self.log_write(item[2])
                messagebox.showerror('Error', item[1])
                self.status_label.configure(text='Computation Failed')
                self.after(3000, self.reset_progress)
                self._is_polling = False
                return
            elif item[0] == 'ok':
                self.btn_gen.configure(state='normal', text='🚀 Generate Plan')
                self.prog.set(1.0)
                self.prog_pct_label.configure(text='100%')
                self.status_label.configure(text='Plan Computed Successfully')
                self.after(3000, self.reset_progress)
                res = item[1]
                # generate_plan now returns 6-tuple (lam_sun, w_start, w_end, selected, stats, atlas_gap_map)
                if len(res) == 6:
                    _, _, _, selected, stats, built_gap_map = res
                    # Cache the built map so next Generate reuses it without re-parsing PNGs
                    if built_gap_map:
                        self._atlas_gap_map = built_gap_map
                else:
                    _, _, _, selected, stats = res
                self.last_selected = selected
                self.last_mode = self.mode_var.get().upper()
                self.last_date = self.date_var.get()
                atlas_info = ""
                if stats.get("atlas_gap_frames", 0) > 0:
                    atlas_info = (f" | ATLAS frames: {stats['atlas_gap_frames']}"
                                  f" race-night: {stats.get('atlas_race_frames',0)}")
                self.log_write(
                    f"Generated [{self.last_mode}] | Found: {len(selected)} fields "
                    f"(Grid: {stats['grid_total']}){atlas_info}")
                self._update_ui_data()
                self._refresh_candidate_badge()
                self._is_polling = False
                return
        except queue.Empty:
            pass
        if self._is_polling:
            self.after(100, self._poll_worker)

    def _update_ui_data(self):
        if not self.last_selected:
            self.c_total.update_val('0')
            self.c_alt.update_val('N/A')
            self.c_moon.update_val('N/A')
            messagebox.showwarning('No Fields Found', 'No fields passed the operational constraints.')
            return
        alts  = [d['best_alt'] for d in self.last_selected]
        moons = [d['moon_sep'] for d in self.last_selected]
        self.c_total.update_val(len(self.last_selected))
        self.c_alt.update_val(f"{sum(alts) / len(self.last_selected):.1f}")
        self.c_moon.update_val(f"{min(moons):.1f}")
        for i, it in enumerate(self.last_selected, 1):
            r, d = format_ra_dec(it['coord'])
            ws = utc_to_local_dt(it['window_start'], self.utc_offset).strftime('%H:%M')
            we = utc_to_local_dt(it['window_end'],   self.utc_offset).strftime('%H:%M')
            bt = utc_to_local_dt(it['best_time'],    self.utc_offset).strftime('%H:%M')
            self.tree.insert('', 'end', values=(
                i, it.get('target_id', f'FIELD_{i:03d}'),
                it.get('role', 'DISCOVERY'), r, d,
                it.get('sector', ''), f"{ws}-{we}", bt,
                f"{it['best_alt']:.0f}", f"{it.get('duration', 0):.1f}"))

    def on_archive_plan(self):
        """Save the current night plan into the history database (CSV + meta JSON)."""
        if not self.last_selected:
            return messagebox.showwarning("Warning", "Generate a plan first!")
        try:
            plan_csv, meta_json = archive_plan(
                self.last_date, self.last_mode, self.utc_offset,
                self.loc_var.get(), self._collect_current_settings(),
                self.last_selected, self.output_dir)
            self.log_write(f"Archived plan: {plan_csv.name}")
            messagebox.showinfo("Archived",
                                f"Plan archived to:\n{plan_csv}\n\nMetadata:\n{meta_json}")
        except Exception:
            self.log_write("=== ARCHIVE ERROR ===")
            self.log_write(traceback.format_exc())
            messagebox.showerror("Archive Error", "Failed to archive current plan.")

    def on_load_archived_plan(self):
        """Load a previously archived plan CSV/XLSX back into the UI."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Load Archived Plan",
            filetypes=[("Plan files", "*.csv *.xlsx"), ("CSV", "*.csv"), ("Excel", "*.xlsx")])
        if not path:
            return
        try:
            loaded = load_archived_plan(path, self.utc_offset)
            self.last_selected = loaded["selected"]
            self.last_mode = loaded["mode"] or self.mode_var.get().upper()
            self.last_date = loaded["date_local"] or self.date_var.get()
            self.mode_var.set(self.last_mode)
            self.date_var.set(self.last_date)
            for item in self.tree.get_children():
                self.tree.delete(item)
            self._update_ui_data()
            self.log_write(f"Loaded archived plan: {Path(path).name}")
        except Exception:
            self.log_write("=== LOAD ARCHIVE ERROR ===")
            self.log_write(traceback.format_exc())
            messagebox.showerror("Load Error", "Failed to load archived plan.")
