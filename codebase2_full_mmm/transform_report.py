"""Report of the LEARNED transformation parameters and the ranges actually used.

Answers, per channel, with credible intervals:
  - what adstock decay was learned (and its half-life in periods)
  - what peak lag (theta) was learned, for delayed-adstock channels
  - what half-saturation point (Hill ec) was learned - in scaled AND raw units
  - what Hill slope applied (fixed or learned)
  - what range of adstocked values the data actually covered, and how far into
    the saturation curve each region operated (share of weeks above ec)

Outputs (04_transforms/):
  transform_parameters.csv     one row per channel: all of the above
  adstock_ranges.csv           per channel x region: adstocked-series ranges
  adstock_decay_curves.png     carryover weights vs lag, with 90% bands
  saturation_curves.png        Hill response curves with observed-range markers
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

from config import ModelConfig
from data_prep import PanelData
from outputs import stack_posterior
from transforms import adstock_weights_np, apply_adstock_np, half_life


def _s(x, hdi=0.90):
    lo, hi = np.percentile(x, [(1 - hdi) / 2 * 100, (1 + hdi) / 2 * 100])
    return float(np.median(x)), float(lo), float(hi)


def transform_report(idata, p: PanelData, cfg: ModelConfig, outdir: str,
                     n_draws: int = 400, seed: int = 0) -> pd.DataFrame:
    tch = cfg.transformed_channels
    if not tch:
        return pd.DataFrame()
    os.makedirs(outdir, exist_ok=True)
    post = stack_posterior(idata, n_draws, seed)

    a_d = post["adstock_alpha"].transpose("sample", "channel").values
    th_d = post["adstock_theta"].transpose("sample", "channel").values
    ec_d = post["hill_ec"].transpose("sample", "channel").values
    sl_d = post["hill_slope"].transpose("sample", "channel").values

    # ---- parameter table ----------------------------------------------------
    rows = []
    for j, ch in enumerate(tch):
        am, alo, ahi = _s(a_d[:, j])
        hl = half_life(a_d[:, j])
        hm, hlo, hhi = _s(hl)
        em, elo, ehi = _s(ec_d[:, j])
        sm, slo, shi = _s(sl_d[:, j])
        med_scale = p.media_scale[:, j]
        row = {
            "channel": ch.name, "adstock_type": ch.adstock, "max_lag": ch.max_lag,
            "decay_median": am, "decay_hdi_5": alo, "decay_hdi_95": ahi,
            "half_life_periods_median": hm, "half_life_hdi_5": hlo,
            "half_life_hdi_95": hhi,
            "hill_ec_scaled_median": em, "hill_ec_hdi_5": elo, "hill_ec_hdi_95": ehi,
            # ec back in raw activity units: ec * per-region media median
            "hill_ec_raw_units_min_region": float(em * med_scale.min()),
            "hill_ec_raw_units_median_region": float(em * np.median(med_scale)),
            "hill_ec_raw_units_max_region": float(em * med_scale.max()),
            "hill_slope_median": sm, "hill_slope_hdi_5": slo, "hill_slope_hdi_95": shi,
            "slope_learned": bool(ch.learn_slope),
        }
        if ch.adstock == "delayed":
            tm, tlo, thi = _s(th_d[:, j])
            row.update({"peak_lag_median": tm, "peak_lag_hdi_5": tlo,
                        "peak_lag_hdi_95": thi})
        for fx in ("fix_alpha", "fix_ec", "fix_slope", "fix_theta"):
            v = getattr(ch, fx)
            if v is not None:
                row[fx] = v
        rows.append(row)
    params = pd.DataFrame(rows)
    params.to_csv(os.path.join(outdir, "transform_parameters.csv"), index=False)

    # ---- adstocked ranges per region (posterior-median parameters) -----------
    range_rows = []
    for j, ch in enumerate(tch):  # ranges use a single posterior-median "draw"
        a_med = np.array([np.median(a_d[:, j])])
        th_med = np.array([np.median(th_d[:, j])])
        ec_med = float(np.median(ec_d[:, j]))
        w = adstock_weights_np(a_med, th_med, p.L, ch.adstock == "delayed",
                               ch.max_lag)
        ads = apply_adstock_np(p.Mlag[:, :, :, j], w)[0]     # (G,T)
        carryover_share = float(1.0 - w[0, 0])
        for g, r in enumerate(p.region_names):
            x = ads[g]
            pos = x[x > 0]
            range_rows.append({
                "channel": ch.name, "region": r,
                "adstocked_scaled_median_active": float(np.median(pos)) if len(pos) else 0.0,
                "adstocked_scaled_p95": float(np.percentile(x, 95)),
                "adstocked_scaled_max": float(x.max()),
                "pct_weeks_active": float((x > 0).mean() * 100),
                "pct_active_weeks_above_ec": float((pos > ec_med).mean() * 100)
                if len(pos) else 0.0,
                "carryover_share_of_effect_pct": carryover_share * 100,
            })
    ranges = pd.DataFrame(range_rows)
    ranges.to_csv(os.path.join(outdir, "adstock_ranges.csv"), index=False)

    # ---- decay curve plot -----------------------------------------------------
    C = len(tch)
    ncol = min(3, C)
    nrow = int(np.ceil(C / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.5 * ncol, 3 * nrow),
                             squeeze=False)
    for j, ch in enumerate(tch):
        ax = axes[j // ncol][j % ncol]
        w = adstock_weights_np(a_d[:, j], th_d[:, j], p.L,
                               ch.adstock == "delayed", ch.max_lag)  # (S,L)
        lags = np.arange(ch.max_lag)
        med = np.median(w[:, :ch.max_lag], axis=0)
        lo = np.percentile(w[:, :ch.max_lag], 5, axis=0)
        hi = np.percentile(w[:, :ch.max_lag], 95, axis=0)
        ax.plot(lags, med, marker="o", ms=3)
        ax.fill_between(lags, lo, hi, alpha=0.25)
        ax.set_title(f"{ch.name} ({ch.adstock})", fontsize=9)
        ax.set_xlabel("lag (periods)", fontsize=8)
        ax.set_ylabel("weight", fontsize=8)
    for k in range(C, nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("Learned adstock (carryover) curves - median and 90% interval")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "adstock_decay_curves.png"), dpi=130)
    plt.close(fig)

    # ---- saturation curve plot --------------------------------------------------
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.5 * ncol, 3 * nrow),
                             squeeze=False)
    for j, ch in enumerate(tch):
        ax = axes[j // ncol][j % ncol]
        ch_ranges = ranges[ranges["channel"] == ch.name]
        x_max = max(ch_ranges["adstocked_scaled_max"].max() * 1.1, 0.1)
        xg = np.linspace(0, x_max, 80)
        # hill over the grid for every draw: (S, len(xg))
        xs = (xg[None, :] + 1e-9) ** sl_d[:, j][:, None]
        ecs = (ec_d[:, j] ** sl_d[:, j])[:, None]
        h = xs / (xs + ecs)
        ax.plot(xg, np.median(h, axis=0))
        ax.fill_between(xg, np.percentile(h, 5, axis=0),
                        np.percentile(h, 95, axis=0), alpha=0.25)
        ax.axvline(np.median(ec_d[:, j]), color="green", ls="--", lw=1,
                   label="ec (half-saturation)")
        ax.axvline(ch_ranges["adstocked_scaled_median_active"].median(),
                   color="grey", ls=":", lw=1, label="median activity")
        ax.axvline(ch_ranges["adstocked_scaled_p95"].max(), color="grey",
                   ls="--", lw=1, label="p95 activity")
        ax.set_title(ch.name, fontsize=9)
        ax.set_xlabel("adstocked media (scaled units)", fontsize=8)
        ax.set_ylabel("saturation (0-1)", fontsize=8)
        if j == 0:
            ax.legend(fontsize=7)
    for k in range(C, nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("Learned saturation (Hill) curves with observed activity range")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "saturation_curves.png"), dpi=130)
    plt.close(fig)

    print(f"[transforms] parameter + range report -> {outdir}")
    return params
