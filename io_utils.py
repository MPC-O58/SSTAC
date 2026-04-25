import csv
import re
from pathlib import Path
from urllib.request import Request, urlopen
from config import NEOCP_URL, NEOCP_USER_AGENT, APP_VERSION
from astro_utils import utc_to_local_dt, format_ra_dec
from astropy.coordinates import SkyCoord


def fetch_neocp_text_project_pluto(url=NEOCP_URL, timeout_sec=20):
    try:
        req = Request(url, headers={"User-Agent": NEOCP_USER_AGENT})
        with urlopen(req, timeout=timeout_sec) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
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
            objs.append({"desig": m.group("desig").strip(), "coord": SkyCoord(ra, dec, unit=("hourangle", "deg")), "score": None})
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
    from datetime import datetime
    return objs, f"NEOCP loaded: N={len(objs)} | LIVE | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", False


def load_prev_coords(date_str_local, mode):
    from datetime import datetime, timedelta
    d = datetime.fromisoformat(date_str_local).date() - timedelta(days=1)
    fname = Path(f"nightly_targets_{mode.upper().replace(' ', '_')}_{d.isoformat()}.csv")
    if not fname.exists():
        return None
    coords = []
    with fname.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("RA") and row.get("Dec"):
                try:
                    coords.append(SkyCoord(row.get("RA"), row.get("Dec"), unit=("hourangle", "deg")))
                except Exception:
                    pass
    return SkyCoord(coords) if coords else None


def export_nina_csv(out_path, mode, date_str_local, utc_offset, selected, utc_to_local_dt_func=None, format_ra_dec_func=None):
    utc_to_local_dt_func = utc_to_local_dt_func or utc_to_local_dt
    format_ra_dec_func = format_ra_dec_func or format_ra_dec
    with Path(out_path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
        w.writerow([
            "Name", "Role", "RA", "Dec", "Sector", "WindowStartLocal", "WindowEndLocal",
            "BestTimeLocal", "BestAltDeg", "MoonSepDeg", "PhaseAngleDeg",
            "Gal_b_Deg", "Duration_Hr", "Score", "Version"
        ])
        for i, item in enumerate(selected, start=1):
            ra, dec = format_ra_dec_func(item["coord"])
            sec = item.get("sector", "")
            name = item.get("target_id", f"FIELD_{i:03d}")
            w_start = utc_to_local_dt_func(item["window_start"], utc_offset).strftime("%Y-%m-%d %H:%M")
            w_end = utc_to_local_dt_func(item["window_end"], utc_offset).strftime("%Y-%m-%d %H:%M")
            b_time = utc_to_local_dt_func(item["best_time"], utc_offset).strftime("%Y-%m-%d %H:%M")
            w.writerow([
                name, item.get("role", "DISCOVERY"), ra, dec, sec, w_start, w_end, b_time,
                f"{item['best_alt']:.1f}", f"{item['moon_sep']:.1f}", f"{item.get('phase_best', 0.0):.1f}",
                f"{item['gal_b_deg']:.1f}", f"{item.get('duration', 0.0):.1f}", f"{item.get('score', 0.0):.2f}", APP_VERSION
            ])
