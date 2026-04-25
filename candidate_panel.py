"""
candidate_panel.py  —  SSTAC v1.3
Candidate Registry UI panel + dialogs.

=== IMPORTANT: Blank-window bug workaround ===
CustomTkinter's CTkToplevel has an internal `after(200, iconbitmap)` call
during __init__ which causes blank windows on Windows when widgets are
packed + grab_set() is called too quickly. See GitHub issues #469, #1219,
#1690, #1934.

Solution: All dialogs inherit from `SSTACDialog` which uses plain
`tk.Toplevel` (no CTk delay) while still using CTk widgets inside. This
eliminates the blank-window bug entirely.
"""

import traceback
import tkinter as tk
from tkinter import ttk, messagebox

import customtkinter as ctk

from candidate_registry import (
    register_candidate, update_ephemeris, update_ephemeris_online, update_status, update_uncertainty,
    list_all, list_active, get_candidate, update_observation,

    encode_from_field, decode_object_code,
    parse_findorb_ephemeris,
    STATUS_UNCONFIRMED, STATUS_CONFIRMED, STATUS_SUBMITTED,
    STATUS_LOST, STATUS_REJECTED,
    alert_level, FOV_ARCSEC,
)


# ═════════════════════════════════════════════════════════════════════
#  Base dialog class — uses tk.Toplevel (NOT ctk.CTkToplevel)
# ═════════════════════════════════════════════════════════════════════
#
# This is the KEY fix for the blank-window bug.  Using tk.Toplevel
# bypasses CustomTkinter's internal 200ms iconbitmap delay that
# interferes with widget rendering + grab_set() on Windows.
#
# We still use CTk widgets INSIDE the window — they render normally.

class SSTACDialog(tk.Toplevel):
    """Base class for all SSTAC dialogs — uses plain tk.Toplevel.

    Bypasses the CTkToplevel blank-window bug while still rendering
    CustomTkinter widgets inside with the SSTAC dark theme.
    Title bar is darkened on Windows 10+ via DWM API.
    """

    def __init__(self, parent, title="SSTAC", geometry="600x500", transient_over=None):
        super().__init__(parent)
        self.title(title)
        self.geometry(geometry)
        self.configure(bg="#121212")
        # Transient: dialog stays on top of parent, minimizes with it
        # If transient_over is given, use it instead of parent for z-order
        try:
            self.transient(transient_over if transient_over is not None else parent)
        except Exception:
            pass
        # Apply dark titlebar on Windows 10+ to match CTk theme
        self._set_dark_titlebar()
        # Bring to front immediately
        self.lift()
        self.focus_force()

    def _set_dark_titlebar(self):
        """Use Windows DWM API to darken the titlebar (Win10 1809+)."""
        try:
            import sys
            if not sys.platform.startswith("win"):
                return
            import ctypes
            from ctypes import wintypes
            # Ensure window is created before we query its HWND
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Win10 1903+), fallback 19 (1809)
            value = ctypes.c_int(1)
            for attr in (20, 19):
                res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
                if res == 0:
                    break
        except Exception:
            pass  # fail silently on older Windows / other OS

    def apply_modal_grab(self):
        """Call this at END of subclass __init__ after all widgets packed."""
        try:
            self.update_idletasks()
            self.grab_set()
        except Exception:
            pass


# Alert colours matching SSTAC dark theme
ALERT_COLOR = {
    "GREEN":  "#2ecc71",
    "YELLOW": "#f1c40f",
    "RED":    "#e74c3c",
}

STATUS_COLOR = {
    STATUS_UNCONFIRMED: "#3b8ed0",
    STATUS_CONFIRMED:   "#2ecc71",
    STATUS_SUBMITTED:   "#9b59b6",
    STATUS_LOST:        "#e74c3c",
    STATUS_REJECTED:    "#7f8c8d",
}


# ═════════════════════════════════════════════════════════════════════
#  Register New Candidate Dialog
# ═════════════════════════════════════════════════════════════════════

