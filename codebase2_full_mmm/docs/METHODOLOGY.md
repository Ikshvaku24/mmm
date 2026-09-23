# Methodology — how to build a Phase 2 model

> **Status:** planning, 2026-09-21. **No code yet.**
>
> **Companions** (same folder):
> - `EXPLANATION.md`: what adstock and saturation are, and how they change the answer.
> - `PHASE2_ARCHITECTURE.md`: the full blueprint (every table, every default).
> - `Codebase_2_blueprint.html`: the one-page overview.
> - `EDA_CHECKS.md`: what is checked, and when.
>
> This is the **order of operations**. Codebase 1's `docs/METHODOLOGY.md` is still the
> reference for everything the two codebases share; this file covers what changes once the
> transforms are learned inside the model.

## Contents

0. What arrives, and what we do to it
1. Stage 0: splitting variables by region or period
2. The staged build
3. Deciding what each variable is
4. Setting the transform bounds
5. Priors, on sales volume
6. The data budget
7. Choosing between specifications
8. The bridge test
9. Anti-patterns
10. First-run checklist

---

## 0. What arrives, and what we do to it

**The datacube arrives with the variables already built**, to the data structure. Media, trade
and expert activity are already one column per channel-and-split, and the consumption facts are
already the KPI, distribution, price and category columns.

So there is **no variable-building step**. The earlier builder design is parked in
`VARIABLE_CREATION_parked.md`; do not treat it as the plan.

The only preparation the modeller may need is **splitting a variable that already exists**:

| Split | Meaning | Example |
|---|---|---|
| **By region** | one column becomes one column per region, or per group of regions | TV attributed to one sub-brand: hero in its own region, halo in the others |
| **By period** | one column becomes one column per date range | OOH before and after a buying change; a MAT 1 / MAT 2 cut |

Everything else — scaling, the lag tensor, priors — happens inside the pipeline (blueprint §7).

---

## 1. Stage 0: splitting variables by region or period

### 1.1 When a split is justified

Split only when you can name **why the same activity would work differently** in the pieces.

| Good reason | Example |
|---|---|
| The activity is attributed to one region and only spills into others | sub-brand TV: hero vs halo |
| The buying, creative or measurement changed | OOH bought differently in 2025 than 2024 |
| The vendor's decomposition reports the pieces separately, and you want to compare like with like | `…_invest_2024` / `…_invest_2025` |
| A launch or a one-off regime that the rest of the window cannot represent | first 13 weeks of a new pack |

Not a good reason: the fit improves. That is what cross-validation is for (§7), and a split that
only helps in-sample is a parameter spent on noise.

### 1.2 What a split does mechanically

- **Region split.** The variable keeps its weekly values in the regions the split names, and is
  **zero** in the others. One column can become two (hero / halo) or several.
- **Period split.** The variable keeps its values inside the date range and is **zero** outside it.

In both cases the pieces **add back to the original, week by week**. That is the property to check;
it is what makes a split a re-labelling of the same activity rather than new data.

```
week                 1 ─────────────── 52 │ 53 ─────────────── 104
original OOH         ████████████████████ │ ████████████████████
…_2024               ████████████████████ │ 0 0 0 0 0 0 0 0 0 0 0
…_2025               0 0 0 0 0 0 0 0 0 0 0│ ████████████████████
                     the two pieces add back to the original, every week
```

### 1.3 Rules

| Rule | Why | If broken |
|---|---|---|
| Pieces add back to the original, every week and every region | a split is a re-labelling, not new data | error |
| Periods do not overlap, and together cover the window | otherwise activity is double-counted or lost | error |
| Each piece keeps the original's units | you cannot split GRPs into GRPs and impressions | error |
| Each piece gets its own row in the feature table | sign, pillar, baseline flag and transform settings are per column | error |
| Each piece has enough active weeks | under 5 is a memorised residual; under about 13 a transformed channel cannot show carryover | warning |
| Pieces are not near-duplicates of each other | two pieces correlated above 0.9 will trade off | warning |

