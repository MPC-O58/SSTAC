
import time
from datetime import datetime, date
import math
import numpy as np
from astropy.coordinates import SkyCoord, AltAz, get_sun, get_body
import astropy.units as u
from config import (
    LAMBDA_STEP_DEG, STRIP_BETA_TOL_NE, STRIP_BETA_TOL_HI, BETA_MBA_STEP, BETA_NEO_STEP,
    MBA_NIGHT_START, MBA_NIGHT_END, MBA_STEP_MIN, DUPLICATE_RADIUS_DEG,
    FOV_ARCMIN_DEFAULT, DRIFT_REFERENCE_DATE, DRIFT_RATE_NE_DEG, DRIFT_RATE_HI_DEG,
    LOOKBACK_NIGHTS, HISTORY_SCALE_DEG, NOVELTY_RADIUS_DEG, MEMORY_PENALTY_WEIGHT,
    NOVELTY_BONUS_WEIGHT, DIVERSITY_BONUS_WEIGHT, CLUSTERING_PENALTY_WEIGHT, CORE_FRACTION
)
from astro_utils import to_utc_time, make_time_grid, fixed_local_window, ecliptic_to_icrs, estimate_phase_angle
from scoring import elongation_score, twilight_score, compute_field_score, role_from_mode, circular_sep_deg, clip01
from io_utils import load_prev_coords
from history_utils import load_recent_history_points

try:
    from atlas_gap import load_atlas_gap_map, atlas_gap_bonus, atlas_gap_summary
    _ATLAS_GAP_AVAILABLE = True
except ImportError:
    _ATLAS_GAP_AVAILABLE = False

def mode_prefix(mode):
    mode_u = mode.upper()
    if "NARROW" in mode_u:
        return "NE"
    if "HIGH" in mode_u:
        return "HI"
    return "UK"

def build_target_id(mode, date_str_local, idx):
    date_str = date_str_local.replace("-", "")
    return f"{mode_prefix(mode)}_{date_str}_{idx:03d}"

def renumber_target_ids(selected, mode, date_str_local):
    for i, item in enumerate(selected, start=1):
        item["target_id"] = build_target_id(mode, date_str_local, i)
    return selected

def _night_index(date_str_local):
    d0 = date.fromisoformat(DRIFT_REFERENCE_DATE)
    d1 = date.fromisoformat(date_str_local)
    return (d1 - d0).days

def _drifted_center_lambda(lambda_sun_deg, mode, date_str_local):
    day_idx = _night_index(date_str_local)
    amp = DRIFT_RATE_NE_DEG if mode.upper() == "NARROW ECLIPTIC" else DRIFT_RATE_HI_DEG
    phase = (day_idx % 5) - 2
    drift = (phase / 2.0) * amp
    return ((lambda_sun_deg + 180.0) % 360.0 + drift) % 360.0

def generate_grid(mode, lambda_sun_deg, date_str_local, avoid_mba=False):
    mode = mode.upper()
    grid = []
    lam_center = _drifted_center_lambda(lambda_sun_deg, mode, date_str_local)
    if mode == "NARROW ECLIPTIC":
        lam_vals = (lam_center + np.arange(-18.0, 18.0 + 1e-9, LAMBDA_STEP_DEG)) % 360.0
        for l in lam_vals:
            for b in np.arange(-10.0, 10.0 + 1e-9, BETA_MBA_STEP):
                grid.append((float(l), float(b), "NIGHT"))
    elif mode == "HIGH INCLINATION":
        # Bug fix: use -15.0 + 1e-9 as stop so that -15.0 itself is included
        beta_vals = np.concatenate([
            np.arange(-30.0, -15.0 + 1e-9, BETA_NEO_STEP),
            np.arange(15.0, 30.0 + 1e-9, BETA_NEO_STEP)
        ])
        lam_vals = (lam_center + np.arange(-15.0, 15.0 + 1e-9, LAMBDA_STEP_DEG)) % 360.0
        for l in lam_vals:
            for b in beta_vals:
                grid.append((float(l), float(b), "NIGHT"))
    if avoid_mba:
        grid = [(l, b, sec) for (l, b, sec) in grid if abs(b) >= 15.0]
    return grid

