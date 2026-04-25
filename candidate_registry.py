"""
candidate_registry.py  —  SSTAC v1.3
Candidate lifecycle management linked to SSTAC Object Code system.

Primary key = SSTAC object code (e.g. "T631X31")
All date/mode/field metadata is decoded from the code itself
using the same logic as object_code.py — no duplication.

Data file: sstac_candidates.json  (same directory as other SSTAC data)
"""

import json
import re
from datetime import datetime, date, timedelta
from pathlib import Path

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

from config import BASE36, MODE_SECTOR_MAP, INV_MODE_MAP
from object_code import to_base36, from_base36

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────

CANDIDATE_DB   = "sstac_candidates.json"
BASE_YEAR      = 2020
FOV_ARCMIN     = 34.9          # arcmin — default O58 FOV
FOV_ARCSEC     = FOV_ARCMIN * 60.0  # 2094"

# Status lifecycle
STATUS_UNCONFIRMED = "UNCONFIRMED"   # tracklet sent to MPC, awaiting 2nd night
STATUS_CONFIRMED   = "CONFIRMED"     # 2nd-night recovery done, MPC accepted
STATUS_SUBMITTED   = "SUBMITTED"     # officially submitted / designated
STATUS_LOST        = "LOST"          # uncertainty > FOV, recovery unlikely
STATUS_REJECTED    = "REJECTED"      # turned out to be known object / artifact

ACTIVE_STATUSES = {STATUS_UNCONFIRMED, STATUS_CONFIRMED}

# Priority colour thresholds (fraction of FOV)
ALERT_GREEN  = 0.30   # uncertainty < 30% FOV
ALERT_YELLOW = 0.80   # uncertainty 30-80% FOV
# >80% = RED


# ─────────────────────────────────────────────
#  Code ↔ Metadata helpers  (reuse object_code logic)
# ─────────────────────────────────────────────

def decode_object_code(code: str) -> dict:
    """
    Decode a 7-char SSTAC code back to metadata.
    Returns dict with keys: year, doy, date_str, mode, sector, field_index, track_no
    Raises ValueError on bad format.
    """
    code = code.strip().upper()
    if len(code) != 7 or not code.startswith("T"):
        raise ValueError(f"Invalid SSTAC code '{code}' — must be T+6 chars")

    year      = BASE_YEAR + from_base36(code[1])
    doy       = from_base36(code[2:4])
    s_code    = code[4]
    field_idx = from_base36(code[5])
    track_no  = from_base36(code[6])

    try:
        dt = date(year, 1, 1) + timedelta(days=doy - 1)
    except (ValueError, OverflowError):
        raise ValueError(f"Invalid day-of-year {doy} for year {year}")

    mode_info = INV_MODE_MAP.get(s_code, ("UNKNOWN", "UNKNOWN"))
    return {
        "year":        year,
        "doy":         doy,
        "date_str":    dt.strftime("%Y-%m-%d"),
        "mode":        mode_info[0],
        "sector":      mode_info[1] if mode_info[1] else "NIGHT",
        "field_index": field_idx,
        "track_no":    track_no,
        "sector_code": s_code,
    }


def encode_from_field(field_name: str, track_no: int = 1) -> str:
    """
    Generate SSTAC object code from field name like 'NE_20260419_003'.
    Mirrors ObjectCodeWindow.on_generate() logic but as a pure function.
    """
    m = re.match(r"^(NE|HI)_(\d{8})_(\d{3})$", field_name.strip().upper())
    if not m:
        raise ValueError(f"Field name '{field_name}' must be NE_YYYYMMDD_NNN or HI_YYYYMMDD_NNN")

    prefix, date_str, f_idx_str = m.groups()
    mode   = "NARROW ECLIPTIC" if prefix == "NE" else "HIGH INCLINATION"
    sector = "NIGHT"
    dt     = datetime.strptime(date_str, "%Y%m%d").date()

    Y  = to_base36(dt.year - BASE_YEAR, 1)
    DD = to_base36(int(dt.strftime("%j")), 2)
    S  = MODE_SECTOR_MAP.get((mode, sector), "X")
    F  = to_base36(int(f_idx_str), 1)
    O  = to_base36(track_no, 1)
    return f"T{Y}{DD}{S}{F}{O}"


# ─────────────────────────────────────────────
#  Find_Orb ephemeris parser
# ─────────────────────────────────────────────

