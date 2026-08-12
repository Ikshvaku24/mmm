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

import math
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


def save_fig(fig, path: str, dpi: int = 130) -> None:
    """Robust savefig: ensure the directory exists, retry once (the Databricks
    /Workspace filesystem can transiently fail rapid PNG writes), and on final
    failure warn-and-continue - a plot must never kill a finished fit."""
    import time as _time
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for attempt in (1, 2):
        try:
            fig.savefig(path, dpi=dpi)
            break
        except OSError as e:
            if attempt == 2:
                print(f"[outputs] WARNING: could not save {path}: {e}")
            else:
                _time.sleep(0.5)
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    plt.close(fig)


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


def _two_sided_p(t: float, dof: int) -> float:
    """Two-sided p-value for a t statistic (normal approximation without SciPy)."""
    if not np.isfinite(t):
        return np.nan
    try:
        from scipy import stats
        return float(2.0 * stats.t.sf(abs(t), dof))
    except Exception:  # noqa: BLE001
        return float(math.erfc(abs(t) / math.sqrt(2.0)))


def _signif_stats(x: np.ndarray) -> dict:
    """Bayesian analogues of the classical t statistic and p value.

    t_stat  = posterior mean / posterior SD. NOTE the denominator: the old
              production code used mcse_mean, which shrinks as you draw more
              samples, so "significance" inflated simply by sampling longer.
              The posterior SD is a property of the posterior, not of how long
              the sampler ran.
    p_value = 2 * min(P(effect > 0), P(effect < 0)) - the posterior probability
              of the sign being wrong, doubled for a two-sided reading. It is
              the direct Bayesian counterpart of a two-sided p value, not a
              frequentist test: read it as "how sure are we of the direction?".
    """
    sd = float(np.std(x))
    mean = float(np.mean(x))
    p_pos = float((x > 0).mean())
    return {"t_stat": float(mean / sd) if sd > 0 else np.nan,
            "p_value": float(2.0 * min(p_pos, 1.0 - p_pos))}


def _resid_stats(resid: np.ndarray) -> dict:
    """Residual diagnostics for the fit-metrics table.

    resid_t_stat / resid_p_value  one-sample t test of H0: mean residual = 0,
                                  i.e. "is the model biased over this window?".
                                  A small p value means a systematic over- or
                                  under-prediction, not a good model.
    durbin_watson                 2 = no residual autocorrelation, < 1.5 =
                                  positive autocorrelation (missing trend or
                                  seasonality), > 2.5 = negative.
    """
    out = {"resid_t_stat": np.nan, "resid_p_value": np.nan,
           "durbin_watson": np.nan}
    n = len(resid)
    if n < 2:
        return out
    sd = float(np.std(resid, ddof=1))
    if sd > 0:
        t = float(np.mean(resid) / (sd / np.sqrt(n)))
        out["resid_t_stat"] = t
        out["resid_p_value"] = _two_sided_p(t, n - 1)
    denom = float(np.sum(resid ** 2))
    if denom > 0:
        out["durbin_watson"] = float(np.sum(np.diff(resid) ** 2) / denom)
    return out


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
    """feature name -> (n_samples,) population-level effect (median for signed).

    Only features that HAVE a population level appear: pooled (hierarchical)
    features have an explicit pop_beta, global features share one coefficient
    across regions. Features fitted with pooling="independent" have no
    population parameter at all - reporting one region's draws as "the
    population" would invent a level the model never estimated - so they are
    omitted and get no __population__ row.
    """
    out = {}
    for bname, specs in pdata.buckets.items():
        if not specs:
            continue
        if f"pop_beta_{bname}" in post:          # hierarchical
            arr = post[f"pop_beta_{bname}"].transpose("sample", f"feat_{bname}").values
            for j, s in enumerate(specs):
                out[s.name] = arr[:, j]
        elif specs[0].pooling == "global" and f"beta_{bname}" in post:
            arr = post[f"beta_{bname}"].transpose("sample", "region", f"feat_{bname}").values
            for j, s in enumerate(specs):
                out[s.name] = arr[:, 0, j]      # identical in every region
    return out


