"""Post-fit outputs for the full MMM: coefficients, fit quality, contributions, ROI.

All numbers are computed from posterior draws (median + percentile intervals +
P(direction)); the transform-parameter report lives in transform_report.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

from config import ModelConfig, RunConfig
from data_prep import PanelData
from transforms import adstock_weights_np, apply_adstock_np, hill_np


def stack_posterior(idata, n_draws: int | None = None, seed: int = 0):
    post = idata.posterior.stack(sample=("chain", "draw"))
    total = post.sizes["sample"]
    if n_draws and n_draws < total:
        rng = np.random.default_rng(seed)
        sel = np.sort(rng.choice(total, n_draws, replace=False))
        post = post.isel(sample=sel)
    return post


def _stats(x: np.ndarray, hdi=0.90) -> dict:
    lo, hi = np.percentile(x, [(1 - hdi) / 2 * 100, (1 + hdi) / 2 * 100])
    return {"mean": float(np.mean(x)), "sd": float(np.std(x)),
            "median": float(np.median(x)),
            "hdi_5": float(lo), "hdi_95": float(hi),
            "prob_positive": float((x > 0).mean()),
            "excludes_zero": bool(lo > 0 or hi < 0)}


def beta_draws_all(post, p: PanelData) -> tuple[dict, dict]:
    """Returns (regional, population): name -> (S,G) and name -> (S,)."""
    reg_d, pop_d = {}, {}
    if "beta_media" in post:
        arr = post["beta_media"].transpose("sample", "region", "channel").values
        pop = post["pop_beta_media"].transpose("sample", "channel").values
        for j, name in enumerate(p.channel_names):
            reg_d[name] = arr[:, :, j]
            pop_d[name] = pop[:, j]
    for bname, specs in p.buckets.items():
        if not specs or f"beta_{bname}" not in post:
            continue
        arr = post[f"beta_{bname}"].transpose("sample", "region",
                                              f"feat_{bname}").values
        for j, s in enumerate(specs):
            reg_d[s.name] = arr[:, :, j]
        if f"pop_beta_{bname}" in post:
            pop = post[f"pop_beta_{bname}"].transpose("sample",
                                                      f"feat_{bname}").values
            for j, s in enumerate(specs):
                pop_d[s.name] = pop[:, j]
    return reg_d, pop_d


# --------------------------------------------------------------------------
# decomposition (replays the learned transforms in numpy)
# --------------------------------------------------------------------------
@dataclass
class Decomposition:
    yhat_draws: np.ndarray        # (S,G,T) original units
    baseline_draws: np.ndarray    # (S,G,T)
    contrib_median: dict          # name -> (G,T) original units
    contrib_totals: dict          # name -> (S,G)
    baseline_totals: np.ndarray   # (S,G)


def compute_decomposition(idata, p: PanelData, cfg: ModelConfig,
                          n_draws: int = 300, seed: int = 0) -> Decomposition:
    post = stack_posterior(idata, n_draws, seed)
    S = post.sizes["sample"]
    G, T = p.n_regions, p.T

    alpha = post["alpha_region"].transpose("sample", "region").values
    base = np.broadcast_to(alpha[:, :, None], (S, G, T)).copy()
    if p.X_fourier is not None and "beta_fourier" in post:
        bf = post["beta_fourier"].transpose("sample", "fourier").values
        base += (bf @ p.X_fourier.T)[:, None, :]
    if "beta_trend_region" in post:
        btr = post["beta_trend_region"].transpose("sample", "region").values
        base += btr[:, :, None] * p.t[None, None, :]

    yhat = base.copy()
    contrib_median, contrib_totals = {}, {}
    ysc = p.y_scale

    # media channels: replay adstock -> Hill with the posterior draws
    tch = cfg.transformed_channels
    if tch:
        a_d = post["adstock_alpha"].transpose("sample", "channel").values
        th_d = post["adstock_theta"].transpose("sample", "channel").values
        ec_d = post["hill_ec"].transpose("sample", "channel").values
        sl_d = post["hill_slope"].transpose("sample", "channel").values
        bm_d = post["beta_media"].transpose("sample", "region", "channel").values
        for j, ch in enumerate(tch):
            w = adstock_weights_np(a_d[:, j], th_d[:, j], p.L,
                                   ch.adstock == "delayed", ch.max_lag)
            ads = apply_adstock_np(p.Mlag[:, :, :, j], w)          # (S,G,T)
            sat = hill_np(ads, ec_d[:, j], sl_d[:, j])
            c = bm_d[:, :, j][:, :, None] * sat
            yhat += c
            c_o = c * ysc[None, :, None]
            contrib_median[ch.name] = np.median(c_o, axis=0)
            contrib_totals[ch.name] = c_o.sum(axis=2)
            del ads, sat, c, c_o

    # linear features
    reg_d, _ = beta_draws_all(post, p)
    for name in p.lin_names:
        if name not in reg_d:
            continue
        k = p.lin_index[name]
        c = reg_d[name][:, :, None] * p.X_lin[:, :, k][None, :, :]
        yhat += c
        c_o = c * ysc[None, :, None]
        contrib_median[name] = np.median(c_o, axis=0)
        contrib_totals[name] = c_o.sum(axis=2)
        del c, c_o

    yhat_o = yhat * ysc[None, :, None] + p.y_mean[None, :, None]
    base_o = base * ysc[None, :, None] + p.y_mean[None, :, None]
    return Decomposition(yhat_o, base_o, contrib_median, contrib_totals,
                         base_o.sum(axis=2))


# --------------------------------------------------------------------------
# coefficient report
# --------------------------------------------------------------------------
def coefficient_report(idata, p: PanelData, outdir: str,
                       make_forest_plots: bool = True) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    post = stack_posterior(idata)
    reg_d, pop_d = beta_draws_all(post, p)
    rows = []
    for name, arr in reg_d.items():
        if name in pop_d:
            rows.append({"feature": name, "region": "__population__",
                         **_stats(pop_d[name])})
        for g, r in enumerate(p.region_names):
            rows.append({"feature": name, "region": r, **_stats(arr[:, g])})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "coefficient_report.csv"), index=False)

    if make_forest_plots:
        fdir = os.path.join(outdir, "forest")
        os.makedirs(fdir, exist_ok=True)
        for name, arr in reg_d.items():
            med = np.median(arr, axis=0)
            lo, hi = np.percentile(arr, 5, axis=0), np.percentile(arr, 95, axis=0)
            order = np.argsort(med)
            fig, ax = plt.subplots(figsize=(6, max(2.5, 0.3 * len(order) + 1)))
            ypos = np.arange(len(order))
            ax.errorbar(med[order], ypos,
                        xerr=[med[order] - lo[order], hi[order] - med[order]],
                        fmt="o", ms=4, lw=1.2, capsize=2)
            ax.set_yticks(ypos)
            ax.set_yticklabels([p.region_names[i] for i in order], fontsize=8)
            if name in pop_d:
                ax.axvline(np.median(pop_d[name]), color="firebrick", ls="--",
                           lw=1, label="population median")
                ax.legend(fontsize=8)
            ax.axvline(0, color="grey", lw=0.8)
            ax.set_title(f"{name} - regional coefficients (median, 90% interval)",
                         fontsize=10)
            fig.tight_layout()
            fig.savefig(os.path.join(fdir, f"{name}.png"), dpi=130)
            plt.close(fig)
    return df


# --------------------------------------------------------------------------
# fit quality
# --------------------------------------------------------------------------
def _metrics(actual, pred_med, lo, hi) -> dict:
    resid = actual - pred_med
    nz = actual != 0
    sst = ((actual - actual.mean()) ** 2).sum()
    return {"r2": float(1 - (resid ** 2).sum() / sst) if sst > 0 else np.nan,
            "mape_pct": float(np.mean(np.abs(resid[nz] / actual[nz])) * 100)
            if nz.any() else np.nan,
            "wmape_pct": float(np.abs(resid).sum() / np.abs(actual).sum() * 100),
            "mae": float(np.abs(resid).mean()),
            "coverage_90_pct": float(np.mean((actual >= lo) & (actual <= hi)) * 100),
            "n": int(actual.size)}


def fit_report(decomp: Decomposition, p: PanelData, outdir: str) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    med = np.median(decomp.yhat_draws, axis=0)     # (G,T)
    lo = np.percentile(decomp.yhat_draws, 5, axis=0)
    hi = np.percentile(decomp.yhat_draws, 95, axis=0)

    tr = np.arange(p.T) < p.T_train
    rows = []
    datasets = [("train", tr)] + ([("test", ~tr)] if (~tr).any() else [])
    for which, m in datasets:
        rows.append({"region": "__all__", "dataset": which,
                     **_metrics(p.y_orig[:, m].ravel(), med[:, m].ravel(),
                                lo[:, m].ravel(), hi[:, m].ravel())})
        for g, r in enumerate(p.region_names):
            rows.append({"region": r, "dataset": which,
                         **_metrics(p.y_orig[g, m], med[g, m], lo[g, m], hi[g, m])})
    dfm = pd.DataFrame(rows)
    dfm.to_csv(os.path.join(outdir, "fit_metrics.csv"), index=False)

    G = p.n_regions
    ncol = min(3, G)
    nrow = int(np.ceil(G / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.5 * ncol, 2.8 * nrow),
                             squeeze=False, sharex=True)
    for g, r in enumerate(p.region_names):
        ax = axes[g // ncol][g % ncol]
        ax.plot(p.dates, p.y_orig[g], color="black", lw=1, label="actual")
        ax.plot(p.dates, med[g], color="tab:blue", lw=1, label="fitted")
        ax.fill_between(p.dates, lo[g], hi[g], color="tab:blue", alpha=0.2)
        if p.T_train < p.T:
            ax.axvspan(p.dates[p.T_train], p.dates[-1], alpha=0.12, color="orange")
        ax.set_title(str(r), fontsize=9)
        if g == 0:
            ax.legend(fontsize=8)
    for k in range(G, nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("Actual vs fitted (90% interval; orange = holdout)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "actual_vs_fitted.png"), dpi=130)
    plt.close(fig)

    resid = (p.y_orig - med).ravel()
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
    axes[0].scatter(med.ravel(), resid, s=6, alpha=0.4)
    axes[0].axhline(0, color="grey", lw=0.8)
    axes[0].set_xlabel("fitted")
    axes[0].set_ylabel("residual")
    axes[1].hist(resid, bins=40)
    axes[1].set_title("residual distribution")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "residuals.png"), dpi=130)
    plt.close(fig)
    return dfm


# --------------------------------------------------------------------------
# contributions + ROI
# --------------------------------------------------------------------------
def contribution_report(decomp: Decomposition, p: PanelData, cfg: ModelConfig,
                        run_cfg: RunConfig, outdir: str,
                        top_n: int = 20) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    G = p.n_regions
    actual_by_region = p.y_orig.sum(axis=1)
    total_actual = actual_by_region.sum()

    rows = []
    items = list(decomp.contrib_totals.items()) + [("__baseline__",
                                                    decomp.baseline_totals)]
    for name, tot in items:
        port = tot.sum(axis=1)
        rows.append({"feature": name, "region": "__portfolio__", **_stats(port),
                     "share_of_actual_pct": float(np.median(port) / total_actual * 100)})
        for g, r in enumerate(p.region_names):
            rows.append({"feature": name, "region": r, **_stats(tot[:, g]),
                         "share_of_actual_pct":
                             float(np.median(tot[:, g]) / actual_by_region[g] * 100)})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "contribution_totals.csv"), index=False)

    port = df[(df["region"] == "__portfolio__") & (df["feature"] != "__baseline__")]
    port = port.reindex(port["median"].abs().sort_values(ascending=False).index).head(top_n)
    fig, ax = plt.subplots(figsize=(8, max(3, 0.35 * len(port) + 1)))
    ypos = np.arange(len(port))
    ax.barh(ypos, port["median"],
            xerr=[port["median"] - port["hdi_5"], port["hdi_95"] - port["median"]],
            capsize=2, color=np.where(port["median"] >= 0, "tab:blue", "tab:red"))
    ax.set_yticks(ypos)
    ax.set_yticklabels(port["feature"], fontsize=8)
    ax.invert_yaxis()
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_title("Total contribution by feature (median, 90% interval)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "contribution_bars.png"), dpi=130)
    plt.close(fig)

    # weekly portfolio decomposition
    weekly = pd.DataFrame(index=p.dates)
    weekly["baseline"] = np.median(decomp.baseline_draws, axis=0).sum(axis=0)
    for name, gm in decomp.contrib_median.items():
        weekly[name] = gm.sum(axis=0)
    feats = [c for c in weekly.columns if c != "baseline"]
    pos = [c for c in feats if weekly[c].sum() >= 0]
    neg = [c for c in feats if weekly[c].sum() < 0]
    fig, ax = plt.subplots(figsize=(11, 5))
    stack_cols = ["baseline"] + pos
    ax.stackplot(weekly.index, [weekly[c].clip(lower=0) for c in stack_cols],
                 labels=stack_cols, alpha=0.85)
    if neg:
        ax.stackplot(weekly.index, [weekly[c].clip(upper=0) for c in neg],
                     labels=neg, alpha=0.85)
    ax.plot(p.dates, p.y_orig.sum(axis=0), color="black", lw=1.2, label="actual")
    ax.legend(fontsize=7, ncol=3)
    ax.set_title("Portfolio decomposition (posterior-median contributions)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "decomposition_area.png"), dpi=130)
    plt.close(fig)

    # ROI for channels with spend
    roi_rows = []
    for ch in cfg.channels:
        if ch.name not in p.spend or ch.name not in decomp.contrib_totals:
            continue
        spend_g = p.spend[ch.name].sum(axis=1)           # (G,)
        tot = decomp.contrib_totals[ch.name] * run_cfg.revenue_per_unit
        if spend_g.sum() > 0:
            roi_rows.append({"channel": ch.name, "region": "__portfolio__",
                             "spend": float(spend_g.sum()),
                             **{f"roi_{k}": v for k, v in
                                _stats(tot.sum(axis=1) / spend_g.sum()).items()}})
        for g, r in enumerate(p.region_names):
            if spend_g[g] > 0:
                roi_rows.append({"channel": ch.name, "region": r,
                                 "spend": float(spend_g[g]),
                                 **{f"roi_{k}": v for k, v in
                                    _stats(tot[:, g] / spend_g[g]).items()}})
    if roi_rows:
        roi = pd.DataFrame(roi_rows)
        roi.to_csv(os.path.join(outdir, "roi_report.csv"), index=False)
        pr = roi[roi["region"] == "__portfolio__"]
        fig, ax = plt.subplots(figsize=(7, max(2.5, 0.4 * len(pr) + 1)))
        ypos = np.arange(len(pr))
        ax.barh(ypos, pr["roi_median"],
                xerr=[pr["roi_median"] - pr["roi_hdi_5"],
                      pr["roi_hdi_95"] - pr["roi_median"]], capsize=2)
        ax.set_yticks(ypos)
        ax.set_yticklabels(pr["channel"], fontsize=9)
        ax.axvline(1.0, color="firebrick", ls="--", lw=1, label="break-even")
        ax.legend(fontsize=8)
        ax.set_title("Channel ROI (median, 90% interval)"
                     + ("" if run_cfg.revenue_per_unit == 1.0
                        else f"  [revenue_per_unit={run_cfg.revenue_per_unit}]"))
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, "roi_bars.png"), dpi=130)
        plt.close(fig)
    return df


def prior_predictive_plot(idata, p: PanelData, outdir: str) -> None:
    if not hasattr(idata, "prior_predictive") or "y_obs" not in idata.prior_predictive:
        return
    os.makedirs(outdir, exist_ok=True)
    pp = idata.prior_predictive["y_obs"].values.reshape(-1)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.hist(pp, bins=60, density=True, alpha=0.5, label="prior predictive (scaled)")
    ax.hist(p.y[:, :p.T_train].ravel(), bins=60, density=True, alpha=0.5,
            label="actual (scaled)")
    ax.legend(fontsize=8)
    ax.set_title("Prior predictive check - KPI scale")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "prior_predictive_check.png"), dpi=130)
    plt.close(fig)
