"""Collinearity and the regression assumptions, checked against THIS model.

Until now nothing in this pipeline measured collinearity. The `near_constant_sd`
guard caught one special case - a feature so flat it duplicates the intercept -
and nothing caught the general case: two features that move together, whose
coefficients are then only identified as a sum. That is the failure mode behind
"+91% / -97% contributions that cancel out", and it is invisible in every
reconciliation check because the sum is right.

Two things matter about HOW it is measured here.

**Against the model's own design matrix, not the raw data.** The columns the
sampler sees are the scaled features PLUS the intercept, the Fourier
seasonality block and the trend. A correlation matrix of the raw features
cannot see a feature that duplicates the intercept, or a promo flag that is
really just December. So the design is rebuilt exactly as `model.build_model`
assembles it - including `include_intercept` - and per region, because scaling
and coefficients are both per region.

**Uncentred, deliberately.** Belsley's convention is to scale each column to
unit length but NOT to centre it, precisely because centring removes the
intercept's involvement - which is the collinearity we most need to see here.

Pre-fit vs post-fit
-------------------
Both are needed and they answer different questions.

* **Pre-fit** (VIF, condition index, pairwise |r|) is a property of the DATA.
  It says the likelihood alone cannot separate these columns.
* **Post-fit** (posterior correlation between coefficient draws) is a property
  of the FITTED MODEL. It says whether the model, priors included, ended up
  unable to separate them.

They disagree in a way that matters: a tight prior can pin two collinear
coefficients so they look fine post-fit while the data still says nothing about
either. High VIF + low posterior correlation + low `contraction` is exactly that
- the priors are doing the work and the split between the two features is an
assumption you made, not a finding.

The other assumptions
---------------------
Bayesian linear regression rests on the same structural assumptions as OLS -
they just show up as model misspecification rather than invalid standard errors:

  1. correct functional form (linearity in the transformed inputs)
  2. no harmful collinearity
  3. residuals independent (no autocorrelation) - the big one for weekly panels
  4. homoscedastic residuals (or a model that admits heteroscedasticity)
  5. residuals follow the assumed likelihood (Normal, or Student-t)
  6. no dominating influential observations
  7. exogeneity - regressors uncorrelated with the error. NOT testable from the
     residuals; it is an argument about how the data was generated, and it is
     what media-mix models most often get wrong (spend follows expected sales).
  8. convergence and calibration - covered by 02_convergence and the coverage
     columns in 04_fit.

`residual_assumptions` reports 1-6 with a verdict and a fix for each. 7 is
printed as a standing caveat because no statistic can settle it.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

# thresholds, in one place so the report and the docs cannot drift
VIF_WARN, VIF_BAD = 5.0, 10.0
COND_WARN, COND_BAD = 10.0, 30.0        # Belsley condition index
PAIR_WARN, PAIR_BAD = 0.8, 0.95         # |correlation| between design columns
POST_WARN, POST_BAD = 0.7, 0.9          # |correlation| between coefficient draws
DW_LO, DW_HI = 1.5, 2.5                 # Durbin-Watson acceptable band


# --------------------------------------------------------------------------- #
# the design matrix, exactly as the model sees it
# --------------------------------------------------------------------------- #
def design_matrix(pdata, model_cfg, region: int | None = None,
                  train_only: bool = True):
    """(M, names) for one region - every column the sampler actually gets.

    Order matches `model.build_model`: intercept, Fourier, trend, features.
    """
    m = pdata.train_mask if train_only else np.ones(len(pdata.y), dtype=bool)
    if region is not None:
        m = m & (pdata.region_idx == region)
    cols, names = [], []
    if getattr(model_cfg, "include_intercept", True):
        cols.append(np.ones(int(m.sum())))
        names.append("__intercept__")
    if pdata.X_fourier is not None and len(getattr(pdata, "fourier_names", [])):
        for k, fn in enumerate(pdata.fourier_names):
            cols.append(pdata.X_fourier[m, k])
            names.append(f"__fourier__{fn}")
    if getattr(model_cfg, "include_trend", False):
        cols.append(np.asarray(pdata.t)[m])
        names.append("__trend__")
    for name in pdata.feature_names:
        cols.append(pdata.X[m, pdata.feature_index[name]])
        names.append(name)
    if not cols:
        return np.zeros((int(m.sum()), 0)), []
    return np.column_stack(cols).astype(float), names


def _unit_length(M: np.ndarray) -> np.ndarray:
    """Scale each column to unit length WITHOUT centring (Belsley)."""
    norm = np.sqrt((M ** 2).sum(axis=0))
    norm[norm <= 0] = 1.0
    return M / norm


def _aux_r2(y: np.ndarray, others: np.ndarray, centred: bool) -> float:
    """R^2 of y on `others`. Centred = the textbook VIF; uncentred keeps the mean."""
    n = len(y)
    sst = float(((y - y.mean()) ** 2).sum()) if centred else float((y ** 2).sum())
    if others.size == 0 or sst <= 0 or n <= others.shape[1]:
        return np.nan
    try:
        beta, *_ = np.linalg.lstsq(others, y, rcond=None)
        r2 = 1.0 - float(((y - others @ beta) ** 2).sum()) / sst
    except np.linalg.LinAlgError:
        r2 = 1.0
    return min(max(r2, 0.0), 1.0 - 1e-12)


def vif(M: np.ndarray, names: list, has_intercept: bool) -> pd.DataFrame:
    """Variance inflation per column - reported TWO ways, on purpose.

    `vif` is the textbook one: R^2 of the column against the others with the
    mean removed. It is what the published thresholds (5, 10) refer to.

    `vif_uncentred` keeps the mean in. This matters here because **the centred
    VIF is structurally blind to a column that duplicates the intercept**: an
    always-on feature scaled to ~1.0 every period has almost no centred
    variance, the auxiliary regression explains almost none of it, and the
    textbook VIF comes back at ~1.1 - looking perfect - while the design is
    near-singular. On exactly that case the centred VIF read 1.09 and the
    Belsley condition number read 23,000. It is the defect that broke
    real_data_v1, so it gets its own column rather than a footnote.

    Read them together: a high `vif` means the feature duplicates other
    FEATURES; a high `vif_uncentred` with a low `vif` means it duplicates the
    LEVEL, i.e. the intercept. The `duplicates` column says which.
    """
    rows = []
    n = M.shape[0]
    for j, name in enumerate(names):
        if name == "__intercept__":
            continue
        y = M[:, j]
        others = np.delete(M, j, axis=1)
        # The auxiliary regression gets a constant ONLY if the model has one.
        # Adding one unconditionally reports features as duplicating an
        # intercept that does not exist under include_intercept=False.
        aux = others if not has_intercept or "__intercept__" in names             else np.column_stack([np.ones(n), others])
        r2_c = _aux_r2(y, aux, centred=True)
        r2_u = _aux_r2(y, aux, centred=False)
        v_c = (1.0 / (1.0 - r2_c)) if np.isfinite(r2_c) else np.nan
        v_u = (1.0 / (1.0 - r2_u)) if np.isfinite(r2_u) else np.nan
        if np.isfinite(v_c) and v_c > VIF_BAD:
            dup = "other features"
        elif has_intercept and np.isfinite(v_u) and v_u > VIF_BAD:
            dup = "the intercept/level"
        elif np.isfinite(v_u) and v_u > VIF_BAD:
            dup = "a shared constant level"
        else:
            dup = ""
        rows.append({"column": name, "r2_vs_others": r2_c, "vif": v_c,
                     "vif_uncentred": v_u, "duplicates": dup})
    return pd.DataFrame(rows)


def condition_index(M: np.ndarray) -> tuple[float, np.ndarray]:
    """Belsley condition number and per-dimension indices of the scaled design."""
    if M.size == 0 or M.shape[1] == 0:
        return float("nan"), np.array([])
    sv = np.linalg.svd(_unit_length(M), compute_uv=False)
    sv = sv[sv > 0]
    if not len(sv):
        return float("inf"), np.array([])
    return float(sv.max() / sv.min()), sv.max() / sv


def correlation_pairs(M: np.ndarray, names: list,
                      threshold: float = PAIR_WARN) -> pd.DataFrame:
    """Design columns whose |correlation| exceeds `threshold`."""
    if M.shape[1] < 2:
        return pd.DataFrame(columns=["column_a", "column_b", "correlation"])
    sd = M.std(axis=0)
    live = sd > 0
    C = np.corrcoef(M[:, live], rowvar=False)
    idx = np.where(live)[0]
    rows = []
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            r = float(C[a, b])
            if abs(r) >= threshold:
                rows.append({"column_a": names[idx[a]], "column_b": names[idx[b]],
                             "correlation": r})
    return (pd.DataFrame(rows).sort_values(
        "correlation", key=lambda s: s.abs(), ascending=False)
        if rows else pd.DataFrame(columns=["column_a", "column_b", "correlation"]))


def collinearity(pdata, model_cfg) -> dict:
    """Pre-fit collinearity, per region, on the model's own design."""
    has_i = bool(getattr(model_cfg, "include_intercept", True))
    vifs, pairs, summary = [], [], []
    for g, rname in enumerate(pdata.region_names):
        M, names = design_matrix(pdata, model_cfg, region=g)
        if M.shape[0] <= 2:
            continue
        v = vif(M, names, has_i)
        v.insert(0, "region", rname)
        vifs.append(v)
        p = correlation_pairs(M, names)
        if len(p):
            p.insert(0, "region", rname)
            pairs.append(p)
        cond, _ = condition_index(M)
        mx = float(v["vif"].max()) if len(v) and v["vif"].notna().any() else 0.0
        mxu = (float(v["vif_uncentred"].max())
               if len(v) and v["vif_uncentred"].notna().any() else 0.0)
        worst = v.loc[v["vif"].idxmax()] if v["vif"].notna().any() else None
        summary.append({
            "region": rname, "n_obs": int(M.shape[0]), "n_columns": len(names),
            "condition_number": cond,
            "max_vif": mx, "max_vif_uncentred": mxu,
            "worst_column": (str(worst["column"]) if worst is not None else ""),
            "n_vif_over_10": int((v["vif"] > VIF_BAD).sum()),
            "n_duplicating_intercept": int(
                (v["duplicates"] == "the intercept/level").sum()),
            "n_pairs_over_0.8": int(len(p)),
            "verdict": _collin_verdict(cond, max(mx, mxu)),
        })
    return {
        "summary": pd.DataFrame(summary),
        "vif": pd.concat(vifs, ignore_index=True) if vifs else pd.DataFrame(),
        "pairs": pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame(),
    }


