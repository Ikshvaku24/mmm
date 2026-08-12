# Reading the model outputs — `real_data_v3`

Every folder, every file, every column, and what the numbers actually mean.
All example values are the real ones from the `real_data_v3` run
(5 retailers × 104 weeks, 2024-01-07 … 2025-12-28, 27 features, 13-week holdout).

> **Anything marked 🆕 does not exist in the v3 files** — columns and whole
> files alike. They were added after that run and will appear next time you
> execute the pipeline. Everything else is exactly what is sitting in your v3
> folders. The 🆕 files are the *reconciliation* outputs: they exist so every
> number in the core tables can be re-derived by hand from the data.

---

## Folder map

| Folder | Question it answers | Look here when |
|---|---|---|
| `01_data` | What actually went into the model? | Numbers look strange — check scaling first (🆕 `model_input_matrix.csv` is the row-by-row answer) |
| `02_convergence` | Can I trust the sampler at all? | **Always read this first** |
| `03_coefficients` | What did each driver do, per retailer? | Building the effect story |
| `04_fit` | How well does it predict? | Judging model quality |
| `05_contributions` | How do sales split across drivers? | Building the business deck |
| `trace.nc` | The raw posterior (all draws) | Re-analysis without re-fitting |

**Read them in this order: 02 → 04 → 03 → 05.** If convergence failed, nothing
downstream means anything. If the fit is poor, the contributions are a story
about a model that doesn't describe your business.

---

## Choosing which files get written — `OutputConfig`

The **core tables are always written**: `panel_summary.csv`,
`feature_scaling_stats.csv`, `coefficient_report.csv`, `fit_metrics.csv`,
`contribution_totals.csv`, `contribution_by_pillar.csv`. Everything else is a
**reconciliation output** and is switched on or off in `config.OutputConfig`,
passed to `run(...)` as `out_cfg=`.

```python
from config import OutputConfig
run(df, model_cfg, run_cfg, sampler_cfg, out_cfg=OutputConfig(period_split="mat"))

OutputConfig()                                   # everything on (the default)
OutputConfig(contribution_timeseries=False)      # skip the one large file
OutputConfig.tables_only()                       # every CSV, no PNGs
OutputConfig.core_only()                         # nothing optional
OutputConfig.core_only(contribution_summary=True)  # only the volume table
```

| Flag | File(s) | Stage |
|---|---|---|
| `model_input_matrix` | `model_input_matrix.csv` | 01 |
| `model_input_summary` | `model_input_summary.csv` | 01 |
| `data_plots` | `kpi_by_region.png` | 01 |
| `forest_plots` | `forest/*.png` | 03 |
| `actual_vs_predicted` | `actual_vs_predicted.csv` | 04 |
| `fit_plots` | `actual_vs_fitted.png`, `residuals.png` | 04 |
| `contribution_summary` | `contribution_summary.csv` | 05 |
| `contribution_timeseries` | `contribution_timeseries.csv` | 05 |
| `contribution_math` | `contribution_math.csv` | 05 |
| `contribution_reconciliation` | `contribution_reconciliation.csv` | 05 |
| `contribution_plots` | the four 05 PNGs | 05 |

Two options rather than switches:

- **`period_split`** — `"mat"` (default), `"week"`, `"year"` or `"none"`.
  Controls the reporting periods in `contribution_summary.csv`; a **Total**
  block is always emitted alongside.

  | Value | Blocks on the real 104-week panel |
  |---|---|
  | `"mat"` | **MAT 1** = 2024-01-07…2024-12-29, **MAT 2** = 2025-01-05…2025-12-28, + Total — the same cut as `snapshots/true_output/contribution_summary.png` |
  | `"week"` | one block per date (104) + Total — **the weekly detail report** |
  | `"year"` | 2024 + 2025 + Total |
  | `"none"` | Total only |

  **How `"mat"` cuts a panel that isn't exactly 104 weeks.** The two MAT
  windows are anchored on the **last** date and are always the same length, so
  MAT 1 and MAT 2 are always comparable:

  | Panel | Result |
  |---|---|
  | exactly 104 weeks | MAT 1 = first 52, MAT 2 = last 52 |
  | **more** than 104 | MAT 2 = last 52, MAT 1 = the 52 before, everything older → one **`Pre-MAT`** block. 130 weeks ⇒ Pre-MAT (26) + MAT 1 (52) + MAT 2 (52) |
  | **fewer** than 104 | no full year to roll, so split in half: MAT 1 = older half, MAT 2 = recent half. 80 weeks ⇒ 40 + 40; an odd period goes to MAT 1 |

  `Pre-MAT` is deliberately *not* folded into MAT 1 — that would make MAT 1 an
  unequal window and a MAT-on-MAT volume comparison would be meaningless. It is
  also not split into further year blocks; if you have several years of history
  and want each one, use `period_split="year"`. `n_periods` always states the
  block length, so an unequal split is never silent. A monthly panel rolls
  12 + 12 rather than 52 + 52 — the period length is inferred from the data.

  `"week"` multiplies the file by roughly the number of dates — for the real
  panel (27 features × 5 regions × 104 weeks) that is ~23k rows, ~2.5 MB. Still
  a normal spreadsheet, but see the note under `contribution_summary.csv` on
  reading weekly percentages.
