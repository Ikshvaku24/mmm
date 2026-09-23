# Modelling methodology — how to build an MMM you can defend

This is the **approach**: the order in which to add variables, what to check
before each addition, and how to set priors when nobody has handed you a
benchmark contribution to reverse-engineer.

It exists because the two ways this project has built models so far are both
special cases of it:

- **Benchmark-anchored** — a vendor decomposition exists, priors are derived
  from it (`prior_mean = contribution / Σx / dv_scale`), and the job is to
  reproduce and then interrogate it.
- **Open** — no benchmark. Priors have to come from theory, spend, and the
  data itself, and the model has to be built up in stages.

The benchmark-anchored route has a trap the open route does not: **a feature
whose prior came from the benchmark and whose `contraction` is ~0 has not
validated anything.** The posterior is the prior; the agreement is circular.
That trap is why the staged approach below matters even when you *do* have a
benchmark — you run it to find out which numbers the data can actually support.

> **Related:** `TUNING_GUIDE.md` is the reference for *which lever does what*.
> This document is the *order of operations*. Use them together: this file says
> "now check collinearity"; the tuning guide says which setting fixes what you
> find.

---

## 0. The principle

> Add variables in the order of how confident you are that they belong, and
> stop adding when the new variable stops paying for itself.

A model is not "the list of things marketing spends money on". Every column you
add spends degrees of freedom and steals variance from the columns already
there. The staged build makes that cost visible: you see exactly what each
block bought, and what it took from the blocks before it.

Three numbers decide whether a block earns its place, and **all three must move
in the right direction**:

| Number | Where | What it says |
|---|---|---|
| `r2_within_region` (test) | `04_fit/fit_metrics.csv` | did prediction improve out of sample? |
| `contraction` | `02_convergence/prior_posterior_contraction.csv` | did the data learn anything about the new coefficients? |
| posterior correlation | `04_fit/posterior_correlation.csv` | did the new block make an existing one unidentifiable? |

In-sample R² is not on that list. It rises when you add noise.

---

## 1. The staged build

Each stage is a full run with its own `run_name`. Keep every stage's output
folder — the comparison between them *is* the evidence.

```yaml
# stage_1_baseline.yaml
run:
  run_name: s1_baseline
model:
  include_intercept: true
  include_trend: true
  fourier_order: 2
```

### Stage 1 — baseline only

**Include:** intercept, trend, seasonality. Nothing else.

**Why first:** this is the level and shape of the business without any driver
you control. Everything you add later is judged against it, and its fit is the
number that tells you how much there is left to explain. If the baseline alone
predicts the holdout well, marketing effects are going to be small and no
amount of priors will make them large.

**What to record:** test `r2_within_region`, `baseline_core_pct`, and the
residual autocorrelation (`04_fit/assumption_checks.csv`). Strong residual
autocorrelation *here* means the baseline itself is misspecified — fix that
before adding anything, or the drivers will be fitted to a moving target.

**Common mistake:** skipping this because "the baseline isn't interesting". It
is the single largest term in your final decomposition. In `real_data_v3` it
was **91%** of sales.

### Stage 2 — always-on business drivers

**Add:** distribution (TDP/ACV), price (AVP), and anything that is on every
period and is not a marketing decision.

**Why second:** these are *confounders*, not campaigns. They move with sales
and with each other, and if they are absent when you add media, the media
coefficients will absorb them. Adding them early means the media block is
competing against an honest baseline.

**Settings that matter:** these are always-on level variables, so they need
`center_mode=mean` — without it they scale to ~1.0 every period and duplicate
the intercept (the defect that broke `real_data_v1`). Pair with
`contribution_reference=zero` or their reported contribution collapses to ~0.

**Check before moving on:** `01_data/collinearity_summary.csv`. Distribution and
price are the classic collinear pair.

### Stage 3 — paid media

**Add:** the media channels, all at once if they are budgeted independently;
in spend order (largest first) if you suspect they overlap.

**Why third:** media is the thing you are trying to measure, so it goes in last
among the "real" variables — everything it could be confused with is already in
the model.

**Settings that matter:** `sign_constraint=positive` for every paid channel.
This is not cosmetic — it is the strongest, cheapest piece of prior information
you have, and it prevents the "negative TV effect" conversation entirely.

