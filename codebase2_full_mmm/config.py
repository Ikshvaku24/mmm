"""Configuration for the full MMM (Codebase 2 - raw media, transforms learned in-model).

Meridian-style: feed RAW weekly media (GRPs / clicks / impressions - never
pre-adstocked). Adstock (carryover), Hill (saturation) and lag (peak delay)
are applied INSIDE the model and their parameters are learned by NUTS, then
reported with credible intervals (04_transforms outputs).

Per-channel transform control (goal 4 "controllable per feature"):
  adstock="geometric"  w_l = decay^l                (Meridian's default adstock)
  adstock="delayed"    w_l = decay^((l-theta)^2)    (learned peak lag theta)
  adstock="none"       no adstock/Hill - the channel enters linearly
  fix_alpha / fix_ec / fix_slope / fix_theta        pin any parameter instead
                                                    of learning it
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VALID_SIGNS = ("positive", "negative", "free")
BUCKET_ORDER = ["hpos", "hneg", "hfree", "gpos", "gneg", "gfree"]


# --------------------------------------------------------------------------
# media channels (get adstock + Hill unless adstock="none")
# --------------------------------------------------------------------------
@dataclass
class ChannelSpec:
    name: str
    col: str                          # raw execution column (GRPs, clicks, ...)
    spend_col: str | None = None      # enables ROI reporting
    adstock: str = "geometric"        # "geometric" | "delayed" | "none"
    max_lag: int = 13                 # carryover window, periods
    learn_slope: bool = False         # Hill slope: default fixed (Meridian default = 1.0)
    fix_alpha: float | None = None    # pin adstock decay in [0,1)
    fix_ec: float | None = None       # pin half-saturation (scaled units)
    fix_slope: float | None = None    # pin Hill slope (used when learn_slope=False)
    fix_theta: float | None = None    # pin peak delay (delayed adstock only)
    prior_beta_mean: float = 0.05     # typical effect magnitude (scaled axes), > 0
    prior_beta_sd: float = 1.0        # log-scale sd
    regional_sd: float = 0.5          # cross-region heterogeneity (log scale)

    def resolved(self) -> "ChannelSpec":
        s = ChannelSpec(**self.__dict__)
        if s.adstock not in ("geometric", "delayed", "none"):
            raise ValueError(f"{s.name}: adstock must be geometric|delayed|none")
        if s.prior_beta_mean <= 0:
            warnings.warn(f"{s.name}: prior_beta_mean must be > 0, using 0.05")
            s.prior_beta_mean = 0.05
        if s.max_lag < 1:
            raise ValueError(f"{s.name}: max_lag must be >= 1")
        if not (np.isfinite(s.prior_beta_sd) and s.prior_beta_sd > 0):
            raise ValueError(f"{s.name}: prior_beta_sd must be finite and > 0")
        if not (np.isfinite(s.regional_sd) and s.regional_sd >= 0):
            raise ValueError(f"{s.name}: regional_sd must be finite and >= 0")
        return s


# --------------------------------------------------------------------------
# linear features: controls, non-media treatments (price/promo/"trade"), etc.
# --------------------------------------------------------------------------
@dataclass
class FeatureSpec:
    name: str
    hierarchical: bool = True
    sign: str = "free"
    prior_mean: float | None = None
    prior_sd: float | None = None
    regional_sd: float | None = None

    def resolved(self) -> "FeatureSpec":
        s = FeatureSpec(**self.__dict__)
        if s.sign not in VALID_SIGNS:
            raise ValueError(f"{s.name}: sign must be one of {VALID_SIGNS}")
        if s.sign == "free":
            s.prior_mean = 0.0 if s.prior_mean is None else float(s.prior_mean)
            s.prior_sd = 0.5 if s.prior_sd is None else float(s.prior_sd)
        else:
            if s.prior_mean is None or s.prior_mean <= 0:
                s.prior_mean = 0.05
            s.prior_sd = 1.0 if s.prior_sd is None else float(s.prior_sd)
        s.regional_sd = 0.5 if s.regional_sd is None else float(s.regional_sd)
        if not (np.isfinite(s.prior_sd) and s.prior_sd > 0):
            raise ValueError(f"{s.name}: prior_sd must be finite and > 0")
        if not (np.isfinite(s.regional_sd) and s.regional_sd >= 0):
            raise ValueError(f"{s.name}: regional_sd must be finite and >= 0")
        return s


def bucket_name(spec: FeatureSpec) -> str:
    return ("h" if spec.hierarchical else "g") + {
        "positive": "pos", "negative": "neg", "free": "free"}[spec.sign]


def bucket_features(specs: list[FeatureSpec]) -> dict[str, list[FeatureSpec]]:
    return {b: [s for s in specs if bucket_name(s) == b] for b in BUCKET_ORDER}


def load_channel_config(path: str) -> list[ChannelSpec]:
    """CSV columns (extras ignored): name, col, spend_col, adstock, max_lag,
    learn_slope, fix_alpha, fix_ec, fix_slope, fix_theta,
    prior_beta_mean, prior_beta_sd, regional_sd"""
    df = pd.read_csv(path)

    def _f(r, c):
        return None if c not in r or pd.isna(r[c]) else float(r[c])

    out = []
    for _, r in df.iterrows():
        out.append(ChannelSpec(
            name=str(r["name"]).strip(), col=str(r["col"]).strip(),
            spend_col=None if pd.isna(r.get("spend_col")) else str(r["spend_col"]).strip(),
            adstock=str(r.get("adstock", "geometric")).strip().lower(),
            max_lag=int(r.get("max_lag", 13)),
            learn_slope=bool(int(r.get("learn_slope", 0))),
            fix_alpha=_f(r, "fix_alpha"), fix_ec=_f(r, "fix_ec"),
            fix_slope=_f(r, "fix_slope"), fix_theta=_f(r, "fix_theta"),
            prior_beta_mean=float(r.get("prior_beta_mean", 0.05)),
            prior_beta_sd=float(r.get("prior_beta_sd", 1.0)),
            regional_sd=float(r.get("regional_sd", 0.5)),
        ).resolved())
    return out


@dataclass
class ModelConfig:
    channels: list[ChannelSpec]
    controls: list[FeatureSpec] = field(default_factory=list)
    likelihood: str = "normal"            # "normal" | "student_t"
    fourier_order: int = 2                # seasonality baseline (0 = off)
    fourier_period_days: float = 365.25
    include_trend: bool = True
    alpha_prior_sd: float = 0.5
    alpha_regional_sd: float = 0.5
    pool_sigma: bool = True               # partial-pool region noise on the log scale
    # Hill half-saturation prior on median-scaled media (Meridian default:
    # ec ~ TruncatedNormal(0.8, 0.8) on [0.1, 10])
    ec_mu: float = 0.8
    ec_sigma: float = 0.8
    ec_lower: float = 0.1
    ec_upper: float = 10.0
    theta_max: float = 6.0                # max peak delay for delayed adstock

    def __post_init__(self):
        self.channels = [c.resolved() for c in self.channels]
        self.controls = [s.resolved() for s in self.controls]
        names = [c.name for c in self.channels] + [s.name for s in self.controls]
        if len(names) != len(set(names)):
            raise ValueError("duplicate channel/control names in config")

    @property
    def transformed_channels(self) -> list[ChannelSpec]:
        return [c for c in self.channels if c.adstock != "none"]

    @property
    def linear_channels(self) -> list[ChannelSpec]:
        return [c for c in self.channels if c.adstock == "none"]

    @property
    def linear_features(self) -> list[FeatureSpec]:
        """Controls + 'none' channels (linear, positive-signed, hierarchical)."""
        lin = [FeatureSpec(name=c.name, hierarchical=True, sign="positive",
                           prior_mean=c.prior_beta_mean, prior_sd=c.prior_beta_sd,
                           regional_sd=c.regional_sd).resolved()
               for c in self.linear_channels]
        return lin + self.controls


@dataclass
class SamplerConfig:
    draws: int = 1000
    tune: int = 1000
    chains: int = 4
    target_accept: float = 0.92
    seed: int = 42
    sampler: str = "numpyro"
    chain_method: str = "sequential"      # NumPyro chains: "sequential" |
                                          # "parallel" | "vectorized" (1 GPU)
    nuts_kwargs: dict = field(default_factory=dict)  # NUTS kernel args only
    prior_predictive_draws: int = 500
    store_log_likelihood: bool = False    # compute pointwise log-lik post-fit
    advi_iters: int = 30000               # sampler="advi": mean-field VI iterations
                                          # (PE convention: ADVI for CV speed,
                                          # NUTS for the final fit)
    allow_sampler_fallback: bool = False  # False = failed numpyro run raises

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
    holdout_periods: int = 0
    report_draws: int = 300
    zero_fill_media: bool = True     # missing media/spend cells -> 0 (with a count warning)
    revenue_per_unit: float = 1.0    # ROI = contribution * revenue_per_unit / spend
                                     # (leave 1.0 if dv is already revenue)
    on_convergence_failure: str = "warn"  # "warn" | "fail" - PE-style guardrail


@dataclass
class CVConfig:
    """Expanding-window (rolling-origin) cross-validation settings."""
    horizon: int = 13                  # test periods per fold
    n_folds: int = 5
    step: int | None = None            # spacing between origins (default: horizon)
    min_train_periods: int = 52
    draws: int | None = None           # override sampler draws for CV speed
    tune: int | None = None
    make_plots: bool = True
