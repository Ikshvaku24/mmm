"""YAML front end for every config object in the pipeline.

Why this exists
---------------
`ModelConfig` / `RunConfig` / `SamplerConfig` / `OutputConfig` / `CVConfig` are
the real settings objects and stay the source of truth. Editing them means
editing a `.py` driver, which is fine in a notebook and awful everywhere else:
a run's settings end up scattered across a script that also loads data and
prints things, and two people cannot compare two runs without diffing code.

This module gives the same knobs a single declarative file:

    settings = load_settings("config.yaml")
    result = run_from_yaml("config.yaml")          # load data + fit + report

and, so nobody has to read source to find out what a knob does or what it is
set to by default, it can WRITE a fully annotated file with every field at its
default value:

    python -c "import settings; settings.write_default_yaml('config.yaml')"
    # or:  python settings.py --write config.yaml

Rules the loader enforces
-------------------------
* **Unknown keys are an error, never ignored.** A silently dropped `draw: 2000`
  (missing the `s`) is a run that quietly used the default and a conclusion
  drawn from the wrong numbers. The error names the offending key, the section,
  and the closest valid spelling.
* **Every value still goes through the dataclass `__post_init__`**, so the YAML
  path cannot reach a state the Python path would have rejected.
* **Omitted keys take the dataclass default.** A YAML file may be as short as
  the two lines that differ from the defaults.

The features themselves are NOT in the YAML - they stay in the feature-prior
CSV, which is a table and belongs in a table. The YAML points at it via
`data.feature_priors`.
"""
from __future__ import annotations

import dataclasses
import difflib
import os
from dataclasses import fields

import yaml

from mmm.core.config import (AssumptionConfig, CVConfig, ModelConfig, OutputConfig,
                    RunConfig, SamplerConfig, load_feature_config)

# --------------------------------------------------------------------------- #
# section table: yaml key -> dataclass, and the fields that are NOT settings
# --------------------------------------------------------------------------- #
SECTIONS = {
    "model": ModelConfig,
    "run": RunConfig,
    "sampler": SamplerConfig,
    "output": OutputConfig,
    "assumptions": AssumptionConfig,
    "cv": CVConfig,
}

# `features` is built from the prior CSV, not written in the YAML
EXCLUDED = {"model": ("features",)}

DATA_KEYS = ("input_path", "sheet", "feature_priors", "date_format",
             "mapping_file", "share_file", "dv_aggregation", "pre_model_dir")

# keys that used to exist, and what replaced them - so an old config gets told
# what to do instead of a bare "unknown key"
RENAMED_KEYS = {
    "benchmark_mapping": "mapping_file (vendor_variable,our_variable[,region]"
                         "[,contribution])",
    "vendor_contribution": "the `contribution` column of mapping_file",
    "pillar_spend": "share_file (section,pillar,pillar_share_pct,variable,"
                    "spend,variable_share_pct)",
}


@dataclasses.dataclass
class Settings:
    """Everything one run needs, as loaded from a YAML file."""
    data: dict
    model: ModelConfig
    run: RunConfig
    sampler: SamplerConfig
    output: OutputConfig
    assumptions: AssumptionConfig
    cv: CVConfig
    source_path: str = ""

    def as_dict(self) -> dict:
        """Round-trippable plain-Python view (features are dropped again)."""
        out = {"data": dict(self.data)}
        for key, cls in SECTIONS.items():
            skip = EXCLUDED.get(key, ())
            obj = getattr(self, key)
            out[key] = {f.name: getattr(obj, f.name)
                        for f in fields(cls)
                        if f.name not in skip and not f.name.startswith("_")}
        return out


