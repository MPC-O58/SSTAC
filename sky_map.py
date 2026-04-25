
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Rectangle
import astropy.units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord, GeocentricTrueEcliptic, get_sun, get_body
from astro_utils import utc_to_local_dt


def _field_polygon_radec_deg(ra_deg, dec_deg, fov_arcmin=34.9):
    half_deg = (fov_arcmin / 2.0) / 60.0
    dra = half_deg / max(np.cos(np.deg2rad(dec_deg)), 1e-6)
    r_arr = np.array([ra_deg - dra, ra_deg + dra, ra_deg + dra, ra_deg - dra, ra_deg - dra], dtype=float) % 360.0
    d_arr = np.array([dec_deg - half_deg, dec_deg - half_deg, dec_deg + half_deg, dec_deg + half_deg, dec_deg - half_deg], dtype=float)
    return r_arr, d_arr


def _radec_to_xy(ra_deg, dec_deg):
    ra_rad = np.deg2rad(np.array(ra_deg, dtype=float))
    x_val = -((ra_rad) % (2 * np.pi) - np.pi)
    y_val = np.deg2rad(np.array(dec_deg, dtype=float))
    return x_val, y_val


def _radec_poly_to_xy(ra_list_deg, dec_list_deg):
    ra_rad = np.deg2rad(np.array(ra_list_deg, dtype=float))
    ra_centered = (ra_rad) % (2 * np.pi)
    x_val = -((np.unwrap(ra_centered)) - np.pi)
    y_val = np.deg2rad(np.array(dec_list_deg, dtype=float))
    return x_val, y_val


def show_field_zoom(field_coord, field_no, neocp_coords=None, fov_deg=34.9 / 60.0):
    ra_center = field_coord.ra.deg
    dec0 = field_coord.dec.deg
    half = fov_deg / 2.0
    ra_half_deg = half / max(float(np.cos(np.deg2rad(dec0))), 1e-6)

    fig, ax = plt.subplots(figsize=(7.6, 6.2), facecolor="#000000")
    ax.set_facecolor("#000000")
    ax.set_title(f"Field #{field_no} | RA={field_coord.ra.to_string(u.hour, sep=':')}  Dec={field_coord.dec.to_string(sep=':')}", color="white")
    ax.set_xlabel("Relative RA (deg) [axis inverted]", color="white")
    ax.set_ylabel("Dec (deg)", color="white")
    ax.set_xlim(ra_half_deg * 1.4, -ra_half_deg * 1.4)
    ax.set_ylim(dec0 - half * 1.4, dec0 + half * 1.4)

    ax.add_patch(Rectangle((-ra_half_deg, dec0 - half), 2 * ra_half_deg, 2 * half, fill=False, linewidth=2.0, edgecolor="cyan"))
    ax.scatter([0], [dec0], s=90, marker="x", color="white")

    if neocp_coords is not None and len(neocp_coords) > 0:
        def ra_diff(ra1, ra2):
            return (ra1 - ra2 + 180) % 360 - 180

        nc_ra_diff = ra_diff(neocp_coords.ra.deg, ra_center)
        nc_dec = neocp_coords.dec.deg

        idx_in = np.where(
            (np.abs(nc_ra_diff * max(float(np.cos(np.deg2rad(dec0))), 1e-6)) <= half)
            & (np.abs(nc_dec - dec0) <= half)
        )[0]
        if len(idx_in) > 0:
            ax.scatter(nc_ra_diff[idx_in], nc_dec[idx_in], s=45, color="orange", alpha=0.85)

    ax.grid(True, alpha=0.35, color="white")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("white")
    plt.show()


