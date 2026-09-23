# Feature prior guide — filling in the prior CSV

One row per feature. This file is the source of truth for **which columns are
modelled** and **what the model believes about each one before it sees the
data**. Everything else lives in `config.yaml` (see `CONFIG_GUIDE.md`).

**You do not write this file from scratch, and you do not need one to get
one.** The pre-model step builds it from the **datacube** — every column that
is not `date`, `region` or `dv` becomes a row — and fills in the means and
signs if you also hand it a mapping or share file. Jump to §5.

---

## 1. The columns

| Column | Required | Default | What it does |
|---|---|---|---|
| `variable` | **yes** | — | must match the data column name **exactly** |
| `region` | no | blank | blank = a feature-level row. Fill it on an **extra** row to override one region's prior |
| `pooling` | no | `hierarchical` | `hierarchical` / `independent` / `global` |
| `sign_constraint` | no | `free` | `positive` / `negative` / `free` |
| `global_prior_mean` | no | 0.05 (signed), 0.0 (free) | the **magnitude** of the coefficient (signed features); the signed location (free) |
| `global_prior_sd` | no | 1.0 (signed), 0.5 (free) | how hard the prior resists the data |
| `regional_sd_prior` | no | 0.5 | how far regions may differ |
| `prior_sd_basis` | no | `log` | how to READ the two sds: `log` / `relative` / `absolute` |
| `prior_mean_basis` | no | `median` | is the mean the median or the mean of the coefficient? |
| `center_mode` | no | **`none`** (every sign) | `none` / `mean` |
| `scale_mode` | no | **`none`** (every sign) | `none`/`sd`/`mean`/`mean_positive`/`max` |
| `baseline` | no | 0 | 1 = fold into the baseline instead of reporting as incremental |
| `pillar` | no | blank | reporting roll-up group |
| `contribution_reference` | no | `auto` | `auto`/`zero`/`mean`/`min`/a number. **Reporting only** |

> **Defaults: no centring, no scaling.** A blank `center_mode` / `scale_mode`
> passes the column to the model exactly as it is in the datacube, so every
> prior is per **raw** unit. Centring and scaling are opt-in (§2, and
> `TUNING_GUIDE.md` §4.2). The statistics for any centre or scale you do ask
> for come from `run.scaling_window` — the training window by default.
> *Until 2026-09-22 a blank meant `mean_positive` for signed features and
> `mean`/`sd` for free ones.* The built-in fallback `global_prior_mean` (0.05)
> was sized for scaled inputs — on raw columns always write your own.

> **A blank cell is legal everywhere, and means "take the default".** A file
> with nothing but a `variable` column loads and runs. A blank
> `sign_constraint` is `free`; a blank `global_prior_mean` is the built-in
> default (0.05 signed, 0.0 free) and the run warns, once, with the list of
> variables it applied to. That is the state of a **new variable you are
> testing** — and of every row of a freshly generated file that neither input
> covered. *(Until 2026-09-24 a blank `sign_constraint` cell crashed the
> loader with `sign must be one of ... got 'nan'`.)*
>
> One consequence worth knowing: a blank `global_prior_sd` is the default
> `1.0`, and with `prior_sd_basis: relative` that reads as **±100%** — very
> wide. Write the width you can defend.

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

**The exception is `free`.** A free coefficient is `Normal(mean, sd)` — no
exponential — so its mean keeps its sign: a free dummy expected to lift sales
is `+0.02`, one expected to dent them is `-0.02`.

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

Distribution, price index, ACV. Uncentred they are near-constant — TDP at
80 ± 0.5 every week — and duplicate the region intercept, the defect that broke
`real_data_v1` (R-hat 1.26, TDP +91% / AVP −97% cancelling out). The default
is uncentred, so the run warns (`collinear_with_intercept`) when `sd / level`
is under `run.near_constant_sd`. Centring does not change the coefficient's
units, so **the prior mean stays valid**.

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

You do not write 65 prior means by hand, and you do not need an existing
prior file to make one. **The variable list comes from the datacube**: every
column except `date`, `region` and `dv` becomes a row. Give the step up to
**two optional files** and it also fills in the means and the signs.

```yaml
data:
  input_path: input_datacube.xlsx     # date, region, dv, then one column per variable
  feature_priors: null                # nothing yet - that is the point
```