# --------------------------------------------------------------------------- #
# one-line help for every settable field. A test asserts this covers them all,
# so a new config field cannot ship without an explanation in the template.
# --------------------------------------------------------------------------- #
HELP: dict[str, dict[str, str]] = {
    "data": {
        "input_path": "panel file: one row per region x date, columns date/region/dv/features",
        "sheet": "Excel sheet name (null = first sheet). Ignored for .csv/.parquet",
        "feature_priors": "the feature/prior table - the list of modelled columns",
        "date_format": "explicit strptime format for the date column (null = infer)",
        "mapping_file": ("OPTIONAL. vendor_variable, our_variable[, region][, contribution] - "
                         "which vendor variable is which of ours. Replicate the vendor name "
                         "across rows when we split it (period / sub-brand), or ours when "
                         "they do. Every our_variable must be in data.feature_priors (which "
                         "may carry more). Groups the benchmark sheet; WITH contributions it "
                         "also drives the pre-model prior builder (case a) and pre-fills the "
                         "sheet's benchmark cells. Sample: samples/mapping_sample.csv"),
        "share_file": ("OPTIONAL. section, pillar, pillar_share_pct, variable, spend, "
                       "variable_share_pct[, sign_constraint]. Sections: media, expert, "
                       "comp_media, trade, baseline. Builds the prior file from shares of "
                       "sales when the mapping has no contributions (cases b and c); always "
                       "supplies pillars and the baseline flag. Sample: "
                       "samples/share_sample.csv"),
        "dv_aggregation": ("the per-region KPI level a generated coefficient is expressed "
                           "against: mean (default) | sum | median. It must equal run.dv_scale, "
                           "so keep mean and set run.dv_scale: mean when fitting with generated "
                           "priors - the pre-model step warns otherwise"),
        "pre_model_dir": ("where the pre-model step writes feature_priors_national.csv, "
                          "feature_priors_regional.csv and the calculation workbook. null = "
                          "pre_model_outputs/"),
    },
    "model": {
        "likelihood": "'normal' | 'student_t'. student_t is robust to promo/holiday spikes",
        "fourier_order": "annual seasonality harmonics. 0 = no seasonality block. 2 is a good start",
        "fourier_period_days": "period of the seasonal cycle in days (365.25 = annual)",
        "include_trend": "add a pooled per-region linear time trend",
        "include_intercept": ("keep the region intercept alpha_g. false removes it entirely, so no "
                              "ESTIMATED term can absorb sales the drivers should be explaining - "
                              "reach for it when the intercept has eaten the decomposition. Note "
                              "the level does not vanish: with run.dv_center: mean the baseline "
                              "becomes the FIXED historical mean (zero posterior width) instead of "
                              "a free parameter. Only sane with run.dv_center: mean"),
        "alpha_prior_sd": "prior sd of the POPULATION intercept (KPI is standardised, so 0.5 is wide)",
        "alpha_regional_sd": "prior scale of how far region intercepts spread around it",
        "pool_sigma": "partial-pool the per-region noise level. false = independent sigma per region",
    },
    "run": {
        "run_name": "output folder name. Change it every run or you overwrite the last one",
        "output_dir": "parent directory the run folder is created in",
        "date_col": "name of the date column in the input file",
        "region_col": "name of the region column",
        "dv_col": "name of the KPI/dependent column. Pass it RAW - scaling happens here",
        "dv_center": "'mean' | 'none' - what is subtracted from the KPI before fitting",
        "dv_scale": "'none'|'sd'|'mean'|'mean_positive'|'max' - the unit your PRIORS live in",
        "dv_scale_scope": "'region' (own scale each) | 'global' (one number for all regions)",
        "scaling_window": ("'train' | 'full' - which periods the centring/scaling statistics "
                           "come from, for features AND the KPI. 'full' matches the window "
                           "generated priors were computed over, but the holdout metrics are "
                           "then not strictly out of sample. CV always uses each fold's train "
                           "window"),
        "cadence": "'auto'|'weekly'|'monthly' - sets every period count downstream",
        "holdout_periods": ("last N dates held out per region for OOS metrics, as an ABSOLUTE "
                            "count. null = use holdout_fraction / the cadence policy"),
        "holdout_fraction": ("holdout as a FRACTION of the panel, so it follows the data: 0.125 "
                             "is 13 weeks on 2 years and 26 on 4. Ignored when holdout_periods "
                             "is an integer. null = the policy default (0.125)"),
        "report_draws": "posterior draws used for the decomposition and plots",
        "on_convergence_failure": "'warn' | 'fail' - 'fail' refuses to persist an unconverged fit",
        "zero_threshold_rel": "snap |v| < this * max|v| to 0 before scaling. ~1e-6 kills adstock dust",
        "min_feature_scale": "reject a scale-only feature whose scaling factor is below this",
        "near_constant_sd": ("warn when an always-on uncentred feature has sd / level below "
                             "this (collinear with alpha)"),
    },
    "sampler": {
        "draws": "posterior draws kept per chain",
        "tune": "warmup/adaptation steps per chain (discarded)",
        "chains": "independent chains. 4 is the minimum for a trustworthy R-hat",
        "target_accept": "NUTS acceptance target. Raise toward 0.99 to clear divergences",
        "seed": "random seed - set it, so a run is reproducible",
        "sampler": "'numpyro' (JAX/GPU) | 'pymc' | 'advi'",
        "chain_method": "numpyro only: 'sequential' | 'parallel' | 'vectorized' (best on one GPU)",
        "nuts_kwargs": "extra kwargs for the NUTS kernel only (mapping, {} for none)",
        "prior_predictive_draws": "prior draws - these feed the contraction report, keep >= 500",
        "store_log_likelihood": "keep pointwise log-lik for LOO/WAIC. Makes the trace much bigger",
        "advi_iters": "sampler: advi only - mean-field VI iterations",
        "allow_sampler_fallback": "false = a failed GPU run raises instead of silently going to CPU",
    },
    "output": {
        "model_input_matrix": "01_data: every row exactly as the model sees it",
        "model_input_summary": "01_data: per region x feature scaled-column statistics",
        "data_plots": "01_data: kpi_by_region.png",
        "collinearity": ("01_data: VIF, Belsley condition number and correlated column pairs, "
                         "measured on the MODEL's design matrix (intercept + Fourier + trend + "
                         "features), per region. Pre-fit: what the DATA can separate"),
        "prior_summary": "01_data: what each written prior means as a coefficient distribution",
        "contraction_plot": "02_convergence: prior_posterior_contraction.png",
        "prior_posterior_plots": "02_convergence: the three-curve prior/data/posterior chart per parameter",
        "prior_posterior_max": "cap on how many of those charts to draw (largest mean shift first)",
        "report_intercept": ("include mu_alpha/tau_alpha/z_alpha/alpha_region in the CONTRACTION "
                             "report and its charts. false hides them when the intercept is a "
                             "nuisance level. Never affects convergence tables or reconciliation"),
        "forest_plots": "03_coefficients: per-feature forest plot across regions",
        "actual_vs_predicted": "04_fit: row-level actual / fitted / residual",
        "assumption_checks": ("04_fit: linearity, homoscedasticity, autocorrelation, residual "
                              "tails and influence, plus which coefficient pairs are trading off "
                              "in the posterior. Post-fit: what the MODEL could separate"),
        "fit_plots": "04_fit: fit and residual charts",
        "contribution_summary": "05_contributions: the vendor-style volume + % table",
        "contribution_timeseries": "05_contributions: volume per region x date x driver (large file)",
        "contribution_math": "05_contributions: the beta x sum(x) x dv_scale audit trail",
        "contribution_reconciliation": "05_contributions: components -> fitted -> actual",
        "benchmark_comparison": ("05_contributions: benchmark_comparison.xlsx - paste a "
                                 "benchmark contribution into one column and %diff, the "
                                 "ratio, delta and the corrected global_prior_mean "
                                 "recalculate live. Needs contribution_math"),
        "contribution_plots": "05_contributions: contribution bars and stacked decomposition",
        "period_split": "'none' | 'week' | 'year' | 'mat' - reporting blocks in contribution_summary",
        "cadence": "'auto'|'weekly'|'monthly' - sets the MAT block length (52 weekly / 12 monthly)",
        "include_raw_features": "also dump the pre-scaling feature values",
        "rope_scaled": ("region of practical equivalence on the SCALED coefficient axis. Gives "
                        "prob_negligible, the only non-vacuous 'significance' for a "
                        "sign-constrained feature (whose p_value is 0 by construction). 0 = skip"),
        "fig_dpi": "resolution of every chart. 160 stays legible pasted into a deck",
        "fig_scale": "multiplies every figure size. Raise for projection, lower to fit a page",
    },
    "assumptions": {
        "vif_warn": "VIF flagged as moderate. Textbook 5 (Meridian uses 1000 - see MERIDIAN_ASSUMPTIONS.md)",
        "vif_bad": "VIF flagged as severe. Textbook 10",
        "cond_warn": "Belsley condition number, moderate",
        "cond_bad": "Belsley condition number, severe. This is what catches a feature that duplicates the intercept",
        "pair_warn": "|corr| between design columns to report. SET 0 TO LIST EVERY PAIR",
        "pair_bad": "|corr| above which a pair is called severe",
        "vif_top_k": "how many culprits to name per feature ('explained by A, B and C'). 0 = off",
        "corr_heatmap": "write 01_data/collinearity_heatmap_<region>.png",
        "heatmap_max_features": "above this many features a heatmap is unreadable; the highest-VIF ones are kept",
        "post_corr_warn": "|corr| between coefficient DRAWS - the pair is trading off",
        "post_corr_bad": "...severe",
        "dw_lo": "Durbin-Watson lower bound. Below this = positive residual autocorrelation",
        "dw_hi": "Durbin-Watson upper bound",
        "linearity_max_corr": "|corr(residual, fitted)| above which the functional form is questioned",
        "hetero_ratio_max": "sd(resid) in the top third of fitted values / the bottom third",
        "acf_max": "|autocorrelation| at lags 2/4/13",
        "skew_max": "|skew| of the standardised residuals",
        "kurtosis_max": "excess kurtosis before student_t is recommended",
        "influence_sd": "|standardised residual| counted as an influential point",
        "exogeneity_max_lags": "cross-correlate each feature against the residual over +/- this many periods",
        "exogeneity_warn": "|cross-correlation| to flag, floored at 2/sqrt(n) on short panels",
        "confound_warn": "|corr(treatment, control)| - Meridian's bar, also floored at 2/sqrt(n)",
        "ppp_fail": "aggregate posterior predictive p-value below which the total is implausible",
        "neg_baseline_review": "P(baseline < 0) that triggers a review",
        "neg_baseline_fail": "P(baseline < 0) that fails",
    },
    "cv": {
        "enabled": ("run expanding-window cross-validation after the main fit. Off by default: "
                    "every fold is a FULL refit, so a 5-fold CV costs roughly 5x the headline "
                    "run. Turn it on once the single fit looks sane. Outputs land in "
                    "06_cross_validation/"),
        "cadence": "'auto'|'weekly'|'monthly' - the preset every null below is filled from",
        "horizon": ("test periods per fold, ABSOLUTE. null -> horizon_fraction, then the "
                    "policy (12.5% of the panel: 13 on a 2-year weekly panel, 26 on 4)"),
        "horizon_fraction": "test periods per fold as a fraction of the panel. null = the policy",
        "n_folds": "number of expanding-window folds (null -> 5 weekly / 3 monthly)",
        "step": "periods between fold origins (null -> horizon)",
        "min_train_periods": ("shortest training window, ABSOLUTE. null -> min_train_fraction, "
                              "then the policy (50% of the panel, never under one year)"),
        "min_train_fraction": "shortest training window as a fraction of the panel",
        "draws": "override sampler.draws for CV speed (null -> preset)",
        "tune": "override sampler.tune for CV speed (null -> preset)",
        "make_plots": "write the CV accuracy charts",
    },
}

