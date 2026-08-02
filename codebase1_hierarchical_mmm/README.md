# Codebase 1 — Hierarchical MMM on pre-transformed data

Upgrade of `production_code.py` that keeps the current workflow (adstock / Hill / lag
applied **in preprocessing**, transformed data handed to the model) but replaces the
per-region loop of independent PyMC regressions with **one joint hierarchical model**,
sampled on a GPU-capable JAX backend, with full stage-by-stage outputs.

Covers goals **1** (GPU/vectorised), **2** (hierarchical), **3** (outputs & metrics).
Goals 4/4.2 (in-model transforms + EDA) are deliberately out of scope here — that is
`codebase2_full_mmm`.

## Data contract

One long (stacked) DataFrame — same shape as `input_datacube.xlsx`:

| column | meaning |
|---|---|
| `date` | period (weekly/monthly), parseable dates |
| `region` | region / geo id |
| `dv` | KPI (sales), original units, no NaN |
| everything else | **pre-transformed** regressors, no NaN (zero-fill inactive weeks) |

## Feature configuration (replaces the b0/B0 prior file)

One row per **feature** (not per region×feature — the hierarchy generates regional
variation). Via `FeatureSpec(...)` in code or a CSV loaded with `load_feature_config`:

```csv
variable,hierarchical,sign_constraint,global_prior_mean,global_prior_sd,regional_sd_prior
tv,1,positive,0.05,1.0,0.5
price_index,1,negative,0.3,0.7,0.4
distribution,1,free,0.0,0.5,0.5
dummy_covid,0,free,0.0,1.0,
```

- `sign_constraint=positive/negative` → coefficient built as `±exp(normal)`: it can
  **never** cross zero (structural sign control, like the PE model / Meridian).
- `hierarchical=1` → non-centred partial pooling across regions; `0` → one global
  coefficient shared by all regions.
- Scale conventions for priors are documented at the top of `config.py`
  (KPI standardised per region; signed features scaled by mean positive activity;
  free features z-scored per region).

## Run

```python
from config import ModelConfig, RunConfig, SamplerConfig, load_feature_config
from run_pipeline import run

result = run(df, ModelConfig(features=load_feature_config("feature_priors.csv"),
                             fourier_order=2, include_trend=True,
                             likelihood="student_t"),
             RunConfig(run_name="fy26", holdout_periods=13),
             SamplerConfig(sampler="numpyro", chains=4))
```

Smoke test / parameter recovery: `python synthetic_example.py`.

## Outputs (`outputs/<run_name>/`)

| folder | contents |
|---|---|
| `01_data` | panel summary, per-region scaling stats, KPI plots |
| `02_convergence` | sampling log (backend, wall time), R-hat / ESS / divergences report, energy & worst-trace plots, prior-posterior contraction, prior-predictive check |
| `03_coefficients` | `coefficient_report.csv` (median, sd, 90% HDI, **P(effect>0)** per region + population row) and forest plots showing shrinkage toward the population mean |
| `04_fit` | R² / MAPE / wMAPE / MAE / 90%-coverage per region, train **and holdout**, actual-vs-fitted with credible bands, residual plots |
| `05_contributions` | contribution totals with HDIs and share-of-sales, bar chart, weekly portfolio decomposition |

## What changed vs `production_code.py` (talking points)

1. **One model, all regions** — partial pooling: noisy regions borrow strength; no more
   dropping all-zero columns per region (the hierarchy handles them: contribution 0,
   coefficient shrinks to the population mean).
2. **Speed** — one compiled graph instead of ~70; JAX JIT + optional GPU
   (`SamplerConfig(sampler="numpyro", nuts_kwargs={"chain_method":"vectorized"})` on one GPU).
3. **Valid inference reporting** — `t = mean/mcse_mean` p-values are gone; use
   `prob_positive` and whether the 90% HDI excludes zero.
4. **Sign control and baseline** — per-feature sign constraints; optional Fourier
   seasonality + trend so media doesn't absorb the seasonal cycle.

## Caveat to keep stating

Because data arrives pre-transformed, any error in the hand-chosen adstock/saturation
parameters is invisible to this model — the fit will look fine and the coefficients will
be biased. That is structural to the preprocessing approach and is exactly what
codebase 2 (learned transforms) exists to fix.
