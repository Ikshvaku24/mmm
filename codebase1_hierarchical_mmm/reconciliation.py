"""Reconciliation outputs: re-derive every reported number from the data.

The core reports (coefficients, fit, contributions) answer "what did the model
conclude". These files answer "show me the arithmetic". Each one evidences one
link in the chain from a raw feature column to a percentage in the deck:

    raw feature      -> scaled feature      01_data/model_input_matrix.csv
                                            01_data/model_input_summary.csv
    scaled feature   -> contribution        05_contributions/contribution_math.csv
    contributions    -> fitted sales        05_contributions/contribution_reconciliation.csv
    fitted sales     -> actual sales        04_fit/actual_vs_predicted.csv
    everything       -> volume + % table    05_contributions/contribution_summary.csv

Which of them are written is controlled by `OutputConfig` (config.py).

Two arithmetics, deliberately kept apart - mixing them is the usual source of
"the numbers don't tie":

  median of the total     the posterior median of a feature's whole-window
                          total, with an HDI. This is what
                          `contribution_totals.csv` reports, and it is the right
                          number to quote WITH uncertainty. Medians of parts do
                          not add up to the median of the whole.
  sum of the medians      the per-week posterior median contribution, summed.
                          These DO add up, week by week, so this is the basis
                          for every reconciling table here. No HDI is attached,
                          because a sum of medians has no honest interval.

Both are printed side by side in `contribution_math.csv`, with the difference,
so the size of the gap is visible rather than assumed.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from config import OutputConfig

BASELINE_CORE = "__baseline_core__"
ACTUAL_ROW = "Sales Volume (Actual)"


# --------------------------------------------------------------------------
# reporting periods
# --------------------------------------------------------------------------
def period_labels(dates, mode: str = "mat") -> np.ndarray:
    """Label each observation with a reporting period.

    "none"  everything in one "Total" block
    "week"  every period is its own block, labelled with its ISO date. Gives a
            week-by-week volume and % report; the price is one block per date,
            so contribution_summary.csv grows by a factor of ~n_dates.
    "year"  calendar year
    "mat"   TWO moving-annual-total blocks anchored on the LAST date, the cut
            used by the vendor decomposition in snapshots/true_output/:
              MAT 2  the most recent 52 periods
              MAT 1  the 52 periods before those
            On the real panel (104 weeks, 2024-01-07 … 2025-12-28) that is
            MAT 1 = 2024 and MAT 2 = 2025 exactly.

            Panels that are not exactly 104 periods:
              longer   anything older than the last 104 goes to its own
                       "Pre-MAT" block rather than being folded into MAT 1,
                       which would make MAT 1 an unequal window
              shorter  there is no full year to roll, so the data is split in
                       half: MAT 1 = older half, MAT 2 = recent half. An odd
                       period goes to MAT 1, keeping the recent block clean.
            `n_periods` in contribution_summary.csv always states the block
            length, so an unequal split is never silent.

            The period length is inferred from the observed date spacing, so a
            monthly panel rolls 12 + 12 rather than 52 + 52.
    """
    dts = pd.DatetimeIndex(pd.to_datetime(pd.Series(np.asarray(dates))))
    if mode == "none":
        return np.array(["Total"] * len(dts), dtype=object)
    if mode == "week":
        # ISO dates so the blocks sort chronologically as text
        return dts.strftime("%Y-%m-%d").to_numpy(dtype=object)
    if mode == "year":
        return dts.year.astype(str).to_numpy(dtype=object)
    if mode != "mat":
        raise ValueError(f"unknown period_split {mode!r}")

    uniq = pd.DatetimeIndex(np.sort(dts.unique()))
    n = len(uniq)
    if n < 2:
        return np.array(["MAT 1"] * len(dts), dtype=object)
    gap_days = float(np.median(np.diff(uniq.to_numpy()).astype("timedelta64[D]")
                               .astype(float)))
    per_year = max(1, int(round(365.25 / gap_days))) if gap_days > 0 else n

    if n >= 2 * per_year:
        mat2 = per_year               # most recent full year
        mat1 = per_year               # the year before it
    else:
        mat2 = n // 2                 # no full year to roll - split in half,
        mat1 = n - mat2               # the odd period going to the older block

    lab = np.empty(n, dtype=object)
    lab[:] = "Pre-MAT"                # only survives when n > mat1 + mat2
    lab[n - mat1 - mat2: n - mat2] = "MAT 1"
    lab[n - mat2:] = "MAT 2"
    return pd.Series(lab, index=uniq).reindex(dts).to_numpy(dtype=object)


def _spec_map(pdata) -> dict:
    return {s.name: s for specs in pdata.buckets.values() for s in specs}


def _pillar_of(spec) -> str:
    """Same rule as contribution_report: baseline drivers report as Baseline."""
    if spec is None or spec.baseline:
        return "Baseline"
    return spec.pillar or "Unassigned"


def _region_col(pdata) -> np.ndarray:
    names = np.asarray(pdata.region_names, dtype=object)
    return names[pdata.region_idx]


# --------------------------------------------------------------------------
# 01_data - the transformed matrix the model actually receives
# --------------------------------------------------------------------------
def write_model_input(pdata, outdir: str, out_cfg: OutputConfig | None = None) -> None:
    out_cfg = out_cfg or OutputConfig()
    os.makedirs(outdir, exist_ok=True)
    if out_cfg.model_input_matrix:
        _model_input_matrix(pdata, out_cfg).to_csv(
            os.path.join(outdir, "model_input_matrix.csv"), index=False)
    if out_cfg.model_input_summary:
        _model_input_summary(pdata).to_csv(
            os.path.join(outdir, "model_input_summary.csv"), index=False)


def _model_input_matrix(pdata, out_cfg: OutputConfig) -> pd.DataFrame:
    """One row per region x period, exactly as handed to the sampler.

    `<feature>__scaled` IS the number multiplied by the coefficient. Together
    with `<feature>__raw` and the centre/scale in feature_scaling_stats.csv the
    whole transformation is reproducible in a spreadsheet:
        scaled = (raw - center) / scale     (center = 0 for scale_only features)
    """
    reg = pdata.region_idx
    df = pd.DataFrame({
        "region": _region_col(pdata),
        "date": pd.to_datetime(pdata.dates.values),
        "dataset": np.where(pdata.train_mask, "train", "test"),
        "dv_raw": pdata.y_orig,
        "dv_scaled": pdata.y,
        "dv_center_used": pdata.y_mean[reg],
        "dv_scale_used": pdata.y_scale[reg],
        "trend_t": pdata.t,
    })
    if pdata.X_fourier is not None:
        for k, name in enumerate(pdata.fourier_names):
            df[name] = pdata.X_fourier[:, k]
    have_raw = out_cfg.include_raw_features and pdata.X_raw is not None
    for j, name in enumerate(pdata.feature_names):
        if have_raw:
            df[f"{name}__raw"] = pdata.X_raw[:, j]
        df[f"{name}__scaled"] = pdata.X[:, j]
    return df.sort_values(["region", "date"]).reset_index(drop=True)


def _model_input_summary(pdata) -> pd.DataFrame:
    """Per region x feature stats of the SCALED column - the fastest way to see
    why a contribution came out the size it did.

    For a `center_scale` feature `scaled_mean_train` is 0 by construction, so
    its contribution over the training window is ~0 whatever its coefficient:
    the level sits in the intercept, and only the deviations are attributed.
    For a `scale_only` feature the mean is ~1 in active weeks, so its
    contribution is a genuine "versus no activity" increment.
    """
    specs = _spec_map(pdata)
    tbl = {(r.region, r.feature): r
           for r in pdata.x_scale_table.itertuples(index=False)}
    have_raw = pdata.X_raw is not None
    rows = []
    for g, rname in enumerate(pdata.region_names):
        m_all = pdata.region_idx == g
        m_tr = m_all & pdata.train_mask
        for j, fname in enumerate(pdata.feature_names):
            info = tbl[(rname, fname)]
            spec = specs.get(fname)
            sc = pdata.X[:, j]
            raw = pdata.X_raw[:, j] if have_raw else np.full(len(sc), np.nan)
            centred = info.method == "center_scale"
            rows.append({
                "region": rname, "feature": fname,
                "pillar": _pillar_of(spec),
                "sign": "" if spec is None else spec.sign,
                "pooling": "" if spec is None else spec.pooling,
                "baseline": "" if spec is None else bool(spec.baseline),
                "contribution_reference": ("" if spec is None
                                           else spec.contribution_reference),
                "scaling_method": info.method,
                "center_used": float(info.center),
                "scale_used": float(info.scale),
                "n_train": int(m_tr.sum()), "n_obs": int(m_all.sum()),
                "n_active_train": int(info.n_active_train),
                "raw_mean_train": float(np.mean(raw[m_tr])),
                "raw_sd_train": float(np.std(raw[m_tr])),
                "raw_min": float(np.min(raw[m_all])),
                "raw_max": float(np.max(raw[m_all])),
                "raw_sum_all": float(np.sum(raw[m_all])),
                "scaled_mean_train": float(np.mean(sc[m_tr])),
                "scaled_sd_train": float(np.std(sc[m_tr])),
                "scaled_mean_all": float(np.mean(sc[m_all])),
                "scaled_min": float(np.min(sc[m_all])),
                "scaled_max": float(np.max(sc[m_all])),
                "scaled_sum_all": float(np.sum(sc[m_all])),
                "pct_weeks_negative_scaled": float((sc[m_all] < 0).mean() * 100),
                "mean_zero_by_construction": bool(centred),
                "scaled_value_reads_as": (
                    "deviations from the train mean in sd units "
                    "(0 = the feature's typical level)" if centred else
                    "multiples of the mean active level (0 = no activity)"),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 04_fit - row-level actual vs predicted
# --------------------------------------------------------------------------
def write_actual_vs_predicted(decomp, pdata, outdir: str,
                              out_cfg: OutputConfig | None = None) -> pd.DataFrame:
    """One row per region x period: actual, fitted, both intervals, residual.

    `incremental` is fitted - baseline, i.e. everything the model attributes to
    switchable drivers in that week. `actual - fitted` is the residual the
    decomposition can never explain; it is what the Residual line of
    contribution_summary.csv carries.
    """
    out_cfg = out_cfg or OutputConfig()
    os.makedirs(outdir, exist_ok=True)
    med = np.median(decomp.yhat_draws, axis=0)
    m_lo = np.percentile(decomp.yhat_draws, 5, axis=0)
    m_hi = np.percentile(decomp.yhat_draws, 95, axis=0)
    p_lo = np.percentile(decomp.ypred_draws, 5, axis=0)
    p_hi = np.percentile(decomp.ypred_draws, 95, axis=0)
    base = np.median(decomp.baseline_draws, axis=0)
    core = (np.median(decomp.core_draws, axis=0)
            if decomp.core_draws is not None else base)
    actual = pdata.y_orig
    resid = actual - med
    with np.errstate(divide="ignore", invalid="ignore"):
        ape = np.where(actual != 0, np.abs(resid / actual) * 100, np.nan)

    df = pd.DataFrame({
        "region": _region_col(pdata),
        "date": pd.to_datetime(pdata.dates.values),
        "dataset": np.where(pdata.train_mask, "train", "test"),
        "period": period_labels(pdata.dates.values, out_cfg.period_split),
        "actual": actual, "fitted": med, "residual": resid,
        "abs_pct_error": ape,
        "fitted_lo90_mean": m_lo, "fitted_hi90_mean": m_hi,
        "pred_lo90": p_lo, "pred_hi90": p_hi,
        "inside_pred_90": (actual >= p_lo) & (actual <= p_hi),
        "baseline": base, "baseline_core": core,
        "incremental": med - base,
        "baseline_pct_of_actual": np.where(actual != 0, base / actual * 100, np.nan),
    }).sort_values(["region", "date"]).reset_index(drop=True)
    df.to_csv(os.path.join(outdir, "actual_vs_predicted.csv"), index=False)
    return df


# --------------------------------------------------------------------------
# 05_contributions - volume, reconciliation, and the arithmetic behind them
# --------------------------------------------------------------------------
def _component_frame(decomp, pdata, out_cfg: OutputConfig) -> pd.DataFrame:
    """Long frame, one row per region x period x component.

    `volume` is the posterior-median contribution of that component in that
    week, in KPI units. Components are the baseline core plus every feature, so
    they sum (week by week) to the fitted value up to the median gap.
    """
    specs = _spec_map(pdata)
    base_feats = set(decomp.baseline_features or ())
    core = (decomp.core_draws if decomp.core_draws is not None
            else decomp.baseline_draws)
    region = _region_col(pdata)
    dates = pd.to_datetime(pdata.dates.values)
    dataset = np.where(pdata.train_mask, "train", "test")
    period = period_labels(pdata.dates.values, out_cfg.period_split)

    parts = [(BASELINE_CORE, "baseline_core", "Baseline", np.median(core, axis=0))]
    for name, vals in decomp.contrib_median.items():
        parts.append((name,
                      "baseline_part" if name in base_feats else "incremental",
                      _pillar_of(specs.get(name)), vals))

    frames = []
    for name, group, pillar, vals in parts:
        frames.append(pd.DataFrame({
            "region": region, "date": dates, "dataset": dataset,
            "period": period, "pillar": pillar, "feature": name,
            "group": group, "volume": np.asarray(vals, dtype=float)}))
    return pd.concat(frames, ignore_index=True)


def _n_periods(dates) -> int:
    return int(pd.Series(pd.to_datetime(np.asarray(dates))).nunique())


def write_contribution_timeseries(comp: pd.DataFrame, decomp, pdata,
                                  outdir: str, out_cfg: OutputConfig) -> pd.DataFrame:
    """The weekly decomposition as data, not a picture.

    Pivot on `feature` and every reconciliation is one SUM away:
        sum(volume where group in baseline_core/baseline_part/incremental)
          + __median_gap__ = __fitted__
        __fitted__ + __residual__ = __actual__

    `__median_gap__` exists because each component row is a posterior MEDIAN and
    the median of a sum is not the sum of medians. It is normally a rounding
    error next to the residual; carrying it explicitly means the weekly stack
    ties out exactly instead of nearly.
    """
    med = np.median(decomp.yhat_draws, axis=0)
    comp_sum = comp.groupby(["region", "date"])["volume"].sum()
    keys = pd.MultiIndex.from_arrays(
        [_region_col(pdata), pd.to_datetime(pdata.dates.values)])
    gap = med - comp_sum.reindex(keys).to_numpy()
    ref = pd.DataFrame({
        "region": _region_col(pdata),
        "date": pd.to_datetime(pdata.dates.values),
        "dataset": np.where(pdata.train_mask, "train", "test"),
        "period": period_labels(pdata.dates.values, out_cfg.period_split),
    })
    extra = []
    for name, group, vals in (
            ("__median_gap__", "median_gap", gap),
            ("__fitted__", "reference", med),
            ("__actual__", "reference", pdata.y_orig),
            ("__residual__", "reference", pdata.y_orig - med)):
        e = ref.copy()
        e["pillar"] = ""
        e["feature"] = name
        e["group"] = group
        e["volume"] = np.asarray(vals, dtype=float)
        extra.append(e)
    df = (pd.concat([comp] + extra, ignore_index=True)
          .sort_values(["region", "date", "group", "feature"])
          .reset_index(drop=True))
    df.to_csv(os.path.join(outdir, "contribution_timeseries.csv"), index=False)
    return df


def write_contribution_summary(comp: pd.DataFrame, decomp, pdata, outdir: str,
                               out_cfg: OutputConfig) -> pd.DataFrame:
    """Volume AND percentage contribution, laid out like the vendor deck.

    Rows per (period, region): the actual sales line, every driver grouped into
    its pillar with a pillar subtotal, a Residual line inside Baseline, and a
    Grand Total that equals actual sales exactly - so the percentages sum to
    100.00% by construction rather than by luck.

    Percentages are of ACTUAL sales, matching
    `snapshots/true_output/contribution_summary.png`. The Residual line is what
    makes them close: the drivers sum to FITTED sales, and actual - fitted has
    to appear somewhere or the column stops at 99.x%.
    """
    med = np.median(decomp.yhat_draws, axis=0)
    obs = pd.DataFrame({
        "region": _region_col(pdata),
        "date": pd.to_datetime(pdata.dates.values),
        "period": period_labels(pdata.dates.values, out_cfg.period_split),
        "actual": pdata.y_orig, "fitted": med})

    periods = list(pd.unique(obs["period"]))
    if len(periods) > 1 or periods != ["Total"]:
        periods = periods + ["Total"]
    regions = ["__portfolio__"] + list(pdata.region_names)

    rows = []
    for period in periods:
        p_obs = obs if period == "Total" else obs[obs["period"] == period]
        p_comp = comp if period == "Total" else comp[comp["period"] == period]
        for region in regions:
            o = p_obs if region == "__portfolio__" else p_obs[p_obs["region"] == region]
            c = p_comp if region == "__portfolio__" else p_comp[p_comp["region"] == region]
            if not len(o):
                continue
            actual = float(o["actual"].sum())
            npd = _n_periods(o["date"])
            agg = (c.groupby(["pillar", "feature", "group"], as_index=False)["volume"]
                   .sum())
            residual = actual - float(agg["volume"].sum())
            agg = pd.concat([agg, pd.DataFrame([{
                "pillar": "Baseline", "feature": "__residual__",
                "group": "residual", "volume": residual}])], ignore_index=True)

            def add(pillar, feature, group, row_type, volume):
                rows.append({
                    "period": period, "n_periods": npd, "region": region,
                    "pillar": pillar, "feature": feature, "group": group,
                    "row_type": row_type, "volume": float(volume),
                    "contribution_pct": (float(volume) / actual * 100
                                         if actual else np.nan),
                    "avg_volume_per_period": float(volume) / npd if npd else np.nan,
                })

            add("", ACTUAL_ROW, "", "actual", actual)
            pillars = sorted(agg["pillar"].unique(),
                             key=lambda p: (p != "Baseline", p))
            for pil in pillars:
                block = agg[agg["pillar"] == pil]
                block = block.reindex(
                    block["volume"].abs().sort_values(ascending=False).index)
                for _, r in block.iterrows():
                    add(pil, r["feature"], r["group"], "component", r["volume"])
                add(pil, f"{pil} Total", "", "pillar_total", block["volume"].sum())
            add("", "Grand Total", "", "grand_total", agg["volume"].sum())

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "contribution_summary.csv"), index=False)
    return df


def write_contribution_reconciliation(comp: pd.DataFrame, decomp, pdata,
                                      outdir: str) -> pd.DataFrame:
    """Does the decomposition add up to actual sales? One row per scope x region.

    The chain, and where each gap comes from:
        sum_components  every driver + the baseline core (sum of weekly medians)
        + median_gap    medians of parts do not add exactly to the median of the
                        whole; this is that difference, and it should be tiny
        = fitted        the model's median prediction
        + residual      what the model could not explain (actual - fitted)
        = actual        which is why reconciles_to_actual_pct is exactly 100
    """
    med = np.median(decomp.yhat_draws, axis=0)
    base = np.median(decomp.baseline_draws, axis=0)
    core = (np.median(decomp.core_draws, axis=0)
            if decomp.core_draws is not None else base)
    region = _region_col(pdata)
    base_feats = set(decomp.baseline_features or ())

    scopes = [("all", np.ones(len(med), dtype=bool)), ("train", pdata.train_mask)]
    if pdata.test_mask.any():
        scopes.append(("test", pdata.test_mask))

    rows = []
    for scope, smask in scopes:
        for rname in ["__portfolio__"] + list(pdata.region_names):
            m = smask if rname == "__portfolio__" else smask & (region == rname)
            if not m.any():
                continue
            actual = float(pdata.y_orig[m].sum())
            fitted = float(med[m].sum())
            base_v = float(base[m].sum())
            core_v = float(core[m].sum())
            base_feat_v = float(sum(decomp.contrib_median[f][m].sum()
                                    for f in base_feats))
            incr_v = float(sum(v[m].sum() for f, v in decomp.contrib_median.items()
                               if f not in base_feats))
            comp_v = core_v + base_feat_v + incr_v
            resid = actual - fitted
            gap = fitted - comp_v
            pct = (lambda v: v / actual * 100 if actual else np.nan)
            rows.append({
                "scope": scope, "region": rname,
                "n_obs": int(m.sum()), "n_periods": _n_periods(pdata.dates.values[m]),
                "actual_volume": actual, "fitted_volume": fitted,
                "baseline_core_volume": core_v,
                "baseline_features_volume": base_feat_v,
                "baseline_total_volume": base_v,
                "incremental_volume": incr_v,
                "sum_components_volume": comp_v,
                "median_gap_volume": gap,
                "residual_volume": resid,
                "baseline_core_pct": pct(core_v),
                "baseline_features_pct": pct(base_feat_v),
                "baseline_total_pct": pct(base_v),
                "incremental_pct": pct(incr_v),
                "median_gap_pct": pct(gap),
                "residual_pct": pct(resid),
                "fitted_pct_of_actual": pct(fitted),
                "reconciles_to_actual_pct": pct(comp_v + gap + resid),
            })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "contribution_reconciliation.csv"), index=False)
    return df


def write_contribution_math(decomp, pdata, outdir: str,
                            coef: pd.DataFrame | None = None) -> pd.DataFrame:
    """The contribution arithmetic, per region x feature, in one row.

    Every contribution in this codebase is exactly

        volume = beta_scaled * SUM(x_scaled + reference_shift) * dv_scale_used

    and this file prints each factor so it can be recomputed in a spreadsheet.
    `volume_recomputed` uses the MEDIAN coefficient, so it differs slightly from
    the draw-by-draw figures - the two `*_diff_pct` columns show by how much.

    This is the file that answers "how can a centred feature contribute
    anything?": for a centred feature with contribution_reference=auto,
    `scaled_sum` is ~0 over the training window, so `volume` is ~0 no matter how
    large beta is. Set contribution_reference=zero and `reference_shift`
    becomes center/scale per week, which is where a level driver's real
    "versus nothing" contribution appears.
    """
    specs = _spec_map(pdata)
    tbl = {(r.region, r.feature): r
           for r in pdata.x_scale_table.itertuples(index=False)}
    shifts = decomp.contrib_shift or {}
    beta_med = {}
    if coef is not None and {"feature", "region", "median"} <= set(coef.columns):
        sub = coef[coef["region"] != "__population__"]
        for _, r in sub.iterrows():
            beta_med[(r["feature"], r["region"])] = float(r["median"])

    rows = []
    for g, rname in enumerate(pdata.region_names):
        m = pdata.region_idx == g
        m_tr = m & pdata.train_mask
        sd_y = float(pdata.y_scale[g])
        for fname, tot in decomp.contrib_totals.items():
            j = pdata.feature_index[fname]
            info = tbl[(rname, fname)]
            spec = specs.get(fname)
            shift = shifts.get(fname)
            shift_g = float(shift[m][0]) if shift is not None and m.any() else 0.0
            scaled_sum = float(pdata.X[m, j].sum())
            eff_sum = scaled_sum + shift_g * int(m.sum())
            beta = beta_med.get((fname, rname), np.nan)
            recomputed = beta * eff_sum * sd_y
            reported = float(np.median(tot[:, g]))
            from_medians = float(decomp.contrib_median[fname][m].sum())
            centre = float(info.center)
            scale = float(info.scale)
            rows.append({
                "region": rname, "feature": fname,
                "pillar": _pillar_of(spec),
                "group": ("baseline_part" if fname in set(decomp.baseline_features or ())
                          else "incremental"),
                "scaling_method": info.method,
                "center_used": centre, "scale_used": scale,
                "contribution_reference": ("" if spec is None
                                           else spec.contribution_reference),
                "reference_raw_value": centre - shift_g * scale,
                "reference_shift_scaled": shift_g,
                "n_obs": int(m.sum()),
                "raw_sum": (float(pdata.X_raw[m, j].sum())
                            if pdata.X_raw is not None else np.nan),
                "raw_mean_train": (float(pdata.X_raw[m_tr, j].mean())
                                   if pdata.X_raw is not None else np.nan),
                "scaled_sum": scaled_sum,
                "scaled_mean_train": float(pdata.X[m_tr, j].mean()),
                "effective_scaled_sum": eff_sum,
                "beta_scaled_median": beta,
                "dv_scale_used": sd_y,
                "volume_recomputed": recomputed,
                "volume_sum_of_medians": from_medians,
                "volume_median_of_total": reported,
                "recomputed_diff_pct": (abs(recomputed - reported) / abs(reported) * 100
                                        if reported else np.nan),
                "median_basis_diff_pct": (abs(from_medians - reported) / abs(reported) * 100
                                          if reported else np.nan),
            })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "contribution_math.csv"), index=False)
    return df


def write_contribution_diagnostics(decomp, pdata, outdir: str,
                                   out_cfg: OutputConfig | None = None,
                                   coef: pd.DataFrame | None = None) -> dict:
    """Every optional 05_contributions file the OutputConfig asks for."""
    out_cfg = out_cfg or OutputConfig()
    os.makedirs(outdir, exist_ok=True)
    written = {}
    need_comp = (out_cfg.contribution_summary or out_cfg.contribution_timeseries
                 or out_cfg.contribution_reconciliation)
    comp = _component_frame(decomp, pdata, out_cfg) if need_comp else None
    if out_cfg.contribution_summary:
        written["contribution_summary"] = write_contribution_summary(
            comp, decomp, pdata, outdir, out_cfg)
    if out_cfg.contribution_timeseries:
        written["contribution_timeseries"] = write_contribution_timeseries(
            comp, decomp, pdata, outdir, out_cfg)
    if out_cfg.contribution_reconciliation:
        written["contribution_reconciliation"] = write_contribution_reconciliation(
            comp, decomp, pdata, outdir)
    if out_cfg.contribution_math:
        written["contribution_math"] = write_contribution_math(
            decomp, pdata, outdir, coef)
    return written