SECTION_BLURB = {
    "data": "Where the inputs are. Paths are relative to this file unless absolute.",
    "model": "The statistical model: what terms exist and how strong the baseline priors are.",
    "run": "Data handling, the KPI scale (= the unit your priors live in) and run bookkeeping.",
    "sampler": "MCMC settings. Nothing here changes the model, only how well it is explored.",
    "output": "Which files each stage writes. The CORE tables are always written.",
    "assumptions": "Thresholds for every collinearity and assumption check. Widen or narrow here, not in code.",
    "cv": "Expanding-window cross-validation, used only by cross_validation.run_cv().",
}


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def _settable(cls, section: str):
    skip = EXCLUDED.get(section, ())
    return [f for f in fields(cls) if f.name not in skip and not f.name.startswith("_")]


def _check_keys(got, valid, where: str) -> None:
    unknown = [k for k in got if k not in valid]
    if not unknown:
        return
    bits = []
    for k in unknown:
        if k in RENAMED_KEYS:
            bits.append(f"{k!r} (REMOVED - use {RENAMED_KEYS[k]})")
            continue
        near = difflib.get_close_matches(str(k), list(valid), n=1, cutoff=0.6)
        bits.append(f"{k!r}" + (f" (did you mean {near[0]!r}?)" if near else ""))
    raise ValueError(
        f"unknown key(s) in section [{where}]: {', '.join(bits)}.\n"
        f"Valid keys: {', '.join(sorted(valid))}.\n"
        "Unknown keys are rejected rather than ignored - a typo would otherwise "
        "silently leave the default in place.")


