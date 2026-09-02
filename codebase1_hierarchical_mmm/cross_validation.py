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
    from config import CVConfig, ModelConfig, OutputConfig, RunConfig, SamplerConfig
    from cross_validation import run_cv

    # WEEKLY (104 weeks): the preset gives horizon 13, 5 folds, min_train 52
    cv = run_cv(df, model_cfg,
                run_cfg=RunConfig(run_name="fy26", cadence="weekly"),
                sampler_cfg=SamplerConfig(sampler="numpyro"),
                cv_cfg=CVConfig(),               # everything from the preset
                out_cfg=OutputConfig())          # figure size/dpi only

    # MONTHLY (24 months): horizon 3, 3 folds, min_train 12, draws/tune 500
    cv = run_cv(df, model_cfg,
                run_cfg=RunConfig(run_name="bmc", cadence="monthly"),
                cv_cfg=CVConfig())

CADENCE. Leave `cadence="auto"` and the spacing of the dates decides. Every
CVConfig count left at None then comes from that preset, so you never hand a
24-month panel a 13-period horizon. An explicit value always wins:
CVConfig(horizon=6) keeps 6 and takes the rest from the preset.

`run_cv` does NOT read the model or prior config from anywhere else: pass the
same `model_cfg` and `run_cfg` you passed to `run()`, or the folds will be
scaled and pooled differently from the headline fit and the comparison is void.
`holdout_periods` on the run_cfg is overridden per fold (to cv_cfg.horizon),
so the value you set for the main run is ignored here rather than compounding.

Outputs (<output_dir>/<run_name>/06_cross_validation/):
    cv_fold_metrics.csv          per fold x region x train/test: all fit metrics
                                 + fold convergence (max R-hat, divergences)
    cv_summary.csv               mean +/- sd across folds (test window)
    cv_coefficient_stability.csv fold-wise posterior medians per feature/region
    cv_stability_by_region.csv   mean/sd/min/max + rel_sd_pct per feature x region
    cv_stability_ranking.csv     features ranked by cross-fold instability
    cv_report.md                 headline readout
    cv_accuracy_by_fold.png      test wMAPE per fold, per region
    stability/<feature>.png      coefficient medians across folds, per region
    fold_k/sampling_log.json     per-fold run manifests
