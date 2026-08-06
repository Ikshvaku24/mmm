

compat.py

```
"""Compatibility helpers for Arviz InferenceData and xarray DataTree."""

from __future__ import annotations

def has_group(idata, group_name: str) -> bool:
    """Return True when an inference object contains the requested group."""
    try:
        return group_name in idata
    except TypeError:
        return hasattr(idata, group_name)

def get_group(idata, group_name: str):
    """
    Return an xarray.Dataset for a group from either:

    - legacy Arviz InferenceData
    - PyMC 6 xarray.DataTree
    """
    if not has_group(idata, group_name):
        raise KeyError(
            f"Inference result does not contain group {group_name!r}"
        )

    try:
        group = idata[group_name]
    except (KeyError, TypeError):
        group = getattr(idata, group_name)

    # A DataTree group node exposes its Dataset via `.dataset`.
    if hasattr(group, "dataset"):
        return group.dataset

    # Older InferenceData returns the Dataset directly.
    return group
```

config.py

```
"""Configuration for the hierarchical MMM (Codebase 1 - pre-transformed inputs).

The old prior file (one row per region x variable with b0/B0) is replaced by a
feature-level configuration: one row per FEATURE. The hierarchy generates the
regional variation, so you specify:

- a population prior (where the market-average effect sits),
- a regional heterogeneity prior (how much regions may differ),
- a sign constraint (positive / negative / free),
- whether the feature is hierarchical at all.

Scale conventions (important for choosing priors):
- The KPI (dv) is standardised per region, so effects are in "region sd of dv" units.
- Sign-constrained features are divided by the per-region mean of their positive
  values (no centering - zero activity stays zero, like Meridian's media scaler).
- Free-sign features are centred and scaled per region (like Meridian's controls).
- For sign-constrained features, `prior_mean` is the typical effect MAGNITUDE on
  that scale (must be > 0); `prior_sd` is on the log scale (0.7 ~ a factor of 2).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VALID_SIGNS = ("positive", "negative", "free")

# Bucket = one vectorised block of coefficients in the model.
BUCKET_ORDER = ["hpos", "hneg", "hfree", "gpos", "gneg", "gfree"]

@dataclass
class FeatureSpec:
    name: str
    hierarchical: bool = True
    sign: str = "free"  # "positive" | "negative" | "free"
    prior_mean: float | None = None  # population prior location (magnitude if signed)
    prior_sd: float | None = None  # population prior sd (log-scale if signed)
    regional_sd: float | None = None  # prior scale of cross-region heterogeneity

    def resolved(self) -> "FeatureSpec":
        s = FeatureSpec(**self.__dict__)
        if s.sign not in VALID_SIGNS:
            raise ValueError(f"{s.name}: sign must be one of {VALID_SIGNS}, got {s.sign!r}")
        if s.sign == "free":
            s.prior_mean = 0.0 if s.prior_mean is None else float(s.prior_mean)
            s.prior_sd = 0.5 if s.prior_sd is None else float(s.prior_sd)
        else:
            if s.prior_mean is None:
                s.prior_mean = 0.05
            elif s.prior_mean <= 0:
                warnings.warn(
                    f"{s.name}: sign-constrained features need prior_mean > 0 "
                    f"(a magnitude); got {s.prior_mean}. Using 0.05."
                )
                s.prior_mean = 0.05
            s.prior_sd = 1.0 if s.prior_sd is None else float(s.prior_sd)
            s.regional_sd = 0.5 if s.regional_sd is None else float(s.regional_sd)
        if not (np.isfinite(s.prior_sd) and s.prior_sd > 0):
            raise ValueError(f"{s.name}: prior_sd must be finite and > 0")
        if not np.isfinite(s.regional_sd):
            raise ValueError(
                f"{s.name}: regional_sd must be finite"
            )

        if s.hierarchical and s.regional_sd <= 0:
            raise ValueError(
                f"{s.name}: hierarchical features require regional_sd > 0"
            )

        if not s.hierarchical and s.regional_sd < 0:
            raise ValueError(
                f"{s.name}: regional_sd cannot be negative"
            )
        return s

def bucket_name(spec: FeatureSpec) -> str:
    return ("h" if spec.hierarchical else "g") + {
        "positive": "pos", "negative": "neg", "free": "free"
    }[spec.sign]

def bucket_features(specs: list[FeatureSpec]) -> dict[str, list[FeatureSpec]]:
    return {b: [s for s in specs if bucket_name(s) == b] for b in BUCKET_ORDER}

def load_feature_config(path: str) -> list[FeatureSpec]:
    """Read the feature-level prior file (CSV).

    Expected columns (extra columns ignored):
    variable, hierarchical, sign_constraint,
    global_prior_mean, global_prior_sd, regional_sd_prior
    """
    df = pd.read_csv(path)
    specs = []
    for _, r in df.iterrows():
        specs.append(FeatureSpec(
            name=r["variable"].strip(),
            hierarchical=bool(int(r.get("hierarchical", 1))),
            sign=str(r.get("sign_constraint", "free")).strip().lower(),
            prior_mean=None if pd.isna(r.get("global_prior_mean")) else float(r["global_prior_mean"]),
            prior_sd=None if pd.isna(r.get("global_prior_sd")) else float(r["global_prior_sd"]),
            regional_sd=None if pd.isna(r.get("regional_sd_prior")) else float(r["regional_sd_prior"]),
        ).resolved())
    return specs

@dataclass
class ModelConfig:
    features: list[FeatureSpec]
    likelihood: str = "normal"  # "normal" | "student_t"
    fourier_order: int = 0  # 0 = no seasonality block
    fourier_period_days: float = 365.25
    include_trend: bool = False
    alpha_prior_sd: float = 0.5  # population intercept prior (dv is standardised)
    alpha_regional_sd: float = 0.5
    pool_sigma: bool = True  # partial-pool region noise on the log scale
    # (recommended when regions have unequal
    # series lengths); False = independent
    # HalfNormal sigma per region

    def __post_init__(self):
        self.features = [s.resolved() for s in self.features]
        names = [s.name for s in self.features]
        if len(names) != len(set(names)):
            raise ValueError("duplicate feature names in config")
        if self.likelihood not in {"normal", "student_t"}:
            raise ValueError(
                "likelihood must be 'normal' or 'student_t'"
            )

        if self.fourier_order < 0:
            raise ValueError(
                "fourier_order must be >= 0"
            )

@dataclass
class SamplerConfig:
    draws: int = 1000
    tune: int = 1000
    chains: int = 4
    target_accept: float = 0.92
    seed: int = 42

    sampler: str = "numpyro"

    # NumPyro chain execution strategy:
    # sequential, parallel, or vectorized
    chain_method: str = "sequential"

    # Arguments for the underlying NUTS kernel only.
    # Do not put chain_method in this dictionary.
    nuts_kwargs: dict = field(default_factory=dict)

    prior_predictive_draws: int = 500
    store_log_likelihood: bool = False
    advi_iters: int = 30000

    # False prevents a failed GPU run from silently
    # switching to standard PyMC.
    allow_sampler_fallback: bool = False

    def __post_init__(self):
        if self.sampler not in {
            "numpyro",
            "pymc",
            "advi",
        }:
            raise ValueError(
                "sampler must be 'numpyro', "
                "'pymc', or 'advi'"
            )

        if self.chain_method not in {
            "sequential",
            "parallel",
            "vectorized",
        }:
            raise ValueError(
                "chain_method must be 'sequential', "
                "'parallel', or 'vectorized'"
            )

        if self.draws <= 0:
            raise ValueError(
                "draws must be greater than zero"
            )

        if self.tune < 0:
            raise ValueError(
                "tune must be zero or greater"
            )

        if self.chains <= 0:
            raise ValueError(
                "chains must be greater than zero"
            )

        if not 0 < self.target_accept < 1:
            raise ValueError(
                "target_accept must be between 0 and 1"
            )

@dataclass
class RunConfig:
    run_name: str = "run"
    output_dir: str = "outputs"
    date_col: str = "date"
    region_col: str = "region"
    dv_col: str = "dv"
    holdout_periods: int = 0  # last N dates held out per region for OOS metrics
    report_draws: int = 400  # posterior periods used for decomposition/plots
    on_convergence_failure: str = "warn"  # "warn" | "fail" - PE-style guardrail:
    # "fail" raises instead of silently
    # persisting an unconverged fit

@dataclass
class CVConfig:
    """Expanding-window (rolling-origin) cross-validation settings.

    Fold k trains on everything before its test window and predicts the next
    `horizon` periods; origins step back through the series so accuracy and
    coefficient stability are measured across several windows, not one.
    """
    horizon: int = 13  # test periods per fold
    n_folds: int = 5
    step: int | None = None  # spacing between origins (default: horizon)
    min_train_periods: int = 52  # skip folds with less training data
    draws: int | None = None  # override sampler draws for CV speed
    tune: int | None = None
    make_plots: bool = True
```

# cross\_validation.py

