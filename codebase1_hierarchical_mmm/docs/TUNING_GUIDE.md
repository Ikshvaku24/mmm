# Tuning guide — how to control what the model reports

Every lever in this codebase, what it does, and **when to reach for it**.

The organising idea: a reported contribution is only ever

```
contribution = beta × Σ(x_scaled + reference_shift) × dv_scale
```

so there are exactly four places a number can come from — **the coefficient**
(priors), **the data** (scaling), **the counterfactual** (reporting), and **the
model structure** (what else competes for the same variance). Diagnose which one
before touching anything.

### Where the levers live

| Lever family | Set it in |
|---|---|
| per-feature priors, signs, pooling, scaling, reporting reference | `feature_priors.csv` — one row per feature |
| everything else (model / run / sampler / output / cv) | `config.yaml`, or the Python config objects directly |

`config.yaml` carries **every** setting at its **default value**, with a line of
help above each one, so it doubles as the reference for what the pipeline does
when you say nothing. Omit any key and that default applies; a **misspelled key
is an error**, not a silent default.

```python
from settings import run_from_yaml
result = run_from_yaml("config.yaml")
```

Regenerate it after upgrading the codebase with
`python settings.py --write config.yaml`. Each run also drops its **effective**
settings — every default filled in — at
`outputs/<run_name>/01_data/resolved_config.yaml`, which is the record of what
actually ran.

---

### Start from the warnings

> **Building a model from scratch, rather than fixing one?** Read
> `METHODOLOGY.md` first - it is the order of operations (which variables to add
> when, what to check before each addition, how to set priors with no benchmark
> to reverse-engineer). This guide is the reference for what each lever does.

Every run writes `00_warnings/00_INDEX.md`: the run's own warnings grouped by
category, each document naming the affected features and the fix. Read the
`high` rows before diagnosing anything by hand — `prior_pins_coefficient` and
`collinear_with_intercept` between them explain most surprises, and they name
the exact features.

---

## 0. The diagnostic loop — always start here

Do not change a prior until you know whether the number you dislike is a
**finding** or an **assumption**. Three artefacts answer that:

| File | Question |
|---|---|
| `02_convergence/prior_posterior/<param>.png` | **The picture.** Prior vs Data vs Posterior |
| `02_convergence/prior_posterior_contraction.csv` | `contraction`, `mean_shift_in_prior_sd` |
| `01_data/prior_summary.csv` | what your written prior actually means |

### Reading the three-curve chart

`prior_posterior/<param>.png` draws what you asserted (**Prior**), what the data
alone says (**Data**), and the two combined (**Posterior**). The Data curve is
not stored by the sampler — it is backed out, exactly, from the other two:

```
1/sd_like²  = 1/sd_post² − 1/sd_prior²
mu_like     = sd_like² × (mu_post/sd_post² − mu_prior/sd_prior²)
```

| What you see | What it means | Where to go |
|---|---|---|
| Data narrow, near Prior | data agrees with you | nothing to do |
| Data narrow, far from Prior | **the data disagrees and wins** | §1 — is your prior wrong, or your units? |
| Data very wide / absent | the posterior **is** your prior | §1 — only the prior can move this |
| Data far away, Posterior stuck near Prior | **prior fighting data** | §1.3 — loosen, or accept |
| No Data curve at all | **unidentified** (posterior wider than prior) | §3 — structural, not a prior problem |

### The two numbers behind it

- **`contraction` = 1 − posterior_var/prior_var** — *did the data sharpen it?*
  - `> 0.5` data-driven · `0.2–0.5` mixed · `< 0.2` **prior-driven, report as an
    assumption** · `≤ 0` **unidentified**
- **`mean_shift_in_prior_sd` = (posterior_mean − prior_mean)/prior_sd** — *did
  the data move it, and where?* `|shift| > 2` raises a PRIOR-DATA CONFLICT
  warning.

> **The trap.** A feature that matches your benchmark because contraction ≈ 0 is
> not agreement — it is your own prior echoing back. When your priors were
> derived *from* the benchmark, the green cells are often the parameters the
> model learned nothing about, and the red cells are the ones it did.

### Converting a coefficient move into a contribution gap

For a sign-constrained feature the contribution scales as `exp(mu)`, so:

```
%diff in contribution = exp(posterior_mu − prior_mu) − 1
```

That single line reproduces essentially every gap you will ever see. Combined
with `prior_sd`, it gives the rule of thumb that governs everything below:

