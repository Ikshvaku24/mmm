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

    WHY ONE IS LOGGED AND THE OTHER IS NOT. The coefficient is built as
    beta = +/-exp(eta) with eta ~ Normal(mu, prior_sd), i.e. log(beta) is
    normal. mu and prior_sd are the mean and sd OF LOG BETA:
      - mu carries the units of log(beta), so a prior stated in beta units must
        be converted: mu = log(prior_mean). exp(mu) is then the MEDIAN of beta.
      - prior_sd is dimensionless. It is a MULTIPLICATIVE spread: exp(prior_sd)
        is a factor, not an amount. There is nothing to convert, and "the log of
        an sd" is not a meaningful quantity. 0.2 ~ +/-20%, 0.7 ~ a factor of 2,
        1.0 ~ a factor of e.

    CONVERTING A (mean, sd) PRIOR STATED IN COEFFICIENT UNITS. If you derived
    m and s in beta units (e.g. m = contribution / sum(x) / dv_scale, s = 0.2*m):
        prior_sd   = sqrt(log(1 + (s/m)**2))     ~ s/m when s/m is small
        prior_mean = m                            (m read as the MEDIAN)
    So "20% uncertainty" is prior_sd ~ 0.2, NOT 0.2 * m. Passing 0.2*m makes
    prior_sd tiny, which pins the coefficient and leaves the data no say - see
    the warning raised below when prior_sd < 0.05.
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

# Scaling vocabulary, shared by the features and the KPI.
#   centre: what is subtracted before scaling
#   scale : what the result is divided by (all computed on the TRAIN window)
VALID_CENTER = ("none", "mean")
VALID_SCALE = ("none", "sd", "mean", "mean_positive", "max")

SCALE_HELP = """  none           divide by 1 - pass the column through unchanged
  sd             divide by the standard deviation of the centred column
  mean           divide by the mean of the raw column (level variables)
  mean_positive  divide by the mean of the POSITIVE values (Meridian's media
                 scaler; zero activity stays zero)
  max            divide by max|centred column|, so the result lands in [-1, 1]"""

# Bucket = one vectorised block of coefficients in the model.
# prefix: h = hierarchical (pooled), i = independent per region, g = one global
BUCKET_ORDER = ["hpos", "hneg", "hfree",
                "ipos", "ineg", "ifree",
                "gpos", "gneg", "gfree"]
# ---------------------------------------------------------------------------
# Period cadence: one knob for every "how many periods?" number in the pipeline
# ---------------------------------------------------------------------------
VALID_CADENCE = ("auto", "weekly", "monthly")

# every parameter the region-intercept block creates, in one place, so the
# reporting switch and the model builder cannot drift apart
INTERCEPT_PARAMS = ("mu_alpha", "tau_alpha", "z_alpha", "alpha_region")


@dataclass(frozen=True)
class PeriodPlan:
    """Every period-count the pipeline needs, derived from the data cadence.

    The same model is run on two shapes of panel and each one has its own idea
    of "a year", "a sensible holdout" and "a CV fold":

        weekly    104 periods = 2 years   91 train / 13 test   MAT = 52 + 52
        monthly    24 periods = 2 years   21 train /  3 test   MAT = 12 + 12

    Hard-coding 13 and 52 works for the retailer panel and silently produces
    nonsense on the monthly one - a 13-MONTH holdout out of 24 is over half the
    data, and a 52-month MAT window does not exist. Set `cadence` once and every
    downstream number follows.
    """
    cadence: str                  # "weekly" | "monthly"
    periods_per_year: int         # 52 | 12
    unit: str                     # "weeks" | "months"
    holdout_periods: int          # RunConfig  - 13 | 3  (one quarter)
    mat_periods: int              # OutputConfig - the MAT block length
    cv_horizon: int               # CVConfig   - 13 | 3
    cv_min_train_periods: int     # CVConfig   - one year = 50% of a 2-yr panel
    cv_n_folds: int               # CVConfig   - fewer folds when periods are scarce
    cv_draws: int | None          # CVConfig   - None = leave the sampler alone
    cv_tune: int | None


