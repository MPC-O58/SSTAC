"""
mpc_coverage_export.py  —  SSTAC v1.2+  (Fixed: date filtering)
=================================================================
Generates and (optionally) submits sky-coverage reports to the
Minor Planet Center (MPC) from SSTAC's performance_log.csv.

Two submission paths supported by MPC
──────────────────────────────────────
1. Legacy e-mail format  (skycov@cfa.harvard.edu)
   • Plain-text, subject line must contain 'Sky Coverage'
   • Header block  →  7 mandatory lines
   • Data lines    →  one field-center per line, comma-separated

2. Modern JSON Pointings API  (https://www.minorplanetcenter.net/pointings/)
   • One JSON object per exposure (ISO-8601 timestamp, center, FOV, filter)

FIXES v1.1
──────────
- Removed silent fallback-to-all when date filter returns empty  ← main bug
- Robust _normalize_date_str() handles pandas Timestamps / trailing spaces
- Returns diagnostics dict so GUI shows which dates are available
- Better RA/Dec validation with range checking
"""

from __future__ import annotations

import json
import smtplib
import argparse
import datetime as _dt
from datetime import datetime, timezone, date as _date
from email.mime.text import MIMEText
from pathlib import Path

MPC_SKYCOV_EMAIL  = "skycov@cfa.harvard.edu"
MPC_POINTINGS_URL = "https://www.minorplanetcenter.net/pointings/"
MPC_EMAIL_SUBJECT = "Sky Coverage"


# ──────────────────────────────────────────────────────────────────────────────
#  Date normalization helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_date_str(raw) -> str:
    """Normalize any date-like value → 'YYYY-MM-DD' string.

    Handles:
      - plain string '2026-04-19'
      - string with time  '2026-04-19 21:10'
      - pandas Timestamp  (has .date() method)
      - datetime / date objects
      - strings with extra whitespace or 'nan'
    """
    if raw is None:
        return ""
    # pandas Timestamp or datetime object with .date()
    if hasattr(raw, "date") and callable(raw.date):
        try:
            return raw.date().isoformat()
        except Exception:
            pass
    # standard date/datetime
    if isinstance(raw, (_date, datetime)):
        return raw.strftime("%Y-%m-%d")
    # string
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "nat", "none", ""):
        return ""
    return s[:10]           # take first 10 chars → 'YYYY-MM-DD'


def _to_mpc_date(date_local_str: str) -> str:
    """'YYYY-MM-DD' → 'YYYYDDD'  (MPC legacy date format).
    Example: '2026-04-19' → '2026109'
    """
    d   = datetime.strptime(date_local_str[:10], "%Y-%m-%d")
    doy = d.timetuple().tm_yday
    return f"{d.year}{doy:03d}"


# ──────────────────────────────────────────────────────────────────────────────
#  Coordinate helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ra_hms_to_deg(ra_hms: str) -> float:
    """'HH:MM:SS.ss' → decimal degrees (0 – 360)."""
    parts = str(ra_hms).strip().split(":")
    if len(parts) < 3:
        raise ValueError(f"Cannot parse RA: '{ra_hms}'")
    h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
    deg = 15.0 * (h + m / 60.0 + s / 3600.0)
    if not (0.0 <= deg < 360.0):
        raise ValueError(f"RA out of range: {deg:.3f}° from '{ra_hms}'")
    return deg


def _dec_dms_to_deg(dec_dms: str) -> float:
    """'+DD:MM:SS.s' → decimal degrees (-90 – +90)."""
    dec_dms = str(dec_dms).strip()
    sign    = -1.0 if dec_dms.startswith("-") else 1.0
    parts   = dec_dms.lstrip("+-").split(":")
    if len(parts) < 3:
        raise ValueError(f"Cannot parse Dec: '{dec_dms}'")
    d, m, s = float(parts[0]), float(parts[1]), float(parts[2])
    deg = sign * (d + m / 60.0 + s / 3600.0)
    if not (-90.0 <= deg <= 90.0):
        raise ValueError(f"Dec out of range: {deg:.3f}° from '{dec_dms}'")
    return deg


def _safe_float(val, default: float = 0.0) -> float:
    try:
        v = float(val)
        return v if v == v else default     # NaN → default
    except (TypeError, ValueError):
        return default