class RegisterCandidateDialog(SSTACDialog):
    """
    Dialog to register a new candidate from a selected SSTAC field.
    Pre-fills object code from field_name if provided.
    If field_item is given, auto-fills RA, Dec, and Date from field.
    """

    def __init__(self, parent, field_name: str = "", base_dir=None,
                 transient_over=None, field_item=None, date_local=None,
                 utc_offset=7.0):
        super().__init__(parent,
                         title="Register Discovery Candidate",
                         geometry="580x720",
                         transient_over=transient_over)
        self.minsize(540, 680)
        self.base_dir    = base_dir
        self.result_code = None
        self._field_item = field_item
        self._date_local = date_local
        self._utc_offset = utc_offset

        # Use a regular CTkFrame (not scrollable) — height fits content
        frm = ctk.CTkFrame(self, fg_color="#121212")
        frm.pack(fill="both", expand=True, padx=20, pady=15)

        # ── Object Code (auto-generated or manual) ──────────────────
        ctk.CTkLabel(frm, text="SSTAC Object Code",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#00a8ff").pack(anchor="w", pady=(0, 2))

        code_row = ctk.CTkFrame(frm, fg_color="transparent")
        code_row.pack(fill="x", pady=(0, 8))

        self.var_code = tk.StringVar()
        self.ent_code = ctk.CTkEntry(code_row, textvariable=self.var_code,
                                     font=ctk.CTkFont(family="Consolas", size=16, weight="bold"),
                                     placeholder_text="e.g. T631X31")
        self.ent_code.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(code_row, text="Decode", width=80,
                      command=self._decode_code).pack(side="left")

        # Decoded info label
        self.lbl_decoded = ctk.CTkLabel(frm, text="(enter code or generate from field)",
                                        font=ctk.CTkFont(family="Consolas", size=10),
                                        text_color="#666",
                                        justify="left")
        self.lbl_decoded.pack(anchor="w", pady=(0, 10))

        # ── Field Name shortcut ──────────────────────────────────────
        ctk.CTkLabel(frm, text="Or generate from Field Name",
                     text_color="#888",
                     font=ctk.CTkFont(size=11)).pack(anchor="w")

        frow = ctk.CTkFrame(frm, fg_color="transparent")
        frow.pack(fill="x", pady=(2, 12))

        self.var_field = tk.StringVar(value=field_name)
        ctk.CTkEntry(frow, textvariable=self.var_field,
                     placeholder_text="NE_20260419_003").pack(side="left", fill="x",
                                                              expand=True, padx=(0, 8))
        self.var_track = tk.StringVar(value="1")
        ctk.CTkEntry(frow, textvariable=self.var_track,
                     width=60, placeholder_text="Trk").pack(side="left", padx=(0, 8))
        ctk.CTkButton(frow, text="Generate Code", width=120,
                      command=self._generate_code).pack(side="left")

        # ── Discovery position ───────────────────────────────────────
        ctk.CTkLabel(frm, text="Discovery RA  (hh:mm:ss.s)",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(8, 2))
        self.var_ra = tk.StringVar()
        ctk.CTkEntry(frm, textvariable=self.var_ra,
                     placeholder_text="12:34:56.7").pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(frm, text="Discovery Dec  (±dd:mm:ss)",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(0, 2))
        self.var_dec = tk.StringVar()
        ctk.CTkEntry(frm, textvariable=self.var_dec,
                     placeholder_text="+05:12:34").pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(frm, text="Discovery Date (local, YYYY-MM-DD)",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(0, 2))
        self.var_date = tk.StringVar()
        ctk.CTkEntry(frm, textvariable=self.var_date,
                     placeholder_text="2026-04-19").pack(fill="x", pady=(0, 8))

        # ── Optional quick fields ────────────────────────────────────
        opt_row = ctk.CTkFrame(frm, fg_color="transparent")
        opt_row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(opt_row, text="Est. Mag", text_color="#aaa").pack(side="left")
        self.var_mag = tk.StringVar()
        ctk.CTkEntry(opt_row, textvariable=self.var_mag, width=70,
                     placeholder_text="18.7").pack(side="left", padx=8)

        ctk.CTkLabel(opt_row, text='Motion "/min', text_color="#aaa").pack(side="left")
        self.var_motion = tk.StringVar()
        ctk.CTkEntry(opt_row, textvariable=self.var_motion, width=70,
                     placeholder_text="0.8").pack(side="left", padx=8)

        ctk.CTkLabel(frm, text="Note (optional)",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(0, 2))
        self.var_note = tk.StringVar()
        ctk.CTkEntry(frm, textvariable=self.var_note,
                     placeholder_text="Fast mover, NEO candidate...").pack(fill="x", pady=(0, 12))

        # ── Buttons ──────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(frm, fg_color="transparent")
        btn_row.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(btn_row, text="Register Candidate",
                      fg_color="#2ecc71", hover_color="#27ae60",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self._do_register).pack(side="left", expand=True,
                                                   fill="x", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Cancel",
                      fg_color="#333", hover_color="#444",
                      command=self.destroy).pack(side="left", expand=True, fill="x")

        # Pre-generate code if field_name already given
        if field_name:
            self._generate_code()

        # Auto-fill RA / Dec / Date from the selected field if provided
        self._autofill_from_field()

        # Apply modal grab AFTER all widgets packed
        self.apply_modal_grab()

    def _autofill_from_field(self):
        """Populate RA, Dec, and Discovery Date from the selected field."""
        # Discovery Date — prefer explicit date_local, else extract from field_name
        if self._date_local:
            self.var_date.set(self._date_local)
        else:
            # Try to parse date from field_name like "NE_20260418_002"
            fname = self.var_field.get().strip()
            try:
                date_part = fname.split("_")[1]    # "20260418"
                y, m, d = date_part[:4], date_part[4:6], date_part[6:8]
                self.var_date.set(f"{y}-{m}-{d}")
            except Exception:
                pass

        # RA / Dec — from the SkyCoord on the field
        if self._field_item is not None:
            try:
                from astropy import units as u
                coord = self._field_item.get("coord")
                if coord is not None:
                    ra_s  = coord.ra.to_string(unit=u.hour, sep=":",
                                               precision=2, pad=True)
                    dec_s = coord.dec.to_string(unit=u.deg, sep=":",
                                                precision=1,
                                                alwayssign=True, pad=True)
                    self.var_ra.set(ra_s)
                    self.var_dec.set(dec_s)
            except Exception:
                pass

            # Est. magnitude if field carries a sky-quality limiting mag
            try:
                lim = self._field_item.get("limit_mag_single")
                if lim:
                    # Use limit mag as reasonable upper bound for estimated mag
                    self.var_mag.set(f"{float(lim) - 0.5:.1f}")
            except Exception:
                pass

    def _generate_code(self):
        try:
            field = self.var_field.get().strip()
            track = int(self.var_track.get().strip() or "1")
            code  = encode_from_field(field, track)
            self.var_code.set(code)
            self._decode_code()
        except Exception as e:
            self.lbl_decoded.configure(text=f"Error: {e}", text_color="#e74c3c")

    def _decode_code(self):
        try:
            meta = decode_object_code(self.var_code.get())
            self.lbl_decoded.configure(
                text=(f"Date: {meta['date_str']}  |  Mode: {meta['mode']}\n"
                      f"Field #{meta['field_index']:03d}  |  Track {meta['track_no']:02d}"),
                text_color="#2ecc71")
        except Exception as e:
            self.lbl_decoded.configure(text=f"Bad code: {e}", text_color="#e74c3c")

    def _do_register(self):
        code = self.var_code.get().strip().upper()
        ra   = self.var_ra.get().strip()
        dec  = self.var_dec.get().strip()
        d    = self.var_date.get().strip()

        if not code or not ra or not dec or not d:
            messagebox.showerror("Missing Fields",
                                 "Object Code, RA, Dec, and Discovery Date are required.",
                                 parent=self)
            return
        try:
            mag    = float(self.var_mag.get())    if self.var_mag.get().strip()    else None
            motion = float(self.var_motion.get()) if self.var_motion.get().strip() else None

            cand = register_candidate(
                object_code=code,
                discovery_ra=ra,
                discovery_dec=dec,
                discovery_date_local=d,
                predicted_mag=mag,
                motion_arcsec_min=motion,
                note=self.var_note.get().strip(),
                base_dir=self.base_dir,
            )
            self.result_code = code
            messagebox.showinfo("Registered",
                                f"Candidate {code} registered.\n"
                                f"Alert level: {cand['alert_level']}",
                                parent=self)
            self.destroy()
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)


