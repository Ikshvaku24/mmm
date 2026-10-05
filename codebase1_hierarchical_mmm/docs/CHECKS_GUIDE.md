# Checks guide — what every check means and what to change

**For:** modellers running codebase 1 who have a finished run folder and need to
decide whether to trust it, and if not, what to change.

**Read with:** `OUTPUTS_GUIDE.md` (every file and column), `TUNING_GUIDE.md`
(every lever), `METHODOLOGY.md` (the order to build a model in). This guide
joins them up. For every check the pipeline runs it gives:

1. **What it computes** — the theory, in standard econometrics / MMM terms.
2. **An example** — from our own runs where one exists (v1, v3, v9), otherwise
   marked *illustrative*.
3. **How to read the value in this model** — the threshold, plus the traps
   specific to a hierarchical, sign-constrained, possibly intercept-free MMM.
4. **What to change** — split into **variables**, **config** and **priors**.

Every threshold quoted is the shipped default. §16 lists each one with its
`config.yaml` key, and flags the few the YAML does **not** reach yet.

---

## Contents

- [0. How to use this guide](#0-how-to-use-this-guide)
- [1. Before the model: prior builder and config loader](#1-before-the-model-prior-builder-and-config-loader)
- [2. Data checks](#2-data-checks)
- [3. Collinearity (pre-fit)](#3-collinearity-pre-fit)
- [4. Convergence](#4-convergence)
- [5. Prior vs posterior: who decided each number](#5-prior-vs-posterior-who-decided-each-number)
- [6. Coefficients: material, supported, and in which units](#6-coefficients-material-supported-and-in-which-units)
- [7. Fit quality](#7-fit-quality)
- [8. Residual assumptions](#8-residual-assumptions)
- [9. Posterior correlation: can the fitted model separate them?](#9-posterior-correlation-can-the-fitted-model-separate-them)
- [10. Structural checks adopted from Meridian](#10-structural-checks-adopted-from-meridian)
- [11. Exogeneity and endogeneity](#11-exogeneity-and-endogeneity)
- [12. Contribution arithmetic and reconciliation](#12-contribution-arithmetic-and-reconciliation)
- [13. Benchmark comparison](#13-benchmark-comparison)
- [14. Cross-validation and model selection](#14-cross-validation-and-model-selection)
- [15. The warnings folder](#15-the-warnings-folder)
- [16. Threshold reference](#16-threshold-reference)
- [17. Symptom → check → fix](#17-symptom--check--fix)
- [18. What the checks cannot tell you](#18-what-the-checks-cannot-tell-you)

---

## 0. How to use this guide

### 0.1 Read in pipeline order: an early failure voids the later readings

| Step | File | The question | Section |
|---|---|---|---|
| 1 | `00_warnings/00_INDEX.md` | Was anything read differently from what I wrote? | §1, §2, §15 |
| 2 | `01_data/collinearity_summary.csv` | Can the data separate the drivers at all? | §3 |
| 3 | `02_convergence/convergence_report.txt` | Did the sampler explore the posterior? | §4 |
| 4 | `02_convergence/prior_posterior_contraction.csv` | Did the data or my prior decide each coefficient? | §5 |
| 5 | `03_coefficients/coefficient_report.csv` | Is each effect material, and supported in each region? | §6 |
| 6 | `04_fit/fit_metrics.csv` | Does it explain movement, in and out of sample? | §7 |
| 7 | `04_fit/assumption_checks.csv`, `posterior_correlation.csv`, `structural_checks.csv`, `confounding_pairs.csv`, `exogeneity_cross_correlation.csv` | Are the intervals honest, and could the coefficients be causal? | §8–§11 |
| 8 | `05_contributions/contribution_reconciliation.csv`, `contribution_math.csv`, `benchmark_comparison.xlsx` | Do the numbers add up, and to what the benchmark says? | §12–§13 |
| 9 | `06_cross_validation/cv_scorecard.csv` | Does it hold up when the window moves? | §14 |

**Seen something odd?** Check `cases/README.md` first: problems we have
already met are written up there, start to finish.

**Steps 1–3 are gates.** Stop at any of these:

- a high-severity warning;
- a severe collinearity verdict on a variable you report;
- a convergence FAIL.

Each means the later files describe a different model from the one you meant to
fit. Fix it and rerun before reading further.

**Steps 4–9 are readings.** They tell you what a number can be used for, and most
end in "report it differently", not "rerun".

### 0.2 Three kinds of fix, tried in this order

| Kind | Where you change it | What it changes | Typical moves |
|---|---|---|---|
| **1. Variables** | The datacube, and which variables are in the prior file | What the model *can* learn | Merge `_mat1`/`_mat2`. Drop a duplicate. Add an event dummy or a missing driver (category, competition). Re-transform upstream (adstock, lag) |
| **2. Config** | `config.yaml`, plus the structural columns of the prior file: `center_mode`, `scale_mode`, `pooling`, `sign_constraint`, `contribution_reference`, `baseline` | How the model is built, scaled and sampled | `center_mode: mean`; `model.likelihood: student_t`; `model.fourier_order`; `model.include_trend`; `model.include_intercept`; `sampler.target_accept` |
| **3. Priors** | `feature_priors*.csv`: `global_prior_mean`, `global_prior_sd`, `regional_sd_prior`, per-region override rows | What you **assume** where the data is silent | Write back `suggested_prior`. Tighten an sd to impose a benchmark. Loosen one the data is fighting |

**Priors come last because a prior can silence almost any check.** Tighten
`global_prior_sd` and:

- the coefficient stops moving;
- its posterior correlation falls;
- its benchmark gap closes;
- its forest plot narrows.

None of that fixes the problem. The data has been **replaced by your
assumption**. That is legitimate only when the deliverable says so, for example
*"set by the prior, contraction 0.04"*. It is how a secondary model works
(METHODOLOGY §2c). `contraction` (§5) is how anyone can tell which kind of
number they are looking at.

### 0.3 Notation

| Symbol | Meaning |
|---|---|
| β | A coefficient on the model's axis: KPI `dv_scale` units per **feature unit**. With the default `center_mode`/`scale_mode` = `none`, the feature unit is the **raw** datacube unit |
| η | The log of a sign-constrained coefficient: β = ±exp(η), η ~ Normal(μ, σ) |
| c | **Contraction** = 1 − posterior variance ÷ prior variance |
| shift | **Mean shift** = (posterior mean − prior mean) ÷ prior sd |
| n | Training periods in one region: 91 on the 104-week retailer panel (13-week holdout); 21 on the 24-month BMC panel |
| 2/√n | The noise floor of a correlation. Two unrelated series of length n correlate at about ±1/√n by chance, so ±2/√n is a two-standard-error band: **0.21 at n = 91, 0.44 at n = 21**. Several checks floor their threshold here |

---

## 1. Before the model: prior builder and config loader

These run when the prior file is generated, and again every time a prior file is
read. Generation happens through `build_priors`, or through `run_from_yaml` with no
`data.feature_priors`.

Warnings go to two places:

- builder warnings → `<pre_model_dir>/00_warnings/`;
- loader warnings → the run's `00_warnings/`.

Most of these checks **stop** the run with a message that names the fix.

### 1.1 Hard stops: names, regions, the share file

| Check | Stops when | Why it exists | Fix |
|---|---|---|---|
| Variable names | A mapping or share variable is not a datacube column (or, with `data.feature_priors` set, not a prior-file variable) | **The subset rule:** the prior file may carry *more* variables than the mapping and share files, never fewer. A typo would otherwise leave a prior silently blank. The message suggests the nearest names | Correct the spelling, or add the variable to the prior file |
| Region alignment | A mapping region matches no datacube region, even ignoring case, spaces, punctuation and word order | An unmatched region silently drops its vendor contribution. That was the v9 "no contribution found" failure: regions arrived written as `('Base', 'Droguerias')` | Rename the region on one side. The message lists both sides |
| National **and** regional rows for one vendor variable | Both are present | The builder cannot know which total to trust | Keep one |
| Share-file structure | Any of: an unknown `section`; a variable in two sections; a pillar with two different `pillar_share_pct`, or none; a `comp_media`/`trade`/`baseline` row with no `variable_share_pct`; a media pillar with no spend | Each would build a prior from an ambiguous number | Fix the row the message names |
| Version sync | `[mmm] codebase … OUT OF SYNC` is printed at start-up | One module is an older copy than the rest (a stale Databricks upload). Its behaviour is not what the docs describe | Re-upload the whole `mmm/` folder |

### 1.2 Pre-model warnings

| Warning (file in `00_warnings/`) | Fires when | What it means for the model | Change |
|---|---|---|---|
| Shares add up past 100% (`other`) | Baseline variable shares exceed 100% of the baseline, or the shares imply more than 100% of sales | The generated priors over-claim before the model sees any data. The adding-up constraint will then push something else negative | **Variables:** fix the share file |
| `generated_prior_units` (high) | `run.dv_scale` ≠ `mean`, `run.dv_scale_scope` ≠ `region`, or `data.dv_aggregation` ≠ `mean` | A generated mean is `C ÷ Σ raw x ÷ mean KPI`: per raw unit, per unit of the region's mean KPI. Under any other scale every contribution is wrong by a constant multiple **and still reconciles to 100%**. That was the v5 failure | **Config:** `run.dv_scale: mean`, `run.dv_scale_scope: region`. Leave `scale_mode: none` on generated variables, or divide their means by any scale you add |
| `CONCENTRATED` (`other`) | The total-preserving national coefficient differs from the plain average by more than 1.25× | The vendor contribution sits in a few regions while the variable has support in many. Under `pooling: global` the average under-delivers the national total by exactly that ratio, and **no number of refits closes the gap**. v9 examples: `tdp_ninos`, 98.8% in one region → R ≈ 4.9; `tdp_base` → R = 4.10 | **Config:** `data.national_basis: weighted` for a global model. **Priors:** or use `feature_priors_regional.csv` with `pooling: independent` |
| `generated_prior_blank` (medium) | A variable is in neither input file, or has no support | Its `global_prior_mean` is left blank, so it starts at the built-in default | **Priors:** fill it by hand, or leave it if this is the new variable you are testing |
| `regional_prior_sign_skipped` (review) | A region cell runs against the variable's sign, or is exactly 0 in the vendor decomposition where the variable has support | A signed coefficient cannot be zero or the other sign, so that region gets no override row and falls back to the national prior | Decide whether the variable is really signed. If its sign genuinely flips by region, make it `free` |

### 1.3 Prior-file warnings (config loader)

| Category | Fires when | Meaning | Change (priors) |
|---|---|---|---|
| `prior_pins_coefficient` (high) | Signed feature, `prior_sd_basis: log`, `global_prior_sd` < 0.05 | On the log scale, 0.02 means ±2%, so the data cannot move the coefficient. Usually someone meant 20% | `global_prior_sd: 0.2` with `prior_sd_basis: relative` |
| `prior_deliberately_pinned` (info) | Signed feature, non-log basis, resulting σ_log < 0.05 | The coefficient is **fixed**, not estimated | None, if intended (the LOCKED rows of a secondary model). Describe it as an input |
| `pooling_collapsed` (medium) | `pooling: hierarchical` with 0 < `regional_sd_prior` < 0.02 | Regions cannot differ, so it is a global model with extra parameters | Raise `regional_sd_prior`, or write `pooling: global` |
| `per_region_prior_sd_ignored` (medium) | A region override row carries a prior sd under hierarchical pooling | The pooled `regional_sd_prior` governs spread, so the number you wrote does nothing | `pooling: independent` to set it per region |
| `relative_sd_read_as_absolute` (info) | Free variable, `relative` sd, mean 0 | A fraction of zero is nothing, so the sd is read as an absolute number | Intended for a new test variable; otherwise give it a mean |
| `negative_mean_read_as_size` (review) | A negative mean on a `negative` feature | The mean of a signed variable is a size; the sign comes from `sign_constraint` | Write the magnitude |
| `prior_mean_not_a_magnitude` (high) | A signed feature with mean ≤ 0 (after the rule above) | Replaced by **0.05**. Under raw-unit features that default can be orders of magnitude off | Write a positive magnitude, or make the variable `free` |
| "have no `global_prior_mean`" (`other`) | Blank means | The built-in default applies: 0.05 for a signed feature, 0 for a free one, both per raw unit | Fill them, or accept that those contributions are placeholders |

> **Reading the prior you actually wrote: `01_data/prior_summary.csv`.** This is
> the check most often skipped. For every feature it prints:
>
> - the parameters the sampler gets (`mu_log`, `sigma_log`);
> - what they mean back in coefficient units: `implied_median`, `implied_mean`,
>   `implied_q05`/`implied_q95` and `implied_rel_sd`.
>
> **Example (from the defaults).** The default for a signed feature is
> `global_prior_sd: 1.0` on the `log` basis. That is a 90% prior interval from
> **0.19× to 5.2×** your mean, with `implied_rel_sd` = √(e¹ − 1) = **1.31**. Read
> that as "somewhere between a fifth and five times what I said".
>
> If you meant ±20%, the row will show it is not ±20%. Write
> `global_prior_sd: 0.2` with `prior_sd_basis: relative`, and `implied_rel_sd`
> reads ≈ 0.20.

---

## 2. Data checks

`prepare_data` runs these before any sampling, on the training window (or the
full panel if `run.scaling_window: full`).

### 2.1 Hard errors

| Error | Cause | Fix |
|---|---|---|
| Missing columns | A configured feature or the `dv` is not in the datacube | Fix the name |
| Duplicate (region, date) rows | The datacube was stacked twice | De-duplicate upstream |
| NaN in the `dv` or a feature | Gaps not filled | Zero-fill inactive periods upstream. A NaN is never "no activity" |
| A region with < 10 training periods | Holdout too long, or a short region | Reduce `run.holdout_periods`, or drop the region |
| Region priors naming an unknown region | Override row typo | Fix the region name |
| Scale below `run.min_feature_scale` | A column whose every non-zero value is numerical dust (e.g. 5e-17, an adstock tail that ended before the window). Scaling it turns float noise into a unit regressor; under a sign constraint it **manufactures contribution out of nothing**. Only checked when a `scale_mode` is set: under `none` a dust column passes, so look for a tiny `raw_max` in `model_input_summary.csv` | Drop the feature, or fix its units upstream |

### 2.2 Warnings

**`negative_values_uncentred` (high).**

- **Computes.** A sign-constrained feature has negative raw values and is not
  centred.
- **Why it matters.** A positive coefficient on a column that goes negative
  produces negative contribution in those weeks, against the sign you imposed.
  Typical case: a price index, or a variable already centred upstream.
- **Change (config):** `center_mode: mean` + `contribution_reference: zero`.
  If the variable really can go negative, make it `free`.

**`collinear_with_intercept` (high).**

- **Computes.** An uncentred, always-on feature (> 90% non-zero) has
  sd ÷ mean of its non-zero values below `run.near_constant_sd` (0.1).
- **Theory.** The region intercept is a column of ones. A column at 80 ± 0.5 is
  80 × that column plus a tiny wiggle, so the model cannot tell the two apart. The
  sampler slides along the ridge: a big coefficient with a negative intercept, or
  the reverse.
- **Example (v1).** Two such variables went in with media-style scaling. Result:
  R-hat 1.26, min ESS 13, tree depth saturated 100%, and TDP +91% against AVP
  −97% — offsetting contributions.
- **Change (config):** `center_mode: mean` + `contribution_reference: zero`.
  This fixes the geometry without changing attribution (v7: 0.1pp). See
  TUNING_GUIDE §4.2.

**`near_constant_mutual` (high).**

- **Computes.** The same condition, with `include_intercept: false` and two or
  more such columns.
- **Theory.** Nothing is there to compete with a level, but the near-constant
  columns are collinear with **each other**. Only their sum is identified; the
  split is set by the priors.
- **Change.** Config: centre them. Variables: or keep one and drop the rest.
  Priors: or accept the split as an assumption — pinned rows in a secondary
  model.

**`seasonality_overfit_risk` (medium).**

- **Computes.** `model.fourier_order` is above (training periods ÷ 6).
- **Theory.** Each order adds a sine/cosine pair. With few years of data, a
  high order fits last year's specific weeks, not a recurring season.
- **Change (config):** lower `fourier_order`, or replace the high harmonics
  with event dummies.

**`cadence_ambiguous` (medium).** The date spacing is neither weekly nor
monthly, so MAT blocks and CV presets may be wrong. **Change (config):** set
`run.cadence` and `cv.cadence` explicitly.

**`degenerate_feature_column` (high).**

- **Computes.** A `scale_mode` produced a zero, negative or non-finite scale,
  so 1.0 was used instead. The column is constant, all-zero or entirely
  non-positive in the training window.
- **Example (v1).** Three coupon columns whose non-zero values were all
  ~1e-15: float noise being fitted as a regressor.
- **Change.**
  - *Variables:* drop the rows, or check they are the columns you meant.
  - *Config:* `run.zero_threshold_rel: 1.0e-6` snaps adstock-tail dust to
    exact zero.

**`intercept_without_centering` (high).**

- **Computes.** `include_intercept: false` while `dv_center: none`.
- **Meaning.** The KPI still carries its level, and nothing in the model holds
  it. Every coefficient is dragged upwards to fake an intercept.
- **Change (config).** `run.dv_center: mean`. The baseline then becomes the
  fixed training mean instead of a free parameter. Or keep the intercept. See
  TUNING_GUIDE §2.2.

**`scaling_uses_holdout` (review).**

- **Computes.** `run.scaling_window: full`.
- **Meaning.** The scaling statistics include the holdout, so the test rows of
  `fit_metrics.csv` are no longer strictly out of sample.
- **Change.** None, if you chose it to match a vendor window. Judge
  out-of-sample accuracy with CV, which always scales on each fold's own
  training window.

### 2.3 What went in: `01_data/model_input_summary.csv`

Two columns are checks.

**`pct_weeks_negative_scaled`.** A centred feature is negative in roughly half
its weeks; that is expected. An *uncentred* signed feature should read 0. If it
does not, see `negative_values_uncentred` above.

**`mean_zero_by_construction`.** TRUE for a centred feature. Its contribution
is ≈ 0 over the training window under `contribution_reference: auto`; see §12.

---

## 3. Collinearity (pre-fit)

**Files:**

- `01_data/collinearity_summary.csv` (one row per region);
- `collinearity_vif.csv`;
- `collinearity_pairs.csv`;
- `collinearity_heatmap_<region>.png`.

### 3.0 Why it is measured on the design matrix, not on the raw data

The sampler does not see your features alone. Per region it sees the
**intercept**, the **Fourier columns** (`__fourier__sin_1`, `__fourier__cos_1`, …),
the **trend** (`__trend__`) and the features, on the training window. A
correlation matrix of the raw datacube cannot see:

- a feature that duplicates the intercept;
- a promo flag that is really December, i.e. collinear with the seasonal block;
- a channel that only ran during the growth phase, i.e. collinear with the trend.

So the checks rebuild the design exactly as the model assembles it. In the
regression sense, collinearity means the **likelihood is flat along some
direction**: the data constrains a combination of coefficients, such as their sum,
but not each one. In OLS that inflates standard errors. In our Bayesian model the
prior decides the position along the flat direction, which is why collinearity
shows up later as low `contraction` or high posterior correlation (§5, §9).

### 3.1 Pairwise correlation — `collinearity_pairs.csv`

- **Computes.** Pearson r between every pair of design columns. Lists
  \|r\| ≥ `pair_warn` (0.8); severe at `pair_bad` (0.95).
- **Example (illustrative).** `tv_mat1` and `tv_mat2` at r = 0.97: one activity
  split by period, so effectively one variable.
- **Reading it.** Catches only *two* columns moving together. It misses three
  columns where A + B ≈ C, and it misses the intercept (a constant has no
  correlation).
- **Change (variables):** merge the pair, or drop one. Under a pair at 0.95+ the
  data can identify the sum, not the split.

### 3.2 VIF (centred) — `collinearity_vif.csv` → `vif`, `explained_by`

- **Computes.** VIF_j = 1 ÷ (1 − R²_j), where R²_j comes from regressing column j
  on every other design column, intercept included.
- **Theory.** In OLS, VIF is exactly the factor by which collinearity inflates
  the coefficient's variance. VIF 10 means the coefficient sd is √10 ≈ 3.2× what
  it would be with an orthogonal column.
- **Thresholds.** 5 is moderate (`vif_warn`); 10 is severe (`vif_bad`).
- **`explained_by`** names the columns that explain this one — computed below.
- **Reading it.** A culprit that is `__fourier__…` or `__trend__` means the
  feature is confounded with seasonality or growth. That is a different fix
  from feature-on-feature collinearity.
- **Change.**
  - *Variables:* merge or drop (METHODOLOGY §3, in order of preference).
  - *Config:* if a culprit is `__trend__`, question `include_trend`; if
    `__fourier__`, question `fourier_order` or the dummy.
  - *Priors:* last resort, as an assumption — it decides the split, it does not
    remove the collinearity.

#### How `explained_by` is computed

A VIF says *how much* of a column the others explain. `explained_by` says
*which* others, and how much each one carries. It is filled only for a column
with centred VIF ≥ `assumptions.vif_warn` (5); for every other column it is
blank on purpose. Every column has *some* largest neighbour, and naming one for
an independent feature would read as an accusation.

**Step 1 — the auxiliary regression.** Regress column j on every other column
of the region's design, by least squares on the training window:

> x_j = b₀·intercept + b₁·x₁ + b₂·x₂ + … + error

The "others" are the intercept (when the model has one), the Fourier columns,
the trend and every other feature active in that region. This is the same
regression whose R² gives the VIF.

**Step 2 — a standardised weight for each regressor.** For each other column k:

> weight_k = |b_k| × sd(x_k) ÷ sd(x_j)

This is the **standardised (beta) coefficient**: how many standard deviations
x_j moves when x_k moves by one standard deviation, **holding the other columns
fixed**. Raw b's cannot be compared — a GRP and a distribution point have
different units — so they are rescaled by the sds.

The intercept has sd 0, so its weight is always 0 and it is **never named**.
Collinearity with the intercept is what `vif_uncentred` and `duplicates` are
for (§3.3).

**Step 3 — rank and keep the top ones.**

- Sort by weight and keep the top `assumptions.vif_top_k` (3).
- Drop any with weight ≤ 0.01.
- Names are written as `a + b + c`; weights in the same order in
  `explained_by_weights` (`1.48 + 1.45 + 0.02`).
- `vif_top_k: 0` turns both columns off.

**Worked example** (made-up data, computed with the real function). On 91
weeks, `tdp` = 0.9 × `category_vol` + 25 × trend + noise, and `tv` is
unrelated. The auxiliary regression of `tdp` on the other columns gives:

| Regressor | b | sd(x) | weight = \|b\| × sd(x) ÷ sd(tdp) |
|---|---|---|---|
| intercept | −0.49 | 0 | 0 — never named |
| `__trend__` | 25.01 | 0.292 | 25.01 × 0.292 ÷ 5.036 = **1.45** |
| `category_vol` | 0.906 | 8.227 | 0.906 × 8.227 ÷ 5.036 = **1.48** |
| `tv` | −0.0019 | 57.93 | **0.02** |

The centred R² is 0.9696, so VIF = 1 ÷ (1 − 0.9696) = **32.9**. The row reads:

> `vif` 32.9 · `explained_by` = `category_vol + __trend__ + tv` ·
> `explained_by_weights` = `1.48 + 1.45 + 0.02`

**Three things the example shows:**

1. **The pairwise check missed it.** r(tdp, category) = 0.36 and r(tdp, trend) =
   0.30 — nowhere near the 0.8 bar in `collinearity_pairs.csv`. Jointly they
   explain 97% of TDP. This is why `explained_by` uses the joint regression,
   not pairwise correlations.
2. **Weights are not shares.** They do not add to 1 or to R², and they can each
   exceed 1. Here category and trend are themselves correlated (r = −0.78) and
   partly *offset* each other inside TDP, so both carry large weights. When two
   culprits have weights well above 1, they are collinear with each other as
   well: expect them in each other's `explained_by` too (they are, in this
   example).
3. **Read the weight, not just the name.** `tv` is listed third at 0.02 — above
   the 0.01 cut-off, but noise. A culprit with a weight under ~0.1 is not a
   reason to act.

**What to do with it.** The names are the candidates for the §3.7 fix: merge
them, drop one, or pool them into a pillar. In the example: keep TDP and the
category series only if the business needs both coefficients, and expect them
to trade off (§9). If the trend is a culprit, test `include_trend` on and off
with CV.

### 3.3 Uncentred VIF — `vif_uncentred`, `duplicates`

- **Computes.** The same auxiliary regression, but R² is measured against Σy²
  instead of Σ(y − ȳ)², so the column's level is kept in.
- **Theory: why it exists.** **The textbook VIF is structurally blind to a
  column that duplicates the intercept.** Centring removes exactly the level
  that makes the column collinear with the constant. For a column at level L with
  sd s, explained only by the intercept:

  > vif_uncentred ≈ 1 + (L ÷ s)²

  So any always-on variable with a coefficient of variation below about 1/3
  reads above 10.
- **Example (v1).** On the always-on variable that broke the run, centred VIF
  was **1.09** (looks perfect), the condition number was **23,000**, and
  `vif_uncentred` was **9.7 × 10⁷**.
- **`duplicates` says which kind of collinearity you have:**

  | Value | Meaning | Fix |
  |---|---|---|
  | `other features` | Centred VIF > 10: a combination of other columns | §3.2 |
  | `the intercept/level` | Uncentred > 10, centred fine: it duplicates the region intercept | **Config:** `center_mode: mean` + `contribution_reference: zero` |
  | `a shared constant level` | Uncentred > 10 with `include_intercept: false`: several always-on columns stand in for the missing intercept | Centre, or keep one (see `near_constant_mutual`, §2.2) |

- **Reading it under the raw-unit default.** With `center_mode: none`, every
  price, distribution or TDP column will usually read `the intercept/level`, and
  that alone makes the region's verdict `severe`.
  - That is a *sampling-geometry* finding (centre it), not a reason to drop the
    variable.
  - It also tells you something about attribution. The "versus zero"
    contribution of such a variable is an **extrapolation**: the data only ever
    sees x between, say, 70 and 90, and the contribution asks what happens at 0.
    The slope comes from the wiggle; the size of the contribution comes from the
    level × that slope.

### 3.4 Condition number — `condition_number`

- **Computes.** σ_max ÷ σ_min, the ratio of the design's singular values, with
  columns scaled to unit length but **not centred** (Belsley's convention).
- **Theory.** It measures how close the whole design is to singular, across all
  columns at once. It is the only one of the four statistics that sees a
  three-way dependence (A + B ≈ C).
- **Thresholds.** 10 is moderate; 30 is severe (Belsley: 30+ is "strong").
- **`inf`** means the design is exactly singular — usually more columns than
  training periods in that region (§3.8).
- **Example.** v1 read 23,000. After centring, v2 converged.
- **Change.** If no single VIF is high, look for a group: the heatmap shows the
  block. Fix the block, by merging or pooling into a pillar.

### 3.5 Verdict — `collinearity_summary.csv` → `verdict`

| Verdict | Rule |
|---|---|
| `severe` | Condition number > 30, **or** max(centred VIF, uncentred VIF) > 10, **or** the condition number is not finite |
| `moderate` | Condition number > 10 or VIF > 5 |
| `ok` | Neither |

The same row gives `worst_column`, `worst_explained_by`, `n_vif_over_10`,
`n_duplicating_intercept` and `n_pairs_flagged`, so the summary names the culprit
without opening the other files. Three more columns explain the row itself:

- `n_obs` / `n_columns` — training periods and design columns in this region.
  **If `n_columns` > `n_obs`, no VIF can be computed** (§3.8).
- `n_dead_columns` — features with no activity in this region, excluded from
  its design.
- `note` — why anything in the row is blank or excluded. `max_vif` is blank
  (not 0) when no VIF could be computed.

### 3.6 Heatmap — `collinearity_heatmap_<region>.png`

The pairs table says *which* pairs crossed a line. The heatmap shows the
**structure**:

- **One dark off-diagonal cell** → one bad pair → drop or merge.
- **A dark block** → a family of variables that move together (all
  `category_vol_*`, all slow-moving levels) → pool them into a pillar and report
  the pillar total, which *is* identified.

Cells with \|r\| ≥ `pair_bad` are annotated when the map has ≤ 25 columns. Above
`heatmap_max_features` (40), only the highest-VIF columns are drawn.

### 3.7 What to do about collinearity, in order

1. **Merge** columns that are one activity (`_mat1`/`_mat2`).
2. **Drop** one — keep the better-measured one or the one with the clearer
   meaning, and state that its coefficient now carries both.
3. **Centre** if the collinearity is with the intercept (`center_mode: mean`).
4. **Pool** the block into one `pillar` and report the pillar total.
5. **Impose a prior** — last, with named evidence, and say the split is an
   assumption.

**Never** drop a variable because its coefficient "looks wrong". In a collinear
block, a wrong-looking coefficient is a symptom.

> **Meridian's view, for balance.** Meridian errors only at VIF 1000 /
> \|r\| 0.999. It treats everything short of numerical degeneracy as something
> the priors regularise. Our thresholds are the textbook ones, deliberately
> tighter, because our coefficients are reported one by one. See
> `MERIDIAN_ASSUMPTIONS.md`.

### 3.8 When `collinearity_vif.csv` is blank — `vif_note`

Every row now says why a blank cell is blank, in `vif_note`. There are three
causes:

| `vif_note` starts with | Cause | What it means | Change |
|---|---|---|---|
| `not computable: P design columns vs N training periods` | The region's design has **more columns than training periods**: intercept + Fourier + trend + every feature active there | Each column is then an *exact* combination of the others: the auxiliary regression fits perfectly, VIF is infinite by construction, and the condition number is `inf`. **Every VIF in the region is blank.** The region's data alone cannot separate the coefficients; the priors and pooling across regions do | Nothing breaks — read `contraction` (§5) to see which coefficients the data actually moved. To get the VIF back: fewer variables (merge, drop, pool into a pillar), a lower `fourier_order`, or a longer panel. `collinearity_pairs.csv` and the heatmap are still valid |
| `no activity in this region's training window` | The feature is all zero in this region — e.g. one brand's media in another brand's regions | It multiplies nothing here, so it is **excluded from this region's design** and does not count towards the columns | None |
| `constant in this region` | The column has no variation around its mean | The centred VIF is undefined; it duplicates the intercept. Read `vif_uncentred` and `duplicates` | `center_mode: mean`, or drop it |

**Example (computed).** A monthly panel with 24 months and 27 features gives
28 design columns (27 + intercept) against 24 periods. Every VIF is blank;
`collinearity_summary.csv` shows `condition_number` = inf and the `note` gives
the counts. The run also writes `00_warnings/collinearity_not_computable.md`.

**Before 2026-10-06** the same panel wrote a VIF file with every number blank
and no reason, and the summary reported `max_vif` = 0 — which reads as "no
collinearity" when the truth is "too many columns to measure it". Features with
no activity in a region also counted towards the columns, and pushed that
region's condition number towards infinity.

**The full case** — a 12-region monthly panel with 80 columns on 21 periods,
what an external diagnosis got right and wrong, and what to do — is
[`cases/001`](cases/001_blank_vif_more_columns_than_periods.md). The general
approach for short panels (what pooling can and cannot separate, the metrics
to use instead, modelling options) is METHODOLOGY §3c.

**Close to the limit, VIF is inflated by the column count alone.** For a column
unrelated to all the others, the auxiliary R² still averages about k ÷ (n − 1),
where k is the number of other non-constant columns and n the periods. So on a
short panel, a high VIF partly measures *how many columns you have*, not how
related they are.

**Example (computed).** 20 independent random features on 24 months: k = 19,
so R² ≈ 19 ÷ 23 = 0.83 and VIF ≈ 6 from noise alone. The largest VIF in that run
was 15.5 — "severe" for columns built to be unrelated. On a monthly panel,
compare a VIF against that baseline before acting on it.

---

## 4. Convergence

**Files:**

- `02_convergence/convergence_report.txt`;
- `energy_plot.png`;
- `trace_worst_rhat.png`;
- `sampling_log.json`.

**A convergence FAIL voids everything downstream.** Every coefficient,
contribution and check after this point is computed from the draws. If the draws
do not represent the posterior, nothing computed from them does either.

### 4.1 R-hat

- **Computes.** Rank-normalised split R-hat (ArviZ). It compares the variance
  *between* chains with the variance *within* them. If four chains started in
  different places and all explored the same distribution, R-hat = 1.00.
- **Thresholds.** < 1.01 OK; 1.01–1.05 WARN; > 1.05 FAIL. It is `n/a` with one
  chain or ADVI — **ADVI has no convergence diagnostic at all**.
- **Example.** v1 read 1.26: chains in different places, the posterior not
  explored. v3 read 1.0058.
- **Reading it.** The worst-10 table names the parameters.
  - Intercept + one level feature → intercept collinearity (§3.3).
  - Two features → they trade off (§9).
  - `tau_*`/`regional_sd` → the hierarchy's funnel (§4.3).

### 4.2 Effective sample size — bulk and tail

- **Computes.** The number of independent draws the autocorrelated chain is
  worth.
  - **Bulk ESS** governs the reliability of the mean and median.
  - **Tail ESS** governs the 5%/95% quantiles, i.e. the HDI endpoints.
- **Threshold.** > 400: 100 per chain × 4 chains (Vehtari et al. 2021).
- **Example.** v1: min ESS **13**, so 4,000 draws were worth thirteen. v3:
  bulk 1052, tail 1345.
- **Reading it.**
  - Low *bulk* means the point estimates are noisy.
  - Low *tail* alone means the intervals are noisy: the report says
    "interval endpoints unstable". Do not quote HDIs from that run.
- **Change (config).** Fix the geometry first (§4.6). More `sampler.draws` only
  helps once the chains already agree.

### 4.3 Divergences

- **Computes.** NUTS integrates a physics simulation along the posterior. A
  divergence is a step where the simulation's energy error exploded, because the
  posterior curves too sharply for the step size. The sampler cannot enter that
  region, so the draws are **biased away from it**.
- **Thresholds.** The report says INVESTIGATE at any divergence. The
  guardrail fails above **1%** of transitions.
- **Example.**
  - v3: 7 divergences = 0.18% of 4,000. Flagged, below the guardrail, and the
    pattern was checked.
  - v7: 9 → 0 after centring.
- **Typical causes in this model:**
  - a level variable collinear with the intercept (a long narrow ridge);
  - a hierarchical **funnel**: when `regional_sd` is near 0 the region offsets
    are squeezed into a narrow neck. The model is non-centred (`z_` offsets),
    which helps but does not cure it;
  - a very wide log-scale prior: exp(η) with σ = 3 spans six orders of
    magnitude.
- **Change.**
  - *Config:* centre level variables; raise `sampler.target_accept` from 0.92
    to 0.95–0.99 (smaller steps, slower).
  - *Priors:* tighten `regional_sd_prior` or a huge `global_prior_sd`.
  - *Variables:* drop or merge the redundant column.

### 4.4 Tree depth

- **Computes.** NUTS doubles each trajectory until it turns back, up to depth
  10 (1,023 steps). "Saturated" means it hit the cap: the sampler is walking a
  long, narrow, correlated ridge and still had not turned.
- **Threshold.** The report flags max depth ≥ 10, with the % of steps
  saturated.
- **Example.** v1 was saturated **100%** of steps. v2 was saturated 0.2%. v3
  reached max depth 8, so nothing saturated.
- **Change.** Treat the *cause* — collinearity, nearly always (§3). Raising the
  depth cap just pays more compute to walk the same ridge.

### 4.5 BFMI and the energy plot

- **Computes.** E-BFMI = mean((E_t − E_{t−1})²) ÷ var(E), per chain. It measures
  how well each momentum refresh lets the sampler jump between energy levels.
- **Threshold.** > 0.3 OK. Below that, the sampler cannot move between the
  hierarchy's levels (a funnel), or the posterior has heavy tails.
- **Energy plot.** The marginal-energy and energy-transition histograms should
  overlap. A transition histogram much narrower than the marginal is the same
  diagnosis as low BFMI.
- **Change (priors):** tighten hyper-priors (`regional_sd_prior`); **config:**
  reconsider `hierarchical` for a feature active in only one or two regions.

### 4.6 Trace plot of the worst R-hat parameters

**What to look for:**

| Pattern | Meaning | Change |
|---|---|---|
| Chains flat but at **different levels** | Several modes. Examples: a free variable whose sign can go either way; two collinear features swapping roles | `sign_constraint` if the sign is known; merge the pair |
| A slow **drift** in the first part | Not yet warmed up | More `sampler.tune` |
| "Hairy caterpillars" that overlap | Healthy | — |

**Repair order for any convergence failure.** Each step is cheaper than the next,
and the earlier ones fix the cause rather than the symptom:

1. Centre level variables.
2. Merge or drop collinear columns.
3. Tighten pathological priors.
4. Raise `target_accept`.
5. More `tune` / `draws`.

### 4.7 The guardrail and the sampling log

- **`run.on_convergence_failure`.**
  - `warn` (default) prints the problem and keeps going.
  - `fail` refuses to persist a run with R-hat > 1.05 or > 1% divergences.
  - Use `fail` for production refreshes.
- **`sampling_log.json`.**
  - `sampler_requested` vs `sampler_used`: if they differ, a NumPyro/GPU failure
    fell back to PyMC under `allow_sampler_fallback`.
  - `chain_method_applied`, `wall_seconds`, and the library versions.
  - Check it before comparing two runs' timings or results.

---

## 5. Prior vs posterior: who decided each number

**Files:**

- `02_convergence/prior_posterior_contraction.csv`;
- `prior_posterior_contraction.png`;
- `prior_posterior/<parameter>.png`;
- `prior_predictive_check.png`;
- the WARNING lines appended to `convergence_report.txt`.

This section answers the one question a manager will ask about every number:
**"is that the data talking, or your assumption?"**

### 5.1 Contraction

- **Computes.** c = 1 − Var(posterior) ÷ Var(prior), per parameter, from the
  prior and posterior draws.
- **Theory.** In a Normal model, precisions add: posterior precision = prior
  precision + data precision. So

  > c = data precision ÷ (prior precision + data precision)

  This is the **share of the posterior's information that came from the data**.

| c | Reading | Verdict label |
|---|---|---|
| > 0.5 | The data did most of the work | `data-driven` |
| 0.2 – 0.5 | Both contributed | `mixed prior and data` |
| 0 – 0.2 | The posterior is your prior, slightly sharpened. Report it as an **assumption, not a finding** | `PRIOR-DRIVEN` |
| ≤ 0 | The posterior is **wider** than the prior | `UNIDENTIFIED` |

**Why c can be negative here, when it cannot in a textbook model.** In a linear
Gaussian model the posterior can never be wider than the prior. It happens here
for three reasons; check them in this order:

1. **The chains never converged.** Between-chain disagreement inflates the
   "posterior variance". v1: TDP/AVP contraction **−131 / −112**; v2, after
   centring: **+0.89 / +0.93**. Fix convergence (§4) first.
2. **You are reading a natural-scale row of a log-scale parameter.** For a
   signed feature β = exp(η). If the posterior of η is narrower but sits higher,
   β's spread can still be larger. The worked example in
   `prior_posterior_contraction_fitting.md` has c_η = 0.51 throughout, while
   c_β = 0.60, 0.26 or −1.00 depending on where the posterior lands. **Read the
   row with `use_for_delta = TRUE`** (§5.4).
3. **Genuine non-identification.** Two duplicate columns, or a region dummy
   alongside the region intercept. TUNING_GUIDE §3 has the signature table.

### 5.2 Mean shift — prior–data conflict

- **Computes.** shift = (posterior mean − prior mean) ÷ prior sd.
- **Theory.** Contraction says whether the data **sharpened** the parameter;
  shift says where it **moved** it. A parameter can contract hard around a value
  nowhere near your prior mean. |shift| > 2 means the data pulled it more than
  two prior sds away: a **prior–data conflict**. The run appends it to
  `convergence_report.txt`, and the verdict gets `| PRIOR-DATA CONFLICT`.
- **The first suspect is units, not the prior.** If shift is large *and
  same-signed across many features*, the model reads different units from the
  ones the priors were written in (`dv_scale`, `scale_mode`). That is the
  signature of a scale mismatch (TUNING_GUIDE §4).
- **Example (v9, `tdp_base`).** shift −2.48 at c = 0.61. The prior was then
  raised 4.1× to close a benchmark gap. Next run: shift **−9.26** at c = 0.92.
  The data wanted it *lower*; raising the prior quadrupled the conflict and
  bought 1.4pp of contribution.

### 5.3 Reading c and shift together

| c | \|shift\| | Reading | Change |
|---|---|---|---|
| ≤ 0 | any | Unidentified | **Variables / config** (§3, §4). Never the prior mean |
| < 0.2 | ≤ 2 | Prior-driven: the number is yours | **Priors** are the lever: write `suggested_prior` back (§13), or set it from evidence. Report it as an input |
| < 0.2 | > 2 | Weak data pulling hard. Usually it trades off with a correlated variable, or the units differ | Check `posterior_correlation.csv` (§9) and units, before touching the prior |
| 0.2 – 0.5 | ≤ 2 | Mixed | One correction with R^(1/(1−c)), then stop (METHODOLOGY §2d) |
| > 0.5 | ≤ 2 | The data agrees with you and sharpened it | Nothing: this is a finding |
| > 0.5 | > 2 | **The data confidently disagrees** | Units first. Then either accept the data, or impose with a tight sd **and named evidence**. Do **not** chase it with repeated mean corrections: v9's three rounds oscillated |

**Where the data alone would put it (the implied likelihood).** Precision
weighting gives

> posterior mean = (1 − c) × prior mean + c × data mean

so the data's own estimate is

> data mean = prior mean + shift × prior sd ÷ c

Use it to predict where a looser prior would land before you rerun. Two
references:

- METHODOLOGY §2, "Updating a prior mean with no benchmark";
- `prior_posterior_contraction_fitting.md`.

**Example (v9, `ucm`).** c 0.723 with shift −6.0 in one run; c 0.412 with
shift −1.9 in the next. Its posterior median 0.0272 gave a 4.45% share. To
report 8% it had to be **pinned** at 0.049, which makes that 8% an assumption.
METHODOLOGY §2b–2c covers when that is legitimate and how to make room for it.

### 5.4 Which row to read: `use_for_delta`, `scale`, `role`

Each feature has several parameters (population mean, cross-region spread,
region values, log and natural scales). **Exactly one family per feature has
`use_for_delta = TRUE`** — the one the delta arithmetic is valid for:

| Pooling | Signed | Free |
|---|---|---|
| global | `glogbeta_*` (log) | `gbeta_*` |
| hierarchical | `mu_logbeta_*` (log) | `mu_beta_*` |
| independent | `logbeta_*` (log) | `beta_ifree` |

- **Read the c and shift of that row.** The `role` column labels the rest:
  `non-centred offset` rows are N(0,1) by construction and never informative.
- **Rows dropped on purpose.** A region × feature with no activity in the
  training window is dropped, because its contribution is 0 whatever the
  coefficient.
- **`output.report_intercept: false`** removes the intercept rows when they
  crowd the file.

### 5.5 The three-curve plots — `prior_posterior/<parameter>.png`

Each plot shows three curves:

- the **prior**;
- the **posterior**;
- the **implied likelihood** — what the data alone says, by precision
  subtraction.

How to read them:

| Picture | Meaning |
|---|---|
| Likelihood and prior overlap | Agreement |
| Likelihood far from the prior, posterior in between | Conflict |
| Likelihood flat or absent | No data signal: the posterior is the prior |

Capped at `output.prior_posterior_max` (60) plots.

**The summary plot** (`prior_posterior_contraction.png`) puts every
`use_for_delta` parameter on one chart:

- colour by contraction: **red** < 0.2, **amber** < 0.5, **green** otherwise;
- **±2 shift lines**: anything outside them is in conflict.

### 5.6 Prior predictive check — `prior_predictive_check.png`

- **Computes.** Simulates the KPI from the priors alone, with no data, and
  overlays the actual scaled KPI.
- **Reading it.**

  | Picture | Meaning | What follows |
  |---|---|---|
  | Prior band covers the actuals and is somewhat wider | Healthy | — |
  | Band hugely wider | Priors too vague | Fine for fitting, but contraction will be high everywhere and the sampler may struggle |
  | Band narrower than the actuals, or offset | Priors fight the data | Shows up later as §5.2 conflicts. Usually units again |

---

## 6. Coefficients: material, supported, and in which units

**Files:**

- `03_coefficients/coefficient_report.csv`: one row per feature × region, plus
  `__population__` for pooled features;
- `support_warnings.txt`;
- `forest/<feature>.png`.

### 6.1 Median, HDI, `prob_positive`, `excludes_zero`

- **Computes.** The posterior median and the **90% highest-density interval**,
  the shortest interval holding 90% of the draws (not percentiles).
- **Reading it.** An HDI spanning an order of magnitude means "we cannot tell",
  whatever the other columns say.
- **For a free feature**, `excludes_zero` and `prob_positive` are real evidence
  of direction.
- **For a sign-constrained feature** they are 1 and TRUE **by construction**.

### 6.2 `t_stat`

- **Computes.** Posterior mean ÷ posterior sd: signal-to-noise.
- **Why it changed.** The old production code divided by `mcse_mean`. The Monte
  Carlo standard error shrinks as you draw more samples, so its "significance"
  grew simply by sampling longer.
- **Reading it.** Posterior sd is a property of the posterior, not of run
  length.
  - |t| ≳ 2 is the analogue of "clearly non-zero" for a free feature.
  - For a signed (log-normal) coefficient, t = 1 ÷ coefficient of variation:
    **t < 1 means the sd exceeds the mean** — very uncertain even though it
    cannot cross zero.

### 6.3 `p_value` and `p_value_basis`

- **Computes.** 2 × min(P(β > 0), P(β < 0)): the posterior probability of the
  wrong sign, doubled.
- **Read `p_value_basis` first.**

  | `p_value_basis` | Meaning |
  |---|---|
  | `posterior sign` | Free feature. The p_value is evidence: the posterior could have come out the other way and did not |
  | `sign-constrained (vacuous)` | β = ±exp(·) cannot cross zero, so `p_value` = 0 **always**. It says nothing about the data. Never quote it |

### 6.4 `prob_negligible` (ROPE) — read it with care under raw-unit features

- **Computes.** P(|β| ≤ `output.rope_scaled`): the share of the posterior inside
  a region of practical equivalence (default 0.01).
- **Theory.** For a sign-constrained coefficient the honest question is not "is
  it non-zero?", which is guaranteed, but **"could it be too small to matter?"**.
  - Near 0: materially bigger than nothing.
  - Near 1: the model cannot rule out that it does essentially nothing.

> ⚠️ **The ROPE is in β units, and β is per raw feature unit under the default
> scaling.** The 0.01 default was designed when every feature was scaled to
> ≈ 1, so it meant "under 1% of the KPI's scale per typical unit of activity".
> Since the `none`/`none` default (2026-09-22) a feature unit is whatever the
> datacube holds — a GRP, a point of distribution, a currency unit of price. The
> same 0.01 is then a different threshold for every feature.
>
> **Example (illustrative).**
>
> - A TV variable averaging 200 GRPs a week with β = 0.0005 moves the KPI by
>   0.1 of its scale each week — clearly material. Yet |β| ≤ 0.01 on every
>   draw, so `prob_negligible` = **1.00**.
> - A distribution variable on a 0–1 scale with β = 0.05 reads
>   `prob_negligible` = **0.00**, however small its effect is.
>
> **Until the ROPE is per feature, judge materiality on
> `05_contributions/contribution_totals.csv`.** `share_of_actual_pct` and
> `volume_hdi_low`/`volume_hdi_high` are unit-free. Alternatively, set
> `rope_scaled` for the one feature you are asking about:
>
> > material β = (smallest weekly effect that matters, in KPI units) ÷
> > (`dv_scale` × typical weekly x)

### 6.5 `data_support` and `support_warnings.txt`

| Value | Rule | Meaning | Change |
|---|---|---|---|
| `none` | 0 active training periods, or zero variance | The coefficient multiplies nothing in that region | Nothing; its contribution there is 0 |
| `weak` | Fewer than 8 active training periods (raw column) | Too few weeks to learn from. Under pooling, the value is the **hierarchy's**, borrowed from other regions | Do not present it as regionally estimated. **Config:** `pooling: hierarchical` if it is independent |
| `weak (near-constant)` | Uncentred, and sd ÷ mean of its non-zero values < 0.1 — in practice always on and barely moving | Only the tiny wiggle identifies it; the level is spanned by the intercept | **Config:** `center_mode: mean` (§2.2) |
| `adequate` | Otherwise | Enough variation to learn from | — |

`support_warnings.txt` lists every non-adequate cell. **Anything listed there must
not be presented as a regional finding.**

### 6.6 Original units — `median_orig_units`, `hdi_*_orig_units`

- **Computes.** β × `dv_scale` ÷ feature scale, i.e. **KPI units per raw feature
  unit**. For example, "one extra GRP → 35 units of sales a week".
- **Reading it.** This is the business sanity check. Compare it with the vendor's
  response, the spend, and plain sense. A coefficient that implies 1 GRP = 5% of
  weekly sales is wrong however good the diagnostics look.

### 6.7 Forest plots — the pooling check

`forest/<feature>.png` shows each region's coefficient with its HDI.

| Picture | Reading | Change (config / priors) |
|---|---|---|
| Every region identical | Pooling too strong: `regional_sd_prior` tiny, or `pooling: global` | Raise `regional_sd_prior` (TUNING_GUIDE §1.5) |
| Wildly different, small regions widest | No pooling | `pooling: hierarchical` |
| Big regions tight, small ones pulled toward the population line | **Working as intended** | — |

---

## 7. Fit quality

**File:** `04_fit/fit_metrics.csv`. One row per region × `train`/`test`, plus:

- `__all__`: every region pooled;
- `__aggregate__`: regions summed to one national series per date.

The example column below is v3 (train / test).

| Metric | Computes | v3 | Read it as |
|---|---|---|---|
| `r2` | 1 − SSE ÷ SST against **one grand mean** | 0.994 / 0.977 | **Inflated on `__all__`** by level differences between regions; it can be 0.99 while every region fits poorly. Do not quote it |
| `r2_within_region` | 1 − SSE ÷ Σ over regions of variance around **each region's own mean** | **0.596 / −0.662** | The honest "does it explain movement?" number. **Negative on test** = worse than a flat line at each region's test mean |
| `mape_pct` | mean \|e ÷ y\| | — | Unstable when some weeks are small |
| `wmape_pct` | Σ\|e\| ÷ Σ\|y\| | — | Total error as a share of total volume. **The CV selection metric** |
| `mape_region_weighted_pct` | Per-region MAPE, volume-weighted | — | "Average regional error, big regions first" |
| `coverage_90_mean_pct` | % of actuals inside the 90% band of the **mean response** | 43.7 | **Expected to be far below 90**: that band excludes week-to-week noise. Not a defect |
| `coverage_90_pred_pct` | % inside the 90% **posterior predictive** band | **91.2 / 73.8** | Target ≈ 90. Train 91 = calibrated in sample. **Test 74 = over-confident out of sample** |
| `crps` | Continuous ranked probability score, in KPI units | — | A proper scoring rule: it rewards calibration *and* sharpness. Lower is better. Only meaningful compared across runs on the same data |
| `resid_t_stat`, `resid_p_value` | t-test of H0: mean residual = 0 | p 0.400 (train) | With an intercept, train is ≈ 0 by construction. **On test**, small p = systematic over- or under-prediction (drift) |
| `durbin_watson` | See §8.3. On `__all__`, the mean of the per-region values | 1.63 | — |

**`__aggregate__`** is the like-for-like comparison with a national total-sales
model. It always looks better than the regional rows, because summing cancels
regional noise. **Quote it next to them, never instead of them.**

**The two pictures.**

- **`04_fit/actual_vs_fitted.png`** shows, per region, the actual series and
  the median fit with both 90% bands; the holdout is shaded orange. Read the
  *shape* of any miss:
  - a fitted line drifting away through the holdout → trend or seasonality;
  - misses only in promo or event weeks → a missing driver;
  - a fit that tracks every training wiggle and then fails the holdout →
    over-fitting.
- **`04_fit/residuals.png`** shows residual vs fitted, plus the residual
  histogram, with **all regions pooled, in KPI units**.
  - A funnel here can simply be region size: big retailers have bigger errors.
    The per-region `homoscedasticity` row (§8.2) is the actual test.
  - Curvature is the linearity signal (§8.1).

**The holdout reading in this project.** Test `r2_within_region` ≈ −0.66 and
predictive coverage 74% have been stable across runs. Every region's fitted line
drifts down through Oct–Dec while the actuals recover. Two causes:

- the latent trend extrapolates;
- `fourier_order: 2` is far too smooth for Q4, with only one prior Q4 to learn
  from.

This is a **specification** problem, not convergence or collinearity.

**Change.**

- *Config:* `model.include_trend` (on or off, judged on CV); a higher
  `fourier_order` (watch §2.2's overfit warning).
- *Variables:* explicit Q4/event dummies.
- *Config:* judge the change with CV (§14), not one holdout.

| Symptom | Likely cause | Change |
|---|---|---|
| Train fine, test r² far lower | Overfitting, or the test window has a new regime | Fewer parameters; CV; check the holdout weeks for events |
| Test coverage ≪ 90 | Intervals too narrow out of sample: autocorrelation (§8.3), drift | Fix the specification; never widen intervals by hand |
| Test residual mean significantly ≠ 0 | Trend / level drift | `include_trend`; a level-shift dummy |
| Train coverage ≫ 95 | Noise over-estimated, model too loose | Usually harmless. Check `sigma` against the KPI's scale |

---

## 8. Residual assumptions

**File:** `04_fit/assumption_checks.csv`.

- Per region, on the **training window**, using actual − median fitted, in KPI
  units.
- Every row carries `what_it_means` and `what_to_do`.
- The readout is `04_fit/assumptions_report.md`.

Bayesian regression rests on the same structural assumptions as OLS. When they
fail, the result is not "invalid standard errors" but a **misspecified
likelihood**: intervals that are too narrow, and coefficients bent toward a few
weeks.

### 8.1 Linearity — |corr(residual, fitted)| < 0.2

- **Theory.** If the response is linear in the (pre-transformed) inputs, the
  residual carries no signal correlated with the fitted value. In OLS with an
  intercept this correlation is **exactly 0** in sample.
- **Reading it here: the sign tells you which way the priors push.** In a
  Bayesian model the correlation is non-zero when priors stop the fit from
  following the data:
  - **corr > 0**: fitted values swing **too little**. Coefficients are shrunk or
    pinned below what the data wants (prior too small, or too tight).
  - **corr < 0**: fitted values swing **too much**. Coefficients are pinned
    **above** what the data wants. In a secondary model with vendor-pinned rows,
    this is the vendor's effects being too big for this data.
  - Large either way, with priors that are not tight: a genuine functional-form
    problem — the adstock/saturation upstream is wrong, or a driver is missing.
- **Change.**
  - *Priors:* check the pinned rows (c < 0.2) against the direction of the sign.
  - *Variables:* revisit the transforms (Phase 2 learns them in-model), or add
    the missing driver.

### 8.2 Homoscedasticity — sd(residual), top third ÷ bottom third of fitted < 1.5

- **Theory.** One noise sd (`sigma`) per region assumes the error size does not
  grow with the level. If peak weeks have errors 2× quiet weeks, the intervals
  are too wide in quiet weeks and too narrow in peaks. The tercile ratio is used
  instead of corr(|e|, ŷ) because it is more powerful: a doubling of the error
  shows up as only about 1.7 between tercile means, hence 1.5, not 2.
- **Change.**
  - *Config:* there is no log-KPI switch. Modelling log sales is an upstream
    transform that makes the decomposition multiplicative, a bigger decision.
  - Usually: accept, and state that peak-week intervals are understated.
    `pool_sigma` does **not** help; it pools across regions, not across time.

### 8.3 Independence — Durbin-Watson in 1.5–2.5, and ACF at lags 2, 4, 13

- **Theory.** DW = Σ(e_t − e_{t−1})² ÷ Σe_t² ≈ 2(1 − ρ₁).
  - 2 means no autocorrelation.
  - Below 1.5, consecutive errors are positively correlated: something
    persistent is missing (trend, seasonality, carryover).
  - The cost is hidden: the **effective sample size is smaller than the row
    count, so every interval is too narrow**.
- **Example (v3).** DW 1.63, so ρ₁ ≈ 0.19 and the effective n ≈ 91 × 0.81 ÷
  1.19 ≈ 62. Intervals are roughly √(91 ÷ 62) ≈ **1.2× too narrow**: mild, but
  real.
- **ACF at lags 2, 4 and 13.** |r| < 0.3.
  - A spike at lag 13 (a quarter, on weekly data) means the seasonal block is
    too smooth.
  - **On monthly panels**, lag 13 is not a seasonal lag and has very few pairs.
    Ignore it there.
- **Change.**
  - *Config:* `include_trend`; a higher `fourier_order`.
  - *Variables:* a longer adstock or lag upstream; event dummies.
- **This is the assumption to take most seriously on a weekly panel.**

### 8.4 Skew — |skew| < 1

- **Theory.** A skewed residual means the model is systematically wrong on one
  side. In MMM that is typically under-predicting peaks: promo weeks with no promo
  variable.
- **Change (variables):** the missing promotional or event driver.

### 8.5 Tails — excess kurtosis < 1, unless `likelihood: student_t`

- **Theory.** Under a Normal likelihood a few extreme weeks have unbounded pull:
  every coefficient bends toward them.
- **Change (config):** `model.likelihood: student_t`. It down-weights extreme
  weeks without deleting data. The check stops flagging once Student-t is on.

### 8.6 Influential observations — count beyond 3 sd

- **Theory.** Under a Normal distribution, 0.27% of points lie beyond 3 sd. On
  91 weeks that is 0.25 points. The check allows max(1, int(0.003n) + 1), i.e.
  **one**; two or more warn.
- **Change.**
  - Find them in `04_fit/actual_vs_predicted.csv` (largest |residual|).
  - A real event → **variables:** a dummy.
  - A data error → fix the extract.
  - **Never delete silently.** Or use **config:** `likelihood: student_t`.

---

## 9. Posterior correlation: can the fitted model separate them?

**File:** `04_fit/posterior_correlation.csv`.

- **Computes.** The Pearson correlation between the **posterior draws** of
  every pair of region-level coefficients, per region.
- **Threshold.** Pairs at |r| ≥ 0.7 are listed; 0.9 or more is `severe`.
- **Theory.** For collinear columns the likelihood pins their weighted sum, so
  across draws, when one coefficient is high the other is low. The draws are
  strongly **negatively** correlated. The model knows the sum and not the split,
  so **neither coefficient, and neither contribution, can be read alone**.
- **Example (v1).** Two always-on variables sharing the intercept's level gave
  TDP +91% and AVP −97%: equal and opposite, summing to roughly nothing.

### The four-way reading — always next to `contraction`

| Pre-fit VIF | Posterior corr | Contraction | Reading |
|---|---|---|---|
| low | low | high | **Clean.** Identified and learned from data |
| high | high (negative) | any | **Trading off.** Report the pair's sum |
| high | **low** | **low** | **The dangerous one.** Tight priors pinned both, so they *look* separate. The data never separated them; the split is your assumption |
| low | low | low | The feature has no signal; the prior is the answer |

Row 3 is why a clean posterior-correlation file is not, on its own, good news.

**Change.**

- *Variables:* merge the pair, or pool into a pillar and report the pillar
  total.
- *Config:* centre if they share a level.
- *Priors:* fix **one** coefficient from external evidence, and say so.

---

## 10. Structural checks adopted from Meridian

Meridian runs almost no residual tests, but it runs three structural checks.
All three address failures this project has actually had.

### 10.1 Confounding — `04_fit/confounding_pairs.csv`

- **Computes.** The correlation between every **treatment** (a feature with
  `baseline = 0`) and every **control** (`baseline = 1`: distribution, price),
  per region.
- **Threshold.** Flags at |r| ≥ max(0.1, 2/√n). That is 0.21 on 91 weeks: a
  flat 0.1 would flag about a third of pure-noise pairs on a panel this short.
  The flag is `high` at |r| ≥ 0.5, otherwise `review`.
- **Theory.** This is Meridian's `PotentialBiasCheck`. If media rises whenever
  distribution rises, the media coefficient may be absorbing distribution's
  effect, and **no goodness-of-fit number will ever say so**. It is a property of
  the design, so it is a statement about what the coefficient *can mean*, not a
  defect a prior can fix.
- **Example (illustrative).** `tv_base` vs `tdp_base` at r = 0.55 (`high`).
  Launches got TV and distribution together, so the TV coefficient is TV plus
  some of distribution's lift.
- **Reading it.** **Empty file = no controls marked.** The check needs
  `baseline = 1` on the control variables; the generated prior file sets it from
  the share file's baseline section.
- **Change.**
  - Report the pair together.
  - *Variables:* if the overlap is a known launch, a launch dummy can separate
    it.
  - *Priors:* an informative prior on one of the two, **from an experiment**.
  - Otherwise state the coefficient as "TV including co-moving distribution".

### 10.2 Aggregate posterior predictive p-value — `structural_checks.csv` → `ppp`

- **Computes.** Take the posterior predictive draws of the **total** KPI over the
  training window, per region and overall (`__all__`). The p-value is the share
  of draws at least as far from their centre as the actual total is:

  > ppp = P(|expected total − centre| ≥ |actual total − centre|)

  It fails below 0.05.
- **Theory.** Weekly coverage can be perfect while the **annual total** sits in
  the model's tail. That is exactly the shape of a decomposition that reconciles
  to 100% and is still wrong.
- **Reading it here.**
  - **With a free intercept, ppp on the training window passes almost
    automatically.** The intercept is precisely the parameter that matches the
    total.
  - The check earns its keep in the **secondary model** (`include_intercept:
    false`). There nothing absorbs the level, and pinned vendor coefficients may
    not add up to this data's sales.
- **Change.**
  - *Priors:* in a secondary model, a failing region means the LOCKED rows do not
    sum to its sales. Loosen a DONOR row (METHODOLOGY §2c).
  - *Config:* or restore the intercept with a tight `alpha_prior_sd`.

### 10.3 P(baseline < 0) — `structural_checks.csv` → `p_negative_baseline`

- **Computes.** The share of draws in which the region's **total baseline** is
  negative. The baseline is core (intercept + seasonality + trend) plus every
  `baseline = 1` feature.
- **Thresholds.** Review at ≥ 0.2; fail at ≥ 0.8.
- **Theory.** Meridian's `BaselineCheck`. A negative baseline says sales would be
  negative with no marketing, which is not a business statement. It is the
  signature of **drivers over-claiming** while the baseline absorbs the offset.
  This project has seen exactly that: `baseline_core` strongly negative, features
  above 100%.
- **Causes, in the order to check:**
  1. Units (`generated_prior_units`).
  2. Shares summing past 100% (§1.2).
  3. Two collinear level variables both claiming the level (§9).
  4. A free intercept going negative to make room.
- **Change.**
  - *Config:* `model.alpha_prior_sd: 0.05`, then `include_intercept: false`
    (TUNING_GUIDE §2.1–2.2).
  - *Priors:* lower the over-claiming means; check `prior_summary.csv`.

---

## 11. Exogeneity and endogeneity

**File:** `04_fit/exogeneity_cross_correlation.csv`; also §4d of
`assumptions_report.md`.

### 11.1 The assumption, in MMM terms

A regression coefficient is causal only if the regressor is **exogenous**:
uncorrelated with the error term, meaning everything that drives sales and is
not in the model. When it is not, the variable is **endogenous** and its
coefficient is biased, with no warning from any fit statistic. In an MMM this
happens in four ways:

| Mechanism | MMM example | Direction of bias |
|---|---|---|
| **Reverse causality / budget-setting** | Spend follows expected sales: the budget goes where and when sales were going to be high anyway | Media over-credited (ROI overstated) |
| **Feedback** | Budget released after a good quarter, rescue spend after a bad one | Either way, depending on the rule |
| **Omitted variable** | A driver that moves with your activity is missing (category demand, competitor activity, a launch), so its effect loads onto whatever correlates with it | Toward the missing driver's effect |
| **Measurement error** | GRPs mis-recorded, or impressions as a noisy proxy for exposure | Toward zero (attenuation) |

The last two are why the vendor comparison matters here. The vendor's retailer
models carry **Category (~55.8%)** and **Competition (−4.9%)**, and our retailer
panel has neither.

### 11.2 Why the obvious test cannot work, and what the file does instead

The obvious test is to correlate each feature with the residual. **It is
useless at lag 0.** For a regressor that is in the model, the fit drives
corr(x_t, e_t) to ≈ 0 whether or not the true error is independent of x. A zero
there proves nothing, so the file marks lag 0 `by_construction = TRUE`.

The fit does **not** force the correlation to zero at **other lags**:

| Lag k | Correlation | Reading | What it usually means |
|---|---|---|---|
| **k > 0** | corr(x_t, e_{t+k}) | **The feature leads the error** | Activity today predicts what the model gets wrong *later*: the carryover (adstock decay or lag) is mis-specified upstream, or the effect's shape is wrong |
| **k < 0** | corr(x_t, e_{t−\|k\|}) | **The error leads the feature** | **The endogeneity that matters**: past surprises in sales predict today's activity, i.e. spend responding to how sales have been going. The regressor is then correlated with the error, and the coefficient is biased |

- **Settings.** Lags ±`exogeneity_max_lags` (4).
- **Flag.** `review` at |r| ≥ max(0.2, 2/√n), which is 0.21 on 91 weeks.
- **Minimum length.** The region needs n ≥ max(10, 3 × lags).

**Example (illustrative).**

- `tv_base` at k = −1, −2, −3 reads +0.28, +0.31, +0.25 in four of five regions.
  Weeks where sales beat the model are followed by more TV, which is the budget
  rule chasing sales. Its coefficient is probably overstated.
- `promo_flag` at k = +1 reads −0.33. The week after a promo, the model
  over-predicts: post-promo **dip** or pantry-loading, which the transform does
  not capture.

### 11.3 How to read the file without fooling yourself

The file runs **features × 8 lags × regions** tests: 27 × 8 × 5 = 1,080 on the
retailer panel. At a two-standard-error threshold about 5% flag by chance, so
**expect around 50 flags from pure noise.** Believe a flag when:

1. **It repeats across regions**, with the same sign in most of them.
2. **It runs across adjacent lags** (k = −1, −2, −3), not a lone spike.
3. **It is well above the threshold**, not just at it.
4. **It has a business story**: the budget really does react to sales, or the
   product really is pantry-loaded.

A single region at a single lag at 0.22 is noise.

### 11.4 What to change

| Finding | Change |
|---|---|
| **k > 0** (feature leads the error) | **Variables:** re-transform upstream with a longer adstock decay or an added lag; for a negative k > 0, add a post-event dip variable. Phase 2 learns adstock in the model, which removes this class of error |
| **k < 0** (error leads the feature: budget follows sales) | **Variables:** add the demand driver the budget is reacting to (category, seasonality, a trend). The spend is then reacting to something *in* the model, not to the error. **Priors:** an informative prior **from an experiment** (geo test, holdout region). **Reporting:** state the coefficient as an upper bound |
| Omitted driver suspected | **Variables:** add it (category, competition) and watch which coefficients fall. The ones that fall were carrying it |
| Measurement error suspected | Fix the data. No model setting corrects attenuation |

### 11.5 What no statistic in this codebase can settle

**Purely contemporaneous simultaneity** — spend set from a forecast of this
same week — is invisible to residual-based tests at any lag. Two things answer
it, and neither is a statistic of the fit:

- **design**: a geo experiment, a holdout region, a switchback test, or an
  instrument;
- **an ROI prior taken from such a design.**

Treat a clean exogeneity file as **necessary, not sufficient**.

---

## 12. Contribution arithmetic and reconciliation

**Files:** `05_contributions/contribution_reconciliation.csv`,
`contribution_math.csv`, `contribution_summary.csv`.

### 12.1 `reconciles_to_actual_pct` = 100 is an identity, not a check

The reconciliation chain is: components + median gap = fitted; fitted + residual
= actual. So **`reconciles_to_actual_pct` is exactly 100 by construction.** It
proves the bookkeeping, not the model.

> **The unit rule.** If priors and data disagree on units (`dv_scale`,
> `scale_mode`), every contribution is wrong by a constant multiple and the file
> still reads 100. No reconciliation check can catch a units error; §1.2's
> `generated_prior_units` and §5.2's same-signed shifts are the checks that can.

### 12.2 `median_gap_pct` — the two arithmetics

- **Computes.** The median of a total is not the sum of the medians of its
  parts. The gap is the difference.
- **Reading it.**
  - A fraction of a percent is normal.
  - Several percent means the contribution posteriors are skewed (wide,
    log-normal), so the parts will not add to the reported total.
- **Change.** Nothing in the model. Report totals as medians of the total, and
  say the parts are medians too.

### 12.3 `contribution_math.csv` — recompute any contribution by hand

**Computes, per region × feature:**

> volume = β_scaled × Σ(x_scaled + reference_shift) × dv_scale

with every factor printed.

| Column | Reading |
|---|---|
| `recomputed_diff_pct` | Median β × sum vs the reported median total. Differs because the median of a product ≠ the product of medians. Over ~10% means a wide coefficient posterior: quote the HDI |
| `median_basis_diff_pct` | Sum of weekly medians vs median of the total (§12.2) |
| `scaled_sum_train` ≈ 0 | A **centred** feature under `contribution_reference: auto` is measured against its own training mean, so its contribution is ≈ 0, or comes only from the holdout weeks. **Config:** `contribution_reference: zero` |
| `effective_scaled_sum` | What actually multiplies β, after the reference shift |

**Use it when** a contribution "can't be right". OUTPUTS_GUIDE has a table of the
usual causes. Nine times out of ten, one factor in this row is the answer.

---

## 13. Benchmark comparison

**File:** `05_contributions/benchmark_comparison.xlsx`, written when a mapping
file is configured.

- Paste the vendor or benchmark contributions into the `benchmark` column (TOTAL
  block, or per region).
- Every other cell is a live formula.
- The derivation is in `prior_posterior_contraction_fitting.md`.

| Column | Computes | Reading |
|---|---|---|
| `pct_diff` | (ours − theirs) ÷ \|theirs\| × 100 | Within ±10% is `ok` |
| `ratio` R | theirs ÷ ours | The multiplier that closes the gap. A split variable's members share their **group's** R |
| `contraction` | From §5, the `use_for_delta` row | Decides whether the prior can close the gap |
| `suggested_prior` | current × R^(1/(1 − max(c, 0))) | The prior mean that lands on the benchmark. Exact for a signed variable; for a free one only when c is small |
| `delta` | ln(R) ÷ c | How far the data wants to move, in log units |

**Verdicts:**

| Verdict | Condition | Change |
|---|---|---|
| `ok` | \|pct_diff\| < 10 | — |
| `UNIDENTIFIED` | c < −0.2 | Fix collinearity first (§3, §9). Any prior written now is arbitrary |
| `prior-driven` | c < 0.2 | **Priors:** write `suggested_prior` back. The result will be *your* number, and must be reported that way |
| `data disagrees` | c > 0.5 | **Priors:** impose (tighten sd) with named evidence, or **accept the gap**. Repeated mean corrections will not converge |
| `mixed` | Otherwise | Check `delta` against the other rows |

**The summary block: read it first.**

- **delta CLUSTERS** (IQR < 0.1, with at least 3 rows): one **global**
  constraint is pushing every driver the same way. The usual cause is a term the
  benchmark does not have, such as a free region intercept claiming part of a
  fixed total. Correcting prior means one by one cannot hold: the next refit
  re-imposes the same shortfall.
  - **Config:** `alpha_prior_sd`, `include_intercept: false` (the secondary
    model).
- **delta SCATTERS**: the gaps are per variable, and `suggested_prior` is the
  fix.

**Example (v9): why three rounds of corrections did not converge.**

- `tdp_base`: R 4.10 → 0.892 → still ~11% over. c climbed 0.607 → 0.923 →
  0.965, and a 4.4× cut to the prior moved the contribution 1.4pp.
- The exponent explodes as c → 1: multipliers of 36 and 324, and 58,701,935 for
  a dummy. Above c ≈ 0.7 the correction is an extrapolation.

The four causes were:

1. A national prior averaged over regions where the contribution was
   concentrated. **Config:** `national_basis: weighted`.
2. `pooling: global` cannot match a regional pattern. **Priors:** the regional
   file.
3. c not constant.
4. The adding-up constraint with 60 rows pinned.

**The stopping rule** is METHODOLOGY §2d: correct **once** at c < 0.7. Above
that, impose or accept; do not iterate.

---

## 14. Cross-validation and model selection

**Folder:** `06_cross_validation/`, written when `cv.enabled: true`.

- Expanding-window folds: each fold trains on everything before its test window
  and predicts the next `horizon` periods.
- Every fold is a full refit, scaled on its **own** training window
  (`scaling_window` is forced to `train`).

### 14.1 Accuracy across folds — `cv_fold_metrics.csv`, `cv_summary.csv`, `cv_accuracy_by_fold.png`

These are the §7 metrics on each fold's test window. **Read the spread, not just
the mean.** A model whose wMAPE is 6% on four folds and 25% on one has a regime
it cannot handle, usually Q4.

Two printed notes matter:

- **Fewer folds than requested.** Earlier origins would fall below
  `min_train_periods`, so the CV ran fewer folds than you asked for.
- **A region set that differs across folds.** Stability then covers only the
  regions every fold shares.

### 14.2 Coefficient stability — `cv_stability_by_region.csv`, `cv_stability_ranking.csv`, `stability/*.png`

- **Computes.** Each fold's posterior median coefficient, per feature × region.
  - `rel_sd_pct` = sd ÷ |mean| × 100 across folds.
  - `avg_rel_sd_pct` averages that over regions.
- **Theory.** This is the MMM-specific test. The deliverable is a decomposition,
  not a forecast. A coefficient that swings as the window moves **cannot support
  a budget decision**, however well the model predicts.
- **Reading it.**
  - A median below ~15% is stable.
  - **Above 50% for a feature** counts as unstable (`n_unstable_features`).
  - The by-region table says *which* region is unstable. That is usually the one
    with weak support (§6.5).
  - Fold-to-fold changes in `dv_scale` (each fold scales its KPI on its own
    window) add a few percent of apparent instability. That matters only near
    the thresholds.
- **Change.**
  - *Variables:* merge or pool the unstable, collinear ones.
  - *Config:* `pooling: hierarchical` for region-unstable features.
  - *Priors:* or accept that the coefficient is prior-led and report it as such.

### 14.3 The scorecard — `cv_scorecard.csv`

One row per CV run: the unit of comparison between candidate models.

**Admissibility is a gate, not a score.** A run is excluded outright if:

- any fold has R-hat > 1.05;
- any fold has **any** divergence;
- mean predictive coverage falls outside **70–98%**.

The reason is in `why_not`. You cannot trade convergence against accuracy.

### 14.4 Choosing between runs — `compare_cv_runs` → `cv_model_comparison.csv`

`select_model` applies four rules, in order:

1. **Admissible runs only.**
2. **Same folds only.** Different `folds_signature` (cadence / horizon / folds /
   min-train) → refuse. Their accuracies are not comparable.
3. **Accuracy beyond the noise.** Runs within **one standard error** of the best
   mean test wMAPE count as **tied**. Picking the smallest number out of a
   cluster narrower than the fold-to-fold spread is selecting on noise. Paired
   fold wins are reported alongside.
4. **Stability, then parsimony.** Among tied runs, the lowest median coefficient
   instability wins. If that differs by under 1pp, the fewest parameters win.

**Use it** to settle a specification argument with evidence — `include_trend`
on/off, `fourier_order` 2 vs 4, a merged vs split pair — instead of one holdout.

---

## 15. The warnings folder

`00_warnings/00_INDEX.md` is written at the end of the run.

- It counts every warning by category and severity.
- It links one document per category, with what it means and the fix.
- Library chatter goes to `library_notices.csv`.
- **Read the high-severity categories first**: each means a reported number is
  probably not what it appears.

| Category | Severity | Covered in |
|---|---|---|
| `prior_pins_coefficient` | high | §1.3 |
| `prior_mean_not_a_magnitude` | high | §1.3 |
| `negative_values_uncentred` | high | §2.2 |
| `collinear_with_intercept` | high | §2.2, §3.3 |
| `near_constant_mutual` | high | §2.2 |
| `degenerate_feature_column` | high | §2.2 |
| `intercept_without_centering` | high | §2.2 |
| `generated_prior_units` | high | §1.2 |
| `collinearity_not_computable` | medium | §3.8 |
| `pooling_collapsed` | medium | §1.3 |
| `per_region_prior_sd_ignored` | medium | §1.3 |
| `seasonality_overfit_risk` | medium | §2.2 |
| `cadence_ambiguous` | medium | §2.2 |
| `generated_prior_blank` | medium | §1.2 |
| `negative_mean_read_as_size` | review | §1.3 |
| `scaling_uses_holdout` | review | §2.2 |
| `regional_prior_sign_skipped` | review | §1.2 |
| `prior_deliberately_pinned` | info | §1.3 |
| `relative_sd_read_as_absolute` | info | §1.3 |
| `other` | review | Read each one. The CONCENTRATED, shares-add-up and blank-mean warnings currently land here |

---

## 16. Threshold reference

**Column key.** "**Yes**" in the YAML column means the `config.yaml` key changes
the check. "**Partly**" or "**No**" means some or all of the check uses the
built-in default whatever the YAML says; the note says which part.

### 16.1 Collinearity (§3)

| Check | Default | YAML key | Reaches the check? |
|---|---|---|---|
| VIF warn / severe | 5 / 10 | `assumptions.vif_warn` / `vif_bad` | Yes — the verdict, `n_vif_over_10`, the `duplicates` label and which columns get `explained_by` (since 2026-10-06) |
| Condition number warn / severe | 10 / 30 | `assumptions.cond_warn` / `cond_bad` | Yes |
| Pairwise \|r\| list / severe | 0.8 / 0.95 | `assumptions.pair_warn` / `pair_bad` | Yes. `pair_warn: 0` dumps every pair. `pair_bad` sets the heatmap annotations |
| Culprits named per column | 3 | `assumptions.vif_top_k` | Yes |
| Heatmap on / max columns | true / 40 | `assumptions.corr_heatmap` / `heatmap_max_features` | Yes |
| Near-constant (data warning) | 0.1 sd ÷ level | `run.near_constant_sd` | Yes |
| Dust column | 1e-12 | `run.min_feature_scale` | Yes — only when a `scale_mode` is set |

### 16.2 Convergence (§4)

| Check | Default | YAML key | Reaches the check? |
|---|---|---|---|
| R-hat warn / fail | 1.01 / 1.05 | — | **No** (built in) |
| ESS bulk / tail | 400 | — | **No** |
| Divergence guardrail | 1% | — | **No**. `run.on_convergence_failure` sets warn vs fail |
| BFMI | 0.3 | — | **No** |
| Tree depth flagged | ≥ 10 | — | **No** |

### 16.3 Prior vs posterior and coefficients (§5–§6)

| Check | Default | YAML key | Reaches the check? |
|---|---|---|---|
| Contraction warning / verdict bands | 0.2; 0 / 0.2 / 0.5 | — | **No** |
| Prior–data conflict | \|shift\| > 2 | — | **No** |
| ROPE | 0.01 (β units) | `output.rope_scaled` | Yes — but see §6.4 on units |
| `data_support` weak | < 8 active periods | — | **No** |
| `data_support` near-constant | 0.1 | — | **No**. Uses 0.1, not `run.near_constant_sd` |

### 16.4 Residual assumptions (§8)

| Check | Default | YAML key | Reaches the check? |
|---|---|---|---|
| Durbin-Watson band | 1.5 – 2.5 | `assumptions.dw_lo` / `dw_hi` | Yes |
| Linearity | 0.2 | `assumptions.linearity_max_corr` | Yes |
| Heteroscedasticity ratio | 1.5 | `assumptions.hetero_ratio_max` | Yes |
| ACF (lags 2, 4, 13) | 0.3 | `assumptions.acf_max` | Yes |
| Skew / excess kurtosis | 1 / 1 | `assumptions.skew_max` / `kurtosis_max` | Yes |
| Influence | 3 sd | `assumptions.influence_sd` | Yes |

### 16.5 Posterior correlation, structural checks and exogeneity (§9–§11)

| Check | Default | YAML key | Reaches the check? |
|---|---|---|---|
| Posterior corr list / severe | 0.7 / 0.9 | `assumptions.post_corr_warn` / `post_corr_bad` | **Partly.** `post_corr_warn` is used. **`post_corr_bad` is not**: the `severe` label always uses 0.9 |
| Confounding | max(0.1, 2/√n); `high` ≥ 0.5 | `assumptions.confound_warn` | Yes (the 0.5 is built in) |
| Aggregate PPP fail | 0.05 | `assumptions.ppp_fail` | **No**: the verdict always uses 0.05 |
| P(baseline < 0) review / fail | 0.2 / 0.8 | `assumptions.neg_baseline_review` / `neg_baseline_fail` | Yes |
| Exogeneity lags / threshold | ±4 / max(0.2, 2/√n) | `assumptions.exogeneity_max_lags` / `exogeneity_warn` | Yes |

### 16.6 Benchmark and cross-validation (§13–§14)

| Check | Default | YAML key | Reaches the check? |
|---|---|---|---|
| Benchmark `ok` / verdict bands / cluster IQR | 10% / −0.2, 0.2, 0.5 / 0.1 | — | Excel formulas: edit them in the sheet |
| CV gates | R-hat 1.05, 0 divergences, coverage 70–98% | — | **No** |
| CV unstable feature | > 50% | — | **No** |

### 16.7 Pre-model (§1)

| Check | Default | YAML key | Reaches the check? |
|---|---|---|---|
| Concentration warning | 1.25× | — | **No** |
| Fourier overfit warning | training periods < 6 × order | — | **No** |

> **The report text prints the built-in values.** `assumptions_report.md` prints
> the default VIF, condition, pair, posterior-correlation, confounding, PPP and
> negative-baseline thresholds in its prose even when config.yaml changes them.
> The CSV verdicts follow the rules above. **When you change a threshold, trust
> the CSV, not the prose.**

---

## 17. Symptom → check → fix

| Symptom | Confirm with | Most likely cause | Change |
|---|---|---|---|
| R-hat > 1.05, saturated tree depth, offsetting ± contributions | §3.3 `duplicates = the intercept/level`; §9 | Level variable collinear with the intercept | **Config:** `center_mode: mean` + `contribution_reference: zero` |
| `collinearity_vif.csv` blank, condition number `inf` | §3.8 `vif_note`; `n_columns` > `n_obs` in the summary | More design columns than training periods in the region | **Variables:** merge / drop / pool. **Config:** lower `fourier_order`. Meanwhile read contraction |
| Divergences, low BFMI | §4.3, §4.5; worst-R-hat on `tau_*` | Hierarchical funnel; over-wide log prior | **Priors:** tighten `regional_sd_prior` / `global_prior_sd`. **Config:** `target_accept` 0.95+ |
| Contraction ≤ 0 | §4 first, then §5.4, then §3 | Non-convergence; natural-scale row; duplicate column | Fix convergence, read `use_for_delta`, then **variables:** merge/drop |
| Two coefficients trade off (corr < −0.7) | §9 + §3.2 | Collinear pair | **Variables:** merge, or pool and report the sum |
| A number is "too low/high" vs the vendor, c < 0.2 | §5, §13 | It **is** your prior | **Priors:** `suggested_prior`, reported as an assumption |
| Too low/high, c > 0.5 | §5.3 | The data disagrees | Units first; then accept, or impose with evidence. Do not iterate |
| \|shift\| > 2 on many features, same sign | §5.2 | Units mismatch | **Config:** `dv_scale` / `scale_mode` to match the priors (§1.2) |
| Every delta the same in the benchmark sheet | §13 summary | One global constraint (a free intercept) | **Config:** `alpha_prior_sd: 0.05` → `include_intercept: false` |
| Repeated corrections oscillate | §13, v9 | c high, concentration, global pooling | `national_basis: weighted`; regional priors; stop at one correction |
| `r2` 0.99 but `r2_within_region` 0.6 | §7 | Region levels inflating pooled r² | Quote `r2_within_region` |
| Holdout drifts away while actuals recover | §7, §8.3 | Trend extrapolating; seasonality too smooth | **Config:** `include_trend`, `fourier_order`. **Variables:** Q4 dummies. Judge with CV |
| DW < 1.5 | §8.3 | Missing persistent driver | **Config:** trend/seasonality. **Variables:** longer adstock/lag upstream |
| Kurtosis / outliers flagged | §8.5–8.6 | A few extreme weeks | **Variables:** event dummies. **Config:** `likelihood: student_t` |
| Linearity corr negative in a pinned model | §8.1 | Pinned effects too big for this data | **Priors:** loosen or lower the LOCKED rows |
| P(baseline < 0) ≥ 0.2 | §10.3 | Drivers over-claiming | Units → shares → collinearity → intercept |
| Media correlated with distribution | §10.1 | Design confounding | Report jointly; experiment-based prior |
| Lead-lag flags at k < 0 across regions | §11 | Budget follows sales | **Variables:** add the demand driver. **Priors:** experiment. Report as an upper bound |
| Lead-lag flags at k > 0 | §11 | Carryover mis-specified | **Variables:** re-transform upstream (Phase 2) |
| Centred feature reports ≈ 0% | §12.3 `scaled_sum_train` ≈ 0 | Measured against its own mean | **Config:** `contribution_reference: zero` |
| `prob_negligible` ≈ 1 on a big-contribution feature | §6.4 | ROPE in raw units | Judge on `share_of_actual_pct` and its HDI |
| A coefficient moves every refit | §14.2 | Window-dependent estimate | **Config:** `cv.enabled: true`; pool; merge |
| Region coefficients identical | §6.7 | Pooling too strong | **Priors:** raise `regional_sd_prior` |

---

## 18. What the checks cannot tell you

1. **Whether a coefficient is causal.** Exogeneity is only partly testable
   (§11). The lead/lag file catches feedback; nothing in the fit catches
   same-week simultaneity or an omitted driver that moves exactly with your
   activity.
2. **Whether the units are right, from the reconciliation alone.** A units error
   reconciles to 100% (§12.1). Only the pre-model units warning and same-signed
   prior–data conflicts (§5.2) point at it.
3. **Whether the adstock / saturation transforms are right.** Codebase 1 takes
   them as given. The k > 0 exogeneity lags and the linearity check are indirect
   hints; Phase 2 learns the transforms in the model.
4. **Whether a pinned number is true.** A pinned prior passes every check by
   construction: c ≈ 0, no conflict, no trade-off. `contraction` is the only file
   that says it was pinned, so put it next to every number you report.
5. **Out-of-sample truth from one holdout.** One window is one draw. Use CV
   (§14) before changing the specification on the strength of a test score.

**Known gaps in the checks themselves.** These are documented, not yet fixed:

- **Unwired thresholds.** `assumptions.ppp_fail` and `assumptions.post_corr_bad`
  are accepted by config.yaml but not used.
  `data_support`'s near-constant test ignores `run.near_constant_sd`. The
  assumptions report's prose prints the built-in thresholds (§16).
- **The ROPE is in raw feature units** under the `none`/`none` default (§6.4).
- **Axis labels.** Forest plots and the CV stability plots label the
  coefficient axis "scaled axis: KPI sd per feature sd". Under the `none`/`none`
  default the feature side is the **raw** unit, and the KPI side is whatever
  `run.dv_scale` is (`sd` by default, `mean` for generated priors).
- **Uncategorised warnings.** The CONCENTRATED, shares-add-up and blank-mean
  warnings land in `00_warnings/other.md` rather than their own category.
