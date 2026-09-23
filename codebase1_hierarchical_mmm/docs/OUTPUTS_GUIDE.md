# Reading the model outputs — `real_data_v3`

Every folder, every file, every column, and what the numbers actually mean.
All example values are the real ones from the `real_data_v3` run
(5 retailers × 104 weeks, 2024-01-07 … 2025-12-28, 27 features, 13-week holdout).

> **Anything marked 🆕 does not exist in the v3 files** — columns and whole
> files alike. They were added after that run and will appear next time you
> execute the pipeline. Everything else is exactly what is sitting in your v3
> folders. The 🆕 files are the *reconciliation* outputs: they exist so every
> number in the core tables can be re-derived by hand from the data — plus the
> two folders added since: `00_warnings/` (every warning, grouped by category
> instead of printed once per feature) and `06_cross_validation/` (written only
> when `cv.enabled: true`).

Which files a run writes is controlled by `config.yaml`; see
`docs/TUNING_GUIDE.md` for which setting to reach for when a number looks wrong.

---

## Folder map

| Folder | Question it answers | Look here when |
|---|---|---|
| 🆕 `00_warnings` | What did the run object to? | **Read the index first** — one document per category, not one paragraph per feature |
| `01_data` | What actually went into the model? | Numbers look strange — check scaling first (🆕 `model_input_matrix.csv` is the row-by-row answer) |
| `02_convergence` | Can I trust the sampler at all? | **Always read this first** |
| `03_coefficients` | What did each driver do, per retailer? | Building the effect story |
| `04_fit` | How well does it predict? | Judging model quality |
| `05_contributions` | How do sales split across drivers? | Building the business deck |
| 🆕 `06_cross_validation` | Does it hold up on data it has not seen, and are the coefficients stable? | Before anyone acts on a ROI number. Written only when `cv.enabled: true` |
| `trace.nc` | The raw posterior (all draws) | Re-analysis without re-fitting |

**Read them in this order: 00 → 02 → 04 → 03 → 05 (→ 06).** A high-severity
warning usually explains the thing you were about to spend an hour diagnosing.
If convergence failed, nothing downstream means anything. If the fit is poor,
the contributions are a story about a model that doesn't describe your business.

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
| `collinearity` | `collinearity_summary.csv`, `_vif.csv`, `_pairs.csv`, `collinearity_heatmap_<region>.png` | 01 |
| `prior_summary` | `prior_summary.csv` | 01 |
| *(always, when run from YAML)* | `resolved_config.yaml` | 01 |
| `contraction_plot` | `prior_posterior_contraction.png` | 02 |
| `prior_posterior_plots` | `prior_posterior/<param>.png` | 02 |
| `report_intercept` | *(hides rows, writes no file — see below)* | 02 |
| `forest_plots` | `forest/*.png` | 03 |
| `actual_vs_predicted` | `actual_vs_predicted.csv` | 04 |
| `assumption_checks` | `assumption_checks.csv`, `posterior_correlation.csv`, `assumptions_report.md` | 04 |
| `fit_plots` | `actual_vs_fitted.png`, `residuals.png` | 04 |
| `contribution_summary` | `contribution_summary.csv` | 05 |
| `contribution_timeseries` | `contribution_timeseries.csv` | 05 |
| `contribution_math` | `contribution_math.csv` | 05 |
| `contribution_reconciliation` | `contribution_reconciliation.csv` | 05 |
| `benchmark_comparison` | `benchmark_comparison.xlsx` (or `.csv`) — regions across the columns; group + member rows and pre-filled benchmarks from `data.mapping_file` | 05 |
| `contribution_plots` | the four 05 PNGs | 05 |
| *(always)* | `00_warnings/*` | 00 |
| `cv.enabled` **(CVConfig, not OutputConfig)** | everything in `06_cross_validation/` | 06 |

`report_intercept` is the odd one out: it writes no file, it **removes rows**.
`false` drops `mu_alpha` / `tau_alpha` / `z_alpha` / `alpha_region` from
`prior_posterior_contraction.csv` and its per-parameter charts, so a nuisance
level does not crowd out the drivers in the diagnostic you actually read. It
never touches the convergence tables — an intercept with a bad R-hat is never
hidden — and never touches the decomposition, so contributions still reconcile.

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

  **The MAT length follows the cadence.** `OutputConfig(cadence=...)` sets it
  explicitly (`weekly` → 52, `monthly` → 12); `auto` takes it from the run's
  `PeriodPlan`, and failing that infers it from the date spacing. So a 24-month
  panel rolls **12 + 12**, not 52 + 52.

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
- **`rope_scaled`** — the region of practical equivalence, on the scaled
  coefficient axis. Drives `prob_negligible`; set `0` to skip.

### Figure size and resolution

Every chart in every stage goes through `plotting.py`, so two knobs change all
of them at once:

```python
OutputConfig(fig_dpi=220, fig_scale=1.8)   # big, for a wall or a projector
OutputConfig(fig_dpi=110, fig_scale=1.0)   # small, to fit more on a page
```

| Option | Default | Effect |
|---|---|---|
| `fig_dpi` | `160` | Output resolution. 160 stays legible pasted into a deck at half width |
| `fig_scale` | `1.4` | Multiplies every figure's width and height |

Both apply to `01_data`, `02_convergence`, `03_coefficients`, `04_fit`,
`05_contributions` **and** the CV stability charts. Every axis carries its
quantity *and its unit*, every colour/line style has a legend entry, and most
figures carry a footnote saying which axis the numbers live on — raw, scaled,
or original KPI units. That distinction is the one that has caused every
scaling bug in this project's history, so it is stated on the picture rather
than left to the reader.

### `prior_summary.csv`

**What each written prior actually means as a coefficient distribution.** The
prior file is written in convenient units; the model samples something else. For
a sign-constrained feature it samples `beta = ±exp(Normal(mu, sigma))`, so
*neither* `global_prior_mean` nor `global_prior_sd` is the number PyMC sees.

| Column | Meaning |
|---|---|
| `feature`, `region` | `__population__` = the feature-level prior; then one row per region |
| `is_region_override` | `True` where a per-region row in the CSV supplied it |
| `distribution` | `lognormal(+)` / `lognormal(-)` / `normal` |
| `input_prior_mean`, `input_prior_sd`, `input_regional_sd` | **exactly what is in the CSV** |
| `prior_sd_basis`, `prior_mean_basis` | how those inputs were interpreted |
| `mu_log`, `sigma_log`, `regional_sd_log` | **the parameters handed to PyMC** |
| `implied_median`, `implied_mean`, `implied_sd` | back-transformed to coefficient units |
| `implied_q05`, `implied_q95` | 90% prior interval on the coefficient |
| `implied_rel_sd` | `implied_sd / implied_mean` — the realised "±x%" |