def _fov_deg(fov_arcmin: float) -> float:
    return fov_arcmin / 60.0


# ──────────────────────────────────────────────────────────────────────────────
#  Night-row filter  ← KEY FIX: no silent fallback
# ──────────────────────────────────────────────────────────────────────────────

def filter_rows_by_date(
    observed_rows:  list[dict],
    date_local_str: str,
) -> tuple[list[dict], dict]:
    """Return only rows whose date_local matches date_local_str.

    Returns
    -------
    (matched_rows, diagnostics_dict)

    diagnostics keys:
        target_date      : normalized target '2026-04-19'
        total_rows       : total observed rows across all dates
        matched          : count matched for this night
        available_dates  : sorted list of unique dates found in log
        skipped_bad_date : rows with unparseable date_local
    """
    target      = _normalize_date_str(date_local_str)
    if not target:
        raise ValueError(f"Invalid target date: '{date_local_str}'")

    matched     = []
    available   = set()
    skipped_bad = 0

    for row in observed_rows:
        norm = _normalize_date_str(row.get("date_local", ""))
        if not norm:
            skipped_bad += 1
            continue
        available.add(norm)
        if norm == target:
            matched.append(row)

    diag = {
        "target_date":     target,
        "total_rows":      len(observed_rows),
        "matched":         len(matched),
        "available_dates": sorted(available),
        "skipped_bad_date": skipped_bad,
    }
    return matched, diag


# ──────────────────────────────────────────────────────────────────────────────
#  Format 1 — Legacy plain-text (email format)
# ──────────────────────────────────────────────────────────────────────────────

def build_legacy_coverage_text(
    rows:            list[dict],
    obs_code:        str,
    date_local_str:  str,
    fov_arcmin:      float = 34.9,
    default_lim_mag: float = 19.5,
) -> str:
    """Build MPC legacy sky-coverage text from already-filtered night rows."""
    fov_d    = _fov_deg(fov_arcmin)
    mpc_date = _to_mpc_date(date_local_str)

    lines = [
        "NEO SEARCH FIELD CENTERS",
        f"SOURCE: {obs_code}",
        f"DATE: {mpc_date}",
        f"FIELD SIZE RA: {fov_d:.4f}",
        f"FIELD SIZE DEC: {fov_d:.4f}",
        f"LIMITING MAGNITUDE: {default_lim_mag:.1f}",
        f"FILENAME: {obs_code}_{mpc_date}",
    ]

    skipped = 0
    for row in rows:
        try:
            ra_deg  = _ra_hms_to_deg(str(row["ra"]))
            dec_deg = _dec_dms_to_deg(str(row["dec"]))
        except Exception:
            skipped += 1
            continue

        # Prefer stacked limit mag (better represents survey depth for MPC)
        # Fall back: stack → single → header default
        lim_mag = _safe_float(row.get("limit_mag_stack", ""), 0.0)
        if lim_mag <= 0:
            lim_mag = _safe_float(row.get("limit_mag_single", ""), 0.0)
        if lim_mag <= 0:
            lim_mag = default_lim_mag

        lines.append(
            f"{ra_deg:.6f}, {dec_deg:.6f}, {fov_d:.4f}, {fov_d:.4f}, {lim_mag:.1f}"
        )

    if skipped:
        lines.append(f"# {skipped} row(s) skipped (bad RA/Dec format)")

    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────────────────────
#  Format 2 — Modern JSON Pointings API
# ──────────────────────────────────────────────────────────────────────────────

def _field_corner_offsets(fov_deg: float) -> list[list[float]]:
    h = fov_deg / 2.0
    return [[-h, h], [h, h], [h, -h], [-h, -h]]


