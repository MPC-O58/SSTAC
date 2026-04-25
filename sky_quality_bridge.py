
import csv
import subprocess
import sys
from pathlib import Path
from config import SKY_QUALITY_LOG_CSV

def launch_sky_quality(script_path=None):
    candidates = []
    if script_path:
        candidates.append(Path(script_path))
    candidates.append(Path("SkyQuality_beta.py"))
    candidates.append(Path(__file__).resolve().parent / "SkyQuality_beta.py")

    script = next((p for p in candidates if p.exists()), None)
    if script is None:
        raise FileNotFoundError("SkyQuality script not found")
    subprocess.Popen([sys.executable, str(script)])
    return str(script)

def import_latest_sky_quality(log_path=None):
    candidates = []
    if log_path:
        candidates.append(Path(log_path))
    candidates.append(Path(SKY_QUALITY_LOG_CSV))
    candidates.append(Path(__file__).resolve().parent / SKY_QUALITY_LOG_CSV)

    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError("Sky quality log not found")
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("Sky quality log is empty")
    row = rows[-1]
    return {
        "sky_mag_arcsec2": row.get("Sky_mag_arcsec2",""),
        "seeing_fwhm_arcsec": row.get("Seeing_FWHM_arcsec",""),
        "limit_mag_single": row.get("Est_Limit_Mag_1Fr",""),
        "limit_mag_stack": row.get("Est_Limit_Mag_Stack",""),
    }