def _resolve_path(value, base_dir: str):
    if not value or os.path.isabs(str(value)):
        return value
    return os.path.normpath(os.path.join(base_dir, str(value)))


def load_settings(path: str, features=None) -> Settings:
    """Read a YAML settings file into real config objects.

    `features` overrides `data.feature_priors` when you already have the specs
    in hand (e.g. built in a notebook). Otherwise the CSV named in the YAML is
    loaded; a Settings can also be built with no features at all, which is what
    `write_default_yaml` round-trip checks do.
    """
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: the top level of the file must be a mapping "
                         f"of sections, got {type(raw).__name__}")

    _check_keys(raw, set(SECTIONS) | {"data"}, "top level")
    base_dir = os.path.dirname(os.path.abspath(path))

    data = dict(raw.get("data") or {})
    _check_keys(data, set(DATA_KEYS), "data")
    for k in ("input_path", "feature_priors", "mapping_file", "share_file",
              "pre_model_dir"):
        if data.get(k):
            data[k] = _resolve_path(data[k], base_dir)

    if features is None and data.get("feature_priors"):
        features = load_feature_config(data["feature_priors"])

    built = {}
    for key, cls in SECTIONS.items():
        block = dict(raw.get(key) or {})
        valid = {f.name for f in _settable(cls, key)}
        _check_keys(block, valid, key)
        if key == "model":
            block["features"] = list(features or [])
        built[key] = cls(**block)

    return Settings(data=data, source_path=os.path.abspath(path), **built)


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def _yaml_scalar(v) -> str:
    """One value as YAML text.

    Everything goes through PyYAML rather than repr(), because repr() is not
    YAML: repr(1e-12) is "1e-12", which YAML 1.1 reads back as the STRING
    "1e-12" (it wants the "1.0e-12" form). That silently turned
    min_feature_scale into text on the first attempt at this file.
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dict) and not v:
        return "{}"
    if isinstance(v, (list, tuple)) and not v:
        return "[]"
    if isinstance(v, tuple):
        v = list(v)
    text = yaml.safe_dump(v, default_flow_style=True, width=10 ** 6).strip()
    if text.endswith("..."):                     # document-end marker on scalars
        text = text[:-3].strip()
    return text



def _wrap_comment(text: str, indent: int, width: int = 96) -> list[str]:
    pad = " " * indent + "# "
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(pad) + len(cur) + 1 + len(w) > width:
            lines.append(pad + cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(pad + cur)
    return lines


DEFAULT_DATA = {
    "input_path": "input_datacube.xlsx",
    "sheet": None,
    "feature_priors": "feature_priors.csv",
    "date_format": None,
    "mapping_file": None,
    "share_file": None,
    "dv_aggregation": "mean",
    "pre_model_dir": None,
}

HEADER = """\
# ===========================================================================
#  Codebase 1 - hierarchical MMM: run settings
# ===========================================================================
#  EVERY key below is shown at its DEFAULT value, so this file doubles as the
#  reference for what the pipeline does when you say nothing. Delete anything
#  you are happy with - omitted keys fall back to exactly these values.
#
#  Run it:
#      from settings import run_from_yaml
#      result = run_from_yaml("config.yaml")
#  or, from a shell:
#      python settings.py config.yaml
#
#  Regenerate this file (e.g. after upgrading the codebase):
#      python settings.py --write config.yaml
#
#  A typo is an ERROR, not a silent default: an unknown key stops the run and
#  names the closest valid spelling.
#
#  The FEATURES and their priors are not here - they live in the CSV named by
#  data.feature_priors, because a per-feature prior table belongs in a table.
#  See docs/TUNING_GUIDE.md for which knob to reach for in which situation.
# ===========================================================================
"""


def default_settings_text() -> str:
    """The annotated YAML template: every field, its default and one line of help."""
    out = [HEADER]
    blocks = [("data", DEFAULT_DATA, None)]
    for key, cls in SECTIONS.items():
        defaults = {}
        for f in _settable(cls, key):
            if f.default is not dataclasses.MISSING:
                defaults[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                defaults[f.name] = f.default_factory()          # type: ignore[misc]
        blocks.append((key, defaults, cls))

    for key, defaults, _cls in blocks:
        out.append("")
        out.append("# " + "-" * 74)
        out.extend(_wrap_comment(SECTION_BLURB[key], 0))
        out.append("# " + "-" * 74)
        out.append(f"{key}:")
        help_for = HELP.get(key, {})
        for name, value in defaults.items():
            out.extend(_wrap_comment(help_for.get(name, "(undocumented)"), 2))
            out.append(f"  {name}: {_yaml_scalar(value)}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_default_yaml(path: str) -> str:
    """Write the annotated all-defaults template. Refuses to clobber silently."""
    if os.path.exists(path):
        raise FileExistsError(
            f"{path} already exists. Delete or rename it first - this workspace "
            "is not under version control, so overwriting would lose your edits.")
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(default_settings_text())
    return path


def dump_settings(settings: Settings, path: str) -> str:
    """Write the EFFECTIVE settings of a run (defaults filled in) as plain YAML.

    Dropped next to a run's outputs this is the record of what actually ran -
    including every default the YAML did not mention.
    """
    payload = settings.as_dict()
    payload["_source"] = settings.source_path
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# effective settings for this run - every default filled in\n")
        yaml.safe_dump(payload, fh, sort_keys=False, default_flow_style=False)
    return path


# --------------------------------------------------------------------------- #
# driving a run from the file
# --------------------------------------------------------------------------- #
def load_panel(settings: Settings):
    """Read the input panel named by `data.input_path` (.xlsx / .csv / .parquet)."""
    import pandas as pd

    path = settings.data.get("input_path")
    if not path:
        raise ValueError("data.input_path is not set in the settings file")
    ext = os.path.splitext(str(path))[1].lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(path, sheet_name=settings.data.get("sheet") or 0,
                           engine="openpyxl")
    elif ext == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    fmt = settings.data.get("date_format")
    dc = settings.run.date_col
    if fmt and dc in df.columns:
        df[dc] = pd.to_datetime(df[dc], format=fmt)
    return df


def run_from_yaml(path: str, df=None, save_trace: bool = True,
                  record_settings: bool = True, settings: "Settings" = None,
                  extra_warnings: list | None = None):
    """Load a settings file, fit, and write the full report.

    `df` skips the file read when the panel is already in memory. The effective
    settings are written to <run folder>/01_data/resolved_config.yaml so the run
    can be reproduced without the original file.

    Cross-validation runs afterwards only when `cv.enabled` is true - it is a
    full refit per fold, so it is opt-in rather than part of every run.
    """
    from mmm.run_pipeline import run
    from mmm.checks.warnings_report import collect_warnings
    try:
        from mmm.data.prior_builder import run_pre_model
    except ImportError:            # optional - the run works without it
        run_pre_model = None

    # Loading the settings resolves every feature spec, which is where the
    # per-feature prior warnings come from. Capture them here so they reach
    # 00_warnings/ instead of the notebook. A caller that already loaded the
    # settings passes them in, so the file is not read (and re-warned) twice.
    caught = list(extra_warnings or [])
    if settings is None:
        with collect_warnings() as c:
            settings = load_settings(path)
        caught += list(c)
    if not settings.model.features:
        raise ValueError(
            f"{path}: no features. Set data.feature_priors to the prior CSV.")
    panel = load_panel(settings) if df is None else df

    # PRE-MODEL: if the client gave us a vendor decomposition or a pillar/spend
    # file, turn it into a sample prior file (plus the working) before fitting.
    # It never overwrites `data.feature_priors` - you review it and point at it
    # yourself, because a generated prior is a proposal, not a decision.
    if run_pre_model is not None and (settings.data.get("mapping_file")
                                      or settings.data.get("share_file")):
        with collect_warnings() as _pre:
            result_pre = run_pre_model(settings, df=panel)
        caught += list(_pre)
    else:
        result_pre = {}

    result = run(panel, settings.model, settings.run, settings.sampler,
                 save_trace=save_trace, out_cfg=settings.output,
                 cv_cfg=settings.cv, extra_warnings=caught,
                 assumption_cfg=settings.assumptions,
                 benchmark_mapping=settings.data.get("mapping_file"))
    if record_settings:
        dump_settings(settings, os.path.join(result["output_dir"], "01_data",
                                             "resolved_config.yaml"))
    result["settings"] = settings
    if result_pre and result_pre.get("case") != "d":
        result["pre_model"] = result_pre
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "--write":
        print("wrote", write_default_yaml(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] not in ("-h", "--help"):
        r = run_from_yaml(sys.argv[1])
        print("outputs ->", r["output_dir"])
    else:
        print(__doc__)
        print("usage:\n  python settings.py --write config.yaml"
              "\n  python settings.py config.yaml")
