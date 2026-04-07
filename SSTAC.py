# SSTAC Survey (beta) version - Final Stable Build
import sys
import os

# --- FIX FOR PYINSTALLER NOCONSOLE & MATPLOTLIB ---
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import matplotlib
matplotlib.use('TkAgg') 
# -----------------------------------------------------

import csv
import time
import math
import re
import threading
import queue
import warnings
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Rectangle

import astropy.units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, GeocentricTrueEcliptic, get_sun, get_body
from astropy.utils import iers
from astropy.coordinates import solar_system_ephemeris
from astropy.coordinates.baseframe import NonRotationTransformationWarning

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
iers.conf.auto_download = False
iers.conf.auto_max_age = None          
iers.conf.iers_degraded_accuracy = "warn"
solar_system_ephemeris.set("builtin")
warnings.filterwarnings("ignore", category=NonRotationTransformationWarning)

CONFIG_FILE = "sstac_config.json"

DEFAULT_CONFIG = {
    "default_location": "MPC-O58",
    "locations": {
        "MPC-O58": {"lat": 14.6983, "lon": 101.4541, "alt": 317.0, "utc_offset": 7.0, "fov": 34.9}
    },
    "survey_defaults": {
        "max_fields": 10, "min_moon_sep": 30.0, "min_alt": 25.0, 
        "avoid_gal": False, "gal_b_min": 12.0, "use_overlap": True, 
        "overlap_pct": 10, "use_neocp": False, "avoid_mba": False
    }
}

FOV_DEG = 34.9 / 60.0
DUPLICATE_RADIUS_DEG = 0.4
LAMBDA_STEP_DEG = 0.6  
ECL_MBA_BAND, ECL_NEO_BAND, ECL_COMET_BAND = 8.0, 15.0, 40.0 
COMET_EVE_LAM_MIN_OFF, COMET_EVE_LAM_MAX_OFF = 30.0, 90.0
COMET_MORN_LAM_MIN_OFF, COMET_MORN_LAM_MAX_OFF = 30.0, 90.0
BETA_MBA_STEP, BETA_NEO_STEP, BETA_COMET_STEP = 1.0, 2.0, 3.0
MBA_NIGHT_START, MBA_NIGHT_END = "19:00", "04:00"
NEO_NIGHT_START, NEO_NIGHT_END = "17:00", "06:00"
COMET_TWILIGHT_START, COMET_TWILIGHT_END = "17:00", "06:00"
MBA_STEP_MIN, NEO_STEP_MIN, COMET_STEP_MIN = 10, 5, 5
NEOCP_URL = "https://www.projectpluto.com/neocp2/summary.htm"
NEOCP_USER_AGENT = "Mozilla/5.0 (NightlySurveyPlanner; +https://minorplanetcenter.net/)"
HEATMAP_BINS_X, HEATMAP_BINS_Y = 240, 120   

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                for key in DEFAULT_CONFIG:
                    if key not in cfg: 
                        cfg[key] = DEFAULT_CONFIG[key]
                for loc_name, loc_data in cfg.get("locations", {}).items():
                    if "utc_offset" not in loc_data: 
                        loc_data["utc_offset"] = cfg.get("utc_offset", 7.0)
                    if "fov" not in loc_data:
                        loc_data["fov"] = 34.9
                return cfg
        except Exception: 
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f: 
        json.dump(cfg, f, indent=4)
# ==========================================
# ASTRONOMY & PHYSICS ENGINE
# ==========================================

def local_date_default(): 
    return datetime.now().strftime("%Y-%m-%d")

def to_utc_time(local_dt, utc_offset): 
    return Time(local_dt) - utc_offset * u.hour

def utc_to_local_dt(t_utc, utc_offset): 
    return t_utc.to_datetime() + timedelta(hours=utc_offset)

def make_time_grid(start_utc, end_utc, step_min): 
    steps = max(2, int((end_utc - start_utc).to(u.minute).value) // step_min + 1)
    return start_utc + np.arange(steps) * (step_min * u.minute)

def fixed_local_window(date_str_local, start_hm, end_hm, utc_offset):
    d = datetime.fromisoformat(date_str_local).date()
    sh, sm = map(int, start_hm.split(":"))
    eh, em = map(int, end_hm.split(":"))
    st = datetime(d.year, d.month, d.day, sh, sm)
    en = datetime(d.year, d.month, d.day, eh, em)
    if en <= st: 
        en += timedelta(days=1)
    return to_utc_time(st, utc_offset), to_utc_time(en, utc_offset)

def _lambda_range_wrap(lmin, lmax, step): 
    if lmin <= lmax:
        return np.arange(lmin, lmax + 1e-9, step)
    else:
        return np.concatenate([np.arange(lmin, 360.0, step), np.arange(0.0, lmax + 1e-9, step)])

def generate_grid(mode, lambda_sun_deg, avoid_mba=False):
    mode = mode.upper()
    grid = []
    
    if mode == "MBA":
        lam_vals = ((lambda_sun_deg + 180.0) % 360.0 + np.arange(-10.0, 10.0 + 1e-9, LAMBDA_STEP_DEG)) % 360.0
        for l in lam_vals:
            for b in np.arange(-ECL_MBA_BAND, ECL_MBA_BAND + 1e-9, BETA_MBA_STEP): 
                grid.append((float(l), float(b), "NIGHT"))
                
    elif mode == "NEO":
        if avoid_mba:
            beta_vals = np.concatenate([np.arange(-35.0, -14.9, BETA_NEO_STEP), np.arange(15.0, 35.1, BETA_NEO_STEP)])
        else:
            beta_vals = np.arange(-ECL_NEO_BAND, ECL_NEO_BAND + 1e-9, BETA_NEO_STEP)
            
        for l in _lambda_range_wrap((lambda_sun_deg + 60.0) % 360.0, (lambda_sun_deg + 90.0) % 360.0, LAMBDA_STEP_DEG):
            for b in beta_vals: 
                grid.append((float(l), float(b), "EVE"))
        for l in _lambda_range_wrap((lambda_sun_deg - 90.0) % 360.0, (lambda_sun_deg - 60.0) % 360.0, LAMBDA_STEP_DEG):
            for b in beta_vals: 
                grid.append((float(l), float(b), "MORN"))
                
    elif mode == "COMET-TWILIGHT":
        for l in _lambda_range_wrap((lambda_sun_deg + COMET_EVE_LAM_MIN_OFF) % 360.0, (lambda_sun_deg + COMET_EVE_LAM_MAX_OFF) % 360.0, LAMBDA_STEP_DEG):
            for b in np.arange(-ECL_COMET_BAND, ECL_COMET_BAND + 1e-9, BETA_COMET_STEP): 
                grid.append((float(l), float(b), "EVE"))
        for l in _lambda_range_wrap((lambda_sun_deg - COMET_MORN_LAM_MAX_OFF) % 360.0, (lambda_sun_deg - COMET_MORN_LAM_MIN_OFF) % 360.0, LAMBDA_STEP_DEG):
            for b in np.arange(-ECL_COMET_BAND, ECL_COMET_BAND + 1e-9, BETA_COMET_STEP): 
                grid.append((float(l), float(b), "MORN"))
                
    elif mode == "PHA-MIDNIGHT":
        beta_vals = np.concatenate([np.arange(-40.0, -14.9, BETA_NEO_STEP), np.arange(15.0, 40.1, BETA_NEO_STEP)])
        for l in _lambda_range_wrap((lambda_sun_deg + 120.0) % 360.0, (lambda_sun_deg + 240.0) % 360.0, LAMBDA_STEP_DEG):
            for b in beta_vals: 
                grid.append((float(l), float(b), "NIGHT"))
                
    elif mode == "DEEP-OPPOSITION":
        lam_vals = ((lambda_sun_deg + 180.0) % 360.0 + np.arange(-15.0, 15.0 + 1e-9, LAMBDA_STEP_DEG)) % 360.0
        for l in lam_vals:
            for b in np.arange(-20.0, 20.0 + 1e-9, BETA_MBA_STEP): 
                grid.append((float(l), float(b), "NIGHT"))
                
    # ----------------------------------------------------
    # UNIVERSAL AVOID MBA FILTER
    # กรองพิกัดที่ติดเส้น Ecliptic (|b| < 15 องศา) ออกจากทุกๆ โหมดที่ทำงานอยู่
    if avoid_mba:
        grid = [(l, b, sec) for (l, b, sec) in grid if abs(b) >= 15.0]
    # ----------------------------------------------------
                
    return grid

def ecliptic_to_icrs(lam_deg, bet_deg, obstime): 
    return SkyCoord(lon=lam_deg * u.deg, lat=bet_deg * u.deg, frame=GeocentricTrueEcliptic(equinox=obstime)).transform_to("icrs")

def format_ra_dec(coord_eq): 
    return coord_eq.ra.to_string(unit=u.hour, sep=":", precision=2, pad=True), coord_eq.dec.to_string(unit=u.deg, sep=":", precision=1, alwayssign=True, pad=True)

def estimate_phase_angle(elong_deg, mode_u):
    delta = 1.5 if mode_u in ("MBA", "DEEP-OPPOSITION") else 1.0   
    r_sq = 1.0 + delta**2 - 2 * delta * math.cos(math.radians(elong_deg))
    if math.sqrt(r_sq) * delta < 1e-6:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (r_sq + delta**2 - 1.0) / (2 * math.sqrt(r_sq) * delta)))))

def _clip01(x): 
    return float(max(0.0, min(1.0, x)))

def gaussian_score(x, mu, sigma): 
    if sigma > 0:
        return float(math.exp(-0.5 * ((x - mu) / sigma) ** 2))
    return 0.0

