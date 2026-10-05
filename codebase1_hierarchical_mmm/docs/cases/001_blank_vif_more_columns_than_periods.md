# Case 001 — blank VIF file and "severe" everywhere on a monthly panel

| | |
|---|---|
| **Date** | 2026-10-06 |
| **Panel** | The v9-type panel: 12 regions (3 brands × 4 channels), **monthly**, **21 training periods** per region |
| **Design** | 77 features, 17 of them dummies → **80 columns** per region |
| **Status** | Diagnosed. Reporting fixed in codebase **2026.10.06.1**. The modelling response is below and in METHODOLOGY §3c |
| **Docs** | CHECKS_GUIDE §3.8 (blank cells) · METHODOLOGY §3c (short panels) · METHODOLOGY §2c (the secondary model) |

## What we saw

On every run:

- `01_data/collinearity_vif.csv` — every VIF blank.
- `01_data/collinearity_summary.csv`:
  - `max_vif` = 0;
  - `condition_number` between 105 and 507 (Base_Tradicionales 507,
    Base_Droguerias 489, Base_SuperCadena 355, Ninos_Superetes 106);
  - verdict `severe` in all 12 regions;
  - 9–19 flagged correlation pairs per region.

## Why — in simple words

VIF asks, one column at a time: **can the other columns predict this one?** It
answers by running a small regression of that column on all the others.

A regression needs more rows than unknowns.

- Each region has **21 rows** (training months).
- The regression for one column has **79 unknowns** (the other 79 columns).

That is 21 equations with 79 unknowns. There are infinitely many exact
solutions, so the "prediction" is perfect for **every** column, whatever the
data. A perfect prediction means VIF = infinity — for all 80 columns, by
arithmetic, not because of anything about these particular variables.

> **School version.** 2 equations, 2 unknowns → one answer. 2 equations, 79
> unknowns → any answer you like.

The code saw that the regression could not be run (`_aux_r2` returns NaN when
rows ≤ unknowns) and left the cell blank, without saying why.

## What was wrong in the outputs — fixed in 2026.10.06.1

The blank itself is arithmetic. Three things around it were reporting bugs:

| Output | Before | Why it was wrong | Now |
|---|---|---|---|
| `collinearity_vif.csv` | Every number blank, no reason | A blank looked like a broken check | `vif_note` on every row: "not computable: 80 design columns vs 21 training periods…" |
| `max_vif` (summary) | 0 | Reads as "no collinearity", the opposite of the truth | Blank, with a `note` saying why |
| `condition_number` | 105–507 | With more columns than rows the design is **exactly singular**, so the true value is infinite. The SVD of a 21 × 80 matrix returns only 21 singular values; their ratio is not the condition number of the design | `inf` |
| Features with no activity in a region (one brand's media in another brand's regions) | Counted as columns | They multiply nothing there, but inflated the column count and the condition number | Excluded per region, listed with a note, counted in `n_dead_columns` |
| Console / warnings | "max VIF 0.0" | — | Warning `00_warnings/collinearity_not_computable.md` |

## The external diagnosis — what to keep, what to correct

A Databricks Genie diagnosis of the same outputs found the right root cause.
Four of its points need correcting before anyone acts on them:

| Genie said | Keep or correct |
|---|---|
| 21 rows vs 80 columns; `_aux_r2` returns NaN; "not a bug — a data constraint" | **Keep the cause.** The blank is arithmetic. The silence around it, `max_vif = 0` and the finite condition number were reporting bugs — now fixed |
| "The condition number IS computed — 105 to 507 — because SVD works regardless of the n-vs-p ratio" | **Correct this.** The SVD runs, but returns only 21 singular values for 80 columns. The design has at least 59 exact dependencies and its condition number is infinite. **105–507 meant nothing**; do not read a per-region condition number when columns exceed periods |
| "Rely on the condition number and correlation pairs instead" | **Keep the pairs file and the heatmaps** — they are valid at any ratio. Not the condition number |
| "With 21 rows, no more than ~5–7 features" | **True only for a model fitted region by region** (`pooling: independent`). Under `global` or `hierarchical` pooling a coefficient is estimated from every region where the variable moves. What 21 periods really cap is the **time-only** columns — the ones that move the same way in every region: national dummies, a brand's media across its channels, Fourier, trend. METHODOLOGY §3c |
| "Tighter informative priors", "group into pillars", "more data" | **Keep**, with the label that goes with it: a pinned coefficient is an input, not a finding (METHODOLOGY §2c) |

## How to confirm on your own run

1. `collinearity_summary.csv`: **`n_columns` > `n_obs`** → this case.
2. From 2026.10.06.1 on:
   - the summary's `note` column starts "VIF not computable";
   - `vif_note` in the VIF file gives the counts;
   - `00_warnings/collinearity_not_computable.md` exists.
3. `n_dead_columns` shows how many brand-specific columns were excluded in each
   region. If excluding them brings `n_columns` below `n_obs`, the VIFs come
   back for that region.

## "Severe" means three different things

