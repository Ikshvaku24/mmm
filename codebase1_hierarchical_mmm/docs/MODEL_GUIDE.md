# The core model — what `model.py` builds, term by term

**For:** modellers who run codebase 1 and want to know what the model actually
is: the equation, every parameter in it, how the three pooling modes
(`hierarchical`, `global`, `independent`) differ, and what happens around the
model — the data it sees, the priors it samples, the sampler that fits it, and
how the draws become the contributions in the report.

**Code:**

- `mmm/modelling/model.py` — the model (`build_model`, `_bucket_betas`);
- `mmm/modelling/fit.py` — the sampler;
- `mmm/core/config.py` — `FeatureSpec`, `ModelConfig`, `SamplerConfig`, and the
  prior conversion (`resolve_prior_params`);
- `mmm/data/data_prep.py` — the scaled data the model sees;
- `mmm/reporting/outputs.py` — `compute_decomposition`, draws to contributions.

**Not in here:** which lever to pull (`TUNING_GUIDE.md`), the order to build a
model in (`METHODOLOGY.md`), every check (`CHECKS_GUIDE.md`), every output
column (`OUTPUTS_GUIDE.md`), the prior CSV (`FEATURE_PRIOR_GUIDE.md`).

Numbers marked *illustrative* come from small worked examples, not client data.

---

## Contents

