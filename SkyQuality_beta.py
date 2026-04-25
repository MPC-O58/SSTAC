import os
import math
import csv
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Circle

from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.visualization import ImageNormalize, PercentileInterval, AsinhStretch
from astropy.modeling import models, fitting

# --- Constants & Style Config ---
APP_TITLE = "SKY QUALITY ANALYZER"
VERSION = "BETA"
CREDIT = "Credit: SSTAC Project"

# Modern Color Palette
CLR_BG = "#0f172a"      # Deep Slate
CLR_PANEL = "#1e293b"   # Lighter Slate
CLR_ACCENT = "#38bdf8"  # Sky Blue
CLR_GREEN = "#22c55e"   # Emerald
CLR_ORANGE = "#fb923c"  # Orange
CLR_TEXT = "#f8fafc"    # Ghost White
CLR_SUBTEXT = "#94a3b8" # Muted Blue

DEFAULT_PIXEL_SCALE = 1.43
DEFAULT_ZP = 21.0
THRESHOLD_SIGMA = 8.0
DETECT_BOX = 5
CUTOUT_HALF = 8
ANN_IN = 9
ANN_OUT = 14
MIN_PEAK_SNR = 8.0
MIN_FWHM_PX = 1.2
MAX_FWHM_PX = 8.0
MAX_ELLIPTICITY = 0.55
MIN_STAR_SEP = 10
MAX_STARS_TO_FIT = 250
DEFAULT_STACKS = 30
DEFAULT_K_CONST = 7.6
CSV_LOG_FILE = "SSTAC_Observation_Log.csv"

# --- CORE LOGIC (KEEP UNCHANGED) ---

def detect_local_maxima(data, threshold_sigma, detect_box, border):
    _, med, std = sigma_clipped_stats(data, sigma=3.0, maxiters=5)
    threshold = med + threshold_sigma * std
    half = detect_box // 2
    h, w = data.shape
    candidates = []
    for y in range(border, h - border):
        for x in range(border, w - border):
            val = data[y, x]
            if not np.isfinite(val) or val < threshold: continue
            patch = data[y-half:y+half+1, x-half:x+half+1]
            if val == np.nanmax(patch) and np.count_nonzero(patch == val) == 1:
                candidates.append((x, y, float(val)))
    candidates.sort(key=lambda t: t[2], reverse=True)
    return candidates[:5000], med, std

def enforce_min_separation(candidates, min_sep, max_keep):
    kept = []
    min_sep2 = min_sep * min_sep
    for x, y, peak in candidates:
        too_close = False
        for kx, ky, _ in kept:
            if (x - kx)**2 + (y - ky)**2 < min_sep2:
                too_close = True; break
        if not too_close: kept.append((x, y, peak))
        if len(kept) >= max_keep: break
    return kept

def fit_gaussian_1d(profile):
    x = np.arange(profile.size, dtype=float)
    prof = np.where(np.isfinite(profile) & (profile > 0), profile, 0.0)
    amp0 = float(np.max(prof))
    if amp0 <= 0: return None
    total = np.sum(prof)
    mean0 = float(np.sum(x * prof) / total) if total > 0 else (len(prof)-1)/2.0
    std0 = max(math.sqrt(max(np.sum(((x - mean0)**2) * prof) / total, 0.25)), 0.6)
    model = models.Gaussian1D(amplitude=amp0, mean=mean0, stddev=std0)
    fitter = fitting.LevMarLSQFitter()
    try:
        fit = fitter(model, x, prof)
        std = abs(float(fit.stddev.value))
        if std <= 0 or fit.amplitude.value <= 0: return None
        return {"fwhm": 2.354820045 * std}
    except: return None

# --- UI CLASS ---