def _collin_verdict(cond: float, max_vif: float) -> str:
    if not np.isfinite(cond) or cond > COND_BAD or max_vif > VIF_BAD:
        return "severe"
    if cond > COND_WARN or max_vif > VIF_WARN:
        return "moderate"
    return "ok"


# --------------------------------------------------------------------------- #
# post-fit: can the FITTED model separate them?
# --------------------------------------------------------------------------- #
def posterior_correlation(beta_by_feature: dict, region_names: list,
                          threshold: float = POST_WARN) -> pd.DataFrame:
    """|correlation| between coefficient DRAWS, per region.

    This is the check the pre-fit statistics cannot make. Two features whose
    posterior draws are strongly negatively correlated are trading off: the
    model knows their sum and not their split, so neither coefficient - and
    neither contribution - can be read on its own.
    """
    names = list(beta_by_feature)
    rows = []
    for g, rname in enumerate(region_names):
        cols = {n: np.asarray(beta_by_feature[n])[:, g] for n in names}
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                xa, xb = cols[names[a]], cols[names[b]]
                if xa.std() <= 0 or xb.std() <= 0:
                    continue
                r = float(np.corrcoef(xa, xb)[0, 1])
                if abs(r) >= threshold:
                    rows.append({"region": rname, "feature_a": names[a],
                                 "feature_b": names[b], "posterior_corr": r,
                                 "verdict": "severe" if abs(r) >= POST_BAD
                                            else "moderate"})
    return (pd.DataFrame(rows).sort_values(
        "posterior_corr", key=lambda s: s.abs(), ascending=False)
        if rows else pd.DataFrame(columns=["region", "feature_a", "feature_b",
                                           "posterior_corr", "verdict"]))