```bash
python -m mmm.data.prior_builder config.yaml
```

With neither input file that writes the **skeleton**: one row per variable,
`prior_sd_basis: relative`, `prior_mean_basis: median`, everything else blank.
With a mapping or share file it writes **two prior files** plus
`prior_calculation.xlsx` showing every intermediate number:

| File | Rows | Use it when |
|---|---|---|
| `feature_priors_national.csv` | one per variable — the **national** mean | `pooling: hierarchical` (written) — one prior, regions shrink to it |
| `feature_priors_regional.csv` | the same, **plus one override row per region** with that region's own mean | `pooling: independent` (written) — each region its own prior |

Which one applies is **your pooling decision**, not the builder's, so both are
always written.

```yaml
data:
  feature_priors: feature_priors_v7.csv   # the LIST the two files must sit inside
  mapping_file: mapping.csv     # vendor_variable, our_variable[, region][, contribution]
  share_file:   shares.csv      # section, pillar, pillar_share_pct, variable, spend, variable_share_pct
  dv_aggregation: mean          # keep mean - it must equal run.dv_scale
run:
  dv_scale: mean                # REQUIRED to fit with a generated file - see "Units" below
  dv_scale_scope: region
```

It runs automatically at the front of `run_from_yaml`, or on its own:

```bash
python -m mmm.data.prior_builder config.yaml
```

**It never overwrites `data.feature_priors`.** You review the generated file and
point at it yourself.

### The four cases

| | mapping file | contributions in it | share file | what happens |
|---|---|---|---|---|
| **a** | ✓ | ✓ | optional | invert the vendor decomposition |
| **b** | ✓ | — | ✓ | build from the shares |
| **c** | — | — | ✓ | build from the shares |
| **d** | — or ✓ without contributions | — | — | the **skeleton** from the datacube — variable names only. (With `data.feature_priors` already set there is nothing to add, so nothing is written) |

When contributions **and** shares are both given, **the contribution wins
wherever it exists** — it is evidence, the share is an assumption. A variable
the vendor never reported (a channel they did not model) falls back to its
share-based prior rather than a blank. The share file always supplies the
**pillar** names, the **baseline** flag and the default **signs**, because
those are definitions rather than estimates.

### What each input file must contain

Both files are validated before anything is computed, and both stop the run
rather than silently skipping a row.

**The mapping file** (`data.mapping_file`)

| Column | Required | Rule |
|---|---|---|
| `vendor_variable` | **yes** | the vendor's name. Never checked against anything — it is theirs |
| `our_variable` | **yes** | must be a **datacube column** (or, when `data.feature_priors` is set, one of its variables) |
| `region` | no | blank, or the column absent, means **national**. A name that is not a region in the data is not caught here — it simply never matches |
| `contribution` | no | the vendor's number. Without it the file only **groups** variables (case b/d) |

- The contribution belongs to the **vendor variable**. Replicated across
  several of our rows, it is counted **once** — repeat the same number or
  leave the copies blank. **Two different numbers for the same
  `vendor_variable` + `region` is an error.**
- A variable may appear in only one group; groups are the connected components
  of the vendor ↔ ours links, so linking A–B and B–C makes one group of three.
- Header names are sniffed, not fixed: `vendor`, `their_variable`,
  `model_variable`, `volume` and similar are all recognised.

**The share file** (`data.share_file`)

| Column | Required | Rule |
|---|---|---|
| `section` | **yes** | one of `media`, `expert`, `comp_media`, `trade`, `baseline`. Anything else stops the run (consumption data goes in `baseline`) |
| `variable` | **yes** | must be a datacube column / prior-file variable, and may appear in **one section only** |
| `pillar` | media, expert | the roll-up group. `comp_media`, `trade` and `baseline` default to *Competitor Media* / *Trade* / *Baseline* |
| `pillar_share_pct` | media, expert, baseline | one number **per pillar** — write it once or repeat the same value; two different values for one pillar is an error. For `baseline` it is the whole-baseline share |
| `spend` | media, expert | how the pillar's share is split. A pillar with no spend at all cannot be split and stops the run |
| `variable_share_pct` | comp_media, trade, baseline | the variable's own share. Missing on any row of those sections is an error |
| `sign_constraint` | no | `positive`/`negative`/`free`. Blank falls back to: negative share → negative, a dummy → free, `comp_media` → negative, else positive |

