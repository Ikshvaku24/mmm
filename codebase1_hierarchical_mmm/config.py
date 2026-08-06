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
    sign: str = "free"                 # "positive" | "negative" | "free"
    prior_mean: float | None = None    # population prior location (magnitude if signed)
    prior_sd: float | None = None      # population prior sd (log-scale if signed)
    regional_sd: float | None = None   # prior scale of cross-region heterogeneity

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
            raise ValueError(f"{s.name}: regional_sd must be finite")
        if s.hierarchical and s.regional_sd <= 0:
            raise ValueError(f"{s.name}: hierarchical features require regional_sd > 0")
        if s.regional_sd < 0:
            raise ValueError(f"{s.name}: regional_sd cannot be negative")
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
            name=str(r["variable"]).strip(),
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
    likelihood: str = "normal"          # "normal" | "student_t"
    fourier_order: int = 0              # 0 = no seasonality block
    fourier_period_days: float = 365.25
    include_trend: bool = False
    alpha_prior_sd: float = 0.5         # population intercept prior (dv is standardised)
    alpha_regional_sd: float = 0.5
    pool_sigma: bool = True             # partial-pool region noise on the log scale
                                        # (recommended when regions have unequal
                                        # series lengths); False = independent
                                        # HalfNormal sigma per region

    def __post_init__(self):
        self.features = [s.resolved() for s in self.features]
        names = [s.name for s in self.features]
        if len(names) != len(set(names)):
            raise ValueError("duplicate feature names in config")
        if self.likelihood not in {"normal", "student_t"}:
            raise ValueError("likelihood must be 'normal' or 'student_t'")
        if self.fourier_order < 0:
            raise ValueError("fourier_order must be >= 0")


@dataclass
class SamplerConfig:
    draws: int = 1000
    tune: int = 1000
    chains: int = 4
    target_accept: float = 0.92
    seed: int = 42
    sampler: str = "numpyro"            # "numpyro" (JAX/GPU) | "pymc" | "advi"
    chain_method: str = "sequential"    # NumPyro chain execution: "sequential" |
                                        # "parallel" (multi-device) | "vectorized"
                                        # (recommended on a single GPU)
    nuts_kwargs: dict = field(default_factory=dict)  # extra args for the NUTS
                                        # kernel only (NOT chain_method)
    prior_predictive_draws: int = 500
    store_log_likelihood: bool = False  # compute pointwise log-lik after sampling
                                        # (needed for LOO/WAIC; increases trace size)
    advi_iters: int = 30000             # sampler="advi": mean-field VI iterations.
                                        # PE-package convention: ADVI for CV /
                                        # exploration speed, NUTS for the final fit
                                        # (ADVI understates uncertainty).
    allow_sampler_fallback: bool = False  # False = a failed numpyro/GPU run raises
                                          # instead of silently switching to the
                                          # slow default sampler

    def __post_init__(self):
        if self.sampler not in {"numpyro", "pymc", "advi"}:
            raise ValueError("sampler must be 'numpyro', 'pymc', or 'advi'")
        if self.chain_method not in {"sequential", "parallel", "vectorized"}:
            raise ValueError(
                "chain_method must be 'sequential', 'parallel', or 'vectorized'")
        if self.draws <= 0 or self.chains <= 0 or self.tune < 0:
            raise ValueError("draws/chains must be > 0 and tune >= 0")
        if not 0 < self.target_accept < 1:
            raise ValueError("target_accept must be between 0 and 1")


@dataclass
class RunConfig:
    run_name: str = "run"
    output_dir: str = "outputs"
    date_col: str = "date"
    region_col: str = "region"
    dv_col: str = "dv"
    holdout_periods: int = 0            # last N dates held out per region for OOS metrics
    report_draws: int = 400             # posterior draws used for decomposition/plots
    on_convergence_failure: str = "warn"  # "warn" | "fail" - PE-style guardrail:
                                          # "fail" raises instead of silently
                                          # persisting an unconverged fit
    zero_threshold_rel: float = 0.0       # if > 0: feature values with
                                          # |v| < threshold * max|v| are set to 0
                                          # before scaling. Use ~1e-6 for
                                          # pre-transformed data whose adstock tail
                                          # leaves numerical dust (e.g. 5.2e-17)
                                          # instead of exact zeros.

    def __post_init__(self):
        if self.on_convergence_failure not in {"warn", "fail"}:
            raise ValueError("on_convergence_failure must be 'warn' or 'fail'")
        if self.zero_threshold_rel < 0:
            raise ValueError("zero_threshold_rel must be >= 0")


@dataclass
class CVConfig:
    """Expanding-window (rolling-origin) cross-validation settings.

    Fold k trains on everything before its test window and predicts the next
    `horizon` periods; origins step back through the series so accuracy and
    coefficient stability are measured across several windows, not one.
    """
    horizon: int = 13                  # test periods per fold
    n_folds: int = 5
    step: int | None = None            # spacing between origins (default: horizon)
    min_train_periods: int = 52        # skip folds with less training data
    draws: int | None = None           # override sampler draws for CV speed
    tune: int | None = None
    make_plots: bool = True
