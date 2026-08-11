# Output guide — Codebase 1 (hierarchical MMM)

Every folder, every file, every column produced by `run_pipeline.run(...)`, written
in standard MMM/econometrics language rather than PyMC vocabulary.

Illustrated throughout with the **`real_data_v2`** run: 5 retailer regions × 104 weeks
(2024-01-07 → 2025-12-28), 27 features, last 13 weeks held out.

```
outputs/<run_name>/
├── 01_data/              what actually went into the model
├── 02_convergence/       is the fit trustworthy?
├── 03_coefficients/      what did each driver do?
├── 04_fit/               how well does it track and predict?
├── 05_contributions/     where did the volume come from?
├── 06_cross_validation/  only if you run cross_validation.run_cv()
└── trace.nc              full posterior (save_trace=True)
```

**Read them in that order.** Stages 3–5 are meaningless if stage 2 failed — a
non-converged sampler produces coefficient tables that look perfectly formatted and
are entirely fictional.

> **Note on this snapshot.** The files in `snapshots/real_data_ouput_v2/` are
> screenshots/transcriptions, so some names differ from what the code writes:
> `pannel_summary` → `panel_summary.csv`, `features_scaling_stats` →
> `feature_scaling_stats.csv`, `prior_posterior_contractions` →
> `prior_posterior_contraction.csv`.

---

## Conventions used everywhere

**Two scales.** The model fits on *scaled* axes: the KPI is standardised per region
and each feature is scaled per region. So a coefficient of `0.20` means "one unit of
scaled feature moves sales by 0.20 of that region's sales standard deviation". Every
coefficient is also converted back to original units (`*_orig_units`).

**Intervals are 90% HDI**, the *shortest* interval containing 90% of the posterior —
not 5th/95th percentiles. Columns `hdi_low` / `hdi_high`.
⚠️ The one exception is `posterior_summary_full.csv`, which is raw ArviZ output and
uses **94%** (`hdi_3%` / `hdi_97%`). Don't compare the two files' intervals directly.

**Point estimate is the posterior median**, never the mean — the signed coefficients
are log-normal, so their mean sits above their median.

**Sign-constrained features** (`sign_constraint=positive/negative`) are built as
`±exp(·)` and can never cross zero. `prob_positive` and `excludes_zero` are therefore
**left blank** for them — they'd be true by construction and read as false
significance. Judge those features on effect size, HDI width and `data_support`.

**Contributions cover the whole period** (train + holdout), not the training window
only.

---

## `01_data/` — what actually went into the model

Verify this before believing anything downstream. Most modelling disasters are
visible here.

### `panel_summary.csv` — one row per region

| Column | Meaning |
|---|---|
| `region` | Region label (here: retailer account) |
| `n_obs` | Total weekly rows for this region (104) |
| `n_train` | Training periods (91) |
| `n_test` | Holdout periods (13) |
| `dv_mean`, `dv_sd` | Mean / sd of the KPI over **all** periods, original units |
| `kpi_center_used` | Mean subtracted when standardising — **train window only** |
| `kpi_scale_used` | Sd divided by when standardising — **train window only** |
| `date_min`, `date_max` | Date coverage |

**Why `dv_mean` ≠ `kpi_center_used`:** the first spans all 104 weeks, the second only
the 91 training weeks. They *should* differ slightly. If they were identical, holdout
information would be leaking into the scaling.

**This is why `dv` must arrive un-standardised.** The pipeline standardises
internally and converts every report back to original units. Pre-standardising would
make contributions and fit metrics meaningless.

### `feature_scaling_stats.csv` — one row per region × feature

| Column | Meaning |
|---|---|
| `region`, `feature` | The cell |
| `method` | `scale_only` (media-style) or `center_scale` (control-style) |
| `center` | Value **subtracted**. `0.0` for `scale_only` |
| `scale` | Value **divided by** |
| `n_active_train` | Non-zero weeks in the training window, counted on the **raw** column |

⚠️ **`center` here is a number, not the `center` flag from `feature_priors.csv`.**
The config flag chooses the method; this column reports the constant that was used.

**`method` decoding — the single most important field in stage 1:**

- **`scale_only`** — divided by the training mean of positive values, no centring.
  Zero stays zero, so "no activity" is preserved. Correct for **media**.
- **`center_scale`** — centred and standardised (mean 0, sd 1). Correct for
  **always-on level variables** (distribution, price, ACV) and for all `free` features.