```
"""Expanding-window (rolling-origin) cross-validation.

Design follows the PE package's hierarchical temporal CV: each fold refits the
full joint model on an expanding training window, predicts the next `horizon`
periods, and records (a) fold-level accuracy - wMAPE / region-weighted MAPE /
MAPE / CRPS / predictive coverage - and (b) COEFFICIENT STABILITY across folds,
which for an MMM is the more important trust check: if a coefficient swings as
the origin moves, its contribution story is fragile whatever the error metric
says.

Because all scaling statistics are computed from the training window only, each
fold simply slices the data to its window and reuses the normal pipeline - no
leakage, no special-cased code path.

Speed: each fold is a full refit. With the NumPyro backend this is minutes per
fold; for quick sweeps use SamplerConfig(sampler="advi") (PE convention: ADVI
for CV speed, NUTS for the final fit) or CVConfig(draws=..., tune=...).

Usage:
    from config import CVConfig, ModelConfig, RunConfig, SamplerConfig
    from cross_validation import run_cv
    cv = run_cv(df, model_cfg, RunConfig(run_name="fy26"),
                SamplerConfig(sampler="numpyro"), CVConfig(horizon=13, n_folds=5))

Outputs (<output_dir>/<run_name>/06_cross_validation/):
    cv_fold_metrics.csv      per fold x region x train/test: all fit metrics
                             + fold convergence (max R-hat, divergences)
    cv_summary.csv           mean +/- sd across folds (test window)
    cv_coefficient_stability.csv fold-wise posterior medians per feature/region
    cv_stability_ranking.csv features ranked by cross-fold instability
    cv_report.md             headline readout
    stability/<feature>.png   coefficient medians across folds, per region
    fold_k/sampling_log.json per-fold run manifests
"""

from __future__ import annotations

import os
from dataclasses import replace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

from config import CVConfig, ModelConfig, RunConfig, SamplerConfig
from data_prep import make_folds, prepare_data
from diagnostics import quick_convergence_checks
from fit import fit
from model import build_model
from outputs import (beta_draws_by_feature, compute_decomposition,
                    compute_fit_metrics, stack_posterior)

def run_cv(df: pd.DataFrame,
           model_cfg: ModelConfig,
           run_cfg: RunConfig | None = None,
           sampler_cfg: SamplerConfig | None = None,
           cv_cfg: CVConfig | None = None) -> dict:
    run_cfg = run_cfg or RunConfig()
    sampler_cfg = sampler_cfg or SamplerConfig()
    cv_cfg = cv_cfg or CVConfig()
    outdir = os.path.join(run_cfg.output_dir, run_cfg.run_name,
                          "06_cross_validation")
    os.makedirs(outdir, exist_ok=True)

    dc = run_cfg.date_col
    d = df.copy()
    d[dc] = pd.to_datetime(d[dc])
    dates = np.sort(d[dc].unique())
    folds = make_folds(len(dates), cv_cfg.horizon, cv_cfg.n_folds,
                      cv_cfg.step, cv_cfg.min_train_periods)

    scfg = sampler_cfg
    if cv_cfg.draws or cv_cfg.tune:
        scfg = replace(sampler_cfg,
                      draws=cv_cfg.draws or sampler_cfg.draws,
                      tune=cv_cfg.tune or sampler_cfg.tune)

    metrics_frames, coef_rows = [], []
    region_sets = []
    for k, (ts, te) in enumerate(folds, start=1):
        train_end = pd.Timestamp(dates[ts - 1]).date()
        test_span = (pd.Timestamp(dates[ts]).date(), pd.Timestamp(dates[te - 1]).date())
        print(f"[cv] fold {k}/{len(folds)}: train ..{train_end}, "
              f"test {test_span[0]}..{test_span[1]}")

        sub = d[d[dc] <= pd.Timestamp(dates[te - 1])]
        fold_run = replace(run_cfg, holdout_periods=cv_cfg.horizon)
        pdata = prepare_data(sub, fold_run, model_cfg)
        region_sets.append(tuple(pdata.region_names))

        model = build_model(pdata, model_cfg)
        idata = fit(model, scfg, outdir=os.path.join(outdir, f"fold_{k}"))
        conv = quick_convergence_checks(idata)

        decomp = compute_decomposition(idata, pdata, model_cfg,
                                       n_draws=run_cfg.report_draws, seed=k)

        fm = compute_fit_metrics(decomp, pdata)
        fm.insert(0, "fold", k)
        fm["train_end"] = str(train_end)
        fm["test_start"], fm["test_end"] = str(test_span[0]), str(test_span[1])
        fm["max_rhat"] = conv["max_rhat"]
        fm["divergences"] = conv["divergences"]
        metrics_frames.append(fm)

        post = stack_posterior(idata)
        for name, arr in beta_draws_by_feature(post, pdata).items():
            for g, r in enumerate(pdata.region_names):
                coef_rows.append({"fold": k, "feature": name, "region": r,
                                "median": float(np.median(arr[:, g]))})

    del idata, decomp, post

    if len(set(region_sets)) > 1:
        print("[cv] WARNING: region set differs across folds - "
              "stability comparisons only cover shared regions")

    fold_metrics = pd.concat(metrics_frames, ignore_index=True)
    fold_metrics.to_csv(os.path.join(outdir, "cv_fold_metrics.csv"), index=False)

    # ---- summary across folds (test window) -----
    test = fold_metrics[fold_metrics["dataset"] == "test"]
    agg_cols = [c for c in ["wmape_pct", "mape_pct", "mape_region_weighted_pct",
                          "mae", "crps", "coverage_90_pred_pct", "r2"]
                if c in test.columns]
    summary = (test.groupby("region")[agg_cols]
               .agg(["mean", "std"]).round(3))
    summary.columns = [f"{a}_{b}" for a, b in summary.columns]
    summary = summary.reset_index()
    summary.to_csv(os.path.join(outdir, "cv_summary.csv"), index=False)

    # ---- coefficient stability -----
    coef = pd.DataFrame(coef_rows)
    coef.to_csv(os.path.join(outdir, "cv_coefficient_stability.csv"), index=False)
    stab = (coef.groupby(["feature", "region"])["median"]
            .agg(["mean", "std", "min", "max"]).reset_index())
    stab["rel_sd_pct"] = (stab["std"] / (stab["mean"].abs() + 1e-12) * 100).round(1)
    rank = (stab.groupby("feature")["rel_sd_pct"].mean()
            .sort_values(ascending=False).reset_index()
            .rename(columns={"rel_sd_pct": "avg_rel_sd_pct"}))
    rank.to_csv(os.path.join(outdir, "cv_stability_ranking.csv"), index=False)

    if cv_cfg.make_plots:
        sdir = os.path.join(outdir, "stability")
        os.makedirs(sdir, exist_ok=True)
        for name, grp in coef.groupby("feature"):
            fig, ax = plt.subplots(figsize=(6, 3.2))
            for r, gr in grp.groupby("region"):
                ax.plot(gr["fold"], gr["median"], marker="o", ms=3, lw=1,
                       label=str(r))

                ax.axhline(0, color="grey", lw=0.8)
                ax.set_xlabel("fold (chronological)")
                ax.set_ylabel("posterior median (scaled)")
                ax.set_title(f"{name} - coefficient stability across CV folds",
                           fontsize=10)

            if grp["region"].nunique() <= 10:
                ax.legend(fontsize=7)

            fig.tight_layout()
            fig.savefig(os.path.join(sdir, f"{name}.png"), dpi=130)
            plt.close(fig)

    # ---- headline report -----
    t_all = test[test["region"] == "_all_"]
    conv_bad = fold_metrics[(fold_metrics["region"] == "_all_"
                              & (fold_metrics["dataset"] == "test")
                              & ((fold_metrics["max_rhat"] > 1.05)
                                | (fold_metrics["divergences"] > 0))]

    lines = [
        "# Expanding-window CV report",
        f"- folds: {len(folds)}, horizon: {cv_cfg.horizon} periods, ",
        f"sampler: {scfg.sampler}",
        f"- test wMAPE: {t_all['wmape_pct'].mean():.2f}% ",
        f"(/- {t_all['wmape_pct'].std():.2f} across folds)",
        f"- test region-weighted MAPE: ",
        f"{t_all['mape_region_weighted_pct'].mean():.2f}%",
        f"- test CRPS: {t_all['crps'].mean():.1f} (KPI units; lower is better)",
        f"- predictive 90% coverage on test: ",
        f"{t_all['coverage_90_pred_pct'].mean():.1f}% (target ~90; well below = ",
        f"overconfident, well above = intervals too wide)",
        f"- folds with convergence flags: {len(conv_bad)} of {len(folds)}",
        "",
        "## Least stable features across folds (avg relative sd of the ",
        "regional coefficient medians)", ""],
        for _, r in rank.head(5).iterrows():
            lines.append(f"- {r['feature']}: {r['avg_rel_sd_pct']:.1f}%")
    lines += ["", "A feature that reorders its swings across folds has a fragile "
              "contribution story - treat its ROI/contribution read with "
              "caution even if overall wMAPE looks good.",
              "", "Note: fold metrics judge PREDICTION. Contribution/ROI "
              "validity additionally needs stable coefficients (above) and, "
              "ideally, calibration against lift experiments."]

    with open(os.path.join(outdir, "cv_report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[cv] done -> {outdir}")
    return {"fold_metrics": fold_metrics, "summary": summary,
            "coefficient_stability": stab, "stability_ranking": rank,
            "output_dir": outdir}
```