PERIOD_PRESETS = {
    "weekly": PeriodPlan(
        cadence="weekly", periods_per_year=52, unit="weeks",
        holdout_periods=13,          # one quarter
        mat_periods=52,
        cv_horizon=13, cv_min_train_periods=52, cv_n_folds=5,
        cv_draws=None, cv_tune=None),   # matches the historic defaults exactly
    "monthly": PeriodPlan(
        cadence="monthly", periods_per_year=12, unit="months",
        holdout_periods=3,           # one quarter
        mat_periods=12,
        cv_horizon=3, cv_min_train_periods=12, cv_n_folds=3,
        # 24 months with horizon=3 and min_train=12 admits at most 4 folds, and
        # each fold is a full refit on 21 rows per region - cheap to sample but
        # there is little for NUTS to learn, so shorter chains are plenty.
        cv_draws=500, cv_tune=500),
}


def infer_cadence(dates) -> str:
    """'weekly' or 'monthly' from the median spacing of the observed dates."""
    uniq = pd.DatetimeIndex(np.sort(pd.DatetimeIndex(
        pd.to_datetime(pd.Series(np.asarray(dates)))).unique()))
    if len(uniq) < 2:
        return "weekly"
    gap = float(np.median(np.diff(uniq.to_numpy())
                          .astype("timedelta64[D]").astype(float)))
    if gap <= 0:
        return "weekly"
    per_year = 365.25 / gap
    if per_year >= 26:
        return "weekly"
    if per_year < 3:
        warnings.warn(
            f"date spacing of {gap:.0f} days implies {per_year:.1f} periods per "
            "year, which is neither weekly nor monthly. Treating it as monthly; "
            "set cadence= explicitly if that is wrong.")
    return "monthly"


def resolve_period_plan(cadence: str = "auto", dates=None) -> PeriodPlan:
    """cadence + (optionally) the data -> the full set of period counts."""
    c = str(cadence or "auto").strip().lower()
    if c not in VALID_CADENCE:
        raise ValueError(f"cadence must be one of {VALID_CADENCE}, got {cadence!r}")
    if c == "auto":
        if dates is None:
            raise ValueError(
                "cadence='auto' needs the dates to infer from. Pass dates=, or "
                "set cadence='weekly'/'monthly' explicitly.")
        c = infer_cadence(dates)
    return PERIOD_PRESETS[c]


VALID_SD_BASIS = ("log", "relative", "absolute")
VALID_MEAN_BASIS = ("median", "mean")

SD_BASIS_HELP = """  log        the number IS the sd parameter the model samples with. For a
             signed feature that is the LOG-scale sigma (0.7 ~ a factor of 2);
             for a free feature it is a plain sd in coefficient units.
  relative   the number is a FRACTION: 0.2 = "+/-20%". Converted for you --
             signed: sigma = sqrt(log(1 + rel^2));  free: sd = rel * |mean|.
  absolute   the number is in COEFFICIENT units. Converted for you --
             signed: sigma = sqrt(log(1 + (sd/mean)^2));  free: used as-is."""

MEAN_BASIS_HELP = """  median     prior_mean is the MEDIAN coefficient  -> mu = log(m)
  mean       prior_mean is the MEAN coefficient    -> mu = log(m) - sigma^2/2"""


def lognormal_sigma(rel_sd: float) -> float:
    """Log-scale sigma that gives a coefficient of variation of `rel_sd`.

        sigma = sqrt(log(1 + (s/m)^2))

    This is the exact conversion, not the sigma ~ s/m approximation: they agree
    to 2% at rel_sd=0.2 but diverge fast (at 1.0 the approximation is 20% off).
    """
    r = float(rel_sd)
    if not np.isfinite(r) or r <= 0:
        raise ValueError(f"relative sd must be finite and > 0, got {rel_sd!r}")
    return float(np.sqrt(np.log(1.0 + r * r)))


