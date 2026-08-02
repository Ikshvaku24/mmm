"""End-to-end pipeline for Codebase 1 (hierarchical MMM on pre-transformed data).

Stages and their output folders (under <output_dir>/<run_name>/):
  01_data           panel summary, scaling stats, KPI plots
  02_convergence    sampling log, R-hat/ESS/divergences, contraction, prior check
  03_coefficients   coefficient report CSV + forest plots
  04_fit            fit metrics (R2/MAPE/wMAPE/coverage), actual-vs-fitted, residuals
  05_contributions  contribution totals CSV, bars, weekly decomposition

Typical use (e.g. from a Databricks notebook):

    import pandas as pd
    from config import ModelConfig, RunConfig, SamplerConfig, load_feature_config
    from run_pipeline import run

    df = pd.read_excel(".../input_datacube.xlsx")          # date, region, dv, features
    features = load_feature_config(".../feature_priors.csv")
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
from diagnostics import convergence_report, prior_posterior_report
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
    sampler_cfg = sampler_cfg or SamplerConfig()
    root = os.path.join(run_cfg.output_dir, run_cfg.run_name)
    dirs = {k: os.path.join(root, k) for k in
            ["01_data", "02_convergence", "03_coefficients", "04_fit",
             "05_contributions"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    print("[1/5] preparing data")
    pdata = prepare_data(df, run_cfg, model_cfg)
    write_data_stage_outputs(pdata, dirs["01_data"])

    print("[2/5] building + sampling model")
    model = build_model(pdata, model_cfg)
    prior_idata = sample_prior(model, sampler_cfg)
    idata = fit(model, sampler_cfg, outdir=dirs["02_convergence"])
    idata.extend(prior_idata)

    print("[3/5] convergence diagnostics")
    convergence_report(idata, dirs["02_convergence"])
    prior_posterior_report(idata, dirs["02_convergence"])
    prior_predictive_plot(idata, pdata, dirs["02_convergence"])

    print("[4/5] coefficients + fit quality")
    coef = coefficient_report(idata, pdata, dirs["03_coefficients"])
    decomp = compute_decomposition(idata, pdata, model_cfg,
                                   n_draws=run_cfg.report_draws)
    metrics = fit_report(decomp, pdata, dirs["04_fit"])

    print("[5/5] contributions")
    contrib = contribution_report(decomp, pdata, dirs["05_contributions"])

    if save_trace:
        az.to_netcdf(idata, os.path.join(root, "trace.nc"))

    print(f"done -> {root}")
    return {"idata": idata, "pdata": pdata, "decomposition": decomp,
            "coefficients": coef, "metrics": metrics, "contributions": contrib,
            "output_dir": root}
