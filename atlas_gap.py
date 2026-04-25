"""
atlas_gap.py  —  SSTAC Survey Gap Intelligence v3
===================================================
อ่าน MPC Sky Coverage PNG → ตรวจ legend → detect coverage ด้วย Hue

แก้ปัญหาหลัก:
  - สีเปลี่ยนทุกคืนตาม observatory ที่ submit (W68/R17/M22 ล้วนเป็น ATLAS)
  - ใช้ Hue-based detection แทน RGB hardcode
  - อ่าน legend ก่อน → หา dominant color = largest survey = ATLAS-equivalent
  - ตกหลังมาใช้ ALL survey coverage (any major survey = avoid)

ATLAS codes: W68 (Chile), T05 (Hawaii HKO), T08 (Mauna Loa), M22 (S.Africa), R17 (ATLAS)
"""

from __future__ import annotations
import math
import colorsys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
import re

import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u


# ──────────────────────────────────────────────────────────────────────────────
#  Date extraction from MPC sky coverage PNG
# ──────────────────────────────────────────────────────────────────────────────

def extract_date_from_coverage_png(png_path: str | Path) -> Optional[date]:
    """
    Read the coverage date directly from an MPC sky coverage PNG.

    The MPC image always has the coverage date in the top-right corner in
    large white text, e.g. "2026/04/18".  We try three strategies in order:

    1. pytesseract OCR on the top-right crop (best accuracy).
    2. Pixel-scan subtitle line for "Plot prepared YYYY/MM/DD.xxx" text
       by looking at bright pixel runs — extracts date from image metadata line.
    3. Returns None (caller falls back to filename).
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        img = Image.open(str(png_path)).convert("RGB")
        w, h = img.size

        # ── Strategy 1: pytesseract OCR on top-right corner ──────────────
        try:
            import pytesseract

            # Crop: top 18% of height, right 38% of width
            crop_box = (int(w * 0.62), 0, w, int(h * 0.18))
            crop = img.crop(crop_box)

            # Enlarge for better OCR accuracy
            scale = 3
            crop = crop.resize((crop.width * scale, crop.height * scale),
                               Image.LANCZOS)

            # Binarize: white text on black → keep bright pixels
            arr = np.array(crop)
            # MPC date text is white/light — threshold brightness
            bright = (arr[:, :, 0].astype(int) +
                      arr[:, :, 1].astype(int) +
                      arr[:, :, 2].astype(int)) > 400
            bin_arr = np.where(bright[:, :, None], arr,
                               np.zeros_like(arr)).astype(np.uint8)
            from PIL import Image as _Img
            bin_img = _Img.fromarray(bin_arr)

            ocr_cfg = "--psm 6 -c tessedit_char_whitelist=0123456789/"
            text = pytesseract.image_to_string(bin_img, config=ocr_cfg)

            # Find YYYY/MM/DD pattern
            m = re.search(r"(\d{4})[/\-](\d{2})[/\-](\d{2})", text)
            if m:
                return datetime.strptime(
                    f"{m.group(1)}{m.group(2)}{m.group(3)}", "%Y%m%d").date()
        except Exception:
            pass   # pytesseract not installed or failed — try next strategy

        # ── Strategy 2: scan subtitle area for "Plot prepared YYYY/MM/DD" ─
        # The subtitle is in the top ~10% of the image, centred, small white text.
        # We scan pixel rows for bright (white) pixels and collect contiguous
        # character-width runs, then OCR or regex-match the found text.
        try:
            arr = np.array(img)
            subtitle_band = arr[int(h * 0.06): int(h * 0.12), :, :]
            # Sum brightness per pixel
            bright_mask = (subtitle_band[:, :, 0].astype(int) +
                           subtitle_band[:, :, 1].astype(int) +
                           subtitle_band[:, :, 2].astype(int)) > 400
            # Collect columns that have any bright pixel → text columns
            col_has_text = bright_mask.any(axis=0)
            # Build a simple string of "bright column" markers and find date runs
            # by looking for groups of digits separated by "/"
            # Instead: try pytesseract on the full subtitle strip
            try:
                import pytesseract
                sub_img = Image.fromarray(subtitle_band)
                scale = 4
                sub_img = sub_img.resize(
                    (sub_img.width * scale, sub_img.height * scale),
                    Image.LANCZOS)
                text2 = pytesseract.image_to_string(sub_img)
                m = re.search(r"(\d{4})[/\-](\d{2})[/\-](\d{1,2})", text2)
                if m:
                    return datetime.strptime(
                        f"{m.group(1)}{m.group(2)}{m.group(3).zfill(2)}",
                        "%Y%m%d").date()
            except Exception:
                pass
        except Exception:
            pass

    except Exception:
        pass

    return None  # caller will fall back to filename parsing

CACHE_DIR      = Path("atlas_gap_cache")
MAX_BONUS      = 6.0
GAP_RADIUS_DEG = 3.8
AGE_RACE_DAYS  = 2
AGE_GAP_DAYS   = 3

# ATLAS observatory codes (all generations)
ATLAS_CODES = {"W68", "T05", "T08", "M22", "R17"}

# Hue ranges for known legend colors (H in 0-1 from colorsys)
# magenta ≈ 0.83  yellow ≈ 0.17  green ≈ 0.33  blue ≈ 0.67  orange ≈ 0.08
HUE_RANGES = {
    "magenta": (0.78, 0.95),   # R17, W68 typical
    "yellow":  (0.12, 0.20),   # M22 typical
    "green":   (0.27, 0.40),   # I52
    "blue":    (0.58, 0.72),   # Y00, V06
    "orange":  (0.04, 0.11),   # V11
}

# Which hues → ATLAS network
ATLAS_HUES = {"magenta", "yellow"}   # M22=yellow, R17/W68=magenta


# ──────────────────────────────────────────────────────────────────────────────
#  Color utilities
# ──────────────────────────────────────────────────────────────────────────────

def _rgb_to_hue(r: int, g: int, b: int) -> Optional[float]:
    """Return HSV hue (0-1) or None if pixel is too dark/gray."""
    rf, gf, bf = r/255.0, g/255.0, b/255.0
    h, s, v = colorsys.rgb_to_hsv(rf, gf, bf)
    if v < 0.30 or s < 0.35:   # too dark or too gray
        return None
    return h


def _classify_hue(hue: float) -> Optional[str]:
    """Map hue → color name using HUE_RANGES, or None."""
    for name, (lo, hi) in HUE_RANGES.items():
        if lo <= hue <= hi:
            return name
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Step 1 — Scan legend strip → map color_name → pixel_count in map
# ──────────────────────────────────────────────────────────────────────────────

def _scan_legend_colors(arr: np.ndarray) -> dict[str, int]:
    """
    Scan bottom legend strip of MPC sky coverage image.
    Returns {color_name: pixel_count_in_legend_strip}.
    """
    h, w = arr.shape[:2]
    legend_top = int(h * 0.85)
    legend = arr[legend_top:, :]

    counts: dict[str, int] = {}
    for row in legend:
        for px in row:
            hue = _rgb_to_hue(int(px[0]), int(px[1]), int(px[2]))
            if hue is None:
                continue
            name = _classify_hue(hue)
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


# ──────────────────────────────────────────────────────────────────────────────
#  Step 2 — Detect coverage in map area by hue
# ──────────────────────────────────────────────────────────────────────────────

def _detect_coverage_by_hue(
    arr:         np.ndarray,
    target_hues: set[str],
    sample_step: int = 3,
) -> list[tuple[int, int]]:
    """
    Scan the map area (top 87% of image) for pixels matching target hues.
    Returns list of (px, py) pixel positions.
    """
    h, w = arr.shape[:2]
    map_bottom = int(h * 0.87)
    coverage_pixels = []

    for py in range(0, map_bottom, sample_step):
        for px in range(0, w, sample_step):
            r, g, b = int(arr[py, px, 0]), int(arr[py, px, 1]), int(arr[py, px, 2])
            hue = _rgb_to_hue(r, g, b)
            if hue is None:
                continue
            name = _classify_hue(hue)
            if name and name in target_hues:
                coverage_pixels.append((px, py))

    return coverage_pixels


# ──────────────────────────────────────────────────────────────────────────────
#  Step 3 — Mollweide pixel → RA/Dec
# ──────────────────────────────────────────────────────────────────────────────

def _pixel_to_radec(
    px: int, py: int,
    img_w: int, img_h: int,
) -> Optional[tuple[float, float]]:
    """
    Convert map pixel → (ra_deg, dec_deg).

    MPC Mollweide convention:
      center = RA 12h = 180°, Dec 0°
      x: right = Evening (low RA), left = Morning (high RA)
      y: up = North
    Map occupies ~90% of image width (5% margin each side) and ~85% height.
    """
    # Normalised coords accounting for plot margins
    margin_x = 0.04   # ~4% left/right margin
    margin_y_top = 0.12  # title area
    margin_y_bot = 0.15  # legend + axis area

    xn = (px / img_w - margin_x) / (1.0 - 2 * margin_x)   # 0=left=Morning, 1=right=Evening
    yn = 1.0 - (py / img_h - margin_y_top) / (1.0 - margin_y_top - margin_y_bot)

    # Convert to Mollweide normalized [-1,+1]
    xm = (xn - 0.5) * 2.0  # right→left RA decreases
    ym = (yn - 0.5) * 2.0

    # Ellipse check: Mollweide ellipse is x^2/(2*sqrt(2))^2 + y^2/sqrt(2)^2 <= 1
    # With xm in [-2,+2] and ym in [-1,+1] (normalised to sqrt(2) units):
    if (xm / 2.0) ** 2 + ym ** 2 > 1.0:
        return None

    # Mollweide inverse — correct formula:
    # The projection defines:  y_map = sqrt(2) * sin(theta)
    # so:  sin(theta) = ym / sqrt(2)   (ym already in [-1,+1])
    # Then: 2*theta + sin(2*theta) = pi * sin(dec)
    sin_theta = ym / math.sqrt(2.0)
    sin_theta = max(-1.0, min(1.0, sin_theta))
    theta = math.asin(sin_theta)

    sin_dec = (2.0 * theta + math.sin(2.0 * theta)) / math.pi
    dec_deg = math.degrees(math.asin(max(-1.0, min(1.0, sin_dec))))

    cos_theta = math.cos(theta)
    if abs(cos_theta) < 1e-9:
        ra_offset = 0.0
    else:
        # xm positive = right = Evening = lower RA (right of centre 12h)
        ra_offset = math.degrees(math.pi * xm / (2.0 * math.sqrt(2.0) * cos_theta))

    # Centre at RA = 180°, right side = lower RA
    ra_deg = (180.0 - ra_offset) % 360.0
    return ra_deg, dec_deg


# ──────────────────────────────────────────────────────────────────────────────
#  Main: parse PNG → gap map
# ──────────────────────────────────────────────────────────────────────────────

def parse_coverage_png(
    png_path: str | Path,
    age_days:    int = 1,
    sample_step: int = 4,
    atlas_only:  bool = True,
) -> tuple[list[dict], dict]:
    """
    Parse MPC Sky Coverage PNG.

    Strategy:
      1. Scan legend → find which hue names appear (magenta, yellow, etc.)
      2. Find ATLAS hues (magenta/yellow) in legend
      3. If none found, use ALL hues (any large survey = coverage)
      4. Detect coverage pixels by those hues in map area
      5. Convert pixels → RA/Dec via Mollweide inverse

    Returns
    -------
    (gap_map_list, info_dict)

    gap_map_list : list of {"coord", "age_days", "obs_code", "source"}
    info_dict    : {"atlas_hues_detected", "n_coverage_pixels", "fallback_used"}
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("Pillow not installed. Run: pip install Pillow")

    img  = Image.open(str(png_path)).convert("RGB")
    arr  = np.array(img)
    h, w = arr.shape[:2]

    # ── Step 1: Scan legend ────────────────────────────────────────────────
    legend_colors = _scan_legend_colors(arr)   # {color_name: count}

    # ── Step 2: Determine target hues ─────────────────────────────────────
    detected_atlas_hues = set(legend_colors.keys()) & ATLAS_HUES
    fallback_used = False

    if atlas_only and detected_atlas_hues:
        target_hues = detected_atlas_hues
    elif atlas_only and not detected_atlas_hues:
        # ATLAS not in legend this night — use ALL survey hues as fallback
        target_hues = set(legend_colors.keys())
        fallback_used = True
    else:
        target_hues = set(legend_colors.keys())

    info = {
        "legend_colors_found": legend_colors,
        "atlas_hues_detected": list(detected_atlas_hues),
        "target_hues":         list(target_hues),
        "fallback_used":       fallback_used,
    }

    if not target_hues:
        return [], info

    # ── Step 3: Detect coverage pixels ────────────────────────────────────
    coverage_pixels = _detect_coverage_by_hue(arr, target_hues, sample_step)
    info["n_coverage_pixels"] = len(coverage_pixels)

    if not coverage_pixels:
        return [], info

    # ── Step 4: Convert pixels → RA/Dec → SkyCoord ────────────────────────
    gap_map = []
    for px, py in coverage_pixels:
        result = _pixel_to_radec(px, py, w, h)
        if result is None:
            continue
        ra_deg, dec_deg = result
        try:
            coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg)
            gap_map.append({
                "coord":    coord,
                "age_days": max(1, age_days),
                "obs_code": "ATLAS",
                "source":   "png",
            })
        except Exception:
            continue

    info["n_fields_parsed"] = len(gap_map)
    return gap_map, info


