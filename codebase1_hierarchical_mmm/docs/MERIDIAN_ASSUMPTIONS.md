# How Meridian handles collinearity and the other assumptions — and what we should take

Read against the Meridian source in `../../../meridian/`, not from memory. Every
claim below has a file pointer.

The headline: **Meridian's philosophy is the opposite of the textbook one, and
it is defensible.** It does not try to eliminate collinearity. It assumes the
priors regularise it, checks only for *numerical* degeneracy, and spends its
diagnostic effort on the posterior instead. That is a coherent position, but it
has a cost the classical thresholds are there to catch, and the right answer for
us is a hybrid.

---

## 1. Collinearity

### Where it lives

Meridian has a dedicated pre-fit EDA engine:
`meridian/model/eda/eda_engine.py`, with a spec in `eda_spec.py` and thresholds
in `constants.py`. Findings are graded `ERROR` / `ATTENTION` / `INFO`.

### The five checks it runs

| Check | What it does | Threshold | Severity |
|---|---|---|---|
| `check_pairwise_corr` | Pearson correlation between every treatment/control pair | **0.999** | ERROR |
| `check_vif` | VIF via `statsmodels`, on treatments + controls, with a constant added | **1000** | ERROR |
| `check_std` | flags near-constant variables (VIF returns NaN for them) | sd < 1e-4 | ATTENTION |
| `check_variable_geo_time_collinearity` | adjusted R² of each variable against **geo** and against **time** | — | INFO |
| `check_data_param_ratio` | data points vs parameter count | — | INFO |

### Read those thresholds again

**VIF > 1000. Correlation > 0.999.** The textbook numbers are 5–10 and 0.8.
Meridian's are two to three orders of magnitude looser.

This is not an oversight. It follows from the Bayesian position: with proper
priors, the posterior is proper even when the design is rank-deficient, so
collinearity does not *break* anything — it just means the prior carries more of
the answer. Meridian therefore only errors when the design is numerically
degenerate (a duplicated column, a constant), and treats everything short of
that as the prior's job.

**What that position costs you.** Two channels correlated at 0.95 sail through
every Meridian check. Their individual ROIs are then determined mostly by their
priors, and Meridian will report them with credible intervals that look
perfectly reasonable. The model is not *wrong* — the joint posterior is honest —
but anyone reading one channel's ROI off the summary is reading an assumption.
Meridian's answer to that is `check_variable_geo_time_collinearity` (INFO) and
the post-fit checks in §2, not a pre-fit gate.

### One thing Meridian does that we did not

`check_variable_geo_time_collinearity` regresses every variable on **geo** and on
**time** separately and reports adjusted R². That is a smart, cheap check we
lacked a direct equivalent for:

- **high R² vs time** → the variable is essentially a time trend, so it competes
  with the seasonality/trend baseline rather than explaining anything;
- **high R² vs geo** → the variable is essentially a region fixed effect, so it
  competes with the intercept.

Our design-matrix VIF catches both *implicitly* (the trend and Fourier columns
are in the design), but it does not label which one, and it needs the trend
actually to be switched on. **Worth adding** — see §5.

### One thing we do that Meridian does not

Meridian's VIF is `statsmodels.variance_inflation_factor` on a design with
`sm.add_constant(...)` — i.e. the **centred** VIF — and **constant variables get
NaN and are dropped** from the calculation entirely
(`eda_engine.py::_calculate_vif`).

That is exactly the blind spot we hit: the centred VIF cannot see a column that
duplicates the intercept. On our `real_data_v1` case the centred VIF read
**1.09** while the Belsley condition number read **23,000**. Meridian covers the
extreme end of this with a separate `check_std` (constant variables get an
ATTENTION), but a column that is *nearly* constant — sd just above 1e-4, which
is where our TDP/AVP columns sat after mean-scaling — passes both checks.

**We report the uncentred VIF and the condition number; Meridian reports
neither.** That is a genuine advantage of ours, and it is the check that would
have caught v1 on day one.

---

## 2. The other assumptions

Meridian does almost nothing classical here. There is **no** residual test suite
— no Durbin-Watson, no Breusch-Pagan, no normality test, no autocorrelation
check anywhere in the source. Instead it runs a **post-fit review battery**
(`meridian/analysis/review/checks.py`):

