# Config guide — filling in `config.yaml`

Everything a run does, except the per-feature priors, is one YAML file. This is
the reference for what each setting means, what it defaults to, and when to
change it.

`config.yaml` itself already carries every key at its default with a one-line
comment above it — **that file is the short version of this one**. Regenerate it
any time with:

```bash
python settings.py --write config.yaml
```

Then run:

```bash
python run_real_data.py config.yaml
```

**Two rules that save time:**

1. **Omitted keys take the default.** A working file can be six lines. Only
   write what differs.
2. **A misspelled key is an error, not a silent default.** `draw: 2000` stops
   the run and names the closest valid spelling. This is deliberate: a settings
   file that quietly ignores a typo is worse than no settings file.

**What is NOT here:** the per-feature priors, signs, pooling, scaling and
reporting reference. Those are one row per feature in the prior CSV named by
`data.feature_priors`, because a per-feature table belongs in a table. See
`TUNING_GUIDE.md` §1 for those columns.

---

## The seven sections

| Section | What it controls | Touch it when |
|---|---|---|
| `data` | where the inputs are | every new project |
| `model` | which terms exist, and how strong the baseline priors are | changing the specification |
| `run` | data handling, the KPI scale, bookkeeping | every run (at least `run_name`) |
| `sampler` | MCMC only — never changes the model | convergence problems, or speed |
| `output` | which files get written | trimming a slow run |
| `assumptions` | every diagnostic threshold | you want more or fewer flags |
| `cv` | cross-validation | before anyone acts on a number |

---

## A minimal file

```yaml
data:
  input_path: input_datacube.xlsx
  feature_priors: feature_priors_v7.csv
run:
  run_name: fy26_baseline
  holdout_periods: 13
model:
  fourier_order: 2
  include_trend: true
  likelihood: student_t
```

Everything else is a default.

---

## `data` — where the inputs are

Paths are relative **to the YAML file**, not the working directory.

| Key | Default | Meaning |
|---|---|---|
| `input_path` | `input_datacube.xlsx` | the panel: one row per region × date, with date/region/dv/feature columns. `.xlsx`, `.csv` or `.parquet` |
| `sheet` | `null` | Excel sheet name. `null` = first sheet. Ignored for csv/parquet |
| `feature_priors` | `feature_priors.csv` | the feature/prior table — **the list of modelled columns**. Its `variable` values must match the data column names exactly |
| `date_format` | `null` | explicit strptime format. `null` = infer |
| `benchmark_mapping` | `null` | **optional.** Maps our features to a benchmark's combined variables. See "Comparing to a benchmark" below |

---

## `model` — the specification

| Key | Default | Options | Meaning |
|---|---|---|---|
| `likelihood` | `normal` | `normal`, `student_t` | `student_t` downweights promo/holiday spikes instead of letting them drag every coefficient. Switch when `04_fit/assumption_checks.csv` flags excess kurtosis |
| `fourier_order` | `0` | 0+ | annual seasonality harmonics. Each costs 2 parameters. **2 is a good start**; 0 if seasonality is already carried by dummies |
| `fourier_period_days` | `365.25` | | period of the cycle. Leave it |
| `include_trend` | `false` | | a pooled per-region linear time trend. Powerful and dangerous — it extrapolates into the holdout |
| `include_intercept` | `true` | | the region intercept. **`false` removes it entirely** so no estimated term can absorb sales the drivers should explain |
| `alpha_prior_sd` | `0.5` | | prior sd of the population intercept. **`0.05` pins it near zero** |
| `alpha_regional_sd` | `0.5` | | how far region intercepts may spread around it |
| `pool_sigma` | `true` | | partial-pool the per-region noise level. `false` = independent sigma per region |

### The intercept, in order of severity

This is the lever for "my baseline is eating the decomposition".