# ──────────────────────────────────────────────────────────────────────────────
#  High-level loader (called from planner.py)
# ──────────────────────────────────────────────────────────────────────────────

def load_atlas_gap_map(
    png_paths:   list = None,
    date_strings: list = None,
    sample_step: int  = 4,
    try_api:     bool = False,
    progress_cb       = None,
) -> list[dict]:
    """
    Build ATLAS gap map from PNG images.

    Parameters
    ----------
    png_paths : list of (path, age_days) tuples or plain paths
                age_days inferred from filename date if possible
    """
    gap_map: list[dict] = []
    sources = list(png_paths or [])
    total   = len(sources)

    for step, item in enumerate(sources, 1):
        if progress_cb:
            progress_cb(step, max(total, 1), f"Parsing coverage PNG {step}/{total}...")

        # Unpack (path, age) or just path
        if isinstance(item, (list, tuple)) and len(item) == 2:
            png_path, age_days = item
        else:
            png_path  = item
            age_days  = 1
            m = re.search(r"(\d{8})", str(png_path))
            if m:
                try:
                    d    = datetime.strptime(m.group(1), "%Y%m%d").date()
                    age_days = max(1, (date.today() - d).days)
                except Exception:
                    pass

        try:
            pts, info = parse_coverage_png(
                png_path, age_days=age_days, sample_step=sample_step)
            gap_map.extend(pts)
            if progress_cb and info.get("n_fields_parsed", 0) > 0:
                progress_cb(step, max(total, 1),
                    f"PNG {step}: {info['n_fields_parsed']} fields, "
                    f"hues={info['target_hues']}, "
                    f"{'FALLBACK' if info['fallback_used'] else 'ATLAS'}")
        except ImportError:
            raise
        except Exception:
            continue

    return gap_map