A `scale_only` feature that is *always on* lands at ≈1.0 every week, which is
collinear with the region intercept — the model then can't separate its coefficient
from the baseline. That's what broke `real_data_v1` (TDP and AVP got coefficients of
+31/−33 and contributions of +91%/−97% that cancelled out). `prepare_data` now warns
when it sees this. Fix by setting `center=1` for that feature.

**A `scale` near 1e-15 means the column is numerical dust** (an adstock tail of
activity that ended before the window). The pipeline now refuses to run on these —
`v1` fitted three coupon columns of pure float noise and reported effects of 2.3e+18
per unit.

### `kpi_by_region.png`
KPI time series per region, holdout shaded orange. Look for level shifts, partial
final weeks, and whether the holdout period looks like the training period.

---

## `02_convergence/` — is the fit trustworthy?

### `sampling_log.json` — the run manifest

| Key | Meaning |
|---|---|
| `backend_info` | JAX devices seen, e.g. `['cuda:0']` = GPU was used |
| `versions` | pymc / pytensor / arviz / numpy / pandas / xarray / jax / numpyro |
| `sampler_requested` → `sampler_used` | **If these differ, a fallback happened** |
| `sampling_route` | `pymc.sampling.jax (direct)` or `pm.sample` |
| `chain_method` / `chain_method_applied` | `vectorized` runs all chains in one GPU kernel. If `applied` is `false`, the hint was silently dropped and chains ran sequentially (≈4× slower) |
| `draws`, `tune`, `chains` | 1000 / 1000 / 4 → 4000 posterior draws |
| `target_accept` | NUTS step-size target; raise toward 0.99 to kill divergences |
| `seed` | Reproducibility |
| `store_log_likelihood` | Whether pointwise log-lik was kept (needed for LOO/WAIC) |
| `allow_sampler_fallback` | `false` = a failed GPU run raises instead of burning CPU hours |
| `wall_seconds` | Sampling time (v2: 353.5s) |
| `failed_*`, `direct_route_error` | Only present when a route failed — worth reading |

### `convergence_report.txt` — the gate

| Line | Target | v2 | v1 (broken) |
|---|---|---|---|
| `max R-hat` | < 1.01 OK, > 1.05 FAIL | **1.0063** | 1.2591 |
| `min ESS (bulk)` | > 400 | **791** | 13 |
| `min ESS (tail)` | > 400 | **922** | 11 |
| `divergences` | 0 | 4 (0.1%) | 0 |
| `BFMI by chain` | > 0.3 | — | — |
| `max tree depth` | low saturation % | **0.2%** | **100%** |

- **R-hat** — do the 4 chains agree? >1.05 means they explored different places; the
  posterior is not a posterior yet.
- **ESS** — how many *independent* draws the 4000 correlated ones are worth. ESS of
  13 means your "4000 draws" carry the information of 13.
- **Divergences** — the sampler hit curvature it couldn't follow. A handful out of
  4000 (v2: 0.1%) is cosmetic; the guardrail fires above 1%.
- **Tree depth saturation** — the giveaway for a **flat ridge** (non-identified
  parameters). 100% saturation with *zero* divergences, as in v1, is the classic
  signature of two variables trading off against each other. 0.2% is healthy.

Then **Worst parameters by R-hat** (top 10) and a footer warning listing parameters
with contraction < 0.2. v2 reports 28 such parameters — nearly all `tau_*`
(cross-region spread) hyperparameters, which is expected with only 5 regions.

### `posterior_summary_full.csv` — every parameter

ArviZ summary: `mean`, `sd`, `hdi_3%`, `hdi_97%` (94% interval!), `mcse_mean`,
`mcse_sd`, `ess_bulk`, `ess_tail`, `r_hat`.

⚠️ **Do not compute `mean / mcse_mean` as a t-statistic.** MCSE shrinks as you sample
longer, so "significance" inflates just by running more draws. This was the
methodological flaw in the old `production_code.py`. Use the HDI instead.

**Parameter naming scheme** (`g` = region, `j` = feature within its bucket):

| Parameter | MMM meaning |
|---|---|
| `alpha_region[g]` | Region intercept — the baseline level |
| `mu_alpha`, `tau_alpha`, `z_alpha[g]` | Its hierarchy: grand mean, cross-region spread, region offset |
| `beta_fourier[sin_1, cos_1, …]` | Annual seasonality (shared across regions) |
| `mu_trend`, `tau_trend`, `beta_trend_region[g]` | Per-region linear trend |
| `mu_logbeta_<bucket>[j]` | Population mean effect, **log scale** (signed buckets) |
| `tau_logbeta_<bucket>[j]` | How much regions differ, log scale |
| `z_beta_<bucket>[g,j]` | Standardised region offset (non-centred parameterisation) |
| `beta_<bucket>[g,j]` | **The actual regional coefficient** — what stage 3 reports |
| `pop_beta_<bucket>[j]` | Population-level effect (median for signed buckets) |
| `sigma_region[g]` | Residual noise per region |
| `mu_log_sigma`, `tau_log_sigma` | Noise hierarchy (pooled on the log scale) |
| `nu` | Student-t degrees of freedom — low = fat tails = spiky weeks tolerated |