1. `alpha_prior_sd: 0.05` — intercept pinned near zero but still estimated.
2. `include_intercept: false` — gone. **Only with `run.dv_center: mean`.** The
   level does not vanish: the baseline becomes the *fixed* training mean with
   zero posterior width, instead of a free parameter.
3. Explicit region dummies as features, if you want a level you can see.

> **Symptom:** `mu_alpha` near 0.9 on a KPI scaled to mean 1.0 means the
> intercept claimed ~90% of sales before any feature spoke.

---

## `run` — data handling and the KPI scale

| Key | Default | Meaning |
|---|---|---|
| `run_name` | `run` | output folder name. **Change it every run or you overwrite the last one** |
| `output_dir` | `outputs` | parent directory |
| `date_col` / `region_col` / `dv_col` | `date` / `region` / `dv` | column names in the input |
| `dv_center` | `mean` | `mean` or `none` — what is subtracted from the KPI |
| `dv_scale` | `sd` | `none`/`sd`/`mean`/`mean_positive`/`max` — **the unit your priors live in** |
| `dv_scale_scope` | `region` | `region` (own scale each) or `global` (one number for all) |
| `cadence` | `auto` | `auto`/`weekly`/`monthly` — sets every period count downstream |
| `holdout_periods` | `0` | last N dates held out per region. `null` = cadence preset (13 weekly / 3 monthly) |
| `report_draws` | `400` | posterior draws used for the decomposition and plots |
| `on_convergence_failure` | `warn` | `warn` or `fail` — `fail` refuses to persist an unconverged fit |
| `zero_threshold_rel` | `0.0` | snap `abs(v) < this × max abs(v)` to 0. **Use `1.0e-6`** for pre-transformed data whose adstock tail leaves dust |
| `min_feature_scale` | `1.0e-12` | reject a column whose own scale is dust |
| `near_constant_sd` | `0.1` | warn when an always-on scaled feature is this flat |

### ⚠️ The scaling rule that keeps costing runs

**The scale is the unit of the priors.** A coefficient means "moves the KPI by
`beta × dv_scale` per feature unit". Change either scale and **every prior mean
must be divided by the same factor**.

Get this wrong and the decomposition still reconciles to exactly 100% while
being wrong by a constant multiple — **no reconciliation check can catch it.**

Two real failures:

- Priors derived as `contribution / Σx / mean_dv_region` while the KPI was
  divided by a **global max** → contributions inflated by 1.2× / 8.3× / 14.9×
  across three regions. Use `dv_scale_scope: region` unless the priors were
  built on one shared scale.
- Centring stripped from the forward transform while the inverse still added
  the mean back → every fitted value inflated by the region mean, MAPE ~100%.
  **Never edit one side of the transform.** Set `dv_center: none` and the stored
  centre becomes 0, which keeps the inverse correct.

---

## `sampler` — MCMC only

**Nothing here changes the model**, only how well it is explored.

| Key | Default | Meaning |
|---|---|---|
| `draws` / `tune` | `1000` / `1000` | kept draws / warmup, per chain |
| `chains` | `4` | minimum for a trustworthy R-hat |
| `target_accept` | `0.92` | raise toward `0.99` to clear divergences |
| `seed` | `42` | set it, so a run is reproducible |
| `sampler` | `numpyro` | `numpyro` (JAX/GPU), `pymc`, `advi` |
| `chain_method` | `sequential` | `vectorized` is best on a single GPU — all chains in one kernel |
| `nuts_kwargs` | `{}` | extra kwargs for the NUTS kernel only |
| `prior_predictive_draws` | `500` | feeds the contraction report — **keep ≥ 500** |
| `store_log_likelihood` | `false` | needed for LOO/WAIC; makes the trace much bigger |
| `advi_iters` | `30000` | `sampler: advi` only |
| `allow_sampler_fallback` | `false` | `false` = a failed GPU run raises instead of silently burning CPU hours |