- **`include_raw_features`** — also dump the pre-scaling feature values into
  `model_input_matrix.csv`. Doubles that file's width; leave it on unless size
  matters, because without it the scaling cannot be checked.

### The reconciliation chain

Each optional file evidences one link between the raw data and a percentage in
the deck:

| Link | File |
|---|---|
| raw feature → scaled feature | `01_data/model_input_matrix.csv`, `model_input_summary.csv` |
| scaled feature → contribution | `05_contributions/contribution_math.csv` |
| contributions → fitted sales | `05_contributions/contribution_reconciliation.csv` |
| fitted sales → actual sales | `04_fit/actual_vs_predicted.csv` |
| everything → volume and % | `05_contributions/contribution_summary.csv` |

### Two arithmetics — the reason numbers sometimes look 0.1% off

| | What it is | Where |
|---|---|---|
| **median of the total** | posterior median of a feature's whole-window total, **with an HDI** | `contribution_totals.csv` |
| **sum of the medians** | per-week posterior median, summed — these **do** add up week by week, so they reconcile exactly, but a sum of medians has no honest interval | every file listed above |

Both are printed side by side in `contribution_math.csv`
(`volume_median_of_total` vs `volume_sum_of_medians`) with the difference, so
the size of the gap is visible rather than assumed. It is normally < 0.5%.
Quote `contribution_totals.csv` when you need uncertainty; quote
`contribution_summary.csv` when the numbers have to add up on a slide.

---

## The one thing to understand first: scaled vs original units

The model does not work in dollars. Internally:

- **KPI (`dv`)** is standardised per region: `y = (sales − mean_g) / sd_g`, using
  **training-window** statistics only. So a coefficient of `0.30` means
  *"moves that region's sales by 0.30 of its own standard deviation"*.
- **Features** are scaled per region in one of two ways (`01_data/feature_scaling_stats.csv`):
  - `scale_only` — divided by the mean of positive values. Zero stays zero.
    Used for media: "0 spend" is a meaningful point.
  - `center_scale` — centred *and* scaled (z-scored). Used for controls and for
    always-on level variables (TDP, AVP). Zero is no longer meaningful, so
    effects are measured against the feature's **average level**.

Because of this, **every coefficient appears twice**: on the scaled axis
(comparable across regions) and converted back to original units. Contributions
are always reported in real KPI units.

---

## `01_data` — what went into the model

### `panel_summary.csv`

One row per region.

| Column | Meaning |
|---|---|
| `region` | Retailer account |
| `n_obs` / `n_train` / `n_test` | Total / training / holdout weeks. v3: `104 / 91 / 13` |
| `dv_mean`, `dv_sd` | Raw sales mean and sd over **all** weeks |
| `kpi_center_used` | The mean actually subtracted (training window only) |
| `kpi_scale_used` | The sd actually divided by (training window only) |
| `date_min`, `date_max` | Date span |

**How to read it.** `dv_mean` and `kpi_center_used` differ slightly
(1,762,267 vs 1,762,931 for Walmart) — that is correct and expected: the first
covers all 104 weeks, the second only the 91 training weeks. If they were
identical, the holdout would be leaking into the scaling.

### `feature_scaling_stats.csv`

One row per **region × feature** — 27 × 5 = 135 rows.

| Column | Meaning |
|---|---|
| `region`, `feature` | The cell |
| `method` | `scale_only` or `center_scale` (see above) |
| `center` | Value subtracted (`0` for `scale_only`) |
| `scale` | Value divided by |
| `n_active_train` | Non-zero weeks in training, counted on the **raw** column |

**How to read it.** `scale` is your sanity check on the data. A scale of `1e-15`
means the column is floating-point noise, not data — the pipeline now refuses to
run on those (this is what killed the three Coupon variables). `n_active_train`
tells you how much evidence exists: `Dummy` has **1**, so its coefficient rests
on a single week per region.