def build_json_pointings(
    rows:           list[dict],
    obs_code:       str,
    date_local_str: str,
    utc_offset:     float = 7.0,
    fov_arcmin:     float = 34.9,
    filter_name:    str   = "UNFILTERED",
    exposure_sec:   float = 60.0,
    mode:           str   = "survey",
) -> list[dict]:
    """Build MPC JSON Pointings API objects from already-filtered night rows."""
    fov_d   = _fov_deg(fov_arcmin)
    offsets = _field_corner_offsets(fov_d)
    records = []

    for i, row in enumerate(rows):
        try:
            ra_deg  = _ra_hms_to_deg(str(row["ra"]))
            dec_deg = _dec_dms_to_deg(str(row["dec"]))
        except Exception:
            continue

        time_str = str(
            row.get("best_time_local") or row.get("window_start_local", "")
        ).strip()
        if not time_str or time_str.lower() in ("nan", "none", ""):
            continue

        try:
            local_dt = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    local_dt = datetime.strptime(time_str[:19], fmt)
                    break
                except ValueError:
                    continue
            if local_dt is None:
                continue
            utc_dt = local_dt.replace(tzinfo=timezone.utc) - \
                     _dt.timedelta(hours=utc_offset)
            iso_ts = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.0000")
        except Exception:
            continue

        # Prefer stacked limit mag (deeper, better for MPC survey reporting)
        lim_mag = _safe_float(row.get("limit_mag_stack", ""), 0.0)
        if lim_mag <= 0:
            lim_mag = _safe_float(row.get("limit_mag_single", ""), 0.0)
        exp_name = str(row.get("target_id", f"{obs_code}_{i+1:04d}"))

        record = {
            "action":        "exposed",
            "surveyExpName": exp_name,
            "mode":          mode,
            "mpcCode":       obs_code,
            "time":          iso_ts,
            "duration":      int(exposure_sec),
            "center":        [round(ra_deg, 6), round(dec_deg, 6)],
            "width":         round(fov_d, 4),
            "offsets":       offsets,
            "filter":        filter_name,
        }
        if lim_mag > 0:
            record["limit"] = round(lim_mag, 1)

        records.append(record)

    return records


# ──────────────────────────────────────────────────────────────────────────────
#  Submission helpers
# ──────────────────────────────────────────────────────────────────────────────

def submit_via_email(
    coverage_text: str,
    sender_email:  str,
    smtp_host:     str  = "smtp.gmail.com",
    smtp_port:     int  = 587,
    smtp_user:     str  = "",
    smtp_pass:     str  = "",
    dry_run:       bool = True,
) -> tuple[bool, str]:
    """Send legacy coverage text to MPC via SMTP."""
    msg            = MIMEText(coverage_text, "plain", "utf-8")
    msg["Subject"] = MPC_EMAIL_SUBJECT
    msg["From"]    = sender_email
    msg["To"]      = MPC_SKYCOV_EMAIL

    if dry_run:
        return True, (
            f"[DRY RUN] Would send to: {MPC_SKYCOV_EMAIL}\n"
            f"Subject : {msg['Subject']}\n"
            f"From    : {sender_email}\n"
            f"{'─'*50}\n"
            f"{coverage_text[:1500]}{'...' if len(coverage_text) > 1500 else ''}"
        )
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(sender_email, [MPC_SKYCOV_EMAIL], msg.as_string())
        return True, f"Coverage submitted to {MPC_SKYCOV_EMAIL}"
    except Exception as exc:
        return False, f"SMTP error: {exc}"


