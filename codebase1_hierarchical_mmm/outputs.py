"""Post-fit outputs: coefficients, fit quality, contribution decomposition.

Everything is computed from posterior DRAWS (never from combined summaries):
point estimate = posterior median, uncertainty = HDI-style percentile interval,
"significance" = P(effect > 0) and whether the interval excludes zero.
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
from data_prep import PreparedData


# --------------------------------------------------------------------------
# posterior access helpers
# --------------------------------------------------------------------------
def stack_posterior(idata, n_draws: int | None = None, seed: int = 0):
    post = idata.posterior.stack(sample=("chain", "draw"))
    total = post.sizes["sample"]
    if n_draws and n_draws < total:
        rng = np.random.default_rng(seed)
        sel = np.sort(rng.choice(total, n_draws, replace=False))
        post = post.isel(sample=sel)
    return post


def beta_draws_by_feature(post, pdata: PreparedData) -> dict[str, np.ndarray]:
    """feature name -> array (n_samples, n_regions), sign already applied."""
    out = {}
    for bname, specs in pdata.buckets.items():
        if not specs or f"beta_{bname}" not in post:
            continue
        arr = post[f"beta_{bname}"].transpose("sample", "region", f"feat_{bname}").values
        for j, s in enumerate(specs):
            out[s.name] = arr[:, :, j]
    return out


def population_draws_by_feature(post, pdata: PreparedData) -> dict[str, np.ndarray]:
    """feature name -> (n_samples,) population-level effect (median for signed)."""
    out = {}
    for bname, specs in pdata.buckets.items():
        if not specs:
            continue
        if f"pop_beta_{bname}" in post:
            arr = post[f"pop_beta_{bname}"].transpose("sample", f"feat_{bname}").values
            for j, s in enumerate(specs):
                out[s.name] = arr[:, j]
        elif f"beta_{bname}" in post:  # global bucket: same in every region
            arr = post[f"beta_{bname}"].transpose("sample", "region", f"feat_{bname}").values
            for j, s in enumerate(specs):
                out[s.name] = arr[:, 0, j]
    return out


# --------------------------------------------------------------------------
# decomposition
# --------------------------------------------------------------------------
@dataclass
class Decomposition:
    yhat_draws: np.ndarray          # (S, n_obs) original units
    baseline_draws: np.ndarray      # (S, n_obs) original units
    contrib_median: dict            # feature -> (n_obs,) original units
    contrib_totals: dict            # feature -> (S, G) totals in original units
    baseline_totals: np.ndarray     # (S, G)


def compute_decomposition(idata, pdata: PreparedData, cfg: ModelConfig,
                          n_draws: int = 400, seed: int = 0) -> Decomposition:
    post = stack_posterior(idata, n_draws, seed)
    reg = pdata.region_idx
    G = len(pdata.region_names)
    S = post.sizes["sample"]

    alpha = post["alpha_region"].transpose("sample", "region").values
    base = alpha[:, reg]
    if pdata.X_fourier is not None and "beta_fourier" in post:
        bf = post["beta_fourier"].transpose("sample", "fourier").values
        base = base + bf @ pdata.X_fourier.T
    if "beta_trend_region" in post:
        btr = post["beta_trend_region"].transpose("sample", "region").values
        base = base + btr[:, reg] * pdata.t[None, :]

    yhat = base.copy()
    contrib_median, contrib_totals = {}, {}
    sg = pdata.y_scale[reg]

    for name, b in beta_draws_by_feature(post, pdata).items():
        j = pdata.feature_index[name]
        c = b[:, reg] * pdata.X[:, j][None, :]
        yhat += c
        c_o = c * sg[None, :]
        contrib_median[name] = np.median(c_o, axis=0)
        tot = np.zeros((S, G))
        for g in range(G):
            tot[:, g] = c_o[:, reg == g].sum(axis=1)
        contrib_totals[name] = tot
        del c, c_o

    yhat_o = yhat * sg[None, :] + pdata.y_mean[reg][None, :]
    base_o = base * sg[None, :] + pdata.y_mean[reg][None, :]
    base_totals = np.zeros((S, G))
    for g in range(G):
        base_totals[:, g] = base_o[:, reg == g].sum(axis=1)
    return Decomposition(yhat_o, base_o, contrib_median, contrib_totals, base_totals)


# --------------------------------------------------------------------------
# coefficient report
# --------------------------------------------------------------------------
def _stats(x: np.ndarray, hdi=0.90) -> dict:
    lo, hi = np.percentile(x, [(1 - hdi) / 2 * 100, (1 + hdi) / 2 * 100])
    return {"mean": float(np.mean(x)), "sd": float(np.std(x)),
            "median": float(np.median(x)),
            f"hdi_{int((1-hdi)/2*100)}": float(lo),
            f"hdi_{int((1+hdi)/2*100)}": float(hi),
            "prob_positive": float((x > 0).mean()),
            "excludes_zero": bool(lo > 0 or hi < 0)}


def coefficient_report(idata, pdata: PreparedData, outdir: str,
                       make_forest_plots: bool = True) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    post = stack_posterior(idata)
    bd = beta_draws_by_feature(post, pdata)
    popd = population_draws_by_feature(post, pdata)

    rows = []
    for name, arr in bd.items():
        if name in popd:
            rows.append({"feature": name, "region": "__population__", **_stats(popd[name])})
        for g, r in enumerate(pdata.region_names):
            rows.append({"feature": name, "region": r, **_stats(arr[:, g])})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "coefficient_report.csv"), index=False)

    if make_forest_plots:
        fdir = os.path.join(outdir, "forest")
        os.makedirs(fdir, exist_ok=True)
        for name, arr in bd.items():
            med = np.median(arr, axis=0)
            lo = np.percentile(arr, 5, axis=0)
            hi = np.percentile(arr, 95, axis=0)
            order = np.argsort(med)
            fig, ax = plt.subplots(figsize=(6, max(2.5, 0.3 * len(order) + 1)))
            ypos = np.arange(len(order))
            ax.errorbar(med[order], ypos,
                        xerr=[med[order] - lo[order], hi[order] - med[order]],
                        fmt="o", ms=4, lw=1.2, capsize=2)
            ax.set_yticks(ypos)
            ax.set_yticklabels([pdata.region_names[i] for i in order], fontsize=8)
            if name in popd:
                ax.axvline(np.median(popd[name]), color="firebrick", ls="--", lw=1,
                           label="population median")
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
    return {
        "r2": float(1 - (resid ** 2).sum() / sst) if sst > 0 else np.nan,
        "mape_pct": float(np.mean(np.abs(resid[nz] / actual[nz])) * 100) if nz.any() else np.nan,
        "wmape_pct": float(np.abs(resid).sum() / np.abs(actual).sum() * 100),
        "mae": float(np.abs(resid).mean()),
        "coverage_90_pct": float(np.mean((actual >= lo) & (actual <= hi)) * 100),
        "n": int(len(actual)),
    }


def fit_report(decomp: Decomposition, pdata: PreparedData, outdir: str) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    med = np.median(decomp.yhat_draws, axis=0)
    lo = np.percentile(decomp.yhat_draws, 5, axis=0)
    hi = np.percentile(decomp.yhat_draws, 95, axis=0)

    rows = []
    datasets = ["train"] + (["test"] if pdata.test_mask.any() else [])
    for which in datasets:
        m = pdata.sel(which)
        rows.append({"region": "__all__", "dataset": which,
                     **_metrics(pdata.y_orig[m], med[m], lo[m], hi[m])})
        for g, r in enumerate(pdata.region_names):
            mg = m & (pdata.region_idx == g)
            if mg.any():
                rows.append({"region": r, "dataset": which,
                             **_metrics(pdata.y_orig[mg], med[mg], lo[mg], hi[mg])})
    dfm = pd.DataFrame(rows)
    dfm.to_csv(os.path.join(outdir, "fit_metrics.csv"), index=False)

    # actual vs fitted, grid of regions
    G = len(pdata.region_names)
    ncol = min(3, G)
    nrow = int(np.ceil(G / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.5 * ncol, 2.8 * nrow),
                             squeeze=False, sharex=True)
    for g, r in enumerate(pdata.region_names):
        ax = axes[g // ncol][g % ncol]
        mg = pdata.region_idx == g
        ax.plot(pdata.dates[mg], pdata.y_orig[mg], color="black", lw=1, label="actual")
        ax.plot(pdata.dates[mg], med[mg], color="tab:blue", lw=1, label="fitted")
        ax.fill_between(pdata.dates[mg], lo[mg], hi[mg], color="tab:blue", alpha=0.2)
        mt = mg & pdata.test_mask
        if mt.any():
            ax.axvspan(pdata.dates[mt].min(), pdata.dates[mt].max(),
                       alpha=0.12, color="orange")
        ax.set_title(str(r), fontsize=9)
        if g == 0:
            ax.legend(fontsize=8)
    for k in range(G, nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("Actual vs fitted (90% interval; orange = holdout)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "actual_vs_fitted.png"), dpi=130)
    plt.close(fig)

    # residual diagnostics
    resid = pdata.y_orig - med
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
    axes[0].scatter(med, resid, s=6, alpha=0.4)
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
# contributions
# --------------------------------------------------------------------------
def contribution_report(decomp: Decomposition, pdata: PreparedData, outdir: str,
                        top_n: int = 20) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    G = len(pdata.region_names)
    actual_by_region = np.array([pdata.y_orig[pdata.region_idx == g].sum()
                                 for g in range(G)])
    total_actual = actual_by_region.sum()

    rows = []
    items = list(decomp.contrib_totals.items()) + [("__baseline__", decomp.baseline_totals)]
    for name, tot in items:
        port = tot.sum(axis=1)  # (S,)
        rows.append({"feature": name, "region": "__portfolio__", **_stats(port),
                     "share_of_actual_pct": float(np.median(port) / total_actual * 100)})
        for g, r in enumerate(pdata.region_names):
            rows.append({"feature": name, "region": r, **_stats(tot[:, g]),
                         "share_of_actual_pct":
                             float(np.median(tot[:, g]) / actual_by_region[g] * 100)})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "contribution_totals.csv"), index=False)

    # portfolio bar chart with uncertainty
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

    # portfolio weekly decomposition (median contributions, summed over regions)
    dts = pd.Series(pdata.dates.values)
    agg = pd.DataFrame({"date": dts})
    agg["baseline"] = np.median(decomp.baseline_draws, axis=0)
    for name, series in decomp.contrib_median.items():
        agg[name] = series
    weekly = agg.groupby("date").sum()
    feats = [c for c in weekly.columns if c != "baseline"]
    pos = [c for c in feats if weekly[c].sum() >= 0]
    neg = [c for c in feats if weekly[c].sum() < 0]
    actual_weekly = pd.Series(pdata.y_orig, index=dts).groupby(level=0).sum()

    fig, ax = plt.subplots(figsize=(11, 5))
    stack_cols = ["baseline"] + pos
    ax.stackplot(weekly.index, [weekly[c].clip(lower=0) for c in stack_cols],
                 labels=stack_cols, alpha=0.85)
    if neg:
        ax.stackplot(weekly.index, [weekly[c].clip(upper=0) for c in neg],
                     labels=neg, alpha=0.85)
    ax.plot(actual_weekly.index, actual_weekly.values, color="black", lw=1.2,
            label="actual")
    ax.legend(fontsize=7, ncol=3)
    ax.set_title("Portfolio decomposition (posterior-median contributions)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "decomposition_area.png"), dpi=130)
    plt.close(fig)
    return df


def prior_predictive_plot(idata, pdata: PreparedData, outdir: str) -> None:
    """Sanity check: does the model *before seeing data* generate KPI values on
    the right scale? (Meridian: prior sampling step.)"""
    if not hasattr(idata, "prior_predictive") or "y_obs" not in idata.prior_predictive:
        return
    os.makedirs(outdir, exist_ok=True)
    pp = idata.prior_predictive["y_obs"].values.reshape(-1)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.hist(pp, bins=60, density=True, alpha=0.5, label="prior predictive (scaled)")
    ax.hist(pdata.y[pdata.train_mask], bins=60, density=True, alpha=0.5,
            label="actual (scaled)")
    ax.legend(fontsize=8)
    ax.set_title("Prior predictive check - KPI scale")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "prior_predictive_check.png"), dpi=130)
    plt.close(fig)
