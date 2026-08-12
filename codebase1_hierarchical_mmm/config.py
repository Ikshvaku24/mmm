"""Configuration for the hierarchical MMM (Codebase 1 - pre-transformed inputs).

Priors are specified per FEATURE, and optionally ALSO per (feature, region).
For each feature you set:

  - a population prior (where the market-average effect sits),
  - a regional heterogeneity prior (how much regions may differ),
  - a sign constraint (positive / negative / free),
  - a pooling mode (see below),
  - optionally, per-region prior overrides.

Pooling modes (`pooling`, one per feature):
  "hierarchical"  partial pooling - one population prior, regional coefficients
                  shrink toward it (the default; Meridian's beta_gm = beta_m +
                  eta_m * N(0,1)).
  "independent"   every region gets its OWN prior and its own coefficient, with
                  no pooling. This is the mode for full per-region prior control
                  (and reproduces the old b0/B0-per-region prior file).
  "global"        one coefficient shared by every region.

Per-region priors (a `region` column in the prior CSV) work in both region-aware
modes, but they mean different things:
  - under "independent": the region's prior IS its prior (mean and sd).
  - under "hierarchical": the region's prior_mean shifts where that region's
    coefficient is centred, as a fixed offset, while the hierarchy still pools
    how far regions wander from their own centre. A per-region prior_sd is not
    used here (the pooled tau plays that role) and is ignored with a warning.

Baseline features (`baseline=1`):
  Features flagged as baseline are folded into the BASELINE rather than reported
  as incremental effects - the right treatment for always-on business drivers
  such as distribution (TDP) and price (AVP), which are not marketing levers you
  can switch off. Their individual contributions are still computed and reported,
  so the baseline can be expanded into its parts (see 05_contributions).

Scale conventions (important for choosing priors):
  - The KPI (dv) is standardised per region, so effects are in "region sd of dv" units.
  - Sign-constrained features are divided by the per-region mean of their positive
    values (no centering - zero activity stays zero, like Meridian's media scaler).
  - Free-sign features are centred and scaled per region (like Meridian's controls).
  - For sign-constrained features, `prior_mean` is the typical effect MAGNITUDE on
    that scale (must be > 0); `prior_sd` is on the log scale (0.7 ~ a factor of 2).
  - `center=True` keeps the sign constraint but scales the feature like a control
    (centre + scale). Use it for ALWAYS-ON LEVEL variables - distribution points,
    price indices, ACV measures. Media-style scale-only scaling leaves such a
    variable at ~1.0 every week, which is collinear with the region intercept:
    the sampler cannot separate its coefficient from the baseline. Meridian
    centres its non-media treatments for exactly this reason.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VALID_SIGNS = ("positive", "negative", "free")
VALID_POOLING = ("hierarchical", "independent", "global")

# Bucket = one vectorised block of coefficients in the model.
# prefix: h = hierarchical (pooled), i = independent per region, g = one global
BUCKET_ORDER = ["hpos", "hneg", "hfree",
                "ipos", "ineg", "ifree",
                "gpos", "gneg", "gfree"]
_POOL_PREFIX = {"hierarchical": "h", "independent": "i", "global": "g"}


@dataclass
class RegionPrior:
    """Prior override for one (feature, region) cell.

    prior_mean: magnitude for signed features (> 0), location for free ones.
    prior_sd:   only used under pooling="independent" (under "hierarchical" the
                pooled tau governs the spread, so a per-region sd is ignored).
    """
    prior_mean: float | None = None
    prior_sd: float | None = None


@dataclass
class FeatureSpec:
    name: str
    hierarchical: bool = True          # DEPRECATED alias: True -> pooling
                                       # "hierarchical", False -> "global".
                                       # Ignored when `pooling` is set explicitly.
    sign: str = "free"                 # "positive" | "negative" | "free"
    prior_mean: float | None = None    # population prior location (magnitude if signed)
    prior_sd: float | None = None      # population prior sd (log-scale if signed)
    regional_sd: float | None = None   # prior scale of cross-region heterogeneity
    center: bool = False               # signed features: centre+scale instead of
                                       # mean-of-positives. REQUIRED for always-on
                                       # level variables (distribution, price index,
                                       # ACV) - see the module docstring. Ignored for
                                       # sign="free", which is always centred.
    pooling: str | None = None         # "hierarchical" | "independent" | "global"
    baseline: bool = False             # fold into the BASELINE instead of reporting
                                       # as an incremental effect (always-on business
                                       # drivers: distribution, price). Its own
                                       # contribution is still reported so the
                                       # baseline can be expanded.
    region_priors: dict = field(default_factory=dict)   # region name -> RegionPrior
    contribution_reference: object = "auto"  # counterfactual the CONTRIBUTION is
                                       # measured against, in RAW feature units:
                                       #   "auto" - matches the scaling (zero for
                                       #            scale_only, the feature mean for
                                       #            centred features)
                                       #   "zero" - "what if this were 0?" - the
                                       #            industry-standard due-to
                                       #            decomposition for always-on
                                       #            level drivers (distribution,
                                       #            price)
                                       #   "min"  - vs the lowest observed level
                                       #   <float>- vs a fixed raw value
                                       # Reporting only: it never changes the fit.
    pillar: str = ""                   # reporting group ("Online Media", "TV & DTV",
                                       # "Expert", ...) - contributions are rolled up
                                       # by pillar in 05_contributions

    def resolved(self) -> "FeatureSpec":
        s = FeatureSpec(**{**self.__dict__,
                           "region_priors": dict(self.region_priors)})
        if s.sign not in VALID_SIGNS:
            raise ValueError(f"{s.name}: sign must be one of {VALID_SIGNS}, got {s.sign!r}")
        if s.pooling is None:
            s.pooling = "hierarchical" if s.hierarchical else "global"
        s.pooling = str(s.pooling).strip().lower()
        if s.pooling not in VALID_POOLING:
            raise ValueError(
                f"{s.name}: pooling must be one of {VALID_POOLING}, got {s.pooling!r}")
        s.hierarchical = s.pooling == "hierarchical"   # keep the alias consistent
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
        s.center = bool(s.center) or s.sign == "free"   # free is always centred
        s.baseline = bool(s.baseline)
        s.pillar = "" if s.pillar is None else str(s.pillar).strip()
        ref = s.contribution_reference
        if isinstance(ref, str):
            ref = ref.strip().lower()
            if ref not in ("auto", "zero", "mean", "min"):
                try:
                    ref = float(ref)
                except ValueError:
                    raise ValueError(
                        f"{s.name}: contribution_reference must be "
                        "'auto'/'zero'/'mean'/'min' or a number, got "
                        f"{s.contribution_reference!r}") from None
        elif ref is not None:
            ref = float(ref)
        s.contribution_reference = "auto" if ref is None else ref
        s.regional_sd = 0.5 if s.regional_sd is None else float(s.regional_sd)
        if not (np.isfinite(s.prior_sd) and s.prior_sd > 0):
            raise ValueError(f"{s.name}: prior_sd must be finite and > 0")
        if not np.isfinite(s.regional_sd):
            raise ValueError(f"{s.name}: regional_sd must be finite")
        if s.pooling == "hierarchical" and s.regional_sd <= 0:
            raise ValueError(f"{s.name}: hierarchical features require regional_sd > 0")
        if s.regional_sd < 0:
            raise ValueError(f"{s.name}: regional_sd cannot be negative")

        # ---- per-region priors -------------------------------------------
        if s.region_priors and s.pooling == "global":
            raise ValueError(
                f"{s.name}: pooling='global' fits ONE coefficient for all regions, "
                "so per-region priors cannot apply. Use pooling='independent' "
                "(own prior per region) or 'hierarchical' (region prior shifts the "
                "centre it shrinks toward).")
        clean = {}
        for reg, rp in s.region_priors.items():
            rp = rp if isinstance(rp, RegionPrior) else RegionPrior(**dict(rp))
            if rp.prior_mean is not None:
                rp.prior_mean = float(rp.prior_mean)
                if s.sign != "free" and rp.prior_mean <= 0:
                    warnings.warn(
                        f"{s.name}[{reg}]: sign-constrained features need "
                        f"prior_mean > 0 (a magnitude); got {rp.prior_mean}. "
                        "Falling back to the feature-level prior.")
                    rp.prior_mean = None
            if rp.prior_sd is not None:
                rp.prior_sd = float(rp.prior_sd)
                if not (np.isfinite(rp.prior_sd) and rp.prior_sd > 0):
                    raise ValueError(
                        f"{s.name}[{reg}]: prior_sd must be finite and > 0")
                if s.pooling == "hierarchical":
                    warnings.warn(
                        f"{s.name}[{reg}]: a per-region prior_sd is ignored under "
                        "pooling='hierarchical' (the pooled regional_sd governs "
                        "spread). Use pooling='independent' to set it per region.")
                    rp.prior_sd = None
            if rp.prior_mean is not None or rp.prior_sd is not None:
                clean[str(reg)] = rp
        s.region_priors = clean
        return s

    # -- per-region prior lookup (falls back to the feature-level prior) ----
    def prior_mean_for(self, region: str) -> float:
        rp = self.region_priors.get(region)
        if rp is not None and rp.prior_mean is not None:
            return float(rp.prior_mean)
        return float(self.prior_mean)

    def prior_sd_for(self, region: str) -> float:
        rp = self.region_priors.get(region)
        if rp is not None and rp.prior_sd is not None:
            return float(rp.prior_sd)
        return float(self.prior_sd)


def bucket_name(spec: FeatureSpec) -> str:
    pooling = spec.pooling or ("hierarchical" if spec.hierarchical else "global")
    return _POOL_PREFIX[pooling] + {
        "positive": "pos", "negative": "neg", "free": "free"
    }[spec.sign]


def bucket_features(specs: list[FeatureSpec]) -> dict[str, list[FeatureSpec]]:
    return {b: [s for s in specs if bucket_name(s) == b] for b in BUCKET_ORDER}


def _cell(row, key):
    """Value of `key` in a CSV row, or None when absent/blank."""
    v = row.get(key)
    return None if v is None or pd.isna(v) else v


def load_feature_config(path: str) -> list[FeatureSpec]:
    """Read the feature prior file (CSV).

    FEATURE-LEVEL rows (one per feature, `region` blank or column absent):
      variable, hierarchical, sign_constraint,
      global_prior_mean, global_prior_sd, regional_sd_prior
    Optional columns:
      center   (0/1) 1 for always-on level variables (distribution, price index,
               ACV) so they are centred rather than only scaled.
      pooling  "hierarchical" | "independent" | "global". Defaults to
               hierarchical/global from the `hierarchical` column.
      baseline (0/1) 1 to fold the feature into the baseline instead of
               reporting it as an incremental effect.

    PER-REGION rows (optional): same file, with `region` filled in. They override
    the feature-level prior for that region only:
      variable, region, global_prior_mean[, global_prior_sd]
    A per-region row must follow a feature-level row for the same variable.
    Under pooling="hierarchical" only the mean is used (as a fixed offset to the
    centre that region shrinks toward); under "independent" both apply.

    Example:
        variable,region,pooling,sign_constraint,global_prior_mean,global_prior_sd
        TV_GM,,hierarchical,positive,0.05,1.0
        TV_GM,1-Walmart+FamilyDollar,,,0.20,
        TV_GM,3-Target-Corp,,,0.02,
    """
    df = pd.read_csv(path)
    if "variable" not in df.columns:
        raise ValueError(f"{path}: missing required column 'variable'")

    base_rows, region_rows = [], []
    for _, r in df.iterrows():
        (region_rows if _cell(r, "region") is not None else base_rows).append(r)

    specs, by_name = [], {}
    for r in base_rows:
        name = str(r["variable"]).strip()
        if name in by_name:
            raise ValueError(f"{path}: duplicate feature-level row for {name!r}")
        hier = _cell(r, "hierarchical")
        center = _cell(r, "center")
        baseline = _cell(r, "baseline")
        pooling = _cell(r, "pooling")
        spec = FeatureSpec(
            name=name,
            hierarchical=True if hier is None else bool(int(hier)),
            sign=str(r.get("sign_constraint", "free")).strip().lower(),
            prior_mean=_cell(r, "global_prior_mean"),
            prior_sd=_cell(r, "global_prior_sd"),
            regional_sd=_cell(r, "regional_sd_prior"),
            center=False if center is None else bool(int(center)),
            pooling=None if pooling is None else str(pooling).strip().lower(),
            baseline=False if baseline is None else bool(int(baseline)),
            contribution_reference=_cell(r, "contribution_reference") or "auto",
            pillar=_cell(r, "pillar") or "",
        )
        by_name[name] = spec
        specs.append(spec)

    for r in region_rows:
        name = str(r["variable"]).strip()
        if name not in by_name:
            raise ValueError(
                f"{path}: per-region row for {name!r} (region "
                f"{_cell(r, 'region')!r}) has no feature-level row. Add a row for "
                f"{name!r} with the `region` column left blank.")
        by_name[name].region_priors[str(_cell(r, "region")).strip()] = RegionPrior(
            prior_mean=_cell(r, "global_prior_mean"),
            prior_sd=_cell(r, "global_prior_sd"),
        )

    return [s.resolved() for s in specs]


def validate_region_priors(specs: list[FeatureSpec], region_names: list) -> None:
    """Fail loudly on per-region priors whose region is not in the data.

    A typo'd region name would otherwise be silently ignored and the analyst
    would never learn their prior did not apply.
    """
    known = {str(r) for r in region_names}
    problems = []
    for s in specs:
        for reg in s.region_priors:
            if reg not in known:
                problems.append(f"{s.name}[{reg}]")
    if problems:
        raise ValueError(
            f"per-region priors reference regions not present in the data: "
            f"{problems}. Regions in the data: {sorted(known)}")


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
    min_feature_scale: float = 1e-12      # reject scale-only features whose scaling
                                          # factor (train mean of positive values)
                                          # is below this. zero_threshold_rel is
                                          # RELATIVE, so it cannot catch a column
                                          # whose own maximum is dust; dividing by
                                          # ~1e-15 turns float noise into a
                                          # regressor. Set 0 to disable the check.
    near_constant_sd: float = 0.1         # warn when an always-on scale-only feature
                                          # has scaled sd below this: it is ~constant
                                          # at 1.0 and therefore collinear with the
                                          # region intercept (use center=1 instead)

    def __post_init__(self):
        if self.on_convergence_failure not in {"warn", "fail"}:
            raise ValueError("on_convergence_failure must be 'warn' or 'fail'")
        if self.zero_threshold_rel < 0:
            raise ValueError("zero_threshold_rel must be >= 0")
        if self.min_feature_scale < 0:
            raise ValueError("min_feature_scale must be >= 0")
        if self.near_constant_sd < 0:
            raise ValueError("near_constant_sd must be >= 0")


@dataclass
class OutputConfig:
    """Which optional files each stage writes.

    The CORE tables are always written (panel_summary, feature_scaling_stats,
    coefficient_report, fit_metrics, contribution_totals) - they are the reason
    the pipeline exists. Everything below is a RECONCILIATION / DIAGNOSTIC
    output: it exists so the reported numbers can be re-derived by hand from the
    data, and each one can be switched off to keep a run light.

    The reconciliation chain, and the file that evidences each link:

        raw feature      -> scaled feature      model_input_matrix.csv
        scaled feature   -> contribution        contribution_math.csv
        contributions    -> fitted sales        contribution_reconciliation.csv
        fitted sales     -> actual sales        actual_vs_predicted.csv
        everything       -> volume + % table    contribution_summary.csv

    period_split controls the reporting periods of contribution_summary.csv:
      "none"  one "Total" block
      "year"  calendar year
      "mat"   trailing moving-annual-total blocks counted back from the LAST
              date (MAT 1 = oldest), which is how the vendor decomposition
              in snapshots/true_output/ is cut. Always emits "Total" as well.
    """
    # ---- 01_data ----------------------------------------------------------
    model_input_matrix: bool = True     # every row exactly as the model sees it
    model_input_summary: bool = True    # per region x feature scaled-column stats
    data_plots: bool = True             # kpi_by_region.png
    # ---- 03_coefficients --------------------------------------------------
    forest_plots: bool = True
    # ---- 04_fit -----------------------------------------------------------
    actual_vs_predicted: bool = True    # row-level actual / fitted / residual
    fit_plots: bool = True
    # ---- 05_contributions -------------------------------------------------
    contribution_summary: bool = True        # vendor-style volume + % table
    contribution_timeseries: bool = True     # volume per region x date x driver
    contribution_math: bool = True           # beta x sum(x) x sd_y audit trail
    contribution_reconciliation: bool = True  # components -> fitted -> actual
    contribution_plots: bool = True
    # ---- options ----------------------------------------------------------
    period_split: str = "mat"           # "none" | "year" | "mat"
    include_raw_features: bool = True   # also dump pre-scaling feature values

    _FLAGS = ("model_input_matrix", "model_input_summary", "data_plots",
              "forest_plots", "actual_vs_predicted", "fit_plots",
              "contribution_summary", "contribution_timeseries",
              "contribution_math", "contribution_reconciliation",
              "contribution_plots")

    def __post_init__(self):
        if self.period_split not in {"none", "year", "mat"}:
            raise ValueError("period_split must be 'none', 'year' or 'mat'")

    @classmethod
    def core_only(cls, **overrides) -> "OutputConfig":
        """Every optional output off - fastest run, core tables only."""
        return cls(**{**{f: False for f in cls._FLAGS}, **overrides})

    @classmethod
    def tables_only(cls, **overrides) -> "OutputConfig":
        """All CSVs, no PNGs (Databricks /Workspace hates rapid image writes)."""
        off = {"data_plots": False, "forest_plots": False,
               "fit_plots": False, "contribution_plots": False}
        return cls(**{**off, **overrides})

    def enabled(self) -> list[str]:
        return [f for f in self._FLAGS if getattr(self, f)]


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