**Databricks:** `sampler: numpyro` + `chain_method: vectorized`. Check
`02_convergence/sampling_log.json` → `chain_method_applied` to confirm it took.

---

## `output` — which files get written

Core tables always write. Everything else is a flag; all default `true`.

| Stage | Flags |
|---|---|
| `01_data` | `model_input_matrix`, `model_input_summary`, `data_plots`, `collinearity`, `prior_summary` |
| `02_convergence` | `contraction_plot`, `prior_posterior_plots`, `prior_posterior_max` (60), `report_intercept` |
| `03_coefficients` | `forest_plots` |
| `04_fit` | `actual_vs_predicted`, `assumption_checks`, `fit_plots` |
| `05_contributions` | `contribution_summary`, `contribution_timeseries`, `contribution_math`, `contribution_reconciliation`, `benchmark_comparison`, `contribution_plots` |

Two that are not on/off switches:

| Key | Default | Meaning |
|---|---|---|
| `period_split` | `mat` | `none` / `week` / `year` / `mat` — reporting blocks in `contribution_summary`. `mat` = two moving-annual-total blocks, the cut vendor decks use |
| `rope_scaled` | `0.01` | region of practical equivalence. Gives `prob_negligible` — the only non-vacuous "significance" for a sign-constrained feature, whose `p_value` is 0 by construction |
| `report_intercept` | `true` | `false` hides the intercept parameters from the **contraction report only**. Never touches convergence tables or reconciliation |
| `fig_dpi` / `fig_scale` | `160` / `1.4` | every chart |

For a fast run: `contribution_timeseries: false` drops the biggest file.

---

## `assumptions` — every diagnostic threshold

All 25 thresholds are here so you never edit code to change a flag.

### Collinearity (pre-fit — what the DATA can separate)

| Key | Default | Meaning |
|---|---|---|
| `vif_warn` / `vif_bad` | `5` / `10` | the textbook thresholds |
| `cond_warn` / `cond_bad` | `10` / `30` | Belsley condition number. **This is what catches a feature that duplicates the intercept** — the centred VIF cannot |
| `pair_warn` / `pair_bad` | `0.8` / `0.95` | `abs(corr)` between design columns |
| `vif_top_k` | `3` | how many culprits to name per feature ("explained by A, B and C"). `0` = off |
| `corr_heatmap` | `true` | `01_data/collinearity_heatmap_<region>.png` |
| `heatmap_max_features` | `40` | above this a heatmap is unreadable; the highest-VIF features are kept |

> **To get the FULL correlation matrix rather than only flagged pairs, set
> `pair_warn: 0.0`.**

### Identifiability (post-fit — what the MODEL could separate)

| Key | Default | Meaning |
|---|---|---|
| `post_corr_warn` / `post_corr_bad` | `0.7` / `0.9` | `abs(corr)` between coefficient **draws**. Strongly negative = the pair is trading off |

### Residual assumptions

| Key | Default | Meaning |
|---|---|---|
| `dw_lo` / `dw_hi` | `1.5` / `2.5` | Durbin-Watson band |
| `linearity_max_corr` | `0.2` | `abs(corr(residual, fitted))` |
| `hetero_ratio_max` | `1.5` | sd(resid) top third ÷ bottom third of fitted |
| `acf_max` | `0.3` | autocorrelation at lags 2/4/13 |
| `skew_max` / `kurtosis_max` | `1.0` / `1.0` | residual shape; excess kurtosis above this recommends `student_t` |
| `influence_sd` | `3.0` | `abs(standardised residual)` counted as extreme |

### Structural

| Key | Default | Meaning |
|---|---|---|
| `exogeneity_max_lags` | `4` | cross-correlate each feature against the residual over ± this many periods |
| `exogeneity_warn` | `0.2` | flag threshold, **automatically floored at `2/√n`** |
| `confound_warn` | `0.1` | `abs(corr(treatment, control))`, also floored at `2/√n` |
| `ppp_fail` | `0.05` | aggregate posterior predictive p-value |
| `neg_baseline_review` / `_fail` | `0.2` / `0.8` | P(baseline < 0) |