# data\_prep.py

```
"""Data validation, scaling and packaging for the hierarchical MMM (codebase 1).

Input contract: one long ("stacked") pandas DataFrame with columns
[date_col, region_col, dv_col, <feature columns...>]
one row per region x period. Features arrive ALREADY transformed
(adstock / saturation / lag done in preprocessing) - this codebase does not
transform them further, it only scales for sampler geometry.

Scaling (stats computed on the TRAINING window only, stored for reuse):
- dv:      standardised per region      (Meridian: KPI transformer)
- signed feats: / per-region mean of positive values (Meridian: media transformer -
               no centering, zero stays zero)
- free feats:  centred + scaled per region      (Meridian: controls transformer)
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import FeaturesSpec, ModelConfig, RunConfig, bucket_features


def make_folds(n_dates: int, horizon: int, n_folds: int,
               step: int | None = None, min_train: int = 52) -> list[tuple[int, int]]:
    """Expanding-window (rolling-origin) fold boundaries, chronological order.

    Returns [(test_start, test_end), ...] as indices into the sorted unique
    dates; fold k trains on dates[:test_start] and tests on
    dates[test_start:test_end]. The last fold ends at the final date; earlier
    folds step back by 'step' (default: horizon). Folds whose training window
    would fall below 'min_train' periods are dropped.
    """
    step = step or horizon
    folds = []
    i = 0
    while len(folds) < n_folds:
        test_end = n_dates - i * step
        test_start = test_end - horizon
        if test_start < min_train:
            break
        folds.append((test_start, test_end))
        i += 1
    if not folds:
        raise ValueError(
            f"no valid CV folds: {n_dates} periods cannot fit horizon={horizon} "
            f"with min_train={min_train}")
    return folds[:-1]


def fourier_features(dates: pd.Series, order: int, period_days: float = 365.25):
    """Annual-cycle sin/cos features (a light version of Meridian's spline mu_t)"""
    t_days = (dates - dates.min()).dt.days.to_numpy(dtype=float)
    cols, names = [], []
    for k in range(1, order + 1):
        ang = 2.0 * np.pi * k * t_days / period_days
        cols += [np.sin(ang), np.cos(ang)]
        names += [f"sin_{k}", f"cos_{k}"]
    if not cols:
        return None, []
    return np.column_stack(cols), names


@dataclass
class PreparedData:
    region_names: list
    region_idx: np.ndarray          # (n_obs,) int
    dates: pd.Series               # (n_obs,)
    y_orig: np.ndarray            # (n_obs,) original units
    y: np.ndarray                 # (n_obs,) scaled
    y_mean: np.ndarray            # (G,)
    y_scale: np.ndarray           # (G,)
    X: np.ndarray                 # (n_obs, k) scaled
    feature_names: list
    feature_index: dict           # bucket name -> list[FeaturesSpec]
    buckets: dict
    X_fourier: np.ndarray | None
    fourier_names: list
    t: np.ndarray                 # (n_obs,) trend feature, 0..1 over train window
    train_mask: np.ndarray        # (n_obs,) bool
    test_mask: np.ndarray
    x_scale_table: pd.DataFrame = field(default=None)  # region x feature scaling stats

    def sel(self, which: str) -> np.ndarray:
        return {"train": self.train_mask, "test": self.test_mask,
                "all": np.ones_like(self.train_mask, dtype=bool)}[which]


def prepare_data(df: pd.DataFrame, run_cfg: RunConfig, model_cfg: ModelConfig) -> PreparedData:
    d = df.copy()
    dc, rc, yc = run_cfg.date_col, run_cfg.region_col, run_cfg.dv_col

    # validation 
    missing_cols = [c for c in [dc, rc, yc] if c not in d.columns]
    if missing_cols:
        raise ValueError(f"missing required columns: {missing_cols}")
    feat_names = [s.name for s in model_cfg.features]
    missing_feats = [c for c in feat_names if c not in d.columns]
    if missing_feats:
        raise ValueError(f"feature columns not in data: {missing_feats}")

    d[dc] = pd.to_datetime(d[dc])
    d = d.sort_values([rc, dc]).reset_index(drop=True)

    if d.duplicated([rc, dc]).any():
        dup = d[d.duplicated([rc, dc], keep=False)][[rc, dc]].head()
        raise ValueError(f"duplicate (region, date) rows, e.g.: \n{dup}")
    for c in [yc] + feat_names:
        d[c] = pd.to_numeric(d[c], errors="raise")
    if d[yc].isna().any():
        raise ValueError("dv contains NaN - fix upstream (do not zero-fill the KPI)")
    nan_feats = [c for c in feat_names if d[c].isna().any()]
    if nan_feats:
        raise ValueError(f"features contain NaN (zero-fill inactive periods upstream): {nan_feats}")

    regions = sorted(d[rc].unique().tolist())
    region_idx = d[rc].map({r: i for i, r in enumerate(regions)}).to_numpy()
    G = len(regions)

    # train / holdout split by date 
    all_dates = np.sort(d[dc].unique())
    if run_cfg.holdout_periods > 0:
        cutoff = all_dates[run_cfg.holdout_periods]
        train_mask = (d[dc] < cutoff).to_numpy()
    else:
        train_mask = np.ones(len(d), dtype=bool)
        test_mask = ~train_mask

    # per-region training support and seasonality sanity (config validation)
    for g, r in enumerate(regions):
        n_tr = int(((region_idx == g) & train_mask).sum())
        if n_tr < 10:
            raise ValueError(f"region {r} has only {n_tr} training periods after "
                             "holdout - reduce holdout_periods or drop the region")

    if model_cfg.fourier_order > 0:
        n_dates_tr = len(np.unique(d.loc[train_mask, dc]))
        if n_dates_tr < 6 * model_cfg.fourier_order:
            warnings.warn(f"fourier_order={model_cfg.fourier_order} is high for "
                          f"{n_dates_tr} training periods - risk of overfitting "
                          "the seasonal cycle")

    # dv scaling: standardise per region (train stats) 
    y_orig = d[yc].to_numpy(dtype=float)
    y_mean = np.zeros(G)
    y_scale = np.ones(G)
    for g in range(G):
        m = (region_idx == g) & train_mask
        y_mean[g] = y_orig[m].mean()
        sd = y_orig[m].std()
        y_scale[g] = sd if sd > 0 else 1.0
    y = (y_orig - y_mean[region_idx]) / y_scale[region_idx]

    # feature scaling 
    X = np.zeros((len(d), len(feat_names)))
    scale_rows = []
    for j, spec in enumerate(model_cfg.features):
        v = d[spec.name].to_numpy(dtype=float)
        if spec.sign != "free" and (v < 0).any():
            warnings.warn(f"{spec.name}: sign-constrained features are scaled "
                          "without centering, which assumes non-negative values - "
                          f"found {(v < 0).sum()} negative entries")
        for g in range(G):
            m_all = region_idx == g
            m_tr = m_all & train_mask
            if spec.sign == "free":
                mu, sd = v[m_tr].mean(), v[m_tr].std()
                sd = sd if sd > 0 else 1.0
                X[m_all, j] = (v[m_all] - mu) / sd
                scale_rows.append((regions[g], spec.name, "center_scale", mu, sd))
            else:
                pos = v[m_tr][v[m_tr] > 0]
                sc = pos.mean() if len(pos) else 1.0
                X[m_all, j] = v[m_all] / sc
                scale_rows.append((regions[g], spec.name, "scale_only", 0.0, sc))
    x_scale_table = pd.DataFrame(
        scale_rows, columns=["region", "feature", "method", "center", "scale"])

    # seasonality & trend 
    Xf, f_names = fourier_features(d[dc], model_cfg.fourier_order,
                                   model_cfg.fourier_period_days)
    t_days = (d[dc] - d[dc].min()).dt.days.to_numpy(dtype=float)
    span = t_days[train_mask].max() or 1.0
    t = t_days / span

    return PreparedData(
        region_names=regions, region_idx=region_idx, dates=d[dc],
        y_orig=y_orig, y=y, y_mean=y_mean, y_scale=y_scale,
        X=X, feature_names=feat_names,
        feature_index={n: j for j, n in enumerate(feat_names)},
        buckets=bucket_features(model_cfg.features),
        X_fourier=Xf, fourier_names=f_names, t=t,
        train_mask=train_mask, test_mask=test_mask,
        x_scale_table=x_scale_table,
    )


def write_data_stage_outputs(pdata: PreparedData, outdir: str) -> None:
    """Stage 01 outputs: what went into the model, per region."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    G = len(pdata.region_names)

    rows = []
    for g, r in enumerate(pdata.region_names):
        m = pdata.region_idx == g
        rows.append({
            "region": r, "n_obs": int(m.sum()),
            "n_train": int((m & pdata.train_mask).sum()),
            "n_test": int((m & pdata.test_mask).sum()),
            "dv_mean": pdata.y_orig[m].mean(), "dv_sd": pdata.y_orig[m].std(),
            "date_min": str(pdata.dates[m].min().date()),
            "date_max": str(pdata.dates[m].max().date()),
        })
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "panel_summary.csv"), index=False)
    pdata.x_scale_table.to_csv(os.path.join(outdir, "feature_scaling_stats.csv"), index=False)

    ncol = min(4, G)
    nrow = int(np.ceil(G / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.5 * ncol, 2.6 * nrow),
                             squeeze=False, sharex=True)
    for g, r in enumerate(pdata.region_names):
        ax = axes[g // ncol][g % ncol]
        m = pdata.region_idx == g
        ax.plot(pdata.dates[m], pdata.y_orig[m], lw=0.9)
        if pdata.test_mask.any():
            mt = m & pdata.test_mask
            if mt.any():
                ax.axvspan(pdata.dates[mt].min(), pdata.dates[mt].max(),
                           alpha=0.15, color="orange")
            ax.set_title(str(r), fontsize=9)
        for k in range(G, nrow * ncol):
            axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("KPI by region (orange = holdout)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "kpi_by_region.png"), dpi=130)
    plt.close(fig)
```