### 🆕 `model_input_matrix.csv` — **the data as the model sees it**

One row per region × week: the transformed matrix handed to the sampler, with
the raw values beside it. 520 rows for the real panel.

| Column | Meaning |
|---|---|
| `region`, `date`, `dataset` | The cell, and `train` / `test` |
| `dv_raw`, `dv_scaled` | Sales before and after standardisation |
| `dv_center_used`, `dv_scale_used` | The region's training mean and sd |
| `trend_t` | The trend regressor, 0…1 over the training window |
| `sin_1`, `cos_1`, `sin_2`, `cos_2` | The Fourier seasonality regressors |
| `<feature>__raw` | The column after dust-zeroing, before scaling |
| `<feature>__scaled` | **The number the coefficient multiplies** |

**How to read it.** Everything is reproducible from this file plus
`feature_scaling_stats.csv`, in a spreadsheet:

```
<feature>__scaled = (<feature>__raw − center) / scale     (center = 0 for scale_only)
dv_scaled         = (dv_raw − dv_center_used) / dv_scale_used
```

This is where to look first when a contribution's size is surprising. Sort a
centred feature's `__scaled` column: it will run roughly −2…+2 and average zero,
which is why its contribution over the window is near zero however large its
coefficient is.

### 🆕 `model_input_summary.csv`

One row per region × feature — the same 135 cells as
`feature_scaling_stats.csv`, but describing the **scaled** column.

| Column | Meaning |
|---|---|
| `pillar`, `sign`, `pooling`, `baseline`, `contribution_reference` | Config as applied |
| `scaling_method`, `center_used`, `scale_used` | The transform |
| `n_train`, `n_obs`, `n_active_train` | Support |
| `raw_mean_train`, `raw_sd_train`, `raw_min`, `raw_max`, `raw_sum_all` | The raw column |
| `scaled_mean_train`, `scaled_sd_train`, `scaled_mean_all` | The scaled column |
| `scaled_min`, `scaled_max`, `scaled_sum_all` | Range and total |
| `pct_weeks_negative_scaled` | 0 for `scale_only`, ~50 for centred features |
| `mean_zero_by_construction` | TRUE for every `center_scale` feature |
| `scaled_value_reads_as` | Plain-English meaning of a value in that column |

**How to read it.** `scaled_mean_train` is the punchline. For a `center_scale`
feature it is **exactly 0** and `scaled_sd_train` is **exactly 1** — that is what
centring means, and it is why the level of that driver lives in the intercept
rather than in its own contribution. For a `scale_only` feature the mean is
around 1 in active weeks and never negative, so its contribution is a genuine
"versus no activity" increment.

### `kpi_by_region.png`

Weekly sales per retailer, holdout shaded orange. Use it to spot level shifts,
outlier weeks and partial final weeks before trusting anything else.

---

## `02_convergence` — can I trust the sampler?

### `convergence_report.txt` — **read this first, every time**

v3 values:

```
max R-hat        : 1.0058  (OK)
min ESS (bulk)   : 1052    (OK)
min ESS (tail)   : 1345    (OK)
divergences      : 7       (INVESTIGATE)
max tree depth   : 8       (OK)
```

| Statistic | What it is | Pass mark |
|---|---|---|
| **R-hat** | Do the 4 chains agree? Ratio of between- to within-chain variance | < 1.01 good, > 1.05 **fail** |
| **ESS (bulk)** | Independent-equivalent draws for the centre of the posterior | > 400 |
| **ESS (tail)** | Same, for the interval endpoints — governs HDI stability | > 400 |
| **Divergences** | Steps where the sampler hit curvature it couldn't follow | 0 ideal; < 1% of draws tolerable |
| **Tree depth** | Sampler steps per iteration; "saturated" = it ran out of room | Not saturated |

**How to read v3.** All green. The 7 divergences are 0.18% of 4,000 draws — flagged
because it isn't zero, but far below the 1% guardrail. Tree depth 8 with no
saturation is the important one: in v1 it was **saturated in 100% of steps**,
which is the signature of a flat ridge in the posterior (the TDP/AVP
collinearity). That is gone.

The report ends with the 10 worst parameters by R-hat and a warning listing
parameters whose posterior barely moved from the prior.

### `posterior_summary_full.csv`

ArviZ's per-parameter table: `mean`, `sd`, `hdi_3%`, `hdi_97%`, `mcse_mean`,
`mcse_sd`, `ess_bulk`, `ess_tail`, `r_hat`.