- Shares are **percentages**, not fractions: write `6.0`, not `0.06`.
- Baseline `variable_share_pct` is a share **of the baseline**, not of sales,
  and they should total ≤ 100 (it warns otherwise).
- The step prints the implied total share of sales and warns if it exceeds 100%.

### Every name must be in the feature prior file

**The feature prior file may carry MORE variables than the mapping or share
file — never fewer.** Every `our_variable` in the mapping and every `variable`
in the share file must be a variable of `data.feature_priors`; a variable only
the prior file has simply gets a blank generated mean (fill it by hand). With no
prior file configured yet, the datacube's columns are the list instead.

A name that fails stops the pre-model step with the closest spelling:

```
mapping.csv: 1 our_variable name(s) are not variables of the feature prior file (data.feature_priors):
  radio_grps   (did you mean radio_grp?)
The feature prior file may carry MORE variables than this file, never fewer. Add the
variable to data.feature_priors, or drop it from this file.
```

A typo would otherwise be silently skipped and leave that variable's prior blank.
Only **our** names are checked — the vendor's names are the vendor's.

---

### File 1 — the mapping file

Vendor and model carry **approximately the same variables**. Where they differ
it is almost always because we **split** a vendor variable — by period or by
sub-brand:

```
vendor   media_digital-display_..._effervescent_hero_impressions
ours     media_digital-display_..._effervescent_hero_impressions_mat1
         media_digital-display_..._effervescent_hero_impressions_mat2
```

One row per link. **Replicate** whichever side is shared
(`samples/mapping_sample.csv`):

```csv
vendor_variable,our_variable,region,contribution
digital_hero,digital_hero_mat1,Core,880000
digital_hero,digital_hero_mat2,Core,880000         <- vendor name replicated (we split)
calls_mat1,calls_premium,Core,210000
calls_mat2,calls_premium,Core,190000               <- OUR name replicated (they split)
base_price,base_price,Core,                        <- 1:1, no contribution: grouping only
```

| Column | Required | Meaning |
|---|---|---|
| `vendor_variable` | **yes** | the vendor's name |
| `our_variable` | **yes** | ours — must be a datacube column |
| `region` | no | blank or absent = a **national** number |
| `contribution` | no | the vendor's contribution for that vendor variable |

**A contribution belongs to the VENDOR variable.** When a vendor row is
replicated across several of ours, repeat the same number — or give it once and
leave the replicas blank. It is counted **once**, never summed across replicas.
Two *different* numbers for the same vendor variable and region is an error.

**How rows become groups.** The file is a bipartite graph — vendor names on one
side, ours on the other — and a **group** is a connected component of it. One
rule covers every case: one vendor → many ours, many vendor → one of ours, or
both at once. Each group is treated as **one unit** — here, and again in the
benchmark sheet, which reads the same file. The grouping that *builds* a prior
is therefore always the grouping that *checks* it.

### Case a — inverting a vendor decomposition

A contribution is `beta × Σx × dv_scale`, so the coefficient that reproduces it
is:

```
region_coef[group, region] = contribution / support / dv_agg

national_coef[group]       = SUM(region_coef over regions WITH support)
                             / number of regions with support
```

- **`support`** — Σ of the group members' **raw** values over the window. Raw,
  because the vendor's number is in raw units.
- **`dv_agg`** — the region's KPI aggregate (`dv_aggregation`, mean by default).
- **The national mean divides by the regions that had support**, not by the
  region count: contribution and support in 2 regions out of 5 → the sum of 2
  coefficients divided by 2. A feature that never ran in a region contributes
  nothing there whatever its coefficient. `prior_calculation.xlsx` prints
  `sum_of_region_coefs` and `n_regions_used` so the division can be checked.
- The average is **signed**: a region whose contribution runs the other way
  pulls it towards zero rather than counting as agreement.
- `national_coef` → `feature_priors_national.csv`. Each `region_coef` → one
  override row in `feature_priors_regional.csv`.
- **`data.national_basis` picks the aggregation**: `average` (default, the mean
  above) or `weighted` = `Σ C / Σ(support × dv_agg)` — the single coefficient
  that reproduces the national **total**, which is what `pooling: global`
  needs. They differ when a contribution is concentrated in a few regions; the
  workbook prints both plus `weighted_over_average`, and the step warns when
  that ratio exceeds 1.25. See `CONFIG_GUIDE.md`.