# --------------------------------------------------------------------------
# decomposition
# --------------------------------------------------------------------------
@dataclass
class Decomposition:
    """Sales decomposition, in original KPI units.

    BASELINE = core (region intercept + seasonality + trend) + every feature
    flagged `baseline=1` (always-on business drivers such as distribution and
    price, which are not switchable marketing levers). Those features keep their
    own entries in contrib_* as well, so the baseline can be EXPANDED into its
    parts without double counting:

        baseline_draws  = core_draws + sum(contributions of baseline features)
        yhat_draws      = baseline_draws + sum(contributions of the rest)
    """
    yhat_draws: np.ndarray          # (S, n_obs) mean response, original units
    ypred_draws: np.ndarray         # (S, n_obs) posterior PREDICTIVE, original units
    baseline_draws: np.ndarray      # (S, n_obs) full baseline (core + baseline feats)
    contrib_median: dict            # feature -> (n_obs,) original units
    contrib_totals: dict            # feature -> (S, G) totals in original units
    baseline_totals: np.ndarray     # (S, G) full baseline totals
    core_draws: np.ndarray = None       # (S, n_obs) intercept + seasonality + trend
    core_totals: np.ndarray = None      # (S, G)
    baseline_features: tuple = ()       # names folded into the baseline


def compute_decomposition(idata, pdata: PreparedData, cfg: ModelConfig,
                          n_draws: int = 400, seed: int = 0) -> Decomposition:
    post = stack_posterior(idata, n_draws, seed)
    reg = pdata.region_idx
    G = len(pdata.region_names)
    S = post.sizes["sample"]

    alpha = post["alpha_region"].transpose("sample", "region").values
    core = alpha[:, reg]
    if pdata.X_fourier is not None and "beta_fourier" in post:
        bf = post["beta_fourier"].transpose("sample", "fourier").values
        core = core + bf @ pdata.X_fourier.T
    if "beta_trend_region" in post:
        btr = post["beta_trend_region"].transpose("sample", "region").values
        core = core + btr[:, reg] * pdata.t[None, :]

    # features flagged baseline=1 belong INSIDE the baseline, not alongside it
    spec_by_name = {s.name: s for specs in pdata.buckets.values() for s in specs}
    scale_tbl = {(r.region, r.feature): r
                 for r in pdata.x_scale_table.itertuples(index=False)}
    base = core.copy()
    yhat = core.copy()
    core_shift = np.zeros_like(core)     # total re-referencing moved out of core
    contrib_median, contrib_totals, baseline_features = {}, {}, []
    sg = pdata.y_scale[reg]

    for name, b in beta_draws_by_feature(post, pdata).items():
        j = pdata.feature_index[name]
        Xj = pdata.X[:, j]
        c = b[:, reg] * Xj[None, :]
        yhat += c                       # the FIT always uses the modelled axis

        # Reporting counterfactual. The model estimates beta on the scaled axis
        # ((x-mu)/sigma for centred features), so its contribution is measured
        # against mu. A due-to decomposition usually wants it measured against
        # zero instead ("what if distribution were 0?"). Shifting the reference
        # is an exact algebraic restatement of the SAME fit:
        #     beta*(x-ref)/sigma = beta*(x-mu)/sigma + beta*(mu-ref)/sigma
        # so whatever is added here is subtracted from the core below and the
        # decomposition still sums to the fitted value.
        shift = np.zeros(len(Xj))
        spec = spec_by_name.get(name)
        if spec is not None and spec.contribution_reference != "auto":
            for g in range(G):
                info = scale_tbl[(pdata.region_names[g], name)]
                mu_g, sd_g = float(info.center), float(info.scale)
                m_all = reg == g
                ref = spec.contribution_reference
                if ref == "mean":
                    ref_v = mu_g if info.method == "center_scale" else float(
                        (Xj[m_all & pdata.train_mask] * sd_g + mu_g).mean())
                elif ref == "zero":
                    ref_v = 0.0
                elif ref == "min":
                    ref_v = float((Xj[m_all & pdata.train_mask] * sd_g + mu_g).min())
                else:
                    ref_v = float(ref)
                shift[m_all] = (mu_g - ref_v) / sd_g
        if np.any(shift):
            adj = b[:, reg] * shift[None, :]
            c = c + adj
            core_shift += adj              # removed from the core after the loop

        if spec is not None and spec.baseline:
            base = base + c
            baseline_features.append(name)
        c_o = c * sg[None, :]
        contrib_median[name] = np.median(c_o, axis=0)
        tot = np.zeros((S, G))
        for g in range(G):
            tot[:, g] = c_o[:, reg == g].sum(axis=1)
        contrib_totals[name] = tot
        del c, c_o

    # whatever the re-referencing added to the features comes out of the core,
    # so core + baseline features + incremental features still equals the fit
    core = core - core_shift
    base = base - core_shift

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
    # the intercept carries the KPI mean, so it belongs to the CORE; baseline
    # features are added on top of it already in original units
    core_o = core * sg[None, :] + pdata.y_mean[reg][None, :]
    base_o = base * sg[None, :] + pdata.y_mean[reg][None, :]
    base_totals = np.zeros((S, G))
    core_totals = np.zeros((S, G))
    for g in range(G):
        base_totals[:, g] = base_o[:, reg == g].sum(axis=1)
        core_totals[:, g] = core_o[:, reg == g].sum(axis=1)
    return Decomposition(yhat_o, ypred_o, base_o, contrib_median, contrib_totals,
                         base_totals, core_draws=core_o, core_totals=core_totals,
                         baseline_features=tuple(baseline_features))