Parameter naming, so you can find things:

| Name | Meaning |
|---|---|
| `mu_alpha`, `tau_alpha`, `z_alpha` | Region intercept hierarchy (population, spread, offsets) |
| `alpha_region` | The intercept each region ended up with |
| `beta_fourier` | Seasonality coefficients (`sin_1`, `cos_1`, …) |
| `mu_trend`, `tau_trend`, `beta_trend_region` | Linear trend per region |
| `mu_logbeta_hpos[f]` | Population **log** effect of feature `f`, positive bucket |
| `tau_logbeta_hpos[f]` | How much regions differ on `f` |
| `z_beta_hpos[g, f]` | Region `g`'s standardised offset for `f` |
| `beta_hpos[g, f]` | The final coefficient for region `g`, feature `f` |
| `pop_beta_hpos[f]` | Population-level effect (the median, for signed features) |
| `mu_log_sigma`, `tau_log_sigma`, `sigma_region` | Residual noise per region |
| `nu` | Student-t degrees of freedom (fat-tail control) |

Bucket suffixes: `h`/`i`/`g` = hierarchical / independent / global pooling;
`pos`/`neg`/`free` = sign constraint.

> ⚠️ **Do not use `mcse_mean` for significance.** It shrinks as you draw more
> samples, so anything divided by it becomes "significant" simply by sampling
> longer. This was the flaw in the old `production_code.py`. Use `t_stat` and the
> HDI in the coefficient report instead.

### `prior_posterior_contraction.csv`

| Column | Meaning |
|---|---|
| `parameter` | Which parameter |
| `prior_sd` | Spread before seeing data |
| `posterior_sd` | Spread after |
| `contraction` | `1 − (posterior_var / prior_var)` |

**How to read it.** Near **1** = the data determined this number. Near **0** =
the posterior is just your prior, so report it as an assumption, not a finding.
**Negative** = the posterior came out *wider* than the prior, which means the
data is fighting the model — in v1 TDP scored **−131** and AVP **−112**; after
the centring fix they became **+0.89** and **+0.93**, the best-informed
parameters in the model. This file is the fastest way to spot a broken spec.

### `energy_plot.png`, `trace_worst_rhat.png`, `prior_predictive_check.png`

- **Energy** — the two histograms should overlap. A narrow marginal against a
  wide transition distribution means the sampler is exploring badly.
- **Trace** — the 4 chains for the worst-R-hat parameters. Want a fuzzy
  caterpillar; want *not* to see chains sitting at different levels.
- **Prior predictive** — sales the model generates *before seeing data*. If it
  produces impossible values, the priors are wrong on their scale.

### `sampling_log.json`

The run manifest: package versions, `backend_info` (v3: `cuda:0`),
`sampler_requested` vs `sampler_used`, `chain_method_applied`, `sampling_route`,
seed, draws/tune/chains, `target_accept`, `wall_seconds` (v3: 348.8s).

**Check `sampler_used` and `chain_method_applied` every run** — a successful run
does not by itself prove the GPU was used.

---

## `03_coefficients` — what each driver does

### `coefficient_report.csv`

One row per feature × region, plus a `__population__` row per feature.

**Effect size and uncertainty**

| Column | Meaning |
|---|---|
| `mean`, `sd`, `median` | Posterior summary on the **scaled** axis. Use `median` |
| `hdi_low`, `hdi_high` | True 90% highest-density interval (shortest interval, not percentiles) |
| `t_stat` | `mean / sd` — signal-to-noise. **Not** mean/mcse |
| `p_value` | `2 × min(P(β>0), P(β<0))` — probability the *direction* is wrong. Blank for sign-constrained features |
| `prob_positive`, `excludes_zero` | Blank for sign-constrained features — true by construction there |
| `sign_constrained` | TRUE if built as ±exp(·), so it can never cross zero |

**Original units**

| Column | Meaning |
|---|---|
| `median_orig_units` | KPI units per raw feature unit |
| `hdi_low_orig_units`, `hdi_high_orig_units` | Same, as an interval |
| `orig_units_meaning` | Label for the above |

**Evidence quality**

| Column | Meaning |
|---|---|
| `n_active_train` | Non-zero training weeks (raw column) |
| `feature_sd_train` | Sd of the scaled feature. `1.0` = centred; small = barely moves |
| `scaling_method` | `scale_only` / `center_scale` |
| `data_support` | `adequate` / `weak` / `weak (near-constant)` / `none` |