### 1.4 What a split costs

Every piece is a new column, so it adds a coefficient per region (2 + G parameters when pooled).
If the piece is a transformed channel it can also add decay and EC50 — unless it shares a
**transform group** with its siblings, which is usually right: the same channel split by period is
still the same channel, and its carryover should not change with the calendar.

> **Default:** pieces of one original variable share a transform group. Override only with a
> reason, such as a genuine change in how the channel was bought.

### 1.5 Naming

Keep the original name and add the split as a suffix, so the pieces sort together and the vendor
mapping still works:

```
media_ooh_all-plac_all-plat_all-camp_sub-brand-unattr_invest          original
media_ooh_all-plac_all-plat_all-camp_sub-brand-unattr_invest_2024     period piece
media_ooh_all-plac_all-plat_all-camp_sub-brand-unattr_invest_2025     period piece
media_tv_…_sub-brand-effervescent_hero_grps                           region piece
media_tv_…_sub-brand-effervescent_halo_grps                           region piece
```

Record the parent in the feature table (`split_of`), so reporting can roll the pieces back up and
the benchmark sheet can compare one vendor line to the sum of our pieces.

---

## 2. The staged build

Same principle as codebase 1: **add in the order of how confident you are that a block belongs, and
stop when a block stops paying for itself.** Phase 2 inserts one extra step — the transforms start
pinned, and are released only once the media block is stable.

Each stage is a full run with its own `run_name`. Keep every stage's output folder; the comparison
between them is the evidence.

| Stage | Add | Transforms | Check before moving on |
|---|---|---|---|
| **1. Baseline** | intercept, trend, seasonality | — | residual autocorrelation. If the baseline alone is already misspecified, everything after it is fitted to a moving target |
| **2. Always-on drivers** | TDP / ACV, base price, category volume and price | — | collinearity between distribution and price; both centred (`center_mode=mean`) with `contribution_reference=zero` |
| **3. Media, transforms pinned** | the media columns, with `fix_alpha`, `fix_ec`, `fix_slope` set to today's preprocessing values | **pinned** | this is the bridge test (§8). The model should now behave like codebase 1 |
| **4. Release the transforms** | nothing new; unpin one channel family at a time | **learned** | contraction of decay and EC50; bound flags; posterior correlation of decay with β |
| **5. The rest** | trade, competitor, dummies | as agreed | the same three numbers, plus whether the media block moved when these arrived |

### The three numbers, extended

Codebase 1 judged a block on three numbers. Phase 2 keeps them and adds two that only exist once
transforms are learned.

| Number | Where | What it says |
|---|---|---|
| `r2_within_region` on test | `06_fit/fit_metrics.csv` | did out-of-sample prediction improve |
| `contraction` | `03_convergence/prior_posterior_contraction.csv` | did the data learn anything about the new coefficients |
| posterior correlation | `06_fit/posterior_correlation.csv` | did the new block make an existing one unreadable |
| **transform contraction** | same contraction file, `raw_alpha_*` / `raw_ec_*` rows | did the data learn the carryover and the curve, or is the prior answering |
| **bound flags** | `04_transforms/transform_parameters.csv` | is a bound, rather than the data, setting a value |

In-sample R² is on none of these lists. It rises when you add noise.

### Releasing transforms, one family at a time

1. Start from stage 3 (everything pinned).
2. Unpin **one** family — TV first, because it usually has the clearest flighting.
3. Re-run. Read: contraction of its decay and EC50, the bound flags, and whether the media
   contributions moved by more than their intervals.
4. If the decay comes back at its bound, or its contraction is ~0, the data cannot see it. Pin it
   again at a defensible value and say so in the write-up.
5. Only then unpin the next family.

Releasing everything at once is the fastest way to a model nobody can explain: when six channels
and their transforms all move together, no single number tells you which one caused it.

---

## 3. Deciding what each variable is

Five questions per column. The answers become its row in the feature table.