| prior_sd (log scale) | a 1-sd move is | a 2-sd move is |
|---|---|---|
| 0.05 | ±5% | ±11% |
| 0.10 | ±11% | ±22% |
| **0.198** (= "±20%") | **±22%** | **±48%** |
| 0.5 | ±65% | ±172% |

**A ±20% prior converts a mild 1-sd disagreement into a 22% contribution gap.**
Most "unexplained" double-digit gaps are this and nothing else.

---

## 1. Prior levers — per feature, in `feature_priors.csv`

These change **where the coefficient sits** and **how hard it resists the data**.

### 1.1 `global_prior_mean` — where the coefficient sits

Interpretation depends on the sign constraint:

| `sign_constraint` | `global_prior_mean` is | valid values |
|---|---|---|
| `positive` / `negative` | a **magnitude**; direction comes from the constraint | must be `> 0` |
| `free` | a **location** — the sign is part of the assertion | any sign |

**Derivation.** `prior_mean = contribution_target / Σx / dv_scale`. This is only
correct if the run's `dv_scale` is the same denominator (§4).

> **Use when:** you have a target contribution and want the model to start
> there. **Anti-pattern:** deriving with `abs()` on a `free` feature — that
> asserts a positive effect on something that pulls sales down. Either negate
> the mean, or switch to `sign_constraint=negative` with a positive magnitude.

### 1.2 `prior_mean_basis` — median or mean of the coefficient

`exp(Normal(mu, sigma))` is right-skewed, so `prior_mean = 0.05` is ambiguous:

```
median = exp(mu)            -> mu = log(m)              basis = "median" (default)
mean   = exp(mu + sigma²/2) -> mu = log(m) − sigma²/2   basis = "mean"
```

They differ by `exp(−sigma²/2)`: **2% at sigma=0.2, 12% at 0.5, 33% at 0.9.**

> **Use `mean`** only when the number you were handed is explicitly an expected
> value. Default `median` is what every run to date used and what contribution
> reconciliation was validated against. Irrelevant for `free` features.

### 1.3 `global_prior_sd` + `prior_sd_basis` — how hard the prior resists

**This is the single most powerful lever.** It decides whether the reported
number is yours or the data's.

| `prior_sd_basis` | your number means | signed features | free features |
|---|---|---|---|
| `log` (default) | the sigma parameter itself | used as-is | used as-is |
| **`relative`** | **a fraction: 0.2 = ±20%** | `sigma = √(log(1+r²))` | `sd = r·\|mean\|` |
| `absolute` | coefficient units | `sigma = √(log(1+(s/m)²))` | used as-is |

**Always use `relative` unless you have a reason not to.** It is the only basis
whose meaning does not change when `prior_mean` changes.

> ⚠️ **`absolute` on a signed feature with a small mean explodes.** `prior_sd =
> 0.2` with `prior_mean = 0.11` gives `sigma = √(log(1+(0.2/0.11)²)) = 1.20`,
> i.e. **±180%, a factor of 3** — an essentially uninformative prior. If you
> meant "±20%", that is `relative`, not `absolute`.

| Goal | Setting |
|---|---|
| Reproduce a benchmark; data must not override it | `relative`, **0.05–0.10** |
| Benchmark as a starting point, data may adjust | `relative`, **0.2** |
| Let the data decide; prior is a weak nudge | `relative`, **0.5–1.0** |
| Pin a coefficient exactly | `relative`, **0.01** (and say so in the write-up) |

> **Use when:** contraction is high and the posterior moved somewhere you do not
> want. Tightening `prior_sd` is how you *hold* a coefficient; it does not make
> the model more correct, it makes it more obedient. Say which you did.

### 1.4 `sign_constraint` — structural direction

`positive`/`negative` build `beta = ±exp(·)`, which **can never cross zero**.
Consequence: `p_value` is 0 and `prob_positive` is 1 **by construction** —
`p_value_basis` says `sign-constrained (vacuous)`. Judge those on
**`prob_negligible`** (a ROPE test) instead.

> **Use `free`** when the direction is genuinely unknown or the feature is a
> dummy that could go either way. **Use `positive`/`negative`** when economics
> settles it (media ≥ 0, price ≤ 0). Switching a wrongly-signed `free` feature
> to a constraint is usually better than negating its mean — it makes the sign
> structural rather than a hope.

### 1.5 `regional_sd_prior` — how much regions may differ

Prior scale of `tau` in `beta_g = mu + tau·z_g`. **Same axis as `prior_sd`, so
the same `prior_sd_basis` applies.**