**Two questions this answers that the prior file cannot.**

1. *"I wrote `prior_sd=0.2` — is the coefficient really ±20%?"* Read
   `implied_rel_sd`. Under `prior_sd_basis="log"` writing 0.2 gives 0.2020, and
   the hand-rounded 0.198 gives 0.19996; under `"relative"` it is exactly
   0.20000. Under the mistake that broke v5 (`0.2 × prior_mean`) it reads 0.003.
2. *"Is my `prior_mean` the median or the mean?"* Both are printed. They differ
   by `exp(sigma²/2)` — 2% at `sigma=0.2`, 12% at 0.5, **33% at 0.9** — so on a
   wide prior the gap is not a rounding detail.

Compare `implied_median` here against `median` in `coefficient_report.csv` to
see how far the data moved each coefficient, and against
`prior_posterior_contraction.csv`'s `mean_shift_in_prior_sd` for whether that
move is large relative to the prior's own width.

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

## Scaling — and why it silently rescales your priors

Every column, features and KPI alike, goes through one helper:

```
scaled = (raw − centre) / scale        # both from run.scaling_window (train by default)
```

| Knob | Where | Values |
|---|---|---|
| feature `center_mode` | `feature_priors.csv` | `none`, `mean` |
| feature `scale_mode` | `feature_priors.csv` | `none`, `sd`, `mean`, `mean_positive`, `max` |
| `dv_center` | `RunConfig` | `none`, `mean` |
| `dv_scale` | `RunConfig` | `none`, `sd`, `mean`, `mean_positive`, `max` |
| `dv_scale_scope` | `RunConfig` | `region` (default), `global` |

A blank feature `center_mode` / `scale_mode` means **`none` / `none`**: the
column passes through **completely untouched** and priors are per raw unit.
Centring and scaling are opt-in per feature. `run.scaling_window` (`train` |
`full`) sets which periods every centre and scale is computed on, the KPI's
included.

> ⚠️ **The scale is the unit of your priors.** A coefficient means *"moves the
> KPI by `beta × dv_scale` per `feature_scale` of input"*. Change either scale
> and **every prior mean must be divided by the same factor**. If you don't,
> the decomposition will still reconcile to exactly 100% — and be wrong by a
> constant multiple. `contribution_reconciliation.csv` **cannot** catch this;
> only comparing `contribution_math.csv` against a known contribution can.
>
> **`dv_scale_scope`** is the one to watch. `region` gives each region its own
> scale, so coefficients are comparable across regions in relative terms.
> `global` divides every region by one number, so a small region's scaled KPI
> collapses and its coefficients must shrink to match. If your priors were
> derived per region (`contribution ÷ Σx ÷ mean_dv_region`) then `global` scope
> inflates every contribution by `global_scale / mean_dv_region` — on the BMC
> panel that was 1.2×, 8.3× and 14.9× for the three sub-brands.
>
> **Never edit one side of the transform.** The inverse is
> `fitted = y_scaled × y_scale + y_mean`, and both halves come from the same
> place. Setting `dv_center="none"` sets `y_mean` to 0 so the inverse stays
> correct; deleting the centring from the forward pass alone inflates every
> fitted value by the region mean (MAPE ≈ 100%, `r2_within_region` ≈ −116).

**`prior_sd` is on the log scale for sign-constrained features** — `0.7` ≈ a
factor of 2, `0.2` ≈ ±20%, `0.01` ≈ ±1%. Setting it as a *fraction of the mean*
(`0.2 × prior_mean`) pins the coefficient so tightly the data cannot move it;
the posterior comes back as the prior and the contributions are simply whatever
the priors implied. The run now warns when `prior_sd < 0.05`.

---

## 🆕 `pre_model_outputs/` — before the model runs at all

Written whenever there is something to write: `data.mapping_file` carries
contributions, `data.share_file` is given, or **`data.feature_priors` is not
set yet** — in which case the step writes the SKELETON (one row per datacube
variable, means and signs blank) and the run stops so you can fill it in.

| File | Contents |
|---|---|
| `feature_priors_national.csv` | a generated prior file — **a proposal, not a decision**. One row per model variable, the **national** mean (see below); blank where neither file covered it. `pooling: hierarchical` |
| `feature_priors_regional.csv` | the same rows **plus one override row per region** (`variable, region, global_prior_mean` only) with that region's own coefficient. `pooling: independent` |
| `prior_calculation.xlsx` | sheet 1 the working, sheet 2 the resulting means, sheet 3 the method in prose |

Point `data.feature_priors` at whichever matches your pooling decision, after
reviewing it. Both leave `scale_mode` and `contribution_reference` blank —
blank is `none` and `auto`, the units the means were derived in — and are only
in the model's units with `run.dv_scale: mean`; the step warns otherwise.

**How to read the calculation sheet.** Every row shows `C / support / dv_agg`
(signed) with the numbers substituted, so a prior can be checked rather than
believed. That is `prior_mean_region` — the region's coefficient, and its row in
the regional file.

- **case a** (vendor contribution) — one row per mapping **group** × region:
  `members` names the variables combined, `contribution_basis` says whether the
  number was regional or a national figure allocated by support.
- **cases b/c** (shares) — one row per variable × region: `section`, `pillar`,
  `share_of_sales`, `spend_split` (spend / computed pillar spend, media and
  expert only) and `share_formula`.

`usable` and `skipped_because` say why any cell was left out — almost always
zero support, meaning the variable never ran in that region.

Sheet 2 shows the national arithmetic: `national_coef = sum_of_region_coefs /
n_regions_used` — the divisor is the number of regions **with support**, not the
region count (2 of 5 regions with support → divide by 2). `global_prior_mean` is
its magnitude for a signed variable, the signed value for a `free` one.

It also prints the other aggregation next to it: `national_coef_average`,
`national_coef_weighted` (`Σ contribution / Σ(support × dv_agg)` — the one that
reproduces the national total, for `pooling: global`), `weighted_over_average`
(the ratio) and `national_basis` (which one was written). A ratio far from 1
means the contribution is concentrated in a few regions, and the run warns:
that factor is exactly the gap a national correction would chase and never
close (`METHODOLOGY.md` §2d).