**Check before moving on:** posterior correlation between channels. Two channels
that always run together will trade off, and neither coefficient can be read on
its own however good the overall fit is.

### Stage 4 — the rest

**Add:** expert/detailing activity, trade, sampling, one-off dummies.

**Why last:** these are typically the smallest, the noisiest, or the most
correlated with something already in the model. If a variable only "works" when
added at this stage after everything else is fixed, that is a finding about the
variable, not a success.

**Dummies specifically:** a dummy with fewer than ~5 active periods is a
memorised residual, not an effect. The real panel has a `Dummy` column with
**5 non-zero region-weeks in 520 rows** — it will fit perfectly and mean
nothing.

### The stage log

Keep this table. It is the model's defence.

| Stage | Added | test `r2_within_region` | Δ | Baseline % | New coefficients with `contraction` > 0.3 | New posterior-correlation flags | Keep? |
|---|---|---|---|---|---|---|---|
| 1 | baseline | | — | | — | — | — |
| 2 | + distribution, price | | | | | | |
| 3 | + paid media | | | | | | |
| 4 | + expert, trade | | | | | | |

**Rules for the "Keep?" column:**

- Test accuracy got worse → **revert**, whatever the in-sample fit says.
- Test accuracy flat, no new coefficient contracted → **revert**. You added
  parameters and learned nothing.
- Test accuracy flat but coefficients contracted and the block is needed for
  the decomposition → **keep, and say so**: it is there for attribution, not
  prediction. That is legitimate and must be stated.
- A previously stable coefficient became unstable → **the new block is
  collinear with it.** Do not keep both without deciding which one you trust.

---

## 2. Priors, when there is no benchmark

Priors are flexible early and tightened only where you have evidence. The point
is to let stage 1–3 tell you what the data supports before you impose anything.

### The ladder, weakest to strongest

| Level | What you know | Setting |
|---|---|---|
| 0 | Direction only | `sign_constraint=positive`, `global_prior_sd=0.7` (≈ a factor of 2) |
| 1 | Rough magnitude from spend share | `global_prior_mean` from the spend heuristic below, `prior_sd=0.5`. **`mmm/data/prior_builder.py` computes it for you** |
| 2 | A prior study or a category benchmark | mean from it, `prior_sd=0.3` |
| 3 | Your own experiment (geo test, switchback) | mean from it, `prior_sd=0.1–0.2` |
| 4 | An imposed benchmark you are reproducing | mean from it, `prior_sd=0.02`, and **say it is an assumption** |

Start every unknown feature at level 0 or 1. Move a feature up the ladder only
when you can name the evidence. Never start at level 4 for a variable you
intend to *estimate* — you cannot estimate something you have pinned.

### Setting a level-1 mean from spend

> **This is automated.** Put the shares in a `data.share_file` — one row per
> variable with its `section` (`media`, `expert`, `comp_media`, `trade`,
> `baseline`), pillar, pillar share and spend — and the pre-model step does the
> arithmetic below for every section, with a calculation workbook. Media and
> expert pillars are split by spend; competitor media and trade take a share
> each; the baseline takes the whole-baseline share times each variable's share
> WITHIN it, which is what the stage-1 baseline-only run in §1 gives you. See
> `FEATURE_PRIOR_GUIDE.md` §5.

With no benchmark, the defensible starting point is: assume marketing in total
delivers a plausible share of sales, and split that share across channels in
proportion to spend.

```
target_marketing_share  = 0.15          # your category prior. Be honest and
                                        # conservative - 10-25% is typical for
                                        # established brands
share_c                 = spend_c / total_spend
contribution_c          = target_marketing_share * total_sales * share_c
prior_mean_c            = contribution_c / Σx_c / dv_scale
```

This deliberately gives every channel the *same* implied efficiency. That is
not a claim that they are equally efficient — it is the least-informative
starting point that still has the right total. With `prior_sd=0.5` the data has
plenty of room to move them apart, and **the amount each one moves is the
result**. Check it in `02_convergence/prior_posterior_contraction.csv`: a
channel that moved a long way from equal-efficiency is telling you something.