- The group's one mean is **replicated to every member**. They share an implied
  coefficient; splitting it would invent a difference the vendor never measured.
- A **national** contribution (no region) is allocated to the regions in
  proportion to support, so each region is still divided by its own `dv_agg`.

### Signs

| Source | Rule |
|---|---|
| vendor contribution **negative** | `negative` — always, dummies included |
| vendor contribution **positive**, variable is a **dummy** (`dummy` in its name, any case) | `free` |
| vendor contribution **positive**, anything else | `positive` |
| share file | its own `sign_constraint` column (see File 2) |

For a signed variable the written mean is the **magnitude**; for a `free` one it
keeps its sign (§2). Where the contribution's sign **differs across regions**
the sign of the total is used and the row is flagged. In the regional file, a
region whose coefficient runs **against** a signed variable's sign cannot be
written (a region row holds a magnitude; the sign is feature-level), so it gets
**no row** and falls back to the national mean — listed in
`00_warnings/regional_prior_sign_skipped.md`. If that happens a lot, the
variable is probably not really signed.

---

### File 2 — the share file

What share of sales each piece of the business is expected to carry. Five
**sections**, every one optional (`samples/share_sample.csv`):

```csv
section,pillar,pillar_share_pct,variable,spend,variable_share_pct,sign_constraint
media,TV & DTV,6.0,media_tv_core_grps,1800000,,
media,TV & DTV,6.0,media_tv_halo_grps,600000,,
media,Online Media,4.5,media_digital_hero_mat1,450000,,
comp_media,,,media_competitor_tv_grps,,3.0,
expert,Detailing,2.0,btl_expert-calls_premium,300000,,
trade,,,trade_tpr_depth,,4.0,
baseline,,62.0,sales_distribution_tdp,,40.0,
baseline,,62.0,sales_base-price,,-35.0,
baseline,,62.0,dummy_Jul25dip,,2.0,negative
```

| Section | What you give | Formula for `C` |
|---|---|---|
| **media** | pillars inside media, **one share per pillar**, **spend per variable** | `pillar_share × sales × spend / pillar_spend` |
| **expert** | exactly like media | `pillar_share × sales × spend / pillar_spend` |
| **comp_media** | a share per variable — no spend split | `variable_share × sales` |
| **trade** | trade **is** the pillar; a share per variable | `variable_share × sales` |
| **baseline** | the **whole-baseline** share, and each variable's share **within** the baseline | `baseline_share × variable_share × sales` |

and then, for every section:

```
region_coef[variable, region] = C / support / dv_agg
national_coef[variable]       = SUM(region_coef over regions with support) / that count
```

where `sales` is the region's total KPI over the window.

**`pillar_spend` is computed, never typed** — it is the sum of `spend` over the
variables in that pillar. A pillar's `pillar_share_pct` may be written once or
repeated on every row; two different numbers for one pillar is an error.

**Pillars flow into the prior file.** The `pillar` you write here becomes the
`pillar` column of the generated file, so the reporting roll-up matches the way
the shares were defined. `comp_media` defaults to *Competitor Media*, `trade` to
*Trade*, `baseline` to *Baseline*.

**The baseline section** holds the consumption data — TDP, price, category
volume and price, dummies, sometimes price promotion. There is no separate
"consumption" section. Its variables are written with `baseline=1`.

`variable_share_pct` in the baseline section is the variable's share **of the
baseline**, not of sales. That is exactly what the **stage-1 baseline-only run**
gives you (`METHODOLOGY.md` §1): run it, read each baseline variable's share in
`05_contributions/contribution_summary.csv`, and write those numbers here. They
should total at most 100% — less, when the intercept, trend and seasonality
take some of the baseline.

**Signs.** The file's own `sign_constraint` column decides. A blank falls back,
in order, to: a **negative share** → negative (write price as `-35`, the way a
vendor deck prints it); a **dummy** → free; `comp_media` → negative; everything
else → positive. `C` carries the resolved sign, so a free variable's mean is
signed and a signed variable's is a magnitude.

Inside a media or expert pillar every variable gets the **same implied
efficiency**. That is not a claim they are equally efficient — it is the
least-informative start that still has the right total. With a wide
`global_prior_sd` the data has room to move them apart, and **how far each one
moves is the result**. If the shares say marketing is 15% of sales and the fit
returns 7%, that is a finding (usually a missing category or competitor
variable), not a prior to force.