| Question | Options | Rule of thumb |
|---|---|---|
| **Transform?** | none / geometric / delayed | anything bought as bursts and expected to linger: transform it. Anything already expressed as a rate or a level (price, distribution, TPR): leave linear |
| **Saturation?** | hill / none | saturate what you might spend more on and want a curve for. Leave it off where the question is only "did it help" (competitor activity, small trade lines) |
| **Baseline?** | 0 / 1 | is it a lever the business pulls (incremental) or the conditions it trades in (baseline)? Distribution, price, category and competitor are baseline; media and trade are incremental |
| **Sign?** | positive / negative / free | any paid activity is positive; price and competitor are negative; dummies are free |
| **Pooling?** | hierarchical / independent / global | hierarchical by default; global for a variable that is identical in every region and rare (most event dummies); independent only when you truly want no sharing |

`baseline` and `transform` are independent: competitor TV is transformed **and** in the baseline.

---

## 4. Setting the transform bounds

The full rules, tables and conversions are in the blueprint (§9). The order to think in:

1. **Start from the business, not the maths.** "TV keeps working for about two to three weeks"
   is a half-life range, and a half-life range is a decay range.
2. **Let the window follow the decay.** `max_lag = ceil(ln 0.05 ÷ ln α_max − 1)`. If you allow a
   three-week half-life, you need a 13-week window; an 8-week window only supports about two weeks.
3. **Check the window against the data.** A 13-week window on a 104-week panel means the first 13
   weeks are missing history unless media from before the window is supplied.
4. **Put EC50 in the units you think in.** "Half-saturates somewhere between 80 and 250 GRPs"
   converts to a scaled range once the median active week is known.
5. **Pin what the data cannot inform.** A channel with no off-weeks cannot show its decay; a
   channel that never rises far above a typical week cannot show its EC50. Pin, and say so.

---

## 5. Priors, on sales volume

**No ROI.** The prior is in the KPI's own units, as in codebase 1.

| Feature type | Prior | Where it comes from |
|---|---|---|
| Saturating channels | **contribution volume**: how many units the channel delivered over the training window | the vendor decomposition, a benchmark, or the spend-share heuristic |
| Linear and carryover-only features | **coefficient**, in codebase 1 units | codebase 1's prior builder, unchanged |

### Using codebase 1's prior builder

`mmm/data/prior_builder.py` turns a vendor decomposition into coefficient priors with:

```
region_coef   = contribution ÷ support ÷ dv_agg          support = Σ of the RAW column
national mean = Σ region_coef over regions with support ÷ that count
```

It writes two files - `feature_priors_national.csv` (hierarchical) and
`feature_priors_regional.csv` (plus a row per region, independent) - with
`scale_mode: none`, so they are only in the model's units with
`dv_scale: mean`, `dv_scale_scope: region`. Codebase 2 must keep that pairing
when it ports `resolve_scaling`, or warn the same way.

- **Linear and carryover-only features:** this still holds. Normalised adstock preserves volume, so
  the support of the adstocked column is the support of the raw one (minus carryover past the end).
- **Saturating channels:** it does **not** hold, because `Σ Hill(Adstock(x))` changes with the decay
  and the EC50. Take the **contribution itself** as the prior target and let the model do the
  division on every draw.

So the same vendor file feeds both routes: one column of numbers, used two ways.

### The ladder, on volume

| Level | What you know | Relative sd |
|---|---|---|
| 0 | only that it contributes something | 1.0 |
| 1 | a plausible total marketing share, split by spend | 0.5 |
| 2 | a vendor or category decomposition | 0.3 |
| 3 | your own experiment | 0.1–0.2 |
| 4 | an imposed benchmark | 0.02 — an assumption, not a finding |

Read `contraction` **before** you read agreement with the benchmark. A prior taken from the
benchmark, pinned tight, will agree with the benchmark and prove nothing.

---

## 6. The data budget

A 104-week panel over a handful of regions does not support an unlimited model.

