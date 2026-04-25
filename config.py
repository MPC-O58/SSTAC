
import os
import json

APP_TITLE = "SSTAC SURVEY v1.3"
APP_SUBTITLE = "V1.3"
APP_VERSION = "SSTAC v1.3"
CONFIG_FILE = "sstac_config.json"

DEFAULT_CONFIG = {
    "default_location": "MPC-O58",
    "locations": {
        "MPC-O58": {"lat": 14.6983, "lon": 101.4541, "alt": 317.0, "utc_offset": 7.0, "fov": 34.9}
    },
    "survey_defaults": {
        "max_fields": 6,
        "min_moon_sep": 30.0,
        "min_alt": 25.0,
        "avoid_gal": True,
        "gal_b_min": 12.0,
        "use_overlap": True,
        "overlap_pct": 10,
        "use_neocp": True,
        "avoid_mba": False,
    }
}

BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MODE_SECTOR_MAP = {
    ("NEO", "EVE"): "E", ("NEO", "MORN"): "M", ("NEO", "NIGHT"): "N", ("NEO", ""): "N",
    ("MBA", ""): "A", ("MBA", "NIGHT"): "A",
    ("COMET-TWILIGHT", "EVE"): "V", ("COMET-TWILIGHT", "MORN"): "W", ("COMET-TWILIGHT", ""): "C",
    ("HIGH INCLINATION", "NIGHT"): "I", ("HIGH INCLINATION", ""): "I",
    ("NARROW ECLIPTIC", "NIGHT"): "X", ("NARROW ECLIPTIC", ""): "X",
}
INV_MODE_MAP = {v: k for k, v in MODE_SECTOR_MAP.items()}

FOV_ARCMIN_DEFAULT = 34.9
LAMBDA_STEP_DEG = 0.6
ECL_MBA_BAND = 8.0
ECL_NEO_BAND = 15.0
BETA_MBA_STEP = 1.0
BETA_NEO_STEP = 2.0
MBA_NIGHT_START, MBA_NIGHT_END = "19:00", "04:00"
MBA_STEP_MIN = 10
NEOCP_URL = "https://www.projectpluto.com/neocp2/summary.htm"
NEOCP_USER_AGENT = "Mozilla/5.0 (NightlySurveyPlanner; +https://minorplanetcenter.net/)"
HEATMAP_BINS_X, HEATMAP_BINS_Y = 240, 120
DUPLICATE_RADIUS_DEG = 0.4

# Discovery engine controls
DRIFT_REFERENCE_DATE = "2026-01-01"
DRIFT_RATE_NE_DEG = 1.2
DRIFT_RATE_HI_DEG = 0.8
LOOKBACK_NIGHTS = 5
HISTORY_SCALE_DEG = 2.0
NOVELTY_RADIUS_DEG = 3.0
MEMORY_PENALTY_WEIGHT = 8.0
NOVELTY_BONUS_WEIGHT = 4.0
DIVERSITY_BONUS_WEIGHT = 2.5
CLUSTERING_PENALTY_WEIGHT = 5.0
CORE_FRACTION = 0.80
STRIP_BETA_TOL_NE = 1.6
STRIP_BETA_TOL_HI = 3.0

HISTORY_DB_DIR = "sstac_history_db"
HISTORY_MASTER_CSV = "master_history.csv"
PERFORMANCE_LOG_CSV = "performance_log.csv"
SKY_QUALITY_LOG_CSV = "SSTAC_Observation_Log.csv"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for key, val in DEFAULT_CONFIG.items():
                if key not in cfg:
                    cfg[key] = val
            for loc_name, loc_data in cfg.get("locations", {}).items():
                if "utc_offset" not in loc_data:
                    loc_data["utc_offset"] = 7.0
                if "fov" not in loc_data:
                    loc_data["fov"] = FOV_ARCMIN_DEFAULT
            return cfg
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