| Source | How to tell | Meaning | Action |
|---|---|---|---|
| More columns than periods | `note` starts "VIF not computable"; condition number `inf` | Arithmetic: nothing can be measured region by region | Restructure the design (below); read contraction |
| Level collinearity of raw, always-on variables | `duplicates` = `the intercept/level` or `a shared constant level`; high `vif_uncentred` | Sampling geometry: the levels move together | Centre (`center_mode: mean`) when the model has an intercept |
| Genuine feature-on-feature collinearity | High centred `vif`, `explained_by`, the pairs file | The split between those variables is not in the data | Merge, pillar, or prior (CHECKS_GUIDE §3.7) |

## What to do — in order

**1. Re-upload codebase 2026.10.06.1 and rerun.** Brand-specific columns are now
excluded in the regions where they never run, so `n_columns` shows the real
per-region count. Re-upload the whole `mmm/` folder: `assumptions.py` is now
version-stamped, and a partial upload prints OUT OF SYNC.

**2. Count what the data actually has to separate.** Not all 80 columns are
estimated:

- **Pinned rows don't count.** A coefficient fixed by its prior (the LOCKED
  rows, `global_prior_sd: 0.02` relative) is not estimated; it acts as a known
  offset. In the v9 register about 60 of the rows were pinned, so the data has
  to separate the free ones: `tdp_*`, `category_vol_*`, the 17 free dummies and
  `ucm`.
- **Split the free columns into two kinds:**
  - *Time-only* (same movement in every region of a brand): dummies, brand
    media, Fourier, trend, a national category series. **Within a brand they
    share at most 20 separable time patterns on 21 months**, however many
    channels the brand has.
  - *Cross-region* (different movement by channel): TDP, price, distribution
    by channel. These gain from pooling.
- A common guideline is 10–20 periods per freely estimated coefficient. On 21
  months that is one or two time-only coefficients from the data alone. More is
  allowed, but the extra ones lean on their priors — and `contraction` will say
  so.

**3. Shrink the time-only block.** It is where the budget goes.

- **17 dummies** are 17 time-only columns. A dummy that is 1 in a single month
  fits that month exactly: the month stops informing anything else. Merge
  same-kind events into one variable (all price increases, all stock-outs),
  drop events with no business story, or pin the ones whose effect you know.
- **`fourier_order`**: on fewer than two years, seasonality cannot be told
  apart from trend and events. Use 1 at most, or 0 if the dummies already carry
  the seasonal events.
- **`include_trend`**: decide on the holdout, not on the in-sample fit.

**4. Model tangled groups as pillars, with a stated split.** Where the pairs
file or heatmap shows a block (several media of one brand moving together):

- build the pillar as one column upstream, in the datacube, in a common unit
  (spend or GRPs);
- estimate the pillar once;
- split its contribution inside the pillar by spend or GRP share, or the
  vendor's split;
- report the total as estimated and the split as allocated.

**5. Use pooling for what it can do.**

- `global` (one coefficient for all regions) for variables that are time-only
  within a brand: pooling averages noise but cannot separate them.
- `hierarchical` only for the cross-region drivers you need per region.
- Avoid `independent` on this panel: it is the one setting where the 21-row
  limit applies to every coefficient.

**6. Set the priors with the secondary-model register** (METHODOLOGY §2c):

- LOCKED from the vendor or other evidence where the data cannot separate;
- DONOR for the rows allowed to give up share;
- CANDIDATE only for the one to three variables you are testing.

**7. Read the right metric.** In this regime the per-region VIF is gone;
**`contraction`** (the `use_for_delta` row in
`02_convergence/prior_posterior_contraction.csv`) tells you, coefficient by
coefficient, whether the data or the prior decided it:

- above 0.5 → estimated by the data;
- below 0.2 → set by the prior;
- `04_fit/posterior_correlation.csv` → which free coefficients trade off;
- the pairs file and heatmaps → which columns tangle.

**8. Report the basis of every number:** *estimated* (contraction > 0.5),
*assumed from <source>* (< 0.2, including LOCKED rows), or *allocated within
<pillar> by <rule>*.

## What not to do

- **Don't read the per-region condition number when `n_columns` > `n_obs`.** It
  is infinite; any finite number from an older run is meaningless.
- **Don't treat "severe" as an instruction to drop variables.** Find which of
  the three sources above it is first.
- **Don't chase benchmark gaps on tangled variables with repeated prior
  corrections.** That is v9's oscillation (METHODOLOGY §2d).
- **Don't take the in-sample fit as evidence** that the coefficients are right.
  With this many columns on 21 months, judge on the holdout and on contraction.

## Code changes (2026.10.06.1)

- `mmm/checks/assumptions.py`:
  - `vif_note` column;
  - features with no activity in a region excluded from that region's design;
  - summary `n_dead_columns` and `note`, and `max_vif` blank instead of 0;
  - condition number `inf` when columns exceed rows;
  - the `collinearity_not_computable` warning;
  - `vif_warn`/`vif_bad` now reach `duplicates` and `explained_by`.
- `mmm/checks/warnings_report.py`: the new category.
- `mmm.checks.assumptions` added to the version-stamped modules.
- `tests/test_v14_assumptions.py`: 18 new checks. All 1811 pass.

## Still open — better tooling for this case

Proposed in METHODOLOGY §3c; not built:

- VIF on the **stacked** design (all regions, free columns only), so collinearity
  is measured on the design the pooled model actually estimates;
- a **time-only budget** per brand;
- **dependency groups** — which variables form each tangle;
- **pillar totals with intervals**, so an identified total can be told apart from
  an unidentified split;
- a **region + period effects** model option.