def resolve_prior_params(prior_mean: float, prior_sd: float, sign: str,
                         sd_basis: str = "log", mean_basis: str = "median",
                         label: str = "") -> tuple[float, float]:
    """(prior_mean, prior_sd) as WRITTEN -> (mu, sigma) as SAMPLED.

    A signed feature is built as beta = +/-exp(Normal(mu, sigma)), so both
    numbers change meaning on the way in. A free feature is Normal(mu, sigma)
    directly, so mu passes through untouched and only the sd basis applies.

    THE MEDIAN/MEAN CHOICE. exp(Normal(mu, sigma)) is right-skewed:

        median = exp(mu)              mean = exp(mu + sigma^2/2)

    so "prior_mean = 0.05" is ambiguous until you say which one you meant, and
    the two differ by exp(-sigma^2/2): 2% at sigma=0.2, 12% at 0.5, 33% at 0.9.
    Neither reading is wrong; `mean_basis` makes the choice explicit. Default is
    "median" because that is what this codebase has always done and what the
    contribution reconciliation was validated against.
    """
    m, s = float(prior_mean), float(prior_sd)
    sd_basis = str(sd_basis).strip().lower()
    mean_basis = str(mean_basis).strip().lower()
    tag = f"{label}: " if label else ""
    if sd_basis not in VALID_SD_BASIS:
        raise ValueError(f"{tag}prior_sd_basis must be one of {VALID_SD_BASIS}, "
                         f"got {sd_basis!r}. " + SD_BASIS_HELP)
    if mean_basis not in VALID_MEAN_BASIS:
        raise ValueError(f"{tag}prior_mean_basis must be one of "
                         f"{VALID_MEAN_BASIS}, got {mean_basis!r}. "
                         + MEAN_BASIS_HELP)

    if sign == "free":
        # Normal(mu, sigma): no exponential, so mean == median and there is no
        # log scale. "relative" still means something useful (a sd expressed as
        # a fraction of the location), "absolute" is the identity.
        if sd_basis == "relative":
            sigma = s * abs(m)
            if sigma <= 0:
                raise ValueError(
                    f"{tag}prior_sd_basis='relative' needs a non-zero "
                    f"prior_mean to be a fraction OF (got {m}). Use "
                    "prior_sd_basis='absolute' for a free feature centred on 0.")
        else:
            sigma = s
        return m, float(sigma)

    if m <= 0:
        raise ValueError(f"{tag}sign-constrained features need prior_mean > 0")
    if sd_basis == "log":
        sigma = s
    elif sd_basis == "relative":
        sigma = lognormal_sigma(s)
    else:                                    # absolute, in coefficient units
        sigma = lognormal_sigma(s / m)
    mu = np.log(m)
    if mean_basis == "mean":
        mu = mu - 0.5 * sigma * sigma
    return float(mu), float(sigma)