1. [The equation](#1-the-equation)
2. [What the model sees: the scaled axis](#2-what-the-model-sees-the-scaled-axis)
3. [The baseline terms](#3-the-baseline-terms)
4. [A feature's coefficient and its sign](#4-a-features-coefficient-and-its-sign)
5. [Pooling: hierarchical, global, independent](#5-pooling-hierarchical-global-independent)
6. [Buckets: how the code builds it](#6-buckets-how-the-code-builds-it)
7. [Priors: what you write vs what is sampled](#7-priors-what-you-write-vs-what-is-sampled)
8. [Noise and the likelihood](#8-noise-and-the-likelihood)
9. [Fitting: the NUTS sampler and its settings](#9-fitting-the-nuts-sampler-and-its-settings)
10. [From draws to the report](#10-from-draws-to-the-report)
11. [Choosing a pooling mode](#11-choosing-a-pooling-mode)
12. [Compared with Meridian](#12-compared-with-meridian)
13. [What is learned, and what you fix](#13-what-is-learned-and-what-you-fix)

[Glossary](#glossary)

---

## 1. The equation

One joint model over **all regions at once** — not a loop that fits each region
separately, as `production_code.py` did. For region `g` and period `t`, on the
scaled axis (§2):

```
y[g,t] =  α_g                          region intercept          (optional: include_intercept)
        + Σ_k γ_k · fourier_k(t)       seasonality, shared       (optional: fourier_order > 0)
        + δ_g · t                      trend, one slope a region (optional: include_trend)
        + Σ_j β[g,j] · x[g,t,j]        every feature             (pooling and sign per feature)
        + noise

noise ~ Normal(0, σ_g)                 or Student-t (likelihood: student_t)
```

Four things to read off it:

1. **It is a linear regression.** Adstock, saturation and lags are done
   upstream, in the datacube. The model takes `x` as it is (that is what
   codebase 2 changes).
2. **Every coefficient can differ by region** — `α_g`, `δ_g`, `β[g,j]`, `σ_g`.
   How much they are allowed to differ is the pooling choice (§5).
3. **Only the training window is fitted.** Holdout rows are predicted, never
   used to fit.
4. **Additive.** A feature's contribution is `β[g,j] · x[g,t,j]`, so the
   decomposition adds up to the fitted value by construction.

---

## 2. What the model sees: the scaled axis

The model never sees the raw KPI. `data_prep.py` transforms it per region:

```
y[g,t] = (sales[g,t] − centre_g) / scale_g
```

- `run.dv_center`: `mean` (default) subtracts the region's training mean, or
  `none`.
- `run.dv_scale`: `sd` (default), `mean`, `mean_positive`, `max` or `none`.
- `run.dv_scale_scope`: one scale per `region` (default), or one `global`
  number.

Features are transformed the same way, per feature:

- `center_mode`: `none` (default) or `mean`.
- `scale_mode`: `none` (default), `sd`, `mean`, `mean_positive` or `max`.

With the defaults (`none` / `none`), **a feature enters in its raw datacube
unit.**

**What a coefficient means.** On this axis, β is:

> KPI `scale_g` units per feature unit

so `β = 0.002` with `dv_scale: mean` means "one more unit of x moves sales by
0.2% of the region's mean". **Every prior is written in these units** —
`TUNING_GUIDE.md` §4 is the rule ("the scale is the unit of your priors").

**Why scale at all.** It lets one set of priors (intercept, seasonality, noise)
work for every region, whatever its size, and keeps the sampler on numbers of
order 1.

---

## 3. The baseline terms

These carry the level, the seasonal shape and the drift — "what would have
happened anyway".

### 3.1 Region intercept `α_g` — always hierarchical

```
μ_α ~ Normal(0, alpha_prior_sd)        population intercept         (default 0.5)
τ_α ~ HalfNormal(alpha_regional_sd)    how much regions differ      (default 0.5)
z_g ~ Normal(0, 1)
α_g = μ_α + τ_α · z_g                  each region's intercept
```

- **Parameters in the trace:** `mu_alpha`, `tau_alpha`, `z_alpha`,
  `alpha_region`.
- **Reading it:** with `dv_center: mean` the KPI is already centred, so `α_g`
  is the region's level *relative to* its training mean; it is usually close to
  0. A large `mu_alpha` means the intercept is claiming sales the drivers should
  explain (v7: 0.911 on a mean-1.0 KPI).
- **`include_intercept: false`** removes the block entirely. With
  `dv_center: none` that is a **decomposition versus zero** — the vendor
  convention, the team's config and the Secondary type: the uncentred always-on
  level variables carry the level of sales. With `dv_center: mean` the training
  mean becomes a fixed baseline instead (TUNING_GUIDE §2.2).
- **`alpha_prior_sd: 0.05`** keeps it but pins it near 0 (TUNING_GUIDE §2.1).

### 3.2 Seasonality — Fourier terms, shared by every region

```
fourier_k(t) = sin(2πk·d/365.25), cos(2πk·d/365.25),   k = 1 … fourier_order
γ_k ~ Normal(0, 0.3)
```

`d` is the number of days since the first date. One set of `γ` (`beta_fourier`)
is shared by all regions: **every region gets the same seasonal shape**, scaled
by its own KPI scale.

- `fourier_order: 1` is a single smooth annual wave; each extra order adds a
  sharper harmonic (2 parameters each).
- Rule of thumb: at least 6 training periods per harmonic (the
  `seasonality_overfit_risk` warning).
- Off by default (`fourier_order: 0`).

### 3.3 Trend — one slope per region, pooled

```
μ_δ ~ Normal(0, 0.2),   τ_δ ~ HalfNormal(0.2),   z_g ~ Normal(0, 1)
δ_g = μ_δ + τ_δ · z_g                      (beta_trend_region)
t   = days since the first date ÷ days in the training window      0 … 1 in training
```

`δ_g` is the change in the scaled KPI over the whole training window.

> **The holdout extrapolates.** `t` runs past 1 in the holdout, so a trend that
> fitted a decline keeps declining. This is part of why holdout prediction has
> drifted on this project (CHECKS_GUIDE §7). Off by default
> (`include_trend: false`).

---

## 4. A feature's coefficient and its sign

Each feature has a `sign_constraint`, and the sign decides how its coefficient
is **built**, not just what it is allowed to be.

| `sign_constraint` | Built as | Distribution of β | Can cross 0? |
|---|---|---|---|
| `positive` | β = +exp(η), η ~ Normal | log-normal, right-skewed | Never |
| `negative` | β = −exp(η), η ~ Normal | log-normal, mirrored | Never |
| `free` | β = η, η ~ Normal | normal, symmetric | Yes |

**Why `exp`.** It makes the sign structural: no draw can ever have the wrong
sign. The alternative — a Normal truncated at zero — piles probability against
the boundary and gives the sampler a hard wall.

What follows from it:

- **η lives on the log scale.** A prior sd of 0.2 on η is about ±20%, *of the
  coefficient's own size* — not ±0.2 in coefficient units (§7).
- **The prior mean is a median.** For log-normal β, `exp(μ)` is the median, and
  the mean is higher by `exp(σ²/2)` (`prior_mean_basis` chooses; §7).
- **`p_value` is vacuous** for signed features (always 0 — CHECKS_GUIDE §6.3).
- **Medians do not add.** The skew is why the reconciliation carries a median
  gap (`cases/002`).

---

## 5. Pooling: hierarchical, global, independent

Every feature chooses **how its coefficient is shared across regions**, through
the `pooling` column of the prior file. This is the single most important
modelling choice after the variables themselves.

The three modes, for a signed feature (a free one is the same without `exp`):

| Mode | Coefficient in region g | What is estimated | Information shared? |
|---|---|---|---|
| **`hierarchical`** (default) | β_g = ±exp(μ + τ·z_g) | a population centre μ, a spread τ, and one deviation z_g per region | **Partly** — each region is pulled towards the centre, more so when its own data is weak |
| **`global`** | β_g = ±exp(b), the same b everywhere | one coefficient | **Completely** — every region has the same β |
| **`independent`** | β_g = ±exp(η_g), η_g with its own prior | one coefficient per region, nothing linking them | **Not at all** |

### 5.1 Hierarchical — partial pooling

```
μ  ~ Normal(mu_log, sigma_log)              the population centre    (your prior)
τ  ~ HalfNormal(regional_sd_log)            how much regions differ  (regional_sd_prior)
z_g ~ Normal(0, 1)                          region g's deviation, in units of τ
η_g = μ + τ · z_g  (+ a fixed offset if region g has its own prior row)
β_g = ±exp(η_g)
```

**Trace names** (bucket `hpos` as the example):

| Parameter | Is |
|---|---|
| `mu_logbeta_hpos` | μ — the population centre, log scale |
| `tau_logbeta_hpos` | τ — the cross-region spread, log scale |
| `z_beta_hpos` | z_g — each region's standardised deviation |
| `beta_hpos` | β_g — the region coefficients (deterministic) |
| `pop_beta_hpos` | ±exp(μ) — the **median** population effect: the `__population__` row of the coefficient report |
| `region_prior_offset_hpos` | the fixed per-region shift, only when region rows exist |

**What partial pooling does.** Each region's coefficient is a compromise
between what its own data says and the population centre. The weight on its own
data is roughly:

> weight_g ≈ τ² ÷ (τ² + s_g²)

where s_g is how uncertain the region's own estimate would be on its own.

**Example (illustrative; treats μ and τ as known).** Population median 0.05,
τ = 0.3 on the log scale.

| Region | Own data says | Own uncertainty s_g | Weight on own data | Pooled estimate |
|---|---|---|---|---|
| A — big, long, clean | 0.08 | 0.1 | 0.09 ÷ 0.10 = **0.90** | **0.076** — keeps most of its own value |
| B — small, noisy | 0.10 | 0.6 | 0.09 ÷ 0.45 = **0.20** | **0.057** — pulled most of the way to 0.05 |

That is the point of pooling: **a small region's coefficient borrows strength
from the others** instead of being pure noise, while a well-measured region
keeps its own answer.

**Why it is written as μ + τ·z and not as β_g ~ Normal(μ, τ).** Both are the
same model. The first is the **non-centred** form. When τ is small, the second
form has a "funnel" — the region values squeezed into a narrow neck — which the
sampler cannot explore, and it diverges. The non-centred form samples z on a
fixed N(0,1) scale. Meridian builds its geo effects the same way (§12). The `z_*`
parameters are uninformative by construction, so the contraction report labels
them so.

**`regional_sd_prior`** is the prior scale of τ, on the same axis as the prior
sd (log for a signed feature). Default 0.5: the prior expects regions to differ
by tens of percent, rarely by more than ×2–3. A very small value (below 0.02)
makes regions identical — the `pooling_collapsed` warning: you have built a
global model with extra parameters.

**Per-region prior rows under hierarchical** do not replace the pooling: the
region's prior mean becomes a **fixed offset** that shifts the centre that
region is pulled towards. Its prior sd is ignored (the pooled τ governs spread —
the `per_region_prior_sd_ignored` warning).

### 5.2 Global — complete pooling

```
b ~ Normal(mu_log, sigma_log)
β_g = ±exp(b)            identical in every region
```

Trace names: `glogbeta_gpos` (b), `beta_gpos` (β, repeated per region).

**Identical on the scaled axis — not in sales.** β is the same number in every
region, but it multiplies each region's own feature and is converted back with
each region's own KPI scale. Under `dv_scale: mean`, a global β says:

> "one unit of x lifts sales by the same **percentage of the region's mean** everywhere"

So a region with twice the sales gets twice the volume per unit of x. That is
an assumption — usually a reasonable one — and worth stating.

**Use it** when the effect is national, regions are short or few, or the
benchmark you reproduce has one national coefficient (the secondary model).
**Its limit:** it cannot match a regional pattern. v9: one β for 12 regions
missed the vendor by −15% in one channel and +1382% / +3943% in others (`../CLAUDE.md`
"The v9 panel").

### 5.3 Independent — no pooling

```
η_g ~ Normal(mu_log_g, sigma_log_g)    region g's OWN prior (a region row, or the feature's)
β_g = ±exp(η_g)
```

Trace names: `logbeta_ipos` (η_g), `beta_ipos` (β_g). For a free feature the
sampled parameter is `beta_ifree` itself.

- **Every region stands alone.** It is the old `production_code.py` behaviour,
  one feature at a time.
- **No population parameter**, so no `__population__` row in the coefficient
  report.
- **Per-region priors** are used in full (the regional prior file,
  `feature_priors_regional.csv`, is written for this mode).
- **Its limit:** a short or noisy region gets a noisy coefficient, and the
  number of periods per region caps what can be separated (`cases/001`).

### 5.4 The three side by side

| | Hierarchical | Global | Independent |
|---|---|---|---|
| Parameters per feature | 2 + one per region | 1 | one per region |
| Small regions | borrow from the others | identical to the others | on their own |
| Regional differences | allowed, shrunk | none | unrestricted |
| `__population__` row | yes (`pop_beta`) | yes (the shared β) | no |
| Row used for contraction / the benchmark delta | `mu_logbeta_*` / `mu_beta_*` | `glogbeta_*` / `gbeta_*` | `logbeta_*` / `beta_ifree` |
| Per-region prior rows | shift the centre | not used | used in full |
| Typical use | regional drivers with some data everywhere | national drivers; short panels; the secondary model | regions that are genuinely different and each have plenty of data |

Every feature picks its own mode, so one model can mix all three.

---

## 6. Buckets: how the code builds it

`model.py` does not loop over features. It groups them into **nine buckets** —
pooling × sign — and builds each bucket as one vectorised block:

| | positive | negative | free |
|---|---|---|---|
| **hierarchical** | `hpos` | `hneg` | `hfree` |
| **independent** | `ipos` | `ineg` | `ifree` |
| **global** | `gpos` | `gneg` | `gfree` |

Each bucket produces a `(region × feature)` matrix `beta_<bucket>`, and the
feature term is

```
Σ over buckets of  (X_bucket · beta_bucket[region])   summed across the bucket's features
```

Parameter names follow a pattern: `log` appears for signed buckets (the
parameter lives on the log scale); the prefix `mu_`/`tau_`/`z_` marks the
hierarchy, `g` marks global. `diagnostics.py` uses these prefixes to decide
which row of the contraction file feeds the benchmark delta (`use_for_delta`).

**Why buckets.** One vectorised block per bucket is far faster on GPU than 30–80
separate scalar parameters, and the sign and pooling are fixed within a block,
so the code stays simple.

---

## 7. Priors: what you write vs what is sampled

You write `global_prior_mean` and `global_prior_sd` in coefficient terms. The
model samples η. `config.resolve_prior_params` converts once, and
`01_data/prior_summary.csv` prints both sides.

**For a signed feature** (β = ±exp(η), η ~ Normal(μ, σ)):

| You write | `prior_sd_basis` | σ sampled |
|---|---|---|
| `global_prior_sd: 0.2` | `log` (default) | 0.2 — already on the log scale |
| `global_prior_sd: 0.2` | `relative` | √ln(1 + 0.2²) = 0.198 — "±20%" |
| `global_prior_sd: 0.01` (with mean 0.05) | `absolute` | √ln(1 + (0.01/0.05)²) = 0.198 — in coefficient units |

| `prior_mean_basis` | μ sampled | Your number is |
|---|---|---|
| `median` (default) | ln(mean) | the median coefficient |
| `mean` | ln(mean) − σ²/2 | the mean coefficient |

**For a free feature** (β ~ Normal(μ, σ)): μ is your mean; σ is your sd
(`relative` multiplies it by |mean|).

**`regional_sd_prior`** is converted with the same basis as the prior sd.

**Example (from the defaults).** A signed feature with mean 0.05 and the
default sd of 1.0 on the log basis has a 90% prior interval from 0.0097 to 0.26
— a fifth to five times your number. Write `0.2` with `relative` for ±20%.
(`FEATURE_PRIOR_GUIDE.md`; `understanding_prior_sd_conversion.md`.)

**The baseline priors are fixed in the code** and live on the scaled axis:

| Term | Prior |
|---|---|
| Intercept | μ_α ~ N(0, `alpha_prior_sd` 0.5), τ_α ~ HalfNormal(`alpha_regional_sd` 0.5) |
| Seasonality | γ_k ~ N(0, 0.3) |
| Trend | μ_δ ~ N(0, 0.2), τ_δ ~ HalfNormal(0.2) |
| Noise | §8 |

---

## 8. Noise and the likelihood

**One noise level per region, `σ_g`.** Regions differ in how noisy they are, so
each gets its own.

- `model.pool_sigma: true` (default) partially pools them on the log scale:
  `log σ_g = μ + τ·z_g`, μ ~ N(−0.5, 1), τ ~ HalfNormal(0.5). A short or noisy
  region borrows a sensible noise level from the others.
- `pool_sigma: false` gives each region σ_g ~ HalfNormal(1), independently.

**The likelihood:**

- `normal` (default): `y ~ Normal(fitted, σ_g)`.
- `student_t`: `y ~ StudentT(ν, fitted, σ_g)` with ν = 2 + Exponential(0.1)
  (mean ν ≈ 12). Heavier tails, so a few extreme weeks (promotions, stock-outs)
  stop dragging every coefficient. Use it when the residual-tails check flags
  (CHECKS_GUIDE §8.5).

---

## 9. Fitting: the NUTS sampler and its settings

`fit.py` fits the model with **NUTS** (the No-U-Turn Sampler), a form of
Hamiltonian Monte Carlo. Nothing in this section changes the model — only how
well, and how fast, it is explored.

### 9.1 What NUTS does, in plain terms

Picture the posterior as a landscape: valleys where the parameters fit the data
and agree with the priors, slopes everywhere else. NUTS rolls a ball across it:

1. Give the ball a random push (momentum).
2. Let it roll for a while, simulated in small **leapfrog steps**.
3. Stop when the path starts to turn back on itself — the "no U-turn" rule.
4. Keep a point from that path as the next **draw**, and repeat.

Three things control the roll:

- **Step size** — how far each leapfrog step goes. Too big and the simulation
  becomes inaccurate (a **divergence**); too small and it crawls. It is tuned
  automatically during warm-up to hit `target_accept`.
- **Mass matrix** — how the sampler scales each parameter, so a coefficient of
  0.002 and an intercept of 0.5 are explored at sensible speeds. Learned during
  warm-up. *Diagonal* (default) learns one scale per parameter; *dense* also
  learns how parameters move together.
- **Tree depth** — each path is built by doubling its length until it turns
  back, up to `max_treedepth` doublings: at most 2^depth − 1 steps (1,023 at the
  default 10). A path that hits the cap is "saturated".

> **There is no "steps" setting.** NUTS chooses the number of leapfrog steps for
> every draw by itself; you only set the cap (`max_treedepth`) and the
> acceptance target that the step size is tuned to.

### 9.2 The two phases: warm-up (burn-in) and sampling

| Phase | Setting | What happens | Kept? |
|---|---|---|---|
| **Warm-up** — what other tools call burn-in | `tune` (1000 per chain) | The chain walks from its starting point into the posterior, while the step size and mass matrix adapt | **No** — discarded |
| **Sampling** | `draws` (1000 per chain) | The settings are frozen; each iteration gives one draw | **Yes** |

With the defaults: 4 chains × 1,000 draws = **4,000 kept draws**, after
4 × 1,000 warm-up iterations. The decomposition and charts use a random subset
of `run.report_draws` (400) of them, to bound memory. There is no thinning
setting: keeping every draw is cheaper than thinning, and `report_draws`
already subsamples where it matters.

### 9.3 Every setting

| Setting (`sampler:`) | Default | What it controls | Change it when | Cost |
|---|---|---|---|---|
| `draws` | 1000 | Kept draws per chain | ESS (bulk or tail) below 400 **after** the geometry is fixed | Linear in time |
| `tune` | 1000 | Warm-up (burn-in) per chain | Trace plots still drifting at the start; `dense_mass` on (it needs more warm-up to learn) | Linear in time |
| `chains` | 4 | Independent chains | Never below 4 — R-hat needs several chains | Linear (or parallel on GPU) |
| `target_accept` | 0.92 | Acceptance target the step size is tuned to | Divergences: raise to 0.95–0.99 | Smaller steps, longer paths |
| 🆕 `max_treedepth` | 10 | Cap on path doubling: at most 2^depth − 1 steps per draw | Tree depth saturated **and** the geometry is already fixed: 11–12 | Each level doubles the worst-case cost of a draw |
| 🆕 `dense_mass` | `false` | Diagonal vs full mass matrix | Coefficients trade off (strong `posterior_correlation.csv`), with saturated tree depth or low ESS on those parameters | Slower per step; needs enough `tune` to estimate; heavy with many hundreds of parameters |
| 🆕 `jitter` | `true` | Start each chain from a randomly jittered point | Leave on. Chains that start in the same place can agree by accident, hiding a problem from R-hat | — |
| `seed` | 42 | Random seed | Set it, so a run can be reproduced | — |
| `sampler` | `numpyro` | `numpyro` (JAX; GPU), `pymc` (CPU), `advi` (approximation) | `advi` only for quick exploration or CV — no R-hat, intervals too narrow | — |
| `chain_method` | `sequential` (`vectorized` in the team's config.yaml) | How NumPyro runs the chains | `vectorized` on one GPU: all chains in one kernel | — |
| `nuts_kwargs` | `{}` | Any other kernel argument (e.g. `step_size`) | Rarely. Either spelling of the tree depth works here; an entry overrides the settings above | — |
| `prior_predictive_draws` | 500 | Draws from the priors alone | Keep ≥ 500 — they feed the contraction report | — |
| `store_log_likelihood` | `false` | Keep pointwise log-likelihood (LOO / WAIC) | Comparing models by LOO | Much bigger trace |
| `advi_iters` | 30000 | `advi` iterations | `sampler: advi` only | — |
| `allow_sampler_fallback` | `false` | Fall back to the PyMC sampler if NumPyro/GPU fails | Leave off: a failed GPU run should stop, not silently burn CPU hours | — |

Before 2026.10.14.1 the tree-depth cap, the mass matrix and the jitter could
only be reached through `nuts_kwargs`, and the two backends spell the cap
differently: NumPyro rejects `max_treedepth` ("unexpected keyword argument") and
PyMC rejects `max_tree_depth`. `fit.kernel_kwargs` now translates for whichever
backend runs. On real PyMC 6.3 / NumPyro 0.22, `max_treedepth: 3` capped the
real trajectories at depth 3, on both routes.

### 9.4 Which setting for which symptom

**Fix the model before the sampler.** Every symptom below is usually the
model's geometry — collinear columns, a level variable fighting the intercept, a
funnel in a hierarchy — and the sampler settings only treat it.

| Symptom (`convergence_report.txt`) | First (the model) | Then (the sampler) |
|---|---|---|
| R-hat > 1.01; chains in different places | Centre level variables; merge collinear pairs (CHECKS_GUIDE §3) | More `tune`; keep `jitter` on |
| Divergences | Same, plus tighter `regional_sd_prior` (a funnel) | `target_accept` 0.95–0.99 |
| Tree depth saturated | Collinearity — a long, narrow ridge (v1: 100% saturated) | `dense_mass: true` if coefficients trade off; then `max_treedepth` 11–12 |
| Low ESS (bulk) | Same | More `draws` |
| Low ESS (tail) only | — | More `draws`; don't quote HDIs until it clears 400 |
| Too slow | Fewer or merged variables | `chain_method: vectorized` on GPU; fewer `draws`/`tune` while exploring, full settings for the final run |

### 9.5 What to check after every run

`02_convergence/sampling_log.json` records what actually happened:

- `sampler_requested` / `sampler_used` — a fallback shows here;
- `sampling_route` and `chain_method_applied`;
- `kernel_kwargs_used` and `kernel_kwargs_applied` — the kernel settings as
  sent to the backend, and whether that backend accepted them;
- `init_used` (PyMC only), e.g. `jitter+adapt_diag`;
- versions and `wall_seconds`.

`convergence_report.txt` names the cap in force: `max tree depth : 8 of cap 10
(OK)`.

---

## 10. From draws to the report

The sampler returns thousands of draws — plausible versions of every
parameter. `outputs.compute_decomposition` turns them into the report:

1. **A random subset of draws** (`run.report_draws`, default 400) is used for
   the decomposition and the charts, to keep memory bounded. Convergence and
   contraction use every draw.
2. **Per draw, per week:** each feature's contribution is `β_g · x[g,t]` on the
   scaled axis, multiplied by the region's KPI scale to get sales units.
3. **`contribution_reference`** (reporting only): measures a contribution
   against zero, the feature's mean, its minimum or a fixed value. Whatever is
   added to a feature is taken out of the baseline core, so the total is
   unchanged (TUNING_GUIDE §5.1).
4. **Blocks:**
   - baseline core = intercept + seasonality + trend (+ the KPI's centre, added
     back);
   - baseline = core + every feature with `baseline=1`;
   - fitted = baseline + every other feature.
5. **Posterior predictive draws** add the noise (σ_g, and ν for Student-t) to
   the fitted value. The holdout coverage is judged on these.
6. **Summaries:** a feature's window total is the median of its total across
   draws, with a 90% HDI (`contribution_totals.csv`). Tables that must add up use
   sums of weekly medians, with the median gap on its own line
   (`contribution_reconciliation.csv`; `cases/002`).

---

## 11. Choosing a pooling mode

| Situation | Pooling | Why |
|---|---|---|
| Regional driver, data in most regions (TDP, price by channel, regional media weights) | `hierarchical` | Regions can differ; small ones borrow strength |
| National driver — the same series in every region (national TV, national events) | `global` | The regions add no new variation for it; one coefficient is honest (METHODOLOGY §3c) |
| Short panel (monthly, ~21 periods) with many variables | `global` for most | `independent` hits the per-region limit hard (`cases/001`) |
| Secondary model reproducing a national vendor model | `global` | Matches the benchmark's structure |
| Vendor coefficients known per region | `independent` + `feature_priors_regional.csv` | Each region's own prior used in full |
| A feature active in only one or two regions | `global` or `independent` | `hierarchical` cannot learn τ from one region |
| Regions genuinely different and each with plenty of data | `independent` | No reason to shrink |

**Signs of the wrong choice:**

| Symptom | Likely | Change |
|---|---|---|
| Forest plot: every region identical, but they should differ | `global`, or `regional_sd_prior` tiny | `hierarchical`; raise `regional_sd_prior` |
| Small regions wildly different, wide intervals | `independent` | `hierarchical` |
| Divergences on `tau_*` | a funnel: τ near 0 with little data | tighten `regional_sd_prior`, or `global` |
| A regional benchmark pattern cannot be matched | `global` | `independent` with regional priors (v9) |

---

## 12. Compared with Meridian

| Piece | Codebase 1 | Meridian (`meridian/model/`) |
|---|---|---|
| Media coefficient across geos | `hierarchical`: β_g = exp(μ + τ·z_g) | `beta_gm = exp(beta_m + eta_m · dev)` with `media_effects_dist: log_normal` (default) — **the same non-centred log-normal hierarchy** (`posterior_sampler.py`, `_joint_dist_base_logic`) |
| One coefficient for all | `global`, per feature | a national model (one geo): `eta_m` is forced to 0 (`prior_distribution.py`) |
| No pooling | `independent`, per feature | not offered |
| Region intercept | pooled: α_g = μ_α + τ_α·z_g | geo fixed effects, `tau_g_excl_baseline ~ Normal(0, 5)`, not pooled |
| Time baseline | Fourier (shared) + a trend per region | `mu_t` from knots; in a geo model, by default **one effect per period** (`knots` = number of periods) |
| Noise | one σ per region, pooled | one σ for all geos unless `unique_sigma_for_each_geo: true`; `sigma ~ HalfNormal(5)` |
| Sign | structural, per feature, three ways | log-normal media effects are positive; controls are free |
| Adstock / saturation | upstream, fixed | inside the model, learned (codebase 2) |
| Prior on media | coefficient (volume basis) | ROI by default |

The two models share the core idea — one joint regression with region-level
coefficients pulled towards a population value, built non-centred. Codebase 1
adds a per-feature choice of how much to pool; Meridian adds learned
transforms and ROI-calibrated priors.

---

## 13. What is learned, and what you fix

| Learned by the sampler | Fixed by you |
|---|---|
| Every coefficient β (and its population centre and spread, if hierarchical) | Which variables, and their pre-transformation (adstock, lag, saturation — upstream) |
| Region intercepts, their centre and spread | The sign of each feature |
| Seasonal shape (γ), trend slopes | The pooling of each feature |
| Region noise levels; ν for Student-t | Every prior: means, sds, regional sds, region rows |
| | The scaling (`dv_scale`, `center_mode`, `scale_mode`) — the unit of every prior |
| | Which baseline terms exist (intercept, Fourier order, trend) |
| | The sampler settings (§9) — they change how well the model is explored, never the model |

---

## Glossary

| Term | Meaning |
|---|---|
| **Bucket** | One of nine groups of features sharing pooling and sign, built as one vectorised block |
| **Complete pooling** | One coefficient for every region (`global`) |
| **Contraction** | 1 − posterior variance ÷ prior variance: how much the data, not the prior, decided a parameter |
| **Draw** | One plausible version of every parameter, from the sampler |
| **η (eta)** | The log of a signed coefficient: β = ±exp(η) |
| **Funnel** | The narrow-neck posterior shape a centred hierarchy makes when τ is small; the non-centred form avoids it |
| **Non-centred** | Writing a region value as centre + spread × N(0,1) instead of drawing it around the centre directly |
| **No pooling** | Each region estimated alone (`independent`) |
| **Partial pooling** | Region values pulled towards a shared centre, more when their own data is weak (`hierarchical`) |
| **Population effect** | The centre of a hierarchy: ±exp(μ), the median effect across regions |
| **Scaled axis** | The KPI and features as the model sees them, after centring and scaling |
| **τ (tau)** | The spread of a hierarchy: how much regions differ |