def elongation_score(elong_deg, mode_u):
    mode_u = mode_u.upper()
    if mode_u in ("MBA", "DEEP-OPPOSITION"): 
        return gaussian_score(float(elong_deg), 180.0, 18.0)  
    if mode_u == "NEO": 
        return gaussian_score(float(elong_deg), 75.0, 10.0)
    if mode_u == "PHA-MIDNIGHT": 
        return gaussian_score(float(elong_deg), 180.0, 45.0) 
    return gaussian_score(float(elong_deg), 60.0, 20.0)

def twilight_score(sun_alt_deg, mode_u):
    mode_u = mode_u.upper()
    s = float(sun_alt_deg)
    if mode_u in ("MBA", "DEEP-OPPOSITION", "PHA-MIDNIGHT"): 
        if s <= -18:
            return 1.0
        elif s >= -12:
            return 0.0
        else:
            return _clip01((-12.0 - s) / 6.0)
    elif mode_u in ("NEO", "COMET-TWILIGHT"): 
        if -18.0 <= s <= -12.0: 
            return 1.0
        if -12.0 < s <= -9.0: 
            return _clip01((-9.0 - s) / 3.0)
        return 0.0
    return 0.0

def best_visibility_v2(coord_eq, times_utc, location, min_alt_deg, mode_u, sector_label, sun_icrs_arr=None, sun_alt_deg_arr=None):
    alt_deg = coord_eq.transform_to(AltAz(obstime=times_utc, location=location)).alt.to_value(u.deg)
    
    if sun_alt_deg_arr is not None and sun_icrs_arr is not None:
        sun_alt_deg = sun_alt_deg_arr
        elong = coord_eq.separation(sun_icrs_arr).to_value(u.deg)
    else:
        sun_alt_deg = get_sun(times_utc).transform_to(AltAz(obstime=times_utc, location=location)).alt.to_value(u.deg)
        elong = coord_eq.separation(get_sun(times_utc).transform_to("icrs")).to_value(u.deg)
    
    alpha = np.array([estimate_phase_angle(e, mode_u) for e in elong])
    mode_up = mode_u.upper()
    
    if mode_up in ("MBA", "DEEP-OPPOSITION"): 
        ok_phase = (alpha >= 0.0) & (alpha <= 45.0)
    elif mode_up == "PHA-MIDNIGHT": 
        ok_phase = (alpha >= 0.0) & (alpha <= 60.0)
    elif mode_up == "NEO": 
        ok_phase = (alpha >= 15.0) & (alpha <= 85.0)
    else: 
        ok_phase = (alpha >= 25.0) & (alpha <= 125.0)

    ok = np.isfinite(alt_deg) & np.isfinite(sun_alt_deg) & (alt_deg >= float(min_alt_deg)) & ok_phase
    if not np.any(ok): 
        return []

    tw = np.array([twilight_score(s, mode_u) for s in sun_alt_deg], dtype=float)
    
    if mode_up in ("NEO", "COMET-TWILIGHT"): 
        tw[~((sun_alt_deg >= -18.0) & (sun_alt_deg <= -9.0))] = 0.0
    elif mode_up not in ("MBA", "DEEP-OPPOSITION", "PHA-MIDNIGHT"): 
        tw[~((sun_alt_deg >= -12.0) & (sun_alt_deg <= -3.0))] = 0.0
        
    el = np.array([elongation_score(e, mode_u) for e in elong], dtype=float)
    al = np.array([float(max(0.0, min(1.0, (a - min_alt_deg) / (70.0 - min_alt_deg)))) for a in alt_deg], dtype=float)
    
    merit = tw * el * al
    merit[~ok] = 0.0

    if np.nanmax(merit) <= 0: 
        return []
        
    idx = np.where(merit > 0)[0]
    if len(idx) == 0: 
        return []

    blocks = []
    start = idx[0]
    prev = idx[0]
    
    for i in idx[1:]:
        if i == prev + 1: 
            prev = i
        else: 
            blocks.append((start, prev))
            start = prev = i
    blocks.append((start, prev))
    
    windows = []
    for (a, b) in blocks:
        k = a + int(np.argmax(merit[a:b+1]))
        duration_hours = (b - a + 1) * (times_utc[1] - times_utc[0]).to_value(u.hour) if len(times_utc) > 1 else 0
        windows.append({
            "label": sector_label, 
            "t_start": times_utc[a], 
            "t_end": times_utc[b], 
            "t_best": times_utc[k], 
            "best_idx": k, 
            "best_alt": float(alt_deg[k]), 
            "phase_best": float(alpha[k]), 
            "merit": float(merit[k]), 
            "duration_hours": duration_hours
        })
    return windows

def moon_sep_deg(coord_eq, t_utc, location=None):
    try:
        moon = get_body("moon", t_utc, location=location) if location is not None else get_body("moon", t_utc)
    except Exception:
        moon = get_body("moon", t_utc)
    try:
        moon = moon.transform_to("icrs")
    except Exception:
        pass
    return coord_eq.separation(moon).to_value(u.deg)

def load_prev_coords(date_str_local, mode):
    d = datetime.fromisoformat(date_str_local).date() - timedelta(days=1)
    fname = Path(f"nightly_targets_{mode.upper()}_{d.isoformat()}.csv")
    if not fname.exists(): 
        return None
    coords = []
    with fname.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("RA") and row.get("Dec"):
                try: 
                    coords.append(SkyCoord(row.get("RA"), row.get("Dec"), unit=(u.hourangle, u.deg)))
                except Exception: 
                    pass
    return SkyCoord(coords) if coords else None

def greedy_select_with_separation(candidates, max_fields, min_sep_deg):
    chosen = []
    chosen_coords = []
    chosen_windows = []
    
    for c in candidates:
        if len(chosen) >= max_fields: 
            break
        coord = c["coord"]
        wlab = c.get("sector", "")
        
        if not chosen_coords:
            chosen.append(c)
            chosen_coords.append(coord)
            chosen_windows.append(wlab)
            continue
            
        sep = coord.separation(SkyCoord(chosen_coords)).to_value(u.deg)
        if float(np.min(sep)) >= min_sep_deg or (float(np.min(sep)) < 1e-6 and not any(float(sj) < 1e-6 and chosen_windows[j] == wlab for j, sj in enumerate(sep))):
            chosen.append(c)
            chosen_coords.append(coord)
            chosen_windows.append(wlab)
            
    return chosen
# ==========================================
# NEOCP & HYBRID GENERATOR
# ==========================================

def fetch_neocp_text_project_pluto(url=NEOCP_URL, timeout_sec=20):
    try:
        req = Request(url, headers={"User-Agent": NEOCP_USER_AGENT})
        with urlopen(req, timeout=timeout_sec) as resp: 
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e: 
        print(f"Fetch NEOCP Network Error: {e}")
        return ""

def parse_project_pluto_neocp(html_text):
    t = re.sub(r"(?is)<[^>]+>", " ", re.sub(r"(?is)</\s*tr\s*>", "\n", re.sub(r"(?is)<\s*br\s*/?>", "\n", html_text))).replace("\xa0", " ")
    ra_dec_re = re.compile(r"^(?P<desig>\S+)\s+.*?(?P<rah>\d{1,2})\s+(?P<ram>\d{2})\s+(?P<ras>\d{2}(?:\.\d+)?)\s+(?P<sign>[+\-])(?P<decd>\d{2})\s+(?P<decm>\d{2})\s+(?P<decs>\d{2}(?:\.\d+)?)\b")
    objs = []
    for ln in t.split("\n"):
        ln = re.sub(r"[ \t\r\f\v]+", " ", ln).strip()
        if not ln or "sorted by" in ln.lower() or "object" in ln.lower() or ln.lower().startswith("desig"): 
            continue
        m = ra_dec_re.search(ln)
        if not m: 
            continue
        try:
            ra = re.sub(r":{2,}", ":", re.sub(r"\s+", " ", f"{int(m.group('rah')):02d}:{int(m.group('ram')):02d}:{m.group('ras')}").replace(" ", ":"))
            dec = re.sub(r":{2,}", ":", re.sub(r"\s+", " ", f"{m.group('sign')}{int(m.group('decd')):02d}:{int(m.group('decm')):02d}:{m.group('decs')}").replace(" ", ":"))
            objs.append({"desig": m.group("desig").strip(), "coord": SkyCoord(ra, dec, unit=(u.hourangle, u.deg)), "score": None})
        except Exception: 
            continue
    return objs

def load_or_fetch_neocp(progress_cb=None):
    if progress_cb: 
        progress_cb(0, 1, "Fetching NEOCP Data...")
        
    html_text = fetch_neocp_text_project_pluto()
    objs = parse_project_pluto_neocp(html_text)
    
    if not objs: 
        return [], "NEOCP fetch failed. Check connection.", True
        
    if progress_cb: 
        progress_cb(1, 1, "NEOCP Loaded Successfully")
        
    return objs, f"NEOCP loaded: N={len(objs)} | LIVE | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", False

