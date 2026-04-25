"""
gui_widgets.py
──────────────
Reusable CTk widget classes shared across the SSTAC GUI.
Extracted from gui.py for independent maintenance.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk

from config import save_config


# ─────────────────────────────────────────────
#  StatCard  — dashboard stat display
# ─────────────────────────────────────────────

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
#  LocationManager  — observatory site manager popup
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
