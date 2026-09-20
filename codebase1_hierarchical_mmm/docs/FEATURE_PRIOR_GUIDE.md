# Feature prior guide — filling in the prior CSV

One row per feature. This file is the source of truth for **which columns are
modelled** and **what the model believes about each one before it sees the
data**. Everything else lives in `config.yaml` (see `CONFIG_GUIDE.md`).

You do not have to write it by hand. If the client gave you a **vendor
decomposition** or a **pillar/spend breakdown**, the pre-model step generates it
for you — jump to §5.

---

## 1. The columns

| Column | Required | Default | What it does |
|---|---|---|---|
| `variable` | **yes** | — | must match the data column name **exactly** |
| `region` | no | blank | blank = a feature-level row. Fill it on an **extra** row to override one region's prior |
| `pooling` | no | `hierarchical` | `hierarchical` / `independent` / `global` |
| `sign_constraint` | no | `free` | `positive` / `negative` / `free` |
| `global_prior_mean` | no | 0.05 (signed), 0.0 (free) | the **magnitude** of the coefficient |
| `global_prior_sd` | no | 1.0 (signed), 0.5 (free) | how hard the prior resists the data |
| `regional_sd_prior` | no | 0.5 | how far regions may differ |
| `prior_sd_basis` | no | `log` | how to READ the two sds: `log` / `relative` / `absolute` |
| `prior_mean_basis` | no | `median` | is the mean the median or the mean of the coefficient? |
| `center_mode` | no | `none` (signed), `mean` (free) | `none` / `mean` |
| `scale_mode` | no | derived | `none`/`sd`/`mean`/`mean_positive`/`max` |
| `baseline` | no | 0 | 1 = fold into the baseline instead of reporting as incremental |
| `pillar` | no | blank | reporting roll-up group |
| `contribution_reference` | no | `auto` | `auto`/`zero`/`mean`/`min`/a number. **Reporting only** |

> **`center` has been REMOVED.** `center_mode` is the only centring setting. A
> file that still carries a `center` column is rejected with instructions —
> two columns meaning the same thing is exactly how a `center=1` silently
> became a no-op in v7, when an explicit `center_mode` won and overwrote it.

---

## 2. The three that cost the most when wrong

### `global_prior_mean` is a MAGNITUDE, never a direction

For a sign-constrained feature the model builds `beta = ±exp(·)`. The sign comes
from `sign_constraint`; the mean is how big the effect is. **A negative price
effect is `sign_constraint=negative` with a positive mean**, not a negative
mean. Writing a negative mean there triggers a warning and the value is replaced.

```csv
variable,sign_constraint,global_prior_mean
avp,negative,0.30          <- right
avp,positive,-0.30         <- wrong: the value is discarded
```

### `prior_sd` is on the LOG scale unless you say otherwise

`prior_sd_basis=log` (the default) means the number IS the sigma the model
samples with. `0.7` ≈ a factor of two; `0.2` ≈ ±20%.

**The classic mistake** is writing `0.2 * prior_mean` because "20% of the mean"
sounds right. On a mean of 0.05 that is `prior_sd=0.01` — ±1%, which pins the
coefficient so hard the data cannot move it. Your reported contribution is then
just the number you typed.

| You want | Write | With |
|---|---|---|
| ±20% uncertainty | `global_prior_sd: 0.2` | `prior_sd_basis: relative` |
| a factor of ~2 | `global_prior_sd: 0.7` | `prior_sd_basis: log` |
| sd in coefficient units | the sd itself | `prior_sd_basis: absolute` |

`01_data/prior_summary.csv` prints `implied_rel_sd` — the direct check on "did
my 0.2 actually mean 20%?".

### `center_mode=mean` for always-on LEVEL variables

Distribution, price index, ACV. Without it they scale to ~1.0 every period and
duplicate the region intercept — the defect that broke `real_data_v1` (R-hat
1.26, TDP +91% / AVP −97% cancelling out).

