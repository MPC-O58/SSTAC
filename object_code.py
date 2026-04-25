import re
from datetime import datetime, date
import tkinter as tk
from tkinter import ttk, messagebox
from config import BASE36, MODE_SECTOR_MAP, INV_MODE_MAP


def to_base36(n: int, width: int) -> str:
    if n < 0:
        return "0" * width
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = BASE36[r] + s
    return s.rjust(width, "0")

def from_base36(s: str) -> int:
    n = 0
    for ch in s.upper():
        if ch in BASE36:
            n = n * 36 + BASE36.index(ch)
    return n

class ObjectCodeWindow(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)
        self.title("SSTAC | Object Code")
        self.geometry("900x700")
        self.configure(bg="#0b1325")
        self.base_year = 2020
        self._setup_styles()
        self._build_ui()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TFrame", background="#0b1325")
        style.configure("Main.TLabelframe", background="#121f38", foreground="#00a8ff", bordercolor="#2c3e50")
        style.configure("Main.TLabelframe.Label", background="#121f38", foreground="#00a8ff", font=("Segoe UI", 12, "bold"))
        style.configure("TLabel", background="#121f38", foreground="#dfe4ea", font=("Segoe UI", 10))
        style.configure("Result.TLabel", background="#121f38", foreground="#4cd137", font=("Consolas", 18, "bold"))

    def _build_ui(self):
        header = tk.Frame(self, bg="#0b1325")
        header.pack(fill="x", pady=20, padx=40)
        tk.Label(header, text="SSTAC OBJECT ENCODER", fg="#00a8ff", bg="#0b1325", font=("Segoe UI", 20, "bold")).pack(side="left")
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True, padx=40)
        enc_box = ttk.Labelframe(container, text=" Generate from SSTAC Field Name ", style="Main.TLabelframe", padding=20)
        enc_box.pack(fill="x", pady=(0, 20))
        enc_box.columnconfigure(1, weight=1)
        ttk.Label(enc_box, text="Field Name:").grid(row=0, column=0, sticky="w")
        self.ent_field = tk.Entry(enc_box, bg="#1e2b45", fg="white", font=("Consolas", 11), borderwidth=0)
        self.ent_field.grid(row=0, column=1, columnspan=2, sticky="ew", padx=10, ipady=6)
        self.ent_field.insert(0, "NE_20260416_001")
        ttk.Label(enc_box, text="Track No:").grid(row=1, column=0, sticky="w", pady=15)
        self.ent_track = tk.Entry(enc_box, bg="#1e2b45", fg="white", font=("Consolas", 11), borderwidth=0, width=12)
        self.ent_track.grid(row=1, column=1, sticky="w", padx=10, ipady=4)
        self.ent_track.insert(0, "1")
        self.lbl_gen_res = ttk.Label(enc_box, text="Code: TXXXXXX", style="Result.TLabel")
        self.lbl_gen_res.grid(row=2, column=0, columnspan=2, sticky="w", pady=10)
        btn_gen = tk.Button(enc_box, text="GENERATE CODE", bg="#00a8ff", fg="white", font=("Segoe UI", 10, "bold"), command=self.on_generate, relief="flat", padx=15)
        btn_gen.grid(row=2, column=2, sticky="e")
        dec_box = ttk.Labelframe(container, text=" Decode Object Info ", style="Main.TLabelframe", padding=20)
        dec_box.pack(fill="x")
        dec_box.columnconfigure(1, weight=1)
        ttk.Label(dec_box, text="Input Code:").grid(row=0, column=0, sticky="w")
        self.ent_code = tk.Entry(dec_box, bg="#1e2b45", fg="#fbc531", font=("Consolas", 16, "bold"), borderwidth=0)
        self.ent_code.grid(row=0, column=1, sticky="ew", padx=10, ipady=5)
        btn_dec = tk.Button(dec_box, text="DECODE", bg="#44bd32", fg="white", font=("Segoe UI", 10, "bold"), command=self.on_decode, relief="flat", padx=20)
        btn_dec.grid(row=0, column=2, sticky="e")
        self.txt_dec_res = tk.Text(dec_box, height=7, bg="#0b1325", fg="#dfe4ea", font=("Consolas", 10), borderwidth=0, padx=10, pady=10)
        self.txt_dec_res.grid(row=1, column=0, columnspan=3, sticky="ew", pady=15)

    def set_field_name(self, name):
        self.ent_field.delete(0, tk.END)
        self.ent_field.insert(0, name)

    def parse_field_name(self, name):
        m = re.match(r"^(NE|HI)_(\d{8})_(\d{3})$", name.strip().upper())
        if not m:
            raise ValueError("Field name must look like NE_YYYYMMDD_001 or HI_YYYYMMDD_001")
        prefix, date_str, f_idx = m.groups()
        mode = "NARROW ECLIPTIC" if prefix == "NE" else "HIGH INCLINATION"
        sector = "NIGHT"
        dt = datetime.strptime(date_str, "%Y%m%d").date()
        return mode, sector, dt.strftime("%Y-%m-%d"), int(f_idx)

    def on_generate(self):
        try:
            name = self.ent_field.get().strip()
            track = int(self.ent_track.get())
            mode, sector, date_str, f_idx = self.parse_field_name(name)
            dt = datetime.strptime(date_str, "%Y-%m-%d").date()
            Y = to_base36(dt.year - self.base_year, 1)
            doy = int(dt.strftime("%j"))
            DD = to_base36(doy, 2)
            S = MODE_SECTOR_MAP.get((mode, sector), "X")
            F = to_base36(f_idx, 1)
            O = to_base36(track, 1)
            res = f"T{Y}{DD}{S}{F}{O}"
            self.lbl_gen_res.config(text=f"Code: {res}")
            self.ent_code.delete(0, tk.END)
            self.ent_code.insert(0, res)
        except Exception as e:
            messagebox.showerror("Error", f"Failed: {e}")

    def on_decode(self):
        try:
            code = self.ent_code.get().strip().upper()
            if len(code) != 7 or not code.startswith('T'):
                raise ValueError("Invalid format")
            year = self.base_year + from_base36(code[1])
            doy = from_base36(code[2:4])
            s_code = code[4]
            f_idx = from_base36(code[5])
            track = from_base36(code[6])
            dt = date(year, 1, 1).fromordinal(date(year, 1, 1).toordinal() + doy - 1)
            mode_info = INV_MODE_MAP.get(s_code, ("Unknown", "Unknown"))
            info = (f"--- SSTAC OBJECT INFO ---\n"
                    f"Date: {dt.strftime('%Y-%m-%d')} (DOY: {doy})\n"
                    f"Mode: {mode_info[0]}\n"
                    f"Sector: {mode_info[1] if mode_info[1] else 'N/A'}\n"
                    f"Field Index: {f_idx:03d} | Track: {track:02d}")
            self.txt_dec_res.delete(1.0, tk.END)
            self.txt_dec_res.insert(tk.END, info)
        except Exception as e:
            messagebox.showerror("Error", str(e))