def parse_findorb_ephemeris(text: str) -> list[dict]:
    """
    Parse plain-text ephemeris exported from Find_Orb.
    Supports two common formats:

    Format A (HH:MM time column — Find_Orb default for short-step ephemerides):

        Ephemerides for (O58) SSTAC, Pak Chong: T630X2B:
        Date (UTC) HH:MM    RA        Dec      delta  r  elong mag '/hr   PA   " sig PA
        2026 04 19 09:20  12 21 54.389  -12 01 01.53  .30982 1.2983 159.0 19.3  0.519  83.0  777 90
        2026 04 19 09:25  12 21 54.564  -12 01 01.21  .30986 1.2983 159.0 19.3  0.516  83.0  781 90

    Format B (fractional-day — Find_Orb default for day-step):

        Date (UTC)          RA (J2000)       Dec         ...  "/hr
        2026 04 20.875  12 34 56.7  +05 12 34  ...  48.3

    Returns list of dicts with standardised keys:
      date_utc, ra, dec, mag, motion_arcsec_min, motion_arcsec_hr,
      uncertainty_arcsec (if present in Format A's "sig" column)

    Missing/unreadable rows are silently skipped.
    """
    rows = []
    for raw in text.strip().split("\n"):
        line = raw.strip()

        # Skip obvious non-data lines
        if not line:
            continue
        if line.startswith("#"):
            continue
        low = line.lower()
        if ("ephemerides" in low
                or low.startswith("date")
                or line.startswith("-")
                or "sig" in low[:40]):
            continue

        # Must start with 4-digit year
        if not re.match(r"^\d{4}\s", line):
            continue

        parts = line.split()
        if len(parts) < 9:
            continue

        try:
            year_i  = int(parts[0])
            month_i = int(parts[1])

            # Detect format by looking at parts[2] and parts[3]
            p2 = parts[2]
            p3 = parts[3]

            if ":" in p3:
                # Format A: "YYYY MM DD HH:MM  hh mm ss.s  ±dd mm ss.s  ..."
                day_i   = int(p2)
                hh, mm  = p3.split(":")
                dt_utc  = datetime(year_i, month_i, day_i,
                                   int(hh), int(mm))
                # RA starts at parts[4], Dec at parts[7]
                ra_h, ra_m, ra_s    = parts[4], parts[5], parts[6]
                dec_d, dec_m, dec_s = parts[7], parts[8], parts[9]
                extras_start = 10
            else:
                # Format B: "YYYY MM DD.ddd  hh mm ss.s  ±dd mm ss.s  ..."
                day_f   = float(p2)
                day_i   = int(day_f)
                frac    = day_f - day_i
                dt_base = datetime(year_i, month_i, day_i)
                dt_utc  = dt_base + timedelta(days=frac)
                ra_h, ra_m, ra_s    = parts[3], parts[4], parts[5]
                dec_d, dec_m, dec_s = parts[6], parts[7], parts[8]
                extras_start = 9

            ra_str  = f"{int(ra_h):02d}:{int(ra_m):02d}:{ra_s}"
            # Dec sign (± or digit) stays as given
            dec_str = f"{dec_d}:{int(dec_m):02d}:{dec_s}"

            # Parse remaining numeric columns.
            # Standard Find_Orb column order after Dec:
            #   delta  r      elong   mag   '/hr           PA    "sig    PA_err
            #    0     1      2       3     4              5     6       7
            # '/hr  = motion in arcMIN/hr (note the apostrophe in header)
            # "sig  = 1-sigma positional uncertainty in arcSEC
            tail = parts[extras_start:]
            numeric_tail = []
            for col in tail:
                try:
                    numeric_tail.append(float(col))
                except ValueError:
                    continue

            mag              = None
            motion_arcmin_hr = None
            unc_arcsec       = None

            if len(numeric_tail) >= 7:
                # Full row: use column positions directly
                mag              = numeric_tail[3]
                motion_arcmin_hr = numeric_tail[4]
                unc_arcsec       = numeric_tail[6]
            elif len(numeric_tail) >= 5:
                # Shorter output (no uncertainty column)
                mag              = numeric_tail[3]
                motion_arcmin_hr = numeric_tail[4]
            else:
                # Last-resort fallback: grab anything that looks like magnitude
                mag_cands = [v for v in numeric_tail if 10.0 < v < 25.0]
                if mag_cands:
                    mag = mag_cands[0]

            # Convert motion units.
            # 1 arcmin/hr == 60 arcsec/hr == 1 arcsec/min
            # (because hr has 60 min, so arcmin/hr divided by 60 min = arcmin/min = 60 arcsec/min... wait)
            # Correct: 0.519 arcmin/hr = 0.519 × 60 arcsec/hr = 31.14 arcsec/hr
            # arcsec/min = arcsec/hr / 60 = 0.519 arcsec/min  (numerically same value)
            motion_hr_arcsec  = motion_arcmin_hr * 60.0 if motion_arcmin_hr is not None else None
            motion_min_arcsec = motion_arcmin_hr        if motion_arcmin_hr is not None else None

            row = {
                "date_utc":          dt_utc.strftime("%Y-%m-%d %H:%M"),
                "ra":                ra_str,
                "dec":               dec_str,
                "mag":               mag,
                "motion_arcsec_min": motion_min_arcsec,
                "motion_arcsec_hr":  motion_hr_arcsec,
            }
            if unc_arcsec is not None:
                row["uncertainty_arcsec"] = unc_arcsec
            rows.append(row)

        except (ValueError, IndexError):
            continue

    return rows