Method and every edge case: `FEATURE_PRIOR_GUIDE.md` §5.

---

## 🆕 `00_warnings` — what the run objected to

Almost every check in this codebase fires **per feature**. With 65 features,
one wrong column in the prior file used to produce 65 near-identical paragraphs
in the notebook — which meant nobody read any of them. The run now captures its
warnings, groups them by category, and writes one document per category. The
console gets **one line per category**, not one per feature.

Nothing is suppressed: every warning still reaches `all_warnings.csv` verbatim.

| File | Contents |
|---|---|
| `00_INDEX.md` | **Read this first.** Counts per category, sorted by severity, each linking to its document |
| `<category>.md` | One per category: what it means, why it fires, what to do, and the table of affected features |
| `all_warnings.csv` | Every warning as one row — for filtering and for diffing two runs |

### `all_warnings.csv`

| Column | Meaning |
|---|---|
| `category` | Category slug, matching the `.md` filename |
| `severity` | `high` (a reported number is probably not measuring what you think) / `medium` (worth knowing) / `review` (uncategorised — nobody has triaged this yet) |
| `feature` | The feature the warning is about, parsed off the front of the message. Blank for run-level warnings |
| `region` | The region, when the warning was region-specific |
| `warning_class` | Python warning class (`UserWarning`, …) |
| `source` | `file.py:line` that raised it |
| `message` | The full text, whitespace-normalised |
| `detail` | The text with the `feature:` prefix stripped — what goes in the per-category table |

### The categories

| Slug | Severity | Fires when |
|---|---|---|
| `prior_pins_coefficient` | high | `prior_sd` converts to a log-scale sigma < 0.05, so the posterior ≈ the prior. **The most common one**, and usually `0.2 * prior_mean` written where `prior_sd=0.2` + `prior_sd_basis=relative` was meant |
| `prior_mean_not_a_magnitude` | high | A sign-constrained feature was given `prior_mean <= 0`. The value was **replaced by a default** — the model is not using your number |
| `negative_values_uncentred` | high | A sign-constrained feature has negative values but is scaled without centring |
| `collinear_with_intercept` | high | An always-on feature is ~constant after scaling and fights the region intercept. This is the defect that broke `real_data_v1` |
| `degenerate_feature_column` | high | A column is constant, empty or non-positive over the training window; the scale fell back to 1.0 |
| `intercept_without_centering` | high | `include_intercept: false` with `dv_center: none` — nothing is left to carry the level |
| `pooling_collapsed` | medium | `regional_sd_prior` < 0.02, so hierarchical pooling is one shared coefficient in all but name |
| `per_region_prior_sd_ignored` | medium | A per-region `prior_sd` was written but hierarchical pooling ignores it |
| `seasonality_overfit_risk` | medium | `fourier_order` is high for the number of training periods |
| `cadence_ambiguous` | medium | Date spacing is neither weekly nor monthly; monthly was assumed |
| `scaling_uses_holdout` | review | `run.scaling_window: full` with a holdout — the holdout rows of `fit_metrics.csv` saw the scaling statistics of the test window. Judge out-of-sample accuracy with CV |
| `generated_prior_units` | high | The pre-model step generated priors per unit of the region's MEAN KPI, but `run.dv_scale` is not `mean` (or the scope is not `region`). Every generated mean would be off by a constant multiple |
| `generated_prior_blank` | medium | Some variables got no generated mean — in neither input file, or no support anywhere |
| `regional_prior_sign_skipped` | review | A region's implied coefficient runs against its variable's sign, so the regional file has no row for it |
| `other` | review | Matched no rule — read it in full rather than assuming it is routine |

**How to read it.** `high` means a number in your report is probably not
measuring what you think. `prior_pins_coefficient` in particular is the
"agreement trap": a feature that matches a benchmark *because the posterior is
the prior you derived from that benchmark* has not been validated by anything.
Cross-check its `contraction` in `02_convergence` — near 0 confirms the model
learned nothing about it.

An empty run still writes `00_INDEX.md` saying so; a missing file would be
ambiguous ("did it not run, or was it clean?").

---

## `01_data` — what went into the model

### `resolved_config.yaml`

Written when the run was started from a settings file (`run_from_yaml`). It is
the **effective** configuration — every default filled in, not just the keys the
YAML mentioned — plus `_source`, the path of the file it came from.

**How to read it.** This is the answer to "what settings produced these
numbers?" six months from now. Diff two runs' `resolved_config.yaml` to see
exactly what changed between them; the feature-level priors are not in here,
they are in the run's `prior_summary.csv`.

### 🆕 `collinearity_summary.csv` / `_vif.csv` / `_pairs.csv`

**Nothing measured collinearity before this.** The old `near_constant_sd` guard
caught one special case; the general case — two features that move together,
whose coefficients are then identified only as a sum — was invisible, and
reconciliation cannot catch it because the sum is right.

Measured on the **model's own design matrix**: the scaled features *plus* the
intercept, the Fourier block and the trend, per region, training window only.
A raw feature correlation matrix cannot see a feature that duplicates the
intercept, a "promo" flag that is really December, or a channel that only ran
during a growth phase.

**`collinearity_summary.csv`** — one row per region.

| Column | Meaning |
|---|---|
| `n_obs`, `n_columns` | size of the design |
| `condition_number` | Belsley condition number of the unit-length design. **10 warn, 30 severe** |
| `max_vif` | worst textbook (centred) VIF. 5 warn, 10 severe |
| `max_vif_uncentred` | worst VIF with the mean kept in |
| `worst_column` | which feature that was |
| `n_vif_over_10`, `n_duplicating_intercept`, `n_pairs_over_0.8` | counts |
| `verdict` | `ok` / `moderate` / `severe` |

**Why two VIF columns.** The textbook centred VIF is **structurally blind to
collinearity with the intercept**. On the exact `real_data_v1` case — an
always-on feature scaled to ~1.0 every week — the centred VIF read **1.09**,
looking perfect, while the condition number read **23,000**. `_vif.csv` has a
`duplicates` column saying which kind you have: `other features` (high centred
VIF) or `the intercept/level` (high uncentred, low centred → set
`center_mode=mean`).

**`collinearity_pairs.csv`** — column pairs above |r| = 0.8, worst first.
Columns are named `__intercept__`, `__fourier__*`, `__trend__` or the feature
name, so you can see *what* a feature is collinear with.

### `panel_summary.csv`

One row per region.