def _generate_single_plan(date_str_local, mode, location, utc_offset, min_moon_deg, min_alt_deg, max_fields, use_history, avoid_galactic, gal_b_min_deg, use_overlap, overlap_percent, use_neocp_weighting, avoid_mba_zone, neocp_objects=None, progress_cb=None):
    mode_u = mode.upper()
    noon_utc = to_utc_time(datetime.fromisoformat(f"{date_str_local} 12:00"), utc_offset)
    lam_sun = get_sun(noon_utc).transform_to(GeocentricTrueEcliptic(equinox=noon_utc)).lon.to_value(u.deg)
    
    if mode_u in ("MBA", "DEEP-OPPOSITION", "PHA-MIDNIGHT"): 
        w_start, w_end = fixed_local_window(date_str_local, MBA_NIGHT_START, MBA_NIGHT_END, utc_offset)
        win_start, win_end, time_step_min = w_start, w_end, MBA_STEP_MIN
    elif mode_u == "NEO": 
        w_start, w_end = fixed_local_window(date_str_local, NEO_NIGHT_START, NEO_NIGHT_END, utc_offset)
        win_start, win_end, time_step_min = w_start, w_end, NEO_STEP_MIN
    else: 
        w_start, w_end = fixed_local_window(date_str_local, COMET_TWILIGHT_START, COMET_TWILIGHT_END, utc_offset)
        win_start, win_end, time_step_min = w_start, w_end, COMET_STEP_MIN

    times = make_time_grid(win_start, win_end, time_step_min)
    grid = generate_grid(mode_u, lam_sun, avoid_mba=avoid_mba_zone)
    total = len(grid)
    
    if progress_cb: 
        progress_cb(0, total, f"Pre-calculating Ephemeris ({total} grids)...")
        
    sun_icrs_arr = get_sun(times)
    sun_alt_deg_arr = sun_icrs_arr.transform_to(AltAz(obstime=times, location=location)).alt.to_value(u.deg)
    
    try: 
        moon_icrs_arr = get_body("moon", times, location=location) if location is not None else get_body("moon", times)
    except Exception: 
        moon_icrs_arr = get_body("moon", times)
    
    prev_coords = load_prev_coords(date_str_local, mode_u) if use_history else None
    neocp_coords = SkyCoord([o["coord"] for o in neocp_objects]) if (use_neocp_weighting and neocp_objects) else None
    
    stats = {"grid_total": total, "pass_vis": 0, "pass_moon": 0, "pass_gal": 0, "pass_history": 0, "candidates": 0, "selected": 0}
    candidates = []
    last_emit = time.time()

    for idx, (lam, bet, sector_label) in enumerate(grid, start=1):
        if progress_cb and (idx == 1 or idx == total or time.time() - last_emit >= 0.15): 
            progress_cb(idx, total, f"Scanning {mode_u} grid")
            last_emit = time.time()

        coord = ecliptic_to_icrs(lam, bet, noon_utc)
        wins = best_visibility_v2(coord, times, location, min_alt_deg, mode_u, sector_label, sun_icrs_arr=sun_icrs_arr, sun_alt_deg_arr=sun_alt_deg_arr)
        if not wins: 
            continue
        stats["pass_vis"] += 1

        gal_b = coord.galactic.b.to_value(u.deg)
        if avoid_galactic and (abs(gal_b) < gal_b_min_deg): 
            continue
        stats["pass_gal"] += 1

        if prev_coords is not None and len(prev_coords) > 0 and np.min(coord.separation(prev_coords).to_value(u.deg)) < DUPLICATE_RADIUS_DEG: 
            continue
        stats["pass_history"] += 1

        if mode_u in ("MBA", "DEEP-OPPOSITION", "PHA-MIDNIGHT"): 
            wins = [max(wins, key=lambda w: w["merit"])]

        for w in wins:
            msep = coord.separation(moon_icrs_arr[w["best_idx"]]).to_value(u.deg)
            if msep < min_moon_deg: 
                continue
            stats["pass_moon"] += 1
            
            if mode_u == "DEEP-OPPOSITION": 
                score = w["duration_hours"] * 10.0 + (float(w["best_alt"]) * 0.1) 
            elif mode_u == "PHA-MIDNIGHT": 
                score = float(w["best_alt"]) + abs(float(bet))*1.5 
            else: 
                score = float(w["best_alt"]) - abs(float(bet)) * 0.5
                
            if neocp_coords is not None and len(neocp_coords) > 0:
                sep_arr = coord.separation(neocp_coords).to_value(u.deg)
                close_mask = sep_arr < 5.0
                if np.any(close_mask): 
                    score += 2.0 * float(np.sum((5.0 - sep_arr[close_mask]) / 5.0))
                    
            candidates.append({
                "orig_mode": mode_u, "coord": coord, "best_time": w["t_best"], 
                "best_alt": float(w["best_alt"]), "moon_sep": float(msep), 
                "score": float(score), "gal_b_deg": float(gal_b), 
                "sector": w["label"], "window_start": w["t_start"], 
                "window_end": w["t_end"], "phase_best": float(w["phase_best"]), 
                "duration": w["duration_hours"]
            })
            
    candidates.sort(key=lambda x: x["score"], reverse=True)
    stats["candidates"] = len(candidates)
    
    if progress_cb: 
        progress_cb(total, total, f"Selecting {mode_u} fields")

    # Standard fast overlap logic
    min_sep = FOV_DEG * (1.0 - max(0, min(80, int(overlap_percent))) / 100.0) if use_overlap else FOV_DEG
    
    if mode_u in ("COMET-TWILIGHT", "NEO"):
        eve_cands = [c for c in candidates if c["sector"] == "EVE"]
        morn_cands = [c for c in candidates if c["sector"] == "MORN"]
        
        k_eve = (max_fields + 1) // 2
        k_morn = max_fields // 2
        
        if len(eve_cands) < k_eve: 
            k_morn += (k_eve - len(eve_cands))
            k_eve = len(eve_cands)
        elif len(morn_cands) < k_morn: 
            k_eve += (k_morn - len(morn_cands))
            k_morn = len(morn_cands)
            
        selected = greedy_select_with_separation(eve_cands, k_eve, min_sep) + greedy_select_with_separation(morn_cands, k_morn, min_sep)
    else:
        selected = greedy_select_with_separation(candidates, max_fields, min_sep)

    stats["selected"] = len(selected)
    return lam_sun, win_start, win_end, selected, stats