| Value | Effect |
|---|---|
| `0` under `hierarchical` | **rejected** — `HalfNormal(0)` is degenerate |
| `< 0.02` | warns: pooling collapses to one shared coefficient |
| `0.2` | regions may differ by ~±20% |
| `0.5+` | regions largely independent |
| any value under `pooling="global"` | **ignored** — there is no `tau` |

### 1.6 `pooling` — how regions share information

| Mode | Structure | Use when |
|---|---|---|
| `hierarchical` | `beta_g = mu + tau·z_g` | regions genuinely differ but inform each other. **Default.** |
| `independent` | own prior + own coefficient | you have real per-region priors and want no shrinkage |
| `global` | one shared coefficient | national features; features non-zero in one region only; anything you want reported as a single number |

> A feature non-zero in only one region under `hierarchical` wastes two
> parameters and reads `data_support = none` for the others. Use `global`.

### 1.7 `region` override rows

An extra row with `region` filled in. Under `hierarchical` only the **mean** is
used (a fixed offset to the shrinkage centre); under `independent` mean and sd
both apply; under `global` it **raises**.

---

## 2. Structural levers — `ModelConfig`

These change **what else competes** for the same variance. Reach for them when
the problem is *where the level went*, not what one coefficient says.

### 2.1 `alpha_prior_sd` / `alpha_regional_sd` — the intercept

Default **0.5** each. `alpha` is a free hierarchical region intercept.

**This is the lever for "my baseline features do not match the benchmark."**
If your benchmark model has no intercept — its region fixed-effect dummies *are*
the intercept — then you are fitting a level term it does not have, and the
level will split differently.

| Setting | Effect | Use when |
|---|---|---|
| `0.5` (default) | intercept free to absorb the level | exploratory; you want the best fit |
| **`0.05`** | intercept pinned near 0; **named features must carry the level** | reproducing a benchmark with explicit fixed effects |
| `2.0` | intercept dominates | rarely; the baseline becomes uninterpretable |

**Symptom to look for:** `baseline_core_pct` strongly negative with
`baseline_features_pct` well above 100. Those are the same degree of freedom
seen twice. Check `mu_alpha`'s posterior — on a KPI scaled to mean 1.0, an
intercept near 0.9 means it has claimed ~90% of sales before any feature speaks.

> ⚠️ Pinning `alpha` forces the level into the named features **in total** — it
> does not choose which. A feature already too high can go higher. Treat it as
> an experiment and re-check the comparison, not a guaranteed fix.

### 2.2 `include_intercept` — remove the intercept outright

Default **`true`**. `alpha_prior_sd=0.05` (§2.1) *squeezes* the intercept;
`include_intercept: false` **deletes it**. No `mu_alpha`, no `tau_alpha`, no
`alpha_region` — the model simply has no estimated level term, so nothing can
quietly absorb sales the named drivers should be explaining.

**What actually happens to the level.** It does not vanish. With
`dv_center: mean` the KPI is centred before fitting and the mean is added back
by the inverse transform, so `__baseline_core__` becomes that **fixed
historical mean** — the same number every draw, **zero posterior width**. The
baseline stops being a free parameter and becomes an accounting constant. Every
contribution still reconciles to actual sales.

| | intercept on | intercept off |
|---|---|---|
| `__baseline_core__` | estimated, has an HDI | the training mean, no HDI |
| what absorbs unexplained variance | the intercept | the residual |
| `alpha_region` in the reports | present | absent |
| drivers | compete with a free level | carry everything above the mean |

> **Use when:** the benchmark you are reproducing has no intercept (region
> fixed-effect dummies *are* its intercept), or `mu_alpha` has run away — on a
> KPI scaled to mean 1.0, a posterior near 0.9 means the intercept claimed ~90%
> of sales before any feature spoke.
>
> **Do not use with `dv_center: none`.** The KPI still carries its level and
> nothing is left to hold it, so every coefficient is dragged upwards to fake an
> intercept. `run_pipeline` warns, but the run will not stop you.

Order of escalation, mildest first:

1. `alpha_prior_sd: 0.05` — intercept pinned near zero but still estimated.
2. `include_intercept: false` — gone.
3. Explicit region dummies as features, if you want a level you can *see*.

Reporting-only twin: `output.report_intercept: false` hides
`mu_alpha` / `tau_alpha` / `z_alpha` / `alpha_region` from the contraction
report and its per-parameter charts, and **nothing else** — convergence tables
still cover every parameter (an intercept with a bad R-hat is never hidden) and
the decomposition is untouched. Use it to stop a nuisance level crowding out the
drivers in the diagnostics you actually read.