**How to read it.** Start with `data_support`. Anything not `adequate` is
essentially the prior wearing a coefficient's clothes — the hierarchy supplied it
by shrinkage, that region's data did not. Then compare `median` against
`hdi_low`/`hdi_high`: a wide interval spanning an order of magnitude means "we
can't tell", regardless of what `t_stat` says.

For sign-constrained features, **ignore significance entirely** — `p_value` is
blank because it is 0 by construction. Judge those on effect size and interval
width.

### `support_warnings.txt`

Plain-language list of every region × feature with `none` or `weak` support.
Anything appearing here must not be presented as a regionally estimated effect.

### `forest/<feature>.png`

Per feature: each region's coefficient with its 90% HDI, and a dashed line at the
population median. Shows shrinkage directly — regions with thin data sit close to
the dashed line because the hierarchy pulled them there.

---

## `04_fit` — how well does it predict?

### `fit_metrics.csv`

Rows: `__all__` (pooled), 🆕 `__aggregate__`, then one per region — each for
`train` and `test`.

| Column | Meaning | v3 (`__all__`, train / test) |
|---|---|---|
| `r2` | Pooled across regions — **inflated**, see below | 0.994 / 0.977 |
| `r2_within_region` | R² against each region's **own** mean — the honest one | **0.596 / −0.662** |
| `mape_pct` | Mean absolute % error | 2.62 / 5.73 |
| `wmape_pct` | Volume-weighted % error | 2.35 / 5.01 |
| `mape_region_weighted_pct` | Volume-weighted average of per-region MAPEs | 2.34 / 4.94 |
| `mae` | Mean absolute error, KPI units | 24,028 |
| `coverage_90_mean_pct` | % of actuals inside the **mean-response** band — expected well below 90 | 43.7 |
| `coverage_90_pred_pct` | % inside the **posterior predictive** band — **judge holdout by this**, target ~90 | 91.2 / 73.8 |
| `crps` | Proper score combining calibration and sharpness; lower better | 17,690 |
| `resid_t_stat` | t test of H₀: mean residual = 0 | 0.843 |
| `resid_p_value` | p for that test — small = systematic bias | 0.400 |
| `durbin_watson` | Residual autocorrelation: 2 = none, < 1.5 = positive | 1.63 |
| `n` | Observations | 455 / 65 |

**How to read v3 — this is the important part.**

- **`r2` 0.994 is not a good number, it is a misleading one.** It pools five
  retailers whose sales levels differ by 3×, so most of the "explained variance"
  is just the trivially predictable gap between Walmart and Sam's Club. Always
  quote **`r2_within_region` = 0.596** instead.
- **Test `r2_within_region` = −0.662 is a genuine failure.** Negative means the
  model predicts the holdout *worse than each region's own holdout average*.
  With `coverage_90_pred_pct` falling from 91% to 74%, the model is both biased
  and overconfident out of sample.
- `resid_p_value` 0.400 on training means no significant in-sample bias — good.
  Check the **test** rows: bias there is what the holdout drift shows.
- `durbin_watson` 1.63 signals mild positive autocorrelation, typically a missing
  trend or seasonal term.
- 🆕 The `__aggregate__` row sums all regions to one national series per date and
  scores that. It is the only like-for-like comparison against a national
  total-sales model. It will always look better than the per-region rows because
  aggregation cancels idiosyncratic noise — quote it *beside* them, never
  instead of them.

### `actual_vs_fitted.png`

Per region: actual (black), fitted (blue), a dark band (mean response) and a pale
band (posterior predictive), holdout shaded orange. **Judge holdout coverage
against the pale band.** In v3 every region's fitted line drifts downward through
the orange window while actuals recover — that is the predictive weakness the
metrics report.

### 🆕 `actual_vs_predicted.csv`

`actual_vs_fitted.png` as data — one row per region × week, so any metric in
`fit_metrics.csv` can be checked against the observations that produced it.

| Column | Meaning |
|---|---|
| `region`, `date`, `dataset`, `period` | The cell, `train`/`test`, and the MAT block |
| `actual`, `fitted` | Sales, and the posterior **median** prediction |
| `residual` | `actual − fitted` — what the decomposition can never explain |
| `abs_pct_error` | Per-week APE; average it to get `mape_pct` |
| `fitted_lo90_mean`, `fitted_hi90_mean` | Mean-response band (the dark band) |
| `pred_lo90`, `pred_hi90` | Posterior predictive band (the pale band) |
| `inside_pred_90` | TRUE/FALSE; the mean of this column **is** `coverage_90_pred_pct` |
| `baseline`, `baseline_core` | Baseline level that week, and its core |
| `incremental` | `fitted − baseline` — everything attributed to switchable drivers |
| `baseline_pct_of_actual` | Baseline as a share of that week's sales |