> **Sanity check the total, not the parts.** If `target_marketing_share` is 15%
> and your model returns 7%, that is a finding worth investigating (missing
> category/competition variables usually), not a prior to force.

### Tightening

After stage 3, look at each feature:

| Contraction | Meaning | Do |
|---|---|---|
| > 0.5 | the data has a strong opinion | **leave the prior wide.** Report the data's answer |
| 0.2 – 0.5 | data and prior both contributing | fine as is |
| < 0.2 | the data cannot see this feature | either accept it is prior-driven and say so, or remove the feature |
| ≤ 0 | unidentified | structural problem — see §3, no prior fixes it |

**Do not tighten a prior because you dislike the answer.** Tighten it only to
impose information you can name. The difference is whether you could write the
sentence "this prior comes from ___" and have it be true.

### Updating a prior mean with no benchmark — mean shift and contraction

With a benchmark there is an outside target, R, and the benchmark sheet does
the arithmetic. **Without one, the only evidence about where a prior should sit
is this run's own data** — and two columns of
`02_convergence/prior_posterior_contraction.csv` carry all of it. Read them on
the **`use_for_delta` row** (`glogbeta_*` / `mu_logbeta_*` / `logbeta_*` for a
signed variable — the log scale; why that row and not `beta_*`:
`prior_posterior_contraction_fitting.md` §2b):

| Column | Formula | Question it answers |
|---|---|---|
| `contraction` = c | `1 − posterior_var / prior_var` | did the data **sharpen** it? |
| `mean_shift_in_prior_sd` = s | `(posterior_mean − prior_mean) / prior_sd` | did the data **move** it, and how far? |

**What the data alone says.** The posterior is a precision-weighted average,
`posterior = (1 − c)·prior + c·data`, so the data's own centre — the "Data"
curve in the prior/posterior charts — is recoverable:

```
mu_data = prior_mean + s × prior_sd / c            (the log-scale row's own numbers)
coefficient the data implies = exp(mu_data)        (signed variable, median basis)
                             = mu_data             (free variable - natural scale, no exp)
```

Worked example — prior median 0.05, `prior_sd` 0.5 (log), c = 0.4, s = +0.8:

```
prior_mean (log)  = ln 0.05                 = −2.996
posterior (log)   = −2.996 + 0.8 × 0.5      = −2.596   → median 0.075
mu_data           = −2.996 + 0.8 × 0.5/0.4  = −1.996   → the data alone says 0.136
check: 0.6 × (−2.996) + 0.4 × (−1.996)      = −2.596   ✓
```

**What to do — decided by c first, then s:**

| c | \|s\| | Reading | The prior mean |
|---|---|---|---|
| < 0.2 | any | the data is silent. `mu_data` divides by a small c, so it is mostly noise | **do not update it from this run.** Keep it and report the result as an assumption, or bring outside evidence (the ladder above). Widening the sd once is a fair test: if c stays under 0.2, the variable is not identified in this data |
| ≥ 0.2 | ≤ 2 | prior and data agree | **leave it.** The posterior is the answer. Re-centring on it and refitting uses the data twice |
| ≥ 0.2 | > 2 | **prior-data conflict** — the prior was in the wrong place | 1. **check units first** (`dv_scale`, `scale_mode`, `scaling_window` — the commonest cause, `TUNING_GUIDE.md` §4). 2. If the prior came from weak evidence (ladder 0–2), set the mean to `exp(mu_data)`, **keep the sd wide** (≥ 0.5 relative) and refit once. 3. If it came from strong evidence (an experiment), keep it and investigate the data instead — confounding, collinearity (§3) |
| > 0.8 | any | the data dominates | leave it — the prior barely matters |

**The rule behind the table: re-centre, never re-tighten.** Moving a misplaced
prior to where the data points, with the sd kept wide, removes a conflict.
Moving it there *and* tightening it counts the same data twice: the next run
reports a narrower interval than the evidence supports, and its contraction
reads as if the data sharpened something when it was you.

The same goes for the staged build (§1): carrying a stage-k posterior forward
as the stage-k+1 prior is this move. Carry the **median** forward as the mean;
set the sd from the ladder level you can justify from *outside* evidence, not
from the posterior sd.