# ──────────────────────────────────────────────────────────────────────────────
#  Gap bonus scorer (unchanged)
# ──────────────────────────────────────────────────────────────────────────────

def atlas_gap_bonus(
    coord:          SkyCoord,
    gap_map:        list[dict],
    gap_radius_deg: float = GAP_RADIUS_DEG,
    max_bonus:      float = MAX_BONUS,
) -> float:
    if not gap_map:
        return max_bonus

    atlas_coords = SkyCoord([p["coord"] for p in gap_map])
    seps         = coord.separation(atlas_coords).to_value(u.deg)
    close_mask   = seps <= gap_radius_deg

    if not np.any(close_mask):
        return max_bonus

    min_age = min(gap_map[i]["age_days"] for i in np.where(close_mask)[0])

    if min_age >= AGE_GAP_DAYS:
        return max_bonus
    elif min_age == AGE_RACE_DAYS:
        return max_bonus * 0.90
    else:
        return max_bonus * 0.10


def atlas_gap_summary(gap_map: list[dict]) -> dict:
    if not gap_map:
        return {"total_frames": 0, "codes": {}, "nights": [],
                "age1_frames": 0, "age2_frames": 0, "age3p_frames": 0}
    from collections import Counter
    return {
        "total_frames":  len(gap_map),
        "codes":         dict(Counter(p["obs_code"] for p in gap_map)),
        "sources":       dict(Counter(p.get("source","?") for p in gap_map)),
        "age1_frames":   sum(1 for p in gap_map if p["age_days"] == 1),
        "age2_frames":   sum(1 for p in gap_map if p["age_days"] == 2),
        "age3p_frames":  sum(1 for p in gap_map if p["age_days"] >= 3),
    }