**Pair it with `contribution_reference=zero`**, or the reported contribution
collapses to ~0: a centred column sums to zero over the training window, so
measuring "versus its own average" gives you nothing.

```csv
variable,sign_constraint,global_prior_mean,center_mode,contribution_reference,baseline
tdp,positive,0.20,mean,zero,1
avp,negative,0.30,mean,zero,1
```

---

## 3. Per-region overrides

Leave `region` blank on the feature-level row, then add an **extra row** with it
filled:

```csv
variable,region,pooling,sign_constraint,global_prior_mean,global_prior_sd
TV_GM,,hierarchical,positive,0.05,1.0
TV_GM,1-Walmart+FamilyDollar,,,0.20,
TV_GM,3-Target-Corp,,,0.02,
```

- Under `hierarchical` only the **mean** is used, as a fixed offset to the
  shrinkage centre. A per-region `sd` is ignored and warned about.
- Under `independent` both mean and sd apply.
- Under `global` an override **raises** — there is one coefficient for everyone.
- A region name not in the data **stops the run**. That is deliberate: a typo'd
  region would otherwise be silently ignored and you would never learn your
  prior did not apply.

---

## 4. The prior ladder — how confident are you actually?

From `METHODOLOGY.md` §2. Start every unknown feature at level 0 or 1 and move
it up only when you can name the evidence.

| Level | What you know | `global_prior_sd` | `prior_sd_basis` |
|---|---|---|---|
| 0 | direction only | `0.7` | `log` |
| 1 | rough magnitude from spend share | `0.5` | `relative` |
| 2 | a prior study or category benchmark | `0.3` | `relative` |
| 3 | your own experiment (geo test, switchback) | `0.1–0.2` | `relative` |
| 4 | an imposed benchmark you are reproducing | `0.02` | `relative` |

> **Level 4 is not estimation.** A prior derived from a benchmark and then
> pinned reproduces the benchmark and validates nothing — the agreement is
> circular. `contraction` will be ~0. That is a legitimate choice when you are
> deliberately imposing a number, but say so when you present it.

---

## 5. Generating the file — the pre-model step

Point `config.yaml` at whatever the client gave you and run the pre-model step.
It writes `feature_priors_sample.csv` plus `prior_calculation.xlsx` showing
every intermediate number.

```bash
python -m mmm.data.prior_builder config.yaml
```

It also runs automatically at the front of `run_from_yaml` when an input is
configured. **It never overwrites `data.feature_priors`** — you review the
generated file and point at it yourself.

### Case A — a vendor decomposition exists

```yaml
data:
  vendor_contribution: vendor_contribution.csv
  benchmark_mapping: benchmark_map.csv     # only if they combine variables
  dv_aggregation: mean                     # mean | sum | median
```

A contribution is `beta × Σx × dv_scale`, so the coefficient that reproduces it
is the contribution divided back out:

```
prior_mean[feature, region] = contribution / support / dv_agg
```

- **`support`** = Σ of the RAW feature over the modelling window. Raw, because
  the vendor's number is in raw units.
- **`dv_agg`** = the region's KPI aggregate (mean by default — set
  `dv_aggregation` to match how the vendor expressed theirs).
- The per-region values are averaged, **dividing by the number of regions that
  had support** — not by the region count. A feature that never ran in a region
  contributes nothing there, so a zero would drag the average down.

Input shape (`samples/vendor_contribution_sample.csv`):

```csv
variable,region,contribution
TV,Core,1250000
Digital,Core,880000
Average Price,Core,-7600000
```

**Negative contributions** become `sign_constraint=negative` with the absolute
value — because the mean is a magnitude. **Signs that disagree across regions**
are flagged and the sign of the total is used; that is a finding about the
driver, not a prior problem.

**When the vendor combines variables** — one "Digital" line where the model
carries four columns — give it a mapping
(`samples/benchmark_mapping_sample.csv`):