# --------------------------------------------------------------------------
# coefficient report
# --------------------------------------------------------------------------
def _support_flag(sign: str, n_active: int, sd: float, method: str,
                  near_constant_sd: float = 0.1) -> str:
    # n_active counts activity on the RAW column, so it is meaningful for free
    # features too (a sparse event dummy is thinly supported whatever its sign).
    if sd == 0 or n_active == 0:
        return "none"
    if n_active < 8:
        return "weak"
    # Always on, but the level is already spanned by the region intercept, so
    # only the tiny residual wiggle identifies the coefficient. n_active alone
    # would call this "adequate" and hide a non-identified parameter.
    if method == "scale_only" and sd < near_constant_sd:
        return "weak (near-constant)"
    return "adequate"


def coefficient_report(idata, pdata: PreparedData, outdir: str,
                       make_forest_plots: bool = True) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    post = stack_posterior(idata)
    bd = beta_draws_by_feature(post, pdata)
    popd = population_draws_by_feature(post, pdata)

    spec_by_name = {s.name: s for specs in pdata.buckets.values() for s in specs}
    tbl = {(r.region, r.feature): r
           for r in pdata.x_scale_table.itertuples(index=False)}

    rows = []
    for name, arr in bd.items():
        sign = spec_by_name[name].sign
        constrained = sign != "free"
        if name in popd:
            row = {"feature": name, "region": "__population__",
                   **_stats(popd[name]), **_signif_stats(popd[name]),
                   "units": "scaled (population level)",
                   "sign_constrained": constrained,
                   "data_support": ""}
            if constrained:
                # beta = +/-exp(.) can never cross zero, so these columns are
                # true BY CONSTRUCTION and say nothing about evidence - blank them
                # rather than let them be read as significance
                row["prob_positive"] = ""
                row["excludes_zero"] = ""
                row["p_value"] = ""
            rows.append(row)
        for g, r in enumerate(pdata.region_names):
            info = tbl[(r, name)]
            # activity counted on the RAW column: after centring every scaled
            # value is non-zero, so counting non-zero X would report full support
            # for a feature that ran in one week out of ninety-one
            n_active = int(info.n_active_train)
            sd = float(pdata.X[(pdata.region_idx == g) & pdata.train_mask,
                               pdata.feature_index[name]].std())
            # original-unit conversion: beta_orig = beta_scaled * s_y_g / s_x_gj
            factor = pdata.y_scale[g] / info.scale
            orig = arr[:, g] * factor
            o_lo, o_hi = _hdi(orig)
            row = {
                "feature": name, "region": r, **_stats(arr[:, g]),
                **_signif_stats(arr[:, g]),
                "units": "scaled (per-region axes)",
                "sign_constrained": constrained,
                "median_orig_units": float(np.median(orig)),
                "hdi_low_orig_units": o_lo, "hdi_high_orig_units": o_hi,
                "orig_units_meaning": "KPI units per raw feature unit",
                "n_active_train": n_active,
                "feature_sd_train": round(sd, 4),
                "scaling_method": info.method,
                "data_support": _support_flag(sign, n_active, sd, info.method),
            }
            if constrained:
                row["prob_positive"] = ""
                row["excludes_zero"] = ""
                row["p_value"] = ""
            rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "coefficient_report.csv"), index=False)

    weak = df[df["data_support"].astype(str).str.startswith(("none", "weak"))]
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
            save_fig(fig, os.path.join(fdir, f"{name}.png"))
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
    out.update(_resid_stats(resid))
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
        # The pooled r2 above measures against ONE grand mean, so it is inflated
        # by the (large, trivially predictable) differences in level between
        # regions - it can sit at 0.99 while every region is fitted poorly.
        # r2_within_region uses each region's own mean as the null model, which
        # is the honest "does the model explain movement?" number.
        sse = float(((pdata.y_orig[m] - med[m]) ** 2).sum())
        sst_w = 0.0
        for g in range(len(pdata.region_names)):
            mg = m & (pdata.region_idx == g)
            if mg.any():
                sst_w += float(((pdata.y_orig[mg] - pdata.y_orig[mg].mean()) ** 2).sum())
        all_row["r2_within_region"] = float(1 - sse / sst_w) if sst_w > 0 else np.nan
        # Durbin-Watson needs a single time series. The pooled mask concatenates
        # regions, so successive differences would straddle region boundaries -
        # average the honest per-region values instead.
        dws = [rr["durbin_watson"] for rr in region_rows
               if np.isfinite(rr.get("durbin_watson", np.nan))]
        all_row["durbin_watson"] = float(np.mean(dws)) if dws else np.nan
        w = np.asarray(weights, dtype=float)
        mp = np.array([rr["mape_pct"] for rr in region_rows], dtype=float)
        ok = np.isfinite(mp) & (w > 0)
        all_row["mape_region_weighted_pct"] = (
            float((mp[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else np.nan)
        rows.append(all_row)

        # __aggregate__: sum regions to ONE national series per date, then score.
        # This is the like-for-like comparison against a national total-sales
        # model. It always looks better than the per-region rows because summing
        # cancels the idiosyncratic noise of individual regions - quote it only
        # next to them, never instead of them.
        dts = pd.Series(pdata.dates.values)[m].to_numpy()
        order = np.unique(dts)
        pick = {d: np.where(dts == d)[0] for d in order}
        act_agg = np.array([pdata.y_orig[m][ix].sum() for ix in pick.values()])
        med_agg = np.array([med[m][ix].sum() for ix in pick.values()])
        pred_agg = np.column_stack(
            [decomp.ypred_draws[:, m][:, ix].sum(axis=1) for ix in pick.values()])
        mean_agg = np.column_stack(
            [decomp.yhat_draws[:, m][:, ix].sum(axis=1) for ix in pick.values()])
        rows.append({"region": "__aggregate__", "dataset": which,
                     **_metrics(act_agg, med_agg,
                                np.percentile(mean_agg, 5, axis=0),
                                np.percentile(mean_agg, 95, axis=0),
                                np.percentile(pred_agg, 5, axis=0),
                                np.percentile(pred_agg, 95, axis=0), pred_agg)})
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
    save_fig(fig, os.path.join(outdir, "actual_vs_fitted.png"))

    resid = pdata.y_orig - med
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
    axes[0].scatter(med, resid, s=6, alpha=0.4)
    axes[0].axhline(0, color="grey", lw=0.8)
    axes[0].set_xlabel("fitted")
    axes[0].set_ylabel("residual")
    axes[1].hist(resid, bins=40)
    axes[1].set_title("residual distribution")
    fig.tight_layout()
    save_fig(fig, os.path.join(outdir, "residuals.png"))
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

    spec_by_name = {s.name: s for specs in pdata.buckets.values() for s in specs}
    method_by_feature = {r.feature: r.method
                         for r in pdata.x_scale_table.itertuples(index=False)}

    base_feats = set(decomp.baseline_features)

    # Row set, and how the parts add up:
    #   __baseline__       = __baseline_core__ + every baseline feature
    #   total sales        = __baseline__ + every incremental feature
    # Baseline features therefore appear TWICE - once inside __baseline__ and
    # once on their own row - which is what makes the baseline expandable. The
    # `group` column says which block a row belongs to, so nothing is summed
    # twice by accident.
    items = [("__baseline__", decomp.baseline_totals, "baseline_total")]
    if base_feats and decomp.core_totals is not None:
        # only worth a row when there is something to expand: with no baseline
        # features the core IS the baseline
        items.append(("__baseline_core__", decomp.core_totals, "baseline_part"))
    items += [(n, t, "baseline_part" if n in base_feats else "incremental")
              for n, t in decomp.contrib_totals.items()]

    rows = []
    for name, tot, group in items:
        spec = spec_by_name.get(name)
        constrained = "" if spec is None else spec.sign != "free"
        # A CENTRED feature contributes deviations from its own average level, not
        # an increment over zero: a positive coefficient can still show a negative
        # total (most weeks sit below the average). Say so, or the sign of the
        # contribution reads as a contradiction of the sign of the coefficient.
        vs = ("" if spec is None else
              ("feature average" if method_by_feature.get(name) == "center_scale"
               else "zero"))
        port = tot.sum(axis=1)  # (S,)
        for region, vals, denom in (
                [("__portfolio__", port, total_actual)]
                + [(r, tot[:, g], actual_by_region[g])
                   for g, r in enumerate(pdata.region_names)]):
            row = {"feature": name, "region": region, "group": group,
                   "pillar": ("Baseline" if spec is None or spec.baseline
                              else (spec.pillar or "")),
                   **_stats(vals),
                   "sign_constrained": constrained, "contribution_vs": vs,
                   "share_of_actual_pct": float(np.median(vals) / denom * 100)}
            if constrained is True:
                row["prob_positive"] = ""      # vacuous under a sign constraint
                row["excludes_zero"] = ""
            rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "contribution_totals.csv"), index=False)

    # ---- pillar roll-up (the format vendor decks report in) -----------------
    # __baseline__ carries the whole base, so roll up baseline_total + the
    # incremental features and skip baseline_part rows to avoid double counting.
    roll = df[df["group"].isin(["baseline_total", "incremental"])].copy()
    roll["pillar"] = roll["pillar"].replace("", "Unassigned")
    pil = (roll.groupby(["pillar", "region"])[["median", "share_of_actual_pct"]]
           .sum().reset_index()
           .sort_values(["region", "share_of_actual_pct"], ascending=[True, False]))
    pil.to_csv(os.path.join(outdir, "contribution_by_pillar.csv"), index=False)

    # portfolio bar chart with uncertainty: incremental drivers only, since the
    # baseline parts are not switchable levers and dwarf everything else
    port = df[(df["region"] == "__portfolio__") & (df["group"] == "incremental")]
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
    ax.set_title("Incremental contribution by feature (median, 90% HDI)")
    fig.tight_layout()
    save_fig(fig, os.path.join(outdir, "contribution_bars.png"))

    # what the baseline is made of (only when something was folded into it)
    if base_feats:
        bp = df[(df["region"] == "__portfolio__")
                & (df["group"] == "baseline_part")]
        bp = bp.reindex(bp["median"].abs().sort_values(ascending=False).index)
        fig, ax = plt.subplots(figsize=(8, max(2.5, 0.4 * len(bp) + 1)))
        ypos = np.arange(len(bp))
        ax.barh(ypos, bp["median"],
                xerr=[bp["median"] - bp["hdi_low"],
                      bp["hdi_high"] - bp["median"]],
                capsize=2,
                color=np.where(bp["median"] >= 0, "tab:green", "tab:red"))
        ax.set_yticks(ypos)
        ax.set_yticklabels(bp["feature"], fontsize=8)
        ax.invert_yaxis()
        ax.axvline(0, color="grey", lw=0.8)
        ax.set_title("Baseline expanded: what makes up the base "
                     "(median, 90% HDI)")
        fig.tight_layout()
        save_fig(fig, os.path.join(outdir, "baseline_breakdown.png"))

    # ---- portfolio weekly decomposition -------------------------------------
    # Two versions: "collapsed" shows the baseline as one block (the business
    # view: base vs what marketing added); "expanded" opens the baseline into
    # core + baseline features. Baseline features are excluded from the
    # collapsed stack because they are already inside the baseline block.
    dts = pd.Series(pdata.dates.values)
    actual_weekly = pd.Series(pdata.y_orig, index=dts).groupby(level=0).sum()
    incremental = [n for n in decomp.contrib_median if n not in base_feats]

    def _stack(base_col: str, base_series: np.ndarray, feats: list,
               title: str, fname: str) -> None:
        agg = pd.DataFrame({"date": dts})
        agg[base_col] = base_series
        for n in feats:
            agg[n] = decomp.contrib_median[n]
        weekly = agg.groupby("date").sum()
        cols = [c for c in weekly.columns if c != base_col]
        pos = [c for c in cols if weekly[c].sum() >= 0]
        neg = [c for c in cols if weekly[c].sum() < 0]

        fig, ax = plt.subplots(figsize=(11, 5))
        stack_cols = [base_col] + pos
        ax.stackplot(weekly.index, [weekly[c].clip(lower=0) for c in stack_cols],
                     labels=stack_cols, alpha=0.85)
        if neg:
            ax.stackplot(weekly.index, [weekly[c].clip(upper=0) for c in neg],
                         labels=neg, alpha=0.85)
        ax.plot(actual_weekly.index, actual_weekly.values, color="black", lw=1.2,
                label="actual")
        ax.legend(fontsize=7, ncol=3)
        ax.set_title(title)
        fig.tight_layout()
        save_fig(fig, os.path.join(outdir, fname))

    _stack("baseline", np.median(decomp.baseline_draws, axis=0), incremental,
           "Portfolio decomposition (posterior-median contributions)",
           "decomposition_area.png")
    if base_feats and decomp.core_draws is not None:
        _stack("baseline_core", np.median(decomp.core_draws, axis=0),
               list(decomp.contrib_median),
               "Portfolio decomposition, baseline expanded "
               "(core = intercept + seasonality + trend)",
               "decomposition_area_expanded.png")
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
    save_fig(fig, os.path.join(outdir, "prior_predictive_check.png"))
