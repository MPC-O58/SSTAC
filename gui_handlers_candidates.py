"""
gui_handlers_candidates.py
───────────────────────────
CandidateMixin — Group 4 methods for SkySurveyApp.
Handles: Candidate Registry badge refresh and full registry window.
Extracted from gui.py for independent maintenance.
"""

import traceback
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import customtkinter as ctk

# Candidate registry (optional — graceful fallback)
try:
    from candidate_registry import (
        register_candidate, update_ephemeris, update_status,
        update_uncertainty, list_active, list_all,
        get_tonight_followups, encode_from_field,
        STATUS_UNCONFIRMED, STATUS_CONFIRMED,
        STATUS_SUBMITTED, STATUS_LOST, STATUS_REJECTED,
        ACTIVE_STATUSES, alert_level
    )
    _CANDIDATE_AVAILABLE = True
except ImportError:
    _CANDIDATE_AVAILABLE = False


class CandidateMixin:
    """Mixin that provides Group 4 – Candidates actions for SkySurveyApp."""

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
                             font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(8, 1))
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

            # Open the dialog implemented in candidate_panel.py
            dlg = GenerateEphemerisDialog(win, object_code=code, base_dir=self.output_dir, transient_over=win)
            win.wait_window(dlg)
            _refresh_table()

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
                         text_color="#aaa").pack(anchor="w", padx=20, pady=(10, 0))
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
