
import csv
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
from astropy.coordinates import SkyCoord

from config import HISTORY_DB_DIR, HISTORY_MASTER_CSV, PERFORMANCE_LOG_CSV
from astro_utils import format_ra_dec, utc_to_local_dt, to_utc_time


def ensure_history_dirs(base_dir=None):
    root = Path(base_dir or Path.cwd()) / HISTORY_DB_DIR
    (root / "plans").mkdir(parents=True, exist_ok=True)
    (root / "meta").mkdir(parents=True, exist_ok=True)
    return root


# ─────────────────────────────────────────────────────────────────
#  Robust CSV loader
#  Handles: mismatched column counts, encoding issues, empty files,
#           pandas version differences (on_bad_lines added in 1.3).
# ─────────────────────────────────────────────────────────────────

def _safe_read_csv(path):
    """Read a CSV tolerantly, skipping malformed rows.

    Returns a list of dicts (same as df.to_dict('records')), or [] on
    any unrecoverable error.  Never raises.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []

    # --- attempt 1: pandas with on_bad_lines='skip' (pandas >= 1.3) ---
    try:
        df = pd.read_csv(
            path,
            on_bad_lines="skip",   # silently skip malformed rows
            encoding="utf-8-sig",  # handle BOM produced by Excel
        )
        if df.empty:
            return []
        return df.to_dict(orient="records")
    except TypeError:
        pass  # older pandas — fall through
    except Exception:
        pass

    # --- attempt 2: older pandas keyword ---
    try:
        df = pd.read_csv(
            path,
            error_bad_lines=False,  # pandas < 1.3
            warn_bad_lines=False,
            encoding="utf-8-sig",
        )
        if df.empty:
            return []
        return df.to_dict(orient="records")
    except Exception:
        pass

    # --- attempt 3: pure-Python csv reader (always works) ---
    rows = []
    try:
        with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return []
            expected = len(reader.fieldnames)
            for raw in reader:
                # DictReader maps extra fields to None key – drop those rows
                if None in raw:
                    continue
                rows.append(dict(raw))
    except Exception:
        pass
    return rows


# ─────────────────────────────────────────────────────────────────
#  Archive
# ─────────────────────────────────────────────────────────────────

def archive_plan(date_str_local, mode, utc_offset, location_name, settings, selected, base_dir=None):
    root = ensure_history_dirs(base_dir)
    safe_mode = mode.upper().replace(" ", "_")
    stem = f"{date_str_local}_{safe_mode}"
    plan_csv = root / "plans" / f"{stem}.csv"
    meta_json = root / "meta" / f"{stem}.json"
    master_csv = root / HISTORY_MASTER_CSV

    rows = []
    for item in selected:
        ra, dec = format_ra_dec(item["coord"])
        rows.append({
            "date_local": date_str_local,
            "mode": mode,
            "location_name": location_name,
            "target_id": item.get("target_id", ""),
            "role": item.get("role", "DISCOVERY"),
            "sector": item.get("sector", ""),
            "ra": ra,
            "dec": dec,
            "window_start_local": utc_to_local_dt(item["window_start"], utc_offset).strftime("%Y-%m-%d %H:%M"),
            "window_end_local": utc_to_local_dt(item["window_end"], utc_offset).strftime("%Y-%m-%d %H:%M"),
            "best_time_local": utc_to_local_dt(item["best_time"], utc_offset).strftime("%Y-%m-%d %H:%M"),
            "best_alt_deg": float(item.get("best_alt", 0.0)),
            "moon_sep_deg": float(item.get("moon_sep", 0.0)),
            "phase_angle_deg": float(item.get("phase_best", 0.0)),
            "gal_b_deg": float(item.get("gal_b_deg", 0.0)),
            "duration_hr": float(item.get("duration", 0.0)),
            "score": float(item.get("score", 0.0)),
        })

    pd.DataFrame(rows).to_csv(plan_csv, index=False)

    meta = {
        "date_local": date_str_local,
        "mode": mode,
        "location_name": location_name,
        "utc_offset": utc_offset,
        "settings": settings,
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "plan_csv": plan_csv.name,
    }
    meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    master_exists = master_csv.exists()
    with master_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(rows[0].keys()) if rows else [
                "date_local", "mode", "location_name", "target_id", "role", "sector", "ra", "dec",
                "window_start_local", "window_end_local", "best_time_local", "best_alt_deg",
                "moon_sep_deg", "phase_angle_deg", "gal_b_deg", "duration_hr", "score"
            ]
        )
        if not master_exists:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)

    return plan_csv, meta_json


# ─────────────────────────────────────────────────────────────────
#  Load helpers
# ─────────────────────────────────────────────────────────────────

def _row_to_item(row, utc_offset):
    coord = SkyCoord(row["ra"], row["dec"], unit=("hourangle", "deg"))
    best_local = datetime.fromisoformat(row["best_time_local"])
    ws_local   = datetime.fromisoformat(row["window_start_local"])
    we_local   = datetime.fromisoformat(row["window_end_local"])
    return {
        "target_id":  row.get("target_id", ""),
        "role":       row.get("role", "DISCOVERY"),
        "sector":     row.get("sector", ""),
        "coord":      coord,
        "window_start": to_utc_time(ws_local, utc_offset),
        "window_end":   to_utc_time(we_local, utc_offset),
        "best_time":    to_utc_time(best_local, utc_offset),
        "best_alt":   float(row.get("best_alt_deg", 0.0)),
        "moon_sep":   float(row.get("moon_sep_deg", 0.0)),
        "phase_best": float(row.get("phase_angle_deg", 0.0)),
        "gal_b_deg":  float(row.get("gal_b_deg", 0.0)),
        "duration":   float(row.get("duration_hr", 0.0)),
        "score":      float(row.get("score", 0.0)),
        "orig_mode":  row.get("mode", ""),
    }


def load_archived_plan(path_like, utc_offset=7.0):
    path = Path(path_like)
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path)
        rows = df.to_dict(orient="records")
    else:
        rows = _safe_read_csv(path)

    if not rows:
        raise ValueError(f"No valid rows found in {path.name}")

    selected = []
    for r in rows:
        try:
            selected.append(_row_to_item(r, utc_offset))
        except Exception:
            continue

    date_local = rows[0].get("date_local", "")
    mode       = rows[0].get("mode", "")
    return {"selected": selected, "date_local": date_local, "mode": mode, "raw_rows": rows}


def load_master_history(base_dir=None):
    root = ensure_history_dirs(base_dir)
    return _safe_read_csv(root / HISTORY_MASTER_CSV)


def load_performance_log(base_dir=None):
    root = ensure_history_dirs(base_dir)
    return _safe_read_csv(root / PERFORMANCE_LOG_CSV)


# ─────────────────────────────────────────────────────────────────
#  Planner memory helpers
# ─────────────────────────────────────────────────────────────────

def load_recent_history_points(date_str_local, lookback_nights=5, base_dir=None, mode=None):
    rows = load_performance_log(base_dir)
    if not rows:
        rows = load_master_history(base_dir)
    if not rows:
        return []

    current_date = datetime.fromisoformat(date_str_local).date()
    pts = []
    for row in rows:
        try:
            status = str(row.get("status", "OBSERVED")).strip().upper()
            if "ra" not in row or "dec" not in row:
                continue
            if status not in ("OBSERVED", "PARTIAL", "OBSERVED "):
                continue
            row_date = datetime.fromisoformat(
                str(pd.to_datetime(row.get("date_local", "")).date())
            ).date()
            age_days = (current_date - row_date).days
            if age_days <= 0 or age_days > lookback_nights:
                continue
            coord = SkyCoord(row["ra"], row["dec"], unit=("hourangle", "deg"))
            pts.append({
                "coord":      coord,
                "age_days":   age_days,
                "mode":       row.get("mode", ""),
                "target_id":  row.get("target_id", ""),
                "date_local": row.get("date_local", ""),
            })
        except Exception:
            continue
    return pts


def load_observed_history_rows(base_dir=None):
    """Return rows from the performance log where status == OBSERVED.

    Uses _safe_read_csv so a malformed CSV will never crash the caller.
    """
    rows = load_performance_log(base_dir)
    if not rows:
        return []

    observed = []
    for row in rows:
        try:
            status = str(row.get("status", "")).strip().upper()
            if status != "OBSERVED":
                continue
            ra  = str(row.get("ra",  "")).strip()
            dec = str(row.get("dec", "")).strip()
            if not ra or not dec or ra == "nan" or dec == "nan":
                continue
            observed.append(row)
        except Exception:
            continue
    return observed


# ─────────────────────────────────────────────────────────────────
#  Performance log append
# ─────────────────────────────────────────────────────────────────

def append_field_performance(record, base_dir=None):
    root = ensure_history_dirs(base_dir)
    perf_csv = root / PERFORMANCE_LOG_CSV
    exists = perf_csv.exists()
    fields = [
        "date_local", "mode", "target_id", "status",
        "ra", "dec",
        "window_start_local", "window_end_local", "best_time_local",
        "best_alt_deg", "moon_sep_deg", "duration_hr", "score",
        "sky_mag_arcsec2", "seeing_fwhm_arcsec",
        "limit_mag_single", "limit_mag_stack",
        "known_objects", "detected_known_objects",
        "discovery_candidates", "note", "updated_at"
    ]
    with perf_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        out = {k: record.get(k, "") for k in fields}
        out["updated_at"] = datetime.now().isoformat(timespec="seconds")
        writer.writerow(out)
    return perf_csv