## demo.py

```
# Databricks notebook source
# MAGIC %pip install -r requirements.txt

# COMMAND 

dbutils.library.restartPython()

# COMMAND 

import jax

print(jax.__version__)
print(jax.devices())

# COMMAND 

import pymc as pm
import pytensor
import arviz as az
import numpy as np
import pandas as pd
import jax
import numpyro

print("PyMC:", pm.__version__)
print("Pytensor:", pytensor.__version__)
print("Arviz:", az.__version__)
print("NumPy:", np.__version__)
print("Pandas:", pd.__version__)
print("JAX:", jax.__version__)
print("NumPyro:", numpyro.__version__)
print("JAX devices:", jax.devices())

# COMMAND 

!python synthetic_example.py

# COMMAND 

import pandas as pd
import numpy as np

INPUT_PATH = "input_datacube.xlsx"

df = pd.read_excel(INPUT_PATH, engine="openpyxl")

expected_columns = [
    "date",
    "region",
    "dv",
    "TD",
    "AVP",
    "ACV_MD_Any Merch",
    "Coupon-Digital",
    "Coupon-ESR",
    "Coupon-Ibotta",
    "Shopper-Digital",
    "calls",
    "Samples",
    "Audio",
    "DEI",
    "Direct Partnership",
    "Display",
    "DTV",
    "Ecommerce Display",
    "Ecommerce Search",
    "Ecommerce Video",
    "Expert Display",
    "Expert Partnership",
    "Expert Search",
    "Expert Social",
    "Expert Video",
    "MSAN",
    "OLV",
    "Paid Search",
    "PR",
    "Social",
    "TV_GM",
    "TV_HM",
    "Dummy",
]

missing = [c for c in expected_columns if c not in df.columns]
unexpected = [c for c in df.columns if c not in expected_columns]

print("Rows:", len(df))
print("Regions:", df["region"].nunique())
print("Dates:", df["date"].nunique())
print("Date range:", df["date"].min(), "to", df["date"].max())
print("Missing expected columns:", missing)
print("Unexpected columns:", unexpected)

assert not missing, f"Missing columns: {missing}"
assert not df.duplicated([r"region", "date"]).any(), (
    "Duplicate region-date rows exist"
)

numeric_columns = [
    c for c in expected_columns
    if c not in {"date", "region"}
]

for column in numeric_columns:
    df[column] = pd.to_numeric(df[column], errors="raise")

assert not df["dv"].isna().any(), "dv contains missing values"
assert not df[numeric_columns].isna().any().any(), (
    "One or more model columns contain missing values"
)

print("\nObservations by region:")
print(df.groupby("region").size().sort_values())

print("\nNon-zero counts by feature:")
print((df[numeric_columns] != 0).sum().sort_values())

# COMMAND 

import pandas as pd

from config import (
    ModelConfig,
    RunConfig,
    SamplerConfig,
    load_feature_config,
)
from run_pipeline import run

INPUT_PATH = "input_datacube.xlsx"
PRIOR_PATH = "feature_priors.csv"

df = pd.read_excel(
    INPUT_PATH,
    engine="openpyxl",
)

features = load_feature_config(PRIOR_PATH)

model_config = ModelConfig(
    features=features,
    likelihood="student_t",
    fourier_order=2,
    fourier_period_days=365.25,
    include_trend=True,
    pool_sigma=True,
)

run_config = RunConfig(
    run_name="regional_mmm_initial",
    output_dir="outputs",
    date_col="date",
    region_col="region",
    dv_col="dv",
    holdout_periods=13,
    report_draws=400,
    on_convergence_failure="warn",
)

sampler_config = SamplerConfig(
    draws=1000,
    tune=1000,
    chains=4,
    target_accept=0.95,
    seed=42,
    sampler="numpyro",
    nuts_kwargs={
        "chain_method": "vectorized",
    },
    prior_predictive_draws=500,
    store_log_likelihood=False,
)

result = run(
    df=df,
    model_cfg=model_config,
    run_cfg=run_config,
    sampler_cfg=sampler_config,
    save_trace=True,
)

print("Output directory:", result["output_dir"])
print(result["metrics"])
```