### 2.3 `fourier_order` / `include_trend` — seasonality and trend

Defaults `0` and `False`.

> **Use when:** the panel has real seasonality not already captured by dummies.
> **Do not use** alongside explicit seasonality dummies — they compete for the
> same weeks, and the dummies will shrink. If holdout drifts while actuals
> recover, a latent trend extrapolating is a common cause.

### 2.4 `likelihood` — `normal` | `student_t`

> **Use `student_t`** when a few weeks (promotions, stockouts) drag the fit.
> It stops outliers from bending every coefficient.

### 2.5 `pool_sigma`

Partial-pool region noise on the log scale. Keep `True` unless a region has
genuinely different volatility and enough data to prove it.

---

## 3. Structural redundancy — when no prior will help

If `contraction ≤ 0` (posterior **wider** than the prior), the likelihood is
telling you nothing and the parameter is unidentified. **No prior setting fixes
this.** Common causes:

| Cause | Signature | Fix |
|---|---|---|
| Two columns are duplicates (`_mat1`/`_mat2`, `_newtarget`/`_oldtarget`) | both negative contraction, identical priors | merge the columns, or report the **pair as a sum** |
| A region dummy alongside `alpha_region` | negative contraction on the dummy | pin `alpha_prior_sd`, or drop the dummy |
| A feature active in ~0 weeks | `n_active_train` tiny, `data_support = none` | drop it, or accept it is a pure prior |
| Collinear level variables | offsetting ± contributions | `center_mode=mean` (§4.2) |

Check `03_coefficients/support_warnings.txt` — it lists every region × feature
combination driven by shrinkage rather than that region's data.

---

## 4. Scaling levers — the unit rule

> **THE SCALE IS THE UNIT OF YOUR PRIORS.** A coefficient means "moves the KPI
> by `beta × dv_scale` per `feature_scale` of input". Change either scale and
> every prior mean must be divided by the same factor. Get this wrong and the
> decomposition still reconciles to exactly 100% while being wrong by a constant
> multiple — **no reconciliation check can catch it.**

### 4.1 KPI — `RunConfig`

| Option | Values | Notes |
|---|---|---|
| `dv_center` | `mean` (default) \| `none` | `none` sets `y_mean = 0`, which keeps the inverse transform correct automatically. **Never strip centring by editing one side of the transform.** |
| `dv_scale` | `none` \| `sd` (default) \| `mean` \| `mean_positive` \| `max` | must match the denominator your priors were derived on |
| `dv_scale_scope` | `region` (default) \| `global` | `global` only if the priors were built on one shared number |

**Symptom of a mismatch:** every contribution off by a constant factor per
region, and `mean_shift_in_prior_sd` large and same-signed across many features.

### 4.2 Features — `center_mode` / `scale_mode`

| `center_mode` | `none` \| `mean` |
| `scale_mode` | `none` \| `sd` \| `mean` \| `mean_positive` \| `max` |

> ⚠️ **An explicit `center_mode` beats the legacy `center` flag** and then
> overwrites it. If your file has `center_mode=none`, setting `center=1` does
> **nothing**. Change `center_mode`.

| Situation | Setting |
|---|---|
| Data already transformed and on the prior's scale | `none` / `none` |
| **Always-on level variable** (distribution, price, ACV) | **`mean` / `none`** + `contribution_reference=zero` |
| Media, zero means "no activity" | `none` / `mean_positive` |
| Control variable | `mean` / `sd` |

**`center_mode=mean` with `scale_mode=none` divides by 1**, so it subtracts a
constant and **preserves your coefficient units** — every prior stays valid.
Centring is a *reparameterisation*, not a transformation: fitted values are
unchanged, only the intercept/coefficient split becomes identifiable.

> **Use when:** you get the *"always on but nearly constant"* warning, or see
> saturated tree depth / divergences / offsetting ± contributions. Expect the
> **sampler** to improve (divergences → 0, tree depth → 0%, better holdout).
> Do **not** expect the attribution to change much — that is §2.1's job.

---

## 5. Reporting levers — these never change the fit

There is a test asserting the fit is bit-identical across `contribution_reference`
settings. Use these to change the **question**, not the answer.

### 5.1 `contribution_reference`

`auto` | `zero` | `mean` | `min` | a number. What the contribution is measured
*against*, in raw feature units.