---

### What the generated file contains — and deliberately does not

**The file is a template, and a blank is an answer.** Only what was actually
given is written:

| Column | Filled with | When it is blank |
|---|---|---|
| `variable` | every datacube column (or every configured feature) | never |
| `global_prior_mean` | the generated coefficient | neither input file covered the variable, or it has no support anywhere |
| `sign_constraint` | the contribution's sign, or the share file's column | same — and a blank reads as `free` |
| `pillar`, `baseline` | the **share file**, which states them in as many words | no share file, or the variable is not in it |
| `prior_sd_basis`, `prior_mean_basis` | `relative` and `median`, always | never |
| `pooling`, `global_prior_sd`, `regional_sd_prior`, `contribution_reference`, `center_mode`, `scale_mode` | — | **always.** Blank is the documented default: `hierarchical`, no centring, no scaling, `auto` reference |

The blanks are not laziness — they are the defaults the generated means are
already in the units of (no scaling, no centring, measured against zero), so an
untouched file is consistent. Two columns are worth your judgement before you
fit:

- **`global_prior_sd`** — the width, and it is blank on purpose. `0.02` pins
  the variable to the number you imported (an assumption); `0.3–0.5` lets the
  data speak. See §4 and `METHODOLOGY.md` §2c. Blank means the default `1.0`,
  which under `relative` is ±100%.
- **`center_mode`** — `mean` for an always-on level (TDP, price, category).
  The prior mean stays valid either way; pair it with
  `contribution_reference: zero`.

Region override rows fill **only** `variable`, `region` and
`global_prior_mean` — the only columns a region row is read for (§3). Features
with no support anywhere get a blank mean and a note
(`00_warnings/generated_prior_blank.md` lists them). **A blank row is also
exactly what you want for a variable you are testing** — a UCM equity series
the vendor never had: it starts free, centred on zero, and the data speaks
first.

### Units — the one setting that makes a generated file silently wrong

A generated mean is `C / Σ raw x / dv_agg`: **per raw unit of the variable, per
unit of the region's mean KPI.** The model reads a coefficient as
`beta × Σ x_scaled × dv_scale`. They agree only when:

| Setting | Must be | Written for you? |
|---|---|---|
| `scale_mode` (prior file) | `none` — x stays in raw units | blank = `none`, the default |
| `center_mode` (prior file) | `none` or `mean` — either is fine for the mean | blank = `none` |
| `contribution_reference` (prior file) | `zero` — the vendor decomposes against zero | blank = `auto`, which IS zero while the variable is uncentred |
| `run.dv_scale` | `mean` | no — config.yaml's default is `sd` |
| `run.dv_scale_scope` | `region` | default |
| `data.dv_aggregation` | `mean` | default |
| `run.scaling_window` | either — the divisor is taken over the **same** window the model scales on | automatic |

Get one wrong and every contribution is off by a constant multiple **and still
reconciles to 100%** — no downstream check can see it (the v5 BMC failure). The
pre-model step warns (`00_warnings/generated_prior_units.md`) when the run is
set up otherwise.

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
7. **Pointing `data.feature_priors` straight at a generated file without
   reading it.** It is a proposal, not a decision.
8. **Ignoring the `collinear_with_intercept` warning on TDP, price and
   category.** The builder writes `center_mode: none`; the v1 collinearity
   failure is what happens when an always-on level stays uncentred and nobody
   reads the warning.
9. **Writing a baseline variable's share of SALES** in the share file. It is its
   share of the *baseline*, from the stage-1 run.
10. **Fitting a generated file with `dv_scale: sd`.** The means are per unit of
    the region's MEAN KPI. Set `run.dv_scale: mean` first.
11. **Switching the regional file to `pooling: hierarchical` without knowing
    what changes.** Each region's mean then becomes the centre that region
    shrinks toward, not its own prior, and any per-region sd you add is
    ignored (§3). Valid — but a different model from the one it was written for.

**Related:** `CONFIG_GUIDE.md` (everything outside this file) ·
`METHODOLOGY.md` (the order to build in) · `TUNING_GUIDE.md` (which lever to
reach for) · `OUTPUTS_GUIDE.md` (every output column).
