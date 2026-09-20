"""End-to-end pipeline for Codebase 1 (hierarchical MMM on pre-transformed data).

Stages and their output folders (under <output_dir>/<run_name>/):
  01_data           panel summary, scaling stats, KPI plots
  02_convergence    sampling log, R-hat/ESS/divergences, contraction, prior check
  03_coefficients   coefficient report CSV + forest plots
  04_fit            fit metrics (R2/MAPE/wMAPE/coverage), actual-vs-fitted, residuals
  05_contributions  contribution totals CSV, bars, weekly decomposition

Which optional files each stage writes is controlled by `OutputConfig`; the
core tables are always written. See config.OutputConfig for the reconciliation
chain those optional files evidence.

Typical use (e.g. from a Databricks notebook):

    import pandas as pd
    from mmm.core.config import (ModelConfig, OutputConfig, RunConfig, SamplerConfig,
                        load_feature_config)
    from mmm.run_pipeline import run

    df = pd.read_excel(".../input_datacube.xlsx")          # date, region, dv, features
    features = load_feature_config(".../feature_priors.csv")
    result = run(df,
                 ModelConfig(features=features, fourier_order=2, include_trend=True),
                 RunConfig(run_name="fy26_refresh", holdout_periods=13),
                 SamplerConfig(sampler="numpyro"),
                 out_cfg=OutputConfig(period_split="mat"))
"""
from __future__ import annotations

import os
import warnings

import pandas as pd

from mmm.core.compat import extend_idata, save_idata
from mmm.core.config import (INTERCEPT_PARAMS, AssumptionConfig, CVConfig,
                    ModelConfig, OutputConfig, RunConfig, SamplerConfig)
from mmm.data.data_prep import prepare_data, write_data_stage_outputs
from mmm.checks.diagnostics import (convergence_report, enforce_convergence,
                         prior_posterior_report, quick_convergence_checks)
from mmm.modelling.fit import fit, sample_prior
from mmm.modelling.model import build_model
from mmm.checks.assumptions import write_assumptions, write_collinearity
from mmm.reporting.outputs import (beta_draws_by_feature, coefficient_report,
                     compute_decomposition, contribution_report, fit_report,
                     prior_predictive_plot, stack_posterior)
from mmm.reporting.plotting import set_figure_defaults
from mmm.checks.warnings_report import (collect_warnings, print_warning_summary,
                             write_warning_docs)


