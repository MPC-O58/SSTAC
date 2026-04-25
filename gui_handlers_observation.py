"""
gui_handlers_observation.py
────────────────────────────
ObservationMixin — Group 3 methods for SkySurveyApp.
Handles: Sky Quality, Field Performance, Object Code generator, Register Candidate form.
Extracted from gui.py for independent maintenance.
"""

import traceback
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

from astro_utils import utc_to_local_dt, format_ra_dec
from sky_quality_bridge import launch_sky_quality, import_latest_sky_quality
from history_utils import append_field_performance
from object_code import ObjectCodeWindow

# Candidate registry (optional — graceful fallback)
try:
    from candidate_registry import register_candidate
    _CANDIDATE_AVAILABLE = True
except ImportError:
    _CANDIDATE_AVAILABLE = False


class ObservationMixin:
    """Mixin that provides Group 3 – Observation Tools actions for SkySurveyApp."""

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
