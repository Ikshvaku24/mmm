"""End-to-end pipeline for Codebase 2 (full MMM: raw media, learned transforms).

Stages and output folders (under <output_dir>/<run_name>/):
  01_data           panel summary, scaling stats (incl. media medians), KPI plots
  02_eda            Meridian-style EDA report (run BEFORE modelling; read it!)
  03_convergence    sampling log, R-hat/ESS/divergences, contraction, prior check
  04_transforms     LEARNED adstock/Hill/lag parameters + ranges used (goal 4)
  05_coefficients   coefficient report + forest plots
  06_fit            R2/MAPE/wMAPE/coverage (train & holdout), fit + residual plots
  07_contributions  contribution totals, decomposition, ROI (if spend given)

Typical use:

    import pandas as pd
    from config import (ChannelSpec, FeatureSpec, ModelConfig, RunConfig,
                        SamplerConfig)
    from run_pipeline import run

    df = pd.read_csv("weekly_panel.csv")   # date, region, dv, RAW media, spend, controls
    cfg = ModelConfig(
        channels=[
            ChannelSpec("tv", col="tv_grps", spend_col="tv_spend",
                        adstock="geometric", max_lag=13),
            ChannelSpec("search", col="search_clicks", spend_col="search_spend",
                        adstock="geometric", max_lag=4),
            ChannelSpec("ooh", col="ooh_panels", adstock="delayed", max_lag=10),
        ],
        controls=[FeatureSpec("price_index", sign="negative", prior_mean=0.3),
                  FeatureSpec("distribution", sign="free")],
    )
    result = run(df, cfg, RunConfig(run_name="pilot", holdout_periods=13),
                 SamplerConfig(sampler="numpyro"))

THE ONE RULE: media columns must be RAW activity (GRPs/clicks/impressions).
Never feed pre-adstocked or pre-saturated media - the model applies
Adstock -> Hill internally and LEARNS the parameters (that is the point).
"""
from __future__ import annotations

import os

import pandas as pd

from compat import extend_idata, save_idata
from config import ModelConfig, RunConfig, SamplerConfig
from data_prep import prepare_data, write_data_stage_outputs
from diagnostics import (convergence_report, enforce_convergence,
                         prior_posterior_report, quick_convergence_checks)
from eda import run_eda
from fit import fit, sample_prior
from model import build_model
from outputs import (coefficient_report, compute_decomposition,
                     contribution_report, fit_report, prior_predictive_plot)
from transform_report import transform_report


def run(df: pd.DataFrame,
        model_cfg: ModelConfig,
        run_cfg: RunConfig | None = None,
        sampler_cfg: SamplerConfig | None = None,
        save_trace: bool = True,
        skip_eda: bool = False):
    run_cfg = run_cfg or RunConfig()
    sampler_cfg = sampler_cfg or SamplerConfig()
    root = os.path.join(run_cfg.output_dir, run_cfg.run_name)
    dirs = {k: os.path.join(root, k) for k in
            ["01_data", "02_eda", "03_convergence", "04_transforms",
             "05_coefficients", "06_fit", "07_contributions"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    print("[1/7] EDA")
    eda_out = None
    if not skip_eda:
        eda_out = run_eda(df, run_cfg, model_cfg, dirs["02_eda"])

    print("[2/7] preparing panel")
    pdata = prepare_data(df, run_cfg, model_cfg)
    write_data_stage_outputs(pdata, dirs["01_data"])

    print("[3/7] building + sampling model (adstock/Hill learned in-model)")
    model = build_model(pdata, model_cfg)
    prior_idata = sample_prior(model, sampler_cfg)
    idata = fit(model, sampler_cfg, outdir=dirs["03_convergence"])
    extend_idata(idata, prior_idata)

    print("[4/7] convergence diagnostics")
    convergence_report(idata, dirs["03_convergence"])
    prior_posterior_report(idata, dirs["03_convergence"])
    prior_predictive_plot(idata, pdata, dirs["03_convergence"])
    enforce_convergence(quick_convergence_checks(idata),
                        run_cfg.on_convergence_failure)

    print("[5/7] learned transform report")
    transforms = transform_report(idata, pdata, model_cfg, dirs["04_transforms"])

    print("[6/7] coefficients + fit quality")
    coef = coefficient_report(idata, pdata, dirs["05_coefficients"])
    decomp = compute_decomposition(idata, pdata, model_cfg,
                                   n_draws=run_cfg.report_draws)
    metrics = fit_report(decomp, pdata, dirs["06_fit"])

    print("[7/7] contributions + ROI")
    contrib = contribution_report(decomp, pdata, model_cfg, run_cfg,
                                  dirs["07_contributions"])

    if save_trace:
        save_idata(idata, os.path.join(root, "trace.nc"))

    print(f"done -> {root}")
    return {"idata": idata, "pdata": pdata, "decomposition": decomp,
            "coefficients": coef, "metrics": metrics, "contributions": contrib,
            "transforms": transforms, "eda": eda_out, "output_dir": root}