def submit_json_via_requests(
    records: list[dict],
    dry_run: bool = True,
) -> tuple[bool, str]:
    """POST JSON pointings to MPC Pointings API."""
    if dry_run:
        return True, (
            f"[DRY RUN] Would POST {len(records)} pointings to\n"
            f"{MPC_POINTINGS_URL}\n\n"
            f"First 2 records:\n{json.dumps(records[:2], indent=2)}"
        )
    try:
        import requests
        resp = requests.post(
            MPC_POINTINGS_URL,
            json=records,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code in (200, 201, 202):
            return True, f"Posted {len(records)} pointings. {resp.text[:200]}"
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except ImportError:
        return False, "Package 'requests' not installed. Run: pip install requests"
    except Exception as exc:
        return False, f"Request error: {exc}"


# ──────────────────────────────────────────────────────────────────────────────
#  Master export function  (called from gui.py)
# ──────────────────────────────────────────────────────────────────────────────

def export_mpc_coverage(
    observed_rows:   list[dict],
    obs_code:        str,
    date_local_str:  str,
    output_dir:      "Path | str" = Path.cwd(),
    utc_offset:      float        = 7.0,
    fov_arcmin:      float        = 34.9,
    filter_name:     str          = "UNFILTERED",
    exposure_sec:    float        = 60.0,
    default_lim_mag: float        = 19.5,
) -> dict:
    """
    Filter performance log to ONE night, then generate both MPC formats.

    ⚠️  No silent fallback — raises ValueError with helpful message
        if no rows match the requested date.

    Returns
    -------
    dict:
        legacy_file   : Path  (.txt)
        json_file     : Path  (.json)
        n_fields      : int
        legacy_text   : str
        json_records  : list
        diagnostics   : dict  (matching info for GUI display)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Filter to the requested night ONLY ───────────────────────────────────
    night_rows, diag = filter_rows_by_date(observed_rows, date_local_str)

    if not night_rows:
        available = diag.get("available_dates", [])
        avail_str = "\n  ".join(available) if available else "(none)"
        raise ValueError(
            f"No OBSERVED fields found for '{diag['target_date']}'.\n\n"
            f"Dates with OBSERVED data in performance log:\n  {avail_str}\n\n"
            f"Total observed rows in log: {diag['total_rows']}\n\n"
            f"Tip: Use '📋 Input Field Performance' and set status=OBSERVED\n"
            f"for each field on this date before exporting."
        )

    # ── Build both formats ───────────────────────────────────────────────────
    legacy_text = build_legacy_coverage_text(
        night_rows, obs_code, date_local_str, fov_arcmin, default_lim_mag)

    json_records = build_json_pointings(
        night_rows, obs_code, date_local_str,
        utc_offset, fov_arcmin, filter_name, exposure_sec)

    # ── Save files ───────────────────────────────────────────────────────────
    mpc_date    = _to_mpc_date(date_local_str)
    legacy_file = output_dir / f"mpc_coverage_{obs_code}_{mpc_date}.txt"
    json_file   = output_dir / f"mpc_pointings_{obs_code}_{mpc_date}.json"

    legacy_file.write_text(legacy_text, encoding="utf-8")
    json_file.write_text(
        json.dumps(json_records, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "legacy_file":  legacy_file,
        "json_file":    json_file,
        "n_fields":     len(night_rows),
        "legacy_text":  legacy_text,
        "json_records": json_records,
        "diagnostics":  diag,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    parser = argparse.ArgumentParser(
        description="Export SSTAC performance log → MPC sky-coverage files")
    parser.add_argument("--date",        required=True)
    parser.add_argument("--code",        default="O58")
    parser.add_argument("--outdir",      default=".")
    parser.add_argument("--fov",         type=float, default=34.9)
    parser.add_argument("--utc",         type=float, default=7.0)
    parser.add_argument("--limmag",      type=float, default=19.5)
    parser.add_argument("--filter",      default="UNFILTERED")
    parser.add_argument("--exptime",     type=float, default=60.0)
    parser.add_argument("--email-submit", action="store_true")
    parser.add_argument("--json-submit",  action="store_true")
    parser.add_argument("--live",         action="store_true")
    parser.add_argument("--smtp-user",    default="")
    parser.add_argument("--smtp-pass",    default="")
    parser.add_argument("--from-email",   default="")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from history_utils import load_observed_history_rows
        rows = load_observed_history_rows(Path.cwd())
    except ImportError:
        print("WARNING: history_utils not found — empty rows")
        rows = []

    try:
        result = export_mpc_coverage(
            observed_rows   = rows,
            obs_code        = args.code,
            date_local_str  = args.date,
            output_dir      = Path(args.outdir),
            utc_offset      = args.utc,
            fov_arcmin      = args.fov,
            filter_name     = args.filter,
            exposure_sec    = args.exptime,
            default_lim_mag = args.limmag,
        )
        d = result["diagnostics"]
        print(f"\n✅  {result['n_fields']} fields exported for {d['target_date']}")
        print(f"   Dates in log: {', '.join(d['available_dates'])}")
        print(f"   Legacy TXT : {result['legacy_file']}")
        print(f"   JSON file  : {result['json_file']}")
    except ValueError as e:
        print(f"\n❌  {e}")
        sys.exit(1)

    dry = not args.live
    if args.email_submit:
        ok, msg = submit_via_email(
            result["legacy_text"], args.from_email,
            smtp_user=args.smtp_user, smtp_pass=args.smtp_pass, dry_run=dry)
        print(f"\n{'✅' if ok else '❌'}  {msg}")
    if args.json_submit:
        ok, msg = submit_json_via_requests(result["json_records"], dry_run=dry)
        print(f"\n{'✅' if ok else '❌'}  {msg}")