def generate_plan(date_str_local, mode, location, utc_offset, min_moon_deg, min_alt_deg, max_fields, use_history, avoid_galactic, gal_b_min_deg, use_overlap, overlap_percent, use_neocp_weighting, avoid_mba_zone, neocp_objects=None, progress_cb=None):
    mode_u = mode.upper()
    
    hybrid_modes = ["FULL-NIGHT (NEO+MBA)", "FULL-NIGHT (COMET+MBA)", "FULL-NIGHT (NEO+PHA)", "FULL-NIGHT (COMET+PHA)"]
    
    if mode_u in hybrid_modes:
        if "COMET" in mode_u:
            p_mode = "COMET-TWILIGHT"
        else:
            p_mode = "NEO"
            
        if "PHA" in mode_u:
            m_mode = "PHA-MIDNIGHT"
        else:
            m_mode = "MBA"
        
        p_max = max(2, max_fields // 2)
        lam_sun, t0_p, t1_p, p_sel, p_st = _generate_single_plan(
            date_str_local, p_mode, location, utc_offset, min_moon_deg, min_alt_deg, 
            p_max, use_history, avoid_galactic, gal_b_min_deg, use_overlap, overlap_percent, 
            use_neocp_weighting, avoid_mba_zone, neocp_objects, progress_cb
        )
        
        req_mid = max(1, max_fields - len(p_sel))
        _, t0_m, t1_m, m_sel, m_st = _generate_single_plan(
            date_str_local, m_mode, location, utc_offset, min_moon_deg, min_alt_deg, 
            req_mid, use_history, avoid_galactic, gal_b_min_deg, use_overlap, overlap_percent, 
            use_neocp_weighting, avoid_mba_zone, neocp_objects, progress_cb
        )
        
        combined = p_sel + m_sel
        combined.sort(key=lambda x: x["best_time"].unix)
        
        total_stats = {
            "grid_total": p_st["grid_total"] + m_st["grid_total"]
        }
        
        if progress_cb: 
            progress_cb(1, 1, "Done")
            
        return lam_sun, min(t0_p, t0_m), max(t1_p, t1_m), combined, total_stats
        
    else:
        l_s, w_s, w_e, sel, st = _generate_single_plan(
            date_str_local, mode, location, utc_offset, min_moon_deg, min_alt_deg, 
            max_fields, use_history, avoid_galactic, gal_b_min_deg, use_overlap, overlap_percent, 
            use_neocp_weighting, avoid_mba_zone, neocp_objects, progress_cb
        )
        sel.sort(key=lambda x: x["best_time"].unix)
        return l_s, w_s, w_e, sel, st
# ==========================================
# EXPORT & MAP RENDERER
# ==========================================
def export_nina_csv(out_path, mode, date_str_local, utc_offset, selected):
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
        w.writerow([
            "Name", "RA", "Dec", "Sector", "WindowStartLocal", "WindowEndLocal", 
            "BestTimeLocal", "BestAltDeg", "MoonSepDeg", "PhaseAngleDeg", 
            "Gal_b_Deg", "Duration_Hr"
        ])
        
        for i, item in enumerate(selected, start=1):
            ra, dec = format_ra_dec(item["coord"])
            sec = item.get("sector", "")
            orig = item.get("orig_mode", mode.upper())
            
            name = f"{orig}_{sec}_{date_str_local}_{i:03d}" if sec else f"{orig}_{date_str_local}_{i:03d}"
            w_start = utc_to_local_dt(item["window_start"], utc_offset).strftime("%Y-%m-%d %H:%M")
            w_end = utc_to_local_dt(item["window_end"], utc_offset).strftime("%Y-%m-%d %H:%M")
            b_time = utc_to_local_dt(item["best_time"], utc_offset).strftime("%Y-%m-%d %H:%M")
            
            w.writerow([
                name, ra, dec, sec, w_start, w_end, b_time, 
                f"{item['best_alt']:.1f}", f"{item['moon_sep']:.1f}", 
                f"{item.get('phase_best', 0.0):.1f}", f"{item['gal_b_deg']:.1f}", 
                f"{item.get('duration', 0.0):.1f}"
            ])

def _field_polygon_radec_deg(ra_deg, dec_deg, fov_arcmin=34.9):
    half_deg = (fov_arcmin / 2.0) / 60.0
    dra = half_deg / max(np.cos(np.deg2rad(dec_deg)), 1e-6)
    r_arr = np.array([ra_deg - dra, ra_deg + dra, ra_deg + dra, ra_deg - dra, ra_deg - dra], dtype=float) % 360.0
    d_arr = np.array([dec_deg - half_deg, dec_deg - half_deg, dec_deg + half_deg, dec_deg + half_deg, dec_deg - half_deg], dtype=float)
    return r_arr, d_arr

def _radec_poly_to_xy(ra_list_deg, dec_list_deg): 
    x_val = -((np.unwrap(np.deg2rad(np.array(ra_list_deg, dtype=float))) + np.pi) % (2 * np.pi) - np.pi)
    y_val = np.deg2rad(np.array(dec_list_deg, dtype=float))
    return x_val, y_val

def _radec_to_xy(ra_deg, dec_deg): 
    x_val = -((np.deg2rad(np.array(ra_deg, dtype=float)) + np.pi) % (2 * np.pi) - np.pi)
    y_val = np.deg2rad(np.array(dec_deg, dtype=float))
    return x_val, y_val

def show_field_zoom(field_coord, field_no, neocp_coords=None):
    ra0 = field_coord.ra.wrap_at(180*u.deg).deg
    dec0 = field_coord.dec.deg
    half = FOV_DEG / 2.0
    ra_half_deg = half / max(float(np.cos(np.deg2rad(dec0))), 1e-6)
    
    fig, ax = plt.subplots(figsize=(7.6, 6.2), facecolor="#000000")
    ax.set_facecolor("#000000")
    ax.set_title(f"Field #{field_no} | RA={field_coord.ra.to_string(u.hour, sep=':')}  Dec={field_coord.dec.to_string(sep=':')}", color="white")
    ax.set_xlabel("RA (deg, wrapped at 180) [axis inverted]", color="white")
    ax.set_ylabel("Dec (deg)", color="white")
    ax.set_xlim(ra0 + ra_half_deg * 1.4, ra0 - ra_half_deg * 1.4)
    ax.set_ylim(dec0 - half * 1.4, dec0 + half * 1.4)
    
    ax.add_patch(Rectangle((ra0 - ra_half_deg, dec0 - half), 2*ra_half_deg, 2*half, fill=False, linewidth=2.0, edgecolor="cyan"))
    ax.scatter([ra0], [dec0], s=90, marker="x", color="white")
    
    if neocp_coords is not None and len(neocp_coords) > 0:
        nc_ra = neocp_coords.ra.wrap_at(180*u.deg).deg
        nc_dec = neocp_coords.dec.deg
        idx_in = np.where((np.abs((nc_ra - ra0) * max(float(np.cos(np.deg2rad(dec0))), 1e-6)) <= half) & (np.abs(nc_dec - dec0) <= half))[0]
        if len(idx_in) > 0: 
            ax.scatter(nc_ra[idx_in], nc_dec[idx_in], s=45, color="orange", alpha=0.85)
            
    ax.grid(True, alpha=0.35, color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values(): spine.set_edgecolor("white")
    plt.show()

def build_sky_map_figure(selected, mode, date_str_local, utc_offset, neocp_objects=None, location=None, location_name="Observatory"):
    mode_u = mode.upper()
    if selected: 
        t_ref = selected[len(selected) // 2]["best_time"]
    else: 
        t_ref = to_utc_time(datetime.fromisoformat(f"{date_str_local} 21:00"), utc_offset)
        
    noon_utc = to_utc_time(datetime.fromisoformat(f"{date_str_local} 12:00"), utc_offset)

    if selected:
        coords = SkyCoord([it["coord"] for it in selected])
        x_f, y_f = _radec_to_xy(coords.ra.deg, coords.dec.deg)
    else:
        x_f, y_f = np.array([]), np.array([])

    l = np.linspace(0, 360, 721) * u.deg
    gal_icrs = SkyCoord(l=l, b=np.zeros_like(l.value) * u.deg, frame="galactic").transform_to("icrs")
    x_gal, y_gal = _radec_to_xy(gal_icrs.ra.deg, gal_icrs.dec.deg)

    ec_frame = GeocentricTrueEcliptic(equinox=noon_utc)
    lam = np.linspace(0, 360, 721) * u.deg
    
    if "MBA" in mode_u or "OPPOSITION" in mode_u: 
        band = ECL_MBA_BAND
    elif "NEO" in mode_u or "PHA" in mode_u: 
        band = ECL_NEO_BAND
    else: 
        band = ECL_COMET_BAND
    
    ecl0 = SkyCoord(lon=lam, lat=np.zeros_like(lam.value) * u.deg, frame=ec_frame).transform_to("icrs")
    eclp = SkyCoord(lon=lam, lat=np.full_like(lam.value, +band) * u.deg, frame=ec_frame).transform_to("icrs")
    eclm = SkyCoord(lon=lam, lat=np.full_like(lam.value, -band) * u.deg, frame=ec_frame).transform_to("icrs")
    
    x_ecl0, y_ecl0 = _radec_to_xy(ecl0.ra.deg, ecl0.dec.deg)
    x_eclp, y_eclp = _radec_to_xy(eclp.ra.deg, eclp.dec.deg)
    x_eclm, y_eclm = _radec_to_xy(eclm.ra.deg, eclm.dec.deg)

    sun = get_sun(t_ref).transform_to("icrs")
    x_sun, y_sun = _radec_to_xy([sun.ra.deg], [sun.dec.deg])
    
    try:
        moon = get_body("moon", t_ref, location=location) if location is not None else get_body("moon", t_ref)
    except Exception:
        moon = get_body("moon", t_ref)
    try:
        moon = moon.transform_to("icrs")
    except Exception:
        pass

    x_moon, y_moon = _radec_to_xy([moon.ra.deg], [moon.dec.deg])
    neocp_coords = SkyCoord([o["coord"] for o in neocp_objects]) if neocp_objects else None

    fig = plt.figure(figsize=(16, 8.5), facecolor="#000000")
    ax = fig.add_axes([0.02, 0.18, 0.58, 0.75], projection="mollweide")
    ax.set_facecolor("#000000")

    if neocp_coords is not None and len(neocp_coords) > 0:
        nc_x, nc_y = _radec_to_xy(neocp_coords.ra.deg, neocp_coords.dec.deg)
        x_edges = np.linspace(-np.pi, np.pi, HEATMAP_BINS_X + 1)
        y_edges = np.linspace(-np.pi/2, np.pi/2, HEATMAP_BINS_Y + 1)
        H, _, _ = np.histogram2d(nc_x, nc_y, bins=[x_edges, y_edges])
        
        if np.any(H > 0):
            K = np.array([[1, 1, 1], [1, 2, 1], [1, 1, 1]], dtype=float) / 10.0
            Hp = np.pad(H, ((1, 1), (1, 1)), mode="edge")
            Hs = np.zeros_like(H, dtype=float)
            for i in range(H.shape[0]):
                for j in range(H.shape[1]): 
                    Hs[i, j] = np.sum(Hp[i:i+3, j:j+3] * K)
            
            X, Y = np.meshgrid(x_edges, y_edges, indexing="ij")
            pcm = ax.pcolormesh(X, Y, Hs, shading="auto", alpha=0.35)
            
            cb = fig.colorbar(pcm, ax=ax, orientation="horizontal", pad=0.06, fraction=0.045, aspect=50)
            cb.set_label("NEOCP density (binned)", color="#a4b0be", fontsize=10)
            cb.ax.xaxis.set_tick_params(color='white')
            plt.setp(cb.ax.get_xticklabels(), color='white')
            cb.outline.set_edgecolor('#2c3e50')

    ax.fill_between(x_eclp, y_eclp, y_eclm, alpha=0.10, color="#9ec5ff", label=f"Ecliptic band ±{band:.0f}°")
    ax.plot(x_gal, y_gal, linewidth=1.1, color="#2bb3ff", alpha=0.9, label="Galactic plane (b=0)")
    ax.plot(x_ecl0, y_ecl0, linewidth=1.2, color="#ff9f1c", alpha=0.95, label="Ecliptic (β=0)")

    for i, it in enumerate(selected, start=1):
        r_l, d_l = _field_polygon_radec_deg(float(it["coord"].ra.deg), float(it["coord"].dec.deg), fov_arcmin=FOV_DEG*60.0)
        x_p, y_p = _radec_poly_to_xy(r_l, d_l)
        
        ax.fill(x_p, y_p, facecolor="#ff5a5f", edgecolor="white", linewidth=0.9, alpha=0.22, zorder=4)
        ax.plot(x_p, y_p, color="white", linewidth=0.9, alpha=0.95, zorder=5)
        
        tx, ty = _radec_to_xy([float(it["coord"].ra.deg)], [float(it["coord"].dec.deg)])
        txt = ax.text(tx[0], ty[0], str(i), fontsize=9.0, ha="center", va="center", color="#ffd84d", weight="bold", zorder=6)
        txt.set_path_effects([pe.withStroke(linewidth=2.6, foreground="black")])

    pts_fields = None
    if x_f.size > 0:
        pts_fields = ax.scatter(x_f, y_f, s=28, alpha=0.01, color="white", picker=True, pickradius=6, label=f"Fields (N={len(x_f)})")
        
    if neocp_coords is not None and len(neocp_coords) > 0:
        nc_x, nc_y = _radec_to_xy(neocp_coords.ra.deg, neocp_coords.dec.deg)
        ax.scatter(nc_x, nc_y, s=9, marker="o", color="#ff8c00", edgecolors="none", alpha=0.9, label=f"NEOCP (N={len(neocp_objects)})", zorder=3)

    ax.scatter(x_sun, y_sun, s=95, marker="*", color="#ff4d4d", label="Sun", zorder=6)
    ax.scatter(x_moon, y_moon, s=60, marker="o", color="#65ff00", label="Moon", zorder=6)

    ax.grid(True, alpha=0.2, color="white")
    ax.set_title(f"{location_name} • {mode_u} • Full-sky context", fontsize=17, color="white", pad=15)
    
    ax.tick_params(colors="white", labelsize=9)
    for spine in ax.spines.values(): 
        spine.set_edgecolor("white")
        
    ax.set_xticks(np.deg2rad(np.array([150, 120, 90, 60, 30, 0, -30, -60, -90, -120, -150])))
    ax.set_xticklabels(["14h", "16h", "18h", "20h", "22h", "0h", "2h", "4h", "6h", "8h", "10h"], color="white")
    
    leg = ax.legend(loc="lower left", fontsize=9, framealpha=0.9)
    leg.get_frame().set_facecolor("#111111")
    leg.get_frame().set_edgecolor("white")
    for t_leg in leg.get_texts(): 
        t_leg.set_color("white")

    zoom_groups = []
    if selected:
        eve = [it for it in selected if it.get("sector") == "EVE"]
        morn = [it for it in selected if it.get("sector") == "MORN"]
        night = [it for it in selected if it.get("sector") == "NIGHT"]
        
        if "FULL-NIGHT" in mode_u:
            if eve: zoom_groups.append(("EVE (Twilight)", eve))
            if night: zoom_groups.append(("MIDNIGHT (Deep)", night))
            if morn: zoom_groups.append(("MORN (Twilight)", morn))
        elif eve and morn and any(m in mode_u for m in ["COMET", "NEO", "COMET-TWILIGHT"]): 
            zoom_groups = [("EVE cluster", eve), ("MORN cluster", morn)]
        else:
            tmp_coords = SkyCoord([it["coord"] for it in selected])
            ra_wrap_all = np.array([c.ra.wrap_at(180*u.deg).deg for c in tmp_coords])
            center_dec_all = float(np.median(tmp_coords.dec.deg))
            center_ra_all = float(np.median(ra_wrap_all))
            
            x_all = np.array([-(float(it["coord"].ra.wrap_at(180*u.deg).deg) - center_ra_all) * max(float(np.cos(np.deg2rad(center_dec_all))), 1e-6) for it in selected])
            order = np.argsort(x_all)
            x_sorted = x_all[order]
            
            if len(x_sorted) >= 2:
                diffs = np.diff(x_sorted)
                max_diff_idx = int(np.argmax(diffs))
                if diffs[max_diff_idx] > max(4.0 * FOV_DEG, 3.0): 
                    zoom_groups = [
                        ("Cluster A", [selected[i] for i in order[:max_diff_idx+1]]), 
                        ("Cluster B", [selected[i] for i in order[max_diff_idx+1:]])
                    ]
            if not zoom_groups: 
                zoom_groups = [("Selected fields", selected)]
    else: 
        zoom_groups = [("Selected fields", [])]

    ax_zoom_list = []
    if len(zoom_groups) == 3: ax_zoom_list = [fig.add_axes([0.64, 0.77, 0.33, 0.16]), fig.add_axes([0.64, 0.58, 0.33, 0.16]), fig.add_axes([0.64, 0.39, 0.33, 0.16])]
    elif len(zoom_groups) == 2: ax_zoom_list = [fig.add_axes([0.64, 0.69, 0.33, 0.24]), fig.add_axes([0.64, 0.41, 0.33, 0.24])]
    else: ax_zoom_list = [fig.add_axes([0.64, 0.41, 0.33, 0.52])]
        
    for axz in ax_zoom_list: 
        axz.set_facecolor("#000000")

    zoom_meta = []
    for _, items in zoom_groups:
        if not items: 
            zoom_meta.append(None)
            continue
            
        cl_c = SkyCoord([it["coord"] for it in items])
        c_ra = float(np.median(np.array([c.ra.wrap_at(180*u.deg).deg for c in cl_c])))
        c_dec = float(np.median(np.array([c.dec.deg for c in cl_c])))
        cosc = max(float(np.cos(np.deg2rad(c_dec))), 1e-6)
        
        h_s = (FOV_DEG / 2.0) * (1.10 if any(m in mode_u for m in ["NEO", "FULL-NIGHT"]) else (1.2 if "COMET" in mode_u else 1.0))
        
        dat, xm, xM, ym, yM = [], np.inf, -np.inf, np.inf, -np.inf
        for j, it in enumerate(items, start=1):
            x0 = -(float(it["coord"].ra.wrap_at(180*u.deg).deg) - c_ra) * cosc
            y0 = float(it["coord"].dec.deg)
            idx_num = selected.index(it) + 1 if it in selected else j
            dat.append((idx_num, x0, y0))
            xm, xM, ym, yM = min(xm, x0 - h_s), max(xM, x0 + h_s), min(ym, y0 - h_s), max(yM, y0 + h_s)
            
        px = max(0.8, (xM - xm) * 0.35)
        py = max(0.8, (yM - ym) * 0.45)
        
        zoom_meta.append({
            "h_s": h_s, "items": dat, 
            "xm": xm - px, "xM": xM + px, 
            "ym": ym - py, "yM": yM + py, 
            "cx": 0.5 * ((xm - px) + (xM + px)), 
            "cy": 0.5 * ((ym - py) + (yM + py)), 
            "w": (xM + px) - (xm - px), 
            "h": (yM + py) - (ym - py)
        })

    cs = None
    if len(zoom_groups) >= 2 and all(m is not None for m in zoom_meta):
        cs = max(max(m["w"], m["h"]) for m in zoom_meta)

    for axz, (title, _items), meta in zip(ax_zoom_list, zoom_groups, zoom_meta):
        if meta is not None:
            for idx_label, x0, y0 in meta["items"]:
                hs = meta["h_s"]
                axz.add_patch(Rectangle((x0 - hs, y0 - hs), 2*hs, 2*hs, facecolor="cyan", edgecolor="cyan", linewidth=1.4, alpha=0.15))
                axz.plot([x0 - hs, x0 + hs, x0 + hs, x0 - hs, x0 - hs], [y0 - hs, y0 - hs, y0 + hs, y0 + hs, y0 - hs], color="cyan", linewidth=1.0, alpha=0.7)
                txt = axz.text(x0, y0, str(idx_label), ha="center", va="center", fontsize=10.5, weight="bold", color="white")
                txt.set_path_effects([pe.withStroke(linewidth=2.8, foreground="black")])
                
            if cs is not None: 
                axz.set_xlim(meta["cx"] - cs/2, meta["cx"] + cs/2)
                axz.set_ylim(meta["cy"] - cs/2, meta["cy"] + cs/2)
            else: 
                axz.set_xlim(meta["xm"], meta["xM"])
                axz.set_ylim(meta["ym"], meta["yM"])
            axz.set_aspect('equal', adjustable='box')
        else: 
            axz.set_xlim(-1, 1)
            axz.set_ylim(-1, 1)
            
        axz.set_title(f"Operational zoom: {title}", fontsize=11, color="#a4b0be", pad=4)
        axz.set_xlabel("Local sky-projected X (deg)", color="white", fontsize=8)
        axz.set_ylabel("Dec (deg)", color="white", fontsize=8)
        axz.grid(True, alpha=0.18, color="white")
        axz.tick_params(colors="white", labelsize=8)
        axz.text(0.99, 0.02, f" ({FOV_DEG*60.0:.1f}' × {FOV_DEG*60.0:.1f}')", transform=axz.transAxes, ha="right", va="bottom", color="white", fontsize=8.2, bbox=dict(boxstyle="round,pad=0.25", facecolor="#111111", edgecolor="white", alpha=0.75))
        
        for spine in axz.spines.values(): 
            spine.set_edgecolor("white")

    ax_tbl = fig.add_axes([0.64, 0.04, 0.33, 0.33])
    ax_tbl.set_facecolor("#000000")
    
    for spine in ax_tbl.spines.values(): 
        spine.set_edgecolor("#2c3e50")
        spine.set_linewidth(1.5)
        
    ax_tbl.set_xticks([])
    ax_tbl.set_yticks([])
    ax_tbl.set_title("Execution Target Summary (Top 10)", color="#00a8ff", fontsize=12, weight="bold", pad=8)
    
    if not selected: 
        ax_tbl.text(0.5, 0.5, "No fields selected", ha="center", va="center", color="#e84118", fontsize=12)
    else:
        # ----------------------------------------------------
        # FIX: RA/Dec Format matched with UI Table
        # ----------------------------------------------------
        header_str = f"{'#':<2} | {'Target ID':<11} | {'RA':<11} | {'Dec':<11} | {'Time':<5} | {'Alt°':<4} | {'Dur'}"
        ax_tbl.text(0.04, 0.88, header_str, color="#4cd137", fontfamily="monospace", fontsize=8.5, weight="bold")
        ax_tbl.text(0.04, 0.82, "-"*72, color="#718093", fontfamily="monospace", fontsize=8.5)
        
        for i, it in enumerate(selected[:10], start=1):
            sec = it.get("sector", "")[:3]
            orig = it.get("orig_mode", mode_u)[:3]
            tid = f"{orig}_{sec}_{i:02d}" if sec else f"{orig}_{i:02d}"
            
            # Extract formatted RA/Dec safely
            r_str, d_str = format_ra_dec(it['coord'])
            
            bt_str = utc_to_local_dt(it['best_time'], utc_offset).strftime('%H:%M')
            alt_v = float(it.get('best_alt', np.nan))
            dur_v = float(it.get('duration', 0.0))
            
            row_str = f"{i:<2} | {tid:<11} | {r_str:<11} | {d_str:<11} | {bt_str:>5} | {alt_v:4.0f} | {dur_v:3.1f}"
            ax_tbl.text(0.04, 0.74 - (i-1)*0.075, row_str, color="#dfe4ea", fontfamily="monospace", fontsize=8.5)

    if pts_fields is not None and neocp_coords is not None and len(neocp_coords) > 0:
        def _on_pick(event):
            if event.artist is not pts_fields or not getattr(event, "ind", None) or len(event.ind) == 0: 
                return
            show_field_zoom(selected[int(event.ind[0])]["coord"], field_no=int(event.ind[0]) + 1, neocp_coords=neocp_coords)
        fig.canvas.mpl_connect("pick_event", _on_pick)
        
    return fig, ax
# ==========================================
# TKINTER GUI APPLICATION (Modernized)
# ==========================================

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class StatCard(ctk.CTkFrame):
    def __init__(self, master, title, value, unit="", color="#3b8ed0", **kwargs):
        super().__init__(master, corner_radius=12, fg_color="#1a1a1a", border_width=1, border_color="#333", **kwargs)
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=11, weight="bold"), text_color="#888").pack(pady=(12,0), padx=15, anchor="w")
        
        self.val_container = ctk.CTkFrame(self, fg_color="transparent")
        self.val_container.pack(fill="x", padx=15, pady=(0,15))
        
        self.v_label = ctk.CTkLabel(self.val_container, text=value, font=ctk.CTkFont(size=28, weight="bold"), text_color="white")
        self.v_label.pack(side="left")
        ctk.CTkLabel(self.val_container, text=f" {unit}", font=ctk.CTkFont(size=13), text_color=color).pack(side="left", pady=(8,0))
        
    def update_val(self, val): 
        self.v_label.configure(text=str(val))

class LocationManager(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent); self.title("Manage Observatory"); self.geometry("450x650"); self.parent = parent
        self.configure(fg_color="#121212"); self.grab_set()
        
        ttk.Label(self, text="Select Site to Edit:", background="#121212", foreground="#00a8ff", font=('Segoe UI', 11, 'bold')).pack(anchor="w", padx=20, pady=(15, 5))
        
        self.combo_var = tk.StringVar()
        self.combo = ctk.CTkOptionMenu(self, variable=self.combo_var, command=self.on_select)
        self.combo.pack(fill="x", padx=20, pady=(0, 15))
        
        ctk.CTkLabel(self, text="Site Name:", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
        self.name_e = ctk.CTkEntry(self); self.name_e.pack(pady=(0,10), padx=20, fill="x")
        
        ctk.CTkLabel(self, text="Latitude (deg):", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
        self.lat_e = ctk.CTkEntry(self); self.lat_e.pack(pady=(0,10), padx=20, fill="x")
        
        ctk.CTkLabel(self, text="Longitude (deg):", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
        self.lon_e = ctk.CTkEntry(self); self.lon_e.pack(pady=(0,10), padx=20, fill="x")
        
        ctk.CTkLabel(self, text="Altitude (m):", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
        self.alt_e = ctk.CTkEntry(self); self.alt_e.pack(pady=(0,10), padx=20, fill="x")
        
        ctk.CTkLabel(self, text="UTC Offset (hours):", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
        self.tz_e = ctk.CTkEntry(self); self.tz_e.pack(pady=(0,10), padx=20, fill="x")

        ctk.CTkLabel(self, text="Field of View (arcmin):", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20)
        self.fov_e = ctk.CTkEntry(self); self.fov_e.pack(pady=(0,10), padx=20, fill="x")
        
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=20)
        
        ctk.CTkButton(btn_frame, text="Save/Update", fg_color="#2ecc71", hover_color="#27ae60", command=self.save).pack(side="left", expand=True, padx=5)
        ctk.CTkButton(btn_frame, text="Delete Site", fg_color="#e74c3c", hover_color="#c0392b", command=self.delete).pack(side="left", expand=True, padx=5)
        ctk.CTkButton(btn_frame, text="Set Default", fg_color="#3b8ed0", hover_color="#2980b9", command=self.set_default).pack(side="left", expand=True, padx=5)
        
        self.refresh_list()

    def refresh_list(self):
        loc_names = list(self.parent.app_config.get("locations", {}).keys())
        self.combo.configure(values=loc_names)
        if loc_names:
            def_name = self.parent.app_config.get("default_location")
            if def_name in loc_names:
                self.combo_var.set(def_name)
            else:
                self.combo_var.set(loc_names[0])
            self.on_select(self.combo_var.get())

    def on_select(self, name):
        if name in self.parent.app_config.get("locations", {}):
            loc = self.parent.app_config["locations"][name]
            self.name_e.delete(0, tk.END); self.name_e.insert(0, name)
            self.lat_e.delete(0, tk.END); self.lat_e.insert(0, str(loc.get("lat", "")))
            self.lon_e.delete(0, tk.END); self.lon_e.insert(0, str(loc.get("lon", "")))
            self.alt_e.delete(0, tk.END); self.alt_e.insert(0, str(loc.get("alt", "")))
            self.tz_e.delete(0, tk.END); self.tz_e.insert(0, str(loc.get("utc_offset", 7.0)))
            self.fov_e.delete(0, tk.END); self.fov_e.insert(0, str(loc.get("fov", 34.9)))

    def save(self):
        try:
            n = self.name_e.get().strip()
            if not n: return
            self.parent.app_config["locations"][n] = {
                "lat": float(self.lat_e.get()), 
                "lon": float(self.lon_e.get()), 
                "alt": float(self.alt_e.get()), 
                "utc_offset": float(self.tz_e.get()),
                "fov": float(self.fov_e.get())
            }
            save_config(self.parent.app_config)
            self.parent.refresh_locs()
            if n == self.parent.loc_var.get():
                self.parent.apply_location()
            messagebox.showinfo("Saved", f"Site '{n}' updated successfully.", parent=self)
            self.refresh_list()
        except ValueError: 
            messagebox.showerror("Error", "Invalid numeric values entered.", parent=self)
    
    def delete(self):
        n = self.name_e.get().strip()
        if n in self.parent.app_config["locations"]:
            del self.parent.app_config["locations"][n]
            save_config(self.parent.app_config)
            self.parent.refresh_locs()
            self.refresh_list()
            messagebox.showinfo("Deleted", f"Site '{n}' has been removed.", parent=self)

    def set_default(self):
        n = self.name_e.get().strip()
        if n in self.parent.app_config["locations"]:
            self.parent.app_config["default_location"] = n
            save_config(self.parent.app_config)
            self.parent.refresh_locs()
            self.parent.loc_var.set(n)
            self.parent.apply_location()
            messagebox.showinfo("Default Set", f"Site '{n}' is now the default location.", parent=self)

class SkySurveyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SSTAC SkySurvey (Beta) Edition")
        self.geometry("1450x950")
        
        self.app_config = load_config()
        self.location = None
        self.utc_offset = 7.0 
        self.last_selected = []
        self.last_mode = ""
        self.last_date = ""
        self.neocp_objects = []
        self.neocp_status = "Offline"
        self.output_dir = Path.cwd()
        
        self._q = queue.Queue()
        self._is_polling = False
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # ----------------------------------------------------
        # FIX: Locked Column Width (Sidebar will NOT shrink)
        self.grid_columnconfigure(0, minsize=420)
        # ----------------------------------------------------
        
        self._build_ui()
        self.apply_location()
        
        self.after(500, self.on_update_neocp)

    def _build_ui(self):
        # --- SIDEBAR ---
        self.sidebar = ctk.CTkFrame(self, width=420, corner_radius=0, fg_color="#0d0d0d")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        
        ctk.CTkLabel(self.sidebar, text="SSTAC SURVEY", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(25, 5))
        ctk.CTkLabel(self.sidebar, text="Beta EDITION", font=ctk.CTkFont(size=11), text_color="#555").pack(pady=(0, 20))
        
        self.loc_var = tk.StringVar()
        self.loc_menu = ctk.CTkOptionMenu(self.sidebar, variable=self.loc_var, values=list(self.app_config["locations"].keys()), command=lambda _: self.apply_location())
        self.loc_menu.pack(pady=5, padx=20, fill="x")
        
        self.loc_info_label = ctk.CTkLabel(self.sidebar, text="Lat: -- | Lon: --\nFOV: --", text_color="#00a8ff", font=ctk.CTkFont(size=11), justify="left")
        self.loc_info_label.pack(pady=(0,5), padx=20, anchor="w")
        
        ctk.CTkButton(self.sidebar, text="⚙ Manage Observatory", fg_color="#333", hover_color="#444", command=lambda: LocationManager(self)).pack(pady=5, padx=20, fill="x")

        # Scrollable Parameters
        scroll = ctk.CTkScrollableFrame(self.sidebar, fg_color="transparent", label_text="Survey Parameters")
        scroll.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(scroll, text="Survey Date (Local)", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(10,0))
        self.date_var = tk.StringVar(value=local_date_default())
        ctk.CTkEntry(scroll, textvariable=self.date_var).pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(scroll, text="Survey Mode", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(10,0))
        self.mode_var = tk.StringVar(value="FULL-NIGHT (NEO+MBA)")
        self.cb_mode = ctk.CTkOptionMenu(scroll, variable=self.mode_var, values=["FULL-NIGHT (NEO+MBA)", "FULL-NIGHT (COMET+MBA)", "FULL-NIGHT (NEO+PHA)", "FULL-NIGHT (COMET+PHA)", "NEO", "COMET-TWILIGHT", "MBA", "PHA-MIDNIGHT", "DEEP-OPPOSITION"], command=self.on_mode_change)
        self.cb_mode.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(scroll, text="Max Fields", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=10, pady=(10,0))
        self.max_var = tk.IntVar(value=10)
        ctk.CTkEntry(scroll, textvariable=self.max_var).pack(fill="x", padx=10, pady=5)

        self.moon_var = tk.DoubleVar(value=30)
        mf = ctk.CTkFrame(scroll, fg_color="transparent"); mf.pack(fill="x", padx=10, pady=(15,0))
        ctk.CTkLabel(mf, text="Min Moon Sep", text_color="#aaa").pack(side="left")
        self.m_lbl = ctk.CTkLabel(mf, text="30°", text_color="#3b8ed0"); self.m_lbl.pack(side="right")
        ctk.CTkSlider(scroll, from_=0, to=90, variable=self.moon_var, command=lambda v: self.m_lbl.configure(text=f"{int(v)}°")).pack(fill="x", padx=10, pady=5)
        
        self.alt_var = tk.DoubleVar(value=25)
        af = ctk.CTkFrame(scroll, fg_color="transparent"); af.pack(fill="x", padx=10, pady=(10,0))
        ctk.CTkLabel(af, text="Min Target Alt", text_color="#aaa").pack(side="left")
        self.a_lbl = ctk.CTkLabel(af, text="25°", text_color="#f1c40f"); self.a_lbl.pack(side="right")
        ctk.CTkSlider(scroll, from_=10, to=60, variable=self.alt_var, command=lambda v: self.a_lbl.configure(text=f"{int(v)}°")).pack(fill="x", padx=10, pady=5)

        self.hist_var = tk.BooleanVar(value=True)
        self.hist_check = ctk.CTkCheckBox(scroll, text="Avoid History", variable=self.hist_var)
        self.hist_check.pack(anchor="w", padx=10, pady=10)

        self.mba_avoid_var = tk.BooleanVar(value=True)
        self.mba_avoid_check = ctk.CTkCheckBox(scroll, text="Avoid MBA Zone", variable=self.mba_avoid_var)
        self.mba_avoid_check.pack(anchor="w", padx=10, pady=5)
        
        self.avoid_gal_var = tk.BooleanVar(value=True)
        self.avoid_gal_check = ctk.CTkCheckBox(scroll, text="Avoid Galactic Plane", variable=self.avoid_gal_var)
        self.avoid_gal_check.pack(anchor="w", padx=10, pady=(10, 0))
        
        gal_f = ctk.CTkFrame(scroll, fg_color="transparent")
        gal_f.pack(fill="x", padx=10, pady=(5, 5))
        ctk.CTkLabel(gal_f, text="Exclude |b| <", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(side="left", padx=(25, 0))
        self.gal_b_var = tk.DoubleVar(value=12.0)
        ctk.CTkEntry(gal_f, textvariable=self.gal_b_var, width=60, height=26).pack(side="left", padx=10)
        ctk.CTkLabel(gal_f, text="deg", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(side="left")

        self.use_overlap_var = tk.BooleanVar(value=True)
        self.use_overlap_check = ctk.CTkCheckBox(scroll, text="Enable Field Overlap Control", variable=self.use_overlap_var)
        self.use_overlap_check.pack(anchor="w", padx=10, pady=(10, 0))
        
        olap_f = ctk.CTkFrame(scroll, fg_color="transparent")
        olap_f.pack(fill="x", padx=10, pady=(5, 5))
        ctk.CTkLabel(olap_f, text="Overlap Size:", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(side="left", padx=(25, 0))
        self.overlap_var = tk.IntVar(value=10)
        ctk.CTkEntry(olap_f, textvariable=self.overlap_var, width=60, height=26).pack(side="left", padx=10)
        ctk.CTkLabel(olap_f, text="%", text_color="#aaa", font=ctk.CTkFont(size=12)).pack(side="left")

        self.use_neocp_var = tk.BooleanVar(value=True)
        self.use_neocp_check = ctk.CTkCheckBox(scroll, text="NEOCP Weighting", variable=self.use_neocp_var)
        self.use_neocp_check.pack(anchor="w", padx=10, pady=(10,5))
        self.btn_update_neocp = ctk.CTkButton(scroll, text="🔄 Update NEOCP", command=self.on_update_neocp, fg_color="#333", hover_color="#444")
        self.btn_update_neocp.pack(fill="x", padx=25, pady=(0, 10))

        # Main Action Buttons
        self.btn_gen = ctk.CTkButton(self.sidebar, text="🚀 Generate Plan", command=self.on_gen, fg_color="#2ecc71", hover_color="#27ae60", font=ctk.CTkFont(size=15, weight="bold"))
        self.btn_gen.pack(pady=10, padx=20, fill="x")
        ctk.CTkButton(self.sidebar, text="🌌 Show Sky Map", command=self.on_show_map, fg_color="#3b8ed0").pack(pady=5, padx=20, fill="x")
        self.btn_export = ctk.CTkButton(self.sidebar, text="💾 Save N.I.N.A. CSV", command=self.on_export_csv, fg_color="#e67e22", hover_color="#d35400")
        self.btn_export.pack(pady=5, padx=20, fill="x")

        # --- MAIN VIEW ---
        self.main = ctk.CTkFrame(self, fg_color="#0a0a0a", corner_radius=0); self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_rowconfigure(1, weight=1) 
        self.main.grid_columnconfigure(0, weight=1)
        
        self.dash = ctk.CTkFrame(self.main, fg_color="transparent"); self.dash.grid(row=0, column=0, sticky="ew", padx=25, pady=(25,0))
        
        self.c_total = StatCard(self.dash, "TOTAL TARGETS", "0", "Fields"); self.c_total.pack(side="left", expand=True, fill="both", padx=10)
        self.c_alt = StatCard(self.dash, "AVG ALTITUDE", "0.0", "Deg", "#f1c40f"); self.c_alt.pack(side="left", expand=True, fill="both", padx=10)
        self.c_moon = StatCard(self.dash, "MIN MOON SEP", "0.0", "Deg", "#e74c3c"); self.c_moon.pack(side="left", expand=True, fill="both", padx=10)

        self.t_frame = ctk.CTkFrame(self.main, fg_color="#181818", corner_radius=12); self.t_frame.grid(row=1, column=0, sticky="nsew", padx=25, pady=25)
        
        style = ttk.Style(); style.theme_use("clam")
        style.configure("Treeview", background="#181818", foreground="white", fieldbackground="#181818", rowheight=32, font=('Segoe UI', 11), borderwidth=0)
        style.configure("Treeview.Heading", background="#2b2b2b", foreground="white", font=('Segoe UI', 11, 'bold'), borderwidth=0, relief="flat")
        style.map('Treeview', background=[('selected', '#1f538d')])
        
        cols = ("#", "Target ID", "RA", "Dec", "Sector", "Window", "Time", "Alt", "Dur (h)")
        self.tree = ttk.Treeview(self.t_frame, columns=cols, show="headings")
        for c in cols: self.tree.heading(c, text=c); self.tree.column(c, width=100, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        sc = ttk.Scrollbar(self.t_frame, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=sc.set); sc.pack(side="right", fill="y")
        
        # --- LOG CONSOLE ---
        self.log_frame = ctk.CTkFrame(self.main, fg_color="#121212", corner_radius=12, height=140)
        self.log_frame.grid(row=2, column=0, sticky="ew", padx=25, pady=(0, 25))
        self.log_frame.pack_propagate(False)
        self.log_label = ctk.CTkLabel(self.log_frame, text="System Console", font=ctk.CTkFont(size=12, weight="bold"), text_color="#00a8ff")
        self.log_label.pack(anchor="w", padx=15, pady=(5,0))
        self.log = ctk.CTkTextbox(self.log_frame, fg_color="#0a0a0a", text_color="#a4b0be", font=ctk.CTkFont(family="Consolas", size=12))
        self.log.pack(fill="both", expand=True, padx=10, pady=(5,10))
        self.log.configure(state="disabled")
        self.log_write("SSTAC Planner Ready.")

        # --- STATUS BAR (BOTTOM FULL SPAN) ---
        self.status_bar = ctk.CTkFrame(self, height=35, corner_radius=0, fg_color="#121212")
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        
        self.status_label = ctk.CTkLabel(self.status_bar, text="System Online", text_color="#a4b0be", font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=20)
        
        self.prog_pct_label = ctk.CTkLabel(self.status_bar, text="0%", text_color="#2ecc71", font=ctk.CTkFont(size=12, weight="bold"))
        self.prog_pct_label.pack(side="right", padx=(0, 20))
        
        self.prog = ctk.CTkProgressBar(self.status_bar, height=10, fg_color="#1a1a1a", progress_color="#2ecc71")
        self.prog.pack(side="right", fill="x", expand=True, padx=15)
        self.prog.set(0)
        self.on_mode_change()

    def on_mode_change(self, *_args):
        mode = (self.mode_var.get() or "").strip().upper()

        mba_modes = {
            "MBA",
            "DEEP-OPPOSITION",
            "FULL-NIGHT (NEO+MBA)",
            "FULL-NIGHT (COMET+MBA)",
        }

        if mode in mba_modes:
            self.mba_avoid_var.set(False)
            try:
                self.mba_avoid_check.configure(state="disabled")
            except Exception:
                pass
        else:
            self.mba_avoid_var.set(True)
            try:
                self.mba_avoid_check.configure(state="normal")
            except Exception:
                pass

    def log_write(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_locs(self):
        loc_names = list(self.app_config.get("locations", {}).keys())
        self.loc_menu.configure(values=loc_names)

    def apply_location(self):
        global FOV_DEG
        loc_names = list(self.app_config.get("locations", {}).keys())
        self.loc_menu.configure(values=loc_names)
        name = self.loc_var.get()
        if not name or name not in loc_names: 
            name = self.app_config.get("default_location", loc_names[0] if loc_names else "Default")
            
        if name in loc_names:
            self.loc_var.set(name)
            loc_data = self.app_config["locations"][name]
            self.location = EarthLocation(lat=loc_data["lat"] * u.deg, lon=loc_data["lon"] * u.deg, height=loc_data["alt"] * u.m)
            self.utc_offset = loc_data.get("utc_offset", 7.0)
            
            fov_arcmin = loc_data.get("fov", 34.9)
            FOV_DEG = fov_arcmin / 60.0
            
            self.loc_info_label.configure(text=f"Lat: {loc_data['lat']:.4f}° | Lon: {loc_data['lon']:.4f}°\nFOV: {fov_arcmin:.1f}' | UTC: {self.utc_offset:+g}h")

    def reset_progress(self):
        self.prog.set(0)
        self.prog_pct_label.configure(text="0%")
        self.status_label.configure(text="System Online")

    def on_update_neocp(self):
        self.status_label.configure(text="Connecting to MPC/Project Pluto...")
        self.prog.set(0); self.prog_pct_label.configure(text="0%")
        self.log_write("Initiating NEOCP Data Fetch...")
        
        def fetch_task():
            try:
                res, msg, err = load_or_fetch_neocp(progress_cb=lambda d, t, s: self._q.put(("prog", 0.0 if t <= 0 else (d / t) * 100.0, s, d, t)))
                if err: 
                    self._q.put(("neocp_err", msg))
                else: 
                    self._q.put(("neocp_ok", [res, msg]))
            except Exception as e:
                self._q.put(("err", f"Failed to fetch NEOCP: {str(e)}", traceback.format_exc()))
                
        threading.Thread(target=fetch_task, daemon=True).start()
        if not self._is_polling: 
            self._is_polling = True
            self._poll_worker()

    def on_gen(self):
        if self.location is None: return messagebox.showerror("Error", "No Observatory Selected.")
        for item in self.tree.get_children(): self.tree.delete(item)
        
        self.c_total.update_val("0")
        self.c_alt.update_val("0.0")
        self.c_moon.update_val("0.0")
        
        self.btn_gen.configure(state="disabled", text="⌛ Computing...")
        self.prog.set(0); self.prog_pct_label.configure(text="0%")
        
        threading.Thread(target=self._worker, daemon=True).start()
        if not self._is_polling: 
            self._is_polling = True
            self.after(100, self._poll_worker)

    def _worker(self):
        try:
            use_nc = self.use_neocp_var.get()
            if use_nc and not self.neocp_objects:
                self.neocp_objects, msg, _ = load_or_fetch_neocp(progress_cb=lambda d, t, s: self._q.put(("prog", (d/t)*100 if t>0 else 0, s)))
                
            res = generate_plan(
                date_str_local=self.date_var.get(), mode=self.mode_var.get(), location=self.location, utc_offset=self.utc_offset, 
                min_moon_deg=self.moon_var.get(), min_alt_deg=self.alt_var.get(), max_fields=self.max_var.get(), 
                use_history=self.hist_var.get(), avoid_galactic=self.avoid_gal_var.get(), gal_b_min_deg=self.gal_b_var.get(), 
                use_overlap=self.use_overlap_var.get(), overlap_percent=self.overlap_var.get(), 
                use_neocp_weighting=use_nc, avoid_mba_zone=self.mba_avoid_var.get(), neocp_objects=self.neocp_objects, 
                progress_cb=lambda d, t, s: self._q.put(("prog", (d/t)*100 if t>0 else 0, s))
            )
            self._q.put(("ok", res))
        except Exception as ex: 
            self._q.put(("err", str(ex), traceback.format_exc()))

    def _poll_worker(self):
        try:
            item = self._q.get_nowait()
            
            if item[0] == "prog":
                p_val = min(1.0, max(0.0, item[1] / 100.0))
                self.prog.set(p_val)
                self.prog_pct_label.configure(text=f"{int(item[1])}%")
                if len(item) > 3:
                    self.status_label.configure(text=f"{item[2]}... {item[3]}/{item[4]}" if item[4] > 0 else item[2])
                else:
                    self.status_label.configure(text=item[2])
                    
            elif item[0] == "neocp_err":
                messagebox.showerror("NEOCP Error", item[1])
                self.status_label.configure(text="Ready")
                self.log_write("NEOCP Data Fetch Failed.")
                self._is_polling = False
                return
                
            elif item[0] == "neocp_ok":
                self.neocp_objects, self.neocp_status = item[1][0], item[1][1]
                self.log_write(item[1][1])
                self.prog.set(1.0); self.prog_pct_label.configure(text="100%")
                self.status_label.configure(text="NEOCP Synced Successfully")
                self.after(3000, self.reset_progress)
                self._is_polling = False
                return
                
            elif item[0] == "err":
                self.btn_gen.configure(state="normal", text="🚀 Generate Plan")
                self.log_write("=== CRITICAL ERROR ===")
                self.log_write(item[2])
                messagebox.showerror("Error", item[1])
                self.status_label.configure(text="Computation Failed")
                self.after(3000, self.reset_progress)
                self._is_polling = False
                return
                
            elif item[0] == "ok":
                self.btn_gen.configure(state="normal", text="🚀 Generate Plan")
                self.prog.set(1.0); self.prog_pct_label.configure(text="100%")
                self.status_label.configure(text="Plan Computed Successfully")
                self.after(3000, self.reset_progress)
                
                _, _, _, selected, stats = item[1]
                self.last_selected = selected; self.last_mode = self.mode_var.get().upper(); self.last_date = self.date_var.get()
                self.log_write(f"Generated [{self.last_mode}] | Found: {len(selected)} fields (Grid: {stats['grid_total']})")
                
                self._update_ui_data()
                self._is_polling = False
                return
                
        except queue.Empty: 
            pass
            
        if self._is_polling: 
            self.after(100, self._poll_worker)

    def _update_ui_data(self):
        if not self.last_selected: 
            self.c_total.update_val("0")
            self.c_alt.update_val("N/A")
            self.c_moon.update_val("N/A")
            messagebox.showwarning("No Fields Found", "No fields passed the operational constraints.")
            return
            
        alts = [d['best_alt'] for d in self.last_selected]
        moons = [d['moon_sep'] for d in self.last_selected]
        
        self.c_total.update_val(len(self.last_selected))
        self.c_alt.update_val(f"{sum(alts)/len(self.last_selected):.1f}")
        self.c_moon.update_val(f"{min(moons):.1f}")
        
        for i, it in enumerate(self.last_selected, 1):
            sec = it.get("sector", "")[:3]
            orig = it.get("orig_mode", self.last_mode)[:3]
            name = f"{orig}_{sec}_{self.last_date}_{i:03d}" if sec else f"{orig}_{self.last_date}_{i:03d}"
            
            r, d = format_ra_dec(it["coord"])
            ws = utc_to_local_dt(it['window_start'], self.utc_offset).strftime('%H:%M')
            we = utc_to_local_dt(it['window_end'], self.utc_offset).strftime('%H:%M')
            bt = utc_to_local_dt(it["best_time"], self.utc_offset).strftime("%H:%M")
            
            self.tree.insert("", "end", values=(i, name, r, d, sec, f"{ws}-{we}", bt, f"{it['best_alt']:.0f}", f"{it.get('duration',0):.1f}"))

    def on_export_csv(self):
        if not self.last_selected: 
            return messagebox.showerror("Export Error", "Generate a plan first.")
        
        out_path = self.output_dir / f"nightly_targets_{self.last_mode.replace(' ','_')}_{self.last_date}.csv"
        export_nina_csv(out_path, self.last_mode, self.last_date, self.utc_offset, self.last_selected)
        
        self.log_write(f"Exported successfully: {out_path.name}")
        messagebox.showinfo("Export Successful", f"N.I.N.A CSV saved to:\n{out_path}")

    def on_show_map(self):
        if not self.last_selected: 
            return messagebox.showwarning("Warning", "Please generate a plan first!")
        try:
            loc_name = self.loc_var.get()
            self.log_write(f"Rendering Full Sky Map for {loc_name}...")
            fig, ax = build_sky_map_figure(
                self.last_selected, 
                self.last_mode, 
                self.last_date, 
                self.utc_offset, 
                self.neocp_objects, 
                self.location,
                location_name=loc_name
            )
            plt.show()  
        except Exception as e:
            self.log_write("=== MAP RENDER ERROR ===")
            self.log_write(traceback.format_exc())
            messagebox.showerror("Map Error", "Failed to render Sky Map. Check System Console.")

if __name__ == "__main__": 
    app = SkySurveyApp()
    app.mainloop()