**How to read it.** Filter `dataset == "test"` and sort by date: the holdout
drift shows up as a run of same-signed `residual`. `inside_pred_90` tells you
exactly which weeks fell outside the band rather than just how many.

### `residuals.png`

Residual vs fitted, and a residual histogram. Want a shapeless cloud centred on
zero; funnel shapes mean non-constant variance, curvature means a missing term.

---

## `05_contributions` — how sales split across drivers

### `contribution_totals.csv`

Contributions in **real KPI units**, per feature per region, plus a
`__portfolio__` row (all regions).

| Column | Meaning |
|---|---|
| `feature` | Driver, or `__baseline__` / `__baseline_core__` |
| `region` | Region or `__portfolio__` |
| `group` | `baseline_total` / `baseline_part` / `incremental` |
| 🆕 `pillar` | Reporting group (Online Media, TV & Digital TV, Expert, …) |
| `mean`, `sd`, `median`, `hdi_low`, `hdi_high` | Posterior summary of the total |
| `sign_constrained` | TRUE if the coefficient can't cross zero |
| `contribution_vs` | `zero` or `feature average` — **the counterfactual** |
| 🆕 `volume` | **The volume contribution** — identical to `median`, named for what it is |
| 🆕 `volume_hdi_low`, `volume_hdi_high` | Same as `hdi_low`/`hdi_high` |
| 🆕 `volume_units` | `KPI units, summed over the window` |
| 🆕 `avg_volume_per_period` | `volume ÷ number of weeks` — the per-week rate |
| 🆕 `actual_volume` | That region's actual sales, i.e. the denominator |
| `share_of_actual_pct` | `volume ÷ actual_volume × 100` |

**How the parts add up** (exactly, per posterior draw):

```
__baseline__  =  __baseline_core__  +  Σ baseline features    (group = baseline_part)
total sales   =  __baseline__       +  Σ incremental features (group = incremental)
```

`__baseline_core__` is region intercept + seasonality + trend. Baseline features
deliberately appear **twice** — inside `__baseline__` and on their own row — which
is what makes the baseline expandable. **Filter on `group` before summing, or you
will double count.**

**`contribution_vs` is the column people misread.** For a centred feature it says
`feature average`, meaning the contribution answers *"what did this driver's
movement add relative to its own typical level?"* — not *"what would we lose if it
went to zero?"*. That is why a **positive** coefficient can show a **negative**
contribution: most weeks sat below average. This is why v3 shows TDP at +0.16%
while the vendor decomposition shows Distribution at +25.5% — different questions,
both correct. Setting `contribution_reference=zero` in `feature_priors.csv`
switches TDP/AVP to the vendor's convention without changing the fit at all.

v3 portfolio values: `__baseline__` **91.13%**, `__baseline_core__` 91.35%,
TDP +0.16%, ACV −0.004%, Samples 1.65%, Display 1.32%.

> Medians of components only add *approximately* — the identity above is exact
> draw by draw, but the median of a sum isn't the sum of medians. Small
> reconciliation gaps in the median column are expected, not a bug.

### 🆕 `contribution_summary.csv` — **volume and % that add up to 100**

The vendor deck's layout, as data:
`snapshots/true_output/contribution_summary.png` reproduced from our own model.
Rows per (`period`, `region`): the actual sales line, every driver inside its
pillar with a subtotal, a Residual line, and a Grand Total **equal to actual
sales**.

| Column | Meaning |
|---|---|
| `period` | `MAT 1` / `MAT 2` / `Total` (plus `Pre-MAT` on panels longer than 104 weeks), or an ISO date under `period_split="week"` |
| `n_periods` | Weeks in that block — 52 / 52 / 104, or 1 for a weekly block |
| `region` | Region or `__portfolio__` |
| `pillar`, `feature` | Reporting group and driver |
| `group` | `baseline_core` / `baseline_part` / `incremental` / `residual` |
| `row_type` | `actual` / `component` / `pillar_total` / `grand_total` |
| `volume` | **Volume contribution in KPI units** |
| `contribution_pct` | `volume ÷ actual sales × 100` |
| `avg_volume_per_period` | `volume ÷ n_periods` |