```
beta·(x−ref)/s = beta·(x−mu)/s + beta·(mu−ref)/s
```

whatever is added is subtracted from the baseline core, so the decomposition
still sums to the fitted value.

| Setting | Question answered |
|---|---|
| `auto` | matches the scaling: zero for scale-only, the feature mean for centred |
| **`zero`** | **"what if this driver were 0?"** — the standard due-to decomposition |
| `mean` | "what if it sat at its average?" |
| `min` | "versus the worst observed level" |

> ⚠️ **A centred feature with `auto` reports ≈ 0**, because `Σ(x − x̄)` over the
> train window is exactly 0. Whenever you set `center_mode=mean` on a level
> driver, set `contribution_reference=zero` as well or its contribution
> vanishes.

### 5.2 `baseline`, `pillar`, `period_split`, `rope_scaled`

- **`baseline=1`** — fold into the baseline block instead of reporting as
  incremental. Its own contribution is still reported, so the baseline stays
  expandable. Baseline features appear **twice** in `contribution_totals.csv` —
  filter on `group` before summing.
- **`pillar`** — roll-up group for `contribution_by_pillar.csv`.
- **`period_split`** — `mat` | `week` | `year` | `none`; MAT length follows
  `cadence` (52 weekly / 12 monthly).
- **`rope_scaled`** — the region of practical equivalence driving
  `prob_negligible`. **The only non-vacuous significance number for a
  sign-constrained feature.**

---

## 6. Situation → lever

| Symptom | Most likely cause | Lever |
|---|---|---|
| Contribution too high/low vs a target, `contraction < 0.2` | it *is* your prior | `global_prior_mean` (§1.1) - paste the target into `benchmark_comparison.xlsx` and read column K |
| Too high/low, `contraction > 0.5` | the data disagrees | tighten `global_prior_sd` to impose, or accept (§1.3) |
| `mean_shift` > 2 prior sd | **prior-data conflict** | check `dv_scale` units first (§4), then the prior |
| Sign is wrong | free feature, wrong-signed mean | `sign_constraint`, or negate the mean (§1.4) |
| `contraction ≤ 0` | unidentified / duplicate column | structural — §3 |
| High R-hat, saturated tree depth, divergences | level variable collinear with intercept | `center_mode=mean` (§4.2) |
| Offsetting ± contributions between two features | mutual collinearity | check `01_data/collinearity_pairs.csv` + `04_fit/posterior_correlation.csv`, then `center_mode` or merge (METHODOLOGY §3) |
| `baseline_core_pct` very negative, features > 100% | redundant free intercept | `alpha_prior_sd=0.05` (§2.1) |
| Intercept alone claims most of sales (`mu_alpha` ≈ 0.9 on a mean-1.0 KPI) | a free level is out-competing every driver | `alpha_prior_sd=0.05`, then `include_intercept: false` (§2.2) |
| Intercept rows drown the contraction report | nuisance parameter, not a driver | `output.report_intercept: false` (§2.2) |
| Centred feature reports ≈ 0% | measured vs its own mean | `contribution_reference=zero` (§5.1) |
| Every contribution off by a constant factor | scale mismatch | `dv_scale` / `dv_scale_scope` (§4.1) |
| Regions all identical when they should differ | `regional_sd` too small, or `global` pooling | §1.5 / §1.6 |
| Regions wildly different, small ones noisy | no pooling | `pooling=hierarchical` (§1.6) |
| Holdout drifts away while actuals recover | trend extrapolating / seasonality too smooth | `include_trend`, `fourier_order` (§2.3) |
| A few weeks bend every coefficient | outliers | `likelihood="student_t"` (§2.4) |
| `p_value = 0` everywhere | sign constraints — vacuous by construction | read `prob_negligible` (§5.2) |
| Accuracy looks fine but a coefficient moves every refit | fragile, window-dependent estimate | `cv.enabled: true`, then `cv_stability_by_region.csv` |

---

## 7. Recipes

### 7.1 Reproduce a benchmark decomposition

```python
# feature_priors.csv
#   global_prior_mean = contribution_benchmark / Σx / dv_scale
#   prior_sd_basis    = relative
#   global_prior_sd   = 0.05        # tight: the benchmark must hold
#   sign_constraint   = negative    # wherever the true contribution is negative
#   center_mode       = mean        # on always-on level drivers
#   scale_mode        = none        # keeps the prior units valid
#   contribution_reference = zero   # required once centred

RunConfig(dv_center="none", dv_scale="mean", dv_scale_scope="region")
ModelConfig(alpha_prior_sd=0.05, alpha_regional_sd=0.05)   # no free intercept
```