**Buckets** group features into one vectorised block: `h`/`g` = hierarchical/global,
then `pos`/`neg`/`free`. v2 has `hpos` (24 features), `hneg` (AVP), `hfree` (DEI),
`gfree` (Dummy).

### `prior_posterior_contraction.csv` — did the data actually say anything?

| Column | Meaning |
|---|---|
| `parameter` | Parameter name, **positionally indexed** (see below) |
| `prior_sd` | Spread before seeing data |
| `posterior_sd` | Spread after |
| `contraction` | `1 − posterior_var/prior_var` |

**Reading `contraction`:**
- **→ 1.0** — the data determined this. v2: `pop_beta_hpos[0]` (TDP) = **0.889**.
- **< 0.2** — the posterior is essentially your prior. Report it as an assumption,
  not a finding.
- **Negative** — the posterior is *wider* than the prior. Mildly negative on `tau_*`
  is normal. Strongly negative on a coefficient means **non-identification**: in v1,
  TDP was **−131** and AVP **−112** (posteriors ~130× wider than their priors). After
  centring they became +0.889 and +0.931 — the worst-informed parameters became the
  best-informed. That single flip is the proof the v1 fix worked.

⚠️ **This file uses positional indices; `posterior_summary_full.csv` uses names.**
The same parameter is `mu_logbeta_hpos[Calls]` in one file and
`mu_logbeta_hpos[3]` in the other. Index order = order in `feature_priors.csv`,
filtered to that bucket. For v2's `hpos`:

| Idx | Feature | Idx | Feature | Idx | Feature |
|---|---|---|---|---|---|
| 0 | TDP | 8 | DTV | 16 | Expert Video |
| 1 | ACV_WD_Any Merch | 9 | Ecommerce Display | 17 | MSAN |
| 2 | Shopper-Digital | 10 | Ecommerce Search | 18 | OLV |
| 3 | Calls | 11 | Ecommerce Video | 19 | Paid Search |
| 4 | Samples | 12 | Expert Display | 20 | PR |
| 5 | Audio | 13 | Expert Partnership | 21 | Social |
| 6 | Direct Partnership | 14 | Expert Search | 22 | TV_GM |
| 7 | Display | 15 | Expert Social | 23 | TV_HM |

`hneg` = AVP, `hfree` = DEI, `gfree` = Dummy (single-element buckets carry no index).

### `energy_plot.png`
Two overlaid distributions (marginal vs transition energy). Similar shapes = the
sampler is exploring the posterior's tails properly. A narrow transition
distribution inside a wide marginal one means poor exploration.

### `trace_worst_rhat.png`
Chain traces for the 3 worst-mixing parameters. Healthy = 4 overlapping "fuzzy
caterpillars". Chains sitting at different levels = the R-hat failure, visualised.

### `prior_predictive_check.png`
Simulated KPI **before seeing data** vs actual, both on the scaled axis. The prior
cloud should comfortably cover the actuals. Much wider = vague priors; not
overlapping = priors contradict the data.

---

## `03_coefficients/` — what did each driver do?

### `coefficient_report.csv`

One row per feature × region, plus a `__population__` row per feature.

| Column | Meaning |
|---|---|
| `feature`, `region` | The cell. `__population__` = market-average effect |
| `mean`, `sd` | Posterior mean and sd, scaled axes |
| `median` | **The headline estimate** |
| `hdi_low`, `hdi_high` | 90% HDI, scaled axes |
| `prob_positive` | P(effect > 0). **Blank for sign-constrained features** |
| `excludes_zero` | Whether the HDI clears zero. **Blank for sign-constrained** |
| `units` | `scaled (per-region axes)` or `scaled (population level)` |
| `sign_constrained` | `TRUE` = built as ±exp(·), so it cannot cross zero |
| `data_support` | `adequate` / `weak` / `weak (near-constant)` / `none` |
| `median_orig_units` | **KPI units per raw feature unit** — the business number |
| `hdi_low_orig_units`, `hdi_high_orig_units` | Same interval, original units |
| `orig_units_meaning` | Fixed label describing the conversion |
| `n_active_train` | Non-zero training weeks (raw column) for this region |
| `feature_sd_train` | Sd of the **scaled** feature. `1` ⇒ `center_scale` by construction |
| `scaling_method` | `scale_only` or `center_scale` — carried from stage 1 |