> `exp(mu_data)` is a **median**. With `prior_mean_basis: mean` multiply by
> `exp(σ²/2)`. The `prior_sd` on the log-scale row is already the log sigma,
> whatever `prior_sd_basis` the file used.

---

## 2b. Raising a variable you believe is under-credited — a UCM brand-equity series

The situation: you add an unobserved-component (UCM) series for brand trust /
equity. There is no vendor contribution for it, it has support, the model runs
— and its share of the total contribution is lower than you believe. How do
you raise it, and when should you not?

### Step 0 — is it how the contribution is measured?

This is the commonest cause, and **no prior can fix it**. A UCM output is a
latent level from a state-space smoother: its origin is arbitrary, it is often
zero-mean and can go negative. The reported contribution is
`beta × Σ(x − reference) × dv_scale`, so:

- `contribution_reference: zero` (or `auto` on an uncentred column) on a
  zero-mean series → `Σx ≈ 0` → contribution ≈ 0 **whatever beta is**;
- `center_mode: mean` with `contribution_reference: auto` → measured against
  its own average → ≈ 0 by construction.

**Fix:** `contribution_reference: min` — "the sales equity delivers above its
weakest observed level" — or rebase the series upstream so zero means
something (no equity). This is reporting only: the fit is bit-identical, only
the number you present changes. Settle it before touching a prior.

### Step 1 — find out what it competes with

A brand-equity series is slow and smooth: the same shape as the **trend**, the
**intercept**, the low Fourier terms, and often distribution (TDP). Look at:

- `04_fit/posterior_correlation.csv` — the UCM coefficient against
  `beta_trend_region` and `alpha_region`;
- `01_data/collinearity_vif.csv` — `explained_by` on the UCM row;
- `contraction` on its `use_for_delta` row.

High correlation with the trend or intercept means **the data cannot split
them — the split is set by the priors.** That is not a failure; it tells you the
share is a modelling choice, which makes raising it legitimate but makes it an
assumption you must declare.

### Step 2 — let contraction decide whether you may

| c | Meaning | Raising the share is |
|---|---|---|
| < 0.2 | the share **is** your prior | legitimate — the data has no opinion. Report it as an assumption |
| 0.2 – 0.5 | data and prior share it | possible; expect the fit to push back partly |
| > 0.5 | the data says it is small | **against the data.** Only with outside evidence (a brand-tracking study, a published equity model). Expect CV accuracy to drop |

### Step 3 — turn the share you want into a prior mean

Treat your belief as the benchmark:

```
V   = target_share × Σ sales            (same window as the contribution report)
V0  = its contribution now              (05_contributions/contribution_summary.csv)
R   = V / V0
new_prior_mean = old_prior_mean × R^(1 / (1 − max(c, 0)))      (c on the log-scale row)
```

The easy way: paste `V` into the TOTAL `benchmark` cell of the variable's row in
`benchmark_comparison.xlsx` and read `suggested_prior`. Example: 2% now, you
believe 6%, c = 0.15 → R = 3 → new = old × 3^(1/0.85) = **old × 3.64**.

For a first prior, before any run: `beta = V / Σ(x − ref) / dv_scale`, with
`ref = 0` under `contribution_reference: zero` and `ref = min(x)` under `min`,
and `dv_scale` the region's mean KPI (`run.dv_scale: mean`). The pre-model
share file does this only for `ref = 0` (it divides by raw `Σx`) — under `min`,
compute it by hand.

### Step 4 — make room for it

Fitted sales ≈ actual sales — the adding-up constraint — so the extra volume
must come **out of something**, and for a slow series it comes out of whatever
it is collinear with. Decide where it should come from and loosen that:

- **the intercept** — smaller `model.alpha_prior_sd` (0.05 nearly pins it) or
  `include_intercept: false` (`CONFIG_GUIDE.md`, "the intercept in order of
  severity");
- **the trend** — `include_trend: false`. A linear trend and a rising equity
  series are nearly the same column; keep one;
- **the sign** — `sign_constraint: positive` on the UCM, so it cannot trade
  negative against the trend.

Skip this step and raising the prior of a variable collinear with the intercept
only moves weight between two terms the data cannot separate: the posterior
correlation goes more negative and the share bounces from run to run.

### Step 5 — tighten only if you are imposing it

If the variable is prior-driven (c < 0.2) and you want it held at `V`, tighten
`global_prior_sd` (ladder level 4, 0.05–0.1 relative) and say so. Tightening
changes c: as c → 0 the exponent → 1, so when you tighten at the same time set
the prior to **current posterior median × R** directly.

### Step 6 — check what you changed

- **contraction** — jumped to ≈ 0? You imposed it. Fine if intended; say so.
- **who gave up share** — `contribution_summary.csv`: out of the intercept or
  trend (intended) or out of media (not intended)?
- **CV** — `cv_scorecard.csv`: if wMAPE worsens by more than one standard
  error, the data rejects the belief.
- **exogeneity** — if the UCM was estimated from the **same sales series** (a
  UCM decomposition of the KPI itself), it is a smoothed piece of the dependent
  variable, not an explanatory variable: endogenous by construction, it will
  absorb anything and its share means little.
  `04_fit/exogeneity_cross_correlation.csv` flags it. Prefer an equity series
  built from brand-tracking data (awareness, consideration, trust scores).

---

## 3. Collinearity — how to check it, properly

**Yes, this is now checked** (`assumptions.py`, on by default). Here is what it
does and how to read it.

### It must be measured on the model's design matrix, not the raw data

The columns the sampler sees are the **scaled** features *plus* the intercept,
the Fourier seasonality block and the trend. A correlation matrix of raw
features cannot see:

- a feature that duplicates the **intercept** (an always-on column scaled to
  ~1.0 every period);
- a "promo" flag that is really just **December**, i.e. collinear with the
  seasonal block;
- a channel that only ever ran during a **growth phase**, i.e. collinear with
  the trend.

So the design is rebuilt exactly as `model.build_model` assembles it, per
region (scaling and coefficients are both per region), on the training window
only.

### The four statistics, and why you need all of them

Written to `01_data/collinearity_summary.csv`, `_vif.csv`, `_pairs.csv`.

| Statistic | Catches | Threshold |
|---|---|---|
| **Pairwise \|r\|** | two columns that move together | 0.8 warn, 0.95 severe |
| **VIF** (centred) | one column predicted by *several* others | 5 warn, 10 severe |
| **VIF (uncentred)** | a column that duplicates the **intercept/level** | 10 |
| **Condition number** (Belsley) | near-singularity of the whole design | 10 warn, 30 severe |

**Why two VIFs.** The textbook centred VIF is **structurally blind to
intercept collinearity**. On the exact case that broke `real_data_v1` — an
always-on feature at ~1.0 every period — the centred VIF read **1.09**, looking
perfect, while the condition number read **23,000**. The uncentred VIF read
9.7 × 10⁷. The `duplicates` column in `collinearity_vif.csv` says which kind
you have:

- `other features` → high centred VIF: this feature is a combination of others.
- `the intercept/level` → high uncentred, low centred: it duplicates the level.
  Fix with `center_mode=mean`.

**Why the condition number too.** Pairwise correlation and VIF are both
one-column-at-a-time. Three columns can be pairwise-innocent and jointly
near-singular (A + B ≈ C). Only the condition number sees that.

Columns are scaled to unit length but **not centred** — Belsley's convention,
because centring is precisely what removes the intercept from the picture.

### The Bayesian check the classical ones cannot make

`04_fit/posterior_correlation.csv` — the correlation between the **posterior
draws** of two coefficients.

This is the definitive one, because it accounts for the priors. If two features
are collinear in the data, their coefficient draws will be strongly
**negatively** correlated: the model knows their *sum* and not their split.

The four-way reading, which is the whole point:

| Pre-fit VIF | Posterior corr | Contraction | Interpretation |
|---|---|---|---|
| low | low | high | **Clean.** Identified and learned from data |
| high | high (negative) | either | **Trading off.** Neither coefficient readable alone |
| high | **low** | **low** | **The dangerous one.** The prior pinned both, so they *look* fine — but the data never separated them and the split is your assumption |
| low | low | low | Feature has no signal; the prior is the answer |

Row 3 is why a clean posterior-correlation table is not sufficient on its own.
**Always read it next to `contraction`.**

### What to do about collinearity

In order of preference:

1. **Merge the columns.** If two variables are 0.97 correlated, they are one
   variable. `_mat1`/`_mat2` splits of the same activity are the obvious case.
2. **Drop one.** Keep the one with the better data quality or the clearer
   business meaning; state that its coefficient now carries both.
3. **Centre.** If the collinearity is with the intercept, `center_mode=mean`
   fixes the geometry without changing attribution.
4. **Pool them.** Put them in one `pillar` and report the pillar total, which
   *is* identified even when the split is not.
5. **Impose a prior.** Last resort, and only with named evidence. This does not
   solve the collinearity — it decides the split by assumption. Say so.

Note what is **not** on the list: dropping a variable because its coefficient
"looks wrong". A wrong-looking coefficient in a collinear block is a symptom.

---

## 3b. What Meridian does differently

Worth knowing, because it is the opposite philosophy and it is defensible:
**Meridian's VIF threshold is 1000 and its pairwise-correlation threshold is
0.999** - two to three orders of magnitude looser than the textbook 5-10 and
0.8. It only errors on numerical degeneracy and trusts the priors to regularise
everything short of that. It also runs **no** classical residual tests at all.

It does run three STRUCTURAL checks we lacked, and we have now adopted all
three:

- **confounding** - correlation between each treatment and each control
  (`04_fit/confounding_pairs.csv`). The closest computable stand-in for
  exogeneity.
- **aggregate posterior predictive p-value** - is the TOTAL plausible, not just
  each week (`04_fit/structural_checks.csv`).
- **P(baseline < 0)** - drivers over-claiming with the baseline absorbing the
  offset (same file).

Full comparison, with source pointers and what NOT to copy:
`MERIDIAN_ASSUMPTIONS.md`.

---

## 4. The other assumptions

`04_fit/assumption_checks.csv` and `assumptions_report.md`. Bayesian regression
rests on the same structural assumptions as OLS — they just show up as
misspecification (intervals too narrow, coefficients trading off) rather than
invalid standard errors.

| Assumption | Statistic | Fails when | Fix |
|---|---|---|---|
| **Linearity** | corr(residual, fitted) | > 0.2 | transforms upstream are wrong, or a driver is missing |
| **Homoscedasticity** | sd(resid) top third ÷ bottom third of fitted | > 1.5 | model log sales; or accept peak-week intervals are understated |
| **Independence** | Durbin-Watson | < 1.5 | missing trend/seasonality/carryover. **Every interval is too narrow** until fixed |
| **Seasonal residual** | ACF at lag 13 / 52 | \|r\| > 0.3 | `fourier_order` too low, or period dummies needed |
| **Residual shape** | skew, excess kurtosis | \|skew\| > 1, kurtosis > 1 | `likelihood: student_t` |
| **Influence** | count beyond 3 sd | more than ~0.3% | dummy the real events; fix the data errors; never delete silently |
| **Confounding** | max \|corr(treatment, control)\| | above max(0.1, 2/√n) | report the pair together, or get an experiment. No prior fixes it |
| **Aggregate calibration** | posterior predictive p-value on the total | < 0.05 | the weekly intervals can be fine while the annual total sits in the model's tail |
| **Baseline sign** | P(baseline < 0) | > 0.2 review, > 0.8 fail | drivers are over-claiming; pin or remove the intercept (§2.2 of TUNING_GUIDE) |

**Independence is the one to take seriously on a weekly panel.** Positive
autocorrelation means the effective sample size is smaller than the row count,
so every credible interval is too narrow and every "significant" driver is less
certain than it looks.

### Exogeneity — the assumption no statistic can check

Every check above reads the residuals. None can tell you whether a regressor is
correlated with the error, and this is the assumption MMMs break most often:

- **Spend follows expected sales.** Budgets go where sales are already expected
  to be high, so media coefficients absorb the demand that *caused* the spend.
  The model fits beautifully and overstates ROI.
- **Omitted common drivers.** A missing category or competitor variable that
  moves with your activity loads onto whatever correlates with it. This project
  has exactly that gap: the vendor models carry Category at ~55.8% and
  Competition at −4.9%, and we have neither.

The only real answers are design-based: geo experiments, holdout regions,
switchback tests — or an ROI prior derived from one. **Treat a clean assumption
report as necessary, not sufficient.**

---

## 5. The full loop

```
1.  Stage 1: baseline only
      └─ residual autocorrelation OK?  ── no ──> fix the baseline first
2.  Stage 2: + always-on business drivers
      └─ collinearity_summary verdict ok/moderate?  ── severe ──> §3
3.  Stage 3: + paid media   (level 0/1 priors, sign constraints on)
      └─ posterior_correlation flags?  ── yes ──> §3, decide before continuing
      └─ test r2_within_region improved?
4.  Stage 4: + expert / trade / dummies
      └─ same two checks
5.  Read contraction for every feature
      └─ tighten only where you can name the evidence  (§2)
6.  Full assumption battery (§4)
7.  Cross-validate:  cv.enabled: true
      └─ cv_stability_ranking.csv — coefficients stable across windows?
      └─ cv_scorecard.csv — admissible? and what is the STANDARD ERROR on
         wMAPE? A rival model must beat you by more than that to be better.
         compare_cv_runs([...]) applies the rule for you
8.  Only now: compare to a benchmark - paste it into the TOTAL (or region)
    `benchmark` cells of 05_contributions/benchmark_comparison.xlsx and read
    the summary sheet
```

Step 8 is last on purpose. A model built to match a benchmark from the start
cannot tell you anything about the benchmark.

---

## 6. Anti-patterns

1. **Adding everything at once, then removing what looks wrong.** You cannot
   attribute a problem to a variable if every variable arrived together.
2. **Judging a block by in-sample R².** It always improves. Use test
   `r2_within_region`.
3. **Tightening a prior until the answer is acceptable.** That is not
   modelling. If you must impose the number, impose it and label the
   contribution an assumption.
4. **Reading a coefficient from a collinear pair.** Report the pair's sum, or
   fix the collinearity.
5. **Citing agreement with a benchmark on a pinned feature.** The prior came
   from the benchmark; the agreement is circular. Check `contraction` first.
6. **Treating a clean assumption report as proof.** It says nothing about
   exogeneity, which is where the real risk is.
7. **Keeping a dummy with 5 active periods.** It is a memorised residual.
8. **Reverting the centring fix** because centred features show ~0%
   contribution. That is `contribution_reference`, not a bug — set it to
   `zero`.
9. **Re-centring a prior on this run's posterior and tightening it.** The
   same data counted twice. Re-centre with the sd kept wide, or not at all
   (§2, "Updating a prior mean with no benchmark").
10. **Raising a variable's share without deciding where the volume comes
    from.** Fitted sales are fixed; a slow series (brand equity, distribution)
    takes its extra share from the intercept or trend, and if you have not
    chosen which, the share bounces between runs (§2b).

---

## 7. Quick reference

```
Stage order        baseline -> always-on drivers -> paid media -> the rest
Keep a block if    test r2 improved  OR  (coefficients contracted AND needed
                                          for attribution - and say which)
Prior ladder       0 sign only (sd 0.7) -> 1 spend split (0.5) -> 2 study (0.3)
                   -> 3 own experiment (0.1-0.2) -> 4 imposed benchmark (0.02)
Collinearity       01_data/collinearity_summary.csv   (pre-fit, the DATA)
                   04_fit/posterior_correlation.csv   (post-fit, the MODEL)
                   read BOTH next to contraction
Assumptions        04_fit/assumptions_report.md
Stability          06_cross_validation/cv_stability_ranking.csv
Model choice       06_cross_validation/cv_scorecard.csv + compare_cv_runs()
Benchmark gap      05_contributions/benchmark_comparison.xlsx
                   (paste into `benchmark`; delta clusters = structural)
```

**Related:** `MERIDIAN_ASSUMPTIONS.md` (how Meridian handles all of this) · `TUNING_GUIDE.md` (which lever does what) · `OUTPUTS_GUIDE.md`
(every file and column) · `../CLAUDE.md` (run history and decisions already
made).