| Column | Meaning |
|---|---|
| `region` | Retailer account |
| `n_obs` / `n_train` / `n_test` | Total / training / holdout weeks. v3: `104 / 91 / 13` |
| `dv_mean`, `dv_sd` | Raw sales mean and sd over **all** weeks |
| `kpi_center_used` | The mean actually subtracted (over `run.scaling_window` — the training window by default) |
| `kpi_scale_used` | The scale actually divided by (same window) |
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

🆕 **Four columns make this file usable rather than merely complete**, and one
filter removes rows that could never mean anything:

| Column | Meaning |
|---|---|
| `feature` / `region` | the label **split apart**, so it joins against every other output. The raw coordinate arrives as `feature @ ('Core',)` — the tuple repr is stripped |
| `scale` | `log` or `natural` |
| `use_for_delta` | **TRUE on exactly ONE parameter family per feature** — the one to multiply a contribution by |

**Which parameter, and why it matters.** For a signed feature the model samples
`eta ~ Normal(mu, sigma)` and forms `beta = ±exp(eta)`. The identity the whole
diagnosis rests on —

```
mu_post = (1 − contraction)·mu_prior + contraction·mu_likelihood
%diff   = exp(mu_post − mu_prior) − 1
```

— is a statement about a **Normal** parameter. It holds on `eta` (the log scale)
and **not** on `beta`, which is lognormal. So:

| pooling | sign | the parameter to use | scale |
|---|---|---|---|
| global | signed | `glogbeta_<bucket>` | log |
| global | free | `gbeta_<bucket>` | natural |
| hierarchical | signed | `mu_logbeta_<bucket>` | log |
| hierarchical | free | `mu_beta_<bucket>` | natural |
| independent | signed | `logbeta_<bucket>` | log |

`beta_<bucket>` is a **deterministic** transform, reported for reference only.
Under `pooling=global` it is also identical in every region (one shared
coefficient broadcast), which is why those rows used to fill the file with
duplicates. `tau_*` is the cross-region *spread*, not the location, so it is
never the delta parameter either.

🆕 **Rows with no data support are dropped.** A region × feature pair where the
feature never ran contributes exactly 0 there, so its contraction describes a
coefficient that multiplies nothing. The count is printed rather than silently
hidden.



| Column | Meaning |
|---|---|
| `parameter` | `variable[name]`, e.g. `mu_logbeta_hpos[TDP]` |
| `variable` | The model parameter block alone |
| `name` | **The feature / region name**, from the model coords |
| `role` | What kind of parameter it is (see below) |
| `prior_mean` / `posterior_mean` | Centre before / after seeing data |
| `prior_sd` / `posterior_sd` | Spread before / after |
| `contraction` | `1 − (posterior_var / prior_var)` — *did the data sharpen it?* |
| `mean_shift_in_prior_sd` | `(posterior_mean − prior_mean) / prior_sd` — *did the data move it?* |
| `informative` | `False` for `z_*` non-centred offsets (`N(0,1)` by construction) |

**Two questions, two columns — you need both.** A parameter can contract hard
around a value nowhere near the prior mean you supplied, and contraction alone
will not tell you.

- **`contraction`** — near **1** = the data determined this number. Near **0** =
  the posterior is just your prior, so report it as an assumption, not a
  finding. **Negative** = the posterior came out *wider* than the prior, which
  means the data is fighting the model — in v1 TDP scored **−131** and AVP
  **−112**; after the centring fix they became **+0.89** and **+0.93**.
- **`mean_shift_in_prior_sd`** — this is the **prior-data conflict** statistic
  and the one to open when *a posterior median disagrees with the prior mean you
  supplied*. `|shift| > 2` gets a warning in `convergence_report.txt`. It means
  either the prior is genuinely wrong, or — far more often — something upstream
  is feeding the model **different units than the prior was built on**. Both v4
  and v5 failures would have shown up here.

**`role` values:** `population mean, LOG scale` · `cross-region spread, LOG
scale` · `population effect` · `coefficient (global pooling…)` · `coefficient
(independent pooling…)` · `coefficient (region level)` · `seasonality` ·
`trend` · `intercept` · `noise` · `non-centred offset`.

> Anything whose name contains **`logbeta`** is on the **log** scale, so a shift
> of 0.7 there is a **factor of 2** in KPI units, not 0.7 of one.

**Every** parameter shared by the prior and posterior groups is now listed.
Before v7 a prefix allowlist silently dropped `pooling="global"` coefficients
(`gbeta_*` / `glogbeta_*`), `pooling="independent"` coefficients (`beta_*` /
`logbeta_*`) and `beta_fourier` — so a config where every dummy was `global`
produced a contraction file with none of its features in it. Filter on `role`
or `informative` instead.

### `prior_posterior/<param>.png` — the three-curve chart

One per parameter: **Prior** (what you asserted), **Data** (what the data alone
says) and **Posterior** (the two combined).

The likelihood is not stored by the sampler. For a Gaussian prior and an
approximately Gaussian posterior it is recovered exactly:

```
1/sd_like²  = 1/sd_post² − 1/sd_prior²
mu_like     = sd_like² × (mu_post/sd_post² − mu_prior/sd_prior²)
```

| What you see | Meaning |
|---|---|
| Data narrow, near Prior | the data agrees with you |
| Data narrow, far from Prior | the data disagrees **and wins** |
| Data very wide | the posterior **is** your prior |
| **No Data curve** | posterior wider than prior — **unidentified** |
| Data far, Posterior stuck near Prior | prior fighting data |

The annotation box carries `contraction`, `mean_shift_in_prior_sd`, a plain-words
verdict, and for log-scale parameters the coefficient in original units.

Only informative parameters are drawn (`z_*` offsets are `N(0,1)` by
construction), only the parameter block rather than its identical per-region
copies, ordered by |mean shift| and capped at `OutputConfig.prior_posterior_max`
(default 60).

**`docs/TUNING_GUIDE.md` explains what to change once you have read one.**

### `prior_posterior_contraction.png`

Two panels, sorted worst-first, `z_*` offsets excluded:

- **Left — how much the data sharpened each parameter.** Bars are `contraction`;
  red below 0.2 (prior-dominated), amber to 0.5, green above.
- **Right — where the data moved it**, in prior sds, with red guides at ±2.