**Conversion:** `median_orig_units = median × kpi_scale_used / scale`. Two regions
with the same scaled coefficient will differ in original units because their KPI and
feature scales differ.

**`data_support` decoding:**
- `adequate` — genuinely estimated from this region's data.
- `weak` — fewer than 8 active weeks. Mostly shrinkage toward the population mean.
- `weak (near-constant)` — always on but barely moving under `scale_only`; the level
  is absorbed by the intercept, so only a tiny wiggle identifies the coefficient.
  **Set `center=1` for this feature.**
- `none` — never ran here, or no variation. This is the prior, not an estimate.

**Scaled vs original units — which to quote?** Scaled coefficients are comparable
*across regions* ("relative responsiveness"). Original units are the ones to put in a
business deck ("+1 TDP → +X units"). Both are exported deliberately.

### `support_warnings.txt`
The `weak`/`none` rows, extracted. Anything listed here must **not** be presented as
a regionally estimated effect. In v2 this catches `Dummy`, which has one active week
per region.

### `forest/<feature>.png`
One plot per feature: regional medians with 90% HDIs, red dashed line at the
population median. This is where **partial pooling** becomes visible — regions with
thin data get pulled toward the population line and show wide intervals.

---

## `04_fit/` — how well does it track and predict?

### `fit_metrics.csv`

One row per region × dataset (`train`, `test`), plus an `__all__` row per dataset.

| Column | Meaning |
|---|---|
| `region` | Region, or `__all__` = all regions pooled |
| `dataset` | `train` (91 weeks) or `test` (13-week holdout) |
| `r2` | Variance explained vs a single grand mean |
| `mape_pct` | Mean absolute % error |
| `wmape_pct` | Volume-weighted: total error ÷ total actual. Robust to small weeks |
| `mae` | Mean absolute error, KPI units |
| `coverage_90_mean_pct` | % of actuals inside the **mean-response** band. Expected to be **well below 90** — ignore for scoring |
| `coverage_90_pred_pct` | % inside the **posterior predictive** band. **Target ≈ 90.** This is the calibration number |
| `n` | Observations |
| `crps` | Continuous Ranked Probability Score — scores the whole predictive distribution (accuracy *and* sharpness). Lower better; KPI units |
| `r2_within_region` | `__all__` rows only — variance explained vs **each region's own mean** |
| `mape_region_weighted_pct` | `__all__` rows only — volume-weighted average of per-region MAPEs |

⚠️ **Read `r2_within_region`, not `r2`, on the `__all__` row.** Pooled `r2` measures
against one grand mean, so it is inflated by the (trivially predictable) differences
in level between retailers. v2 shows `r2 = 0.994` on train while
`r2_within_region = 0.595` and individual regions range 0.33–0.82. The 0.994 is an
artefact; 0.595 is the truth.

**Negative R² is legitimate**, not a bug: it means the model predicts that window
worse than that window's own mean would. On a holdout it signals a level or trend
shift the model didn't anticipate.

**v2 holdout readout:** `r2_within_region = −0.65`, `wmape = 5.01%`,
`coverage_90_pred = 73.8%` against a target of 90 — the model is both biased and
overconfident out of sample. Worst region is Target-Corp (MAPE 13.5%, coverage 38%).
This is a *specification* issue (linear trend extrapolating downward + only two
Fourier harmonics for a Q4 holdout with one observed Q4 in the data), separate from
the v1 collinearity problem, which is fixed.

### `actual_vs_fitted.png`
Per region: actual (black), fitted median (blue), 90% mean-response band (dark) and
90% predictive band (light), holdout shaded orange. In v2 every region's fitted line
drifts **down** through the holdout while actuals recover — the visual form of the
negative test R².

### `residuals.png`
Left: residuals vs fitted — should be a shapeless cloud around zero. Funnelling means
non-constant variance; curvature means a missing non-linearity. Right: residual
histogram — should be roughly symmetric around zero.

---

## `05_contributions/` — where did the volume come from?

### `contribution_totals.csv`

One row per feature × region, plus `__portfolio__` (all regions summed) per feature,
plus a `__baseline__` feature. **Totals span the whole period**, in original KPI units.