```
diagnostics.py

"""Convergence and prior-vs-posterior diagnostics.

Replaces the invalid 't = mean/mcse_mean' p-value of production_code.py
(mcmc shrinks as you sample longer, so 'significance' inflates with more
draws) with the correct tools: R-hat, ESS, divergences, and HDIs.
"""

from __future__ import annotations

import os

import arviz as az
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt # noqa: E402
import numpy as np
import pandas as pd
from compat import get_group, has_group

RHAT_WARN = 1.01
RHAT_FAIL = 1.05
ESS_WARN = 400
DIVERGENCE_FRAC_FAIL = 0.01 # >1% divergent transitions = geometry problem

def quick_convergence_checks(idata) -> dict:
    """cheap numeric checks (no report files) - used per CV fold and by the
    guardrail. Warn R-hat/ESS from constant Deterministics is ignored."""
    out = {"max_rhat": np.nan, "min_ess_bulk": np.nan,
           "divergences": 0, "divergence_frac": 0.0, "n_chains": 0}
    try:
        rhat = az.rhat(idata)
        vals = np.concatenate([np.ravel(v.values) for v in rhat.data_vars.values()])
        if np.isfinite(vals).any():
            out["max_rhat"] = float(np.nanmax(vals))
        ess = az.ess(idata)
        evals = np.concatenate([np.ravel(v.values) for v in ess.data_vars.values()])
        if np.isfinite(evals).any():
            out["min_ess_bulk"] = float(np.nanmin(evals))
    except Exception: # noqa: BLE001 (e.g. single-chain ADVI posterior)
        pass
    try:
        sample_stats = get_group(
            idata,
            "sample_stats",
        )
        if "diverging" in sample_stats:
            div = sample_stats["diverging"].values
            out["divergences"] = int(div.sum())
            out["divergence_frac"] = float(div.mean())
    except Exception:
        pass
    try:
        posterior = get_group(
            idata,
            "posterior",
        )
        out["n_chains"] = int(
            posterior.sizes.get("chain", 0)
        )
    except Exception:
        pass
    return out

def enforce_convergence(checks: dict, policy: str = "warn") -> None:
    """PE-style guardrail: 'fail' raises instead of silently persisting an
    unconverged fit (the PE methodology flags warn-and-continue as a
    production risk)."""
    problems = []
    if np.isfinite(checks.get("max_rhat", np.nan)) and checks["max_rhat"] > RHAT_FAIL:
        problems.append(f"max R-hat {checks['max_rhat']:.3f} > {RHAT_FAIL}")
    if checks.get("divergence_frac", 0.0) > DIVERGENCE_FRAC_FAIL:
        problems.append(f"{checks['divergences']} divergences "
                        f"{checks['divergence_frac'] * 100:.1f}% of transitions")
    if not problems:
        return
    msg = "convergence guardrail: " + " ".join(problems)
    if policy == "fail":
        raise RuntimeError(msg + " (on convergence_failure='fail')")
    print(f"[diagnostics] WARNING - {msg}")

def convergence_report(idata, outdir: str) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    summ = az.summary(idata, round=6)
    summ.to_csv(os.path.join(outdir, "posterior_summary_full.csv"))

    lines = []
    n_div = 0
    if has_group(idata, "sample_stats"):
        sample_stats = get_group(
            idata,
            "sample_stats",
        )
        if "diverging" in sample_stats:
            n_div = int(
                sample_stats["diverging"].values.sum()
            )
    worst_rhat = float(summ["r_hat"].max())
    min_ess = float(summ["ess_bulk"].min())
    min_ess_tail = float(summ["ess_tail"].min()) if "ess_tail" in summ else np.nan
    lines.append(f"max R-hat = {worst_rhat:.4f} "
                 f"({'OK' if worst_rhat < RHAT_WARN else 'WARN' if worst_rhat < RHAT_FAIL else 'FAIL'})")
    lines.append(f"min ESS (bulk) = {min_ess:.0f} "
                 f"({'OK' if min_ess > ESS_WARN else 'WARN'})")
    if np.isfinite(min_ess_tail):
        lines.append(f"min ESS (tail) : {min_ess_tail:.0f} "
                     f"({'OK' if min_ess_tail > ESS_WARN else 'WARN (interval endpoints unstable)'})")
    lines.append(f"divergences : {n_div} ({'OK' if n_div == 0 else 'INVESTIGATE'})")
    try:
        bfmi = az.bfmi(idata)
        per_chain = ", ".join(f"{b:.2f}" for b in np.atleast_1d(bfmi))
        lines.append(f"BFMI by chain : [{per_chain}] "
                     f"({'OK' if np.min(bfmi) > 0.3 else 'WARN (poor energy exploration)'})")
    except Exception: # noqa: BLE001
        pass
    try:
        ss = get_group(
            idata,
            "sample_stats",
        )
        for key in ("tree_depth", "depth"):
            if key in ss:
                td = ss[key].values
                lines.append(f"max tree depth : {int(td.max())} "
                             f"(saturated in {float(td >= td.max()).mean() * 100:.1f}% "
                             f"of steps)" if td.max() >= 10 else
                             f"max tree depth : {int(td.max())} (OK)")
                break
    except Exception: # noqa: BLE001
        pass
    lines.append("")
    lines.append("Worst parameters by R-hat:")
    worst = summ.sort_values("r_hat", ascending=False).head(10)
    lines.append(worst[["mean", "sd", "ess_bulk", "r_hat"]].to_string())
    lines.append("")
    lines.append("NOTE: do not use mean/mcse t-stats for significance. Use the HDI "
                 "and P(direction) columns in the coefficient report instead.")
    report = "\n".join(lines)
    with open(os.path.join(outdir, "convergence_report.txt"), "w") as f:
        f.write(report)
    print(f"[diagnostics]\n\n" + report)

    # plots (best effort)
    try:
        az.plot_energy(idata)
        plt.gcf().savefig(os.path.join(outdir, "energy_plot.png"), dpi=100)
        plt.close("all")
    except Exception: # noqa: BLE001
        pass
    try:
        base_vars = sorted([i.split("[")[0] for i in worst.index[3:]])
        az.plot_trace(idata, var_names=base_vars, compact=True)
        plt.gcf().tight_layout()
        plt.gcf().savefig(os.path.join(outdir, "trace_worst_rhat.png"), dpi=100)
        plt.close("all")
    except Exception: # noqa: BLE001
        pass
    return summ

def prior_posterior_report(idata, outdir: str,
                           var_prefixes=("pop_beta_", "mu_", "tau_",
                                         "adstock_", "hill_", "sigma_region")) -> None:
    """Contraction = 1 - posterior_var/prior_var. Near 1: data did the work.
    Near 0: the number is your prior, not a finding (report it as such)."""
    if not has_group(idata, "prior"):
        return
    prior = get_group(
        idata,
        "prior",
    )
    posterior = get_group(
        idata,
        "posterior",
    )
    rows = []
    for v in prior.data_vars:
        if not any(v.startswith(p) for p in var_prefixes):
            continue
        if v not in posterior:
            continue
        pr = prior[v].stack(
            s=("chain", "draw")
        ).values
        po = posterior[v].stack(
            s=("chain", "draw")
        ).values
        pr = pr.reshape(-1, pr.shape[-1]) if pr.ndim > 1 else pr.reshape(1, -1)
        po = po.reshape(-1, po.shape[-1]) if po.ndim > 1 else po.reshape(1, -1)
        for i in range(pr.shape[0]):
            pv, qv = float(np.var(pr[i])), float(np.var(po[i]))
            rows.append({"parameter": v if pr.shape[0] == 1 else f"{v}[{i}]",
                         "prior_sd": np.sqrt(pv), "posterior_sd": np.sqrt(qv),
                         "contraction": 1.0 - qv / pv if pv > 0 else np.nan})
    if rows:
        df = pd.DataFrame(rows).sort_values("contraction")
        df.to_csv(os.path.join(outdir, "prior_posterior_contraction.csv"), index=False)
        low = df[df["contraction"] < 0.2]
        if len(low):
            with open(os.path.join(outdir, "convergence_report.txt"), "a") as f:
                f.write(f"\n\nWARNING: {len(low)} parameters have contraction < 0.2 "
                        "\n(posterior ~ prior; the data says little about them). "
                        "See prior_posterior_contraction.csv\n")
```

```
fit.py

"""Sampling with a GPU-capable backend and graceful fallback.

Preferred: nuts_sampler="numpyro" -> the model is JIT-compiled with JAX
and NUTS runs on GPU if one is visible to JAX (Databricks GPU cluster, Colab),
otherwise on JIT-compiled CPU (still typically several x faster than the
default PyMC sampler). Falls back to the default sampler if JAX/NumPyro is
not installed.

Every run writes sampling_log.json - a run manifest with package versions,
devices, the sampler REQUESTED vs the sampler actually USED, and timings.
Always check it: a successful run does not by itself mean the GPU was used.
"""

from __future__ import annotations

import importlib
import json
import os
import time

import pymc as pm

from config import SamplerConfig

_MANIFEST_PACKAGES = ("pymc", "pytensor", "arviz", "numpy", "pandas",
                      "xarray", "jax", "numpyro")

def package_versions() -> dict:
    out = {}
    for m in _MANIFEST_PACKAGES:
        try:
            out[m] = importlib.import_module(m).__version__
        except Exception: # noqa: BLE001
            out[m] = "not installed"
    return out

def describe_backend() -> str:
    try:
        import jax
        devs = jax.devices()
        return f"jax devices: {[str(d) for d in devs]}"
    except Exception as e: # noqa: BLE001
        return f"jax not available ({e.__class__.__name__})"

def sample_prior(model: pm.Model, scfg: SamplerConfig):
    with model:
        return pm.sample_prior_predictive(draws=scfg.prior_predictive_draws,
                                         random_seed=scfg.seed)

def fit(
    model: pm.Model,
    scfg: SamplerConfig,
    outdir: str | None = None,
):
    if scfg.sampler == "advi":
        return _fit_advi(
            model,
            scfg,
            outdir,
        )
    common = {
        "draws": scfg.draws,
        "tune": scfg.tune,
        "chains": scfg.chains,
        "target_accept": scfg.target_accept,
        "random_seed": scfg.seed,
        "return_inferencedata": True,
    }
    if getattr(scfg, "allow_sampler_fallback", True):
        order = [scfg.sampler] + [
            sampler
            for sampler in ("numpyro", "pymc")
            if sampler != scfg.sampler
        ]
    else:
        order = [scfg.sampler]

    log = {
        "backend_info": describe_backend(),
        "versions": package_versions(),
        "sampler_requested": scfg.sampler,
        "nuts_kwargs": scfg.nuts_kwargs or {},
        "seed": scfg.seed,
        "draws": scfg.draws,
        "tune": scfg.tune,
        "chains": scfg.chains,
        "target_accept": scfg.target_accept,
        "store_log_likelihood": scfg.store_log_likelihood,
        "allow_sampler_fallback": getattr(
            scfg,
            "allow_sampler_fallback",
            True,
        ),
    }
    idata = None
    used = None
    last_error = None

    for sampler in order:
        started = time.time()
        try:
            with model:
                if sampler == "pymc":
                    pymc_kwargs = dict(common)

                    # Avoid Linux fork after JAX has initialized.
                    pymc_kwargs["cores"] = 1

                    idata = pm.sample(
                        init="adapt_diag",
                        **pymc_kwargs,
                    )
                else:
                    external_kwargs = dict(common)

                    # On the first compatibility run, leave this empty.
                    if scfg.nuts_kwargs:
                        external_kwargs["nuts"] = scfg.nuts_kwargs

                    idata = pm.sample(
                        nuts_sampler=sampler,
                        **external_kwargs,
                    )
                used = sampler
                log["sampler_used"] = sampler
                log["wall_seconds"] = round(
                    time.time() - started,
                    1,
                )
            break
        except Exception as exc:
            last_error = {
                f"{sampler}": " "
                f"{exc.__class__.__name__}: {exc}"
            }
            log[f"failed_{sampler}"] = last_error

        print(
            f"[fit] WARNING: sampler '{sampler}' failed\n"
            f"[fit] {last_error}"
        )

    if idata is None:
        raise RuntimeError(
            "All configured samplers failed. "
            f"Last error: {last_error}"
        )

    if scfg.store_log_likelihood:
        with model:
            result = pm.compute_log_likelihood(
                idata,
                extend_inferencedata=True,
            )
            if result is not None:
                idata = result

    if outdir:
        os.makedirs(outdir, exist_ok=True)
        with open(
            os.path.join(
                outdir,
                "sampling_log.json",
            ),
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                log,
                file,
                indent=2,
                default=str,
            )

    print(
        f"[fit] sampled with '{used}' in "
        f"{log.get('wall_seconds', '?')}s "
        f"{log.get('backend_info')}"
    )
    return idata

def _fit_advi(model: pm.Model, scfg: SamplerConfig, outdir: str | None = None):
    """Mean-field ADVI - the PE-package fast path for CV / exploration.

    Caveat (same as the PE methodology states): mean-field VI ignores posterior
    correlations, uncertainty intervals are optimistic and R-hat is
    unavailable (single 'chain'). Use for relative comparison across folds or
    configs; refit the final model with NUTS.
    """
    to = time.time()
    with model:
        approx = pm.fit(n=scfg.advi_iters, method="advi", random_seed=scfg.seed,
                        progressbar=False)
    idata = approx.sample(draws=scfg.draws * scfg.chains)
    log = {"backend_info": describe_backend(), "versions": package_versions(),
           "sampler_requested": "advi", "sampler_used": "advi",
           "advi_iters": scfg.advi_iters,
           "posterior_draws": scfg.draws * scfg.chains, "seed": scfg.seed,
           "wall_seconds": round(time.time() - to, 1),
           "note": "mean-field ADVI: intervals optimistic, no R-hat; "
                   "use NUTS for the final fit"}
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "sampling_log.json"), "w") as f:
            json.dump(log, f, indent=2, default=str)
    print(f"[fit] ADVI ({scfg.advi_iters} iters) in {log['wall_seconds']}s")
    return idata
```



