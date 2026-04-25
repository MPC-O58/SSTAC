import math
from datetime import datetime, timedelta
import numpy as np
import astropy.units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord, AltAz, GeocentricTrueEcliptic, get_sun, get_body


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

def lambda_range_wrap(lmin, lmax, step):
    if lmin <= lmax:
        return np.arange(lmin, lmax + 1e-9, step)
    return np.concatenate([np.arange(lmin, 360.0, step), np.arange(0.0, lmax + 1e-9, step)])


def _mean_obliquity_deg(obstime):
    """Mean obliquity of the ecliptic at obstime (IAU formula, degrees)."""
    T = (float(obstime.jd) - 2451545.0) / 36525.0   # Julian centuries from J2000
    return 23.439291111 - 0.013004167 * T - 1.638889e-7 * T * T


def ecliptic_to_icrs(lam_deg, bet_deg, obstime):
    """
    Convert ecliptic longitude/latitude to ICRS SkyCoord.

    Uses direct spherical-trig conversion with the mean obliquity at obstime,
    bypassing astropy's GeocentricTrueEcliptic frame which can produce
    incorrect results for high ecliptic latitudes (|β| > 10°) in some
    astropy versions.

    Accuracy: ~ few arcseconds vs 'true' ecliptic (nutation neglected).
    Suitable for survey field planning at 35-arcmin FOV scale.
    """
    eps = math.radians(_mean_obliquity_deg(obstime))
    lam = math.radians(float(lam_deg))
    bet = math.radians(float(bet_deg))

    # Standard ecliptic → equatorial conversion
    sin_dec = (math.sin(bet) * math.cos(eps)
               + math.cos(bet) * math.sin(eps) * math.sin(lam))
    dec_rad = math.asin(max(-1.0, min(1.0, sin_dec)))

    cos_dec = math.cos(dec_rad)
    if abs(cos_dec) < 1e-10:
        ra_rad = 0.0
    else:
        sin_ra_cd = (math.cos(bet) * math.sin(lam) * math.cos(eps)
                     - math.sin(bet) * math.sin(eps))
        cos_ra_cd = math.cos(bet) * math.cos(lam)
        ra_rad = math.atan2(sin_ra_cd, cos_ra_cd) % (2.0 * math.pi)

    return SkyCoord(ra=math.degrees(ra_rad) * u.deg,
                    dec=math.degrees(dec_rad) * u.deg,
                    frame="icrs")


def ecliptic_icrs_sample(lam_deg, bet_deg, obstime):
    """Return (ra_deg, dec_deg) for a sample field — used for diagnostics."""
    c = ecliptic_to_icrs(lam_deg, bet_deg, obstime)
    return float(c.ra.deg), float(c.dec.deg)


def format_ra_dec(coord_eq):
    return (
        coord_eq.ra.to_string(unit=u.hour, sep=":", precision=2, pad=True),
        coord_eq.dec.to_string(unit=u.deg, sep=":", precision=1, alwayssign=True, pad=True),
    )

def estimate_phase_angle(elong_deg, mode_u):
    delta = 1.5 if mode_u in ("MBA", "DEEP-OPPOSITION", "NARROW ECLIPTIC") else 1.0
    r_sq = 1.0 + delta**2 - 2 * delta * math.cos(math.radians(elong_deg))
    if math.sqrt(r_sq) * delta < 1e-6:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (r_sq + delta**2 - 1.0) / (2 * math.sqrt(r_sq) * delta)))))

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

def radec_to_xy(ra_deg, dec_deg):
    ra_rad = np.deg2rad(np.array(ra_deg, dtype=float))
    x_val = -((ra_rad) % (2 * np.pi) - np.pi)
    y_val = np.deg2rad(np.array(dec_deg, dtype=float))
    return x_val, y_val

def radec_poly_to_xy(ra_list_deg, dec_list_deg):
    ra_rad = np.deg2rad(np.array(ra_list_deg, dtype=float))
    ra_centered = (ra_rad) % (2 * np.pi)
    x_val = -((np.unwrap(ra_centered)) - np.pi)
    y_val = np.deg2rad(np.array(dec_list_deg, dtype=float))
    return x_val, y_val