def lognormal_moments(mu: float, sigma: float, sgn: float = 1.0) -> dict:
    """Back-transform (mu, sigma) to what the COEFFICIENT prior actually says."""
    med = np.exp(mu)
    mean = np.exp(mu + 0.5 * sigma * sigma)
    sd = mean * np.sqrt(max(np.exp(sigma * sigma) - 1.0, 0.0))
    lo, hi = np.exp(mu - 1.6448536 * sigma), np.exp(mu + 1.6448536 * sigma)
    if sgn < 0:
        med, mean, lo, hi = -med, -mean, -hi, -lo
    return {"median": float(med), "mean": float(mean), "sd": float(sd),
            "q05": float(lo), "q95": float(hi)}


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
    center: bool = False               # DEPRECATED - use `center_mode` instead.
                                       # Kept only so prior files written before
                                       # center_mode existed still load: a 1 here
                                       # maps to center_mode="mean" and warns.
                                       # Two columns meaning the same thing is
                                       # how `center=1` silently became a no-op
                                       # in v7, when an explicit center_mode
                                       # column won and overwrote it.
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
    center_mode: str | None = None     # "none" | "mean". Explicit override of the
                                       # `center` flag. None = derive from `center`
                                       # (and from sign="free", always centred).
    prior_sd_basis: str = "log"        # how to READ prior_sd / regional_sd:
                                       # "log" (as-is), "relative" (a fraction,
                                       # 0.2 = +/-20%), "absolute" (coefficient
                                       # units). See SD_BASIS_HELP.
    prior_mean_basis: str = "median"   # is prior_mean the MEDIAN or the MEAN of
                                       # the coefficient? Only bites for signed
                                       # features. See MEAN_BASIS_HELP.
    # ---- derived, filled in by resolved(); never set these by hand --------
    mu_log: float | None = None        # the location the model actually samples
    sigma_log: float | None = None     # the sd the model actually samples
    regional_sd_log: float | None = None   # ditto for the cross-region tau
    scale_mode: str | None = None      # "none" | "sd" | "mean" | "mean_positive" |
                                       # "max". None = the legacy default for this
                                       # feature: "sd" when centred, else
                                       # "mean_positive".
                                       # USE "none"/"none" when the feature arrives
                                       # already transformed AND already on the
                                       # scale your priors were derived on - any
                                       # further scaling silently rescales every
                                       # prior by the same factor.

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
        # `center_mode` is the only setting. `center` is accepted for old files
        # and translated here, with a warning when both are present, because a
        # silent disagreement between them is exactly the v7 failure: an
        # explicit center_mode won and the center=1 the analyst had set did
        # nothing at all.
        legacy = bool(s.center)
        if s.center_mode is None:
            s.center_mode = "mean" if (legacy or s.sign == "free") else "none"
            if legacy:
                warnings.warn(
                    f"{s.name}: `center` is deprecated - it has been read as "
                    "center_mode='mean'. Replace the `center` column with "
                    "`center_mode` in the prior file; keeping both is how a "
                    "center=1 silently becomes a no-op.")
        else:
            s.center_mode = str(s.center_mode).strip().lower()
            if legacy and s.center_mode != "mean":
                warnings.warn(
                    f"{s.name}: the prior file sets BOTH center=1 and "
                    f"center_mode={s.center_mode!r}. center_mode wins, so "
                    "center=1 does nothing here. Delete the `center` column.")
        s.center = s.center_mode == "mean"     # keep the alias consistent
        if s.center_mode not in VALID_CENTER:
            raise ValueError(
                f"{s.name}: center_mode must be one of {VALID_CENTER}, "
                f"got {s.center_mode!r}")
        if s.scale_mode is None:
            s.scale_mode = "sd" if s.center_mode == "mean" else "mean_positive"
        else:
            s.scale_mode = str(s.scale_mode).strip().lower()
        if s.scale_mode not in VALID_SCALE:
            raise ValueError(
                f"{s.name}: scale_mode must be one of {VALID_SCALE}, "
                f"got {s.scale_mode!r}. " + SCALE_HELP)
        # keep the legacy flag consistent with the explicit mode - everything
        # downstream (contribution reference, support flags) keys off it
        s.center = s.center_mode == "mean"
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
        # For a SIGNED feature beta = +/-exp(Normal(log(prior_mean), prior_sd)),
        # so prior_sd lives on the LOG scale: 0.7 ~ a factor of 2, 0.2 ~ +/-20%,
        # 0.01 ~ +/-1%. A common mistake is to set it as a FRACTION OF THE MEAN
        # ("20% of 0.015" -> 0.003), which pins the coefficient to three decimal
        # places and leaves the data no say at all: the posterior comes back as
        # the prior, the fit degrades, and the contributions are whatever the
        # priors implied. Warn rather than raise - a deliberately fixed
        # coefficient is a legitimate (if unusual) choice.
        # Only meaningful for prior_sd_basis="log", where the written number IS
        # the sampled sigma. Under "relative"/"absolute" the written number is
        # converted first, so this test would both mis-describe it ("is on the
        # LOG scale") and fire a second time alongside the post-conversion check
        # further down - two warnings for one condition, one of them wrong.
        if (s.sign != "free" and s.prior_sd < 0.05
                and str(s.prior_sd_basis).strip().lower() == "log"):
            warnings.warn(
                f"{s.name}: prior_sd={s.prior_sd:.4g} is on the LOG scale, so "
                f"this pins the coefficient to about +/-{s.prior_sd:.1%} of "
                f"{s.prior_mean:.4g} - the data cannot move it. If you meant "
                "'20% uncertainty', use prior_sd=0.2 with "
                "prior_sd_basis='relative', not 0.2*prior_mean.")
        if s.pooling == "hierarchical" and 0 < s.regional_sd < 0.02:
            warnings.warn(
                f"{s.name}: regional_sd={s.regional_sd:.4g} allows essentially "
                "no cross-region variation, so hierarchical pooling collapses to "
                "one shared coefficient. That is only right if every region truly "
                "responds identically on the SCALED axis - which it cannot, if "
                "the regions were scaled by one global number.")
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

        # ---- convert the written prior into what the model samples --------
        # Done HERE, once, rather than in model.py, so the numbers can be
        # reported and tested without PyMC installed.
        s.prior_sd_basis = str(s.prior_sd_basis).strip().lower()
        s.prior_mean_basis = str(s.prior_mean_basis).strip().lower()
        s.mu_log, s.sigma_log = resolve_prior_params(
            s.prior_mean, s.prior_sd, s.sign,
            s.prior_sd_basis, s.prior_mean_basis, label=s.name)
        # tau lives on the same axis as sigma, so it takes the same basis. Its
        # "mean" has no meaning (it is a spread, not a location), so the
        # mean_basis never applies to it.
        if s.regional_sd > 0:
            _, s.regional_sd_log = resolve_prior_params(
                s.prior_mean, s.regional_sd, s.sign,
                s.prior_sd_basis, "median", label=f"{s.name} regional_sd")
        else:
            s.regional_sd_log = float(s.regional_sd)
        # the < 0.05 warning above tests the WRITTEN number; under a converted
        # basis the sampled sigma is what matters, so re-check it
        # Under a NON-log basis a small number is not a units mistake - writing
        # 0.02 with prior_sd_basis='relative' says "+/-2%", which is exactly
        # what the author meant. So this is NOT the "did you mean 20%?" warning;
        # it is a note that the coefficient is being FIXED rather than
        # estimated, which changes how the contribution must be described.
        if s.sign != "free" and s.prior_sd_basis != "log" and s.sigma_log < 0.05:
            warnings.warn(
                f"{s.name}: prior_sd={s.prior_sd:.4g} with "
                f"prior_sd_basis={s.prior_sd_basis!r} is a deliberate "
                f"+/-{s.prior_sd:.1%} band. The coefficient is therefore FIXED "
                "by the prior, not estimated: its posterior will be its prior "
                "and its contribution is an assumption, not a finding. Valid "
                "choice - recorded so the report says so.")
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

    # -- the SAMPLED parameters (post basis conversion) --------------------
    # model.py uses these, never the raw prior_mean/prior_sd, so the median-vs-
    # mean and percentage conversions happen in exactly one place.
    def sampled_params_for(self, region: str) -> tuple[float, float]:
        """(mu, sigma) for this region, honouring any per-region override."""
        return resolve_prior_params(
            self.prior_mean_for(region), self.prior_sd_for(region), self.sign,
            self.prior_sd_basis, self.prior_mean_basis,
            label=f"{self.name}[{region}]")

    def mu_log_for(self, region: str) -> float:
        return self.sampled_params_for(region)[0]

    def sigma_log_for(self, region: str) -> float:
        return self.sampled_params_for(region)[1]


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
      center_mode  "none" | "mean". Use "mean" for always-on LEVEL variables
               (distribution, price index, ACV) so they are centred rather than
               only scaled. Pair it with contribution_reference="zero" or the
               reported contribution collapses to ~0.
               (A legacy `center` 0/1 column is still read and mapped to this,
               with a deprecation warning. Do not write both.)
      pooling  "hierarchical" | "independent" | "global". Defaults to
               hierarchical/global from the `hierarchical` column.
      baseline (0/1) 1 to fold the feature into the baseline instead of
               reporting it as an incremental effect.
      prior_sd_basis    "log" (default) | "relative" | "absolute". How to read
               global_prior_sd AND regional_sd_prior. Set "relative" and write
               0.2 to mean "+/-20%" - the sqrt(log(1+r^2)) conversion is then
               done for you. See SD_BASIS_HELP.
      prior_mean_basis  "median" (default) | "mean". Whether global_prior_mean
               is the median or the mean of the coefficient; "mean" subtracts
               sigma^2/2 from mu. Signed features only. See MEAN_BASIS_HELP.

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
            center_mode=_cell(r, "center_mode"),
            scale_mode=_cell(r, "scale_mode"),
            prior_sd_basis=(_cell(r, "prior_sd_basis") or "log"),
            prior_mean_basis=(_cell(r, "prior_mean_basis") or "median"),
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
    include_intercept: bool = True      # region intercept alpha_g. False removes
                                        # mu_alpha / tau_alpha / z_alpha /
                                        # alpha_region from the model entirely, so
                                        # no ESTIMATED term can absorb sales the
                                        # drivers should be explaining (the vendor
                                        # decomposition works this way).
                                        # The level does NOT disappear: with
                                        # dv_center="mean" the inverse transform
                                        # still adds the training mean back, so
                                        # __baseline_core__ becomes that FIXED
                                        # number, with zero posterior width,
                                        # rather than a free parameter.
                                        # Only sane with dv_center="mean": with
                                        # dv_center="none" the KPI keeps its level
                                        # and nothing is left to carry it.
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
    dv_center: str = "mean"     # "mean" | "none" - what is subtracted from the KPI.
                                # "none" leaves the level in the data, so the
                                # region intercept must carry it. When it is
                                # "none" the stored centre is 0, which keeps the
                                # inverse transform (fitted = y_scaled * scale +
                                # centre) exactly right - do NOT strip the
                                # centring by editing the transform alone, or
                                # every fitted value is inflated by the mean.
    dv_scale: str = "sd"        # "none" | "sd" | "mean" | "mean_positive" | "max"
                                # See SCALE_HELP. The KPI scale is the unit your
                                # coefficients (and therefore your PRIORS) live
                                # in: a coefficient means "moves the KPI by beta
                                # x dv_scale". Change it and every prior mean
                                # must be rescaled by the same factor.
    dv_scale_scope: str = "region"   # "region" | "global". "region" gives each
                                # region its own scale (coefficients are then
                                # comparable across regions in relative terms).
                                # "global" divides every region by ONE number, so
                                # a small region's scaled KPI is small and its
                                # coefficients must shrink to match - only use it
                                # when your priors were derived on that same
                                # single scale.
    cadence: str = "auto"       # "auto" | "weekly" | "monthly". Sets every
                                # period count downstream. "auto" infers it from
                                # the observed date spacing in prepare_data.
    holdout_periods: int | None = 0     # last N dates held out per region for OOS
                                # metrics. None = take it from the cadence preset
                                # (13 weeks / 3 months - one quarter either way).
                                # The default stays 0 so existing callers are
                                # untouched.
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
        if self.dv_center not in VALID_CENTER:
            raise ValueError(f"dv_center must be one of {VALID_CENTER}")
        if self.dv_scale not in VALID_SCALE:
            raise ValueError(f"dv_scale must be one of {VALID_SCALE}. " + SCALE_HELP)
        if self.dv_scale_scope not in {"region", "global"}:
            raise ValueError("dv_scale_scope must be 'region' or 'global'")
        self.cadence = str(self.cadence or "auto").strip().lower()
        if self.cadence not in VALID_CADENCE:
            raise ValueError(f"cadence must be one of {VALID_CADENCE}, "
                             f"got {self.cadence!r}")
        if self.holdout_periods is not None and self.holdout_periods < 0:
            raise ValueError("holdout_periods must be >= 0 (or None for the "
                             "cadence preset)")
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
      "week"  one block per date - the week-by-week volume and % report. This
              multiplies the file's row count by the number of dates, so use
              it when you need weekly detail in the vendor layout (pillar
              subtotals, Residual, Grand Total = actual). For a plain weekly
              driver series without the roll-up rows, contribution_timeseries
              is the smaller file.
      "year"  calendar year
      "mat"   two moving-annual-total blocks anchored on the LAST date - the
              cut the vendor decomposition in snapshots/true_output/ uses:
              MAT 2 = the most recent 52 periods, MAT 1 = the 52 before those.
              On the real panel that is MAT 1 = 2024, MAT 2 = 2025. Anything
              older than the last 104 periods lands in a separate "Pre-MAT"
              block; a panel shorter than 104 is split in half instead.
    Every setting also emits a "Total" block covering the whole window.
    """
    # ---- 01_data ----------------------------------------------------------
    model_input_matrix: bool = True     # every row exactly as the model sees it
    model_input_summary: bool = True    # per region x feature scaled-column stats
    data_plots: bool = True             # kpi_by_region.png
    collinearity: bool = True           # collinearity_summary/vif/pairs.csv -
                                        # VIF, Belsley condition number and
                                        # correlated pairs, measured on the
                                        # MODEL'S design matrix (intercept +
                                        # Fourier + trend + features), per region
    prior_summary: bool = True          # what each written prior means as a
                                        # coefficient distribution (mu/sigma
                                        # actually sampled + implied median,
                                        # mean, sd and 90% interval)
    # ---- 02_convergence ---------------------------------------------------
    contraction_plot: bool = True       # prior_posterior_contraction.png
    prior_posterior_plots: bool = True  # prior_posterior/<param>.png - the
                                        # three-curve prior / likelihood /
                                        # posterior chart, one per parameter
    prior_posterior_max: int = 60       # cap on how many of those to draw,
                                        # taken in order of |mean shift|
    report_intercept: bool = True       # include the region-intercept parameters
                                        # (mu_alpha, tau_alpha, z_alpha,
                                        # alpha_region) in the prior/posterior
                                        # CONTRACTION report and its per-parameter
                                        # charts. False drops those rows when the
                                        # intercept is a nuisance level you do not
                                        # present. It never touches the convergence
                                        # tables (R-hat/ESS/divergences are always
                                        # reported for every parameter) and never
                                        # touches the decomposition, so
                                        # contributions still reconcile.
    # ---- 03_coefficients --------------------------------------------------
    forest_plots: bool = True
    # ---- 04_fit -----------------------------------------------------------
    actual_vs_predicted: bool = True    # row-level actual / fitted / residual
    assumption_checks: bool = True      # assumption_checks.csv +
                                        # posterior_correlation.csv +
                                        # assumptions_report.md - linearity,
                                        # homoscedasticity, autocorrelation,
                                        # residual tails, influence, and which
                                        # coefficient pairs are trading off
    fit_plots: bool = True
    # ---- 05_contributions -------------------------------------------------
    contribution_summary: bool = True        # vendor-style volume + % table
    contribution_timeseries: bool = True     # volume per region x date x driver
    contribution_math: bool = True           # beta x sum(x) x sd_y audit trail
    contribution_reconciliation: bool = True  # components -> fitted -> actual
    benchmark_comparison: bool = True    # benchmark_comparison.xlsx (or .csv):
                                        # one row per region x feature with the
                                        # run's numbers already laid out, ONE
                                        # empty column to paste a benchmark
                                        # contribution into, and live formulas
                                        # for %diff, the ratio, delta and the
                                        # corrected global_prior_mean. Needs
                                        # contribution_math
    contribution_plots: bool = True
    # ---- options ----------------------------------------------------------
    period_split: str = "mat"           # "none" | "week" | "year" | "mat"
    cadence: str = "auto"               # "auto" | "weekly" | "monthly". Sets the
                                        # MAT block length: 52 periods weekly,
                                        # 12 monthly. "auto" infers it from the
                                        # observed date spacing.
    include_raw_features: bool = True   # also dump pre-scaling feature values
    rope_scaled: float = 0.01           # region of practical equivalence, on the
                                        # SCALED coefficient axis (KPI sd per
                                        # feature unit). coefficient_report gets
                                        # prob_negligible = P(|beta| <= this),
                                        # which is the only non-vacuous
                                        # "significance" for a sign-constrained
                                        # feature, whose p_value is 0 by
                                        # construction. 0.01 = "moves sales by
                                        # less than 1% of a region's sd".
                                        # Set 0 to skip the calculation.
    # ---- figures ----------------------------------------------------------
    # Applied to EVERY chart the run writes, via plotting.set_figure_defaults.
    fig_dpi: int = 160                  # 160 stays legible pasted into a deck
    fig_scale: float = 1.4              # multiplies every figsize; raise for
                                        # projection, lower to fit more on a page

    _FLAGS = ("model_input_matrix", "model_input_summary", "data_plots",
              "collinearity", "prior_summary", "contraction_plot",
              "prior_posterior_plots", "forest_plots", "actual_vs_predicted",
              "assumption_checks",
              "fit_plots", "contribution_summary", "contribution_timeseries",
              "contribution_math", "contribution_reconciliation",
              "benchmark_comparison", "contribution_plots")

    def __post_init__(self):
        if self.period_split not in {"none", "week", "year", "mat"}:
            raise ValueError(
                "period_split must be 'none', 'week', 'year' or 'mat'")
        self.cadence = str(self.cadence or "auto").strip().lower()
        if self.cadence not in VALID_CADENCE:
            raise ValueError(f"cadence must be one of {VALID_CADENCE}, "
                             f"got {self.cadence!r}")
        if self.rope_scaled < 0:
            raise ValueError("rope_scaled must be >= 0")
        if self.prior_posterior_max < 0:
            raise ValueError("prior_posterior_max must be >= 0")
        if self.fig_dpi < 50:
            raise ValueError("fig_dpi must be >= 50")
        if self.fig_scale <= 0:
            raise ValueError("fig_scale must be > 0")

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
class AssumptionConfig:
    """Every threshold the assumption and collinearity checks use.

    They live here rather than as module constants so a modeller can widen or
    narrow any of them from `config.yaml` without editing code - e.g. set
    `pair_warn: 0.0` to dump the FULL correlation matrix instead of only the
    flagged pairs.

    The defaults are the classical econometric ones, deliberately much tighter
    than Meridian's (which errors only at VIF 1000 / correlation 0.999, on the
    view that priors regularise everything short of numerical degeneracy). See
    docs/MERIDIAN_ASSUMPTIONS.md.
    """
    # ---- collinearity (pre-fit, on the model's design matrix) -------------
    vif_warn: float = 5.0            # textbook "moderate"
    vif_bad: float = 10.0            # textbook "severe"
    cond_warn: float = 10.0          # Belsley condition number
    cond_bad: float = 30.0
    pair_warn: float = 0.8           # |corr| between design columns to report.
                                     # Set 0 to list EVERY pair
    pair_bad: float = 0.95
    vif_top_k: int = 3               # how many culprits to name per feature -
                                     # "this is explained by A, B and C"
    corr_heatmap: bool = True        # 01_data/collinearity_heatmap_<region>.png
    heatmap_max_features: int = 40   # above this a heatmap is unreadable; the
                                     # highest-VIF features are kept
    # ---- identifiability (post-fit) ---------------------------------------
    post_corr_warn: float = 0.7      # |corr| between coefficient DRAWS
    post_corr_bad: float = 0.9
    # ---- residual assumptions ---------------------------------------------
    dw_lo: float = 1.5               # Durbin-Watson acceptable band
    dw_hi: float = 2.5
    linearity_max_corr: float = 0.2  # |corr(residual, fitted)|
    hetero_ratio_max: float = 1.5    # sd(resid) top third / bottom third
    acf_max: float = 0.3             # |autocorrelation| at lags 2/4/13
    skew_max: float = 1.0
    kurtosis_max: float = 1.0        # excess kurtosis, under a Normal likelihood
    influence_sd: float = 3.0        # |standardised residual| counted as extreme
    # ---- structural checks (adopted from Meridian) ------------------------
    exogeneity_max_lags: int = 4     # cross-correlate feature vs residual over
                                     # +/- this many periods. Lag 0 is ~0 BY
                                     # CONSTRUCTION for an included regressor,
                                     # so the leads and lags are the real test
    exogeneity_warn: float = 0.2     # |cross-correlation| to flag. Raised to
                                     # 2/sqrt(n) automatically on short panels
    confound_warn: float = 0.1       # |corr(treatment, control)| - Meridian's
                                     # PotentialBiasCheck bar. Also floored at
                                     # 2/sqrt(n)
    ppp_fail: float = 0.05           # aggregate posterior predictive p-value
    neg_baseline_review: float = 0.2  # P(baseline < 0)
    neg_baseline_fail: float = 0.8

    def __post_init__(self):
        for name in ("vif_warn", "vif_bad", "cond_warn", "cond_bad",
                     "hetero_ratio_max", "influence_sd"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        for name in ("pair_warn", "pair_bad", "post_corr_warn", "post_corr_bad",
                     "linearity_max_corr", "acf_max", "exogeneity_warn",
                     "confound_warn", "ppp_fail", "neg_baseline_review",
                     "neg_baseline_fail"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1, got {v}")
        if self.dw_lo >= self.dw_hi:
            raise ValueError("dw_lo must be below dw_hi")
        if self.vif_top_k < 0 or self.heatmap_max_features < 2:
            raise ValueError("vif_top_k >= 0 and heatmap_max_features >= 2")
        if self.exogeneity_max_lags < 0:
            raise ValueError("exogeneity_max_lags must be >= 0")


@dataclass
class CVConfig:
    """Expanding-window (rolling-origin) cross-validation settings.

    Fold k trains on everything before its test window and predicts the next
    `horizon` periods; origins step back through the series so accuracy and
    coefficient stability are measured across several windows, not one.

    Every count defaults to None, meaning "take it from the cadence preset".
    For a WEEKLY panel the preset reproduces the historic hard-coded defaults
    exactly (13 / 5 / 52), so nothing moves; a MONTHLY panel gets 3 / 3 / 12
    plus shorter chains, because 24 months cannot support a 13-period horizon
    or a 52-period minimum training window.
    """
    enabled: bool = False              # run CV at all. Off by default because
                                       # every fold is a FULL refit, so a 5-fold
                                       # CV costs ~5x the headline run. Turn it
                                       # on once the single fit looks sane.
    cadence: str = "auto"              # "auto" | "weekly" | "monthly"
    horizon: int | None = None         # test periods per fold  (13 wk / 3 mo)
    n_folds: int | None = None         # (5 wk / 3 mo)
    step: int | None = None            # spacing between origins (default: horizon)
    min_train_periods: int | None = None   # (52 wk / 12 mo = one year)
    draws: int | None = None           # override sampler draws for CV speed
    tune: int | None = None
    make_plots: bool = True

    def __post_init__(self):
        self.cadence = str(self.cadence or "auto").strip().lower()
        if self.cadence not in VALID_CADENCE:
            raise ValueError(f"cadence must be one of {VALID_CADENCE}, "
                             f"got {self.cadence!r}")

    def resolved(self, plan: "PeriodPlan") -> "CVConfig":
        """Fill every unset count from the cadence preset. Explicit wins."""
        from dataclasses import replace as _replace
        return _replace(
            self,
            cadence=plan.cadence,
            horizon=plan.cv_horizon if self.horizon is None else self.horizon,
            n_folds=plan.cv_n_folds if self.n_folds is None else self.n_folds,
            min_train_periods=(plan.cv_min_train_periods
                               if self.min_train_periods is None
                               else self.min_train_periods),
            draws=plan.cv_draws if self.draws is None else self.draws,
            tune=plan.cv_tune if self.tune is None else self.tune,
        )
