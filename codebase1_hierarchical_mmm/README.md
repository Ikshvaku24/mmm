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
variable,hierarchical,sign_constraint,global_prior_mean,global_prior_sd,regional_sd_prior,center
tv,1,positive,0.05,1.0,0.5,0
price_index,1,negative,0.30,0.7,0.4,1
distribution,1,positive,0.20,0.7,0.4,1
dummy_covid,0,free,0.0,1.0,,
```

- `sign_constraint=positive/negative` → coefficient built as `±exp(normal)`: it can
  **never** cross zero (structural sign control, like the PE model / Meridian).
  Consequence: for these features `P(effect>0)` and "HDI excludes zero" are true
  *by construction*, so the reports leave those two columns **blank** — judge a
  sign-constrained feature by its effect size, HDI width and data support, never
  by "significance".
- `hierarchical=1` → non-centred partial pooling across regions; `0` → one global
  coefficient shared by all regions.
- `center=1` (optional column) → keep the sign constraint but centre *and* scale the
  feature, the way controls are scaled. **Required for always-on level variables**
  — distribution points, price indices, ACV measures. With the default scale-only
  scaling such a variable sits at ≈1.0 every week, which is collinear with the
  region intercept: the sampler cannot separate its coefficient from the baseline.
  `prepare_data` now warns when it detects this. A centred feature's contribution is
  measured **relative to its own average level**, not versus zero — the
  `contribution_vs` column in `contribution_totals.csv` says which, and a positive
  coefficient can legitimately show a negative total (most weeks below average).
- Feature columns whose positive values are all numerical dust (train mean below
  `RunConfig.min_feature_scale`, default `1e-12` — e.g. the adstock tail of activity
  that stopped before the data window) are **rejected** at data prep. Dividing by a
  ~1e-15 scale turns float noise into a unit-scale regressor and, under a sign
  constraint, manufactures contribution out of nothing. Drop the column or fix its
  units upstream.
- Scale conventions for priors are documented at the top of `config.py`
  (KPI standardised per region; signed features scaled by mean positive activity,
  or centred when `center=1`; free features z-scored per region).

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
| `02_convergence` | `sampling_log.json` run manifest (package versions, devices, sampler requested vs used, timings), R-hat / bulk+tail ESS / divergences / per-chain BFMI / tree-depth report, energy & worst-trace plots, prior-posterior contraction, prior-predictive check |
| `03_coefficients` | `coefficient_report.csv` — per region + population row: median, sd, **true 90% HDI**, P(effect>0) *(blank for sign-constrained features — vacuous there)*, `scaling_method`, **original-unit conversion** (KPI units per raw feature unit) and **data-support flags** (`none` / `weak` / `weak (near-constant)` / `adequate` — a region where the feature never ran, **or where it never moves**, gets a shrinkage prior, not a regional estimate; `support_warnings.txt` lists these); forest plots showing shrinkage toward the population mean |
| `04_fit` | R² / MAPE / wMAPE / MAE per region, train **and holdout**, plus **two coverage columns**: `coverage_90_pred_pct` (posterior predictive — judge holdout by this) and `coverage_90_mean_pct` (mean-response interval — expected to be narrower than 90%); fit plots show both bands; residual plots. On the `__all__` row read **`r2_within_region`**, not `r2`: the pooled `r2` is inflated by the differences in level between regions and can sit at 0.99 while every region is fitted badly |
| `05_contributions` | contribution totals with HDIs and share-of-sales, `contribution_vs` (whether a feature's contribution is measured versus zero or versus its own average level), bar chart, weekly portfolio decomposition |

## Cross-validation (expanding window / rolling origin)

The single `holdout_periods` split is a smoke test, not validation: one origin, one
window, high variance, one slice of the seasonal cycle. For a defensible predictive
story use the expanding-window CV (same design as the PE package's hierarchical
temporal CV):

```python
from config import CVConfig, ModelConfig, RunConfig, SamplerConfig
from cross_validation import run_cv

cv = run_cv(df, model_cfg, RunConfig(run_name="fy26"),
            SamplerConfig(sampler="numpyro"),
            CVConfig(horizon=13, n_folds=5))          # 5 refits, ~13-week horizon
```

Each fold refits the full model on an expanding training window (scaling stats
recomputed per fold — no leakage) and predicts the next `horizon` periods. Outputs in
`06_cross_validation/`:

- `cv_fold_metrics.csv` / `cv_summary.csv` — per-fold and mean±sd test metrics:
  wMAPE, **region-weighted MAPE** (volume-weighted average of per-region MAPEs — the
  PE package's SKU-weighted MAPE, answering a different question than wMAPE),
  **CRPS** (proper scoring rule combining calibration and sharpness), predictive
  90% coverage, plus per-fold convergence flags.
- `cv_coefficient_stability.csv` + `cv_stability_ranking.csv` + `stability/*.png` —
  **the key MMM trust check**: coefficient medians across folds. A feature that
  swings as the origin moves has a fragile contribution story regardless of wMAPE.
- `cv_report.md` — headline readout.

Speed: fold count × one fit. For sweeps use `SamplerConfig(sampler="advi")` (PE
convention: **ADVI for CV speed, NUTS for the final fit** — mean-field ADVI
understates uncertainty, so use it for relative comparison only) or override
`CVConfig(draws=..., tune=...)`.

Deliberately **not** used: purged/embargoed CV (features here are exogenous and known
in advance — no label leakage to purge), PSIS-LOO as the primary tool (exchangeability
fails on autocorrelated panels; enable `store_log_likelihood` if you want it for
relative comparison), and leave-region-out CV (answers a transfer question nobody
asked and fights the hierarchy).

## Convergence guardrail

`RunConfig(on_convergence_failure="fail")` makes the pipeline **raise** when max
R-hat > 1.05 or >1% of transitions diverge, instead of silently persisting a bad fit
(the PE methodology flags warn-and-continue as a production risk; default remains
`"warn"` for exploration).

**Interpretation notes** (from the AI code review, adopted):
- Intervals are true HDIs (shortest interval), named `hdi_low`/`hdi_high`.
- Holdout coverage is judged against **posterior predictive** intervals (parameter
  uncertainty + likelihood noise), not mean-response intervals.
- Scaled coefficients pool "response in region-relative units"; equal scaled effects
  in two regions do **not** imply equal original-unit response — use the
  `*_orig_units` columns when comparing regions in absolute terms.
- Region noise `sigma_g` is partially pooled on the log scale by default
  (`ModelConfig(pool_sigma=False)` for independent sigmas).

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