def run(df: pd.DataFrame,
        model_cfg: ModelConfig,
        run_cfg: RunConfig | None = None,
        sampler_cfg: SamplerConfig | None = None,
        save_trace: bool = True,
        out_cfg: OutputConfig | None = None,
        cv_cfg: CVConfig | None = None,
        extra_warnings: list | None = None,
        assumption_cfg: AssumptionConfig | None = None,
        benchmark_mapping: str | None = None,
        benchmark_contribution: str | None = None):
    run_cfg = run_cfg or RunConfig()
    sampler_cfg = sampler_cfg or SamplerConfig()
    out_cfg = out_cfg or OutputConfig()
    assumption_cfg = assumption_cfg or AssumptionConfig()
    root = os.path.join(run_cfg.output_dir, run_cfg.run_name)
    dirs = {k: os.path.join(root, k) for k in
            ["00_warnings", "01_data", "02_convergence", "03_coefficients",
             "04_fit", "05_contributions"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    # one place sets figure size/resolution for every chart in the run
    set_figure_defaults(dpi=out_cfg.fig_dpi, scale=out_cfg.fig_scale)

    # Every warning from here on is captured, grouped by category and written
    # to 00_warnings/ instead of printed one-per-feature. With 65 features a
    # single mistake in one column of the prior file otherwise produces 65
    # near-identical paragraphs and buries the one that mattered.
    with collect_warnings() as caught:
        # The per-feature prior checks live in FeatureSpec.resolved(), which the
        # caller already ran when it built ModelConfig - usually before this
        # function was reached, so those warnings would be missed. Re-resolving
        # is pure arithmetic on the specs (no data, no sampling) and makes the
        # report complete no matter where the config was assembled.
        [s.resolved() for s in model_cfg.features]

        # dropping the intercept only makes sense on a centred KPI: with
        # dv_center="none" the KPI keeps its level and nothing is left to carry
        # it, so every coefficient would be dragged up to fake an intercept.
        if not model_cfg.include_intercept and run_cfg.dv_center == "none":
            warnings.warn(
                "include_intercept=False with dv_center='none': the KPI still "
                "carries its level but the model has no term to absorb it. Use "
                "dv_center='mean', or keep the intercept.")

        result = _run_stages(df, model_cfg, run_cfg, sampler_cfg, out_cfg,
                             dirs, root, save_trace, assumption_cfg,
                             benchmark_mapping, benchmark_contribution)

    # Warnings raised while the CONFIG was being read happen before this
    # function is reached (load_feature_config runs in the caller), so the
    # driver captures them and hands them in - otherwise they escape to the
    # notebook, which is the one place they are not supposed to appear.
    wdf = write_warning_docs(list(extra_warnings or []) + list(caught),
                             dirs["00_warnings"], run_cfg.run_name)
    print_warning_summary(wdf, dirs["00_warnings"])
    result["warnings"] = wdf

    # cross-validation is opt-in: it is a full refit per fold, so it can cost
    # more than the headline run. cv.enabled=false leaves the folder absent.
    if cv_cfg is not None and cv_cfg.enabled:
        from mmm.checks.cross_validation import run_cv
        print("[6/6] cross-validation")
        result["cv"] = run_cv(df, model_cfg, run_cfg, sampler_cfg, cv_cfg, out_cfg)

    print(f"done -> {root}")
    return result


def _run_stages(df, model_cfg, run_cfg, sampler_cfg, out_cfg, dirs, root,
                save_trace, assumption_cfg=None, benchmark_mapping=None,
                benchmark_contribution=None):
    """The five reporting stages. Split out so `run` can wrap them all in one
    warning-capture block without indenting the whole body twice."""
    print("[1/5] preparing data")
    pdata = prepare_data(df, run_cfg, model_cfg)
    write_data_stage_outputs(pdata, dirs["01_data"], out_cfg)
    # Pre-fit collinearity: a property of the DATA, so it is computed before any
    # sampling and its verdict is valid whatever the priors turn out to do.
    collin = (write_collinearity(pdata, model_cfg, dirs["01_data"],
                                 assumption_cfg)
              if out_cfg.collinearity else None)

    print("[2/5] building + sampling model")
    model = build_model(pdata, model_cfg)
    prior_idata = sample_prior(model, sampler_cfg)
    idata = fit(model, sampler_cfg, outdir=dirs["02_convergence"])
    extend_idata(idata, prior_idata)

    print("[3/5] convergence diagnostics")
    convergence_report(idata, dirs["02_convergence"])
    prior_posterior_report(idata, dirs["02_convergence"], out_cfg,
                           skip_prefixes=() if out_cfg.report_intercept
                           else INTERCEPT_PARAMS, pdata=pdata)
    prior_predictive_plot(idata, pdata, dirs["02_convergence"])
    enforce_convergence(quick_convergence_checks(idata),
                        run_cfg.on_convergence_failure)

    print("[4/5] coefficients + fit quality")
    coef = coefficient_report(idata, pdata, dirs["03_coefficients"],
                              out_cfg=out_cfg)
    decomp = compute_decomposition(idata, pdata, model_cfg,
                                   n_draws=run_cfg.report_draws)
    metrics = fit_report(decomp, pdata, dirs["04_fit"], out_cfg)
    if out_cfg.assumption_checks:
        # Post-fit: what the FITTED model could separate, and whether the
        # residuals behave the way the likelihood assumes.
        write_assumptions(decomp, pdata, dirs["04_fit"], model_cfg,
                          beta_draws_by_feature(stack_posterior(idata), pdata),
                          collin, assumption_cfg)

    print("[5/5] contributions")
    # coef is passed in so contribution_math.csv can print the median
    # coefficient beside the volume it produces, without re-stacking the trace
    contrib = contribution_report(decomp, pdata, dirs["05_contributions"],
                                  benchmark_mapping=benchmark_mapping,
                                  benchmark_contribution=benchmark_contribution,
                                  out_cfg=out_cfg, coef=coef)

    if save_trace:
        save_idata(idata, os.path.join(root, "trace.nc"))

    return {"idata": idata, "pdata": pdata, "decomposition": decomp,
            "coefficients": coef, "metrics": metrics, "contributions": contrib,
            "output_dir": root}