# model.py

```
"""One joint hierarchical model over all regions (Codebase 1).

Replaces the per-region loop of production_code.py with a single vectorised
PyMC model. Structure per region g, time t (all on the scaled data):

    y[g,t] = alpha_g                                region intercept (pooled)
            + Fourier seasonality (optional, global)
            + trend_g * t (optional, pooled)
            + sum_j beta[g,j] * X[g,t,j]            features, pooled by config
    y ~ Normal(mu, sigma_g)    or StudentT

Coefficients are built in vectorised "buckets" (hierarchical/global x
positive/negative/free) using the non-centred parameterisation
(mu + tau * z, z~N(0,1)) - the same construction as Meridian's
beta_gm = beta_m + eta_m * N(0,1) and the PE model's hierarchy.
Sign constraints are structural: beta = +/- exp(...) can never cross zero.
"""

from __future__ import annotations

import numpy as np
import pymc as pm
import pytensor.tensor as pt

from config import ModelConfig
from data_prep import PreparedData


def _bucket_betas(model, bname, specs, n_regions):
    """Create one vectorised coefficient block; returns tensor (region, k)."""
    dim = f"feat_{bname}"
    model.add_coord(dim, [s.name for s in specs])
    hier = specs[0].hierarchical
    sign = specs[0].sign

    if sign == "free":
        loc = np.array([s.prior_mean for s in specs])
        sd = np.array([s.prior_sd for s in specs])
        if hier:
            mu = pm.Normal(f"mu_beta_{bname}", mu=loc, sigma=sd, dims=dim)
            tau = pm.HalfNormal(f"tau_beta_{bname}",
                                sigma=np.array([s.regional_sd for s in specs]), dims=dim)
            z = pm.Normal(f"z_beta_{bname}", 0.0, 1.0, dims=("region", dim))
            beta = mu[None, :] + tau[None, :] * z
            pm.Deterministic(f"pop_beta_{bname}", mu, dims=dim)
        else:
            b = pm.Normal(f"gbeta_{bname}", mu=loc, sigma=sd, dims=dim)
            beta = pt.ones((n_regions, 1)) * b[None, :]
    else:
        sgn = 1.0 if sign == "positive" else -1.0
        loc = np.log(np.array([s.prior_mean for s in specs]))
        sd = np.array([s.prior_sd for s in specs])
        if hier:
            mu = pm.Normal(f"mu_logbeta_{bname}", mu=loc, sigma=sd, dims=dim)
            tau = pm.HalfNormal(f"tau_logbeta_{bname}",
                                sigma=np.array([s.regional_sd for s in specs]), dims=dim)
            z = pm.Normal(f"z_beta_{bname}", 0.0, 1.0, dims=("region", dim))
            beta = sgn * pt.exp(mu[None, :] + tau[None, :] * z)
            # population MEDIAN effect (log-normal hierarchy: exp(mu) is the median)
            pm.Deterministic(f"pop_beta_{bname}", sgn * pt.exp(mu), dims=dim)
        else:
            lb = pm.Normal(f"glogbeta_{bname}", mu=loc, sigma=sd, dims=dim)
            beta = sgn * (pt.ones((n_regions, 1)) * pt.exp(lb)[None, :])
    return pm.Deterministic(f"beta_{bname}", beta, dims=("region", dim))


def build_model(pdata: PreparedData, cfg: ModelConfig) -> pm.Model:
    m_tr = pdata.train_mask
    reg = pdata.region_idx[m_tr]
    G = len(pdata.region_names)
    coords = {"region": pdata.region_names}

    with pm.Model(coords=coords) as model:
        terms = []

        # baseline: hierarchical region intercept (Meridian: tau_g)
        mu_a = pm.Normal("mu_alpha", 0.0, cfg.alpha_prior_sd)
        tau_a = pm.HalfNormal("tau_alpha", cfg.alpha_regional_sd)
        z_a = pm.Normal("z_alpha", 0.0, 1.0, dims="region")
        alpha = pm.Deterministic("alpha_region", mu_a + tau_a * z_a, dims="region")
        terms.append(alpha[reg])

        # seasonality (light version of Meridian's spline mu_t)
        if pdata.X_fourier is not None:
            model.add_coord("fourier", pdata.fourier_names)
            bf = pm.Normal("beta_fourier", 0.0, 0.3, dims="fourier")
            terms.append(pt.dot(pdata.X_fourier[m_tr], bf))

        if cfg.include_trend:
            mu_t = pm.Normal("mu_trend", 0.0, 0.2)
            tau_t = pm.HalfNormal("tau_trend", 0.2)
            z_t = pm.Normal("z_trend", 0.0, 1.0, dims="region")
            btr = pm.Deterministic("beta_trend_region", mu_t + tau_t * z_t, dims="region")
            terms.append(btr[reg] * pdata.t[m_tr])

        # feature buckets
        for bname, specs in pdata.buckets.items():
            if not specs:
                continue
            cols = [pdata.feature_index[s.name] for s in specs]
            X_b = pdata.X[m_tr][:, cols]
            beta = _bucket_betas(model, bname, specs, G)
            terms.append((pt.constant(X_b) * beta[reg]).sum(axis=1))

    mu = sum(terms)

    # noise: one sigma per region. Default = partial pooling on the log scale
    # so short/noisy regions borrow strength for their noise level too.
    if cfg.pool_sigma:
        mu_ls = pm.Normal("mu_log_sigma", -0.5, 1.0)
        tau_ls = pm.HalfNormal("tau_log_sigma", 0.5)
        z_ls = pm.Normal("z_log_sigma", 0.0, 1.0, dims="region")
        sigma = pm.Deterministic("sigma_region",
                                pt.exp(mu_ls + tau_ls * z_ls), dims="region")
    else:
        sigma = pm.HalfNormal("sigma_region", 1.0, dims="region")
    s_obs = sigma[reg]

    if cfg.likelihood == "student_t":
        nu_raw = pm.Exponential("nu_minus_2", 0.1)
        nu = pm.Deterministic("nu", nu_raw + 2.0)
        pm.StudentT("y_obs", nu=nu, mu=mu, sigma=s_obs, observed=pdata.y[m_tr])
    else:
        pm.Normal("y_obs", mu=mu, sigma=s_obs, observed=pdata.y[m_tr])

    return model
```



outputs.py

```
"""Post-fit outputs: coefficients, fit quality, contribution decomposition.
```

```
Everything is computed from posterior DRAWS (never from combined summaries):
point estimate      = posterior median
uncertainty         = true 90% HDI (shortest interval, not equal-tail percentiles)
"significance"      = P(effect > 0) and whether the HDI excludes zero
```

```
Two kinds of interval are reported and must not be confused:
mean-response interval      uncertainty about the EXPECTED sales level
posterior predictive interval adds likelihood noise - use THIS for holdout
                                coverage and "where will next week actually land"
```

```
Coefficient units: the model works on scaled axes (KPI standardised per region,
features scaled per region), so scaled coefficients are comparable across
regions in "relative response" terms. The report also converts each regional
coefficient back to ORIGINAL units (KPI units per raw feature unit), where
equal scaled effects generally differ - both are exported, explicitly labelled.
"""
```

```
from __future__ import annotations
```

```
import os
from dataclasses import dataclass
```

```
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd
```

```
from config import ModelConfig, RunConfig
from data_prep import PreparedData
```

```
from compat import get_group, has_group
```

```
 
# posterior access helpers
 
```

```
def stack_posterior(idata, n_draws: int | None = None, seed: int = 0):
    posterior = get_group(idata, "posterior")
    post = posterior.stack(sample=("chain", "draw"))
```

```
    total = post.sizes["sample"]
```

```
    if n_draws and n_draws < total:
        rng = np.random.default_rng(seed)
        sel = np.sort(
            rng.choice(
                total,
                n_draws,
                replace=False,
            )
        )
        post = post.isel(sample=sel)
```

```
    return post
```