| Column | Meaning |
|---|---|
| `feature` | Driver, or `__baseline__` = intercept + seasonality + trend |
| `region` | Region, or `__portfolio__` = summed across regions |
| `mean`, `sd`, `median` | Total contribution over all periods, KPI units |
| `hdi_low`, `hdi_high` | 90% HDI of that total |
| `prob_positive`, `excludes_zero` | Blank for sign-constrained features |
| `sign_constrained` | `TRUE`/`FALSE`; blank on `__baseline__` |
| `contribution_vs` | **`zero`** or **`feature average`** — see below |
| `share_of_actual_pct` | `median ÷ actual sales for that region (or total)` × 100 |

⚠️ **`contribution_vs` changes the question being answered.**

- **`zero`** (media, all `scale_only` features) — "how much volume did this activity
  add versus not running it?" The familiar MMM contribution.
- **`feature average`** (all `center_scale` features: TDP, AVP, ACV, DEI, Dummy) —
  "how much did this driver's *movement around its own average* add or remove?"
  A region whose distribution ran below its own average shows a **negative**
  contribution even though its coefficient is positive. That is not a contradiction.

**Why centred features can't be measured versus zero:** the counterfactual "zero
distribution" never occurs in the data, and its level is inseparable from the region
intercept — that level sits in `__baseline__`. Attempting to measure it is exactly
what produced v1's fictional +91% TDP. Meridian treats its non-media treatments the
same way.

**v2 sanity check** — `share_of_actual_pct` on `__portfolio__`:
baseline 91.5% + media ≈7% + AVP −0.38% + TDP +0.16% ≈ **98% of actual sales**.
The ~2% gap is normal (medians don't sum exactly, and the model slightly
under-predicts). If your columns sum to something far from 100%, something is wrong.
For contrast, v1 read: baseline 94% + TDP **+91%** + AVP **−97%**.

### `contribution_bars.png`
Top 20 features by absolute portfolio contribution, with 90% HDIs. Blue = positive,
red = negative. Bars whose interval spans a wide range are not yet pinned down.

### `decomposition_area.png`
Weekly stacked decomposition across the portfolio, positive contributions above zero
and negative below, actual sales as the black line. Use it to see *when* each driver
worked, and whether negative drivers (price) move against volume as expected.

---

## `06_cross_validation/` — only if you run `run_cv()`

Not in this snapshot. The single 13-week holdout is one origin and one slice of the
seasonal cycle — a smoke test, not validation. `cross_validation.run_cv()` refits on
expanding windows and writes `cv_fold_metrics.csv`, `cv_summary.csv`,
`cv_coefficient_stability.csv`, `cv_stability_ranking.csv`, `cv_report.md` and
per-feature stability plots. **`cv_coefficient_stability.csv` is the one that matters
for business use:** a coefficient that swings across folds cannot support a budget
decision, however tight its HDI looks in a single fit.

---

## `trace.nc`

Full posterior in NetCDF (ArviZ `InferenceData`): posterior draws, sample statistics,
prior and prior-predictive groups. Reload with `arviz.from_netcdf("trace.nc")` to
compute anything not in the standard reports without refitting. Large — this is the
raw material, not a report.

---

## Health checklist for any run

| # | Check | File | Pass |
|---|---|---|---|
| 1 | Sampler and device as intended; `chain_method_applied` true | `sampling_log.json` | — |
| 2 | max R-hat < 1.01; min ESS > 400; tree-depth saturation low | `convergence_report.txt` | v2 ✅ |
| 3 | No strongly negative contraction on any `pop_beta_*` | `prior_posterior_contraction.csv` | v2 ✅ |
| 4 | No `scale` ≈ 1e-15; level variables show `center_scale` | `feature_scaling_stats.csv` | v2 ✅ |
| 5 | No unexpected `weak`/`none` support flags | `support_warnings.txt` | v2 ✅ |
| 6 | Contributions sum to ≈100% of actual; no huge offsetting pairs | `contribution_totals.csv` | v2 ✅ |
| 7 | `coverage_90_pred_pct` ≈ 90 on the holdout | `fit_metrics.csv` | v2 ❌ (74%) |
| 8 | `r2_within_region` positive on the holdout | `fit_metrics.csv` | v2 ❌ (−0.65) |
| 9 | Coefficients stable across CV folds | `cv_coefficient_stability.csv` | not yet run |

**v2 status:** the model is now trustworthy for *inference* (checks 1–6 pass — you can
read and defend the coefficients and the decomposition), but not yet for *forecasting*
(checks 7–8 fail). Fixing the collinearity fixed the inference; the holdout miss is a
separate specification question. Next step is check 9 — expanding-window CV — rather
than tuning against this one holdout.