def generate_ephemeris_online(obs_data: str, obscode: str, step_minutes: int) -> list[dict]:
    """
    Connect to Find Orb online API to generate ephemeris.
    Uses HTML output ('file_no': '0') because JSON output has a server-side bug 
    where it ignores 'year=now' and defaults to the orbit epoch.
    """
    import urllib.request
    import urllib.parse
    import re

    data = {
        'TextArea': obs_data,
        'mpc_code': obscode,
        'year': 'now',
        'n_steps': '50',
        'stepsize': str(step_minutes) + 'm',
        'ephem_type': '0',
        'total_motion': 'on', # Required to get '/hr and PA
        'file_no': '0',       # Request HTML output
    }

    encoded_data = urllib.parse.urlencode(data).encode('utf-8')
    req = urllib.request.Request(
        'https://www.projectpluto.com/cgi-bin/fo/fo_serve.cgi', 
        data=encoded_data
    )

    with urllib.request.urlopen(req) as response:
        result = response.read().decode('utf-8')
        
        # Extract the <pre> block that contains the ephemeris table
        match = re.search(r'Date \(UTC\).*?\n(.*?</pre>)', result, re.DOTALL)
        if not match:
            raise ValueError("No ephemeris text block found in the HTML response.")
            
        ephem_text = match.group(1).replace('</pre>', '').replace('</body></html>', '')
        
        # Reuse our robust existing parser
        rows = parse_findorb_ephemeris(ephem_text)
        
        if not rows:
            raise ValueError("Failed to parse the ephemeris rows from the response.")
            
        return rows



def get_ephemeris_at(rows: list[dict], target_dt: datetime) -> dict | None:
    """
    Interpolate/find the ephemeris row closest to target_dt.
    Returns the closest row or None if rows is empty.
    """
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(
        (datetime.strptime(r["date_utc"], "%Y-%m-%d %H:%M") - target_dt).total_seconds()
    ))
    return best


# ─────────────────────────────────────────────
#  Priority score
# ─────────────────────────────────────────────

def _compute_priority(candidate: dict, fov_arcsec: float = FOV_ARCSEC) -> float:
    """
    Higher score = more urgent.
    Factors:
      - uncertainty growth vs FOV (most important)
      - days since discovery (staleness)
      - motion rate (NEO bonus)
      - predicted magnitude feasibility
    """
    unc       = float(candidate.get("uncertainty_arcsec", fov_arcsec))
    days_old  = float(candidate.get("days_since_discovery", 0))
    motion    = float(candidate.get("motion_arcsec_min", 0.5))
    mag       = float(candidate.get("predicted_mag") or 19.0)
    status    = candidate.get("status", STATUS_UNCONFIRMED)

    if status not in ACTIVE_STATUSES:
        return 0.0

    # Urgency: fraction of FOV used up — approaches infinity near FOV edge
    unc_frac = min(unc / fov_arcsec, 0.99)
    urgency  = unc_frac / (1.0 - unc_frac + 1e-6)

    # Staleness bonus: unconfirmed objects get more urgent each day
    staleness = 1.0 + min(days_old, 4) * 0.4

    # NEO likelihood bonus from motion rate
    neo_bonus = 1.0
    if motion > 2.0:
        neo_bonus = 2.0
    elif motion > 1.0:
        neo_bonus = 1.5

    # Magnitude penalty if too faint for O58
    mag_factor = 1.0 if mag <= 19.0 else max(0.1, 1.0 - (mag - 19.0) * 0.5)

    priority = urgency * staleness * neo_bonus * mag_factor
    return round(priority, 4)