def build_sky_map_figure(selected, mode, date_str_local, utc_offset, fov_deg,
                         neocp_objects=None, location=None, location_name="Observatory"):
    mode_u = mode.upper()
    if selected:
        t_ref = selected[len(selected) // 2]["best_time"]
    else:
        t_ref = Time(f"{date_str_local} 21:00:00") - utc_offset * u.hour

    noon_utc = Time(f"{date_str_local} 12:00:00") - utc_offset * u.hour

    if selected:
        coords = SkyCoord([it["coord"] for it in selected])
        x_f, y_f = _radec_to_xy(coords.ra.deg, coords.dec.deg)
    else:
        x_f, y_f = np.array([]), np.array([])

    def get_sorted_xy(ra_arr, dec_arr):
        x_a, y_a = _radec_to_xy(ra_arr, dec_arr)
        ord_idx = np.argsort(x_a)
        return x_a[ord_idx], y_a[ord_idx]

    l = np.linspace(0, 360, 721) * u.deg
    gal_icrs = SkyCoord(l=l, b=np.zeros_like(l.value) * u.deg, frame="galactic").transform_to("icrs")
    x_gal, y_gal = get_sorted_xy(gal_icrs.ra.deg, gal_icrs.dec.deg)

    ec_frame = GeocentricTrueEcliptic(equinox=noon_utc)
    lam = np.linspace(0, 360, 721) * u.deg

    if "NARROW" in mode_u:
        band = 10.0
    elif "HIGH" in mode_u:
        band = 30.0
    else:
        band = 10.0

    ecl0 = SkyCoord(lon=lam, lat=np.zeros_like(lam.value) * u.deg, frame=ec_frame).transform_to("icrs")
    eclp = SkyCoord(lon=lam, lat=np.full_like(lam.value, +band) * u.deg, frame=ec_frame).transform_to("icrs")
    eclm = SkyCoord(lon=lam, lat=np.full_like(lam.value, -band) * u.deg, frame=ec_frame).transform_to("icrs")

    x_ecl0, y_ecl0 = get_sorted_xy(ecl0.ra.deg, ecl0.dec.deg)
    x_eclp, y_eclp = get_sorted_xy(eclp.ra.deg, eclp.dec.deg)
    x_eclm, y_eclm = get_sorted_xy(eclm.ra.deg, eclm.dec.deg)

    sun = get_sun(t_ref).transform_to("icrs")
    x_sun, y_sun = _radec_to_xy([sun.ra.deg], [sun.dec.deg])

    try:
        moon = get_body("moon", t_ref, location=location) if location is not None else get_body("moon", t_ref)
    except Exception:
        moon = get_body("moon", t_ref)
    try:
        moon = moon.transform_to("icrs")
    except Exception:
        pass

    x_moon, y_moon = _radec_to_xy([moon.ra.deg], [moon.dec.deg])
    neocp_coords = SkyCoord([o["coord"] for o in neocp_objects]) if neocp_objects else None

    fig = plt.figure(figsize=(16, 8.5), facecolor="#000000")
    ax = fig.add_axes([0.02, 0.18, 0.58, 0.75], projection="mollweide")
    ax.set_facecolor("#000000")

    if neocp_coords is not None and len(neocp_coords) > 0:
        nc_x, nc_y = _radec_to_xy(neocp_coords.ra.deg, neocp_coords.dec.deg)
        x_edges = np.linspace(-np.pi, np.pi, 241)
        y_edges = np.linspace(-np.pi / 2, np.pi / 2, 121)
        H, _, _ = np.histogram2d(nc_x, nc_y, bins=[x_edges, y_edges])

        if np.any(H > 0):
            K = np.array([[1, 1, 1], [1, 2, 1], [1, 1, 1]], dtype=float) / 10.0
            Hp = np.pad(H, ((1, 1), (1, 1)), mode="edge")
            Hs = np.zeros_like(H, dtype=float)
            for i in range(H.shape[0]):
                for j in range(H.shape[1]):
                    Hs[i, j] = np.sum(Hp[i:i + 3, j:j + 3] * K)

            X, Y = np.meshgrid(x_edges, y_edges, indexing="ij")
            pcm = ax.pcolormesh(X, Y, Hs, shading="auto", alpha=0.35)
            cb = fig.colorbar(pcm, ax=ax, orientation="horizontal", pad=0.06, fraction=0.045, aspect=50)
            cb.set_label("NEOCP density (binned)", color="#a4b0be", fontsize=10)
            cb.ax.xaxis.set_tick_params(color="white")
            plt.setp(cb.ax.get_xticklabels(), color="white")
            cb.outline.set_edgecolor("#2c3e50")

    ax.fill_between(x_eclp, y_eclp, y_eclm, alpha=0.10, color="#9ec5ff", label=f"Ecliptic band ±{band:.0f}°")
    ax.plot(x_gal, y_gal, linewidth=1.1, color="#2bb3ff", alpha=0.9, label="Galactic plane (b=0)")
    ax.plot(x_ecl0, y_ecl0, linewidth=1.2, color="#ff9f1c", alpha=0.95, label="Ecliptic (β=0)")

    for i, it in enumerate(selected, start=1):
        r_l, d_l = _field_polygon_radec_deg(float(it["coord"].ra.deg), float(it["coord"].dec.deg), fov_arcmin=fov_deg * 60.0)
        x_p, y_p = _radec_poly_to_xy(r_l, d_l)
        ax.fill(x_p, y_p, facecolor="#ff5a5f", edgecolor="white", linewidth=0.9, alpha=0.22, zorder=4)
        ax.plot(x_p, y_p, color="white", linewidth=0.9, alpha=0.95, zorder=5)
        tx, ty = _radec_to_xy([float(it["coord"].ra.deg)], [float(it["coord"].dec.deg)])
        txt = ax.text(tx[0], ty[0], str(i), fontsize=9.0, ha="center", va="center", color="#ffd84d", weight="bold", zorder=6)
        txt.set_path_effects([pe.withStroke(linewidth=2.6, foreground="black")])

    pts_fields = None
    if x_f.size > 0:
        pts_fields = ax.scatter(x_f, y_f, s=28, alpha=0.01, color="white", picker=True, pickradius=6, label=f"Fields (N={len(x_f)})")

    if neocp_coords is not None and len(neocp_coords) > 0:
        nc_x, nc_y = _radec_to_xy(neocp_coords.ra.deg, neocp_coords.dec.deg)
        ax.scatter(nc_x, nc_y, s=9, marker="o", color="#ff8c00", edgecolors="none", alpha=0.9, label=f"NEOCP (N={len(neocp_objects)})", zorder=3)

    ax.scatter(x_sun, y_sun, s=95, marker="*", color="#ff4d4d", label="Sun", zorder=6)
    ax.scatter(x_moon, y_moon, s=60, marker="o", color="#65ff00", label="Moon", zorder=6)

    ax.grid(True, alpha=0.2, color="white")
    ax.set_title(f"{location_name} • {mode_u} • {date_str_local}", fontsize=17, color="white", pad=15)
    ax.tick_params(colors="white", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("white")

    ax.set_xticks(np.deg2rad(np.array([-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150])))
    ax.set_xticklabels(["22h", "20h", "18h", "16h", "14h", "12h", "10h", "8h", "6h", "4h", "2h"], color="white")

    leg = ax.legend(loc="lower left", fontsize=9, framealpha=0.9)
    leg.get_frame().set_facecolor("#111111")
    leg.get_frame().set_edgecolor("white")
    for t_leg in leg.get_texts():
        t_leg.set_color("white")

    # Operational zoom panels
    zoom_groups = []
    if selected:
        tmp_coords = SkyCoord([it["coord"] for it in selected])
        ra_rad = tmp_coords.ra.rad
        center_ra_all = np.degrees(np.arctan2(np.mean(np.sin(ra_rad)), np.mean(np.cos(ra_rad))) % (2 * np.pi))
        center_dec_all = float(np.median(tmp_coords.dec.deg))

        def ra_diff(ra1, ra2):
            return (ra1 - ra2 + 180) % 360 - 180

        x_all = np.array([
            -ra_diff(it["coord"].ra.deg, center_ra_all) * max(float(np.cos(np.deg2rad(center_dec_all))), 1e-6)
            for it in selected
        ])
        order = np.argsort(x_all)
        x_sorted = x_all[order]

        if len(x_sorted) >= 2:
            diffs = np.diff(x_sorted)
            max_diff_idx = int(np.argmax(diffs))
            if diffs[max_diff_idx] > max(4.0 * fov_deg, 3.0):
                zoom_groups = [
                    ("Cluster A", [selected[i] for i in order[:max_diff_idx + 1]]),
                    ("Cluster B", [selected[i] for i in order[max_diff_idx + 1:]])
                ]

        if not zoom_groups:
            zoom_groups = [("Selected fields", selected)]
    else:
        zoom_groups = [("Selected fields", [])]

    if len(zoom_groups) == 2:
        ax_zoom_list = [
            fig.add_axes([0.64, 0.69, 0.33, 0.24]),
            fig.add_axes([0.64, 0.41, 0.33, 0.24])
        ]
    else:
        ax_zoom_list = [fig.add_axes([0.64, 0.41, 0.33, 0.52])]

    for axz in ax_zoom_list:
        axz.set_facecolor("#000000")

    zoom_meta = []
    for _, items in zoom_groups:
        if not items:
            zoom_meta.append(None)
            continue

        cl_c = SkyCoord([it["coord"] for it in items])
        c_ra = np.degrees(np.arctan2(np.mean(np.sin(cl_c.ra.rad)), np.mean(np.cos(cl_c.ra.rad))) % (2 * np.pi))
        c_dec = float(np.median(np.array([c.dec.deg for c in cl_c])))
        cosc = max(float(np.cos(np.deg2rad(c_dec))), 1e-6)

        h_s = fov_deg / 2.0
        dat, xm, xM, ym, yM = [], np.inf, -np.inf, np.inf, -np.inf

        for j, it in enumerate(items, start=1):
            def ra_diff(ra1, ra2):
                return (ra1 - ra2 + 180) % 360 - 180

            x0 = -ra_diff(float(it["coord"].ra.deg), c_ra) * cosc
            y0 = float(it["coord"].dec.deg)
            idx_num = selected.index(it) + 1 if it in selected else j
            dat.append((idx_num, x0, y0))
            xm, xM = min(xm, x0 - h_s), max(xM, x0 + h_s)
            ym, yM = min(ym, y0 - h_s), max(yM, y0 + h_s)

        px = max(0.8, (xM - xm) * 0.35)
        py = max(0.8, (yM - ym) * 0.45)

        zoom_meta.append({
            "h_s": h_s,
            "items": dat,
            "xm": xm - px,
            "xM": xM + px,
            "ym": ym - py,
            "yM": yM + py,
            "cx": 0.5 * ((xm - px) + (xM + px)),
            "cy": 0.5 * ((ym - py) + (yM + py)),
            "w": (xM + px) - (xm - px),
            "h": (yM + py) - (ym - py)
        })

    cs = None
    if len(zoom_groups) >= 2 and all(m is not None for m in zoom_meta):
        cs = max(max(m["w"], m["h"]) for m in zoom_meta)

    for axz, (title, _items), meta in zip(ax_zoom_list, zoom_groups, zoom_meta):
        if meta is not None:
            for idx_label, x0, y0 in meta["items"]:
                hs = meta["h_s"]
                axz.add_patch(Rectangle((x0 - hs, y0 - hs), 2 * hs, 2 * hs, facecolor="cyan", edgecolor="cyan", linewidth=1.4, alpha=0.15))
                axz.plot([x0 - hs, x0 + hs, x0 + hs, x0 - hs, x0 - hs],
                         [y0 - hs, y0 - hs, y0 + hs, y0 + hs, y0 - hs],
                         color="cyan", linewidth=1.0, alpha=0.7)
                txt = axz.text(x0, y0, str(idx_label), ha="center", va="center", fontsize=10.5, weight="bold", color="white")
                txt.set_path_effects([pe.withStroke(linewidth=2.8, foreground="black")])

            if cs is not None:
                axz.set_xlim(meta["cx"] - cs / 2, meta["cx"] + cs / 2)
                axz.set_ylim(meta["cy"] - cs / 2, meta["cy"] + cs / 2)
            else:
                axz.set_xlim(meta["xm"], meta["xM"])
                axz.set_ylim(meta["ym"], meta["yM"])
            axz.set_aspect("equal", adjustable="box")
        else:
            axz.set_xlim(-1, 1)
            axz.set_ylim(-1, 1)

        axz.set_title(f"Operational zoom: {title}", fontsize=11, color="#a4b0be", pad=4)
        axz.set_xlabel("Local sky-projected X (deg)", color="white", fontsize=8)
        axz.set_ylabel("Dec (deg)", color="white", fontsize=8)
        axz.grid(True, alpha=0.18, color="white")
        axz.tick_params(colors="white", labelsize=8)
        axz.text(
            0.99, 0.02,
            f" ({fov_deg * 60.0:.1f}' × {fov_deg * 60.0:.1f}')",
            transform=axz.transAxes,
            ha="right", va="bottom",
            color="white", fontsize=8.2,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#111111", edgecolor="white", alpha=0.75)
        )
        for spine in axz.spines.values():
            spine.set_edgecolor("white")

    ax_tbl = fig.add_axes([0.64, 0.04, 0.33, 0.33])
    ax_tbl.set_facecolor("#000000")
    for spine in ax_tbl.spines.values():
        spine.set_edgecolor("#2c3e50")
        spine.set_linewidth(1.5)

    ax_tbl.set_xticks([])
    ax_tbl.set_yticks([])
    ax_tbl.set_title("Execution Target Summary (Top 10)", color="#00a8ff", fontsize=12, weight="bold", pad=8)

    if not selected:
        ax_tbl.text(0.5, 0.5, "No fields selected", ha="center", va="center", color="#e84118", fontsize=12)
    else:
        header_str = f"{'#':<2} | {'Target ID':<14} | {'RA':<11} | {'Dec':<11} | {'Time':<5} | {'Alt°':<4} | {'Dur'}"
        ax_tbl.text(0.04, 0.88, header_str, color="#4cd137", fontfamily="monospace", fontsize=8.5, weight="bold")
        ax_tbl.text(0.04, 0.82, "-" * 84, color="#718093", fontfamily="monospace", fontsize=8.5)

        for i, it in enumerate(selected[:10], start=1):
            tid = it.get("target_id", f"FIELD_{i:03d}")
            r_str = it["coord"].ra.to_string(unit=u.hour, sep=":", precision=2, pad=True)
            d_str = it["coord"].dec.to_string(unit=u.deg, sep=":", precision=1, alwayssign=True, pad=True)
            bt_str = utc_to_local_dt(it["best_time"], utc_offset).strftime("%H:%M")
            alt_v = float(it.get("best_alt", np.nan))
            dur_v = float(it.get("duration", 0.0))
            row_str = f"{i:<2} | {tid:<14} | {r_str:<11} | {d_str:<11} | {bt_str:>5} | {alt_v:4.0f} | {dur_v:3.1f}"
            ax_tbl.text(0.04, 0.74 - (i - 1) * 0.075, row_str, color="#dfe4ea", fontfamily="monospace", fontsize=8.2)

    if pts_fields is not None and neocp_coords is not None and len(neocp_coords) > 0:
        def _on_pick(event):
            if event.artist is not pts_fields or not getattr(event, "ind", None) or len(event.ind) == 0:
                return
            show_field_zoom(selected[int(event.ind[0])]["coord"], field_no=int(event.ind[0]) + 1, neocp_coords=neocp_coords, fov_deg=fov_deg)
        fig.canvas.mpl_connect("pick_event", _on_pick)

    return fig, ax


def build_history_coverage_figure(rows, current_selected=None, mode_label="SSTAC History Coverage", fov_deg=34.9/60.0):
    fig = plt.figure(figsize=(16, 8.5), facecolor="#000000")
    ax = fig.add_axes([0.05, 0.08, 0.90, 0.84], projection="mollweide")
    ax.set_facecolor("#000000")

    # Ecliptic reference line
    lam = np.linspace(0, 360, 721) * u.deg
    ec_frame = GeocentricTrueEcliptic(equinox=Time.now())
    ecl0 = SkyCoord(lon=lam, lat=np.zeros_like(lam.value) * u.deg, frame=ec_frame).transform_to("icrs")
    x_ecl0, y_ecl0 = _radec_to_xy(ecl0.ra.deg, ecl0.dec.deg)
    order = np.argsort(x_ecl0)
    ax.plot(x_ecl0[order], y_ecl0[order], linewidth=1.0, color="#ff9f1c", alpha=0.8, label="Ecliptic")

    # Galactic plane reference line
    gl = np.linspace(0, 360, 721) * u.deg
    gal_icrs = SkyCoord(l=gl, b=np.zeros_like(gl.value) * u.deg, frame="galactic").transform_to("icrs")
    x_gal, y_gal = _radec_to_xy(gal_icrs.ra.deg, gal_icrs.dec.deg)
    gord = np.argsort(x_gal)
    ax.plot(x_gal[gord], y_gal[gord], linewidth=1.0, color="#2bb3ff", alpha=0.6, label="Galactic plane")

    # ── OBSERVED fields from performance log ─────────────────────
    obs_coords = []
    skipped = 0
    for row in rows:
        ra_raw  = str(row.get("ra",  "")).strip()
        dec_raw = str(row.get("dec", "")).strip()
        if not ra_raw or not dec_raw:
            skipped += 1
            continue
        coord = None
        # Try hourangle format first (HH:MM:SS.s), then decimal degrees
        for ra_unit in ("hourangle", "deg"):
            try:
                coord = SkyCoord(ra_raw, dec_raw, unit=(ra_unit, "deg"))
                break
            except Exception:
                continue
        if coord is None:
            skipped += 1
            continue
        obs_coords.append(coord)
        r_l, d_l = _field_polygon_radec_deg(float(coord.ra.deg), float(coord.dec.deg), fov_arcmin=fov_deg * 60.0)
        x_p, y_p = _radec_poly_to_xy(r_l, d_l)
        ax.fill(x_p, y_p, facecolor="#3498db", edgecolor="#5dade2",
                linewidth=1.2, alpha=0.55, zorder=3)

    # Bright scatter markers so even a single observation is visible
    if obs_coords:
        ras  = [c.ra.deg  for c in obs_coords]
        decs = [c.dec.deg for c in obs_coords]
        sx, sy = _radec_to_xy(ras, decs)
        skip_note = f" ({skipped} skipped)" if skipped else ""
        ax.scatter(sx, sy, s=55, marker="o",
                   facecolor="#5dade2", edgecolor="white",
                   linewidths=1.1, zorder=5,
                   label=f"Observed fields (N={len(obs_coords)}){skip_note}")

    # ── Optional overlay of current plan (only if explicitly passed)
    if current_selected:
        for i, it in enumerate(current_selected, start=1):
            r_l, d_l = _field_polygon_radec_deg(float(it["coord"].ra.deg),
                                                float(it["coord"].dec.deg),
                                                fov_arcmin=fov_deg * 60.0)
            x_p, y_p = _radec_poly_to_xy(r_l, d_l)
            ax.fill(x_p, y_p, facecolor="#ff5a5f", edgecolor="white",
                    linewidth=0.8, alpha=0.35, zorder=4)
            tx, ty = _radec_to_xy([float(it["coord"].ra.deg)],
                                  [float(it["coord"].dec.deg)])
            ax.text(tx[0], ty[0], str(i), fontsize=8.5, ha="center", va="center",
                    color="#ffd84d", weight="bold", zorder=6)

    # Cosmetics
    ax.grid(True, alpha=0.2, color="white")
    ax.set_title(mode_label, fontsize=17, color="white", pad=15)
    ax.tick_params(colors="white", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("white")
    ax.set_xticks(np.deg2rad(np.array([-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150])))
    ax.set_xticklabels(["22h", "20h", "18h", "16h", "14h", "12h", "10h", "8h", "6h", "4h", "2h"],
                       color="white")

    leg = ax.legend(loc="lower left", fontsize=10, framealpha=0.9)
    leg.get_frame().set_facecolor("#111111")
    leg.get_frame().set_edgecolor("white")
    for t_leg in leg.get_texts():
        t_leg.set_color("white")

    return fig, ax


def save_sky_map(fig, mode, date_str_local):
    safe_mode = mode.upper().replace(" ", "_")
    filename = f"skymap_{safe_mode}_{date_str_local}.png"
    fig.savefig(filename, dpi=150, bbox_inches="tight")
    return filename