A parameter that is **green on the left and red on the right** is the dangerous
one: the data was confident, and confident about something your prior did not
predict. Check the scaling before you accept the number.

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
| `p_value` | `2 × min(P(β>0), P(β<0))` — probability the *direction* is wrong |
| 🆕 `p_value_basis` | `posterior sign` or `sign-constrained (vacuous)` — **read this before the p_value** |
| `prob_positive`, `excludes_zero` | P(β>0) and whether the HDI clears zero |
| 🆕 `rope_scaled` | The practical-equivalence threshold used (`OutputConfig.rope_scaled`, default 0.01) |
| 🆕 `prob_negligible` | **P(\|β\| ≤ rope)** — the meaningful significance number for a sign-constrained feature |
| `sign_constrained` | TRUE if built as ±exp(·), so it can never cross zero |

> 🆕 **These columns used to be blank for sign-constrained features. They are
> now always populated.** The blanks were correct in spirit and useless in
> practice: with 26 of 27 features sign-constrained, the report had a `p_value`
> column that was empty almost everywhere.
>
> **But read `p_value_basis` first.** For a sign-constrained feature `β = ±exp(·)`
> cannot cross zero, so `p_value` is **exactly 0** and `prob_positive` is
> **exactly 1** — for every such feature, in every region, always. Those numbers
> are properties of the model's construction, not evidence from your data, and
> quoting them as significance would be wrong.
>
> **`prob_negligible` is the honest replacement.** For a sign-constrained
> coefficient the question was never "is the effect non-zero?" (guaranteed) but
> **"could the effect be so small it doesn't matter?"** `prob_negligible` is the
> share of the posterior inside a region of practical equivalence — near **0**
> means the effect is materially bigger than nothing, near **1** means the model
> cannot rule out that the driver does essentially nothing. Set the threshold
> with `OutputConfig(rope_scaled=…)` on the scaled axis (0.01 ≈ "moves sales by
> under 1% of a region's standard deviation").

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

### 🆕 `assumption_checks.csv` + `posterior_correlation.csv` + `assumptions_report.md`

The classical regression assumptions, checked against this model. They do not
invalidate standard errors in a Bayesian fit — they show up as
**misspecification**: intervals too narrow, coefficients trading off,
contributions that reconcile perfectly and are still wrong.

**`assumption_checks.csv`** — one row per region × check.

| Column | Meaning |
|---|---|
| `region`, `check` | which region, which assumption |
| `statistic` | the measured value |
| `threshold` | what would count as acceptable |
| `verdict` | `ok` / `warn` |
| `what_it_means` | why the assumption matters |
| `what_to_do` | the lever that addresses it |

Checks: linearity, homoscedasticity (sd of residuals in the top vs bottom third
of fitted values — more powerful and more interpretable than a correlation),
Durbin-Watson, ACF at lags 2/4/13, residual skew, excess kurtosis, and count of
observations beyond 3 sd.

**`posterior_correlation.csv`** — the check the pre-fit statistics cannot make:
correlation between two coefficients' **posterior draws**, per region. Strongly
negative means they are trading off — the model knows their sum, not their
split, so neither contribution is readable alone.

> **Read it next to `contraction`.** High VIF + *low* posterior correlation +
> `contraction` near 0 is the dangerous combination: a tight prior pinned both
> coefficients so they look fine, while the data never separated them. The
> split is your assumption, not a finding.

**🆕 `confounding_pairs.csv`** — correlation between every incremental feature
and every `baseline=1` feature, per region. Adopted from Meridian's
`PotentialBiasCheck`, which flags at |r| ≥ 0.1 because this is the closest
computable stand-in for exogeneity: a media coefficient correlated with a
control may be absorbing that control's effect, and no goodness-of-fit number
will say so. **The threshold is raised to `max(0.1, 2/√n)`** — on 91 weeks the
sampling SD of a correlation between unrelated columns is 0.105, so Meridian's
flat 0.1 (fine on a geo × time panel with thousands of rows) would flag about
half of all noise pairs. `threshold_used` and `n_obs` record what was applied.

**🆕 `structural_checks.csv`** — two more from Meridian, per region plus
`__all__`:

| Column | Meaning |
|---|---|
| `ppp` | aggregate posterior predictive p-value: how extreme the observed TOTAL is under the posterior predictive. **Fails below 0.05.** Pointwise coverage cannot see a model whose weekly intervals are fine but whose annual total sits in its own tail — which is the shape of a decomposition that reconciles to 100% and is still wrong |
| `p_negative_baseline` | P(total baseline < 0). Review at 0.2, fail at 0.8. A negative baseline says sales would be negative with no marketing — that is drivers over-claiming with the baseline absorbing the offset, which this project has seen |

**🆕 `exogeneity_cross_correlation.csv`** — **this is the exogeneity test.**
`confounding_pairs.csv` above is a property of the *design*; it never touches
the error term. This one cross-correlates every feature against the model's
**residual** at leads and lags.

| Column | Meaning |
|---|---|
| `region`, `feature`, `lag` | the cell. Lag ranges over ±`assumptions.exogeneity_max_lags` |
| `correlation` | corr(feature at t, residual at t+lag) |
| `n_obs`, `threshold_used` | the sample and the bar applied (floored at 2/√n) |
| `by_construction` | TRUE at lag 0 |
| `reading` | what a flag at this lag means |
| `flag` | `review` when the bar is crossed at a non-zero lag |

**Why lag 0 is useless and the other lags are not.** For a regressor that is
*in* the model, the fit drives `corr(x_t, e_t)` to ~0 whether or not the true
error is independent of it. So the obvious test — correlate the feature with the
residual — cannot work, and a zero there proves nothing. What the fit does not
force to zero:

- **lag > 0** (feature leads the error): activity today predicts what the model
  gets wrong later — usually a carryover/adstock length that is wrong.
- **lag < 0** (error leads the feature): **spend responding to sales.** Budget
  released after a good quarter, rescue spend after a bad one. This is the
  endogeneity that biases MMM coefficients.

Purely **contemporaneous** simultaneity — spend set this week from a forecast of
this same week — stays untestable from residuals. It needs an instrument or an
experiment.

**`assumptions_report.md`** pulls all of it together and closes with the
assumption no statistic can check — **exogeneity**. Spend follows expected
sales; omitted category/competitor drivers load onto whatever correlates with
them. Only geo experiments, holdout regions or switchback tests settle that.

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

**Two lines close the gap to 100%, and they mean different things:**

| Row | Is | v4 portfolio |
|---|---|---|
| `__residual__` | `actual − fitted` — sales the **model** cannot explain | **0.51%** |
| 🆕 `__median_gap__` | `fitted − Σ(component medians)` — a **reporting** artefact, see "two arithmetics" above | **1.71%** |

Drivers explain *fitted* sales, so `actual − fitted` has to appear somewhere or
the column stops at 99.x%. The vendor sheet carries a Residual line for the same
reason — theirs is −0.01%.

> 🆕 **These were one combined row until v5.** On the real panel that row read
> **2.23%**, of which only 0.51% is genuine model error — the other 1.71% is the
> sum-of-medians artefact, which grows with the number of components (~30 here,
> nearly all log-normal and therefore right-skewed, so each median sits a little
> below its mean and the shortfalls accumulate one way). Reporting them together
> overstated the model's error roughly fourfold. Judge the fit on `__residual__`
> alone, and cross-check it against `residual_pct` in
> `contribution_reconciliation.csv`.

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
| 🆕 `scaled_sum_train`, `scaled_sum_test` | The same sum split by window. **For a centred feature `scaled_sum_train` is 0 to machine precision**, so `scaled_sum` = `scaled_sum_test`: the whole reported contribution comes from the holdout weeks |
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

### 🆕 `benchmark_comparison.xlsx` — paste a benchmark, formulas do the rest

This run's numbers laid out with **live formulas in the cells**, and the
**regions across the columns**. Paste the vendor's (or last year's) contribution
and everything recalculates in the spreadsheet.