```
def _hdi(x: np.ndarray, prob: float = 0.90) -> tuple[float, float]:
    """Shortest-interval (highest-density) interval from draws."""
    x = np.sort(np.asarray(x, dtype=float))
    n = len(x)
    m = max(1, int(np.floor(prob * n)))
    if m >= n:
        return float(x[0]), float(x[-1])
    widths = x[m:] - x[:n - m]
    i = int(np.argmax(widths))
    return float(x[i]), float(x[i + m])
```

```
def _stats(x: np.ndarray, prob: float = 0.90) -> dict:
    lo, hi = _hdi(x, prob)
    return {"mean": float(np.mean(x)), "sd": float(np.std(x)),
            "median": float(np.median(x)),
            "hdi_low": lo, "hdi_high": hi,
            "prob_positive": float((x > 0).mean()),
            "excludes_zero": bool(lo > 0 or hi < 0)}
```

```
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
```

```
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
```

```
 
# decomposition
 
```

```
@dataclass
class Decomposition:
    yhat_draws: np.ndarray  # (S, n_obs) mean response, original units
    ypred_draws: np.ndarray  # (S, n_obs) posterior PREDICTIVE, original units
    baseline_draws: np.ndarray  # (S, n_obs) original units
    contrib_median: dict  # feature -> (n_obs,) original units
    contrib_totals: dict  # feature -> (S, G) totals in original units
    baseline_totals: np.ndarray  # (S, G)
```

```
def compute_decomposition(idata, pdata: PreparedData, cfg: ModelConfig,
                          n_draws: int = 400, seed: int = 0) -> Decomposition:
    post = stack_posterior(idata, n_draws, seed)
    reg = pdata.region_idx
    G = len(pdata.region_names)
    S = post.sizes["sample"]
```

```
    alpha = post["alpha_region"].transpose("sample", "region").values
    base = alpha[:, reg]
    if pdata.X_fourier is not None and "beta_fourier" in post:
        bf = post["beta_fourier"].transpose("sample", "fourier").values
        base = base + bf @ pdata.X_fourier.T
    if "beta_trend_region" in post:
        btr = post["beta_trend_region"].transpose("sample", "region").values
        base = base + btr[:, reg] * pdata.t[None, :]
```

```
    yhat = base.copy()
    contrib_median, contrib_totals = {}, {}
    sg = pdata.y_scale[reg]
```

```
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
```

```
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
```

```
    yhat_o = yhat * sg[None, :] + pdata.y_mean[reg][None, :]
    ypred_o = ypred * sg[None, :] + pdata.y_mean[reg][None, :]
    base_o = base * sg[None, :] + pdata.y_mean[reg][None, :]
    base_totals = np.zeros((S, G))
    for g in range(G):
        base_totals[:, g] = base_o[:, reg == g].sum(axis=1)
    return Decomposition(yhat_o, ypred_o, base_o, contrib_median, contrib_totals,
                        base_totals)
```

```
 
# coefficient report
 
```

```
def _support_flag(sign: str, n_active: int, sd: float) -> str:
    if sd == 0 or (sign != "free" and n_active == 0):
        return "none"
    if sign != "free" and n_active < 8:
        return "weak"
    return "adequate"
```

```
def coefficient_report(idata, pdata: PreparedData, outdir: str,
                      make_forest_plots: bool = True) -> pd.DataFrame:
```

```
    os.makedirs(outdir, exist_ok=True)
    post = stack_posterior(idata)
    bd = beta_draws_by_feature(post, pdata)
    popd = population_draws_by_feature(post, pdata)
```

```
    spec_by_name = {s.name: s for specs in pdata.buckets.values() for s in specs}
    x_scale = {(r.region, r.feature): r.scale
                for r in pdata.x_scale_table.itertuples(index=False)}
```

```
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
```

```
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "coefficient_report.csv"), index=False)
```

```
    weak = df[df["data_support"].isin(["none", "weak"])]
    if len(weak):
        with open(os.path.join(outdir, "support_warnings.txt"), "w") as f:
            f.write("Region x feature combinations with little or no data support.\n"
                    "These coefficients are driven by the population hierarchy "
                    "(shrinkage), NOT learned from that region's data - do not "
                    "present them as regionally estimated.\n\n")
            f.write(weak[["feature", "region", "n_active_train",
                          "data_support"]].to_string(index=False))
```

```
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
                       fmt="o", ms=4, lw=1.2, capsiz=2)
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


# -----
# fit quality
# -----
def _crps(pred_draws: np.ndarray, actual: np.ndarray) -> float:
    """Sample-based CRPS (proper scoring rule: calibration AND sharpness),
    averaged over observations. Lower is better; units = KPI units.
    pred_draws (S, n) posterior predictive draws; actual (n,)."""
    S = pred_draws.shape[0]
    term1 = np.mean(np.abs(pred_draws - actual[None, :]))
    x = np.sort(pred_draws, axis=0)
    i = np.arange(1, S + 1)[: , None]
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
    wmape_pct      observation-weighted: "what fraction of total
                   volume does the total error represent?"
    mape_region_weighted_pct  volume-weighted average of per-region MAPes:
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
        all_row = {"region": " _all_ ", "dataset": which,
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

# -----
# contributions
# -----
def contribution_report(decomp: Decomposition, pdata: PreparedData, outdir: str,
                      top_n: int = 20) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    G = len(pdata.region_names)
    actual_by_region = np.array([pdata.y_orig[pdata.region_idx == g].sum()
                                for g in range(G)])
    total_actual = actual_by_region.sum()

    rows = []
    items = list(decomp.contrib_totals.items()) + [{"_baseline_", decomp.baseline_totals}]
    for name, tot in items:
        port = tot.sum(axis=1) # (S,)
        rows.append({"feature": name, "region": " _portfolio_ ", **_stats(port),
                     "share_of_actual_pct": float(np.median(port) / total_actual * 100)})
        for g, r in enumerate(pdata.region_names):
            rows.append({"feature": name, "region": r, **_stats(tot[:, g]),
                         "share_of_actual_pct":
                             float(np.median(tot[:, g]) / actual_by_region[g] * 100)})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "contribution_totals.csv"), index=False)

    # portfolio bar chart with uncertainty
    port = df[(df["region"] == " _portfolio_ ") & (df["feature"] != "_baseline_ ")]
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

    prior_predictive = get_group(
        idata,
        "prior_predictive",
    )

    if "y_obs" not in prior_predictive:
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
```




# run\_cv.py

```
import pandas as pd

from config import (
    CVConfig,
    ModelConfig,
    RunConfig,
    SamplerConfig,
    load_feature_config,
)

from cross_validation import run_cv

df = pd.read_excel(
    "input_datacube.xlsx",
    engine="openpyxl",
)

features = load_feature_config(
    "feature_priors.csv"
)

model_config = ModelConfig(
    features=features,
    likelihood="student_t",
    fourier_order=2,
    include_trend=True,
    pool_sigma=True,
)

run_config = RunConfig(
    run_name="regional_mmm_initial",
    output_dir="outputs",
    date_col="date",
    region_col="region",
    dv_col="dv",
    report_draws=300,
)

sampler_config = SamplerConfig(
    sampler="numpyro",
    draws=600,
    tune=600,
    chains=2,
    target_accept=0.95,
    seed=42,
    nuts_kwargs={
        "chain_method": "vectorized",
    },
)

cv_config = CVConfig(
    horizon=13,
    n_folds=5,
    step=13,
    min_train_periods=52,
    draws=600,
    tune=600,
    make_plots=True,
)

cv_result = run_cv(
    df=df,
    model_cfg=model_config,
    run_cfg=run_config,
    sampler_cfg=sampler_config,
    cv_cfg=cv_config,
)

print("CV output:", cv_result["output_dir"])
```

# run\_pipeline.py

