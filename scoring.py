import math
import numpy as np

def clip01(x):
    return float(max(0.0, min(1.0, x)))

def gaussian_score(x, mu, sigma):
    if sigma <= 0:
        return 0.0
    return float(math.exp(-0.5 * ((x - mu) / sigma) ** 2))

def circular_sep_deg(a_deg, b_deg):
    return abs(((float(a_deg) - float(b_deg) + 180.0) % 360.0) - 180.0)

def normalize_linear(x, x0, x1):
    if x1 <= x0:
        return 0.0
    return clip01((float(x) - float(x0)) / (float(x1) - float(x0)))

def role_from_mode(mode_u):
    return "HI-INC" if mode_u.upper() == "HIGH INCLINATION" else "DISCOVERY"

def elongation_score(elong_deg, mode_u):
    mode_u = mode_u.upper()
    if mode_u in ("NARROW ECLIPTIC", "HIGH INCLINATION", "DEEP-OPPOSITION", "PHA-MIDNIGHT"):
        return gaussian_score(float(elong_deg), 180.0, 18.0 if mode_u == "NARROW ECLIPTIC" else 28.0)
    return gaussian_score(float(elong_deg), 180.0, 24.0)

def twilight_score(sun_alt_deg, mode_u):
    s = float(sun_alt_deg)
    if s <= -18:
        return 1.0
    if s >= -12:
        return 0.0
    return clip01((-12.0 - s) / 6.0)

def compute_field_score(mode_u, lam_deg, bet_deg, lambda_sun_deg, best_alt, moon_sep, phase_best, duration_hours, neocp_boost=0.0, gap_bonus=0.0):
    mode_u = mode_u.upper()
    opp_lon = (float(lambda_sun_deg) + 180.0) % 360.0
    opp_sep = circular_sep_deg(lam_deg, opp_lon)
    abs_b = abs(float(bet_deg))

    opp_score = gaussian_score(opp_sep, 0.0, 10.0 if mode_u == "NARROW ECLIPTIC" else 14.0)
    alt_score = normalize_linear(best_alt, 25.0, 75.0)
    moon_score = normalize_linear(moon_sep, 25.0, 120.0)
    duration_score = normalize_linear(duration_hours, 0.5, 5.0)
    phase_score = clip01(1.0 - float(phase_best) / 60.0)
    neocp_score = clip01(neocp_boost)
    gap_score   = clip01(gap_bonus / 6.0)   # normalise to 0–1 (max_bonus=6.0)

    if mode_u == "NARROW ECLIPTIC":
        ecl_score = gaussian_score(abs_b, 0.0, 4.5)
        return (42.0 * opp_score + 28.0 * ecl_score + 14.0 * alt_score
                + 7.0 * moon_score + 4.0 * phase_score + 2.0 * duration_score
                + 3.0 * neocp_score + 5.0 * gap_score)

    hiinc_center_score = gaussian_score(abs_b, 22.5, 5.5)
    return (36.0 * opp_score + 28.0 * hiinc_center_score + 16.0 * alt_score
            + 9.0 * moon_score + 5.0 * phase_score + 3.0 * duration_score
            + 3.0 * neocp_score + 5.0 * gap_score)
