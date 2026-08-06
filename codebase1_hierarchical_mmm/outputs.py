"""Post-fit outputs: coefficients, fit quality, contribution decomposition.

Everything is computed from posterior DRAWS (never from combined summaries):
  point estimate  = posterior median
  uncertainty     = true 90% HDI (shortest interval, not equal-tail percentiles)
  "significance"  = P(effect > 0) and whether the HDI excludes zero

Two kinds of interval are reported and must not be confused:
  mean-response interval        uncertainty about the EXPECTED sales level
  posterior predictive interval adds likelihood noise - use THIS for holdout
                                coverage and "where will next week actually land"

Coefficient units: the model works on scaled axes (KPI standardised per region,
features scaled per region), so scaled coefficients are comparable across
regions in "relative response" terms. The report also converts each regional
coefficient back to ORIGINAL units (KPI units per raw feature unit), where
equal scaled effects generally differ - both are exported, explicitly labelled.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

from compat import get_group, has_group
from config import ModelConfig, RunConfig
from data_prep import PreparedData


# --------------------------------------------------------------------------
# posterior access helpers
# --------------------------------------------------------------------------
def stack_posterior(idata, n_draws: int | None = None, seed: int = 0):
    post = get_group(idata, "posterior").stack(sample=("chain", "draw"))
    total = post.sizes["sample"]
    if n_draws and n_draws < total:
        rng = np.random.default_rng(seed)
        sel = np.sort(rng.choice(total, n_draws, replace=False))
        post = post.isel(sample=sel)
    return post


def _hdi(x: np.ndarray, prob: float = 0.90) -> tuple[float, float]:
    """Shortest-interval (highest-density) interval from draws."""
    x = np.sort(np.asarray(x, dtype=float))
    n = len(x)
    m = max(1, int(np.floor(prob * n)))
    if m >= n:
        return float(x[0]), float(x[-1])
    widths = x[m:] - x[: n - m]
    i = int(np.argmin(widths))
    return float(x[i]), float(x[i + m])


def _stats(x: np.ndarray, prob: float = 0.90) -> dict:
    lo, hi = _hdi(x, prob)
    return {"mean": float(np.mean(x)), "sd": float(np.std(x)),
            "median": float(np.median(x)),
            "hdi_low": lo, "hdi_high": hi,
            "prob_positive": float((x > 0).mean()),
            "excludes_zero": bool(lo > 0 or hi < 0)}


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
    yhat_draws: np.ndarray          # (S, n_obs) mean response, original units
    ypred_draws: np.ndarray         # (S, n_obs) posterior PREDICTIVE, original units
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

    # posterior predictive draws: add likelihood noise (sigma_g, and nu for
    # Student-t). This is what holdout coverage must be judged against.
    rng = np.random.default_rng(seed + 1)
    sig = post["sigma_region"].transpose("sample", "region").values[:, reg]
    if "nu" in post:
        nu = post["nu"].transpose("sample").values[:, None]
        noise = rng.standard_t(df=np.broadcast_to(nu, yhat.shape)) * sig
    else:
        noise = rng.normal(size=yhat.shape) * sig
    ypred = yhat + noise

    yhat_o = yhat * sg[None, :] + pdata.y_mean[reg][None, :]
    ypred_o = ypred * sg[None, :] + pdata.y_mean[reg][None, :]
    base_o = base * sg[None, :] + pdata.y_mean[reg][None, :]
    base_totals = np.zeros((S, G))
    for g in range(G):
        base_totals[:, g] = base_o[:, reg == g].sum(axis=1)
    return Decomposition(yhat_o, ypred_o, base_o, contrib_median, contrib_totals,
                         base_totals)


# --------------------------------------------------------------------------
# coefficient report
# --------------------------------------------------------------------------
def _support_flag(sign: str, n_active: int, sd: float) -> str:
    if sd == 0 or (sign != "free" and n_active == 0):
        return "none"
    if sign != "free" and n_active < 8:
        return "weak"
    return "adequate"


def coefficient_report(idata, pdata: PreparedData, outdir: str,
                       make_forest_plots: bool = True) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    post = stack_posterior(idata)
    bd = beta_draws_by_feature(post, pdata)
    popd = population_draws_by_feature(post, pdata)

    spec_by_name = {s.name: s for specs in pdata.buckets.values() for s in specs}
    x_scale = {(r.region, r.feature): r.scale
               for r in pdata.x_scale_table.itertuples(index=False)}

    rows = []
    for name, arr in bd.items():
        sign = spec_by_name[name].sign
        if name in popd:
            rows.append({"feature": name, "region": "__population__",
                         **_stats(popd[name]),
                         "units": "scaled (population level)",
                         "data_support": ""})
        for g, r in enumerate(pdata.region_names):
            m = (pdata.region_idx == g) & pdata.train_mask
            col = pdata.X[m, pdata.feature_index[name]]
            n_active = int((col != 0).sum())
            sd = float(col.std())
            # original-unit conversion: beta_orig = beta_scaled * s_y_g / s_x_gj
            factor = pdata.y_scale[g] / x_scale[(r, name)]
            orig = arr[:, g] * factor
            o_lo, o_hi = _hdi(orig)
            rows.append({
                "feature": name, "region": r, **_stats(arr[:, g]),
                "units": "scaled (per-region axes)",
                "median_orig_units": float(np.median(orig)),
                "hdi_low_orig_units": o_lo, "hdi_high_orig_units": o_hi,
                "orig_units_meaning": "KPI units per raw feature unit",
                "n_active_train": n_active,
                "feature_sd_train": round(sd, 4),
                "data_support": _support_flag(sign, n_active, sd),
            })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "coefficient_report.csv"), index=False)

    weak = df[df["data_support"].isin(["none", "weak"])]
    if len(weak):
        with open(os.path.join(outdir, "support_warnings.txt"), "w") as f:
            f.write("Region x feature combinations with little or no data support.\n"
                    "These coefficients are driven by the population hierarchy "
                    "(shrinkage), NOT learned from that region's data - do not "
                    "present them as regionally estimated.\n\n")
            f.write(weak[["feature", "region", "n_active_train",
                          "data_support"]].to_string(index=False))

    if make_forest_plots:
        fdir = os.path.join(outdir, "forest")
        os.makedirs(fdir, exist_ok=True)
        for name, arr in bd.items():
            med = np.median(arr, axis=0)
            lo = np.array([_hdi(arr[:, g])[0] for g in range(arr.shape[1])])
            hi = np.array([_hdi(arr[:, g])[1] for g in range(arr.shape[1])])
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
            ax.set_title(f"{name} - regional coefficients, scaled axes "
                         "(median, 90% HDI)", fontsize=10)
            fig.tight_layout()
            fig.savefig(os.path.join(fdir, f"{name}.png"), dpi=130)
            plt.close(fig)
    return df


# --------------------------------------------------------------------------
# fit quality
# --------------------------------------------------------------------------
def _crps(pred_draws: np.ndarray, actual: np.ndarray) -> float:
    """Sample-based CRPS (proper scoring rule: calibration AND sharpness),
    averaged over observations. Lower is better; units = KPI units.
    pred_draws (S, n) posterior predictive draws; actual (n,)."""
    S = pred_draws.shape[0]
    term1 = np.mean(np.abs(pred_draws - actual[None, :]))
    x = np.sort(pred_draws, axis=0)
    i = np.arange(1, S + 1)[:, None]
    term2 = float(np.mean(np.sum((2 * i - S - 1) * x, axis=0))) / (S * S)
    return float(term1 - term2)


def _metrics(actual, pred_med, m_lo, m_hi, p_lo, p_hi,
             pred_draws: np.ndarray | None = None) -> dict:
    resid = actual - pred_med
    nz = actual != 0
    sst = ((actual - actual.mean()) ** 2).sum()
    out = {
        "r2": float(1 - (resid ** 2).sum() / sst) if sst > 0 else np.nan,
        "mape_pct": float(np.mean(np.abs(resid[nz] / actual[nz])) * 100) if nz.any() else np.nan,
        "wmape_pct": float(np.abs(resid).sum() / np.abs(actual).sum() * 100),
        "mae": float(np.abs(resid).mean()),
        "coverage_90_mean_pct": float(np.mean((actual >= m_lo) & (actual <= m_hi)) * 100),
        "coverage_90_pred_pct": float(np.mean((actual >= p_lo) & (actual <= p_hi)) * 100),
        "n": int(len(actual)),
    }
    if pred_draws is not None:
        out["crps"] = _crps(pred_draws, actual)
    return out


def compute_fit_metrics(decomp: Decomposition, pdata: PreparedData) -> pd.DataFrame:
    """Metrics table (no files/plots) - reused by fit_report and by CV folds.

    Two weighted aggregates, answering different questions (PE-package design):
      wmape_pct                 observation-weighted: "what fraction of total
                                volume does the total error represent?"
      mape_region_weighted_pct  volume-weighted average of per-region MAPEs:
                                "average regional error, prioritising big regions"
    """
    med = np.median(decomp.yhat_draws, axis=0)
    m_lo = np.percentile(decomp.yhat_draws, 5, axis=0)
    m_hi = np.percentile(decomp.yhat_draws, 95, axis=0)
    p_lo = np.percentile(decomp.ypred_draws, 5, axis=0)
    p_hi = np.percentile(decomp.ypred_draws, 95, axis=0)

    rows = []
    datasets = ["train"] + (["test"] if pdata.test_mask.any() else [])
    for which in datasets:
        m = pdata.sel(which)
        region_rows, weights = [], []
        for g, r in enumerate(pdata.region_names):
            mg = m & (pdata.region_idx == g)
            if mg.any():
                region_rows.append(
                    {"region": r, "dataset": which,
                     **_metrics(pdata.y_orig[mg], med[mg], m_lo[mg], m_hi[mg],
                                p_lo[mg], p_hi[mg], decomp.ypred_draws[:, mg])})
                weights.append(pdata.y_orig[mg].sum())
        all_row = {"region": "__all__", "dataset": which,
                   **_metrics(pdata.y_orig[m], med[m], m_lo[m], m_hi[m],
                              p_lo[m], p_hi[m], decomp.ypred_draws[:, m])}
        w = np.asarray(weights, dtype=float)
        mp = np.array([rr["mape_pct"] for rr in region_rows], dtype=float)
        ok = np.isfinite(mp) & (w > 0)
        all_row["mape_region_weighted_pct"] = (
            float((mp[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else np.nan)
        rows.append(all_row)
        rows.extend(region_rows)
    return pd.DataFrame(rows)


def fit_report(decomp: Decomposition, pdata: PreparedData, outdir: str) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    med = np.median(decomp.yhat_draws, axis=0)
    m_lo = np.percentile(decomp.yhat_draws, 5, axis=0)
    m_hi = np.percentile(decomp.yhat_draws, 95, axis=0)
    p_lo = np.percentile(decomp.ypred_draws, 5, axis=0)
    p_hi = np.percentile(decomp.ypred_draws, 95, axis=0)

    dfm = compute_fit_metrics(decomp, pdata)
    dfm.to_csv(os.path.join(outdir, "fit_metrics.csv"), index=False)
    # judge holdout by coverage_90_pred_pct (predictive). coverage_90_mean_pct
    # is the mean-response interval and is EXPECTED to be well below 90%.

    G = len(pdata.region_names)
    ncol = min(3, G)
    nrow = int(np.ceil(G / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.5 * ncol, 2.8 * nrow),
                             squeeze=False, sharex=True)
    for g, r in enumerate(pdata.region_names):
        ax = axes[g // ncol][g % ncol]
        mg = pdata.region_idx == g
        ax.fill_between(pdata.dates[mg], p_lo[mg], p_hi[mg], color="tab:blue",
                        alpha=0.12, label="90% predictive")
        ax.fill_between(pdata.dates[mg], m_lo[mg], m_hi[mg], color="tab:blue",
                        alpha=0.30, label="90% mean response")
        ax.plot(pdata.dates[mg], pdata.y_orig[mg], color="black", lw=1, label="actual")
        ax.plot(pdata.dates[mg], med[mg], color="tab:blue", lw=1, label="fitted")
        mt = mg & pdata.test_mask
        if mt.any():
            ax.axvspan(pdata.dates[mt].min(), pdata.dates[mt].max(),
                       alpha=0.12, color="orange")
        ax.set_title(str(r), fontsize=9)
        if g == 0:
            ax.legend(fontsize=7)
    for k in range(G, nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("Actual vs fitted (orange = holdout)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "actual_vs_fitted.png"), dpi=130)
    plt.close(fig)

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
            xerr=[port["median"] - port["hdi_low"],
                  port["hdi_high"] - port["median"]],
            capsize=2, color=np.where(port["median"] >= 0, "tab:blue", "tab:red"))
    ax.set_yticks(ypos)
    ax.set_yticklabels(port["feature"], fontsize=8)
    ax.invert_yaxis()
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_title("Total contribution by feature (median, 90% HDI)")
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
    if not has_group(idata, "prior_predictive"):
        return
    ppd = get_group(idata, "prior_predictive")
    if "y_obs" not in ppd:
        return
    os.makedirs(outdir, exist_ok=True)
    pp = ppd["y_obs"].values.reshape(-1)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.hist(pp, bins=60, density=True, alpha=0.5, label="prior predictive (scaled)")
    ax.hist(pdata.y[pdata.train_mask], bins=60, density=True, alpha=0.5,
            label="actual (scaled)")
    ax.legend(fontsize=8)
    ax.set_title("Prior predictive check - KPI scale")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "prior_predictive_check.png"), dpi=130)
    plt.close(fig)