Written as `.xlsx` when openpyxl is available (it is on Databricks) with three
sheets — `summary` (read first), `comparison`, `what each column means` —
otherwise as `.csv` with the summary below the table. Excel evaluates `=`
formulas in a CSV on open, so the fallback works the same way.

**Layout of `comparison`.** Row 1 names the blocks, row 2 the columns, data from
row 3. Panes are frozen on the names and headers.

```
A-E   row_type | group | feature | pillar | members
F-N   TOTAL (national):  our | benchmark | pct_diff | ratio | contraction |
                         current_prior | suggested_prior | delta | verdict
O...  one block per region (7 columns each):
                         our | benchmark | pct_diff | ratio | contraction |
                         current_prior | suggested_prior
```

| Column | Filled by | Meaning |
|---|---|---|
| `row_type` | the run | `feature` (compared alone), `group` (a mapping group — the benchmark lives here), `member` (one variable of the group above it) |
| `group` | the run | the mapping group — the vendor's name. A variable not in the mapping is its own group |
| `feature` | the run | our variable; blank on a `group` row |
| `pillar` | the run | reporting group |
| `members` | the run | on a `group` row, the variables it sums |
| `our` | the run / formula | this run's contribution, KPI units. `group` rows: `=SUM` of the member rows below; TOTAL: `=SUM` of the region blocks |
| **`benchmark`** | **the mapping file, or YOU** | yellow on `feature`/`group` rows, grey (not used) on `member` rows. See "where to paste" |
| `pct_diff` | formula | `(ours − theirs) / |theirs| × 100`. `feature`/`group` rows only |
| `ratio` | formula | **R** `= theirs / ours`. A `member` row shows its **group's** R |
| `contraction` | the run | this variable's `use_for_delta` contraction. A pooled feature has one value (repeated in each region); an `independent` one has its own per region, and TOTAL shows their mean |
| `current_prior` | the run | the implied median coefficient your prior file produces (`prior_summary.csv`): the `__population__` row in TOTAL, the region's row in a region block |
| `suggested_prior` | formula | `current × R^(1/(1−max(c,0)))` — **write this back**: TOTAL → `global_prior_mean`, a region block → that region's override row (the regional prior file). Exact for a signed variable; for a **free** one only when c is small — with high c use `current + (R−1)·β_post/(1−c)` (`prior_posterior_contraction_fitting.md` §2b) |
| `delta` | formula (TOTAL) | `ln(R) / c` — how far the data wants to move, log units. Blank when c ∉ (0.001, 0.999) |
| `verdict` | formula (TOTAL) | what to do about this row |

**Where to paste.**

- **A national benchmark** → the TOTAL `benchmark` cell. Each region's
  `benchmark` then spreads it by **our** contribution share, so every region
  shows the national gap — the honest statement, since a national number cannot
  say where the gap is.
- **A regional benchmark** → over the region `benchmark` cells (the spread
  formula is overwritten), then put their sum in TOTAL.
- **With `data.mapping_file` carrying contributions** all of this is
  pre-filled: regional numbers go in the region cells with TOTAL `=SUM` of them,
  a national number goes in TOTAL. A vendor number replicated across several of
  our rows is counted **once**.

**A split variable — how R works when contraction is per member.** The vendor
reports one number for the group (say `digital_hero`), we fit its members
(`digital_hero_mat1`, `_mat2`). So:

1. **R is computed once, on the group:** `R = benchmark / Σ our members`.
2. **Each member is corrected with its OWN contraction and the shared R:**
   `new_m = old_m × R^(1/(1−max(c_m, 0)))`.
3. A member with **c ≤ 0** — the posterior is no narrower than the prior, the
   data is not moving it — scales one-for-one with its prior, so the exponent is
   1 and `new = old × R`. A member the data **does** pin (c > 0) needs a larger
   move, because the data pulls part of any prior change back.
4. Each member closing R on its own contribution closes R on their sum, so the
   **group lands on the benchmark** (the test suite simulates the refit and
   checks exactly this).

The group row carries no prior and no suggestion — there is no single prior to
correct. Its verdict reads `gap - each member row below carries its own fix`.

**How to read it — the `summary` sheet first.** It counts the comparison rows
filled in (`feature` + `group` rows; members never count), the median `pct_diff`,
how many rows sit below/above, and the median `delta` with its IQR, then turns
that into a sentence:

- **`delta` clusters** (IQR < 0.1) → one global constraint is pushing every
  driver the same way, usually a free region intercept the benchmark does not
  have. Correcting prior means one at a time will **not** hold: the next refit
  re-imposes the same shortfall on the corrected numbers. Fix the structure
  (`model.include_intercept`, `model.alpha_prior_sd`) first.
- **`delta` scatters** → the gaps really are per-variable. `suggested_prior` is
  the fix.

`verdict` per row, from the row's gap and its own contraction c:

| verdict | when | meaning |
|---|---|---|
| `ok` | \|pct_diff\| < 10 | leave it |
| `UNIDENTIFIED` | c < −0.2 | the posterior is WIDER than the prior — a collinearity problem. Fix that before trusting any prior |
| `prior-driven` | −0.2 ≤ c < 0.2 | the result is your prior. Writing `suggested_prior` back closes the gap — and the number stays an assumption, not a finding |
| `data disagrees` | c > 0.5 | the data pulls away from the benchmark. Impose it (tighten the sd) or accept the gap |
| `mixed` | otherwise | compare its `delta` with the other rows |