**How to read it.** Filter `region == "__portfolio__"`, `period == "Total"` and
you have the vendor table. Sum `contribution_pct` over `row_type == "component"`
and you get **exactly 100.00%**, because:

```
components (baseline core + every driver)  =  fitted sales
fitted sales  +  Residual                  =  actual sales
```

**The Residual line is what makes the percentages close.** Drivers explain
*fitted* sales; `actual − fitted` has to appear somewhere or the column stops at
99.x%. It also absorbs the small median gap (see "two arithmetics" above). The
vendor sheet carries a Residual line for the same reason — theirs is −0.01%.

### Weekly detail — `period_split="week"`

One block per date, same layout, and each week still reconciles to exactly
100.00%. Use it to see *when* a driver contributed rather than how much it
contributed overall — sorting one feature's rows by `period` gives its weekly
volume series with a percentage attached.

**Read weekly percentages differently from window percentages.** Over 104 weeks
the residual averages out to ~0.1% of sales; in a single week it is routinely
±5%, so `__baseline_core__` can print above 100% and other rows negative in the
same block. That is the week's residual, not a broken decomposition — the Grand
Total is still exactly 100%. Judge drivers on the `Total` block and use weekly
blocks for timing.

If you only want the weekly driver series without the actual / pillar-total /
Grand Total rows, `contribution_timeseries.csv` carries the same volumes in a
narrower file (186 KB vs 440 KB on the test panel).

**Why the percentages sum to a large positive number even though the features
are centred.** They sum to 100% because `__baseline_core__` — the region
intercept plus seasonality plus trend — carries essentially the whole level of
sales (v3: 91%). The centred features contribute only their *deviations* from
their own average, which nearly cancel over the window, so they sit near 0%.
Nothing sums to more than 100%: the intercept is doing the work. Set
`contribution_reference=zero` on TDP/AVP and the level moves out of the
intercept and into those two rows, which is the vendor convention — the total
stays exactly 100% either way, because it is an algebraic restatement of the
same fit, not a refit.

### 🆕 `contribution_reconciliation.csv` — **does it add up?**

One row per `scope` (`all` / `train` / `test`) × region, plus `__portfolio__`.
This is the file to open when a number looks wrong.

| Column | Meaning |
|---|---|
| `actual_volume`, `fitted_volume` | Actual sales, and the median prediction |
| `baseline_core_volume` | Intercept + seasonality + trend |
| `baseline_features_volume` | Features flagged `baseline=1` (TDP, AVP) |
| `baseline_total_volume` | The two above combined |
| `incremental_volume` | Every non-baseline driver |
| `sum_components_volume` | Core + all features |
| `median_gap_volume` | `fitted − sum_components` — the sum-of-medians gap, should be tiny |
| `residual_volume` | `actual − fitted` — genuine unexplained sales |
| `*_pct` | Each of the above as % of actual |
| `reconciles_to_actual_pct` | **Always exactly 100** — if not, something is broken |

```
sum_components + median_gap = fitted
fitted         + residual   = actual
```

### 🆕 `contribution_timeseries.csv` — the weekly decomposition as data

`decomposition_area.png` in a form you can pivot. One row per region × week ×
component: `region, date, dataset, period, pillar, feature, group, volume`.

`feature` also carries four reference rows per week — `__median_gap__`,
`__fitted__`, `__actual__`, `__residual__` — so a pivot reconciles exactly:

```
sum(volume where group in baseline_core/baseline_part/incremental/median_gap)
    = __fitted__
__fitted__ + __residual__ = __actual__
```

This is the largest file in the run (~16k rows for the real panel). Turn it off
with `OutputConfig(contribution_timeseries=False)` if you don't need it.

### 🆕 `contribution_math.csv` — the arithmetic behind every number

One row per region × feature. Every contribution in this codebase is

```
volume = beta_scaled × SUM(x_scaled + reference_shift) × dv_scale_used
```

and this file prints each factor so it can be recomputed in a spreadsheet.

| Column | Meaning |
|---|---|
| `scaling_method`, `center_used`, `scale_used` | The transform applied |
| `contribution_reference`, `reference_raw_value` | The counterfactual, in raw units |
| `reference_shift_scaled` | The per-week shift re-referencing adds. **0 when `auto`** |
| `raw_sum`, `raw_mean_train` | The raw column |
| `scaled_sum`, `scaled_mean_train` | The scaled column — **~0 for centred features** |
| `effective_scaled_sum` | `scaled_sum + shift × n_obs` |
| `beta_scaled_median`, `dv_scale_used` | The other two factors |
| `volume_recomputed` | The product of the three — do this in Excel and match it |
| `volume_sum_of_medians` | What `contribution_summary.csv` reports |
| `volume_median_of_total` | What `contribution_totals.csv` reports |
| `recomputed_diff_pct`, `median_basis_diff_pct` | The two gaps, as % |