def alert_level(candidate: dict, fov_arcsec: float = FOV_ARCSEC) -> str:
    """Return 'GREEN', 'YELLOW', or 'RED' based on uncertainty vs FOV."""
    unc  = float(candidate.get("uncertainty_arcsec", 0))
    frac = unc / fov_arcsec
    if frac < ALERT_GREEN:
        return "GREEN"
    if frac < ALERT_YELLOW:
        return "YELLOW"
    return "RED"


# ─────────────────────────────────────────────
#  Persistence
# ─────────────────────────────────────────────

def _db_path(base_dir=None) -> Path:
    return Path(base_dir or Path.cwd()) / CANDIDATE_DB


def load_registry(base_dir=None) -> dict:
    """Load all candidates from JSON. Returns dict keyed by object code."""
    p = _db_path(base_dir)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_registry(registry: dict, base_dir=None):
    """Persist registry dict to JSON."""
    p = _db_path(base_dir)
    with p.open("w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
#  Core API
# ─────────────────────────────────────────────

def register_candidate(
    object_code: str,
    discovery_ra: str,
    discovery_dec: str,
    discovery_date_local: str,
    ephemeris_rows: list = None,
    predicted_mag: float = None,
    motion_arcsec_min: float = None,
    uncertainty_arcsec: float = None,
    note: str = "",
    base_dir=None,
) -> dict:
    """
    Register or update a candidate.
    object_code must be a valid 7-char SSTAC code (e.g. 'T631X31').
    Metadata (date, mode, field) is decoded from the code automatically.
    Returns the stored candidate dict.
    """
    meta = decode_object_code(object_code)

    registry = load_registry(base_dir)

    # Preserve existing data if updating
    existing = registry.get(object_code, {})

    candidate = {
        # Identity — decoded from code, never stored redundantly
        "object_code":        object_code,
        "date_local":         meta["date_str"],
        "mode":               meta["mode"],
        "sector":             meta["sector"],
        "field_index":        meta["field_index"],
        "track_no":           meta["track_no"],

        # Discovery astrometry
        "discovery_ra":       discovery_ra,
        "discovery_dec":      discovery_dec,
        "discovery_date_local": discovery_date_local,

        # Find_Orb data (may be updated later)
        "ephemeris":          ephemeris_rows or existing.get("ephemeris", []),
        "predicted_mag":      predicted_mag  or existing.get("predicted_mag"),
        "motion_arcsec_min":  motion_arcsec_min or existing.get("motion_arcsec_min", 0.5),
        "uncertainty_arcsec": uncertainty_arcsec or existing.get("uncertainty_arcsec", 60.0),

        # Lifecycle
        "status":             existing.get("status", STATUS_UNCONFIRMED),
        "registered_at":      existing.get("registered_at",
                                           datetime.now().isoformat(timespec="seconds")),
        "updated_at":         datetime.now().isoformat(timespec="seconds"),
        "days_since_discovery": _days_since(discovery_date_local),
        "confirmed_at":       existing.get("confirmed_at"),
        "mpc_desig":          existing.get("mpc_desig"),   # provisional designation if assigned
        "note":               note or existing.get("note", ""),
    }

    # Compute derived fields
    candidate["priority"]    = _compute_priority(candidate)
    candidate["alert_level"] = alert_level(candidate)

    registry[object_code] = candidate
    save_registry(registry, base_dir)
    return candidate


def update_observation(object_code: str, obs_text: str, base_dir=None) -> dict:
    """
    Attach raw observation data to the candidate.
    """
    registry = load_registry(base_dir)
    if object_code not in registry:
        raise KeyError(f"Candidate '{object_code}' not in registry. Register first.")

    candidate = registry[object_code]
    candidate["observation_data"] = obs_text.strip()
    candidate["updated_at"] = datetime.now().isoformat(timespec="seconds")
    
    registry[object_code] = candidate
    save_registry(registry, base_dir)
    return candidate


def update_ephemeris(object_code: str, ephemeris_text: str,
                     base_dir=None) -> dict:
    """
    Parse Find_Orb text and attach to existing candidate.
    Updates uncertainty and motion from the ephemeris data.
    """
    registry = load_registry(base_dir)
    if object_code not in registry:
        raise KeyError(f"Candidate '{object_code}' not in registry. Register first.")

    rows = parse_findorb_ephemeris(ephemeris_text)
    if not rows:
        raise ValueError("No valid ephemeris rows parsed from input text.")

    candidate = registry[object_code]
    candidate["ephemeris"] = rows

    # Pull motion from first row if not already set manually
    first = rows[0]
    if first.get("motion_arcsec_min") is not None:
        candidate["motion_arcsec_min"] = first["motion_arcsec_min"]
    if first.get("mag") is not None and candidate.get("predicted_mag") is None:
        candidate["predicted_mag"] = first["mag"]

    candidate["updated_at"] = datetime.now().isoformat(timespec="seconds")
    candidate["priority"]   = _compute_priority(candidate)
    candidate["alert_level"] = alert_level(candidate)

    registry[object_code] = candidate
    save_registry(registry, base_dir)
    return candidate


def update_ephemeris_online(object_code: str, obs_data: str, obscode: str, step_minutes: int, base_dir=None) -> dict:
    """
    Generate Ephemeris online via Find Orb API and attach to existing candidate.
    """
    registry = load_registry(base_dir)
    if object_code not in registry:
        raise KeyError(f"Candidate '{object_code}' not in registry. Register first.")

    rows = generate_ephemeris_online(obs_data, obscode, step_minutes)
    if not rows:
        raise ValueError("No valid ephemeris rows generated from Find Orb API.")

    candidate = registry[object_code]
    candidate["ephemeris"] = rows

    # Pull motion from first row if not already set manually
    first = rows[0]
    if first.get("motion_arcsec_min") is not None:
        candidate["motion_arcsec_min"] = first["motion_arcsec_min"]
    if first.get("mag") is not None and candidate.get("predicted_mag") is None:
        candidate["predicted_mag"] = first["mag"]

    candidate["updated_at"] = datetime.now().isoformat(timespec="seconds")
    candidate["priority"]   = _compute_priority(candidate)
    candidate["alert_level"] = alert_level(candidate)

    registry[object_code] = candidate
    save_registry(registry, base_dir)
    return candidate


def update_status(object_code: str, status: str,
                  mpc_desig: str = None, base_dir=None) -> dict:
    """Update lifecycle status. Optionally record MPC provisional designation."""
    valid = {STATUS_UNCONFIRMED, STATUS_CONFIRMED,
             STATUS_SUBMITTED, STATUS_LOST, STATUS_REJECTED}
    if status not in valid:
        raise ValueError(f"Status must be one of {valid}")

    registry = load_registry(base_dir)
    if object_code not in registry:
        raise KeyError(f"'{object_code}' not found in registry.")

    candidate = registry[object_code]
    candidate["status"]     = status
    candidate["updated_at"] = datetime.now().isoformat(timespec="seconds")

    if status == STATUS_CONFIRMED:
        candidate["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
    if mpc_desig:
        candidate["mpc_desig"] = mpc_desig

    candidate["days_since_discovery"] = _days_since(candidate["discovery_date_local"])
    candidate["priority"]   = _compute_priority(candidate)
    candidate["alert_level"] = alert_level(candidate)

    registry[object_code] = candidate
    save_registry(registry, base_dir)
    return candidate


def update_uncertainty(object_code: str, uncertainty_arcsec: float,
                       base_dir=None) -> dict:
    """Update current positional uncertainty (call each night)."""
    registry = load_registry(base_dir)
    if object_code not in registry:
        raise KeyError(f"'{object_code}' not found.")

    candidate = registry[object_code]
    candidate["uncertainty_arcsec"] = uncertainty_arcsec
    candidate["days_since_discovery"] = _days_since(candidate["discovery_date_local"])
    candidate["priority"]   = _compute_priority(candidate)
    candidate["alert_level"] = alert_level(candidate)
    candidate["updated_at"] = datetime.now().isoformat(timespec="seconds")

    # Auto-mark as LOST if uncertainty exceeds FOV and still unconfirmed
    if (uncertainty_arcsec > FOV_ARCSEC
            and candidate["status"] == STATUS_UNCONFIRMED):
        candidate["status"] = STATUS_LOST

    registry[object_code] = candidate
    save_registry(registry, base_dir)
    return candidate


def delete_rejected_candidates(base_dir=None) -> int:
    """Delete all candidates with status 'REJECTED' from the registry."""
    registry = load_registry(base_dir)
    to_delete = [code for code, cand in registry.items() if cand.get("status") == STATUS_REJECTED]
    for code in to_delete:
        del registry[code]
    if to_delete:
        save_registry(registry, base_dir)
    return len(to_delete)



# ─────────────────────────────────────────────
#  Planner integration
# ─────────────────────────────────────────────

def get_tonight_followups(
    date_str_local: str,
    utc_offset: float,
    location,                    # astropy EarthLocation
    min_alt_deg: float = 25.0,
    window_start_utc=None,       # astropy Time
    window_end_utc=None,         # astropy Time
    base_dir=None,
) -> list[dict]:
    """
    Return active candidates observable tonight, sorted by priority (highest first).
    Each returned dict includes a 'followup_coord' (SkyCoord) for planner use.
    """
    from astropy.coordinates import AltAz
    from astropy.time import Time as ATime

    registry = load_registry(base_dir)
    tonight  = datetime.fromisoformat(date_str_local)
    results  = []

    for code, cand in registry.items():
        if cand.get("status") not in ACTIVE_STATUSES:
            continue

        # Refresh staleness
        cand["days_since_discovery"] = _days_since(cand["discovery_date_local"])
        cand["priority"] = _compute_priority(cand)
        cand["alert_level"] = alert_level(cand)

        # Find best ephemeris row for tonight
        eph_rows = cand.get("ephemeris", [])
        target_dt = tonight.replace(hour=21, minute=0)  # default 21:00 local
        best_row  = get_ephemeris_at(eph_rows, target_dt)

        if best_row is None:
            # No ephemeris yet — use discovery position as fallback
            ra_str  = cand.get("discovery_ra", "")
            dec_str = cand.get("discovery_dec", "")
        else:
            ra_str  = best_row["ra"]
            dec_str = best_row["dec"]
            if best_row.get("mag"):
                cand["predicted_mag"] = best_row["mag"]

        if not ra_str or not dec_str:
            continue

        try:
            coord = SkyCoord(ra_str, dec_str, unit=("hourangle", "deg"))
        except Exception:
            continue

        # Altitude check at mid-window
        observable = True
        if window_start_utc is not None and window_end_utc is not None and location is not None:
            try:
                t_mid = window_start_utc + (window_end_utc - window_start_utc) * 0.5
                altaz = coord.transform_to(AltAz(obstime=t_mid, location=location))
                if altaz.alt.to_value(u.deg) < min_alt_deg:
                    observable = False
            except Exception:
                pass

        if not observable:
            continue

        cand["followup_coord"] = coord
        cand["followup_ra"]    = ra_str
        cand["followup_dec"]   = dec_str
        results.append(cand)

    results.sort(key=lambda c: c["priority"], reverse=True)
    return results


# ─────────────────────────────────────────────
#  Convenience read helpers
# ─────────────────────────────────────────────

def list_active(base_dir=None) -> list[dict]:
    """Return all active candidates sorted by priority."""
    registry = load_registry(base_dir)
    active = []
    for cand in registry.values():
        if cand.get("status") in ACTIVE_STATUSES:
            cand["days_since_discovery"] = _days_since(cand["discovery_date_local"])
            cand["priority"]    = _compute_priority(cand)
            cand["alert_level"] = alert_level(cand)
            active.append(cand)
    active.sort(key=lambda c: c["priority"], reverse=True)
    return active


def list_all(base_dir=None) -> list[dict]:
    """Return all candidates regardless of status."""
    registry = load_registry(base_dir)
    all_cands = list(registry.values())
    for cand in all_cands:
        cand["days_since_discovery"] = _days_since(cand["discovery_date_local"])
        cand["priority"]    = _compute_priority(cand)
        cand["alert_level"] = alert_level(cand)
    return sorted(all_cands,
                  key=lambda c: c.get("registered_at", ""),
                  reverse=True)


def get_candidate(object_code: str, base_dir=None) -> dict | None:
    """Get single candidate by code. Returns None if not found."""
    return load_registry(base_dir).get(object_code.strip().upper())


# ─────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────

def _days_since(date_str: str) -> float:
    try:
        d = datetime.fromisoformat(date_str).date()
        return (date.today() - d).days
    except Exception:
        return 0.0