> **Removed:** `implied_benchmark_beta`, `effective_scaled_sum` and
> `dv_scale_used`. They re-expressed the benchmark on our axis; `ratio` and
> `suggested_prior` do the same job directly, and the inputs are still in
> `contribution_math.csv`.
>
> The sheet replaces the old `bias_diagnosis.py`. A benchmark never arrives in a
> fixed schema, so a script that reads one is guessing at the join; a sheet with
> the formulas already in it needs only a paste, and every intermediate quantity
> is visible.

### The three charts

| File | Shows |
|---|---|
| `contribution_bars.png` | Incremental drivers ranked, with 90% HDIs. Baseline parts excluded — they are not switchable levers and would dwarf everything |
| `baseline_breakdown.png` | What the baseline is made of (core + each baseline feature) |
| `decomposition_area.png` | Weekly stacked decomposition, baseline as **one block** — the business view |
| `decomposition_area_expanded.png` | Same, with the baseline opened into core + baseline features |

---

## 🆕 `06_cross_validation` — does it hold up out of sample?

Written **only when `cv.enabled: true`** (in `config.yaml`, or
`CVConfig(enabled=True)`). It is off by default because every fold is a **full
refit**: a 5-fold CV costs roughly five times the headline run. Turn it on once
the single fit looks sane.

**What it does.** Expanding-window (rolling-origin) CV. Fold *k* trains on
everything before its test window, predicts the next `horizon` periods, and the
origin steps forward. Because every scaling statistic is computed from the
training window only, each fold just slices the data and reuses the normal
pipeline — no leakage, no special-cased code path.

**Why it matters more than the single holdout.** One holdout is one draw. Five
origins tell you whether the accuracy — and, more importantly, the
*coefficients* — are a property of the model or of the window you happened to
pick. For an MMM the coefficient stability is the real test: if a coefficient
swings as the origin moves, its contribution story is fragile however good the
error metric looks.

**Cadence.** Every count comes from the cadence preset unless you set it:
weekly → horizon 13, 5 folds, min train 52; monthly → horizon 3, 3 folds,
min train 12, and shorter chains. An explicit value always wins.

| File | Contents |
|---|---|
| `cv_fold_metrics.csv` | Every fit metric, per fold × region × train/test |
| `cv_summary.csv` | Mean ± sd of the test metrics across folds, per region |
| `cv_coefficient_stability.csv` | The raw fold-wise coefficient medians |
| 🆕 `cv_stability_by_region.csv` | Spread of those medians, per feature × region |
| `cv_stability_ranking.csv` | Features ranked by instability |
| 🆕 `cv_scorecard.csv` | **One row summarising the run — the unit of model comparison** |
| `cv_report.md` | The headline readout, including the selection rule |
| `cv_accuracy_by_fold.png` | Test wMAPE per fold, per region |
| `stability/<feature>.png` | Coefficient medians across folds, one line per region |
| `fold_k/sampling_log.json` | Per-fold run manifest |

### `cv_fold_metrics.csv`

One row per **fold × region × dataset**. `region` includes `__all__` (pooled)
and `__aggregate__` (regions summed to a national series); `dataset` is `train`
or `test` — **read the `test` rows**, the train rows are there for comparison.

| Column | Meaning |
|---|---|
| `fold` | 1-based fold number, chronological |
| `region` | Region, or `__all__` / `__aggregate__` |
| `dataset` | `train` or `test` |
| `train_end` | Last date in this fold's training window |
| `test_start`, `test_end` | The window being predicted |
| `r2` | Pooled R² — **inflated** when region levels differ; do not quote it |
| `r2_within_region` | R² against each region's own mean — **the honest one** |
| `mape_pct` | Mean absolute % error |
| `wmape_pct` | Volume-weighted % error — the headline accuracy number |
| `mape_region_weighted_pct` | Volume-weighted average of per-region MAPEs |
| `mae` | Mean absolute error, KPI units |
| `crps` | Continuous ranked probability score — combines calibration and sharpness; lower is better, KPI units |
| `coverage_90_mean_pct` | % of actuals inside the mean-response band; expected well below 90 |
| `coverage_90_pred_pct` | % inside the posterior predictive band — **judge the fold by this**, target ~90 |
| `resid_t_stat`, `resid_p_value` | Test of H₀: mean residual = 0. Small p on `test` = systematic bias in that window |
| `durbin_watson` | Residual autocorrelation; 2 = none, < 1.5 = positive |
| `n` | Observations in the cell |
| `max_rhat` | Worst R-hat **of that fold's own fit** |
| `divergences` | Divergent transitions in that fold |

**How to read it.** Check `max_rhat` and `divergences` *first*. A fold that did
not converge contributes a meaningless accuracy number, and averaging it into
`cv_summary.csv` quietly corrupts the summary. `cv_report.md` counts these for
you ("folds with convergence flags: 1 of 5") — if that count is not 0, exclude
those folds by hand before quoting anything.

Then look at the **spread** of `wmape_pct` across folds, not just its mean. A
model at 5% ± 1% is usable; 5% ± 4% means the number depends on where you cut.

### `cv_summary.csv`

One row per region. Every metric appears twice, `<metric>_mean` and
`<metric>_std`, aggregated over the **test** rows only.

| Column | Meaning |
|---|---|
| `region` | Region, `__all__`, or `__aggregate__` |
| `wmape_pct_mean` / `_std` | Headline accuracy and its stability across folds |
| `mape_pct_mean` / `_std` | Unweighted equivalent |
| `mape_region_weighted_pct_mean` / `_std` | Volume-weighted average of per-region MAPEs |
| `mae_mean` / `_std` | KPI units |
| `crps_mean` / `_std` | Probabilistic accuracy |
| `coverage_90_pred_pct_mean` / `_std` | Calibration. Far below 90 = overconfident intervals; far above = uselessly wide |
| `r2_within_region_mean` / `_std` | **Quote this, not `r2`** |
| `r2_mean` / `_std` | Pooled R², inflated by between-region level differences |

**How to read it.** The `_std` column is the point of the file. A mean that
looks good with a large sd is not a result, it is a coin flip. And a region
whose `wmape_pct_mean` is far worse than the others usually has too little
signal to support its own coefficients — check its `data_support` flags in
`03_coefficients/coefficient_report.csv`.

### `cv_coefficient_stability.csv`