| What it costs | Parameters |
|---|---|
| A pooled coefficient | 2 + (one per region) |
| A global coefficient (most dummies) | 1 |
| A learned decay | 1 per channel, or 1 per transform group |
| A learned EC50 | 1 per channel, or 1 per transform group |
| A learned peak lag or slope | 1 more each, per channel or group |

Levers, in the order to reach for them:

1. **Transform groups.** Channels in one family share decay and EC50. β stays per column.
2. **Merge splits** that exist for reporting rather than for modelling.
3. **Drop columns with almost no activity** — under 5 active weeks is a memorised residual.
4. **Pin** the transforms of small channels and spend the budget on the big ones.
5. **Pool harder**: `global` for a variable that barely differs by region.

`02_eda` reports rows per parameter. Below 10 it warns; treat anything near 1 as a warning that the
priors, not the data, will decide most answers.

---

## 7. Choosing between specifications

Specifications are compared with expanding-window cross-validation and codebase 1's selection rule:

1. **Admissibility gate:** R-hat, divergences, predictive coverage. A model that fails the gate is
   not compared on accuracy at all.
2. **Accuracy**, but only when better by more than **one standard error** of the fold spread.
3. **Stability**: coefficients, and the learned decay and EC50, across folds.
4. **Parsimony**: fewer parameters wins ties.

What is worth comparing in Phase 2:

| Comparison | Question it answers |
|---|---|
| Split vs unsplit (region or period) | is the split earning its parameters? |
| `min_lag` 0 vs 1 vs 2 | is there a real delay before the effect starts? |
| Transform group vs per-channel | can the data support separate curves per channel? |
| Learned vs pinned decay | did learning it change anything? |
| Hill vs no saturation | is there enough range to see a curve at all? |
| `fourier_order`, trend on or off | is the baseline absorbing what the drivers should explain? |

Run one comparison at a time. Two changes at once and the scorecard cannot attribute the difference.

---

## 8. The bridge test

Before trusting anything Phase 2 says, make it reproduce Phase 1.

1. Pin every transform at the values preprocessing uses today (`fix_alpha`, `fix_ec`, `fix_slope`,
   and `min_lag` for any shift).
2. Use coefficient priors, not volume priors.
3. Fit the same data in both codebases.

The decompositions should agree, up to the change in scaling. A difference is a porting bug, not a
finding. This only works if the adstock and Hill formulas match the preprocessing ones — check that
first, because a different adstock convention (unnormalised weights, a different lag count) will
show up here as a mystery.

---

## 9. Anti-patterns

1. **Releasing every transform at once.** Nothing is attributable afterwards.
2. **Splitting a variable because the fit improves.** Let CV decide, not the in-sample R².
3. **Reading a decay that sits on its bound** as a finding. The bound set it.
4. **Quoting a contribution whose transform contraction is ~0.** The prior wrote that number.
5. **Comparing two runs with different fold geometry.** The scorecard refuses; do not work around it.
6. **Changing `max_lag` and the priors in the same run**, then wondering which moved the answer.
7. **Treating a long decay as "better" because it fits.** Longer carryover with a smaller
   coefficient draws almost the same line — check the posterior correlation before believing either.
8. **Keeping a period split whose pieces are 0.95 correlated** with each other.

---

## 10. First-run checklist

- [ ] Variables arrive already built; any split has pieces that add back to the original.
- [ ] Feature table has one row per column, with transform, saturation, sign, pooling, baseline and pillar filled in.
- [ ] Level variables are centred, with `contribution_reference=zero`.
- [ ] Transform bounds written in business units; windows consistent with the decay caps.
- [ ] Volume priors for saturating channels; coefficient priors elsewhere; `prior_sd_basis` set.
- [ ] EDA passes with no ERROR; every ATTENTION has been read.
- [ ] Rows per parameter checked; transform groups set where needed.
- [ ] Stage 3 run with transforms pinned, and it reproduces codebase 1.
- [ ] Transforms released one family at a time.
- [ ] CV run before any specification is called final.
