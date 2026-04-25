"""
gui_handlers_mpc.py
────────────────────
MpcMixin — Group 5 methods for SkySurveyApp.
Handles: MPC Sky Coverage export and email submission.
Extracted from gui.py for independent maintenance.
"""

import traceback
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

from history_utils import load_observed_history_rows

# MPC coverage export (optional — graceful fallback)
try:
    from mpc_coverage_export import (
        export_mpc_coverage, build_legacy_coverage_text,
        submit_via_email, submit_json_via_requests
    )
    _MPC_AVAILABLE = True
except ImportError:
    _MPC_AVAILABLE = False


class MpcMixin:
    """Mixin that provides Group 5 – MPC Reporting actions for SkySurveyApp."""

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
