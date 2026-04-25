import sys
import os
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')
import matplotlib
matplotlib.use('TkAgg')

import threading
import queue
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk
import matplotlib.pyplot as plt
import astropy.units as u
from astropy.coordinates import EarthLocation
from astropy.utils import iers
from astropy.coordinates import solar_system_ephemeris
from astropy.coordinates.baseframe import NonRotationTransformationWarning
import warnings

from config import APP_TITLE, APP_SUBTITLE, load_config, save_config, FOV_ARCMIN_DEFAULT
from astro_utils import local_date_default, utc_to_local_dt, format_ra_dec
from planner import generate_plan
from io_utils import load_or_fetch_neocp, export_nina_csv
from sky_map import build_sky_map_figure, save_sky_map, build_history_coverage_figure
from sky_quality_bridge import launch_sky_quality, import_latest_sky_quality
from history_utils import (
    archive_plan, load_archived_plan,
    load_observed_history_rows, append_field_performance
)
from object_code import ObjectCodeWindow

# ── GUI widgets & handler mixins ─────────────────────────────────────────────
from gui_widgets import StatCard, LocationManager
from gui_handlers_planning    import PlanningMixin
from gui_handlers_skymap      import SkyMapMixin
from gui_handlers_observation import ObservationMixin
from gui_handlers_candidates  import CandidateMixin
from gui_handlers_mpc         import MpcMixin

# ── Candidate Registry (optional — graceful fallback if file missing) ─────────
try:
    from candidate_registry import (
        register_candidate, update_ephemeris, update_status,
        update_uncertainty, list_active, list_all,
        get_tonight_followups, encode_from_field,
        load_registry, save_registry,
        STATUS_UNCONFIRMED, STATUS_CONFIRMED,
        STATUS_SUBMITTED, STATUS_LOST, STATUS_REJECTED,
        ACTIVE_STATUSES, alert_level
    )
    _CANDIDATE_AVAILABLE = True
except ImportError:
    _CANDIDATE_AVAILABLE = False
try:
    from mpc_coverage_export import (
        export_mpc_coverage, build_legacy_coverage_text,
        submit_via_email, submit_json_via_requests
    )
    _MPC_AVAILABLE = True
except ImportError:
    _MPC_AVAILABLE = False


iers.conf.auto_download = False
iers.conf.auto_max_age = None
iers.conf.iers_degraded_accuracy = "warn"
solar_system_ephemeris.set("builtin")
warnings.filterwarnings("ignore", category=NonRotationTransformationWarning)

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


# StatCard and LocationManager are defined in gui_widgets.py

class StatCard(ctk.CTkFrame):
    def __init__(self, master, title, value, unit="", color="#3b8ed0", **kwargs):
        super().__init__(master, corner_radius=12, fg_color="#1a1a1a",
                         border_width=1, border_color="#333", **kwargs)
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#888").pack(pady=(12, 0), padx=15, anchor="w")
        self.val_container = ctk.CTkFrame(self, fg_color="transparent")
        self.val_container.pack(fill="x", padx=15, pady=(0, 15))
        self.v_label = ctk.CTkLabel(self.val_container, text=value,
                                    font=ctk.CTkFont(size=28, weight="bold"), text_color="white")
        self.v_label.pack(side="left")
        ctk.CTkLabel(self.val_container, text=f" {unit}",
                     font=ctk.CTkFont(size=13), text_color=color).pack(side="left", pady=(8, 0))

    def update_val(self, val):
        self.v_label.configure(text=str(val))


# ─────────────────────────────────────────────
#  Location Manager popup
# ─────────────────────────────────────────────

