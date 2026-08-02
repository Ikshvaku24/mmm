"""Data validation, panel construction and scaling for the full MMM (Codebase 2).

Input: one long DataFrame [date, region, dv, raw media cols, spend cols,
control cols]. Media must be RAW (never pre-adstocked / pre-saturated).

Internally pivoted to a rectangular panel (region G x time T), because the
adstock convolution runs along the time axis. Scaling mirrors Meridian:
  - KPI standardised per region                       (KpiTransformer)
  - media / (region, channel) median of positive weeks (MediaTransformer -
    no centering: zero activity stays exactly zero)
  - controls centred + scaled per region               (ControlsTransformer)
"""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import ModelConfig, RunConfig, bucket_features


def fourier_features(dates: pd.DatetimeIndex, order: int, period_days: float = 365.25):
    t_days = (dates - dates.min()).days.to_numpy(dtype=float)
    cols, names = [], []
    for k in range(1, order + 1):
        ang = 2.0 * np.pi * k * t_days / period_days
        cols += [np.sin(ang), np.cos(ang)]
        names += [f"sin_{k}", f"cos_{k}"]
    if not cols:
        return None, []
    return np.column_stack(cols), names


def make_lag_tensor(M: np.ndarray, L: int) -> np.ndarray:
    """(G,T,C) -> (G,T,L,C): slot l holds the media value l periods ago
    (zero-padded before the series start)."""
    G, T, C = M.shape
    out = np.zeros((G, T, L, C), dtype=float)
    for l in range(L):
        out[:, l:, l, :] = M[:, :T - l, :]
    return out


@dataclass
class PanelData:
    region_names: list
    dates: pd.DatetimeIndex          # (T,)
    T_train: int
    y_orig: np.ndarray               # (G,T)
    y: np.ndarray                    # (G,T) scaled
    y_mean: np.ndarray               # (G,)
    y_scale: np.ndarray              # (G,)
    # transformed media channels
    channel_names: list
    M_scaled: np.ndarray             # (G,T,C)
    media_scale: np.ndarray          # (G,C) per-region medians of positive weeks
    Mlag: np.ndarray                 # (G,T,L,C)
    lag_mask: np.ndarray             # (L,C) 1 where lag < channel max_lag
    L: int
    spend: dict                      # channel name -> (G,T) raw spend (may be empty)
    # linear features (controls + 'none' channels)
    X_lin: np.ndarray                # (G,T,K) scaled (K may be 0)
    lin_names: list
    lin_index: dict
    buckets: dict
    # baseline features
    X_fourier: np.ndarray | None     # (T, 2*order)
    fourier_names: list
    t: np.ndarray                    # (T,)
    x_scale_table: pd.DataFrame = field(default=None)

    @property
    def n_regions(self):
        return len(self.region_names)

    @property
    def T(self):
        return len(self.dates)


