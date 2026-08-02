"""Data validation, scaling and packaging for the hierarchical MMM (Codebase 1).

Input contract: one long ("stacked") pandas DataFrame with columns
    [date_col, region_col, dv_col, <feature columns...>]
one row per region x period. Features arrive ALREADY transformed
(adstock / saturation / lag done in preprocessing) - this codebase does not
transform them further, it only scales for sampler geometry.

Scaling (stats computed on the TRAINING window only, stored for reuse):
  - dv:            standardised per region                  (Meridian: KPI transformer)
  - signed feats:  / per-region mean of positive values     (Meridian: media transformer -
                   no centering, zero stays zero)
  - free feats:    centred + scaled per region              (Meridian: controls transformer)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import FeatureSpec, ModelConfig, RunConfig, bucket_features


def fourier_features(dates: pd.Series, order: int, period_days: float = 365.25):
    """Annual-cycle sin/cos features (a light version of Meridian's spline mu_t)."""
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
    dates: pd.Series                # (n_obs,)
    y_orig: np.ndarray              # (n_obs,) original units
    y: np.ndarray                   # (n_obs,) scaled
    y_mean: np.ndarray              # (G,)
    y_scale: np.ndarray             # (G,)
    X: np.ndarray                   # (n_obs, k) scaled
    feature_names: list
    feature_index: dict
    buckets: dict                   # bucket name -> list[FeatureSpec]
    X_fourier: np.ndarray | None
    fourier_names: list
    t: np.ndarray                   # (n_obs,) trend feature, 0..1 over train window
    train_mask: np.ndarray          # (n_obs,) bool
    test_mask: np.ndarray
    x_scale_table: pd.DataFrame = field(default=None)   # region x feature scaling stats

    def sel(self, which: str) -> np.ndarray:
        return {"train": self.train_mask, "test": self.test_mask,
                "all": np.ones_like(self.train_mask, dtype=bool)}[which]


def prepare_data(df: pd.DataFrame, run_cfg: RunConfig, model_cfg: ModelConfig) -> PreparedData:
    d = df.copy()
    dc, rc, yc = run_cfg.date_col, run_cfg.region_col, run_cfg.dv_col

    # ---- validation -------------------------------------------------------
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
        raise ValueError(f"duplicate (region, date) rows, e.g.:\n{dup}")
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

    # ---- train / holdout split by date ------------------------------------
    all_dates = np.sort(d[dc].unique())
    if run_cfg.holdout_periods > 0:
        cutoff = all_dates[-run_cfg.holdout_periods]
        train_mask = (d[dc] < cutoff).to_numpy()
    else:
        train_mask = np.ones(len(d), dtype=bool)
    test_mask = ~train_mask

    # ---- dv scaling: standardise per region (train stats) -----------------
    y_orig = d[yc].to_numpy(dtype=float)
    y_mean = np.zeros(G)
    y_scale = np.ones(G)
    for g in range(G):
        m = (region_idx == g) & train_mask
        y_mean[g] = y_orig[m].mean()
        sd = y_orig[m].std()
        y_scale[g] = sd if sd > 0 else 1.0
    y = (y_orig - y_mean[region_idx]) / y_scale[region_idx]

    # ---- feature scaling ---------------------------------------------------
    X = np.zeros((len(d), len(feat_names)))
    scale_rows = []
    for j, spec in enumerate(model_cfg.features):
        v = d[spec.name].to_numpy(dtype=float)
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

    # ---- seasonality & trend ----------------------------------------------
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
