# Casebook — problems we have hit, and what to do

One file per real case: what we saw, why it happened, how to confirm it on your
own run, and what to change. **Before diagnosing something odd in a run, look
here first** — it may have happened before.

The other guides are references (every check, every lever, every column). A
case is the opposite: one concrete situation, start to finish, written so the
next person who meets it can act in ten minutes.

## Cases

| # | Case | Symptom | Date | Status |
|---|---|---|---|---|
| [001](001_blank_vif_more_columns_than_periods.md) | Blank VIF file and "severe" everywhere on a monthly panel | `collinearity_vif.csv` all blank; `max_vif` 0; every region `severe` | 2026-10-06 | Diagnosed; reporting fixed in 2026.10.06.1; modelling response written |
| [002](002_median_gap_not_traceable.md) | The median gap is bigger than the baseline gap, and the rest cannot be traced | `median_gap` 962,225 vs `baseline_total − baseline_features` 942,713; 19,512 unaccounted for | 2026-10-07 | Fixed in 2026.10.07.2: gap split in three, plus a statement file |

## Earlier cases, documented elsewhere

These happened before the casebook existed. Each is written up in full where
the link points. Promote one to its own case file when it recurs.

| Run / date | Symptom | Cause | Where it is written up |
|---|---|---|---|
| v1 | R-hat 1.26, tree depth saturated 100%, TDP +91% / AVP −97% | Always-on level variables collinear with the region intercept | CHECKS_GUIDE §2.2, §3.3 · TUNING_GUIDE §4.2 · `../../../CLAUDE.md` "Run history" |
| v5 (BMC) | Contributions off by 1.2× / 8.3× / 14.9× by region, yet reconciling to 100% | Priors and KPI in different units (`dv_scale`) | `../../../CLAUDE.md` "The scaling rule" · TUNING_GUIDE §4 |
| v3 onwards | Holdout `r2_within_region` ≈ −0.66; fitted line drifts down through Q4 | Trend extrapolating; seasonality too smooth for Q4 | CHECKS_GUIDE §7 · `../../../CLAUDE.md` "Run history" |
| v7 | Intercept claims ~90% of sales (`mu_alpha` 0.911) | A free level out-competing every driver | TUNING_GUIDE §2.1–2.2 |
| v9 | Three rounds of prior-mean corrections oscillate instead of converging | Concentration, global pooling, high contraction, the adding-up constraint | METHODOLOGY §2c–2d · `../../../CLAUDE.md` "The v9 panel" |
| 2026-09-24 | "No contribution found" although the mapping file has contributions | Stale module copy on Databricks; regions written as tuple-strings | CHECKS_GUIDE §1.1 · FEATURE_PRIOR_GUIDE (`region` column) |
| 2026-09-24 | Loader crash `sign must be one of ... got 'nan'` | Blank `sign_constraint` cell | FEATURE_PRIOR_GUIDE (blank cells) |

## Adding a case

Copy the template below into `NNN_short_name.md` (next free number), fill it
in, and add a row to the table above. Write it for a modeller who has the run
folder open and has not read this conversation. Use the real numbers from the
run, and say which ones are computed, measured or estimated.

```markdown
# Case NNN — <the symptom, in the words someone would search for>

| | |
|---|---|
| **Date** | |
| **Panel** | regions × periods, cadence, what the regions are |
| **Config** | the settings that matter (pooling, intercept, scaling, ...) |
| **Status** | diagnosed / fixed in <version> / open |
| **Docs** | the guide sections that cover it |

## What we saw
Files and values, exactly as they appeared.

## Why — in simple words
The cause, with a small example. Then the precise version.

## What was wrong in the outputs (if anything)
Before → why it was wrong → now.

## How to confirm on your own run
Which file, which column, which value.

## What to do — in order
Variables / config / priors, cheapest first.

## What not to do
The tempting fix that makes it worse.

## Code changes
Version, files, tests.
```