> **Why the floors.** On 91 weeks the sampling sd of a correlation between two
> unrelated columns is `1/√91 ≈ 0.105`, so a flat 0.1 bar would flag about half
> of all noise pairs. Meridian's 0.1 is right for a geo × time panel with
> thousands of rows; here it is raised to two standard errors.

> **Why not Meridian's thresholds.** Meridian errors at **VIF 1000** and
> **correlation 0.999** — near-singularity gates, not collinearity checks,
> because it bets the priors regularise everything short of that. Adopting them
> would mean never flagging anything. See `MERIDIAN_ASSUMPTIONS.md`.

---

## `cv` — cross-validation

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | **off by default** — every fold is a full refit, so 5 folds ≈ 5× the run |
| `cadence` | `auto` | the preset every `null` below is filled from |
| `horizon` | `null` | test periods per fold (13 weekly / 3 monthly) |
| `n_folds` | `null` | (5 weekly / 3 monthly) |
| `step` | `null` | spacing between origins (default: horizon) |
| `min_train_periods` | `null` | (52 weekly / 12 monthly) |
| `draws` / `tune` | `null` | override sampler settings for CV speed |
| `make_plots` | `true` | the CV accuracy and stability charts |

Turn it on once the single fit looks sane. Results land in
`06_cross_validation/`, and `cv_scorecard.csv` is the row you compare between
candidate models — see `OUTPUTS_GUIDE.md` for the selection rule.

---

## Comparing to a benchmark

`05_contributions/benchmark_comparison.xlsx` is written every run. Paste the
vendor's contribution into **column E** and the gap, the ratio, `delta` and the
corrected `global_prior_mean` recalculate in the sheet.

**When the vendor combines variables** — one "Digital" line where the model
carries four placements — give it a mapping:

```yaml
data:
  benchmark_mapping: benchmark_map.csv
```

```csv
feature,benchmark_group
btl_expert-samples_premium_mat1,Samples Premium
btl_expert-samples_premium_mat2,Samples Premium
media__digital-social_...,Digital
```

Our features are then summed **within a region** before the comparison, so one
row compares to one row. Volumes and Σx add; contraction and the prior mean are
volume-weighted (they cannot be summed), and a `members` column records what
went into each row. A feature absent from the mapping is compared alone.

---

## Recipes

**Reproduce a benchmark decomposition**

```yaml
model: {include_intercept: false, fourier_order: 2}
run: {dv_center: mean, dv_scale_scope: region}
output: {period_split: mat}
```
Plus `prior_sd_basis: relative` with a tight `global_prior_sd` in the prior CSV.
Expect `contraction ≈ 0` — and say the contributions are assumptions.

**Let the data speak**

```yaml
model: {include_intercept: true, alpha_prior_sd: 0.5}
assumptions: {pair_warn: 0.5}
cv: {enabled: true}
```
Plus `global_prior_sd: 0.5` and sign constraints only.

**Diagnose a bad number**

```yaml
assumptions: {pair_warn: 0.0, vif_top_k: 5}
output: {contribution_math: true, prior_posterior_plots: true}
```

**Fastest possible run**

```yaml
output: {contribution_timeseries: false, prior_posterior_plots: false,
         collinearity: false, assumption_checks: false}
sampler: {draws: 500, tune: 500}
```

---

## Where the run records what it used

Every run writes `01_data/resolved_config.yaml` — the **effective** settings,
every default filled in, plus the path of the file it came from. That is the
record of what actually ran; diff two of them to see what changed between runs.

**Related:** `config.yaml` (the same information, one line per key) ·
`TUNING_GUIDE.md` (which lever to reach for) · `METHODOLOGY.md` (the order to
build in) · `OUTPUTS_GUIDE.md` (every file and column).