def best_visibility_v2(coord_eq, times_utc, location, min_alt_deg, mode_u, sector_label, sun_icrs_arr=None, sun_alt_deg_arr=None):
    alt_deg = coord_eq.transform_to(AltAz(obstime=times_utc, location=location)).alt.to_value(u.deg)
    if sun_alt_deg_arr is not None and sun_icrs_arr is not None:
        sun_alt_deg = sun_alt_deg_arr
        elong = coord_eq.separation(sun_icrs_arr).to_value(u.deg)
    else:
        sun_alt_deg = get_sun(times_utc).transform_to(AltAz(obstime=times_utc, location=location)).alt.to_value(u.deg)
        elong = coord_eq.separation(get_sun(times_utc).transform_to("icrs")).to_value(u.deg)
    alpha = np.array([estimate_phase_angle(e, mode_u) for e in elong])
    # Bug fix: 60° cap was too tight for HI mode and silently dropped valid fields.
    # Phase quality is already handled gracefully by phase_score in compute_field_score.
    phase_cap = 90.0 if mode_u == "HIGH INCLINATION" else 60.0
    ok_phase = (alpha >= 0.0) & (alpha <= phase_cap)
    ok = np.isfinite(alt_deg) & np.isfinite(sun_alt_deg) & (alt_deg >= float(min_alt_deg)) & ok_phase
    if not np.any(ok):
        return []
    tw = np.array([twilight_score(s, mode_u) for s in sun_alt_deg], dtype=float)
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
        k = a + int(np.argmax(merit[a:b + 1]))
        duration_hours = (b - a + 1) * (times_utc[1] - times_utc[0]).to_value(u.hour) if len(times_utc) > 1 else 0
        windows.append({
            "label": sector_label, "t_start": times_utc[a], "t_end": times_utc[b], "t_best": times_utc[k],
            "best_idx": k, "best_alt": float(alt_deg[k]), "phase_best": float(alpha[k]),
            "merit": float(merit[k]), "duration_hours": duration_hours,
        })
    return windows

def _soft_history_memory_terms(coord, history_points):
    if not history_points:
        return 0.0, NOVELTY_BONUS_WEIGHT
    history_coords = SkyCoord([p["coord"] for p in history_points])
    seps = coord.separation(history_coords).to_value(u.deg)
    density = 0.0
    close_hits = 0.0
    for sep, p in zip(seps, history_points):
        age_w = math.exp(-max(p.get("age_days", 1) - 1, 0) / 2.5)
        kern = math.exp(-(float(sep) / max(HISTORY_SCALE_DEG, 1e-6)) ** 2)
        density += age_w * kern
        if float(sep) <= NOVELTY_RADIUS_DEG:
            close_hits += age_w
    memory_penalty = MEMORY_PENALTY_WEIGHT * min(density, 1.6)
    novelty_bonus = NOVELTY_BONUS_WEIGHT * math.exp(-close_hits)
    return float(memory_penalty), float(novelty_bonus)

def _longitude_diversity_terms(candidate, chosen):
    if not chosen:
        return DIVERSITY_BONUS_WEIGHT, 0.0
    min_lam_sep = min(circular_sep_deg(candidate["lam_deg"], s["lam_deg"]) for s in chosen)
    diversity_bonus = DIVERSITY_BONUS_WEIGHT * clip01(min_lam_sep / 10.0)
    clustering_penalty = CLUSTERING_PENALTY_WEIGHT * math.exp(-(min_lam_sep / 2.0) ** 2)
    return float(diversity_bonus), float(clustering_penalty)

def _strip_preserving_sort(selected):
    return sorted(selected, key=lambda x: (float(x["lam_deg"]), float(x["bet_deg"]), x["best_time"].unix))

def _is_core_candidate(c, mode_u, lambda_sun_deg):
    opp_lon = (float(lambda_sun_deg) + 180.0) % 360.0
    opp_sep = circular_sep_deg(c["lam_deg"], opp_lon)
    abs_b = abs(float(c["bet_deg"]))
    if mode_u == "NARROW ECLIPTIC":
        return (abs_b <= 6.0) and (opp_sep <= 12.0)
    # Bug fix: align with grid beta range [15, 17, 19, 21, 23, 25, 27, 29].
    # Old range (18–27) excluded the 15° and 17° rows, starving the core pool.
    return (15.0 <= abs_b <= 29.0) and (opp_sep <= 15.0)

def _valid_separation(candidate, chosen, min_sep_deg):
    if not chosen:
        return True
    sep = candidate["coord"].separation(SkyCoord([s["coord"] for s in chosen])).to_value(u.deg)
    return float(np.min(sep)) >= min_sep_deg

def _strip_beta_tol(mode_u):
    return STRIP_BETA_TOL_NE if mode_u == "NARROW ECLIPTIC" else STRIP_BETA_TOL_HI