# ═════════════════════════════════════════════════════════════════════
#  Import Find_Orb Ephemeris Dialog
# ═════════════════════════════════════════════════════════════════════

class ImportEphemerisDialog(SSTACDialog):
    """Paste Find_Orb plain-text ephemeris and attach to a candidate."""

    def __init__(self, parent, object_code: str = "", base_dir=None, transient_over=None):
        super().__init__(parent,
                         title="Import Find_Orb Ephemeris",
                         geometry="640x580",
                         transient_over=transient_over)
        self.minsize(600, 540)
        self.base_dir = base_dir

        frm = ctk.CTkFrame(self, fg_color="#121212")
        frm.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(frm, text="Object Code",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.var_code = tk.StringVar(value=object_code)
        ctk.CTkEntry(frm, textvariable=self.var_code,
                     font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                     placeholder_text="T631X31").pack(fill="x", pady=(2, 12))

        ctk.CTkLabel(frm, text="Paste Find_Orb Ephemeris Text",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w")
        ctk.CTkLabel(frm,
                     text="Copy the plain-text ephemeris table from Find_Orb and paste below",
                     text_color="#555", font=ctk.CTkFont(size=10)).pack(anchor="w", pady=(0, 4))

        self.txt_eph = ctk.CTkTextbox(frm, fg_color="#0a0a0a",
                                      text_color="#a4b0be",
                                      font=ctk.CTkFont(family="Consolas", size=10),
                                      height=280)
        self.txt_eph.pack(fill="both", expand=True, pady=(0, 12))

        # Parse preview label
        self.lbl_preview = ctk.CTkLabel(frm, text="",
                                        font=ctk.CTkFont(size=11),
                                        text_color="#888")
        self.lbl_preview.pack(anchor="w", pady=(0, 8))

        btn_row = ctk.CTkFrame(frm, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(btn_row, text="Preview Parse",
                      fg_color="#333", hover_color="#444",
                      command=self._preview).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Import & Save",
                      fg_color="#3b8ed0", hover_color="#2980b9",
                      command=self._import).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Cancel",
                      fg_color="#333", hover_color="#444",
                      command=self.destroy).pack(side="left")

        self.apply_modal_grab()

    def _preview(self):
        text = self.txt_eph.get("1.0", "end")
        rows = parse_findorb_ephemeris(text)
        if rows:
            r0 = rows[0]
            rl = rows[-1]
            self.lbl_preview.configure(
                text=f"✓ {len(rows)} rows parsed  |  "
                     f"{r0['date_utc']} → {rl['date_utc']}  |  "
                     f"Motion: {r0.get('motion_arcsec_min','?')} \"/min",
                text_color="#2ecc71")
        else:
            self.lbl_preview.configure(text="✗ No valid rows found — check format",
                                       text_color="#e74c3c")

    def _import(self):
        code = self.var_code.get().strip().upper()
        text = self.txt_eph.get("1.0", "end")
        if not code:
            messagebox.showerror("Missing Code", "Enter the Object Code first.", parent=self)
            return
        try:
            cand = update_ephemeris(code, text, base_dir=self.base_dir)
            rows = cand["ephemeris"]
            messagebox.showinfo("Imported",
                                f"{len(rows)} ephemeris rows attached to {code}.\n"
                                f"Motion: {cand.get('motion_arcsec_min','?')} \"/min",
                                parent=self)
            self.destroy()
        except Exception as e:
            messagebox.showerror("Import Error", str(e), parent=self)


# ═════════════════════════════════════════════════════════════════════
#  Generate Online Ephemeris Dialog
# ═════════════════════════════════════════════════════════════════════

class GenerateEphemerisDialog(SSTACDialog):
    """Upload observation text and fetch ephemeris from Find Orb online API."""

    def __init__(self, parent, object_code: str = "", base_dir=None, transient_over=None):
        super().__init__(parent,
                         title="Generate Online Ephemeris",
                         geometry="640x580",
                         transient_over=transient_over)
        self.minsize(600, 540)
        self.base_dir = base_dir

        frm = ctk.CTkFrame(self, fg_color="#121212")
        frm.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(frm, text="Object Code",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.var_code = tk.StringVar(value=object_code)
        ctk.CTkEntry(frm, textvariable=self.var_code,
                     font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
                     placeholder_text="T631X31").pack(fill="x", pady=(2, 12))

        # Parameters
        param_row = ctk.CTkFrame(frm, fg_color="transparent")
        param_row.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(param_row, text="Observatory:", text_color="#aaa").pack(side="left")
        self.var_obs = tk.StringVar(value="O58")
        ctk.CTkEntry(param_row, textvariable=self.var_obs, width=60).pack(side="left", padx=(4, 16))

        ctk.CTkLabel(param_row, text="Step size (mins):", text_color="#aaa").pack(side="left")
        self.var_step = tk.StringVar(value="5")
        ctk.CTkEntry(param_row, textvariable=self.var_step, width=60).pack(side="left", padx=4)

        ctk.CTkLabel(frm, text="Observation Data (Tycho format)",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w")
        
        browse_row = ctk.CTkFrame(frm, fg_color="transparent")
        browse_row.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(browse_row, text="Browse File...", width=120,
                      fg_color="#333", hover_color="#444",
                      command=self._browse_file).pack(side="left")
        self.lbl_file = ctk.CTkLabel(browse_row, text="No file selected", text_color="#888", font=ctk.CTkFont(size=11))
        self.lbl_file.pack(side="left", padx=10)

        self.txt_obs = ctk.CTkTextbox(frm, fg_color="#0a0a0a",
                                      text_color="#a4b0be",
                                      font=ctk.CTkFont(family="Consolas", size=10),
                                      height=180)
        self.txt_obs.pack(fill="both", expand=True, pady=(2, 12))
        
        # Pre-fill observation data if it exists in registry
        if object_code:
            cand = get_candidate(object_code, base_dir)
            if cand and "observation_data" in cand:
                self.txt_obs.insert("1.0", cand["observation_data"])
                self.lbl_file.configure(text="(Loaded from registry)", text_color="#1a6b3c")

        btn_row = ctk.CTkFrame(frm, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(btn_row, text="Generate Ephemeris",
                      fg_color="#2ecc71", hover_color="#27ae60",
                      command=self._generate).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Cancel",
                      fg_color="#333", hover_color="#444",
                      command=self.destroy).pack(side="left")

        self.apply_modal_grab()

    def _browse_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self, title="Select Observation File",
                                          filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.lbl_file.configure(text=path)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.txt_obs.delete("1.0", "end")
                self.txt_obs.insert("1.0", content)
            except Exception as e:
                messagebox.showerror("Read Error", str(e), parent=self)

    def _generate(self):
        code = self.var_code.get().strip().upper()
        obs_data = self.txt_obs.get("1.0", "end").strip()
        obscode = self.var_obs.get().strip()
        
        if not code:
            messagebox.showerror("Missing Code", "Enter the Object Code first.", parent=self)
            return
        if not obs_data:
            messagebox.showerror("Missing Data", "Provide observation data first.", parent=self)
            return
        try:
            step_mins = int(self.var_step.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Input", "Step size must be an integer.", parent=self)
            return

        try:
            cand = update_ephemeris_online(code, obs_data, obscode, step_mins, base_dir=self.base_dir)
            rows = cand.get("ephemeris", [])
            
            lines = [f"Successfully generated {len(rows)} ephemeris rows for {code}."]
            lines.append(f"Motion: {cand.get('motion_arcsec_min', '?')} \"/min")
            lines.append("-" * 72)
            lines.append(f"{'Date (UTC)':<22} {'RA':<14} {'Dec':<15} {'Mag':>6} {'Mot\"/m':>8}")
            lines.append("-" * 72)
            for r in rows:
                mag_str = str(r.get('mag', ''))
                mot = r.get('motion_arcsec_min', 0)
                mot_str = f"{mot:.2f}" if isinstance(mot, (int, float)) else str(mot)
                lines.append(f"{r.get('date_utc', ''):<22} {r.get('ra', ''):<14} {r.get('dec', ''):<15} {mag_str:>6} {mot_str:>8}")
            
            res_text = "\n".join(lines)
            
            res_win = ctk.CTkToplevel(self.master)
            res_win.title(f"Generated Ephemeris — {code}")
            res_win.geometry("650x450")
            res_win.configure(fg_color="#0d0d0d")
            
            ctk.CTkLabel(res_win, text=f"Ephemeris Results: {code}",
                         font=ctk.CTkFont(size=14, weight="bold"),
                         text_color="#00a8ff").pack(pady=(15, 5), padx=20, anchor="w")
                         
            tb = ctk.CTkTextbox(res_win, fg_color="#0a0a0a", text_color="#a4b0be",
                                font=ctk.CTkFont(family="Consolas", size=12))
            tb.pack(fill="both", expand=True, padx=20, pady=(0, 15))
            tb.insert("end", res_text)
            tb.configure(state="disabled")
            
            ctk.CTkButton(res_win, text="Close", fg_color="#333", command=res_win.destroy).pack(pady=(0, 15))
            
            res_win.transient(self.master)
            try:
                res_win.wait_visibility()
            except Exception:
                pass
            res_win.lift()
            res_win.grab_set()
            res_win.focus_force()
            
            self.destroy()
        except Exception as e:
            messagebox.showerror("API Error", f"Failed to generate ephemeris: {e}", parent=self)


# ═════════════════════════════════════════════════════════════════════
#  Upload Observation Dialog
# ═════════════════════════════════════════════════════════════════════

class UploadObservationDialog(SSTACDialog):
    """Dialog to attach raw observation data to a candidate without generating ephemeris."""

    def __init__(self, parent, object_code: str = "", base_dir=None, transient_over=None):
        super().__init__(parent,
                         title="Upload Observation",
                         geometry="640x500",
                         transient_over=transient_over)
        self.minsize(600, 480)
        self.base_dir = base_dir
        self.object_code = object_code

        frm = ctk.CTkFrame(self, fg_color="#121212")
        frm.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(frm, text=f"Attach Observation to Candidate: {object_code}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#00a8ff").pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(frm, text="Observation Data (Tycho format)",
                     text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w")

        browse_row = ctk.CTkFrame(frm, fg_color="transparent")
        browse_row.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(browse_row, text="Browse File...", width=120,
                      fg_color="#333", hover_color="#444",
                      command=self._browse_file).pack(side="left")
        self.lbl_file = ctk.CTkLabel(browse_row, text="No file selected", text_color="#888", font=ctk.CTkFont(size=11))
        self.lbl_file.pack(side="left", padx=10)

        self.txt_obs = ctk.CTkTextbox(frm, fg_color="#0a0a0a",
                                      text_color="#a4b0be",
                                      font=ctk.CTkFont(family="Consolas", size=10),
                                      height=240)
        self.txt_obs.pack(fill="both", expand=True, pady=(2, 12))

        # Pre-fill if exists
        cand = get_candidate(object_code, base_dir)
        if cand and "observation_data" in cand:
            self.txt_obs.insert("1.0", cand["observation_data"])
            self.lbl_file.configure(text="(Loaded from registry)", text_color="#1a6b3c")

        btn_row = ctk.CTkFrame(frm, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(btn_row, text="💾 Save Observation", fg_color="#1a6b3c",
                      command=self._save).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="Cancel", fg_color="#333",
                      command=self.destroy).pack(side="left")

    def _browse_file(self):
        import tkinter.filedialog as fd
        path = fd.askopenfilename(parent=self, title="Select Observation File",
                                  filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if not path:
            return
        self.lbl_file.configure(text=path, text_color="#aaa")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()
            self.txt_obs.delete("1.0", "end")
            self.txt_obs.insert("end", data)
        except Exception as e:
            messagebox.showerror("Read Error", str(e), parent=self)

    def _save(self):
        obs_data = self.txt_obs.get("1.0", "end").strip()
        if not obs_data:
            messagebox.showerror("Missing Data", "Provide observation data first.", parent=self)
            return

        try:
            update_observation(self.object_code, obs_data, base_dir=self.base_dir)
            messagebox.showinfo("Saved", f"Observation data successfully attached to {self.object_code}.", parent=self)
            self.destroy()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to attach observation: {e}", parent=self)


# ═════════════════════════════════════════════════════════════════════
#  Main Candidate Registry Window
# ═════════════════════════════════════════════════════════════════════

class CandidateRegistryWindow(SSTACDialog):
    """
    Full candidate management window.
    Shows all candidates in a table with alert colouring.
    Provides register / import ephemeris / update status actions.
    """

    def __init__(self, parent):
        super().__init__(parent,
                         title="SSTAC — Candidate Registry",
                         geometry="1050x600")
        self.minsize(960, 540)
        self.configure(bg="#0d0d0d")
        self.parent     = parent
        self.base_dir   = getattr(parent, "output_dir", None)
        self._build()
        self.refresh()
        self.apply_modal_grab()

    def _build(self):
        # ── Toolbar ─────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color="#121212", height=48)
        bar.pack(fill="x", padx=10, pady=(10, 0))
        bar.pack_propagate(False)

        ctk.CTkLabel(bar, text="CANDIDATE REGISTRY",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#00a8ff").pack(side="left", padx=16)

        ctk.CTkButton(bar, text="↺ Refresh", width=90,
                      fg_color="#333", hover_color="#444",
                      command=self.refresh).pack(side="right", padx=4, pady=6)
        ctk.CTkButton(bar, text="🌐 Generate Online", width=140,
                      fg_color="#2980b9", hover_color="#1a5276",
                      command=self._open_generate_online).pack(side="right", padx=4, pady=6)
        ctk.CTkButton(bar, text="📥 Import Ephemeris", width=150,
                      fg_color="#2c5364", hover_color="#203a43",
                      command=self._open_import).pack(side="right", padx=4, pady=6)
        ctk.CTkButton(bar, text="＋ Register New", width=130,
                      fg_color="#2ecc71", hover_color="#27ae60",
                      font=ctk.CTkFont(weight="bold"),
                      command=self._open_register).pack(side="right", padx=4, pady=6)

        # Show-all toggle
        self.show_all_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="Show closed",
                        variable=self.show_all_var,
                        command=self.refresh).pack(side="right", padx=12, pady=6)

        # ── Table ────────────────────────────────────────────────────
        t_frame = ctk.CTkFrame(self, fg_color="#181818", corner_radius=10)
        t_frame.pack(fill="both", expand=True, padx=10, pady=10)

        style = ttk.Style()
        style.configure("Reg.Treeview",
                        background="#181818", foreground="white",
                        fieldbackground="#181818", rowheight=30,
                        font=("Consolas", 11), borderwidth=0)
        style.configure("Reg.Treeview.Heading",
                        background="#2b2b2b", foreground="white",
                        font=("Segoe UI", 11, "bold"), borderwidth=0)
        style.map("Reg.Treeview", background=[("selected", "#1f538d")])

        cols = ("Code", "Date", "Mode", "Fld", "Status",
                "Alert", "Unc\"", "Motion\"/min", "Mag", "Days", "Note")
        self.tree = ttk.Treeview(t_frame, columns=cols, show="headings",
                                 style="Reg.Treeview")

        widths = (90, 95, 140, 45, 110, 65, 65, 95, 55, 45, 180)
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="center")
        self.tree.column("Note", anchor="w")

        sc = ttk.Scrollbar(t_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sc.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        sc.pack(side="right", fill="y")

        self.tree.tag_configure("GREEN",  foreground="#2ecc71")
        self.tree.tag_configure("YELLOW", foreground="#f1c40f")
        self.tree.tag_configure("RED",    foreground="#e74c3c")
        self.tree.tag_configure("CLOSED", foreground="#555555")

        # ── Action buttons below table ───────────────────────────────
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkButton(act, text="✓ Mark Confirmed",
                      fg_color="#27ae60", hover_color="#1f8a4d",
                      command=lambda: self._set_status(STATUS_CONFIRMED)
                      ).pack(side="left", padx=4)
        ctk.CTkButton(act, text="✗ Mark Lost",
                      fg_color="#c0392b", hover_color="#922b21",
                      command=lambda: self._set_status(STATUS_LOST)
                      ).pack(side="left", padx=4)
        ctk.CTkButton(act, text="⊘ Mark Rejected",
                      fg_color="#7f8c8d", hover_color="#616a6b",
                      command=lambda: self._set_status(STATUS_REJECTED)
                      ).pack(side="left", padx=4)
        ctk.CTkButton(act, text="🌐 Mark Submitted MPC",
                      fg_color="#8e44ad", hover_color="#7d3c98",
                      command=lambda: self._set_status(STATUS_SUBMITTED)
                      ).pack(side="left", padx=4)

        # Uncertainty update
        unc_row = ctk.CTkFrame(act, fg_color="transparent")
        unc_row.pack(side="right")
        ctk.CTkLabel(unc_row, text='Update Unc (")',
                     text_color="#aaa").pack(side="left", padx=4)
        self.var_unc = tk.StringVar()
        ctk.CTkEntry(unc_row, textvariable=self.var_unc,
                     width=70, placeholder_text="45").pack(side="left", padx=4)
        ctk.CTkButton(unc_row, text="Set",
                      fg_color="#333", hover_color="#444", width=50,
                      command=self._update_unc).pack(side="left", padx=4)

    # ── Refresh ──────────────────────────────────────────────────────

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        cands = list_all(self.base_dir) if self.show_all_var.get() else list_active(self.base_dir)

        for c in cands:
            al     = c.get("alert_level", "GREEN")
            status = c.get("status", "?")
            tag    = al if status in (STATUS_UNCONFIRMED, STATUS_CONFIRMED) else "CLOSED"

            unc   = c.get("uncertainty_arcsec")
            unc_s = f"{unc:.0f}" if unc is not None else "?"
            mot   = c.get("motion_arcsec_min")
            mot_s = f"{mot:.2f}" if mot is not None else "?"
            mag   = c.get("predicted_mag")
            mag_s = f"{mag:.1f}" if mag is not None else "?"

            self.tree.insert("", "end", tags=(tag,), values=(
                c.get("object_code", ""),
                c.get("date_local", ""),
                c.get("mode", "")[:14],
                f"{c.get('field_index', 0):03d}",
                status,
                al,
                unc_s,
                mot_s,
                mag_s,
                f"{c.get('days_since_discovery', 0):.0f}d",
                c.get("note", ""),
            ))

    # ── Dialogs ──────────────────────────────────────────────────────

    def _release_grab(self):
        try:
            self.grab_release()
        except Exception:
            pass

    def _reclaim_grab(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def _open_register(self):
        self._release_grab()
        # Use main CTk app as parent (so CTk widgets render correctly),
        # but transient over self (so z-order is above Registry window).
        dlg = RegisterCandidateDialog(self.parent,
                                      base_dir=self.base_dir,
                                      transient_over=self)
        self.wait_window(dlg)
        self._reclaim_grab()
        self.refresh()

    def _open_import(self):
        code = self._selected_code()
        self._release_grab()
        dlg  = ImportEphemerisDialog(self.parent,
                                     object_code=code or "",
                                     base_dir=self.base_dir,
                                     transient_over=self)
        self.wait_window(dlg)
        self._reclaim_grab()
        self.refresh()

    def _open_generate_online(self):
        code = self._selected_code()
        self._release_grab()
        dlg  = GenerateEphemerisDialog(self.parent,
                                       object_code=code or "",
                                       base_dir=self.base_dir,
                                       transient_over=self)
        self.wait_window(dlg)
        self._reclaim_grab()
        self.refresh()

    def _selected_code(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.item(sel[0])["values"][0]

    def _set_status(self, new_status: str):
        code = self._selected_code()
        if not code:
            messagebox.showwarning("No Selection", "Select a candidate row first.", parent=self)
            return
        mpc = ""
        if new_status == STATUS_SUBMITTED:
            self._release_grab()
            dlg = _MpcDesigDialog(self.parent, transient_over=self)
            self.wait_window(dlg)
            self._reclaim_grab()
            mpc = dlg.result or ""
        try:
            update_status(code, new_status, mpc_desig=mpc or None, base_dir=self.base_dir)
            self.refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)

    def _update_unc(self):
        code = self._selected_code()
        if not code:
            messagebox.showwarning("No Selection", "Select a candidate row first.", parent=self)
            return
        try:
            unc = float(self.var_unc.get())
            cand = update_uncertainty(code, unc, base_dir=self.base_dir)
            self.refresh()
            if cand["status"] == STATUS_LOST:
                messagebox.showwarning("Auto-Marked LOST",
                                       f"{code} uncertainty ({unc:.0f}\") exceeds FOV "
                                       f"({FOV_ARCSEC:.0f}\") — marked LOST.",
                                       parent=self)
        except ValueError:
            messagebox.showerror("Invalid", "Enter a numeric uncertainty in arcseconds.",
                                 parent=self)
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)


# ═════════════════════════════════════════════════════════════════════
#  MPC Designation Dialog (small helper)
# ═════════════════════════════════════════════════════════════════════

class _MpcDesigDialog(SSTACDialog):
    def __init__(self, parent, transient_over=None):
        super().__init__(parent,
                         title="MPC Designation",
                         geometry="360x180",
                         transient_over=transient_over)
        self.result = None

        frm = ctk.CTkFrame(self, fg_color="#121212")
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frm, text="MPC Provisional Designation (optional)",
                     text_color="#aaa").pack(pady=(20, 4))
        self.var = tk.StringVar()
        ctk.CTkEntry(frm, textvariable=self.var,
                     placeholder_text="2026 AB1").pack(fill="x", padx=20)

        btn = ctk.CTkFrame(frm, fg_color="transparent")
        btn.pack(pady=16)
        ctk.CTkButton(btn, text="OK", command=self._ok).pack(side="left", padx=8)
        ctk.CTkButton(btn, text="Skip", fg_color="#333",
                      command=self.destroy).pack(side="left", padx=8)

        self.apply_modal_grab()

    def _ok(self):
        self.result = self.var.get().strip()
        self.destroy()


# ═════════════════════════════════════════════════════════════════════
#  Quick-register helper called from SkySurveyApp
# ═════════════════════════════════════════════════════════════════════

def quick_register_from_field(parent_app, field_name: str,
                              field_item=None, date_local=None,
                              utc_offset=7.0):
    """
    Called from SkySurveyApp.send_to_object_code() when user wants to
    also register the field as a discovery candidate.
    Opens RegisterCandidateDialog pre-filled with field_name, RA, Dec,
    and discovery date from the selected field.
    """
    base_dir = getattr(parent_app, "output_dir", None)
    dlg = RegisterCandidateDialog(parent_app,
                                  field_name=field_name,
                                  base_dir=base_dir,
                                  field_item=field_item,
                                  date_local=date_local,
                                  utc_offset=utc_offset)
    parent_app.wait_window(dlg)
    return dlg.result_code


# Keep backward-compat name for gui.py
def setup_toplevel_safely(window):
    """Legacy helper — now does nothing since SSTACDialog handles it."""
    try:
        window.update_idletasks()
        window.lift()
        window.focus_force()
        window.grab_set()
    except Exception:
        pass