# --------------------------------------------------------------------------- #
# residual-based assumptions
# --------------------------------------------------------------------------- #
def _dw(e: np.ndarray) -> float:
    return float(((np.diff(e)) ** 2).sum() / (e ** 2).sum()) if len(e) > 1 else np.nan


def _acf(e: np.ndarray, lag: int) -> float:
    if len(e) <= lag:
        return np.nan
    a, b = e[:-lag], e[lag:]
    if a.std() <= 0 or b.std() <= 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def _row(check, stat, threshold, verdict, means, fix):
    return {"check": check, "statistic": stat, "threshold": threshold,
            "verdict": verdict, "what_it_means": means, "what_to_do": fix}


def residual_assumptions(actual: np.ndarray, fitted: np.ndarray,
                         likelihood: str = "normal") -> pd.DataFrame:
    """The classical assumption battery, on one region's residual series."""
    e = np.asarray(actual, dtype=float) - np.asarray(fitted, dtype=float)
    f = np.asarray(fitted, dtype=float)
    n = len(e)
    out = []
    if n < 5:
        return pd.DataFrame(out)
    z = e / (e.std() if e.std() > 0 else 1.0)

    # 1. functional form: residuals should carry no signal in the fitted value
    r_lin = float(np.corrcoef(e, f)[0, 1]) if e.std() > 0 and f.std() > 0 else 0.0
    out.append(_row(
        "linearity", round(r_lin, 4), "|corr(resid, fitted)| < 0.2",
        "ok" if abs(r_lin) < 0.2 else "warn",
        "Residuals should be unrelated to the fitted value. A pattern means the "
        "response is not linear in the transformed inputs.",
        "Revisit the adstock/saturation transforms upstream, or add the missing "
        "driver. A curved residual plot is a functional-form problem, not noise."))

    # 2. homoscedasticity: spread of |e| should not track the level
    # Correlating |e| with the fitted value is a weak test - |e| is itself very
    # noisy, so a real doubling of the error scale can sit under r = 0.3. The
    # ratio of residual sd in the top vs bottom third of fitted values is more
    # powerful and directly interpretable ("errors are 3x larger in peak weeks").
    order = np.argsort(f)
    third = max(2, len(f) // 3)
    lo, hi = e[order[:third]], e[order[-third:]]
    ratio = float(hi.std() / lo.std()) if lo.std() > 0 else np.inf
    out.append(_row(
        "homoscedasticity", round(ratio, 3),
        # 1.5 rather than 2: an error scale that doubles across the range of
        # fitted values only shows up as ~1.7 between tercile MEANS, so a
        # threshold of 2 misses a genuine doubling.
        "sd(resid) top third / bottom third < 1.5",
        "ok" if np.isfinite(ratio) and ratio < 1.5 else "warn",
        "Error size should not grow with the level of sales. If it does, the "
        "single sigma per region is wrong and the intervals are miscalibrated - "
        "too wide in quiet weeks, too narrow in peaks.",
        "Model the KPI on a log scale, or accept that peak-week intervals are "
        "understated. `pool_sigma` does not fix this - it pools ACROSS regions, "
        "not across time."))

    # 3. independence: the assumption weekly panels break most often
    dw = _dw(e)
    ac1 = _acf(e, 1)
    out.append(_row(
        "independence (Durbin-Watson)", round(dw, 3), f"{DW_LO} - {DW_HI}",
        "ok" if DW_LO <= dw <= DW_HI else "warn",
        "2 means no autocorrelation; below 1.5 means consecutive residuals are "
        "positively correlated, so the model is missing something that persists "
        "over time. Every interval is then too narrow, because the effective "
        "sample size is smaller than the row count.",
        "Usually a missing trend, seasonality, or carryover. Try "
        "`include_trend`, a higher `fourier_order`, or longer adstock upstream."))
    for lag in (2, 4, 13):
        a = _acf(e, lag)
        if np.isfinite(a):
            out.append(_row(
                f"autocorrelation lag {lag}", round(a, 4), "|r| < 0.3",
                "ok" if abs(a) < 0.3 else "warn",
                f"Correlation between residuals {lag} periods apart. A spike at "
                "the seasonal lag means the seasonal block is too smooth.",
                "Raise `fourier_order`, or add explicit period dummies."))

    # 4. residual distribution vs the assumed likelihood
    sk = float(((z - z.mean()) ** 3).mean())
    ku = float(((z - z.mean()) ** 4).mean()) - 3.0
    out.append(_row(
        "residual skew", round(sk, 3), "|skew| < 1",
        "ok" if abs(sk) < 1 else "warn",
        "A skewed residual means the model is systematically wrong on one side "
        "- typically under-predicting peaks.",
        "Check for a missing promotional driver; consider modelling log sales."))
    heavy = ku > 1.0 and likelihood != "student_t"
    out.append(_row(
        "residual tails (excess kurtosis)", round(ku, 3),
        "< 1, or likelihood='student_t'",
        "warn" if heavy else "ok",
        "Heavy tails mean a few weeks are far outside what a Normal allows. "
        "Under a Normal likelihood those weeks drag every coefficient toward "
        "themselves.",
        "Set `model.likelihood: student_t`, which downweights them without "
        "deleting any data."))

    # 5. influence
    big = int((np.abs(z) > 3).sum())
    out.append(_row(
        "influential observations", f"{big} of {n} beyond 3sd",
        f"<= {max(1, int(0.003 * n) + 1)} expected",
        "ok" if big <= max(1, int(0.003 * n) + 1) else "warn",
        "Points this far out move coefficients on their own. Under a Normal "
        "likelihood their leverage is unbounded.",
        "Identify them in `04_fit/actual_vs_predicted.csv` (largest "
        "|residual|). If they are real events, model them with a dummy; if they "
        "are data errors, fix the extract. Do not delete them silently."))
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# three checks adopted from Meridian (see docs/MERIDIAN_ASSUMPTIONS.md)
# --------------------------------------------------------------------------- #
# Meridian runs almost no classical residual tests, but it runs three STRUCTURAL
# checks we lacked. All three are cheap and all three address failure modes this
# project has actually hit.
CONFOUND_WARN = 0.1        # Meridian PotentialBiasConfig.correlation_threshold
PPP_FAIL = 0.05            # Meridian BayesianPPPConfig.ppp_threshold
NEG_BASELINE_REVIEW = 0.2  # Meridian BaselineConfig review / fail thresholds
NEG_BASELINE_FAIL = 0.8


def confounding_pairs(pdata, model_cfg,
                      threshold: float = CONFOUND_WARN) -> pd.DataFrame:
    """Correlation between each INCREMENTAL feature and each BASELINE feature.

    Meridian's `PotentialBiasCheck` (analysis/review/checks.py) correlates every
    paid channel against every control and flags anything above 0.1 - a
    deliberately low bar, because this is its structural stand-in for the
    exogeneity assumption no residual statistic can test.

    The logic transfers directly: our `baseline=1` features are the controls
    (distribution, price - confounders you adjust for) and everything else is
    the treatment. A media coefficient correlated with distribution is a
    coefficient that may be absorbing distribution's effect, and no goodness-of-
    fit number will ever say so.

    A flag here is not a defect to fix by editing a prior. It is a statement
    about what the coefficient can mean.
    """
    spec_by_name = {sp.name: sp for specs in pdata.buckets.values() for sp in specs}
    controls = [n for n in pdata.feature_names
                if getattr(spec_by_name.get(n), "baseline", False)]
    treats = [n for n in pdata.feature_names if n not in controls]
    if not controls or not treats:
        return pd.DataFrame(columns=["region", "treatment", "control",
                                     "correlation", "n_obs", "threshold_used",
                                     "flag"])
    rows = []
    for g, rname in enumerate(pdata.region_names):
        m = (pdata.region_idx == g) & pdata.train_mask
        n = int(m.sum())
        if n < 5:
            continue
        # Meridian's flat 0.1 is right for a geo x time panel with thousands of
        # rows. On 91 weeks the sampling sd of a correlation between two
        # unrelated columns is 1/sqrt(91) = 0.105, so a flat 0.1 would flag
        # roughly half of all noise pairs. Take the larger of the Meridian bar
        # and two standard errors, so the check keeps its low intent without
        # becoming a random-number generator on a short panel.
        thr = max(threshold, 2.0 / np.sqrt(n))
        for t in treats:
            xt = pdata.X[m, pdata.feature_index[t]]
            if xt.std() <= 0:
                continue
            for c in controls:
                xc = pdata.X[m, pdata.feature_index[c]]
                if xc.std() <= 0:
                    continue
                r = float(np.corrcoef(xt, xc)[0, 1])
                if abs(r) >= thr:
                    rows.append({"region": rname, "treatment": t, "control": c,
                                 "correlation": round(r, 4),
                                 "n_obs": n, "threshold_used": round(thr, 4),
                                 "flag": "high" if abs(r) >= 0.5 else "review"})
    return (pd.DataFrame(rows).sort_values(
        "correlation", key=lambda x: x.abs(), ascending=False)
        if rows else pd.DataFrame(columns=["region", "treatment", "control",
                                           "correlation", "flag"]))


def posterior_predictive_p(decomp, pdata) -> pd.DataFrame:
    """Aggregate posterior predictive p-value, per region and overall.

    Meridian's `BayesianPPPCheck`:

        ppp = mean(|expected - mean(expected)| >= |actual - mean(expected)|)

    on the TOTAL outcome. It answers a question pointwise coverage cannot: is
    the total plausible under this model at all? A decomposition can have
    perfectly calibrated weekly intervals and still put the annual total in its
    own tail - which is exactly the shape of a reconciles-to-100%-and-is-still-
    wrong failure. Fails below 0.05.
    """
    rows = []
    # Meridian uses the EXPECTED outcome (mean response, no observation noise).
    # We use the posterior PREDICTIVE where it exists, because that is the
    # distribution the actual total is genuinely a draw from - comparing an
    # observed total against a noiseless mean-response spread fails any model
    # whose mean response is sharp, which is most of them.
    draws = getattr(decomp, "ypred_draws", None)
    if draws is None:
        draws = decomp.yhat_draws
    for g, rname in list(enumerate(pdata.region_names)) + [(None, "__all__")]:
        m = pdata.train_mask if g is None else (
            (pdata.region_idx == g) & pdata.train_mask)
        if m.sum() < 3:
            continue
        exp = np.asarray(draws)[:, m].sum(axis=1)
        act = float(pdata.y_orig[m].sum())
        centre = float(exp.mean())
        ppp = float(np.mean(np.abs(exp - centre) >= abs(act - centre)))
        rows.append({"region": rname, "actual_total": act,
                     "expected_total_mean": centre,
                     "ppp": round(ppp, 4),
                     "verdict": "ok" if ppp >= PPP_FAIL else "fail"})
    return pd.DataFrame(rows)


def negative_baseline_probability(decomp, pdata) -> pd.DataFrame:
    """P(total baseline < 0) per region, from the draws we already have.

    Meridian's `BaselineCheck` (review 0.2, fail 0.8). A negative baseline means
    the model says sales would be negative with no marketing, which is not a
    business statement - it is the signature of drivers over-claiming and the
    baseline absorbing the offset. This project has seen exactly that
    (baseline_core strongly negative while baseline features exceeded 100%), so
    it belongs in the automated battery rather than being spotted by eye.
    """
    rows = []
    # a diagnostic must never kill a run: a partial/stubbed decomposition just
    # yields no rows rather than an AttributeError
    base = getattr(decomp, "baseline_totals", None)
    if base is None:
        return pd.DataFrame(columns=["region", "p_negative_baseline", "verdict"])
    for g, rname in enumerate(pdata.region_names):
        pneg = float((np.asarray(base)[:, g] < 0).mean())
        rows.append({
            "region": rname, "p_negative_baseline": round(pneg, 4),
            "median_baseline": float(np.median(np.asarray(base)[:, g])),
            "verdict": ("fail" if pneg >= NEG_BASELINE_FAIL
                        else "review" if pneg >= NEG_BASELINE_REVIEW else "ok")})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# writers
# --------------------------------------------------------------------------- #
def write_collinearity(pdata, model_cfg, outdir: str) -> dict:
    """Pre-fit collinearity files into 01_data/."""
    res = collinearity(pdata, model_cfg)
    os.makedirs(outdir, exist_ok=True)
    res["summary"].to_csv(os.path.join(outdir, "collinearity_summary.csv"),
                          index=False)
    if len(res["vif"]):
        res["vif"].sort_values("vif", ascending=False).to_csv(
            os.path.join(outdir, "collinearity_vif.csv"), index=False)
    res["pairs"].to_csv(os.path.join(outdir, "collinearity_pairs.csv"),
                        index=False)
    bad = res["summary"][res["summary"]["verdict"] != "ok"] \
        if len(res["summary"]) else res["summary"]
    if len(bad):
        print(f"[assumptions] collinearity: {len(bad)} of "
              f"{len(res['summary'])} regions flagged "
              f"(max VIF {res['summary']['max_vif'].max():.1f}) "
              f"-> 01_data/collinearity_summary.csv")
    return res


def write_assumptions(decomp, pdata, outdir: str, model_cfg=None,
                      beta_by_feature: dict | None = None,
                      collin: dict | None = None) -> pd.DataFrame:
    """Post-fit assumption battery + posterior correlation into 04_fit/."""
    os.makedirs(outdir, exist_ok=True)
    lik = getattr(model_cfg, "likelihood", "normal")
    fitted = np.median(decomp.yhat_draws, axis=0)
    frames = []
    for g, rname in enumerate(pdata.region_names):
        m = (pdata.region_idx == g) & pdata.train_mask
        if m.sum() < 5:
            continue
        t = residual_assumptions(pdata.y_orig[m], fitted[m], lik)
        t.insert(0, "region", rname)
        frames.append(t)
    tbl = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(tbl):
        tbl.to_csv(os.path.join(outdir, "assumption_checks.csv"), index=False)

    post = pd.DataFrame()
    if beta_by_feature:
        post = posterior_correlation(beta_by_feature, pdata.region_names)
        post.to_csv(os.path.join(outdir, "posterior_correlation.csv"), index=False)

    # the three checks adopted from Meridian
    conf = confounding_pairs(pdata, model_cfg)
    conf.to_csv(os.path.join(outdir, "confounding_pairs.csv"), index=False)
    ppp = posterior_predictive_p(decomp, pdata)
    negb = negative_baseline_probability(decomp, pdata)
    extra = ppp.merge(negb, on="region", how="outer")
    extra.to_csv(os.path.join(outdir, "structural_checks.csv"), index=False)

    _write_readout(tbl, post, collin, outdir, lik, conf, ppp, negb)
    if len(tbl):
        flagged = tbl[tbl["verdict"] != "ok"]
        if len(flagged):
            worst = (flagged.groupby("check").size()
                     .sort_values(ascending=False).head(3))
            print(f"[assumptions] {len(flagged)} checks flagged across "
                  f"{flagged['region'].nunique()} regions "
                  f"({', '.join(worst.index)}) -> 04_fit/assumptions_report.md")
    return tbl


def _write_readout(tbl, post, collin, outdir, likelihood,
                   conf=None, ppp=None, negb=None) -> None:
    L = ["# Model assumptions", "",
         "Bayesian regression rests on the same structural assumptions as OLS. "
         "They do not invalidate standard errors here - they show up as "
         "**misspecification**: intervals that are too narrow, coefficients "
         "that trade off, contributions that reconcile perfectly and are still "
         "wrong.", ""]

    L += ["## 1. Collinearity (pre-fit: what the DATA can separate)", ""]
    if collin is not None and len(collin.get("summary", [])):
        s = collin["summary"]
        L += ["| region | n obs | columns | condition number | max VIF | max VIF uncentred | worst column | verdict |",
              "|---|---|---|---|---|---|---|---|"]
        for _, r in s.iterrows():
            L.append(f"| {r['region']} | {r['n_obs']} | {r['n_columns']} | "
                     f"{r['condition_number']:.1f} | {r['max_vif']:.1f} | "
                     f"{r.get('max_vif_uncentred', float('nan')):.1f} | "
                     f"`{r['worst_column']}` | **{r['verdict']}** |")
        L += ["", "**The two VIF columns answer different questions.** The "
              "textbook (centred) VIF is blind to a column that duplicates the "
              "INTERCEPT: an always-on feature scaled to ~1.0 every period has "
              "almost no centred variance, so it comes back near 1.0 while the "
              "design is near-singular. The uncentred column and the condition "
              "number both catch it. A high uncentred VIF with a low centred "
              "one means the feature duplicates the LEVEL, not another feature; "
              "`collinearity_vif.csv` says which in its `duplicates` column.",
              "",
              f"Thresholds: VIF > {VIF_BAD} or condition number > {COND_BAD} "
              "is severe; above 5 / 10 is moderate. Measured on the model's own "
              "design matrix - intercept, Fourier block and trend included, "
              "per region, training window only, columns scaled to unit length "
              "but NOT centred (centring would hide collinearity with the "
              "intercept, which is the case that matters most here).", ""]
        pr = collin.get("pairs")
        if pr is not None and len(pr):
            L += [f"### {len(pr)} column pairs above |r| = {PAIR_WARN}", "",
                  "| region | column A | column B | r |", "|---|---|---|---|"]
            for _, r in pr.head(25).iterrows():
                L.append(f"| {r['region']} | `{r['column_a']}` | "
                         f"`{r['column_b']}` | {r['correlation']:.3f} |")
            L.append("")
    else:
        L += ["Not computed.", ""]

    L += ["## 2. Identifiability (post-fit: what the MODEL could separate)", ""]
    if post is not None and len(post):
        L += [f"{len(post)} coefficient pairs correlate above |r| = {POST_WARN} "
              "in the posterior. These are trading off: the model knows their "
              "SUM, not their split, so neither contribution can be read alone.",
              "", "| region | feature A | feature B | posterior r | |",
              "|---|---|---|---|---|"]
        for _, r in post.head(25).iterrows():
            L.append(f"| {r['region']} | `{r['feature_a']}` | `{r['feature_b']}` "
                     f"| {r['posterior_corr']:.3f} | {r['verdict']} |")
        L.append("")
    else:
        L += ["No coefficient pair correlates above "
              f"|r| = {POST_WARN}. ", ""]
    L += ["> **Read this together with section 1 and with `contraction`.** A "
          "tight prior pins two collinear coefficients so they look fine here "
          "while the data still says nothing about either. High VIF + low "
          "posterior correlation + `contraction` near 0 means the priors are "
          "doing the work: the split between those features is an assumption "
          "you made, not a finding.", ""]

    L += ["## 3. Residual assumptions", ""]
    if len(tbl):
        flagged = tbl[tbl["verdict"] != "ok"]
        L += [f"{len(flagged)} of {len(tbl)} checks flagged across "
              f"{tbl['region'].nunique()} regions.", ""]
        if len(flagged):
            L += ["| region | check | statistic | threshold | what to do |",
                  "|---|---|---|---|---|"]
            for _, r in flagged.iterrows():
                L.append(f"| {r['region']} | {r['check']} | `{r['statistic']}` | "
                         f"{r['threshold']} | {r['what_to_do']} |")
            L.append("")
        L += ["Full table with every check and its meaning: "
              "`assumption_checks.csv`.", ""]
    else:
        L += ["Not computed.", ""]

    L += ["## 4. Structural checks (adopted from Meridian)", "",
          "Meridian runs almost no classical residual tests but three "
          "structural ones we lacked. See `docs/MERIDIAN_ASSUMPTIONS.md`.", ""]

    L += ["### 4a. Confounding: media correlated with controls", ""]
    if conf is not None and len(conf):
        L += [f"{len(conf)} treatment/control pairs correlate above "
              f"|r| = {CONFOUND_WARN}. Meridian flags at this level because it "
              "is the closest computable stand-in for exogeneity: a media "
              "coefficient correlated with a control may be absorbing that "
              "control's effect, and no goodness-of-fit number will say so.", "",
              "| region | treatment | control | r | |", "|---|---|---|---|---|"]
        for _, r in conf.head(20).iterrows():
            L.append(f"| {r['region']} | `{r['treatment']}` | `{r['control']}` "
                     f"| {r['correlation']:.3f} | {r['flag']} |")
        L += ["", "This is not fixed by editing a prior. It is a statement about "
              "what the coefficient can mean - report the pair together, or "
              "get an experiment.", ""]
    elif conf is not None:
        L += [f"No treatment/control pair correlates above |r| = "
              f"{CONFOUND_WARN}.", ""]
    else:
        L += ["Not computed (no baseline-flagged features to act as controls).", ""]

    L += ["### 4b. Aggregate posterior predictive p-value", ""]
    if ppp is not None and len(ppp):
        L += ["Is the TOTAL plausible under this model? Pointwise coverage "
              "cannot see a model whose weekly intervals are fine but whose "
              f"annual total sits in its own tail. Fails below {PPP_FAIL}.", "",
              "| region | actual total | expected total | ppp | |",
              "|---|---|---|---|---|"]
        for _, r in ppp.iterrows():
            L.append(f"| {r['region']} | {r['actual_total']:,.0f} | "
                     f"{r['expected_total_mean']:,.0f} | {r['ppp']:.3f} | "
                     f"**{r['verdict']}** |")
        L.append("")

    L += ["### 4c. Probability the baseline is negative", ""]
    if negb is not None and len(negb):
        L += ["A negative baseline says sales would be negative with no "
              "marketing, which is not a business statement - it is drivers "
              "over-claiming with the baseline absorbing the offset. Review at "
              f"{NEG_BASELINE_REVIEW}, fail at {NEG_BASELINE_FAIL}.", "",
              "| region | P(baseline < 0) | median baseline | |",
              "|---|---|---|---|"]
        for _, r in negb.iterrows():
            L.append(f"| {r['region']} | {r['p_negative_baseline']:.3f} | "
                     f"{r['median_baseline']:,.0f} | **{r['verdict']}** |")
        L.append("")

    L += ["## 5. Exogeneity - the one no statistic can check", "",
          "Every check above reads the residuals. None of them can tell you "
          "whether a regressor is correlated with the error, and that is the "
          "assumption marketing-mix models break most often:", "",
          "- **Spend follows expected sales.** Budgets are set high where sales "
          "are already expected to be high, so media coefficients absorb the "
          "demand that caused the spend. The model cannot see this; it fits "
          "beautifully and overstates ROI.",
          "- **Omitted common drivers.** A missing category or competitor "
          "variable that moves with your own activity loads onto whatever "
          "correlates with it.", "",
          "The only real answers are design-based: geo experiments, holdout "
          "regions, switchback tests - or an informative ROI prior derived from "
          "one. Treat a clean assumption report as necessary, not sufficient.",
          "",
          f"*Likelihood in force: `{likelihood}`.*", ""]

    with open(os.path.join(outdir, "assumptions_report.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