class LocationManager(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Manage Observatory")
        self.geometry("450x650")
        self.parent = parent
        self.configure(fg_color="#121212")






        ttk.Label(self, text="Select Site to Edit:", background="#121212",
                  foreground="#00a8ff", font=('Segoe UI', 11, 'bold')).pack(anchor='w', padx=20, pady=(15, 5))
        self.combo_var = tk.StringVar()
        self.combo = ctk.CTkOptionMenu(self, variable=self.combo_var, command=self.on_select)
        self.combo.pack(fill='x', padx=20, pady=(0, 15))

        for label in ["Site Name:", "Latitude (deg):", "Longitude (deg):",
                      "Altitude (m):", "UTC Offset (hours):", "Field of View (arcmin):"]:
            ctk.CTkLabel(self, text=label, text_color="#aaa",
                         font=ctk.CTkFont(size=12)).pack(anchor='w', padx=20)
            if label.startswith("Site Name"):
                self.name_e = ctk.CTkEntry(self)
                self.name_e.pack(pady=(0, 10), padx=20, fill='x')
            elif label.startswith("Latitude"):
                self.lat_e = ctk.CTkEntry(self)
                self.lat_e.pack(pady=(0, 10), padx=20, fill='x')
            elif label.startswith("Longitude"):
                self.lon_e = ctk.CTkEntry(self)
                self.lon_e.pack(pady=(0, 10), padx=20, fill='x')
            elif label.startswith("Altitude"):
                self.alt_e = ctk.CTkEntry(self)
                self.alt_e.pack(pady=(0, 10), padx=20, fill='x')
            elif label.startswith("UTC Offset"):
                self.tz_e = ctk.CTkEntry(self)
                self.tz_e.pack(pady=(0, 10), padx=20, fill='x')
            else:
                self.fov_e = ctk.CTkEntry(self)
                self.fov_e.pack(pady=(0, 10), padx=20, fill='x')

        btn_frame = ctk.CTkFrame(self, fg_color='transparent')
        btn_frame.pack(fill='x', padx=20, pady=20)
        ctk.CTkButton(btn_frame, text='Save/Update', fg_color='#2ecc71',
                      hover_color='#27ae60', command=self.save).pack(side='left', expand=True, padx=5)
        ctk.CTkButton(btn_frame, text='Delete Site', fg_color='#e74c3c',
                      hover_color='#c0392b', command=self.delete).pack(side='left', expand=True, padx=5)
        ctk.CTkButton(btn_frame, text='Set Default', fg_color='#3b8ed0',
                      hover_color='#2980b9', command=self.set_default).pack(side='left', expand=True, padx=5)
        self.refresh_list()
        # Bring to front AFTER all widgets are created
        self.transient(self.parent)
        try:
            self.wait_visibility()
        except Exception:
            pass
        self.lift()
        self.grab_set()
        self.focus_force()

    def refresh_list(self):
        loc_names = list(self.parent.app_config.get('locations', {}).keys())
        self.combo.configure(values=loc_names)
        if loc_names:
            def_name = self.parent.app_config.get('default_location')
            self.combo_var.set(def_name if def_name in loc_names else loc_names[0])
            self.on_select(self.combo_var.get())

    def on_select(self, name):
        if name in self.parent.app_config.get('locations', {}):
            loc = self.parent.app_config['locations'][name]
            for entry, val in [
                (self.name_e, name), (self.lat_e, loc.get('lat', '')),
                (self.lon_e, loc.get('lon', '')), (self.alt_e, loc.get('alt', '')),
                (self.tz_e, loc.get('utc_offset', 7.0)), (self.fov_e, loc.get('fov', 34.9))
            ]:
                entry.delete(0, tk.END)
                entry.insert(0, str(val))

    def save(self):
        try:
            n = self.name_e.get().strip()
            if not n:
                return
            self.parent.app_config['locations'][n] = {
                'lat': float(self.lat_e.get()), 'lon': float(self.lon_e.get()),
                'alt': float(self.alt_e.get()), 'utc_offset': float(self.tz_e.get()),
                'fov': float(self.fov_e.get())
            }
            save_config(self.parent.app_config)
            self.parent.refresh_locs()
            if n == self.parent.loc_var.get():
                self.parent.apply_location()
            messagebox.showinfo('Saved', f"Site '{n}' updated successfully.", parent=self)
            self.refresh_list()
        except ValueError:
            messagebox.showerror('Error', 'Invalid numeric values entered.', parent=self)

    def delete(self):
        n = self.name_e.get().strip()
        if n in self.parent.app_config['locations']:
            del self.parent.app_config['locations'][n]
            save_config(self.parent.app_config)
            self.parent.refresh_locs()
            self.refresh_list()
            messagebox.showinfo('Deleted', f"Site '{n}' has been removed.", parent=self)

    def set_default(self):
        n = self.name_e.get().strip()
        if n in self.parent.app_config['locations']:
            self.parent.app_config['default_location'] = n
            save_config(self.parent.app_config)
            self.parent.refresh_locs()
            self.parent.loc_var.set(n)
            self.parent.apply_location()
            messagebox.showinfo('Default Set', f"Site '{n}' is now the default location.", parent=self)


# ─────────────────────────────────────────────
#  Main Application
# ─────────────────────────────────────────────

class SkySurveyApp(PlanningMixin, SkyMapMixin, ObservationMixin, CandidateMixin, MpcMixin, ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry('1450x950')
        self.after(250, lambda: self.state('zoomed'))
        self.app_config = load_config()
        self.location = None
        self.utc_offset = 7.0
        self.fov_deg = FOV_ARCMIN_DEFAULT / 60.0
        self.last_selected = []
        self.last_mode = ''
        self.last_date = ''
        self.neocp_objects = []
        self.neocp_status = 'Offline'
        self.output_dir = Path.cwd()
        self.current_fig = None
        self._q = queue.Queue()
        self._is_polling = False
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, minsize=420)
        self._build_ui()
        self.apply_location()
        self.after(500, self.on_update_neocp)
        self.after(800, self._refresh_candidate_badge)

    # ── helpers ───────────────────────────────

    def _safe_intvar(self, var, default):
        try:
            return int(var.get())
        except Exception:
            var.set(default)
            return int(default)

    def _safe_floatvar(self, var, default):
        try:
            return float(var.get())
        except Exception:
            var.set(default)
            return float(default)

    def _on_atlas_gap_toggle(self, *_):
        """Show/hide ATLAS Gap controls based on checkbox."""
        enabled = self.use_atlas_gap_var.get()
        state = "normal" if enabled else "disabled"
        try:
            self.atlas_png_btn.configure(state=state)
        except Exception:
            pass

    def _on_atlas_pick_png(self):
        """Let user pick one or more MPC sky coverage PNG files."""
        paths = filedialog.askopenfilenames(
            title="Select MPC Sky Coverage PNG(s) — oldest first",
            filetypes=[("PNG images", "*.png"), ("All files", "*.*")])
        if not paths:
            return

        import re as _re
        self._atlas_png_paths = []
        labels = []

        # Reference date = Survey Date field
        ref_date_str = self.date_var.get().strip()
        try:
            ref_date = datetime.strptime(ref_date_str, "%Y-%m-%d").date()
        except Exception:
            from datetime import timezone as _tz, timedelta as _td
            ref_date = (datetime.now(_tz.utc) + _td(hours=self.utc_offset)).date()
            ref_date_str = str(ref_date)

        self.log_write(f"ATLAS Gap: reference date = {ref_date_str} (Survey Date)")

        try:
            from atlas_gap import extract_date_from_coverage_png as _read_date
            _has_extractor = True
        except ImportError:
            _has_extractor = False

        for p in sorted(paths):
            age  = 1
            name = Path(p).name
            file_date = None

            # ── Strategy 1: read date from image content ──────────────────
            if _has_extractor:
                try:
                    file_date = _read_date(p)
                    if file_date:
                        self.log_write(f"  {name}: date from image = {file_date}")
                except Exception as e:
                    self.log_write(f"  {name}: image date read error: {e}")

            # ── Strategy 2: parse date from filename ──────────────────────
            if file_date is None:
                m = _re.search(r"(\d{4})-?(\d{2})-?(\d{2})", name)
                if m:
                    try:
                        date_str  = f"{m.group(1)}{m.group(2)}{m.group(3)}"
                        file_date = datetime.strptime(date_str, "%Y%m%d").date()
                        self.log_write(f"  {name}: date from filename = {file_date}")
                    except Exception:
                        pass

            # ── Compute age ───────────────────────────────────────────────
            if file_date is not None:
                age = max(1, (ref_date - file_date).days)
            else:
                self.log_write(f"  {name}: no date found, using age=1")

            self._atlas_png_paths.append((p, age))
            date_str_display = str(file_date) if file_date else "date unknown"
            labels.append(f"  📅 {date_str_display}  age={age}d  |  {name}")

        self._atlas_gap_map = []   # reset — will rebuild on Generate
        display_text = "\n".join(labels) if labels else "  No image selected"
        label_color  = "#2ecc71" if labels else "#555"
        self.atlas_png_label.configure(text=display_text, fg=label_color)
        self.atlas_png_label.update_idletasks()
        self.log_write(
            f"ATLAS Gap: {len(self._atlas_png_paths)} PNG(s) loaded "
            f"({', '.join(str(a) for _,a in self._atlas_png_paths)} nights ago)")

    def _section_label(self, parent, text):
        """Thin divider + label used to group sidebar buttons."""
        ctk.CTkLabel(parent, text=text,
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#555").pack(fill='x', padx=20, pady=(12, 2))
        ctk.CTkFrame(parent, height=1, fg_color="#2a2a2a").pack(fill='x', padx=20, pady=(0, 4))

    def _popup_front(self, win, parent=None):
        """Bring a CTkToplevel to the front AFTER all widgets are created.

        Uses Misc.wait_visibility() — blocks until the window manager has
        actually mapped the window, then lifts + grabs focus.
        Must be called as the LAST step of any popup creation.
        """
        p = parent or self
        win.transient(p)
        try:
            win.wait_visibility()   # Misc.wait_visibility() — deterministic, no timing guess
        except Exception:
            pass                    # window destroyed before becoming visible
        win.lift()
        win.grab_set()
        win.focus_force()

    # ── UI build ──────────────────────────────

    def _build_ui(self):
        # ── Sidebar ──────────────────────────
        self.sidebar = ctk.CTkFrame(self, width=420, corner_radius=0, fg_color="#0d0d0d")
        self.sidebar.grid(row=0, column=0, sticky='nsew')
        self.sidebar.grid_propagate(False)

        ctk.CTkLabel(self.sidebar, text=APP_TITLE,
                     font=ctk.CTkFont(size=24, weight='bold')).pack(pady=(25, 5))
        ctk.CTkLabel(self.sidebar, text=APP_SUBTITLE,
                     font=ctk.CTkFont(size=11), text_color='#555').pack(pady=(0, 20))

        # Observatory selector
        self.loc_var = tk.StringVar()
        self.loc_menu = ctk.CTkOptionMenu(
            self.sidebar, variable=self.loc_var,
            values=list(self.app_config['locations'].keys()),
            command=lambda _: self.apply_location())
        self.loc_menu.pack(pady=5, padx=20, fill='x')

        self.loc_info_label = ctk.CTkLabel(
            self.sidebar, text='Lat: -- | Lon: --\nFOV: --',
            text_color='#00a8ff', font=ctk.CTkFont(size=11), justify='left')
        self.loc_info_label.pack(pady=(0, 5), padx=20, anchor='w')

        ctk.CTkButton(self.sidebar, text='⚙ Manage Observatory',
                      fg_color='#333', hover_color='#444',
                      command=lambda: LocationManager(self)).pack(pady=5, padx=20, fill='x')

        # ── Scrollable parameter area ─────────
        scroll = ctk.CTkScrollableFrame(self.sidebar, fg_color='transparent',
                                        label_text='Survey Parameters')
        scroll.pack(fill='both', expand=True, padx=10, pady=10)

        # Date
        ctk.CTkLabel(scroll, text='Survey Date (Local)',
                     text_color='#aaa', font=ctk.CTkFont(size=12)).pack(anchor='w', padx=10, pady=(10, 0))
        self.date_var = tk.StringVar(value=local_date_default())
        ctk.CTkEntry(scroll, textvariable=self.date_var).pack(fill='x', padx=10, pady=5)

        # Mode
        ctk.CTkLabel(scroll, text='Survey Mode',
                     text_color='#aaa', font=ctk.CTkFont(size=12)).pack(anchor='w', padx=10, pady=(10, 0))
        self.mode_var = tk.StringVar(value='NARROW ECLIPTIC')
        self.cb_mode = ctk.CTkOptionMenu(scroll, variable=self.mode_var,
                                         values=['NARROW ECLIPTIC', 'HIGH INCLINATION'],
                                         command=self.on_mode_change)
        self.cb_mode.pack(fill='x', padx=10, pady=5)

        # Max fields
        ctk.CTkLabel(scroll, text='Max Fields',
                     text_color='#aaa', font=ctk.CTkFont(size=12)).pack(anchor='w', padx=10, pady=(10, 0))
        self.max_var = tk.IntVar(value=6)
        ctk.CTkEntry(scroll, textvariable=self.max_var).pack(fill='x', padx=10, pady=5)

        # Moon sep slider
        self.moon_var = tk.DoubleVar(value=20)
        mf = ctk.CTkFrame(scroll, fg_color='transparent')
        mf.pack(fill='x', padx=10, pady=(15, 0))
        ctk.CTkLabel(mf, text='Min Moon Sep', text_color='#aaa').pack(side='left')
        self.m_lbl = ctk.CTkLabel(mf, text='20°', text_color='#3b8ed0')
        self.m_lbl.pack(side='right')
        ctk.CTkSlider(scroll, from_=0, to=90, variable=self.moon_var,
                      command=lambda v: self.m_lbl.configure(text=f"{int(v)}°")).pack(fill='x', padx=10, pady=5)

        # Alt slider
        self.alt_var = tk.DoubleVar(value=25)
        af = ctk.CTkFrame(scroll, fg_color='transparent')
        af.pack(fill='x', padx=10, pady=(10, 0))
        ctk.CTkLabel(af, text='Min Target Alt', text_color='#aaa').pack(side='left')
        self.a_lbl = ctk.CTkLabel(af, text='25°', text_color='#f1c40f')
        self.a_lbl.pack(side='right')
        ctk.CTkSlider(scroll, from_=10, to=60, variable=self.alt_var,
                      command=lambda v: self.a_lbl.configure(text=f"{int(v)}°")).pack(fill='x', padx=10, pady=5)

        # Checkboxes
        self.hist_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(scroll, text='Avoid History',
                        variable=self.hist_var).pack(anchor='w', padx=10, pady=10)

        self.mba_avoid_var = tk.BooleanVar(value=False)
        self.mba_avoid_check = ctk.CTkCheckBox(scroll, text='Avoid MBA Zone',
                                               variable=self.mba_avoid_var)
        # Hidden — not packed (MBA zone not relevant for NE/HI modes)

        self.avoid_gal_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(scroll, text='Avoid Galactic Plane',
                        variable=self.avoid_gal_var).pack(anchor='w', padx=10, pady=(10, 0))

        gal_f = ctk.CTkFrame(scroll, fg_color='transparent')
        gal_f.pack(fill='x', padx=10, pady=(5, 5))
        ctk.CTkLabel(gal_f, text='Exclude |b| <',
                     text_color='#aaa', font=ctk.CTkFont(size=12)).pack(side='left', padx=(25, 0))
        self.gal_b_var = tk.DoubleVar(value=12.0)
        ctk.CTkEntry(gal_f, textvariable=self.gal_b_var, width=60, height=26).pack(side='left', padx=10)
        ctk.CTkLabel(gal_f, text='deg',
                     text_color='#aaa', font=ctk.CTkFont(size=12)).pack(side='left')

        # Overlap
        self.use_overlap_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(scroll, text='Enable Field Overlap Control',
                        variable=self.use_overlap_var).pack(anchor='w', padx=10, pady=(10, 0))
        olap_f = ctk.CTkFrame(scroll, fg_color='transparent')
        olap_f.pack(fill='x', padx=10, pady=(5, 5))
        ctk.CTkLabel(olap_f, text='Overlap Size:',
                     text_color='#aaa', font=ctk.CTkFont(size=12)).pack(side='left', padx=(25, 0))
        self.overlap_var = tk.IntVar(value=10)
        ctk.CTkEntry(olap_f, textvariable=self.overlap_var, width=60, height=26).pack(side='left', padx=10)
        ctk.CTkLabel(olap_f, text='%',
                     text_color='#aaa', font=ctk.CTkFont(size=12)).pack(side='left')

        # NEOCP
        self.use_neocp_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(scroll, text='NEOCP Weighting',
                        variable=self.use_neocp_var).pack(anchor='w', padx=10, pady=(10, 5))
        self.btn_update_neocp = ctk.CTkButton(
            scroll, text='🔄 Update NEOCP',
            command=self.on_update_neocp, fg_color='#333', hover_color='#444')
        self.btn_update_neocp.pack(fill='x', padx=25, pady=(0, 10))

        # ATLAS Gap Intelligence
        self.use_atlas_gap_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(scroll, text='🛰 ATLAS Gap Mode',
                        variable=self.use_atlas_gap_var,
                        command=self._on_atlas_gap_toggle).pack(
                            anchor='w', padx=10, pady=(10, 0))

        self.atlas_gap_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.atlas_gap_frame.pack(fill='x', padx=10, pady=(2, 8))

        self.atlas_png_label = tk.Label(
            self.atlas_gap_frame,
            text='  No image selected',
            fg='#555', bg='#1a1a1a',
            font=('Segoe UI', 9),
            justify='left', anchor='w', wraplength=280)
        self.atlas_png_label.pack(anchor='w', fill='x')

        self.atlas_png_btn = ctk.CTkButton(
            self.atlas_gap_frame,
            text='📂 Upload MPC Coverage PNG',
            fg_color='#2c3e50', hover_color='#1a252f',
            height=26, font=ctk.CTkFont(size=11),
            command=self._on_atlas_pick_png)
        self.atlas_png_btn.pack(fill='x', pady=(2, 0))

        ctk.CTkLabel(
            self.atlas_gap_frame,
            text='  💡 Download from minorplanetcenter.net\n'
                 '     → SkyCoverage → select today-2 → PNG',
            text_color='#444', font=ctk.CTkFont(size=9),
            justify='left').pack(anchor='w', pady=(2, 0))

        self._atlas_png_paths = []   # list of (path, age_days)
        self._atlas_gap_map   = []
        self._on_atlas_gap_toggle()   # init state

        # ══════════════════════════════════════
        #  ACTION BUTTONS  (fixed below scroll)
        # ══════════════════════════════════════

        # ── Group 1 : PLANNING ─────────────────────────────────────
        self._section_label(self.sidebar, "  PLANNING")

        self.btn_gen = ctk.CTkButton(
            self.sidebar, text='🚀 Generate Plan',
            command=self.on_gen, fg_color='#2ecc71', hover_color='#27ae60',
            font=ctk.CTkFont(size=15, weight='bold'))
        self.btn_gen.pack(pady=(2, 2), padx=20, fill='x')

        ctk.CTkButton(self.sidebar, text='📦 Archive Night Plan',
                      command=self.on_archive_plan,
                      fg_color='#1a6b3c', hover_color='#145730').pack(pady=2, padx=20, fill='x')

        ctk.CTkButton(self.sidebar, text='📂 Load Archived Plan',
                      command=self.on_load_archived_plan,
                      fg_color='#2c5364', hover_color='#203a43').pack(pady=2, padx=20, fill='x')

        # ── Group 2 : SKY MAP & EXPORT ─────────────────────────────
        self._section_label(self.sidebar, "  SKY MAP & EXPORT")

        ctk.CTkButton(self.sidebar, text='🌌 Show Sky Map',
                      command=self.on_show_map,
                      fg_color='#3b8ed0', hover_color='#2980b9').pack(pady=2, padx=20, fill='x')

        ctk.CTkButton(self.sidebar, text='🗺 Show History Coverage',
                      command=self.on_show_history_coverage,
                      fg_color='#2c3e50', hover_color='#1a252f').pack(pady=2, padx=20, fill='x')

        ctk.CTkButton(self.sidebar, text='💾 Save Sky Map',
                      command=self.on_save_map,
                      fg_color='#16a085', hover_color='#138d75').pack(pady=2, padx=20, fill='x')

        self.btn_export = ctk.CTkButton(
            self.sidebar, text='💾 Save N.I.N.A. CSV',
            command=self.on_export_csv,
            fg_color='#e67e22', hover_color='#d35400')
        self.btn_export.pack(pady=2, padx=20, fill='x')

        # ── Group 3 : OBSERVATION TOOLS ───────────────────────────
        self._section_label(self.sidebar, "  OBSERVATION TOOLS")

        ctk.CTkButton(self.sidebar, text='🔭 Launch Sky Quality',
                      command=self.on_launch_sky_quality,
                      fg_color='#6c3483', hover_color='#5b2c6f').pack(pady=2, padx=20, fill='x')

        ctk.CTkButton(self.sidebar, text='📋 Input Field Performance',
                      command=self.on_field_performance,
                      fg_color='#784212', hover_color='#6e2c00').pack(pady=2, padx=20, fill='x')

        ctk.CTkButton(self.sidebar, text='🏷 Gen Object Code',
                      command=self.send_to_object_code,
                      fg_color='#8e44ad', hover_color='#7d3c98').pack(pady=2, padx=20, fill='x')

        # ── Group 4 : CANDIDATES ───────────────────────────────────
        self._section_label(self.sidebar, "  CANDIDATES")

        self.btn_candidate = ctk.CTkButton(
            self.sidebar, text='🚩 Candidate Registry [0]',
            command=self.on_candidate_registry,
            fg_color='#7d0000', hover_color='#5c0000')
        self.btn_candidate.pack(pady=(2, 2), padx=20, fill='x')

        # ── Group 5 : MPC REPORTING ────────────────────────────────
        self._section_label(self.sidebar, "  MPC REPORTING")

        ctk.CTkButton(self.sidebar, text='🌐 Export MPC Coverage',
                      command=self.on_export_mpc_coverage,
                      fg_color='#1a4a6b', hover_color='#154060').pack(pady=2, padx=20, fill='x')

        ctk.CTkButton(self.sidebar, text='📧 Submit to MPC (Email)',
                      command=self.on_submit_mpc_email,
                      fg_color='#2c3e50', hover_color='#1a252f').pack(pady=(2, 12), padx=20, fill='x')

        # ── Main content area ─────────────────────────────────────
        self.main = ctk.CTkFrame(self, fg_color='#0a0a0a', corner_radius=0)
        self.main.grid(row=0, column=1, sticky='nsew')
        self.main.grid_rowconfigure(1, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        # Stat cards
        self.dash = ctk.CTkFrame(self.main, fg_color='transparent')
        self.dash.grid(row=0, column=0, sticky='ew', padx=25, pady=(25, 0))
        self.c_total = StatCard(self.dash, 'TOTAL TARGETS', '0', 'Fields')
        self.c_total.pack(side='left', expand=True, fill='both', padx=10)
        self.c_alt = StatCard(self.dash, 'AVG ALTITUDE', '0.0', 'Deg', '#f1c40f')
        self.c_alt.pack(side='left', expand=True, fill='both', padx=10)
        self.c_moon = StatCard(self.dash, 'MIN MOON SEP', '0.0', 'Deg', '#e74c3c')
        self.c_moon.pack(side='left', expand=True, fill='both', padx=10)

        # Target table
        self.t_frame = ctk.CTkFrame(self.main, fg_color='#181818', corner_radius=12)
        self.t_frame.grid(row=1, column=0, sticky='nsew', padx=25, pady=25)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Treeview', background='#181818', foreground='white',
                        fieldbackground='#181818', rowheight=32,
                        font=('Segoe UI', 11), borderwidth=0)
        style.configure('Treeview.Heading', background='#2b2b2b', foreground='white',
                        font=('Segoe UI', 11, 'bold'), borderwidth=0, relief='flat')
        style.map('Treeview', background=[('selected', '#1f538d')])
        cols = ('#', 'Target ID', 'Role', 'RA', 'Dec', 'Sector', 'Window', 'Time', 'Alt', 'Dur (h)')
        self.tree = ttk.Treeview(self.t_frame, columns=cols, show='headings')
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=100, anchor='center')
        self.tree.pack(side='left', fill='both', expand=True, padx=10, pady=10)
        sc = ttk.Scrollbar(self.t_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sc.set)
        sc.pack(side='right', fill='y')

        # Console log
        self.log_frame = ctk.CTkFrame(self.main, fg_color='#121212', corner_radius=12, height=140)
        self.log_frame.grid(row=2, column=0, sticky='ew', padx=25, pady=(0, 25))
        self.log_frame.pack_propagate(False)
        ctk.CTkLabel(self.log_frame, text='System Console',
                     font=ctk.CTkFont(size=12, weight='bold'),
                     text_color='#00a8ff').pack(anchor='w', padx=15, pady=(5, 0))
        self.log = ctk.CTkTextbox(self.log_frame, fg_color='#0a0a0a',
                                  text_color='#a4b0be',
                                  font=ctk.CTkFont(family='Consolas', size=12))
        self.log.pack(fill='both', expand=True, padx=10, pady=(5, 10))
        self.log.configure(state='disabled')
        self.log_write('SSTAC Planner Ready.')

        # Status bar
        self.status_bar = ctk.CTkFrame(self, height=35, corner_radius=0, fg_color='#121212')
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky='ew')
        self.status_label = ctk.CTkLabel(self.status_bar, text='System Online',
                                         text_color='#a4b0be', font=ctk.CTkFont(size=12))
        self.status_label.pack(side='left', padx=20)
        self.prog_pct_label = ctk.CTkLabel(self.status_bar, text='0%',
                                           text_color='#2ecc71',
                                           font=ctk.CTkFont(size=12, weight='bold'))
        self.prog_pct_label.pack(side='right', padx=(0, 20))
        self.prog = ctk.CTkProgressBar(self.status_bar, height=10,
                                       fg_color='#1a1a1a', progress_color='#2ecc71')
        self.prog.pack(side='right', fill='x', expand=True, padx=15)
        self.prog.set(0)

        self.on_mode_change()

    # ── UI state helpers ──────────────────────

    def on_mode_change(self, *_args):
        self.mba_avoid_var.set(False)
        try:
            self.mba_avoid_check.configure(state='disabled')
        except Exception:
            pass

    def log_write(self, msg):
        self.log.configure(state='normal')
        self.log.insert('end', f"[{__import__('datetime').datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see('end')
        self.log.configure(state='disabled')

    def refresh_locs(self):
        self.loc_menu.configure(values=list(self.app_config.get('locations', {}).keys()))

    def apply_location(self):
        loc_names = list(self.app_config.get('locations', {}).keys())
        self.loc_menu.configure(values=loc_names)
        name = self.loc_var.get()
        if not name or name not in loc_names:
            name = self.app_config.get('default_location', loc_names[0] if loc_names else 'Default')
        if name in loc_names:
            self.loc_var.set(name)
            loc_data = self.app_config['locations'][name]
            self.location = EarthLocation(
                lat=loc_data['lat'] * u.deg,
                lon=loc_data['lon'] * u.deg,
                height=loc_data['alt'] * u.m)
            self.utc_offset = loc_data.get('utc_offset', 7.0)
            fov_arcmin = loc_data.get('fov', FOV_ARCMIN_DEFAULT)
            self.fov_deg = fov_arcmin / 60.0
            self.loc_info_label.configure(
                text=f"Lat: {loc_data['lat']:.4f}° | Lon: {loc_data['lon']:.4f}°\n"
                     f"FOV: {fov_arcmin:.1f}' | UTC: {self.utc_offset:+g}h")

    def reset_progress(self):
        self.prog.set(0)
        self.prog_pct_label.configure(text='0%')
        self.status_label.configure(text='System Online')

    def _collect_current_settings(self):
        return {
            "max_fields":    self._safe_intvar(self.max_var, 10),
            "min_moon_sep":  self._safe_floatvar(self.moon_var, 20.0),
            "min_alt":       self._safe_floatvar(self.alt_var, 25.0),
            "avoid_history": bool(self.hist_var.get()),
            "avoid_mba":     bool(self.mba_avoid_var.get()),
            "avoid_galactic": bool(self.avoid_gal_var.get()),
            "gal_b_min":     self._safe_floatvar(self.gal_b_var, 12.0),
            "use_overlap":   bool(self.use_overlap_var.get()),
            "overlap_pct":   self._safe_intvar(self.overlap_var, 10),
            "use_neocp":     bool(self.use_neocp_var.get()),
        }

    def _find_last_selected_item(self, target_id):
        for it in self.last_selected:
            if str(it.get("target_id", "")).strip() == str(target_id).strip():
                return it
        return None

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
                atlas_gap_map=self._atlas_gap_map if self._atlas_gap_map
                              else None,
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
        export_nina_csv(out_path, self.last_mode, self.last_date, self.utc_offset,
                        self.last_selected, utc_to_local_dt, format_ra_dec)
        self.log_write(f"Exported successfully: {out_path.name}")
        messagebox.showinfo('Export Successful', f"N.I.N.A CSV saved to:\n{out_path}")

    # ══════════════════════════════════════════
    #  Group 3 – OBSERVATION TOOLS
    # ══════════════════════════════════════════

    def on_launch_sky_quality(self):
        """Launch SkyQuality_beta.py as a separate process."""
        try:
            script = launch_sky_quality()
            self.log_write(f"Launched Sky Quality Analyzer: {script}")
        except Exception:
            self.log_write("=== SKY QUALITY LAUNCH ERROR ===")
            self.log_write(traceback.format_exc())
            messagebox.showerror("Sky Quality Error", "Failed to launch Sky Quality.")

    def on_import_sky_quality_log(self):
        """Pull the latest row from the SkyQuality CSV log."""
        try:
            latest = import_latest_sky_quality()
            msg = (
                f"Sky background : {latest.get('sky_mag_arcsec2', '')} mag/arcsec²\n"
                f"Seeing         : {latest.get('seeing_fwhm_arcsec', '')} arcsec\n"
                f"Limit mag (1fr): {latest.get('limit_mag_single', '')}\n"
                f"Limit mag (stk): {latest.get('limit_mag_stack', '')}"
            )
            self.log_write("Imported latest SkyQuality log row.")
            messagebox.showinfo("SkyQuality Imported", msg)
        except Exception:
            self.log_write("=== SKY QUALITY IMPORT ERROR ===")
            self.log_write(traceback.format_exc())
            messagebox.showerror("SkyQuality Import Error", "Failed to import SkyQuality log.")

    def on_field_performance(self):
        """Open the Field Performance dialog for a row selected in the table."""
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning(
                "Warning", "Please select a field row in the table first.")
        vals = self.tree.item(sel[0])["values"]
        target_id   = vals[1] if len(vals) > 1 else ""
        field_item  = self._find_last_selected_item(target_id)

        win = ctk.CTkToplevel(self)
        win.title(f"Field Performance — {target_id}")
        win.geometry("540x820")
        win.minsize(520, 780)






        frm = ctk.CTkFrame(win)
        frm.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(frm, text=f"Target: {target_id}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#00a8ff").pack(anchor="w", pady=(0, 10))

        status_var   = tk.StringVar(value="OBSERVED")
        known_var    = tk.StringVar(value="0")
        detected_var = tk.StringVar(value="0")
        cand_var     = tk.StringVar(value="0")
        sky_var      = tk.StringVar(value="")
        seeing_var   = tk.StringVar(value="")
        lim1_var     = tk.StringVar(value="")
        lims_var     = tk.StringVar(value="")
        note_var     = tk.StringVar(value="")

        entries = [
            ("Status",                  status_var,   True),
            ("Known objects",           known_var,    False),
            ("Detected known objects",  detected_var, False),
            ("Discovery candidates",    cand_var,     False),
            ("Sky mag/arcsec²",         sky_var,      False),
            ("Seeing FWHM (arcsec)",    seeing_var,   False),
            ("Limit mag single",        lim1_var,     False),
            ("Limit mag stack",         lims_var,     False),
            ("Note",                    note_var,     False),
        ]
        for label, var, is_menu in entries:
            ctk.CTkLabel(frm, text=label, text_color="#aaa").pack(anchor="w", pady=(6, 1))
            if is_menu:
                ctk.CTkOptionMenu(frm, variable=var,
                                  values=["OBSERVED", "NOT_OBSERVED", "PARTIAL"]).pack(fill="x")
            else:
                ctk.CTkEntry(frm, textvariable=var).pack(fill="x")

        def pull_latest():
            try:
                latest = import_latest_sky_quality()
                sky_var.set(latest.get("sky_mag_arcsec2", ""))
                seeing_var.set(latest.get("seeing_fwhm_arcsec", ""))
                lim1_var.set(latest.get("limit_mag_single", ""))
                lims_var.set(latest.get("limit_mag_stack", ""))
                self.log_write("SkyQuality data imported into performance form.")
            except Exception:
                messagebox.showerror("Import Error",
                                     "Failed to import latest SkyQuality row.", parent=win)

        def save_perf():
            record = {
                "date_local":             self.last_date,
                "mode":                   self.last_mode,
                "target_id":              target_id,
                "status":                 status_var.get(),
                "sky_mag_arcsec2":        sky_var.get(),
                "seeing_fwhm_arcsec":     seeing_var.get(),
                "limit_mag_single":       lim1_var.get(),
                "limit_mag_stack":        lims_var.get(),
                "known_objects":          known_var.get(),
                "detected_known_objects": detected_var.get(),
                "discovery_candidates":   cand_var.get(),
                "note":                   note_var.get(),
            }
            if field_item is not None:
                ra_s, dec_s = format_ra_dec(field_item["coord"])
                record["ra"]  = ra_s
                record["dec"] = dec_s
                record["window_start_local"] = utc_to_local_dt(
                    field_item["window_start"], self.utc_offset).strftime("%Y-%m-%d %H:%M")
                record["window_end_local"] = utc_to_local_dt(
                    field_item["window_end"], self.utc_offset).strftime("%Y-%m-%d %H:%M")
                record["best_time_local"] = utc_to_local_dt(
                    field_item["best_time"], self.utc_offset).strftime("%Y-%m-%d %H:%M")
                record["best_alt_deg"] = float(field_item.get("best_alt", 0.0))
                record["moon_sep_deg"] = float(field_item.get("moon_sep", 0.0))
                record["duration_hr"]  = float(field_item.get("duration", 0.0))
                record["score"]        = float(field_item.get("score", 0.0))
            try:
                perf_path = append_field_performance(record, self.output_dir)
                self.log_write(f"Saved field performance: {target_id}")
                messagebox.showinfo("Saved",
                                    f"Performance saved to:\n{perf_path}", parent=win)
                win.destroy()
            except Exception:
                self.log_write("=== PERFORMANCE SAVE ERROR ===")
                self.log_write(traceback.format_exc())
                messagebox.showerror("Save Error",
                                     "Failed to save field performance.", parent=win)

        btn_row = ctk.CTkFrame(frm, fg_color="transparent")
        btn_row.pack(fill="x", pady=15)
        ctk.CTkButton(btn_row, text="📥 Import SkyQuality Data",
                      command=pull_latest).pack(side="left", expand=True, fill="x", padx=4)
        ctk.CTkButton(btn_row, text="💾 Save Performance",
                      fg_color="#27ae60", hover_color="#1f8a4d",
                      command=save_perf).pack(side="left", expand=True, fill="x", padx=4)
        self._popup_front(win)

    def send_to_object_code(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning('Warning', 'Please select a field first')
        field_name = self.tree.item(sel[0])['values'][1]
        field_item = self._find_last_selected_item(field_name)

        if not _CANDIDATE_AVAILABLE:
            win = ObjectCodeWindow(self)
            win.set_field_name(field_name)
            self._popup_front(win)
            return
        self._open_object_code_window(field_name, field_item)

    def _open_object_code_window(self, field_name: str, field_item):
        """Object Code window: Generate + Copy + Decode, then -> Register."""
        import re as _re
        from object_code import to_base36
        from config import MODE_SECTOR_MAP
        from datetime import datetime as _dt

        win = ctk.CTkToplevel(self)
        win.title(f"Object Code — {field_name}")
        win.geometry("520x600")
        win.configure(fg_color="#0d0d0d")
        win.resizable(False, False)

        # ── Header ───────────────────────────────────────────────
        header_frm = ctk.CTkFrame(win, fg_color="transparent")
        header_frm.pack(fill="x", padx=20, pady=(20, 10))
        
        ctk.CTkLabel(header_frm, text="🏷️  SSTAC Object Code Generator",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#00a8ff").pack()
        ctk.CTkLabel(header_frm, text=f"Field: {field_name}",
                     font=ctk.CTkFont(family="Consolas", size=13),
                     text_color="#fbc531").pack(pady=(4, 0))

        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20)

        # ── Section 1: Generate (Card) ────────────────────────────
        gen_card = ctk.CTkFrame(body, fg_color="#151b22", corner_radius=12)
        gen_card.pack(fill="x", pady=(10, 15), ipadx=10, ipady=10)
        
        ctk.CTkLabel(gen_card, text="Generate Code",
                     text_color="#3b8ed0",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))

        trk_row = ctk.CTkFrame(gen_card, fg_color="transparent")
        trk_row.pack(fill="x", padx=15)
        ctk.CTkLabel(trk_row, text="Track No:", text_color="#aaa",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        track_var = tk.StringVar(value="1")
        ctk.CTkEntry(trk_row, textvariable=track_var, width=60, justify="center").pack(side="left", padx=10)

        # Code display + copy
        code_frame = ctk.CTkFrame(gen_card, fg_color="#081008", corner_radius=8, border_width=1, border_color="#1a6b3c")
        code_frame.pack(fill="x", padx=15, pady=(15, 10))
        code_row = ctk.CTkFrame(code_frame, fg_color="transparent")
        code_row.pack(fill="x", padx=15, pady=12)
        
        code_lbl = ctk.CTkLabel(code_row,
                                text="TXXXXXX",
                                font=ctk.CTkFont(family="Consolas", size=28, weight="bold"),
                                text_color="#4cd137")
        code_lbl.pack(side="left", expand=True)

        copy_btn = ctk.CTkButton(code_row, text="📋 Copy", width=80,
                                 fg_color="#2c3e50", hover_color="#1a252f",
                                 font=ctk.CTkFont(size=12, weight="bold"))
        copy_btn.pack(side="right")

        generated_code = tk.StringVar(value="")

        def do_copy():
            code = generated_code.get()
            if code and code != "TXXXXXX":
                win.clipboard_clear()
                win.clipboard_append(code)
                copy_btn.configure(text="✅ Copied", fg_color="#1a6b3c", hover_color="#145730")
                win.after(1500, lambda: copy_btn.configure(text="📋 Copy", fg_color="#2c3e50", hover_color="#1a252f"))

        copy_btn.configure(command=do_copy)

        def do_generate():
            try:
                m = _re.match(r"^(NE|HI)_(\d{8})_(\d{3})$",
                              field_name.strip().upper())
                if not m:
                    messagebox.showerror("Error",
                        "Field must be NE_YYYYMMDD_NNN or HI_YYYYMMDD_NNN", parent=win)
                    return
                prefix, date_str, f_idx_str = m.groups()
                mode   = "NARROW ECLIPTIC" if prefix == "NE" else "HIGH INCLINATION"
                dt     = _dt.strptime(date_str, "%Y%m%d")
                Y      = to_base36(dt.year - 2020, 1)
                DD     = to_base36(int(dt.strftime("%j")), 2)
                S      = MODE_SECTOR_MAP.get((mode, "NIGHT"), "X")
                F      = to_base36(int(f_idx_str), 1)
                O      = to_base36(int(track_var.get() or "1"), 1)
                code   = f"T{Y}{DD}{S}{F}{O}"
                code_lbl.configure(text=code)
                generated_code.set(code)
                dec_entry.delete(0, tk.END)
                dec_entry.insert(0, code)
                self.log_write(f"Object Code generated: {code}")
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=win)

        ctk.CTkButton(gen_card, text="⚡ Generate New Code",
                      fg_color="#00a8ff", hover_color="#0080cc", height=36,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=do_generate).pack(fill="x", padx=15, pady=(5, 10))

        # ── Section 2: Decode (Card) ──────────────────────────────
        dec_card = ctk.CTkFrame(body, fg_color="#151b22", corner_radius=12)
        dec_card.pack(fill="x", pady=5, ipadx=10, ipady=10)

        ctk.CTkLabel(dec_card, text="Decode Code",
                     text_color="#e67e22",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))

        dec_row = ctk.CTkFrame(dec_card, fg_color="transparent")
        dec_row.pack(fill="x", padx=15, pady=(0, 10))
        dec_entry = tk.Entry(dec_row, bg="#0d1117", fg="#fbc531",
                             font=("Consolas", 14, "bold"),
                             insertbackground="#fbc531",
                             borderwidth=1, relief="solid", width=14)
        dec_entry.pack(side="left", ipady=6, padx=(0, 10))

        dec_result = ctk.CTkTextbox(dec_card, height=80, fg_color="#0d1117",
                                    text_color="#dfe4ea",
                                    font=ctk.CTkFont(family="Consolas", size=11))
        dec_result.pack(fill="x", padx=15, pady=(0, 5))
        dec_result.configure(state="disabled")

        def do_decode():
            from config import INV_MODE_MAP
            from object_code import from_base36
            from datetime import date as _date, timedelta as _td
            try:
                code = dec_entry.get().strip().upper()
                if len(code) != 7 or not code.startswith("T"):
                    raise ValueError("Code must be 7 chars starting with T")
                year   = 2020 + from_base36(code[1])
                doy    = from_base36(code[2:4])
                s_code = code[4]
                f_idx  = from_base36(code[5])
                track  = from_base36(code[6])
                dt_d   = _date(year, 1, 1) + _td(days=doy - 1)
                mi     = INV_MODE_MAP.get(s_code, ("Unknown", "Unknown"))
                info   = (f"Date:   {dt_d}  (DOY {doy})\n"
                          f"Mode:   {mi[0]}\n"
                          f"Sector: {mi[1] or 'NIGHT'}\n"
                          f"Field:  {f_idx:03d}   Track: {track:02d}")
                dec_result.configure(state="normal")
                dec_result.delete("1.0", "end")
                dec_result.insert("end", info)
                dec_result.configure(state="disabled")
            except Exception as e:
                messagebox.showerror("Decode Error", str(e), parent=win)

        ctk.CTkButton(dec_row, text="DECODE", width=90, height=34,
                      fg_color="#44bd32", hover_color="#33a022",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=do_decode).pack(side="left")

        # ── Bottom buttons ────────────────────────────────────────
        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(10, 20))

        def open_register():
            code = generated_code.get()
            if not code or code == "TXXXXXX":
                messagebox.showwarning("No Code",
                    "Generate the object code first.", parent=win)
                return
            self._open_register_from_code(win, code, field_item)

        ctk.CTkButton(btn_row, text="🚩 Register as Candidate",
                      fg_color="#1a6b3c", hover_color="#145730", height=40,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=open_register).pack(side="left", expand=True, fill="x", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Close",
                      fg_color="#333", hover_color="#444", height=40,
                      command=win.destroy).pack(side="left", expand=True, fill="x")

        win.after(120, do_generate)
        self._popup_front(win)

    def _open_register_from_code(self, parent_win, code: str, field_item):
        """Pre-filled registration form; OK registers and opens Candidate Registry."""
        ra_default = dec_default = ""
        date_default = self.last_date or self.date_var.get()
        if field_item is not None:
            try:
                ra_default, dec_default = format_ra_dec(field_item["coord"])
            except Exception:
                pass

        rwin = ctk.CTkToplevel(parent_win)
        rwin.title(f"Register Candidate — {code}")
        rwin.geometry("480x540")
        rwin.configure(fg_color="#121212")

        ctk.CTkLabel(rwin, text="🚩  Register Discovery Candidate",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#00a8ff").pack(pady=(18, 2))

        code_disp = ctk.CTkFrame(rwin, fg_color="#0a1a0a", corner_radius=6)
        code_disp.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkLabel(code_disp,
                     text=f"Object Code:  {code}",
                     font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
                     text_color="#4cd137").pack(pady=8)

        frm = ctk.CTkFrame(rwin, fg_color="transparent")
        frm.pack(fill="both", expand=True, padx=24)

        def _r(label, default=""):
            ctk.CTkLabel(frm, text=label, text_color="#aaa",
                         font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(6, 1))
            var = tk.StringVar(value=str(default))
            ctk.CTkEntry(frm, textvariable=var).pack(fill="x")
            return var

        ra_var   = _r("Discovery RA  (HH:MM:SS.s):", ra_default)
        dec_var  = _r("Discovery Dec (+DD:MM:SS.s):", dec_default)
        date_var = _r("Discovery Date (YYYY-MM-DD):", date_default)
        mag_var  = _r("Magnitude (optional):")
        mot_var  = _r("Motion \"/min (optional):")
        unc_var  = _r("Uncertainty arcsec:", "60")
        note_var = _r("Note:")

        def do_ok():
            try:
                register_candidate(
                    object_code          = code,
                    discovery_ra         = ra_var.get().strip(),
                    discovery_dec        = dec_var.get().strip(),
                    discovery_date_local = date_var.get().strip(),
                    predicted_mag        = float(mag_var.get()) if mag_var.get().strip() else None,
                    motion_arcsec_min    = float(mot_var.get()) if mot_var.get().strip() else None,
                    uncertainty_arcsec   = float(unc_var.get()) if unc_var.get().strip() else None,
                    note                 = note_var.get().strip(),
                    base_dir             = self.output_dir,
                )
                self._refresh_candidate_badge()
                self.log_write(f"Candidate registered: {code}")
                rwin.destroy()
                parent_win.destroy()
                self.on_candidate_registry()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=rwin)

        btn_row = ctk.CTkFrame(rwin, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(10, 18))
        ctk.CTkButton(btn_row,
                      text="✅ OK — Register & Open Registry",
                      fg_color="#1a6b3c", hover_color="#145730",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=do_ok).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Cancel",
                      fg_color="#333", hover_color="#444",
                      command=rwin.destroy).pack(side="left", expand=True, fill="x")

        self._popup_front(rwin, parent=parent_win)

    # ══════════════════════════════════════════
    #  Group 4 – CANDIDATES
    # ══════════════════════════════════════════

    def _refresh_candidate_badge(self):
        """Update badge count from active candidates in registry."""
        if not _CANDIDATE_AVAILABLE:
            return
        try:
            count = len(list_active(self.output_dir))
            self.btn_candidate.configure(
                text=f"🚩 Candidate Registry [{count}]")
        except Exception:
            pass

    def on_candidate_registry(self):
        """Open the Candidate Registry window."""
        if not _CANDIDATE_AVAILABLE:
            return messagebox.showerror(
                "Module Missing",
                "candidate_registry.py not found.\n"
                "Place it in the same folder as gui.py.")

        win = ctk.CTkToplevel(self)
        win.title("SSTAC Candidate Registry")
        win.geometry("1100x780")
        win.configure(fg_color="#0d0d0d")

        # ── Header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(hdr, text="🚩  Candidate Registry",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#00a8ff").pack(side="left")
        self.cand_badge_lbl = ctk.CTkLabel(
            hdr, text="", text_color="#f1c40f",
            font=ctk.CTkFont(size=12))
        self.cand_badge_lbl.pack(side="left", padx=12)

        # ── Candidate table ───────────────────────────────────────────
        tbl_frame = ctk.CTkFrame(win, fg_color="#181818", corner_radius=10)
        tbl_frame.pack(fill="both", expand=True, padx=20, pady=(0, 6))

        cols = ("Code", "Date", "Mode", "Field", "Track", "Status", "Alert",
                "Priority", "Mag", "Motion\"/min", "Unc\"", "Days", "Note")
        tree = ttk.Treeview(tbl_frame, columns=cols, show="headings", height=16)
        widths = [90, 90, 130, 50, 50, 110, 70, 70, 55, 90, 70, 50, 200]
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="center")
        tree.column("Note", anchor="w")

        # Alert colour tags
        tree.tag_configure("GREEN",  foreground="#2ecc71")
        tree.tag_configure("YELLOW", foreground="#f1c40f")
        tree.tag_configure("RED",    foreground="#e74c3c")
        tree.tag_configure("INACTIVE", foreground="#555")

        vsb = ttk.Scrollbar(tbl_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        vsb.pack(side="right", fill="y")

        def _refresh_table():
            for row in tree.get_children():
                tree.delete(row)
            try:
                all_c = list_all(self.output_dir)
            except Exception:
                return
            active_count = 0
            from object_code import from_base36
            for c in all_c:
                status = c.get("status", "")
                is_active = status in ACTIVE_STATUSES
                if is_active:
                    active_count += 1
                tag = c.get("alert_level", "GREEN") if is_active else "INACTIVE"
                
                code = c.get("object_code", "")
                f_idx, track = "-", "-"
                if len(code) == 7 and code.startswith("T"):
                    try:
                        f_idx = f"{from_base36(code[5]):03d}"
                        track = f"{from_base36(code[6]):02d}"
                    except:
                        pass
                
                tree.insert("", "end", iid=c["object_code"], values=(
                    code,
                    c.get("date_local", ""),
                    c.get("mode", "")[:15],
                    f_idx,
                    track,
                    status,
                    c.get("alert_level", "") if is_active else "-",
                    f"{c.get('priority', 0):.3f}" if is_active else "-",
                    f"{c.get('predicted_mag', '')}" if c.get("predicted_mag") else "-",
                    f"{c.get('motion_arcsec_min', 0):.2f}",
                    f"{c.get('uncertainty_arcsec', 0):.0f}",
                    f"{c.get('days_since_discovery', 0):.0f}",
                    c.get("note", ""),
                ), tags=(tag,))
            self.cand_badge_lbl.configure(
                text=f"Active: {active_count} | Total: {len(all_c)}")
            self._refresh_candidate_badge()

        _refresh_table()

        # ── Action buttons row ────────────────────────────────────────
        btn_bar = ctk.CTkFrame(win, fg_color="transparent")
        btn_bar.pack(fill="x", padx=20, pady=4)

        def _selected_code():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Select", "Select a candidate first.", parent=win)
                return None
            return sel[0]

        # ── 1. Register new candidate ─────────────────────────────────
        def do_register():
            sel_field = None
            if self.tree.selection():
                sel_field = self.tree.item(self.tree.selection()[0])["values"][1]

            rwin = ctk.CTkToplevel(win)
            rwin.title("Register Candidate")
            rwin.geometry("480x600")
            rwin.configure(fg_color="#121212")






            ctk.CTkLabel(rwin, text="Register New Candidate",
                         font=ctk.CTkFont(size=14, weight="bold"),
                         text_color="#00a8ff").pack(pady=(18, 6))

            frm = ctk.CTkFrame(rwin, fg_color="transparent")
            frm.pack(fill="x", padx=24)

            def _r(label, default=""):
                ctk.CTkLabel(frm, text=label, text_color="#aaa",
                             font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(8,1))
                var = tk.StringVar(value=str(default))
                ctk.CTkEntry(frm, textvariable=var).pack(fill="x")
                return var

            field_var  = _r("Field Name (NE_YYYYMMDD_NNN):", sel_field or "")
            track_var  = _r("Track No:", "1")
            ra_var     = _r("Discovery RA (HH:MM:SS.s):")
            dec_var    = _r("Discovery Dec (+DD:MM:SS.s):")
            date_var_r = _r("Discovery Date (YYYY-MM-DD):", self.last_date or "")
            mag_var    = _r("Predicted Mag (optional):")
            mot_var    = _r("Motion arcsec/min (optional):")
            unc_var    = _r("Uncertainty arcsec (optional):", "60")
            note_var   = _r("Note:")

            def do_save():
                try:
                    code = encode_from_field(
                        field_var.get().strip(), int(track_var.get()))
                    register_candidate(
                        object_code          = code,
                        discovery_ra         = ra_var.get().strip(),
                        discovery_dec        = dec_var.get().strip(),
                        discovery_date_local = date_var_r.get().strip(),
                        predicted_mag        = float(mag_var.get()) if mag_var.get().strip() else None,
                        motion_arcsec_min    = float(mot_var.get()) if mot_var.get().strip() else None,
                        uncertainty_arcsec   = float(unc_var.get()) if unc_var.get().strip() else None,
                        note                 = note_var.get().strip(),
                        base_dir             = self.output_dir,
                    )
                    self.log_write(f"Candidate registered: {code}")
                    _refresh_table()
                    rwin.destroy()
                    messagebox.showinfo("Registered", f"Candidate {code} registered.", parent=win)
                except Exception as e:
                    messagebox.showerror("Error", str(e), parent=rwin)

            ctk.CTkButton(frm, text="💾 Register",
                          fg_color="#1a6b3c", hover_color="#145730",
                          command=do_save).pack(fill="x", pady=(18, 4))
            ctk.CTkButton(frm, text="Cancel",
                          fg_color="#333", command=rwin.destroy).pack(fill="x")
            self._popup_front(rwin, parent=win)

        # ── 2. Paste Find_Orb ephemeris ───────────────────────────────
        def do_ephemeris():
            code = _selected_code()
            if not code:
                return

            ewin = ctk.CTkToplevel(win)
            ewin.title(f"Find_Orb Ephemeris — {code}")
            ewin.geometry("600x520")
            ewin.configure(fg_color="#121212")






            ctk.CTkLabel(ewin, text=f"Paste Find_Orb ephemeris for {code}",
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color="#00a8ff").pack(pady=(18, 6), padx=20, anchor="w")
            ctk.CTkLabel(ewin, text="Supports both HH:MM and fractional-day formats",
                         text_color="#555", font=ctk.CTkFont(size=10)).pack(
                             padx=20, anchor="w")

            txt = ctk.CTkTextbox(ewin, fg_color="#0a0a0a", text_color="#a4b0be",
                                 font=ctk.CTkFont(family="Consolas", size=11))
            txt.pack(fill="both", expand=True, padx=20, pady=10)

            def do_save():
                try:
                    result = update_ephemeris(code, txt.get("1.0", "end"),
                                             base_dir=self.output_dir)
                    n = len(result.get("ephemeris", []))
                    self.log_write(f"Ephemeris updated: {code} ({n} rows)")
                    _refresh_table()
                    ewin.destroy()
                    messagebox.showinfo("Updated",
                        f"Ephemeris updated: {n} rows parsed.", parent=win)
                except Exception as e:
                    messagebox.showerror("Error", str(e), parent=ewin)

            ctk.CTkButton(ewin, text="💾 Parse & Save",
                          fg_color="#2c5364", command=do_save).pack(
                              fill="x", padx=20, pady=(0, 4))
            ctk.CTkButton(ewin, text="Cancel",
                          fg_color="#333", command=ewin.destroy).pack(
                              fill="x", padx=20, pady=(0, 12))
            self._popup_front(ewin, parent=win)

        # ── 3. Update status ──────────────────────────────────────────
        def do_status():
            code = _selected_code()
            if not code:
                return

            swin = ctk.CTkToplevel(win)
            swin.title(f"Update Status — {code}")
            swin.geometry("380x280")
            swin.configure(fg_color="#121212")






            ctk.CTkLabel(swin, text=f"Update status: {code}",
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color="#00a8ff").pack(pady=(18, 10), padx=20)

            status_var = tk.StringVar(value=STATUS_UNCONFIRMED)
            ctk.CTkLabel(swin, text="New Status:", text_color="#aaa").pack(
                anchor="w", padx=20)
            ctk.CTkOptionMenu(swin, variable=status_var,
                              values=[STATUS_UNCONFIRMED, STATUS_CONFIRMED,
                                      STATUS_SUBMITTED, STATUS_LOST,
                                      STATUS_REJECTED]).pack(
                fill="x", padx=20, pady=4)

            ctk.CTkLabel(swin, text="MPC Designation (optional):",
                         text_color="#aaa").pack(anchor="w", padx=20, pady=(10,0))
            desig_var = tk.StringVar()
            ctk.CTkEntry(swin, textvariable=desig_var).pack(
                fill="x", padx=20, pady=4)

            def do_save():
                try:
                    update_status(code, status_var.get(),
                                  mpc_desig=desig_var.get().strip() or None,
                                  base_dir=self.output_dir)
                    self.log_write(f"Candidate status: {code} → {status_var.get()}")
                    _refresh_table()
                    swin.destroy()
                except Exception as e:
                    messagebox.showerror("Error", str(e), parent=swin)

            ctk.CTkButton(swin, text="💾 Update",
                          fg_color="#1a6b3c", command=do_save).pack(
                              fill="x", padx=20, pady=(12, 4))
            ctk.CTkButton(swin, text="Cancel",
                          fg_color="#333", command=swin.destroy).pack(
                              fill="x", padx=20)
            self._popup_front(swin, parent=win)

        # ── 4. Refresh & Clean ────────────────────────────────────────
        def do_refresh():
            from candidate_registry import delete_rejected_candidates
            try:
                deleted_count = delete_rejected_candidates(base_dir=self.output_dir)
                if deleted_count > 0:
                    self.log_write(f"Removed {deleted_count} rejected candidate(s).")
            except Exception as e:
                self.log_write(f"Error clearing rejected candidates: {e}")
            _refresh_table()

        # ── 5. Tonight's follow-ups ───────────────────────────────────
        def do_tonight():
            try:
                from astro_utils import fixed_local_window
                from config import MBA_NIGHT_START, MBA_NIGHT_END
                w_start, w_end = fixed_local_window(
                    self.date_var.get(), MBA_NIGHT_START, MBA_NIGHT_END,
                    self.utc_offset)
                targets = get_tonight_followups(
                    date_str_local   = self.date_var.get(),
                    utc_offset       = self.utc_offset,
                    location         = self.location,
                    min_alt_deg      = 25.0,
                    window_start_utc = w_start,
                    window_end_utc   = w_end,
                    base_dir         = self.output_dir,
                )
                if not targets:
                    messagebox.showinfo("Tonight's Follow-ups",
                        "No active candidates observable tonight.", parent=win)
                    return

                msg_lines = [f"{'Code':<10} {'Alert':<7} {'Pri':>6} "
                             f"{'RA':<13} {'Dec':<14} {'Mag':>5} {'Motion\":m':>9}"]
                msg_lines.append("─" * 72)
                for t in targets:
                    msg_lines.append(
                        f"{t['object_code']:<10} "
                        f"{t.get('alert_level',''):<7} "
                        f"{t.get('priority',0):>6.3f} "
                        f"{t.get('followup_ra',''):<13} "
                        f"{t.get('followup_dec',''):<14} "
                        f"{t.get('predicted_mag') or '-':>5} "
                        f"{t.get('motion_arcsec_min',0):>9.2f}")

                tgt_win = ctk.CTkToplevel(win)
                tgt_win.title(f"Tonight's Follow-ups — {self.date_var.get()}")
                tgt_win.geometry("760x400")
                tgt_win.configure(fg_color="#0d0d0d")
                ctk.CTkLabel(tgt_win,
                             text=f"Follow-up targets for {self.date_var.get()} "
                                  f"({len(targets)} candidates)",
                             font=ctk.CTkFont(size=13, weight="bold"),
                             text_color="#00a8ff").pack(pady=(16, 6), padx=20, anchor="w")
                tb = ctk.CTkTextbox(tgt_win, fg_color="#0a0a0a", text_color="#a4b0be",
                                    font=ctk.CTkFont(family="Consolas", size=11))
                tb.pack(fill="both", expand=True, padx=20, pady=(0, 16))
                tb.insert("end", "\n".join(msg_lines))
                tb.configure(state="disabled")
                self._popup_front(tgt_win, parent=win)

            except Exception:
                self.log_write("=== FOLLOWUP ERROR ===")
                self.log_write(traceback.format_exc())
                messagebox.showerror("Error", "Failed to get tonight's follow-ups.", parent=win)

        # ── 2.5 Generate Online ───────────────────────────────────────
        def do_generate_online():
            code = _selected_code()
            if not code:
                return
            
            try:
                from candidate_panel import GenerateEphemerisDialog
            except ImportError:
                messagebox.showerror("Error", "candidate_panel.py not found.", parent=win)
                return

            dlg = GenerateEphemerisDialog(win, object_code=code, base_dir=self.output_dir, transient_over=win)
            win.wait_window(dlg)
            _refresh_table()

        # ── 2.6 Upload Observation ────────────────────────────────────
        def do_upload_observation():
            code = _selected_code()
            if not code:
                return
            
            try:
                from candidate_panel import UploadObservationDialog
            except ImportError:
                messagebox.showerror("Error", "candidate_panel.py not found.", parent=win)
                return

            dlg = UploadObservationDialog(win, object_code=code, base_dir=self.output_dir, transient_over=win)
            win.wait_window(dlg)
            _refresh_table()

        # ── Button bar layout ─────────────────────────────────────────
        for txt, cmd, color in [
            ("➕ Register New",        do_register,    "#1a6b3c"),
            ("📤 Upload Observation",  do_upload_observation, "#2c5364"),
            ("🔄 Update Status",       do_status,      "#4a2c6b"),
            ("🌐 Gen. Ephemeris",      do_generate_online,    "#1a4a6b"),
            ("🔃 Refresh",             do_refresh,     "#333"),
        ]:
            ctk.CTkButton(btn_bar, text=txt, fg_color=color,
                          hover_color=color + "cc",
                          command=cmd).pack(side="left", expand=True,
                                            fill="x", padx=3)

        win.protocol("WM_DELETE_WINDOW", lambda: [
            self._refresh_candidate_badge(), win.destroy()])
        self._popup_front(win)

    # ══════════════════════════════════════════
    #  Group 5 – MPC REPORTING
    # ══════════════════════════════════════════

    def on_export_mpc_coverage(self):
        """Export OBSERVED fields from performance log → MPC sky-coverage files.

        Creates 2 files:
          - mpc_coverage_O58_YYYYDDD.txt   (legacy email format)
          - mpc_pointings_O58_YYYYDDD.json (JSON Pointings API)
        """
        if not _MPC_AVAILABLE:
            return messagebox.showerror(
                "Module Missing",
                "mpc_coverage_export.py not found.\n"
                "Please place it in the same folder as gui.py.")

        win = ctk.CTkToplevel(self)
        win.title("Export MPC Sky Coverage")
        win.geometry("460x500")





        win.configure(fg_color="#121212")

        ctk.CTkLabel(win, text="MPC Sky Coverage Export",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#00a8ff").pack(pady=(20, 4))
        ctk.CTkLabel(win, text="skycov@cfa.harvard.edu  |  minorplanetcenter.net/pointings",
                     text_color="#555", font=ctk.CTkFont(size=10)).pack(pady=(0, 12))

        frm = ctk.CTkFrame(win, fg_color="transparent")
        frm.pack(fill="x", padx=30)

        def _row(label, default):
            ctk.CTkLabel(frm, text=label, text_color="#aaa",
                         font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(8, 1))
            var = tk.StringVar(value=str(default))
            ctk.CTkEntry(frm, textvariable=var).pack(fill="x")
            return var

        # Pre-fill from active observatory code (from loc_var key, default O58)
        default_code = self.loc_var.get().replace("MPC-", "") if self.loc_var.get() else "O58"
        obs_code_var = _row("MPC Observatory Code:", default_code)
        date_var     = _row("Survey Date (YYYY-MM-DD):", self.last_date or self.date_var.get())
        fov_var      = _row("FOV (arcmin):", f"{self.fov_deg * 60:.1f}")
        limmag_var   = _row("Default Limiting Magnitude:", "19.5")
        filter_var   = _row("Filter (UNFILTERED / r / V / C):", "UNFILTERED")
        exptime_var  = _row("Exposure Time (seconds):", "60")

        def do_export():
            try:
                rows = load_observed_history_rows(self.output_dir)
                if not rows:
                    messagebox.showwarning(
                        "No Observed Data",
                        "No OBSERVED fields found in performance log.\n\n"
                        "Complete the '📋 Input Field Performance' step\n"
                        "with status = OBSERVED for each field first.",
                        parent=win)
                    return

                result = export_mpc_coverage(
                    observed_rows   = rows,
                    obs_code        = obs_code_var.get().strip().upper(),
                    date_local_str  = date_var.get().strip(),
                    output_dir      = self.output_dir,
                    utc_offset      = self.utc_offset,
                    fov_arcmin      = float(fov_var.get()),
                    filter_name     = filter_var.get().strip(),
                    exposure_sec    = float(exptime_var.get()),
                    default_lim_mag = float(limmag_var.get()),
                )

                diag = result.get("diagnostics", {})
                avail = ", ".join(diag.get("available_dates", []))

                self.log_write(
                    f"MPC Coverage exported: {result['n_fields']} fields "
                    f"for {diag.get('target_date', date_var.get().strip())}")
                self.log_write(
                    f"  Matched {diag.get('matched',0)} / "
                    f"{diag.get('total_rows',0)} observed rows | "
                    f"Dates in log: {avail}")
                self.log_write(f"  TXT  → {result['legacy_file'].name}")
                self.log_write(f"  JSON → {result['json_file'].name}")

                messagebox.showinfo(
                    "Export Successful",
                    f"Exported {result['n_fields']} fields for "
                    f"{diag.get('target_date', date_var.get().strip())}\n\n"
                    f"Matched: {diag.get('matched',0)} of "
                    f"{diag.get('total_rows',0)} observed rows\n"
                    f"Dates in log: {avail}\n\n"
                    f"Legacy TXT  (email format):\n  {result['legacy_file'].name}\n\n"
                    f"JSON (Pointings API):\n  {result['json_file'].name}\n\n"
                    f"Next step:\n"
                    f"  Email TXT to skycov@cfa.harvard.edu\n"
                    f"  Subject must contain: 'Sky Coverage'",
                    parent=win)
                win.destroy()

            except ValueError as ve:
                # Date not found — show helpful message with available dates
                self.log_write(f"MPC Export: date not found — {str(ve)[:120]}")
                messagebox.showerror("Date Not Found", str(ve), parent=win)

            except Exception:
                self.log_write("=== MPC EXPORT ERROR ===")
                self.log_write(traceback.format_exc())
                messagebox.showerror("Export Error",
                                     "Failed to export MPC coverage.\n"
                                     "Check System Console for details.", parent=win)

        ctk.CTkButton(frm, text="💾 Export Files",
                      fg_color="#1a6b3c", hover_color="#145730",
                      command=do_export).pack(fill="x", pady=(20, 4))
        ctk.CTkButton(frm, text="Cancel",
                      fg_color="#333", hover_color="#444",
                      command=win.destroy).pack(fill="x")
        self._popup_front(win)

    def on_submit_mpc_email(self):
        """Submit coverage report to MPC — 3 methods:
        1. SMTP (Gmail + App Password)
        2. Open in default Mail App (mailto://)
        3. Copy to Clipboard → paste in webmail
        """
        if not _MPC_AVAILABLE:
            return messagebox.showerror(
                "Module Missing",
                "mpc_coverage_export.py not found.\n"
                "Please place it in the same folder as gui.py.")

        win = ctk.CTkToplevel(self)
        win.title("Submit to MPC via Email")
        win.geometry("480x780")





        win.configure(fg_color="#121212")

        ctk.CTkLabel(win, text="Submit Sky Coverage to MPC",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color="#00a8ff").pack(pady=(20, 2))
        ctk.CTkLabel(win, text="→  skycov@cfa.harvard.edu",
                     text_color="#2ecc71",
                     font=ctk.CTkFont(size=11)).pack(pady=(0, 4))

        # ── Hotmail/Outlook warning banner ────────────────────────────
        warn = ctk.CTkFrame(win, fg_color="#3a1a00", corner_radius=8)
        warn.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(
            warn,
            text="⚠  Hotmail / Outlook: Basic Auth is disabled\n"
                 "   Use Gmail App Password, or\n"
                 "   use 'Open Mail App' / 'Copy Text' instead",
            text_color="#f39c12", font=ctk.CTkFont(size=11),
            justify="left").pack(anchor="w", padx=12, pady=8)

        frm = ctk.CTkFrame(win, fg_color="transparent")
        frm.pack(fill="x", padx=20)

        def _row(label, default, show=""):
            ctk.CTkLabel(frm, text=label, text_color="#aaa",
                         font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(8, 1))
            var = tk.StringVar(value=str(default))
            e = ctk.CTkEntry(frm, textvariable=var)
            if show:
                e.configure(show=show)
            e.pack(fill="x")
            return var

        default_code = self.loc_var.get().replace("MPC-", "") if self.loc_var.get() else "O58"
        obs_code_var = _row("MPC Observatory Code:", default_code)
        date_var     = _row("Survey Date (YYYY-MM-DD):", self.last_date or self.date_var.get())
        fov_var      = _row("FOV (arcmin):", f"{self.fov_deg * 60:.1f}")
        limmag_var   = _row("Default Limiting Magnitude:", "19.5")

        # ── SMTP section ─────────────────────────────────────────────
        ctk.CTkLabel(frm, text="── SMTP (Gmail + App Password only) ──",
                     text_color="#555", font=ctk.CTkFont(size=10)).pack(
                         anchor="w", pady=(14, 0))
        from_var  = _row("Your Email (From):", "you@gmail.com")
        smtp_var  = _row("SMTP Host:", "smtp.gmail.com")
        user_var  = _row("SMTP Username:", "")
        pass_var  = _row("App Password (16 chars):", "", show="*")

        ctk.CTkLabel(frm,
            text="💡 Gmail: myaccount.google.com → Security → App Passwords",
            text_color="#3b8ed0", font=ctk.CTkFont(size=10)).pack(
                anchor="w", pady=(2, 0))

        dry_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(frm, text="Dry run — preview only, do NOT send",
                        variable=dry_var,
                        text_color="#f1c40f").pack(anchor="w", pady=(10, 0))

        # ── shared: build night_rows ──────────────────────────────────
        def _get_night_rows():
            from mpc_coverage_export import filter_rows_by_date
            all_rows = load_observed_history_rows(self.output_dir)
            if not all_rows:
                messagebox.showwarning("No Observed Data",
                    "No OBSERVED fields found in performance log.", parent=win)
                return None, None, None
            target_date = date_var.get().strip()
            night_rows, diag = filter_rows_by_date(all_rows, target_date)
            if not night_rows:
                available = ", ".join(diag.get("available_dates", []))
                messagebox.showerror(
                    "Date Not Found",
                    f"No OBSERVED fields for '{target_date}'.\n\n"
                    f"Dates available in log:\n  {available or '(none)'}",
                    parent=win)
                return None, None, None
            text = build_legacy_coverage_text(
                rows            = night_rows,
                obs_code        = obs_code_var.get().strip().upper(),
                date_local_str  = target_date,
                fov_arcmin      = float(fov_var.get()),
                default_lim_mag = float(limmag_var.get()),
            )
            return night_rows, diag, text

        # ── Action 1: SMTP send / dry-run preview ────────────────────
        def do_smtp():
            try:
                night_rows, diag, coverage_text = _get_night_rows()
                if night_rows is None:
                    return
                ok, msg = submit_via_email(
                    coverage_text = coverage_text,
                    sender_email  = from_var.get().strip(),
                    smtp_host     = smtp_var.get().strip(),
                    smtp_user     = user_var.get().strip(),
                    smtp_pass     = pass_var.get(),
                    dry_run       = bool(dry_var.get()),
                )
                tag = "DRY RUN" if dry_var.get() else "SUBMITTED"
                self.log_write(
                    f"MPC SMTP [{tag}]: {'OK' if ok else 'FAILED'} | "
                    f"{diag.get('matched',0)} fields for {date_var.get().strip()}")
                if ok:
                    messagebox.showinfo(f"MPC — {tag}", msg, parent=win)
                    if not dry_var.get():
                        win.destroy()
                else:
                    # Friendly hint for common auth errors
                    hint = ""
                    if "535" in str(msg) or "Authentication" in str(msg):
                        hint = (
                            "\n\n💡 Fix:\n"
                            "• Gmail: Use App Password (not your regular password)\n"
                            "  myaccount.google.com → Security → App Passwords\n"
                            "• Hotmail/Outlook: Use 'Open Mail App' button instead"
                        )
                    messagebox.showerror("SMTP Error", msg + hint, parent=win)
            except Exception:
                self.log_write("=== MPC SMTP ERROR ===")
                self.log_write(traceback.format_exc())
                messagebox.showerror("Error", "SMTP failed.\nSee System Console.", parent=win)

        # ── Action 2: Open in default Mail App (mailto://) ───────────
        def do_mailto():
            import urllib.parse, webbrowser
            try:
                night_rows, diag, coverage_text = _get_night_rows()
                if night_rows is None:
                    return
                subject = "Sky Coverage"
                # mailto has ~2000 char limit in some clients — warn if long
                if len(coverage_text) > 1800:
                    messagebox.showwarning(
                        "Long Email",
                        f"Coverage text is {len(coverage_text)} chars.\n"
                        "Some mail clients truncate mailto links.\n"
                        "Use 'Copy Text' and paste manually if it fails.",
                        parent=win)
                params  = urllib.parse.urlencode(
                    {"subject": subject, "body": coverage_text},
                    quote_via=urllib.parse.quote)
                mailto  = f"mailto:skycov@cfa.harvard.edu?{params}"
                webbrowser.open(mailto)
                self.log_write(
                    f"MPC Mailto: opened mail app | "
                    f"{diag.get('matched',0)} fields for {date_var.get().strip()}")
            except Exception:
                self.log_write("=== MPC MAILTO ERROR ===")
                self.log_write(traceback.format_exc())
                messagebox.showerror("Error", "Failed to open mail app.", parent=win)

        # ── Action 3: Copy to clipboard ──────────────────────────────
        def do_copy():
            try:
                night_rows, diag, coverage_text = _get_night_rows()
                if night_rows is None:
                    return
                win.clipboard_clear()
                win.clipboard_append(coverage_text)
                win.update()
                self.log_write(
                    f"MPC Coverage copied to clipboard | "
                    f"{diag.get('matched',0)} fields for {date_var.get().strip()}")
                messagebox.showinfo(
                    "Copied!",
                    f"Coverage text copied to clipboard.\n\n"
                    f"Manual sending steps:\n"
                    f"1. Open Outlook / Gmail in browser\n"
                    f"2. New Email → To: skycov@cfa.harvard.edu\n"
                    f"3. Subject: Sky Coverage\n"
                    f"4. Paste (Ctrl+V) in the email body\n"
                    f"5. Send",
                    parent=win)
            except Exception:
                messagebox.showerror("Error", "Failed to copy to clipboard.", parent=win)

        # ── Button layout ─────────────────────────────────────────────
        ctk.CTkLabel(frm, text="── Select sending method ──",
                     text_color="#555", font=ctk.CTkFont(size=10)).pack(
                         anchor="w", pady=(16, 4))

        ctk.CTkButton(frm, text="📧 SMTP Send / Dry-Run Preview",
                      fg_color="#2c5364", hover_color="#203a43",
                      command=do_smtp).pack(fill="x", pady=2)

        ctk.CTkButton(frm, text="🖥 Open in Mail App  (Outlook / Thunderbird)",
                      fg_color="#1a4a6b", hover_color="#154060",
                      command=do_mailto).pack(fill="x", pady=2)

        ctk.CTkButton(frm, text="📋 Copy Coverage Text  (paste in webmail)",
                      fg_color="#4a3000", hover_color="#3a2400",
                      command=do_copy).pack(fill="x", pady=2)

        ctk.CTkButton(frm, text="Cancel",
                      fg_color="#333", hover_color="#444",
                      command=win.destroy).pack(fill="x", pady=(10, 4))
        self._popup_front(win)


# ─────────────────────────────────────────────

if __name__ == '__main__':
    app = SkySurveyApp()
    app.mainloop()