**How to read it.** This is the direct answer to "how can a centred feature
contribute anything?". For TDP with `contribution_reference=auto`,
`beta_scaled_median` is large (≈0.3) but `scaled_sum` is ≈0, so the product is
≈0. Switch to `zero` and `reference_shift_scaled` becomes `center ÷ scale` per
week — a large positive number × 104 weeks — and the same coefficient now
produces the level contribution the vendor reports. The coefficient never
changed; only the question did.

### 🆕 `contribution_by_pillar.csv`

The same numbers rolled up by `pillar` (Baseline / Trade / Expert / Online Media /
Price Promotions / TV & Digital TV), which is the format vendor decks use — so the
two can be placed side by side. It sums `baseline_total` + `incremental` only, so
it is already double-count safe. Carries `median`, 🆕 `volume` (the same figure)
and `share_of_actual_pct`.

For the full vendor layout — pillar subtotals, a Residual line and a Grand Total
that ties to actual sales — use `contribution_summary.csv` instead.

### The three charts

| File | Shows |
|---|---|
| `contribution_bars.png` | Incremental drivers ranked, with 90% HDIs. Baseline parts excluded — they are not switchable levers and would dwarf everything |
| `baseline_breakdown.png` | What the baseline is made of (core + each baseline feature) |
| `decomposition_area.png` | Weekly stacked decomposition, baseline as **one block** — the business view |
| `decomposition_area_expanded.png` | Same, with the baseline opened into core + baseline features |

---

## `trace.nc`

The full posterior in NetCDF. Reload with
`arviz.from_netcdf("trace.nc")` to compute anything not in the reports without
re-running the 6-minute fit.

---

## Triage checklist

1. **`02_convergence/convergence_report.txt`** — R-hat < 1.01, ESS > 400, tree
   depth not saturated. If not, stop; nothing else is meaningful.
2. **`prior_posterior_contraction.csv`** — any negative contraction means a
   broken specification, not a weak prior.
3. **`04_fit/fit_metrics.csv`** — read `r2_within_region`, not `r2`. Check
   `coverage_90_pred_pct` on the test rows.
4. **`03_coefficients/support_warnings.txt`** — remove anything listed from your
   narrative.
5. **`05_contributions/contribution_totals.csv`** — filter on `group`, and check
   `contribution_vs` before quoting any share.
6. **`05_contributions/contribution_reconciliation.csv`** — `reconciles_to_actual_pct`
   must be 100 and `median_gap_pct` near 0. Then read `residual_pct`: that is how
   much of actual sales the decomposition simply does not explain.

## "That contribution can't be right" — where to look

| Symptom | File | What to check |
|---|---|---|
| A share looks far too small | `contribution_math.csv` | `scaled_sum` ≈ 0 means the feature is **centred**; the level is in the intercept. Set `contribution_reference=zero` if you want the vs-nothing number |
| A positive coefficient shows a negative contribution | `model_input_summary.csv` | Centred feature: most weeks sat below its average. `contribution_vs` says `feature average` |
| Percentages don't sum to 100 | `contribution_summary.csv` | You dropped the Residual line, or summed `baseline_part` rows alongside `__baseline__` |
| Two files disagree by ~0.1% | `contribution_math.csv` | `volume_median_of_total` vs `volume_sum_of_medians` — see "two arithmetics" |
| A scaling factor looks wrong | `model_input_matrix.csv` | Recompute `(raw − center) / scale` and compare with `__scaled` |

## Known gaps in this run

- **No Category or Competition variable.** The reference vendor models carry
  Category at ~55% and Competition at ~−5%. Without them, that variation is
  absorbed by the latent trend and seasonality, which inflates the baseline and
  suppresses measured media effects.
- **Coupon-Digital / Coupon-FSI / Coupon-Ibotta are excluded** because every
  non-zero value in the datacube is ~1e-15. The vendors report real contributions
  for these, so this is a broken data extract, not three dead channels.
- **Holdout prediction is weak** (`r2_within_region` −0.66, coverage 74%). The
  single 13-week holdout lands entirely in Q4, and with two years of data there is
  only one prior Q4 to learn holiday seasonality from. Use the expanding-window CV
  in `cross_validation.py` before drawing conclusions from this one split.
