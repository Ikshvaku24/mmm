# Case 002 — the median gap is bigger than the baseline gap, and the rest cannot be traced

| | |
|---|---|
| **Date** | 2026-10-07 |
| **Panel** | A client run (any panel shows it; the size depends on how skewed the coefficients are) |
| **File** | `05_contributions/contribution_reconciliation.csv` |
| **Status** | Fixed in codebase **2026.10.07.2**: the gap is split into its three parts, and a statement file reads top to bottom |
| **Docs** | OUTPUTS_GUIDE (`contribution_reconciliation.csv`, `contribution_reconciliation_chain.csv`) · CHECKS_GUIDE §12.2 |

## What we saw

From the reconciliation file of one run:

- `baseline_total_volume − baseline_features_volume` = **942,713**
- `median_gap_volume` = **962,225**

That leaves **19,512** unexplained. The file had no column it could come from.
`reconciles_to_actual_pct` was still exactly 100.

## Why — in simple words

Every component in the reconciliation is a **median** of its posterior draws.
**Medians do not add up.**

A three-draw example, two features A and B:

| Draw | A | B | A + B |
|---|---|---|---|
| 1 | 1 | 10 | 11 |
| 2 | 2 | 1 | 3 |
| 3 | 10 | 2 | 12 |
| **median** | **2** | **2** | **11** |

The sum of the medians is 4; the median of the sum is 11. The 7 between them is
a median gap. It is a reporting artefact, not model error: the model is the
same whichever way you summarise it.

Real posteriors are far less extreme, but sign-constrained coefficients are
log-normal (right-skewed), and with ~30–80 of them the small per-feature
differences pile up in one direction.

The gap arises in **three places**, and before 2026.10.07.2 the file lumped all
three into one `median_gap`:

| Gap | Between | Visible before? |
|---|---|---|
| **Baseline gap** | the median of the baseline total, and core + the sum of the baseline features' medians | Only by hand: `baseline_total − core − baseline_features` |
| **Incremental gap** | the median of the incremental total, and the sum of the incremental features' medians | **No** — no incremental total was computed |
| **Cross gap** | the median of fitted (baseline + incremental), and median(baseline) + median(incremental) — the same effect between the two block totals | **No** |

So in this run, the 942,713 was the baseline gap — plus the baseline core, if the
model had one; the hand subtraction could not tell them apart. The 19,512 was
the incremental gap and the cross gap together, with no way to see which.

## What changed (2026.10.07.2)

**`contribution_reconciliation.csv`** gains four columns, with `_pct` twins:

- `baseline_median_gap_volume` = `baseline_total − (core + baseline features)`
- `incremental_total_volume` — the median of the incremental block as one total
- `incremental_median_gap_volume` = `incremental_total − incremental`
- `cross_median_gap_volume` = `fitted − (baseline_total + incremental_total)`

The three gaps add up to `median_gap_volume` exactly, and every block closes:

```
core + baseline features      + baseline gap    = baseline total
incremental features          + incremental gap = incremental total
baseline total + incremental total + cross gap  = fitted
fitted                        + residual        = actual
baseline gap + incremental gap + cross gap      = median gap
```

**`contribution_reconciliation_chain.csv`** (new) prints those five blocks as a
statement, 18 lines per scope × region, with the volume, the % of actual sales
and a one-line meaning for each line. Filter `scope = all`,
`region = __portfolio__` and read down.

## How to read it on your run

1. **The model's error is `residual_volume`, not the median gap.** Judge the fit
   on the residual. The gap is arithmetic.
2. **Which block is skewed?**
   - A large baseline gap → wide, log-normal baseline coefficients. In the v9
     register those were the loose ones: `tdp_*`, `category_vol_*`, `ucm`.
   - A large incremental gap → many wide media coefficients.
3. **A wide coefficient is usually one the data barely moved.** Check those
   features' `contraction` (CHECKS_GUIDE §5). Low contraction plus a wide
   prior is the usual source of a big gap.
4. **Reporting:**
   - tables that must add up (the deck, `contribution_summary.csv`) use sums of
     medians, with the gap on its own line;
   - a single feature quoted with an interval comes from
     `contribution_totals.csv` (the median of its window total, with an HDI).

## What not to do

- **Don't fold the median gap into the residual.** On the v4 panel the combined
  line read 2.23%, of which only 0.51% was model error. Reporting them together
  overstated the error about fourfold.
- **Don't change priors to shrink the gap.** It is a property of how skewed the
  posteriors are, not a misfit. A tighter prior does shrink it, but only by
  replacing data with assumption.

## Code changes (2026.10.07.2)

- `mmm/reporting/reconciliation.py`:
  - `write_contribution_reconciliation` — the four new columns;
  - `write_reconciliation_chain` — the new statement file, under the same
    `output.contribution_reconciliation` flag.
- `tests/test_v5_outputs.py`: 11 new checks (every identity, the
  directly-computed incremental total, every chain block closing, the reading
  order).
- `contribution_summary.csv` and `contribution_timeseries.csv` keep their single
  `__median_gap__` row: the web app's charts read them.