def prepare_data(df: pd.DataFrame, run_cfg: RunConfig, cfg: ModelConfig) -> PanelData:
    d = df.copy()
    dc, rc, yc = run_cfg.date_col, run_cfg.region_col, run_cfg.dv_col

    need = [dc, rc, yc]
    missing = [c for c in need if c not in d.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    ch_cols = [c.col for c in cfg.channels]
    spend_cols = [c.spend_col for c in cfg.channels if c.spend_col]
    ctl_cols = [s.name for s in cfg.controls]
    missing = [c for c in ch_cols + spend_cols + ctl_cols if c not in d.columns]
    if missing:
        raise ValueError(f"configured columns not in data: {missing}")

    d[dc] = pd.to_datetime(d[dc])
    d = d.sort_values([rc, dc]).reset_index(drop=True)
    if d.duplicated([rc, dc]).any():
        raise ValueError("duplicate (region, date) rows - aggregate upstream")

    regions = sorted(d[rc].unique().tolist())
    dates = pd.DatetimeIndex(sorted(d[dc].unique()))
    G, T = len(regions), len(dates)

    # rectangular grid check
    full = pd.MultiIndex.from_product([regions, dates], names=[rc, dc])
    d = d.set_index([rc, dc]).reindex(full)
    n_missing_rows = int(d[yc].isna().sum())
    if run_cfg.zero_fill_media:
        fill_cols = [c for c in ch_cols + spend_cols if c in d.columns]
        n_filled = int(d[fill_cols].isna().sum().sum())
        if n_filled:
            warnings.warn(f"zero-filled {n_filled} missing media/spend cells "
                          "(inactive weeks)")
        d[fill_cols] = d[fill_cols].fillna(0.0)
    if d[yc].isna().any():
        bad = d[d[yc].isna()].index[:10].tolist()
        raise ValueError(
            f"KPI missing for {n_missing_rows} (region, date) cells, e.g. {bad}. "
            "The panel must be rectangular in the KPI - investigate, don't zero-fill.")
    bad_ctl = [c for c in ctl_cols if d[c].isna().any()]
    if bad_ctl:
        raise ValueError(f"controls contain NaN: {bad_ctl}")

    neg = [c for c in ch_cols + spend_cols if (d[c] < 0).any()]
    if neg:
        raise ValueError(f"media/spend must be non-negative: {neg}")

    def pivot(col):  # -> (G,T)
        return d[col].to_numpy(dtype=float).reshape(G, T)

    # ---- train split (by time) --------------------------------------------
    T_train = T - run_cfg.holdout_periods if run_cfg.holdout_periods > 0 else T
    if T_train < 20:
        raise ValueError("fewer than 20 training periods after holdout")

    # ---- KPI scaling -------------------------------------------------------
    y_orig = pivot(yc)
    y_mean = y_orig[:, :T_train].mean(axis=1)
    y_scale = y_orig[:, :T_train].std(axis=1)
    y_scale[y_scale <= 0] = 1.0
    y = (y_orig - y_mean[:, None]) / y_scale[:, None]

    # ---- transformed media -------------------------------------------------
    tch = cfg.transformed_channels
    C = len(tch)
    M = np.stack([pivot(c.col) for c in tch], axis=-1) if C else np.zeros((G, T, 0))
    media_scale = np.ones((G, C))
    scale_rows = []
    for j, c in enumerate(tch):
        for g in range(G):
            v = M[g, :T_train, j]
            pos = v[v > 0]
            sc = np.median(pos) if len(pos) else 1.0
            media_scale[g, j] = sc if sc > 0 else 1.0
            scale_rows.append((regions[g], c.name, "median_positive", 0.0,
                               media_scale[g, j]))
        if (M[:, :T_train, j].sum(axis=1) == 0).any():
            dead = [regions[g] for g in range(G) if M[g, :T_train, j].sum() == 0]
            warnings.warn(f"channel {c.name} has all-zero training activity in "
                          f"regions {dead} - its coefficient there is prior-driven")
    M_scaled = M / media_scale[:, None, :] if C else M

    L = max([c.max_lag for c in tch], default=1)
    Mlag = make_lag_tensor(M_scaled, L) if C else np.zeros((G, T, L, 0))
    lag_mask = np.zeros((L, C))
    for j, c in enumerate(tch):
        lag_mask[:c.max_lag, j] = 1.0

    spend = {}
    for c in cfg.channels:
        if c.spend_col:
            spend[c.name] = pivot(c.spend_col)

    # ---- linear features ----------------------------------------------------
    lin_specs = cfg.linear_features
    K = len(lin_specs)
    X_lin = np.zeros((G, T, K))
    for k, s in enumerate(lin_specs):
        v = pivot(s.name if s.name in d.columns else
                  next(c.col for c in cfg.linear_channels if c.name == s.name))
        for g in range(G):
            tr = v[g, :T_train]
            if s.sign == "free":
                mu, sd = tr.mean(), tr.std()
                sd = sd if sd > 0 else 1.0
                X_lin[g, :, k] = (v[g] - mu) / sd
                scale_rows.append((regions[g], s.name, "center_scale", mu, sd))
            else:
                pos = tr[tr > 0]
                sc = pos.mean() if len(pos) else 1.0
                X_lin[g, :, k] = v[g] / sc
                scale_rows.append((regions[g], s.name, "scale_only", 0.0, sc))

    Xf, f_names = fourier_features(dates, cfg.fourier_order, cfg.fourier_period_days)
    t_days = (dates - dates.min()).days.to_numpy(dtype=float)
    span = t_days[T_train - 1] or 1.0
    t = t_days / span

    return PanelData(
        region_names=regions, dates=dates, T_train=T_train,
        y_orig=y_orig, y=y, y_mean=y_mean, y_scale=y_scale,
        channel_names=[c.name for c in tch], M_scaled=M_scaled,
        media_scale=media_scale, Mlag=Mlag, lag_mask=lag_mask, L=L, spend=spend,
        X_lin=X_lin, lin_names=[s.name for s in lin_specs],
        lin_index={s.name: k for k, s in enumerate(lin_specs)},
        buckets=bucket_features(lin_specs),
        X_fourier=Xf, fourier_names=f_names, t=t,
        x_scale_table=pd.DataFrame(
            scale_rows, columns=["region", "feature", "method", "center", "scale"]),
    )


def write_data_stage_outputs(p: PanelData, outdir: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    rows = []
    for g, r in enumerate(p.region_names):
        rows.append({"region": r, "n_periods": p.T, "n_train": p.T_train,
                     "dv_mean": p.y_orig[g].mean(), "dv_sd": p.y_orig[g].std(),
                     "date_min": str(p.dates.min().date()),
                     "date_max": str(p.dates.max().date())})
    pd.DataFrame(rows).to_csv(os.path.join(outdir, "panel_summary.csv"), index=False)
    p.x_scale_table.to_csv(os.path.join(outdir, "feature_scaling_stats.csv"), index=False)
    pd.DataFrame(p.media_scale, index=p.region_names, columns=p.channel_names) \
        .to_csv(os.path.join(outdir, "media_scale_medians.csv"))

    G = p.n_regions
    ncol = min(4, G)
    nrow = int(np.ceil(G / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.5 * ncol, 2.6 * nrow),
                             squeeze=False, sharex=True)
    for g, r in enumerate(p.region_names):
        ax = axes[g // ncol][g % ncol]
        ax.plot(p.dates, p.y_orig[g], lw=0.9)
        if p.T_train < p.T:
            ax.axvspan(p.dates[p.T_train], p.dates[-1], alpha=0.15, color="orange")
        ax.set_title(str(r), fontsize=9)
    for k in range(G, nrow * ncol):
        axes[k // ncol][k % ncol].axis("off")
    fig.suptitle("KPI by region (orange = holdout)")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "kpi_by_region.png"), dpi=130)
    plt.close(fig)