class SkyQualityV24:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_TITLE} - {VERSION}")
        self.root.geometry("1450x950")
        self.root.configure(bg=CLR_BG)
        
        self.current_header = None
        self.current_path = None
        self.raw_data = None
        self.current_exptime = 30.0
        self.found_zp = DEFAULT_ZP

        self.setup_ui()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        
        # Style Definitions
        style.configure("TFrame", background=CLR_BG)
        style.configure("Card.TFrame", background=CLR_PANEL)
        style.configure("TLabel", background=CLR_PANEL, foreground=CLR_TEXT, font=("Inter", 10))
        style.configure("Header.TLabel", background=CLR_BG, foreground=CLR_ACCENT, font=("Inter Bold", 20))
        style.configure("Section.TLabel", background=CLR_PANEL, foreground=CLR_ACCENT, font=("Inter Bold", 11))
        
        # Modern Button Styles
        style.configure("Action.TButton", font=("Inter Bold", 10), background=CLR_ACCENT, foreground=CLR_BG)
        style.map("Action.TButton", background=[('active', CLR_TEXT)])
        
        style.configure("TProgressbar", thickness=8, background=CLR_ACCENT, troughcolor=CLR_PANEL)

        # Layout Start
        # Top Title Bar
        header_frame = ttk.Frame(self.root)
        header_frame.pack(fill="x", padx=30, pady=(25, 15))
        
        ttk.Label(header_frame, text=APP_TITLE, style="Header.TLabel").pack(side="left")
        ttk.Label(header_frame, text=VERSION, background=CLR_BG, foreground=CLR_SUBTEXT, font=("Inter", 10, "italic")).pack(side="left", padx=15, pady=(8, 0))

        main_container = ttk.Frame(self.root)
        main_container.pack(fill="both", expand=True, padx=30, pady=(0, 10))

        # Left Control Panel (Sidebar)
        left_panel = ttk.Frame(main_container, width=380, style="TFrame")
        left_panel.pack(side="left", fill="y", padx=(0, 25))
        left_panel.pack_propagate(False)

        # -- Settings Card --
        settings_card = ttk.Frame(left_panel, style="Card.TFrame", padding=20)
        settings_card.pack(fill="x", pady=(0, 20))
        
        ttk.Label(settings_card, text="SYSTEM CONFIGURATION", style="Section.TLabel").pack(anchor="w", pady=(0, 15))
        
        def create_entry(parent, label, var_val):
            row = ttk.Frame(parent, style="Card.TFrame")
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=label).pack(side="left")
            var = tk.StringVar(value=str(var_val))
            ent = tk.Entry(row, textvariable=var, font=("Inter", 11), bg="#334155", fg="white", borderwidth=0, insertbackground="white", width=10, justify='center')
            ent.pack(side="right")
            return var

        self.scale_var = create_entry(settings_card, "Pixel Scale (asec/px)", DEFAULT_PIXEL_SCALE)
        self.k_var = create_entry(settings_card, "System Constant (K)", DEFAULT_K_CONST)
        self.stack_var = create_entry(settings_card, "Planned Stacks", DEFAULT_STACKS)

        # -- Actions Card --
        actions_card = ttk.Frame(left_panel, style="Card.TFrame", padding=20)
        actions_card.pack(fill="x", pady=(0, 20))
        
        ttk.Label(actions_card, text="OPERATIONS", style="Section.TLabel").pack(anchor="w", pady=(0, 15))
        
        btn_frames = ttk.Frame(actions_card, style="Card.TFrame")
        btn_frames.pack(fill="x")
        
        ttk.Button(btn_frames, text="OPEN FITS FILE", style="Action.TButton", command=self.load_fits).pack(fill="x", pady=4)
        ttk.Button(btn_frames, text="RUN ANALYSIS", style="Action.TButton", command=self.cal_sky_quality).pack(fill="x", pady=4)
        
        sub_btns = ttk.Frame(btn_frames, style="Card.TFrame")
        sub_btns.pack(fill="x", pady=5)
        tk.Button(sub_btns, text="Save Image", command=self.save_plot, bg="#475569", fg="white", font=("Inter", 9), borderwidth=0, padx=10).pack(side="left", expand=True, fill="x", padx=(0,2))
        tk.Button(sub_btns, text="Header Info", command=self.show_header, bg="#475569", fg="white", font=("Inter", 9), borderwidth=0, padx=10).pack(side="left", expand=True, fill="x", padx=(2,0))

        # Result Terminal
        self.res_text = tk.Text(left_panel, font=("Consolas", 11), bg="#020617", fg="#cbd5e1", borderwidth=0, padx=15, pady=15, highlightthickness=1, highlightbackground="#334155")
        self.res_text.pack(fill="both", expand=True, pady=(0, 10))

        self.progress = ttk.Progressbar(left_panel, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x")

        # Visualizer Panel
        viz_frame = ttk.Frame(main_container, style="Card.TFrame")
        viz_frame.pack(side="right", fill="both", expand=True)

        self.fig, self.ax = plt.subplots(figsize=(10, 10), facecolor=CLR_PANEL)
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.canvas = FigureCanvasTkAgg(self.fig, master=viz_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=2, pady=2)

        # Footer / Credit Bar
        footer = ttk.Frame(self.root, style="TFrame")
        footer.pack(fill="x", side="bottom", padx=30, pady=10)
        ttk.Label(footer, text=CREDIT, background=CLR_BG, foreground=CLR_SUBTEXT, font=("Inter", 9)).pack(side="right")
        ttk.Label(footer, text="System Ready", background=CLR_BG, foreground=CLR_SUBTEXT, font=("Inter", 9)).pack(side="left")

    # --- IMPLEMENTATION FUNCTIONS (LOGIC PRESERVED) ---

    def show_header(self):
        if not self.current_header: return
        win = tk.Toplevel(self.root); win.title("FITS Header Viewer"); win.geometry("700x600")
        tree = ttk.Treeview(win, columns=("K","V"), show="headings")
        tree.heading("K", text="KEYWORD"); tree.heading("V", text="VALUE")
        tree.column("K", width=200); tree.column("V", width=450)
        for k, v in self.current_header.items(): tree.insert("", "end", values=(k,v))
        tree.pack(fill="both", expand=True)

    def save_plot(self):
        if self.current_path:
            p = os.path.splitext(self.current_path)[0] + "_analysis.png"
            self.fig.savefig(p, dpi=200, bbox_inches='tight')
            messagebox.showinfo("Export Successful", f"Analysis image saved to:\n{p}")

    def load_fits(self):
        path = filedialog.askopenfilename(filetypes=[("FITS Files","*.fits *.fit *.fts")])
        if not path: return
        self.current_path = path
        self.progress["value"] = 20
        self.root.update_idletasks()

        try:
            with fits.open(path) as hdul:
                self.current_header = hdul[0].header
                data = hdul[0].data.astype(float)
                while data.ndim > 2: data = data[0]

            self.raw_data = data
            self.current_exptime = self.current_header.get('EXPTIME', 30.0)
            self.found_zp = next((self.current_header.get(k) for k in ['MAGZP','ZP','PHOTZP'] if k in self.current_header), DEFAULT_ZP)

            self.ax.clear()
            norm = ImageNormalize(self.raw_data, interval=PercentileInterval(99.5), stretch=AsinhStretch())
            self.ax.imshow(self.raw_data, cmap="magma", origin="lower", norm=norm) # Changed to Magma for modern look
            
            t_bottom = f"FILE: {os.path.basename(path)}"
            self.ax.text(0.02, 0.02, t_bottom, color="white", fontsize=9, ha='left', va='bottom', transform=self.ax.transAxes, bbox=dict(facecolor='black', alpha=0.5, edgecolor='none'))

            self.ax.set_axis_off()
            self.canvas.draw()
            
            self.res_text.delete('1.0', tk.END)
            self.res_text.insert(tk.END, f"STATUS: FITS Loaded\nEXPTIME: {self.current_exptime}s\nZP: {self.found_zp}\n\nClick 'RUN ANALYSIS' to begin.")
            self.progress["value"] = 100

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load FITS: {str(e)}")
            self.progress["value"] = 0

    def cal_sky_quality(self):
        if self.raw_data is None:
            messagebox.showwarning("Warning", "Please load a FITS file first.")
            return
            
        self.progress["value"] = 10
        self.root.update_idletasks()
        
        try:
            pixel_scale = float(self.scale_var.get())
            k_const = float(self.k_var.get())
            stacks = int(self.stack_var.get())
            
            T_single = self.current_exptime
            T_stacked = self.current_exptime * stacks
            
            airmass = self.current_header.get('AIRMASS', 'N/A')
            altitude = self.current_header.get('OBJCTALT', self.current_header.get('ALTITUDE', 'N/A'))
            if isinstance(airmass, float): airmass = f"{airmass:.3f}"
            if isinstance(altitude, float): altitude = f"{altitude:.2f}"
            
            border = max(CUTOUT_HALF + 2, int(math.ceil(ANN_OUT)) + 1)
            candidates, med, std = detect_local_maxima(self.raw_data, THRESHOLD_SIGMA, DETECT_BOX, border)
            stars = enforce_min_separation(candidates, MIN_STAR_SEP, MAX_STARS_TO_FIT)

            self.progress["value"] = 40
            self.root.update_idletasks()
            
            fwhms, overlays = [], []
            for sx, sy, _ in stars:
                sub = self.raw_data[sy-CUTOUT_HALF:sy+CUTOUT_HALF+1, sx-CUTOUT_HALF:sx+CUTOUT_HALF+1]
                if sub.shape[0] < (CUTOUT_HALF*2 + 1): continue
                
                yy, xx = np.indices(sub.shape)
                rr = np.sqrt((xx - CUTOUT_HALF)**2 + (yy - CUTOUT_HALF)**2)
                sky_vals = sub[(rr >= ANN_IN) & (rr <= ANN_OUT)]
                if sky_vals.size < 10: continue
                
                _, l_med, l_std = sigma_clipped_stats(sky_vals)
                if (np.max(sub) - l_med) / l_std < MIN_PEAK_SNR:
                    overlays.append(((sx, sy), "red")); continue

                sky_sub = np.where(sub - l_med > 0, sub - l_med, 0.0)
                fit_x = fit_gaussian_1d(np.sum(sky_sub, axis=0))
                fit_y = fit_gaussian_1d(np.sum(sky_sub, axis=1))
                if fit_x and fit_y:
                    fx, fy = fit_x['fwhm'], fit_y['fwhm']
                    if MIN_FWHM_PX <= fx <= MAX_FWHM_PX and MIN_FWHM_PX <= fy <= MAX_FWHM_PX and (1.0 - min(fx,fy)/max(fx,fy)) <= MAX_ELLIPTICITY:
                        fwhms.append(math.sqrt(fx*fy))
                        overlays.append(((sx, sy), "lime")); continue
                overlays.append(((sx, sy), "red"))

            self.progress["value"] = 80
            
            sky_mag = self.found_zp - 2.5 * math.log10((med/self.current_exptime)/(pixel_scale**2)) if med > 0 else 0
            med_seeing = np.median(fwhms) * pixel_scale if fwhms else 0
            n_acc = len(fwhms)
            conf = "HIGH" if n_acc >= 100 else ("MEDIUM" if n_acc >= 30 else "LOW")

            limit_mag_1fr = 0.0
            limit_mag_stack = 0.0
            if med_seeing > 0 and sky_mag > 0:
                if T_single > 0:
                    limit_mag_1fr = k_const + (0.5 * sky_mag) - (2.5 * math.log10(med_seeing)) + (1.25 * math.log10(T_single))
                if T_stacked > 0:
                    limit_mag_stack = k_const + (0.5 * sky_mag) - (2.5 * math.log10(med_seeing)) + (1.25 * math.log10(T_stacked))

            # CSV Logging
            file_exists = os.path.isfile(CSV_LOG_FILE)
            try:
                with open(CSV_LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(['Timestamp', 'Filename', 'ExpTime_s', 'Stacks', 'Sky_mag_arcsec2', 'Seeing_FWHM_arcsec', 'K_Const', 'Est_Limit_Mag_1Fr', 'Est_Limit_Mag_Stack', 'Stars', 'Confidence', 'Airmass', 'Altitude'])
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    filename = os.path.basename(self.current_path)
                    writer.writerow([timestamp, filename, self.current_exptime, stacks, f"{sky_mag:.2f}", f"{med_seeing:.2f}", k_const, f"{limit_mag_1fr:.2f}", f"{limit_mag_stack:.2f}", n_acc, conf, airmass, altitude])
            except: pass

            self.ax.clear()
            norm = ImageNormalize(self.raw_data, interval=PercentileInterval(99.5), stretch=AsinhStretch())
            self.ax.imshow(self.raw_data, cmap="gray", origin="lower", norm=norm)
            for (p, c) in overlays: self.ax.add_patch(Circle(p, radius=6.5 if c=="lime" else 4, fill=False, edgecolor=c, lw=0.8))
            
            t_top = f"Sky: {sky_mag:.2f} | Seeing: {med_seeing:.2f}\" | Est. Mag: {limit_mag_1fr:.2f}"
            self.ax.text(0.5, 0.96, t_top, color=CLR_ACCENT, fontsize=12, fontweight='bold', ha='center', va='top', transform=self.ax.transAxes, bbox=dict(facecolor='black', alpha=0.7, edgecolor='none', boxstyle='round,pad=0.5'))
            
            self.ax.set_axis_off()
            self.canvas.draw()

            # Modernized Terminal Output
            self.res_text.delete('1.0', tk.END)
            self.res_text.tag_configure("label", foreground=CLR_SUBTEXT)
            self.res_text.tag_configure("val_sky", foreground=CLR_ORANGE, font=("Consolas", 14, "bold"))
            self.res_text.tag_configure("val_seeing", foreground=CLR_GREEN, font=("Consolas", 14, "bold"))
            self.res_text.tag_configure("val_limit", foreground=CLR_ACCENT, font=("Consolas", 14, "bold"))
            
            self.res_text.insert(tk.END, "ANALYSIS COMPLETE\n", "val_limit")
            self.res_text.insert(tk.END, "---------------------------\n")
            self.res_text.insert(tk.END, "SKY BACKGROUND\n", "label")
            self.res_text.insert(tk.END, f"{sky_mag:.2f} mag/arcsec²\n\n", "val_sky")
            self.res_text.insert(tk.END, "SEEING (FWHM)\n", "label")
            self.res_text.insert(tk.END, f"{med_seeing:.2f} arcsec\n\n", "val_seeing")
            self.res_text.insert(tk.END, "LIMITING MAGNITUDE\n", "label")
            self.res_text.insert(tk.END, f"Single: {limit_mag_1fr:.2f}\nStacked: {limit_mag_stack:.2f}\n\n", "val_limit")
            self.res_text.insert(tk.END, f"Stars: {n_acc} | Conf: {conf}\n")
            self.res_text.insert(tk.END, f"Alt: {altitude} | Air: {airmass}")
            
            self.progress["value"] = 100
            
        except Exception as e: 
            messagebox.showerror("Analysis Error", str(e))
            self.progress["value"] = 0

if __name__ == "__main__":
    root = tk.Tk()
    # Set app icon or properties if needed here
    app = SkyQualityV24(root)
    root.mainloop()