| Meridian check | What it tests | Threshold |
|---|---|---|
| `ConvergenceCheck` | max R-hat over all parameters | **1.2** (fail above 10.0) |
| `BayesianPPPCheck` | posterior predictive p-value on total outcome | 0.05 |
| `GoodnessOfFitCheck` | R², MAPE etc., split by `holdout_id` |  |
| `BaselineCheck` | probability the baseline goes **negative** | 0.2 review / 0.8 fail |
| `PriorPosteriorShiftCheck` | has the ROI posterior moved off the prior? | quantile-based |
| `ImplausibleROICheck` | spend-weighted ROI outside a sane band | 0.5 – 20.0 |
| `HighVarianceCheck` | posterior HDI still as wide as the prior's | ratio 1.0 |
| `PotentialBiasCheck` | correlation between each paid channel and every control | **0.1** |

Three of these are worth dwelling on.

### `PotentialBiasCheck` — the exogeneity proxy

Threshold **0.1**, which is deliberately *very* low: essentially any correlation
between a paid channel and a control variable gets flagged as potential
confounding. This is Meridian's structural answer to the assumption no residual
statistic can test — instead of testing exogeneity (impossible), it flags the
data pattern that most often breaks it.

**We have nothing equivalent**, and our `assumptions_report.md` says exactly that
in prose. Meridian turns the caveat into a computed flag. **Worth adopting.**

### `BayesianPPPCheck` — a proper posterior predictive check

`bayesian_ppp = mean(|expected - mean(expected)| >= |actual - mean(expected)|)`,
i.e. how extreme the actual total outcome is against the posterior predictive
distribution. Fails below 0.05.

Ours is adjacent but not the same: `coverage_90_pred_pct` is a *pointwise*
calibration measure (what fraction of weeks fall inside their own interval).
Meridian's is an *aggregate* one (is the total plausible under the model at
all?). A model can have fine pointwise coverage and still put the annual total
in its own tail. **Worth adopting** — it is about six lines.

### `BaselineCheck` — is the baseline negative?

Meridian explicitly checks the probability that the baseline contribution goes
negative, and fails at 0.8. Given our history — `baseline_core_pct` strongly
negative while baseline features exceed 100% — this is directly relevant and we
have no automated equivalent.

### R-hat 1.2

Meridian's convergence threshold is **1.2**; ours is **1.01**. Ours is the
stricter and the more standard (Vehtari et al. recommend 1.01). Meridian's is
loose enough that a genuinely unconverged chain passes. **Keep ours.**

---

## 3. Cross-validation

**Meridian has none.** No k-fold, no expanding window, no WAIC, no LOO, no ELPD,
no model-comparison machinery anywhere in the source. What it has is
`ModelSpec.holdout_id` (`meridian/model/context.py`): a boolean mask over times
(or geo × time) whose points are **excluded from the likelihood** but still
predicted, so `GoodnessOfFitCheck` can report train and holdout metrics
separately.

Two differences from ours worth noting:

1. **In-model vs post-hoc.** Meridian's holdout is a mask inside the likelihood,
   so one fit gives you both. Ours refits per fold. Meridian's is much cheaper;
   ours measures something stronger, because the scaling statistics are also
   recomputed per fold and the coefficients can be compared across origins.
2. **One split vs many origins.** A single `holdout_id` is one draw. Our
   expanding-window CV is what tells you whether the answer depends on where you
   cut — which, for a two-year panel with one prior Q4, is the question.

**Keep ours.** But note that Meridian's approach implies a cheap addition we
could make: a `holdout_id`-style mask would let us do a fast single-fit holdout
for exploration, reserving the full refit CV for the final candidates.

---

## 4. Side by side

