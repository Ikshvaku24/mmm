# Updated Production MMM — Two Codebases

Two self-contained upgrades of `production_code.py`, both replacing the per-region loop of
independent PyMC models with **one joint, vectorised, hierarchical model** sampled by
NUTS on a GPU-capable backend.

| | `codebase1_hierarchical_mmm` | `codebase2_full_mmm` |
|---|---|---|
| Input data | **Pre-transformed** X (adstock/Hill/lag done in preprocessing, as today) | **Raw** media (transforms learned inside the model, like Meridian) |
| Hierarchical partial pooling across regions (goal 2) | ✅ | ✅ |
| GPU / vectorised sampling (goal 1) | ✅ | ✅ |
| Stage-by-stage outputs: metrics + graphs (goal 3) | ✅ | ✅ |
| In-model adstock / saturation / lag, learned by MCMC (goal 4) | ❌ (by design) | ✅ per-feature controllable |
| Report of learned transform parameters & ranges (goal 4) | ❌ | ✅ (`04_transforms/`) |
| EDA stage like Meridian (goal 4.2) | ❌ (data arrives transformed) | ✅ (`02_eda/`) |

## Why PyMC + JAX (not TensorFlow / PyTorch)

- The team already writes PyMC (this production code **and** the PE price-elasticity model).
  Staying in PyMC means zero retraining; only the *sampler backend* changes.
- `pm.sample(nuts_sampler="numpyro")` compiles the model with **JAX** and runs NUTS on GPU
  (or JIT-compiled CPU, typically 3–10× faster than the default sampler even without a GPU).
- The bigger win is structural: `production_code.py` builds ~1 model **per region** in a
  Python loop. Both codebases compile **one** model over all regions (like Meridian's
  geo-hierarchical model), so compilation and sampling happen once.
- A TensorFlow-Probability or Pyro rewrite would change the whole authoring language for no
  modelling gain. If the team ever adopts Meridian itself, that is the TFP path.

### GPU install notes

- **Databricks GPU cluster / Colab (Linux)** — the intended GPU path:
  `pip install pymc numpyro "jax[cuda12]"`.
- **Windows laptop** — JAX GPU wheels are Linux-only; you still get JIT-compiled CPU
  (`pip install numpyro jax`), or use WSL2 for CUDA. The code falls back to the default
  PyMC sampler automatically if JAX/NumPyro is missing.
- One GPU, several chains: pass `nuts_kwargs={"chain_method": "vectorized"}` in
  `SamplerConfig` to run chains in parallel on a single device.

## Fixes to known flaws of `production_code.py` (both codebases)

- **No pooling** → non-centred hierarchical priors: small regions borrow strength
  (same construction as the PE model and Meridian's `beta_gm = beta_m + eta_m·z`).
- **Per-region zero-column dropping** → unnecessary: an all-zero feature in a region simply
  contributes nothing and its coefficient shrinks to the population mean.
- **`t = mean/mcse_mean` p-values** (statistically invalid — mcse shrinks as you sample
  longer) → replaced with posterior sd, HDI and `P(effect > 0)`.
- **No trend/seasonality baseline** → optional Fourier seasonality + linear trend
  (a light version of Meridian's spline `mu_t` knots).
- **All regressors in one bucket, no sign control** → per-feature config: hierarchical or
  global, positive / negative / free sign (sign enforced structurally via `±exp(·)`,
  exactly like the PE model and Meridian's `media_effects_dist="log_normal"`).
- **Region×variable prior file (b0/B0)** → feature-level prior file: one row per feature
  (population mean, population sd, regional heterogeneity sd); the hierarchy generates the
  regional variation instead of hand-set per-region priors.

## Meridian ↔ this code translation

| Standard MMM term | Meridian | These codebases |
|---|---|---|
| Region random effects | `beta_gm = beta_m + eta_m·N(0,1)` | non-centred buckets in `model.py` |
| Adstock decay | `alpha ~ Uniform(0,1)` | `adstock_alpha` (codebase 2) |
| Saturation half-point (EC50) | `ec ~ TruncatedNormal` | `hill_ec` (codebase 2) |
| Saturation shape | `slope`, fixed 1.0 by default | `hill_slope`, same default |
| Lag / peak delay | (inside adstock window) | `adstock_theta` (delayed adstock, codebase 2) |
| Trend/seasonality baseline | spline knots `mu_t` | Fourier + trend (simpler) |
| Media scaling | ÷ per-geo median | ÷ per-region median of positive weeks |
| KPI scaling | per-geo standardise | per-region standardise |

Each codebase folder has its own README with the data contract, configuration guide and a
runnable synthetic example (`python synthetic_example.py`) that also serves as a
parameter-recovery test.

## Validation strategy (v1.2) — adopted from the PE package

Both codebases now ship `cross_validation.py`: **expanding-window (rolling-origin)
CV** — the same design as the PE package's hierarchical temporal CV. Each fold refits
the full model on an expanding training window and predicts the next `horizon`
periods; the single `holdout_periods` split remains as a quick smoke test. Reported
per fold and aggregated: wMAPE, **region-weighted MAPE** (PE's SKU-weighted MAPE
analogue), **CRPS**, predictive 90% coverage, convergence flags — plus
**coefficient stability across folds** (and in codebase 2, **transform stability**:
learned adstock decay / EC50 across folds), which is the real MMM trust check.

Also adopted from the PE package: the **ADVI fast path** (`sampler="advi"` — CV and
sweeps on ADVI, final fit on NUTS) and the **convergence guardrail**
(`on_convergence_failure="fail"` raises instead of persisting an unconverged fit).

Considered and not adopted (with reasons in the codebase READMEs): purged/embargoed
CV, PSIS-LOO as the primary validation, leave-region-out CV, and the PE package's
HSGP time-varying intercept (a bigger modelling change — the Fourier + trend baseline
is the deliberate conservative first version; revisit if residuals show unexplained
low-frequency drift).

## Review fixes applied (v1.1)

Following the AI code review (`review codebase1.md`), both codebases were updated:
posterior **predictive** intervals now drive holdout coverage (mean-response intervals
are reported separately); intervals are true HDIs named `hdi_low`/`hdi_high`;
coefficient reports add **original-unit conversions** and **data-support flags** per
region×feature; region noise is **partially pooled on the log scale** by default
(`pool_sigma`); config validation and a full run manifest (package versions, sampler
requested vs used) were added. Per-region scaling was kept deliberately — it is a
different estimand, now explicitly labelled in the reports.