"""
from __future__ import annotations

import os
from dataclasses import replace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

from config import (CVConfig, ModelConfig, OutputConfig, RunConfig,
                    SamplerConfig, resolve_period_plan)
from data_prep import make_folds, prepare_data
from diagnostics import quick_convergence_checks
from fit import fit
from model import build_model
from outputs import (beta_draws_by_feature, compute_decomposition,
                     compute_fit_metrics, stack_posterior)
from plotting import (annotate, figsize, save_fig, set_figure_defaults,
                      units_note)


def run_cv(df: pd.DataFrame,
           model_cfg: ModelConfig,
           run_cfg: RunConfig | None = None,
           sampler_cfg: SamplerConfig | None = None,
           cv_cfg: CVConfig | None = None,
           out_cfg: OutputConfig | None = None) -> dict:
    run_cfg = run_cfg or RunConfig()
    sampler_cfg = sampler_cfg or SamplerConfig()
    cv_cfg = cv_cfg or CVConfig()
    # out_cfg is used only for figure size/dpi here: CV writes its own tables,
    # not the staged reconciliation files
    out_cfg = out_cfg or OutputConfig()
    set_figure_defaults(dpi=out_cfg.fig_dpi, scale=out_cfg.fig_scale)
    outdir = os.path.join(run_cfg.output_dir, run_cfg.run_name,
                          "06_cross_validation")
    os.makedirs(outdir, exist_ok=True)

    dc = run_cfg.date_col
    d = df.copy()
    d[dc] = pd.to_datetime(d[dc])
    dates = np.sort(d[dc].unique())

    # Resolve the cadence ONCE, from the CV config if it pins one, else the run
    # config, else the data. Every unset count then comes from that preset:
    #   weekly   horizon 13, 5 folds, min_train 52  (the historic defaults)
    #   monthly  horizon  3, 3 folds, min_train 12, draws/tune 500
    cad = cv_cfg.cadence if cv_cfg.cadence != "auto" else run_cfg.cadence
    plan = resolve_period_plan(cad, dates)
    cv_cfg = cv_cfg.resolved(plan)
    print(f"[cv] cadence={plan.cadence} ({plan.periods_per_year} periods/year): "
          f"horizon={cv_cfg.horizon} {plan.unit}, n_folds={cv_cfg.n_folds}, "
          f"min_train={cv_cfg.min_train_periods} {plan.unit}")
    if len(dates) < cv_cfg.min_train_periods + cv_cfg.horizon:
        raise ValueError(
            f"{len(dates)} {plan.unit} is too short for CV at this cadence: "
            f"min_train_periods={cv_cfg.min_train_periods} + "
            f"horizon={cv_cfg.horizon} needs at least "
            f"{cv_cfg.min_train_periods + cv_cfg.horizon}. Lower them "
            "explicitly on CVConfig, or check the cadence was detected right.")
    folds = make_folds(len(dates), cv_cfg.horizon, cv_cfg.n_folds,
                       cv_cfg.step, cv_cfg.min_train_periods)
    if len(folds) < cv_cfg.n_folds:
        print(f"[cv] NOTE: {len(folds)} folds fit in {len(dates)} "
              f"{plan.unit}, not the {cv_cfg.n_folds} requested "
              "(earlier origins would fall below min_train_periods)")

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
        fold_run = replace(run_cfg, holdout_periods=cv_cfg.horizon,
                           cadence=plan.cadence)
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

    # ---- summary across folds (test window) --------------------------------
    test = fold_metrics[fold_metrics["dataset"] == "test"]
    # r2_within_region is included because plain r2 on the __all__ row pools
    # regions whose levels differ several-fold and reads far too high (v3: 0.994
    # vs 0.596). Quote the within-region figure.
    agg_cols = [c for c in ["wmape_pct", "mape_pct", "mape_region_weighted_pct",
                            "mae", "crps", "coverage_90_pred_pct",
                            "r2_within_region", "r2"]
                if c in test.columns]
    summary = (test.groupby("region")[agg_cols]
               .agg(["mean", "std"]).round(3))
    summary.columns = [f"{a}_{b}" for a, b in summary.columns]
    summary = summary.reset_index()
    summary.to_csv(os.path.join(outdir, "cv_summary.csv"), index=False)

    # ---- coefficient stability ---------------------------------------------
    coef = pd.DataFrame(coef_rows)
    coef.to_csv(os.path.join(outdir, "cv_coefficient_stability.csv"), index=False)
    stab = (coef.groupby(["feature", "region"])["median"]
            .agg(["mean", "std", "min", "max"]).reset_index())
    stab["rel_sd_pct"] = (stab["std"] / (stab["mean"].abs() + 1e-12) * 100).round(1)
    # the per-feature x region view: the raw fold medians are in
    # cv_coefficient_stability.csv and the feature-level roll-up in
    # cv_stability_ranking.csv, but the middle table - which REGION is unstable
    # for a given feature - is the one that usually answers the question
    stab.to_csv(os.path.join(outdir, "cv_stability_by_region.csv"), index=False)
    rank = (stab.groupby("feature")["rel_sd_pct"].mean()
            .sort_values(ascending=False).reset_index()
            .rename(columns={"rel_sd_pct": "avg_rel_sd_pct"}))
    rank.to_csv(os.path.join(outdir, "cv_stability_ranking.csv"), index=False)

    if cv_cfg.make_plots:
        sdir = os.path.join(outdir, "stability")
        os.makedirs(sdir, exist_ok=True)
        for name, grp in coef.groupby("feature"):
            fig, ax = plt.subplots(figsize=figsize(7, 3.8))
            for r, gr in grp.groupby("region"):
                ax.plot(gr["fold"], gr["median"], marker="o", ms=3, lw=1,
                        label=str(r))
            ax.axhline(0, color="grey", lw=0.8, label="no effect")
            ax.set_xticks(sorted(grp["fold"].unique()))
            annotate(ax,
                     "CV fold (1 = earliest origin, later folds see more data)",
                     "posterior median coefficient\n"
                     "(scaled axis: KPI sd per feature sd)",
                     f"{name} - coefficient stability across CV folds",
                     legend=grp["region"].nunique() <= 10, legend_fontsize=7)
            units_note(fig, "A flat line means the coefficient is stable as the "
                            "training window grows - the contribution story "
                            "holds up. A line that swings or changes rank order "
                            "is fragile, however good the error metric looks.")
            fig.tight_layout()
            save_fig(fig, os.path.join(sdir, f"{name}.png"))

        # accuracy across folds, per region - the other half of the CV story
        try:
            fig, ax = plt.subplots(figsize=figsize(8, 4.2))
            for r, gr in test.groupby("region"):
                ax.plot(gr["fold"], gr["wmape_pct"], marker="o", ms=3, lw=1,
                        label=str(r))
            annotate(ax,
                     "CV fold (1 = earliest origin, later folds see more data)",
                     "test wMAPE (%) - lower is better",
                     "Holdout accuracy across folds",
                     legend=True, legend_fontsize=7)
            units_note(fig, "Each point is one refit predicting the next "
                            f"{cv_cfg.horizon} {plan.unit} it has never seen. Rising "
                            "error in later folds usually means the most recent "
                            "window behaves differently (regime change), not "
                            "that the model got worse.")
            fig.tight_layout()
            save_fig(fig, os.path.join(outdir, "cv_accuracy_by_fold.png"))
        except Exception as e:  # noqa: BLE001 - a plot must never kill a CV run
            print(f"[cv] WARNING: accuracy plot failed: {e}")

    # ---- headline report ----------------------------------------------------
    t_all = test[test["region"] == "__all__"]
    conv_bad = fold_metrics[(fold_metrics["region"] == "__all__")
                            & (fold_metrics["dataset"] == "test")
                            & ((fold_metrics["max_rhat"] > 1.05)
                               | (fold_metrics["divergences"] > 0))]
    lines = [
        "# Expanding-window CV report",
        f"- cadence: {plan.cadence} ({plan.periods_per_year} periods/year)",
        f"- folds: {len(folds)}, horizon: {cv_cfg.horizon} {plan.unit}, "
        f"min train: {cv_cfg.min_train_periods} {plan.unit}, "
        f"sampler: {scfg.sampler}",
        f"- test wMAPE: {t_all['wmape_pct'].mean():.2f}% "
        f"(+/- {t_all['wmape_pct'].std():.2f} across folds)",
        f"- test region-weighted MAPE: "
        f"{t_all['mape_region_weighted_pct'].mean():.2f}%",
        f"- test CRPS: {t_all['crps'].mean():,.1f} (KPI units; lower is better)",
        f"- predictive 90% coverage on test: "
        f"{t_all['coverage_90_pred_pct'].mean():.1f}% (target ~90; well below = "
        "overconfident, well above = intervals too wide)",
        f"- folds with convergence flags: {len(conv_bad)} of {len(folds)}",
        "",
        "## Least stable features across folds (avg relative sd of the "
        "regional coefficient medians)", ""]
    for _, r in rank.head(5).iterrows():
        lines.append(f"- {r['feature']}: {r['avg_rel_sd_pct']:.1f}%")
    lines += ["", "A feature that reorders or swings across folds has a fragile "
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