The raw material: one row per **fold × feature × region**.

| Column | Meaning |
|---|---|
| `fold` | Fold number |
| `feature` | Feature name |
| `region` | Region |
| `median` | Posterior median of that coefficient, **on the scaled axis**, in that fold |

Long format on purpose — pivot it to plot a feature's coefficient against
`fold`, which is exactly what `stability/<feature>.png` shows.

### 🆕 `cv_stability_by_region.csv`

The middle view, and usually the one that answers the question: *which region
is making this feature unstable?*

| Column | Meaning |
|---|---|
| `feature`, `region` | The cell |
| `mean` | Average of that coefficient's fold medians |
| `std` | Standard deviation across folds |
| `min`, `max` | Range across folds — check whether it straddles zero |
| `rel_sd_pct` | `std / abs(mean) × 100` — the scale-free instability measure |

**How to read it.** `rel_sd_pct` under ~15% is stable. Above ~50% the
coefficient is being re-estimated from scratch each fold and its contribution
should not be presented as a finding. If `min` and `max` have opposite signs on
a `free` feature, the model cannot even agree on the direction — a sign
constraint or a structural fix is needed, not a prior tweak.

### `cv_stability_ranking.csv`

The roll-up: features sorted worst-first.

| Column | Meaning |
|---|---|
| `feature` | Feature name |
| `avg_rel_sd_pct` | `rel_sd_pct` averaged over regions |

**How to read it.** This is the list to check before anyone acts on a ROI
number. One caveat: a feature pinned by a tight prior will look beautifully
stable here — because the prior, not the data, is holding it still. Read this
file next to `contraction` in `02_convergence`: stable **and** contracted is a
real result; stable **and** uncontracted just means you fixed it by hand.

### 🆕 `cv_scorecard.csv` — how you choose between models

One row per run. This is what you compare when you have two candidate models.

| Column | Meaning |
|---|---|
| `run_name` | which run |
| `admissible` | **a gate, not a score** — false if any fold had R-hat > 1.05 or a divergence, or predictive coverage outside 70–98% |
| `why_not` | why it was disqualified |
| `folds_signature` | `cadence/horizon/folds/min_train` — two runs with different signatures are **not comparable** and `select_model` refuses to rank them |
| `wmape_pct_mean` | mean test wMAPE across folds |
| `wmape_pct_sd` | spread across folds |
| **`wmape_pct_se`** | **the number that decides ties** — `sd/√folds` |
| `crps_mean`, `coverage_90_pred_pct_mean`, `r2_within_region_mean` | the other test metrics |
| `coef_instability_median_pct` | median cross-fold relative sd of the coefficients |
| `n_unstable_features` | how many are above 50% |
| `n_parameters` | for the parsimony tiebreak |

**The selection rule**, applied by `select_model` / `compare_cv_runs`:

1. **Admissibility is a gate.** An unconverged or badly calibrated run is
   excluded outright. Convergence is not tradeable against accuracy.
2. **Accuracy, but only beyond the noise.** Candidates within **one standard
   error** of the best wMAPE are declared TIED. Picking the numerically smallest
   number out of a cluster that differs by less than the fold-to-fold spread is
   selecting on noise.
3. **Coefficient stability breaks the tie.** The deliverable is a decomposition,
   not a forecast: among equally accurate models take the one whose coefficients
   move least across folds.
4. **Parsimony breaks what remains.**

```python
from cross_validation import compare_cv_runs
compare_cv_runs(["outputs/candidate_a", "outputs/candidate_b"])
```

When several candidates tie, it also reports **paired per-fold wins** — how many
folds each beat the leader on. Paired beats unpaired here because every
candidate saw the same windows, so fold-level wins are far more informative than
overlapping means.

> **CV cannot tell you the decomposition is right.** It measures prediction and
> stability. A model can predict well and attribute wrongly — that is exactly
> what an omitted confounder does. Nothing in this folder substitutes for an
> experiment.

### `cv_report.md`

The headline readout — cadence and fold geometry, mean test wMAPE ± sd,
region-weighted MAPE, CRPS, predictive coverage, the count of folds with
convergence flags, and the five least stable features. It closes with the point
worth repeating to stakeholders: **fold metrics judge prediction**. Contribution
and ROI validity additionally need stable coefficients and, ideally, calibration
against lift experiments.

---

## `trace.nc`

The full posterior in NetCDF. Reload with
`arviz.from_netcdf("trace.nc")` to compute anything not in the reports without
re-running the 6-minute fit.

---

## Triage checklist

0. 🆕 **`00_warnings/00_INDEX.md`** — any `high` category first, then
   **`01_data/collinearity_summary.csv`** — a `severe` verdict means the
   coefficients below cannot be read individually. A pinned prior
   or a feature collinear with the intercept explains most "that number looks
   wrong" questions before you open anything else.
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
7. 🆕 **`06_cross_validation/cv_stability_ranking.csv`** (when `cv.enabled`) —
   before anyone acts on a ROI number. Cross-read it with `contraction`: stable
   *and* contracted is a result; stable *and* uncontracted means the prior is
   holding it still.

## "That contribution can't be right" — where to look

| Symptom | File | What to check |
|---|---|---|
| A share looks far too small | `contribution_math.csv` | `scaled_sum` ≈ 0 means the feature is **centred**; the level is in the intercept. Set `contribution_reference=zero` if you want the vs-nothing number |
| A positive coefficient shows a negative contribution | `model_input_summary.csv` | Centred feature: most weeks sat below its average. `contribution_vs` says `feature average` |
| Percentages don't sum to 100 | `contribution_summary.csv` | You dropped the Residual line, or summed `baseline_part` rows alongside `__baseline__` |
| Two files disagree by ~0.1% | `contribution_math.csv` | `volume_median_of_total` vs `volume_sum_of_medians` — see "two arithmetics" |
| A scaling factor looks wrong | `model_input_matrix.csv` | Recompute `(raw − center) / scale` and compare with `__scaled` |
| The number equals the prior you wrote | 🆕 `00_warnings/prior_pins_coefficient.md` | `prior_sd` too tight — the posterior is the prior. Confirm with `contraction` ≈ 0 |
| It changes every time you refit | 🆕 `06_cross_validation/cv_stability_by_region.csv` | `rel_sd_pct` above ~50% means the coefficient is re-estimated from scratch each window |

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
  only one prior Q4 to learn holiday seasonality from. Set `cv.enabled: true` in
  `config.yaml` and read `06_cross_validation/` before drawing conclusions from
  this one split.