**First check after the run:** open `05_contributions/contribution_math.csv` and
confirm `volume_recomputed` lands on the benchmark before the data moves
anything. Then check `contraction` — if it is near 0, say plainly in the
write-up that the contributions are prior-imposed.

### 7.2 Let the data speak

```python
# global_prior_sd = 0.5, prior_sd_basis = relative
ModelConfig(alpha_prior_sd=0.5)
```

Then report `contraction` alongside every contribution, and treat anything
below 0.2 as an assumption.

### 7.3 Diagnose one bad number, in order

1. `prior_posterior/<param>.png` — prior-driven, data-driven, or unidentified?
2. `prior_summary.csv` — does `implied_rel_sd` match what you intended?
3. `contribution_math.csv` — is `scale_used`/`dv_scale_used` the denominator the
   prior was derived on?
4. `contribution_math.csv` — is `contribution_reference` the convention the
   benchmark used?
5. `coefficient_report.csv` — `data_support`. `none`/`weak` means shrinkage, not
   estimation.
6. `model_input_summary.csv` — check the raw column is what you think it is.
7. Only now: change a prior.

---

## 8. Anti-patterns

Each of these cost a run in this project's history.

1. **`prior_sd = 0.2 × prior_mean`** on a signed feature. That is ~0.003 — it
   pins the coefficient and the data has no say. Use `prior_sd_basis=relative`
   with `0.2`.
2. **`prior_sd_basis=absolute` with a small mean.** `0.2/0.11` → sigma 1.20 =
   ±180%. The opposite mistake, equally silent.
3. **Editing one side of a transform.** Stripping centring from the forward pass
   while the inverse still adds the mean back → MAPE ≈ 100%. Use `dv_center`.
4. **A global `dv_scale` with per-region priors.** Contributions inflate by
   `global_scale / region_scale`, and the decomposition still reconciles to 100%.
5. **`abs()` when deriving a `free` feature's mean.** Drops the sign and asserts
   a positive effect on something negative.
6. **Setting `center=1` when `center_mode` is present.** Silently ignored.
7. **Reading `p_value` on a sign-constrained feature.** It is 0 by construction.
8. **Trusting agreement with a benchmark when contraction ≈ 0.** That is your
   own prior echoing back, not corroboration.
9. **Summing `contribution_totals.csv` without filtering `group`.** Baseline
   features appear twice.
10. **Tuning against a single holdout.** Use `cross_validation.run_cv` — and
    read `cv_stability_ranking.csv`, not just the error metric.

---

## 9. Quick reference

```
contribution        = beta × Σ(x_scaled + reference_shift) × dv_scale
%diff               = exp(posterior_mu − prior_mu) − 1
sigma (relative r)  = √(log(1 + r²))          0.2 -> 0.198042
median / mean       = exp(mu) / exp(mu + sigma²/2)
contraction         = 1 − posterior_var / prior_var
mean_shift          = (posterior_mean − prior_mean) / prior_sd
1/sd_like²          = 1/sd_post² − 1/sd_prior²
beta (signed)       = ±exp(Normal(mu, sigma))     — cannot cross zero
beta_g              = mu + tau·z_g                — hierarchical
```

| Want to… | Change |
|---|---|
| move a coefficient | `global_prior_mean` |
| stop the data moving it | `global_prior_sd` ↓ (with `prior_sd_basis=relative`) |
| let the data move it | `global_prior_sd` ↑ |
| fix its direction | `sign_constraint` |
| change region sharing | `pooling`, `regional_sd_prior` |
| free/pin the level | `alpha_prior_sd` |
| remove the level entirely | `model.include_intercept: false` |
| hide the intercept from diagnostics | `output.report_intercept: false` |
| check coefficients across windows | `cv.enabled: true` |
| fix sampler geometry | `center_mode=mean` |
| change the counterfactual | `contribution_reference` |
| change the units | `dv_scale`, `scale_mode` |

**Related:** `METHODOLOGY.md` (how to build the model in the first place) · `MERIDIAN_ASSUMPTIONS.md` · `config.yaml` (every setting + its default) · `OUTPUTS_GUIDE.md` (every file and column) ·
`docs/understanding_prior_sd_conversion.md` · `docs/when_cneter_is_not_1.md` ·
`../CLAUDE.md` (run history and decisions already made)