def _dynamic_pick(pool, chosen, quota, min_sep_deg, history_points, stage_label, mode_u, lambda_sun_deg):
    remaining = list(pool)
    picked = []
    beta_tol = _strip_beta_tol(mode_u)

    while remaining and len(picked) < quota:
        best_idx = None
        best_adj = -1e18
        best_debug = None
        current = chosen + picked
        strip_ref_beta = float(np.median([c["bet_deg"] for c in current])) if current else None

        for idx, c in enumerate(remaining):
            if not _valid_separation(c, current, min_sep_deg):
                continue

            mem_pen, nov_bonus = _soft_history_memory_terms(c["coord"], history_points)
            div_bonus, cluster_pen = _longitude_diversity_terms(c, current)

            stage_bonus = 0.0
            geom_penalty = 0.0

            if stage_label == "explore":
                if mode_u == "NARROW ECLIPTIC":
                    stage_bonus = 1.2 * min(abs(float(c["bet_deg"])), 6.0) / 6.0
                else:
                    stage_bonus = 1.5 * min(abs(abs(float(c["bet_deg"])) - 22.5), 5.0) / 5.0

            if strip_ref_beta is not None:
                beta_dev = abs(float(c["bet_deg"]) - strip_ref_beta)
                if beta_dev > beta_tol:
                    geom_penalty += 6.0 * ((beta_dev - beta_tol) / max(beta_tol, 1e-6))

            opp_lon = (float(lambda_sun_deg) + 180.0) % 360.0
            opp_sep = circular_sep_deg(float(c["lam_deg"]), opp_lon)
            # Bug fix: HI grid spans ±15° in lambda from a drifted center, so
            # edge fields naturally sit ~15–20° from opposition. Use a wider
            # threshold for HI mode to avoid penalising all edge candidates.
            opp_tol = 20.0 if mode_u == "HIGH INCLINATION" else 18.0
            if opp_sep > opp_tol:
                geom_penalty += 2.0 * ((opp_sep - opp_tol) / 6.0)

            adjusted = float(c["score"]) - mem_pen - cluster_pen - geom_penalty + nov_bonus + div_bonus + stage_bonus

            if adjusted > best_adj:
                best_adj = adjusted
                best_idx = idx
                best_debug = (mem_pen, nov_bonus, div_bonus, cluster_pen, stage_bonus, geom_penalty)

        if best_idx is None:
            break

        chosen_c = remaining.pop(best_idx)
        chosen_c["memory_penalty"] = best_debug[0]
        chosen_c["novelty_bonus"] = best_debug[1]
        chosen_c["diversity_bonus"] = best_debug[2]
        chosen_c["clustering_penalty"] = best_debug[3]
        chosen_c["stage_bonus"] = best_debug[4]
        chosen_c["geometry_penalty"] = best_debug[5]
        chosen_c["adjusted_score"] = best_adj
        picked.append(chosen_c)

    return picked