```
"""End-to-end pipeline for Codebase 1 (hierarchical MMM on pre-transformed data).

Stages and their output folders (under output_dir/run_name/):
01_data panel summary, scaling stats, KPI plots
02_convergence sampling log, R-hat/ESS/divergences, contraction, prior check
03_coefficients coefficient report CSV + forest plots
04_fit fit metrics (R2/MAPE/wMAPE/coverage), actual-vs-fitted, residuals
05_contributions contribution totals CSV, bars, weekly decomposition

Typical use (e.g. from a Databricks notebook):

import pandas as pd
from config import ModelConfig, RunConfig, SamplerConfig, load_feature_config
from run_pipeline import run

df = pd.read_excel("../input_datacube.xlsx") # date, region, dv, features
features = load_feature_config("../feature_priors.csv")
result = run(df,
             ModelConfig(features=features, fourier_order=2, include_trend=True),
             RunConfig(run_name="fy26_refresh", holdout_periods=13),
             SamplerConfig(sampler="numpyro"))

"""

from __future__ import annotations

import os

import arviz as az
import pandas as pd

from config import ModelConfig, RunConfig, SamplerConfig
from data_prep import prepare_data, write_data_stage_outputs
from diagnostics import (convergence_report, enforce_convergence,
                        prior_posterior_report, quick_convergence_checks)
from fit import fit, sample_prior
from model import build_model
from outputs import (coefficient_report, compute_decomposition,
                    contribution_report, fit_report, prior_predictive_plot)

def run(df: pd.DataFrame,
        model_cfg: ModelConfig,
        run_cfg: RunConfig | None = None,
        sampler_cfg: SamplerConfig | None = None,
        save_trace: bool = True):
    run_cfg = run_cfg or RunConfig()
    if run_cfg.on_convergence_failure not in {"warn", "fail"}:
        raise ValueError(
            "on_convergence_failure must be 'warn' or 'fail'"
        )

    sampler_cfg = sampler_cfg or SamplerConfig()
    root = os.path.join(run_cfg.output_dir, run_cfg.run_name)
    dirs = {k: os.path.join(root, k) for k in
            ["01_data", "02_convergence", "03_coefficients", "04_fit",
             "05_contributions"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    print("[1/5] preparing data")
    pdata = prepare_data(df, run_cfg, model_cfg)
    for region, group in df.groupby("region"):
        columns = [
            "price_index",
            "distribution",
            "temperature",
        ]

        print(f"\nregion: {region}")
        print(
            group[columns]
            .corr()
            .round(3)
        )

    scaled_df = pd.DataFrame(
        pdata.x,
        columns=pdata.feature_names,
    )

    scaled_df["region"] = [
        pdata.region_names[index]
        for index in pdata.region_idx
    ]

    for region, group in scaled_df.groupby(
        "region"
    ):
        print(f"\nscaled correlations: {region}")

        print(
            group[
                [
                    "price_index",
                    "distribution",
                    "temperature",
                ]
            ]
            .corr()
            .round(3)
        )

    write_data_stage_outputs(pdata, dirs["01_data"])

    print("[2/5] building + sampling model")
    model = build_model(pdata, model_cfg)
    prior_idata = sample_prior(model, sampler_cfg)
    idata = fit(model, sampler_cfg, outdir=dirs["02_convergence"])
    idata.update(prior_idata)

    print("[3/5] convergence diagnostics")
    convergence_report(idata, dirs["02_convergence"])
    prior_posterior_report(idata, dirs["02_convergence"])
    prior_predictive_plot(idata, pdata, dirs["02_convergence"])
    enforce_convergence(quick_convergence_checks(idata),
                       run_cfg.on_convergence_failure)

    print("[4/5] coefficients + fit quality")
    coef = coefficient_report(idata, pdata, dirs["03_coefficients"])
    decomp = compute_decomposition(idata, pdata, model_cfg,
                                   n_draws=run_cfg.report_draws)
    metrics = fit_report(decomp, pdata, dirs["04_fit"])

    print("[5/5] contributions")
    contrib = contribution_report(
        decomp,
        pdata,
        dirs["05_contributions"],
    )

    if save_trace:
        trace_path = os.path.join(root, "trace.nc")
        idata.to_netcdf(trace_path)
        print(f"[save] trace -> {trace_path}")

    print(f"[done -> {root}]")

    return {
        "idata": idata,
        "pdata": pdata,
        "decomposition": decomp,
        "coefficients": coef,
        "metrics": metrics,
        "contributions": contrib,
        "output_dir": root,
    }
```

# synthetic\_example.py

```
"""Runnable smoke test + parameter-recovery check for Codebase 1.

Generates a synthetic panel with MMM regional coefficients (pre-transformed
media proxies + controls), runs the full pipeline, and prints true vs
recovered coefficients. Run: python synthetic_example.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import FeatureSpec, ModelConfig, RunConfig, SamplerConfig
from run_pipeline import run

RNG = np.random.default_rng(7)

def make_data(
    G=4,
    T=156,
    holdout_periods=13,
):
    regions = [
        f"R{i + 1}"
        for i in range(G)
    ]

    dates = pd.date_range(
        "2023-01-02",
        periods=T,
        freq="d-MON",
    )

    train_end = T - holdout_periods
    train_mask = np.arange(T) < train_end

    def media_series():
        x = np.zeros(T)

        for _ in range(
            RNG.integers(6, 10)
        ):
            start = RNG.integers(
                0,
                T - 8,
            )

            duration = RNG.integers(
                3,
                8,
            )

            x[
                start:start + duration
            ] += RNG.gamma(
                2,
                2,
            )

        return np.convolve(
            x,
            [0.5, 0.3, 0.2],
            mode="same",
        )

    feature_signs = {
        "tv": "positive",
        "digital": "positive",
        "print": "positive",
        "price_index": "negative",
        "distribution": "free",
        "temperature": "free",
    }

    rows = []
    effective_truth = {}

    for g, region in enumerate(regions):
        X = {
            "tv": media_series(),
            "digital": media_series(),
            "print": media_series() * 0.3,
            "price_index": (
                100
                + np.cumsum(
                    RNG.normal(
                        0,
                        0.3,
                        T,
                    )
                )
            ),
            "distribution": (
                60
                + np.cumsum(
                    RNG.normal(
                        0,
                        0.15,
                        T,
                    )
                )
            ),
            "temperature": (
                20
                + 8
                * np.sin(
                    2
                    * np.pi
                    * np.arange(T)
                    / 52.18
                )
                + RNG.normal(
                    0,
                    1.5,
                    T,
                )
            ),
        }
        raw_beta = {
            "tv": np.exp(
                RNG.normal(
                    np.log(0.10),
                    0.25,
                )
            ),
            "digital": np.exp(
                RNG.normal(
                    np.log(0.05),
                    0.25,
                )
            ),
            "print": np.exp(
                RNG.normal(
                    np.log(0.02),
                    0.25,
                )
            ),
            "price_index": -np.exp(
                RNG.normal(
                    np.log(0.30),
                    0.20,
                )
            ),
            "distribution": RNG.normal(
                0.25,
                0.05,
            ),
            "temperature": RNG.normal(
                0.10,
                0.03,
            ),
        }

        X_scaled = {}

        for name, sign in feature_signs.items():
            values = X[name]

            if sign == "free":
                train_mean = values[
                    train_mask
                ].mean()

                train_sd = values[
                    train_mask
                ].std()

                if train_sd <= 0:
                    train_sd = 1.0

                X_scaled[name] = (
                    values - train_mean
                ) / train_sd

            else:
                positive_train_values = values[
                    train_mask
                    & (values > 0)
                ]

                feature_scale = (
                    positive_train_values.mean()
                    if len(
                        positive_train_values
                    )
                    else 1.0
                )

                X_scaled[name] = (
                    values / feature_scale
                )

        latent = np.zeros(T)

        for name in feature_signs:
            latent += (
                raw_beta[name]
                * X_scaled[name]
            )

        latent += (
            0.25
            * np.sin(
                2
                * np.pi
                * np.arange(T)
                / 52.18
            )
        )

        latent += RNG.normal(
            0,
            0.30,
            T,
        )

        # Match the pipeline's regional KPI standardization.
        latent_train_mean = latent[
            train_mask
        ].mean()

        latent_train_sd = latent[
            train_mask
        ].std()

        if latent_train_sd <= 0:
            latent_train_sd = 1.0

        latent_standardized = (
            latent - latent_train_mean
        ) / latent_train_sd

        # These are the coefficients on the exact scale
        # estimated by the model.
        effective_truth[region] = {
            name: (
                raw_beta[name]
                / latent_train_sd
            )
            for name in raw_beta
        }

        original_mean = (
            1000 * (g + 2)
        )

        original_scale = (
            150 * (g + 1)
        )

        y = (
            original_mean
            + original_scale
            * latent_standardized
        )

        for t in range(T):
            rows.append(
                {
                    "date": dates[t],
                    "region": region,
                    "dv": y[t],
                    **{
                        name: X[name][t]
                        for name in X
                    },
                }
            )

    return (
        pd.DataFrame(rows),
        effective_truth,
        feature_signs,
    )

def main():
    df, true, feats = make_data()
    features = [FeatureSpec(name=n, hierarchical=True, signs=s,
                            prior_mean=(0.05 if s != "free" else 0.0))
                for n, s in feats.items()]

    res = run(
        df=df,
        model_cfg=ModelConfig(
            features=features,
            fourier_order=2,
            include_trend=False,
            likelihood="normal",
            pool_sigma=True,
        ),
        run_cfg=RunConfig(
            run_name="synthetic_recovery_corrected",
            holdout_periods=13,
            report_draws=500,
            on_convergence_failure="warn",
        ),
        sampler_cfg=SamplerConfig(
            sampler="numpyro",
            tune=2000,
            target_accept=0.99,
            chains=4,
            store_log_likelihood=False,
            allow_sampler_fallback=False,
        ),
    )

    coef = res["coefficients"]
    print(f"\n== parameter recovery (posterior median vs truth) ==")
    for name in feats:
        sub = coef[(coef.feature == name) & (coef.region != "_population_")]
        for _, row in sub.iterrows():
            t = true[row.region][name]
            hit = row["hdi_low"] <= t <= row["hdi_high"]
            print(f"[name: {name}] ({row.region}): true={t:+.3f} est={row['median']:+.3f} "
                  f"[{row['hdi_low']:+.3f},{row['hdi_high']:+.3f}] "
                  f"[{'OK' if hit else 'Miss'}]")

if __name__ == "__main__":
    main()
```