| Assumption | Meridian | Us | Verdict |
|---|---|---|---|
| Pairwise collinearity | ERROR at 0.999 | warn 0.8 / severe 0.95 | **ours** — 0.999 only catches duplicates |
| VIF | centred, threshold 1000, constants dropped | centred **and** uncentred, 5 / 10 | **ours** |
| Condition number | not computed | Belsley, 10 / 30 | **ours** — this is what caught v1 |
| Variable ≈ time or geo | adj R² vs each, INFO | implicit in design VIF | **Meridian's is clearer** → adopt |
| Near-constant column | ATTENTION at sd < 1e-4 | `near_constant_sd` 0.1 | ours is more sensitive |
| Posterior identifiability | not computed | `posterior_correlation.csv` | **ours** |
| Residual autocorrelation | not checked | Durbin-Watson + ACF | **ours** |
| Homoscedasticity | not checked | tercile sd ratio | **ours** |
| Residual tails / normality | not checked | skew + excess kurtosis | **ours** |
| Influential points | not checked | count beyond 3 sd | **ours** |
| Aggregate calibration | Bayesian PPP, 0.05 | pointwise coverage only | **Meridian's** → adopt |
| Confounding / exogeneity | `PotentialBiasCheck` at 0.1 | prose caveat only | **Meridian's** → adopt |
| Negative baseline | probability check, fails at 0.8 | not automated | **Meridian's** → adopt |
| Prior→posterior movement | ROI quantile shift | `contraction` + `mean_shift_in_prior_sd` | ours is finer-grained |
| Implausible ROI | band 0.5–20 | not automated | Meridian's, but we model volume not ROI |
| Convergence | R-hat 1.2 | R-hat 1.01 | **ours** |
| Cross-validation | none (one `holdout_id` mask) | expanding window + stability | **ours** |

We are ahead on the classical battery — unsurprising, since Meridian mostly
declines to run it. Meridian is ahead on three things, all of them *structural*
rather than residual-based, and all of them worth taking.

---

## 5. What to incorporate

In priority order. None is large.

### 5.1 `PotentialBiasCheck` — correlation between media and controls **(highest value)**

Correlate every media/incremental feature against every baseline/control feature
and flag anything above ~0.1. It is the closest thing to an automated
exogeneity warning, and it directly addresses the gap our own
`assumptions_report.md` currently only describes in prose.

For us it maps to: incremental features vs `baseline=1` features. The output is
one table: `feature`, `control`, `correlation`, `flag`.

> On our data this would immediately flag the TDP/AVP-vs-media relationships,
> which is the mechanism by which our media coefficients could be absorbing
> distribution and price effects.

### 5.2 Aggregate posterior predictive p-value

```
ppp = mean(|expected_total - mean(expected_total)| >= |actual_total - mean(expected_total)|)
```

One number per region and one for the portfolio; fail below 0.05. Catches "the
weekly intervals are fine but the annual total is in the model's tail", which
our pointwise coverage cannot see — and which is precisely the failure mode when
a decomposition reconciles to 100% and is still wrong.

### 5.3 Negative-baseline probability

`P(baseline_draw < 0)` per region, from the draws we already have. Given the
history in this project (a baseline that went strongly negative while baseline
features exceeded 100%), this should be a first-class check, not something
noticed by eye in `contribution_reconciliation.csv`.

### 5.4 Variable ≈ time / variable ≈ geo R²

Adjusted R² of each feature against time and against region. Cheap, and it
labels *what* a collinear feature is duplicating in a way a VIF number does not.

### 5.5 Consider, don't rush: a `holdout_id`-style in-model mask

Would give a fast single-fit holdout for exploration, with the expanding-window
CV reserved for final candidates. Worth it only if CV runtime becomes the
bottleneck.

### What NOT to take

- **Meridian's thresholds.** VIF 1000 and correlation 0.999 are near-singularity
  gates, not collinearity checks. Adopting them would mean never flagging
  anything. Keep 5/10 and 0.8/0.95, and keep the condition number.
- **R-hat 1.2.** Too loose. Keep 1.01.
- **Dropping the classical residual battery** because Meridian does. Meridian can
  afford to skip autocorrelation checks less than we can: it models time with a
  flexible knot-based spline baseline that soaks up a lot of what would otherwise
  land in our residuals. With `fourier_order=2` we have far less absorbing
  capacity, which is exactly why our Durbin-Watson matters.

---

## 6. The honest summary

Meridian is not more rigorous than us about regression assumptions — it is
**less** rigorous about the classical ones and **more** rigorous about the
structural ones. It bets that priors handle collinearity and that the
interesting failures are visible in the posterior, not the residuals.

That bet is reasonable for Meridian, which learns its own transforms, carries a
flexible spline baseline, and calibrates on ROI priors from experiments. It is a
worse bet for us: we fix the transforms upstream, carry a much thinner time
baseline, and derive priors from a vendor decomposition rather than an
experiment. Our residuals therefore carry more signal, and our priors are less
trustworthy — so the classical checks earn their place.

Take the three structural checks. Keep our thresholds.

**Related:** `METHODOLOGY.md` (the staged build) · `TUNING_GUIDE.md` (levers) ·
`OUTPUTS_GUIDE.md` (files and columns) · `../../Meridian_Core_Model_Flow.md`
(the model itself).