```csv
feature,benchmark_group
media__digital-social_hero_impressions,Digital
media__digital-display_hero_impressions,Digital
btl_expert-samples_premium_mat1,Samples Premium
btl_expert-samples_premium_mat2,Samples Premium
```

The **support of the members is summed first**, one prior mean is computed for
the group, and that mean is replicated to every member — which is right,
because they share an implied coefficient. The same mapping is reused later so
the benchmark comparison sheet compares one row to one row.

### Case B — no decomposition, but you know spend

```yaml
data:
  pillar_spend: pillar_spend.csv
```

Input shape (`samples/pillar_spend_sample.csv`):

```csv
pillar,feature,feature_spend,pillar_share_pct
TV & DTV,media__tv_core_grps,1800000,6.0
TV & DTV,media__tv_halo_grps,600000,6.0
Online Media,media__digital-social_hero_impressions,900000,4.5
Baseline,tdp,,62.0
```

```
contribution[f] = pillar_share_pct/100 × total_sales × spend[f] / pillar spend
prior_mean[f]   = contribution[f] / support[f] / dv_agg
```

`pillar_share_pct` belongs to the **pillar**: write it once or repeat the same
number on every row of that pillar. Rows that disagree are an error, not a
silent pick. Shares totalling over 100% warn.

This deliberately gives every feature in a pillar the **same implied
efficiency**. That is not a claim they are equally efficient — it is the
least-informative start that still has the right total. With a wide
`global_prior_sd` the data has room to move them apart, and **how far each one
moves is the result**.

A pillar whose features carry no spend (baseline drivers, dummies) is split by
**support** share instead; `split_basis` in the calculation workbook says which
was used.

### Neither?

Nothing is generated and the run proceeds with whatever `data.feature_priors`
already points at. That is the normal path once you have a prior file you trust.

### What the generated file deliberately does NOT do

- It writes `global_prior_sd: 0.5` (**wide**), not 0.02. See the ladder above.
- It writes `prior_sd_basis: relative`, so 0.5 reads as ±50%.
- It leaves `center_mode`, `scale_mode` and `baseline` blank — those are
  judgements about what a variable *is*, and no arithmetic can make them.
  **Set `center_mode=mean` and `baseline=1` on your level variables by hand.**
- Features with no support anywhere get a blank mean and a note. Drop them or
  fix the extract.

---

## 6. Checking it before you trust it

| Check | Where |
|---|---|
| did my sd mean what I think? | `01_data/prior_summary.csv` → `implied_rel_sd` |
| what does the prior imply as a coefficient? | same file → `implied_median` / `implied_q05` / `implied_q95` |
| is anything pinned by accident? | `00_warnings/prior_pins_coefficient.md` |
| did the data have anything to say? | `02_convergence/prior_posterior_contraction.csv` → `contraction` |
| does it match a benchmark? | `05_contributions/benchmark_comparison.xlsx` |

**The order matters.** Read `contraction` before you read agreement with a
benchmark: a feature whose prior came from the benchmark and whose contraction
is ~0 has not validated anything.

---

## 7. Anti-patterns

1. **Writing `0.2 * prior_mean` as the sd.** ±1%, not ±20%. Use
   `prior_sd_basis: relative`.
2. **A negative `global_prior_mean` on a signed feature.** The value is
   discarded. Use `sign_constraint: negative`.
3. **`center_mode=mean` without `contribution_reference=zero`.** The
   contribution collapses to ~0 and looks like a bug.
4. **Tightening a prior because you dislike the answer.** Tighten only to impose
   information you can name.
5. **Citing agreement with a benchmark on a pinned feature.** Circular.
6. **Keeping a dummy with 5 active periods.** It is a memorised residual.
7. **Pointing `data.feature_priors` straight at the generated sample without
   reading it.** It is a proposal, not a decision.

**Related:** `CONFIG_GUIDE.md` (everything outside this file) ·
`METHODOLOGY.md` (the order to build in) · `TUNING_GUIDE.md` (which lever to
reach for) · `OUTPUTS_GUIDE.md` (every output column).