def generate_plan(date_str_local, mode, location, utc_offset, min_moon_deg, min_alt_deg,
                  max_fields, use_history, avoid_galactic, gal_b_min_deg, use_overlap,
                  overlap_percent, use_neocp_weighting, avoid_mba_zone, neocp_objects=None,
                  progress_cb=None, fov_deg=None, use_atlas_gap=False,
                  atlas_gap_map=None, **kwargs):
    mode_u = mode.upper()
    from astropy.coordinates import GeocentricTrueEcliptic
    noon_utc = to_utc_time(datetime.fromisoformat(f"{date_str_local} 12:00"), utc_offset)
    lam_sun = get_sun(noon_utc).transform_to(GeocentricTrueEcliptic(equinox=noon_utc)).lon.to_value(u.deg)
    w_start, w_end = fixed_local_window(date_str_local, MBA_NIGHT_START, MBA_NIGHT_END, utc_offset)
    times = make_time_grid(w_start, w_end, MBA_STEP_MIN)
    grid = generate_grid(mode_u, lam_sun, date_str_local, avoid_mba=avoid_mba_zone)
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
    recent_history = load_recent_history_points(date_str_local, lookback_nights=LOOKBACK_NIGHTS)
    neocp_coords = SkyCoord([o["coord"] for o in neocp_objects]) if (use_neocp_weighting and neocp_objects) else None

    stats = {"grid_total": total, "pass_vis": 0, "pass_moon": 0, "pass_gal": 0, "pass_history": 0,
             "candidates": 0, "selected": 0, "core_candidates": 0, "explore_candidates": 0,
             "history_points": len(recent_history), "drift_center_lambda_deg": _drifted_center_lambda(lam_sun, mode_u, date_str_local)}

    # ── ATLAS Gap Map ──────────────────────────────────────────────────────────
    if use_atlas_gap and _ATLAS_GAP_AVAILABLE:
        if atlas_gap_map is None:
            if progress_cb:
                progress_cb(0, 1, "Loading ATLAS gap data...")
            png_paths = kwargs.get("atlas_png_paths", [])
            atlas_gap_map = load_atlas_gap_map(
                png_paths   = png_paths if png_paths else None,
                progress_cb = lambda d, t, s: progress_cb(d, t, f"ATLAS: {s}")
                              if progress_cb else None,
            )
        summary = atlas_gap_summary(atlas_gap_map)
        stats["atlas_gap_frames"]  = summary["total_frames"]
        stats["atlas_race_frames"] = summary.get("age2_frames", 0)
        stats["atlas_sources"]     = summary.get("sources", {})
    else:
        atlas_gap_map = []
        stats["atlas_gap_frames"]  = 0
        stats["atlas_race_frames"] = 0
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
        wins = [max(wins, key=lambda w: w["merit"])]
        for w in wins:
            msep = coord.separation(moon_icrs_arr[w["best_idx"]]).to_value(u.deg)
            if msep < min_moon_deg:
                continue
            stats["pass_moon"] += 1
            neocp_boost = 0.0
            if neocp_coords is not None and len(neocp_coords) > 0:
                sep_arr = coord.separation(neocp_coords).to_value(u.deg)
                close_mask = sep_arr < 5.0
                if np.any(close_mask):
                    neocp_boost = float(np.max((5.0 - sep_arr[close_mask]) / 5.0))

            # ATLAS gap bonus — higher bonus = ATLAS hasn't been here recently
            gap_bonus = 0.0
            if use_atlas_gap and atlas_gap_map:
                gap_bonus = atlas_gap_bonus(coord, atlas_gap_map)

            score = compute_field_score(mode_u, lam, bet, lam_sun, float(w["best_alt"]), float(msep), float(w["phase_best"]), float(w["duration_hours"]), neocp_boost, gap_bonus=gap_bonus)
            candidates.append({
                "orig_mode": mode_u, "role": role_from_mode(mode_u), "coord": coord, "best_time": w["t_best"],
                "best_alt": float(w["best_alt"]), "moon_sep": float(msep), "score": float(score), "base_score": float(score),
                "gal_b_deg": float(gal_b), "sector": w["label"], "window_start": w["t_start"], "window_end": w["t_end"],
                "phase_best": float(w["phase_best"]), "duration": w["duration_hours"], "lam_deg": float(lam), "bet_deg": float(bet),
                "neocp_boost": float(neocp_boost), "gap_bonus": float(gap_bonus),
            })
    candidates.sort(key=lambda x: x["score"], reverse=True)
    stats["candidates"] = len(candidates)
    fov_deg = float(fov_deg) if fov_deg else (FOV_ARCMIN_DEFAULT / 60.0)
    min_sep = fov_deg * (1.0 - max(0, min(80, int(overlap_percent))) / 100.0) if use_overlap else fov_deg
    core_pool = [c for c in candidates if _is_core_candidate(c, mode_u, lam_sun)]
    explore_pool = [c for c in candidates if c not in core_pool]
    stats["core_candidates"] = len(core_pool)
    stats["explore_candidates"] = len(explore_pool)
    core_quota = max(1, int(round(max_fields * CORE_FRACTION)))
    core_quota = min(core_quota, max_fields)
    explore_quota = max(0, max_fields - core_quota)
   
    stage1_source = core_pool if core_pool else candidates
    stage1 = _dynamic_pick(
        stage1_source,
        [],
        core_quota,
        min_sep,
        recent_history,
        "core",
        mode_u,
        lam_sun
    )

    remaining_pool = [c for c in candidates if c not in stage1]
    explore_candidates_pool = [c for c in explore_pool if c not in stage1]
    stage2_source = explore_candidates_pool if explore_candidates_pool else remaining_pool
    stage2 = _dynamic_pick(
        stage2_source,
        stage1,
        explore_quota,
        min_sep,
        recent_history,
        "explore",
        mode_u,
        lam_sun
    )

    selected = stage1 + stage2

    if len(selected) < max_fields:
        filler_pool = [c for c in candidates if c not in selected]
        filler = _dynamic_pick(
            filler_pool,
            selected,
            max_fields - len(selected),
            min_sep,
            recent_history,
            "fill",
            mode_u,
            lam_sun
        )
        selected.extend(filler)

    selected = _strip_preserving_sort(selected)
    selected = renumber_target_ids(selected, mode_u, date_str_local)

    stats["selected"] = len(selected)
    if progress_cb:
        progress_cb(1, 1, "Done")
    # Return atlas_gap_map so the GUI can cache it and skip re-loading next run
    return lam_sun, w_start, w_end, selected, stats, atlas_gap_map
