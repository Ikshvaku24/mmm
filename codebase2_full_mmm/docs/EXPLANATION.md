# Modelling 101 for codebase 2 — the equation, term by term

> **Status:** planning. First written 2026-09-21; rewritten 2026-10-07 to cover the core model
> only, with the adstock and saturation choices and the trade and baseline variables added.
> **No code yet.**
>
> **Who this is for:** you know the model from codebase 1 — one hierarchical regression over
> regions, sales explained by a baseline plus drivers. This document explains **the model itself**:
> the equation, every term in it, and the transformation each kind of variable gets, with the
> alternatives and the reasons for our choice.
>
> **Not in here:** data checks (`EDA_CHECKS.md`); the build order, prior workflow and
> cross-validation (`METHODOLOGY.md`); outputs and diagnostics (`PHASE2_ARCHITECTURE.md` §11–16).
>
> **Sources.** Meridian is under `../../../meridian/meridian/` (read-only); every Meridian claim
> carries a file pointer. Numbers marked *illustrative* come from small worked examples, not from
> client data.

## Contents

1. The equation
2. Every term at a glance
3. The baseline terms
4. Adstock: carryover
5. Saturation: diminishing returns
6. Adstock and saturation together
7. Trade and baseline variables
8. What the coefficient means, and its prior
9. Units
10. What is learned, and what you fix

Glossary

---

## 1. The equation

For every region `g` and week `t`:

```
sales[g,t] =
  BASELINE: what would have happened anyway ──────────────────────────────────────────
      intercept_g                                   the region's normal level
    + trend_g × t                                   slow drift
    + seasonality(t)                                the yearly pattern
    + Σ γ_d × dummy_d[t]                            one-off events
    + β × TDP  + β × base price                     distribution and price: linear, same week
    + β × category volume + β × competitor price    the market: linear, same week
    − β × Adstock(competitor TV)                    carryover, no saturation
  INCREMENTAL: what our activity added ───────────────────────────────────────────────
    + Σ β_m × Hill(Adstock(media_m))                TV, digital, e-commerce, influencers, OOH, expert
    + Σ β_b × Adstock(BTL_b)                        short carryover, no saturation
    + β × TPR                                       linear, same week
  + noise
```

Every β is per region, pulled toward a shared average; dummies have one shared γ each.

Three things to read off it:

1. **It is the codebase 1 equation with two new functions.** **Adstock** (carryover) and **Hill**
   (saturation) change what a column looks like **before** its coefficient multiplies it.
2. **Only bought pressure that lingers and saturates gets both.** That is media and expert
   activity. Trade and baseline variables mostly enter as they are (§7).
3. **Two kinds of parameter.** The coefficients β differ by region. The transform parameters (decay,
   EC50) are learned **once per channel** and shared by every region.

---

## 2. Every term at a glance

The variable families are the ones in `feature_priors_v7.csv`, codebase 1's prior file for the
sub-brand panel.

| Term | Variables in v7 | Transform | Sign | Reported in |
|---|---|---|---|---|
| Intercept | — | none | free | baseline |
| Trend | — | none | free | baseline |
| Seasonality | — | Fourier terms of the week of the year | free | baseline |
| Dummies | `dummy_*` (21) | none (0/1) | free | baseline |
| Distribution | `sales_brand_all-benefit_distribution_tdp_<sub-brand>` (3) | none | + | baseline |
| Base price | `sales_brand_all-benefit_price_base-price_<sub-brand>` (3) | none | − | baseline |
| Category volume | `sales_market_all-benefit_base_sales-volume` | none | + | baseline |
| Competitor price | `sales_market_all-benefit_price_base-price_Tabs` | none | + | baseline |
| Competitor TV | `media_competitor-tv_…_grps` | adstock | − | baseline |
| Paid media | `media_tv_*`, `media_digital-*`, `media_ecommerce-*`, `media_influencers_*`, `media_ooh_*` (26) | adstock + Hill | + | incremental |
| Expert | the share file's `expert` section (none in v7) | adstock + Hill | + | incremental |
| BTL invest | `btl_price-promotions_*`, `btl_shopper_*` (6) | adstock, ≤ 4 weeks | + | incremental (Trade) |
| TPR | `sales_brand_all-benefit_trade_tpr_<sub-brand>` (3) | none | + | incremental (Trade) |

v7 still flags `sales_market_*`, competitor TV and the dummies `baseline = 0`. The blueprint puts
them in the baseline (decision of 2026-09-14; `PHASE2_ARCHITECTURE.md` §5).

---

## 3. The baseline terms

| Term | What it is | Note |
|---|---|---|
| Intercept | each region's normal level of sales | pooled across regions |
| Trend | a straight-line drift per region | it keeps going into the holdout, which is one reason codebase 1's holdout has never worked |
| Seasonality | sine and cosine pairs of the week of the year (`fourier_order` pairs) | shared by the regions; order 2 is smooth, too smooth for a sharp Q4 |
| Dummies | 1 in the weeks of an event, 0 elsewhere | one shared coefficient each (`global`); with under ~5 active weeks a dummy only memorises a residual |

These need no transform: they are already functions of time.

---

## 4. Adstock: carryover

### 4.1 What it does

Advertising keeps working after the week it ran. Adstock turns "what ran this week" into "what is
working this week":

```
effective media this week = w₀ × this week + w₁ × last week + w₂ × two weeks ago + …
```

With decay 0.6 and a window of 0–8 weeks, one week's GRPs land like this:

| Lands in | same week | +1 | +2 | +3 | +4 | … +8 |
|---|---|---|---|---|---|---|
| Share of the effect | 40.4% | 24.2% | 14.5% | 8.7% | 5.2% | 0.7% |

**Only 40% of a burst is felt in the week it ran.** A model without carryover credits the wrong
weeks, and usually underestimates the channel.

Shifting a column ("a two-week lag") says the whole effect arrives in one later week. Adstock says
it arrives spread over several. You can have both: `min_lag` shifts the start, and the decay
spreads what follows.

### 4.2 The parameters

| Parameter | Plain meaning | Set by |
|---|---|---|
| **decay α** | the share of the effect that survives into the next week | learned, inside a range you set |
| **half-life** | the weeks until half the effect is gone: α = 0.5^(1 / half-life) | how you write that range |
| **window** `min_lag` … `max_lag` | which past weeks may carry weight | you |
| **peak lag θ** | for the delayed form only: the weeks until the effect peaks | learned |

| Half-life (weeks) | 1 | 2 | 3 | 4 | 6 | 13 | 26 |
|---|---|---|---|---|---|---|---|
| decay α | 0.500 | 0.707 | 0.794 | 0.841 | 0.891 | 0.948 | 0.974 |

### 4.3 The forms of adstock

Every adstock is a set of weights on past weeks. The forms differ in three choices:
- the **shape** of the weights;
- whether there is a **window**, a last week after which nothing counts;
- whether the weights are **normalised**, so that they add up to 1.

| Form | Weight on the week `l` weeks ago | Window | Weights add up to | Learned | Used by |
|---|---|---|---|---|---|
| **Traditional geometric** (Broadbent) | α^l, computed as `A_t = x_t + α·A_{t−1}` | none | 1/(1−α): 2 at α 0.5, 5 at 0.8, 10 at 0.9 | α | classic agency models; Robyn's `geometric` |
| **Normalised geometric, no window** | (1−α)·α^l, computed as `A_t = (1−α)·x_t + α·A_{t−1}` | none | 1 | α | — |
| **Normalised geometric, finite window** | α^l ÷ the sum of the weights inside the window | `max_lag` | 1 | α | **Meridian's default, codebase 2** |
| **Delayed** (peaked) | α^((l−θ)²), normalised | `max_lag` | 1 | α, θ | **codebase 2**; Jin et al. (2017, Google). Not in Meridian |
| **Binomial** | (1 − l/W)^(1/α − 1), normalised, with W = `max_lag` + 1 | `max_lag`; the weights reach 0 at its edge | 1 | α | a Meridian option, since version 1.2.0 |
| **Weibull** | a Weibull curve. The "CDF" form lets the week-to-week fade speed up or slow down; the "PDF" form can peak later | finite | — | shape, scale | Meta's Robyn |
| **Koyck** | not an adstock: last week's **sales** becomes a regressor | none | — | one λ shared by everything | econometrics textbooks |
| **Distributed lag** | a free coefficient per lag, or a polynomial across the lags (Almon) | finite | — | one per lag, or per degree | econometrics textbooks |

**What the shapes do to one week of activity** (α 0.8 unless stated; *illustrative*):

| Form | Felt in the same week | Felt by week +4 | Average delay | Peaks at |
|---|---|---|---|---|
| Traditional, no window | 20.0% | 67.2% | 4.0 weeks | week 0 |
| Normalised, window 0–13 | 20.9% | 70.3% | 3.4 weeks | week 0 |
| Normalised, window 0–8 (Meridian's default) | 23.1% | 77.7% | 2.6 weeks | week 0 |
| Delayed, θ = 2, window 0–13 | 11.4% | 95.4% | 2.2 weeks | week 2 |
| Binomial α 0.5, window 0–8 | 20.0% | 77.8% | 2.7 weeks | week 0 |
| Binomial α 0.9, window 0–8 | 12.1% | 58.6% | 3.8 weeks | week 0 |
| Weibull, a fast fade then a long tail (shape 0.5) | 28.1% | 67.4% | 3.5 weeks | week 0 |
| Weibull, peaked (shape 3) | 1.2% | 86.4% | 3.1 weeks | week 3 |

Each form's weights are divided by their own total so the rows can be compared. The traditional
form's raw weights add up to 5.

Two things show already:
- **Normalising does not change the timing.** The traditional row and the normalised 0–13 row
  have almost the same shape.
- **A short window squeezes the timing.** With the same decay, the 0–8 window cuts the average
  delay from 4.0 weeks to 2.6. The part of the effect that belongs after week 8 is not dropped; it
  is re-spread over weeks 0–8, so it arrives too early.

### 4.4 Normalised or traditional: what changes and what does not

**In a linear term they are the same model.** With the same window, the traditional column is the
normalised column multiplied by a fixed number `S`, the sum of the weights (5 at α 0.8). The
coefficient absorbs it:

```
β_traditional = β_normalised ÷ S        same fitted line, same contributions, same timing
```

*Illustrative:* one week of 100 GRPs at α 0.8.
- Traditional adstock adds up to 500 "adstocked GRPs" over the following weeks; normalised adds up
  to 100.
- If that week sold 1,000 extra units, the normalised coefficient is 10 units per GRP and the
  traditional one is 2 units per adstocked GRP.
- Both give the same 1,000 units, landing in the same weeks.

So **traditional adstock does not capture longer effects.** It reports a smaller coefficient on a
bigger column. What lets an effect last longer is a slower decay, and a window long enough to hold
it (§4.5).

**Where the choice does matter.** There are four places, and all four favour normalising.

1. **Before a saturation curve.** Traditional adstock multiplies the column by `S`, and `S` grows
   with the decay. A slow channel is pushed up the Hill curve for no reason except its decay.

   *Illustrative:* a channel on air two weeks in every four at a typical week's weight, EC50 0.6.

   | Decay | Normalised: adstock → response | Traditional: adstock → response |
   |---|---|---|
   | 0.5 | 0.20–0.80 → 0.25–0.57 | 0.40–1.60 → 0.40–0.73 |
   | 0.8 | 0.39–0.61 → 0.39–0.50 | 1.95–3.05 → 0.77–0.84 |
   | 0.9 | 0.45–0.55 → 0.43–0.48 | 4.41–5.46 → 0.88–0.90 |

   - At decay 0.9 the traditional column looks almost saturated.
   - To undo that, the model has to raise EC50 in step with the decay, ten-fold at 0.9. Two
     parameters that must move together are hard for the sampler.
   - The EC50 range then means "typical weeks" only for fast channels.
2. **The prior it implies.** Put the same prior on β under both forms:
   - traditional adstock then assumes slower channels are more effective, because the total effect
     per GRP is β × S and S grows with the decay;
   - normalised adstock makes the decay a statement about **timing only**.
3. **Our coefficient priors.** Codebase 1's prior builder writes `β = contribution ÷ Σx ÷ dv_agg`.
   - Normalised adstock keeps `Σ Adstock(x) = Σx`, apart from the carryover that lands after the
     last week.
   - So that number stays right for the carryover-only variables: competitor TV and BTL.
   - Traditional adstock would need it divided by `S`, which depends on a decay still being learned.
4. **Reading a long-run multiplier.** "One GRP today is worth five GRP-weeks in total" is easier to
   say with traditional weights. The normalised model gives the same number as a derived quantity.

### 4.5 Window or no window

A window is the last week that can carry weight. With normalised weights, a window that is too
short does not lose the effect; it **squeezes** it (§4.3).

So the window should hold the slowest decay you allow, with less than 5% of the carryover beyond
it:

```
max_lag = ceil( ln 0.05 ÷ ln α_max − 1 )
```

| Slowest half-life you allow | 1 wk | 2 wk | 4 wk | 13 wk | 26 wk | 52 wk |
|---|---|---|---|---|---|---|
| Window needed (`max_lag`, weeks) | 4 | 8 | 17 | 56 | 112 | 224 |

Turned around, **Meridian's default window of 8 supports a half-life of about two weeks at most.**

**"No window" is simply the longest window: the whole panel.** Meridian does exactly this when
`max_lag` is longer than the data: the window becomes the whole media history
(`model/adstock_hill.py`, line 254).

**The real limit on a long window is history.** The first weeks of the panel have no earlier media
to carry over. The fix is to supply media from before the modelling window, as Meridian recommends
(`data/input_data.py`, the `media` argument: "up to `max_lag` additional periods prior to this
window").

**`min_lag`** is the first week allowed to carry weight. It is the "lag" in today's preprocessing.
It is fixed rather than learned, because the sampler moves in continuous steps and cannot search
between "1 week" and "2 weeks". The peak lag θ of the delayed form is the continuous version, and
it is learned.

### 4.6 What Meridian uses, and why

| Choice | Meridian | Source |
|---|---|---|
| Shape | geometric by default; binomial per channel as an option | `model/spec.py` (`adstock_decay_spec`); `model/adstock_hill.py::compute_decay_weights` |
| Normalised | always | `adstock_hill.py::_adstock` calls `compute_decay_weights(normalize=True)` (line 290) |
| Window | `max_lag` = 8 by default; longer than the data means the whole history | `spec.py`; `adstock_hill.py`, line 254 |
| Decay prior | `alpha_m ~ Uniform(0, 1)` per channel, shared by every geo | `model/prior_distribution.py` |
| Peaked (delayed), Weibull, Koyck | not available | — |

Meridian does not write down why. The reasons below follow from its other choices, and from Jin et
al. (2017), the Google paper its adstock comes from.

1. **Saturation comes next.** Meridian's EC50 prior, `TruncatedNormal(0.8, 0.8)` on media divided
   by its median, means the same thing at every decay only if adstock keeps media on that axis.
   Normalising does that (§4.4, point 1).
2. **The media prior is on the channel's total effect.** That is ROI in Meridian, and contribution
   volume in our design. Normalising keeps the decay a timing parameter, so the decay cannot move
   that total.
3. **A fixed window is one convolution.** It is fast on a GPU and easy for the sampler to
   differentiate. A week-by-week recursion over 104 weeks or more is slower.
4. **Binomial covers "lasts about N weeks, then stops".** Its weights stay high, then fall to zero
   at the window's edge. Geometric weights always fall fastest at the start.

A trap when reading the source: the docstring of `AdstockTransformer.forward` (line 378) shows the
un-normalised sum, but the code normalises (line 290). The code is what runs.

### 4.7 What we use

| Per-feature setting | Values | Default | Use it for |
|---|---|---|---|
| `adstock` | `geometric`, `delayed`, `binomial` (P3), `none` | `geometric` for media, expert, BTL and competitor TV; `none` for the rest | `delayed` where the effect builds before it peaks (OOH, a launch) |
| half-life range | weeks | by family (`PHASE2_ARCHITECTURE.md` §6) | the business statement "TV works for two to three weeks" |
| `max_lag` (the window) | 0 up to the panel length | the 5% rule above, from the slowest half-life allowed | up to 104 weeks for a long-term variable (§4.8) |
| `min_lag` | 0, 1, 2 … | 0 | a hard delay, chosen by cross-validation |
| `adstock_normalise` | `true` / `false` | `true` | `false` **only** to reproduce preprocessed variables in the bridge test (`METHODOLOGY.md` §8). Not allowed together with Hill |

Not planned:
- **Weibull.** Two shape parameters per channel are more than 104 weeks can learn, and `delayed`
  covers the peaked case with one.
- **Koyck** (§4.8).
- **A free distributed lag**, with one coefficient per lag per channel.

> **Your proposal: a choice between geometric and traditional for each feature.**
> The traditional form differs from ours in two ways: it has **no window**, and its weights are
> **not normalised**.
> - *No window* is what lets it carry long effects. Keep that, per feature, as a long `max_lag`, up
>   to the whole panel.
> - *Not normalised* adds nothing in a linear term, because the coefficient absorbs it. It hurts
>   with saturation and with our priors (§4.4).
>
> So the per-feature option worth having is **the window and the decay range**. Normalisation stays
> on, with an off switch kept only for the bridge test.

### 4.8 Long-lasting effects: brand-building campaigns on a 4-year panel

Some campaigns run for years and become part of the brand. "Long-lasting" can mean two different
things:

- **A long carryover.** The campaign keeps selling for months after it airs, then fades: a
  half-life of 13–52 weeks.
- **A permanent lift.** The campaign changed the brand, and base sales stay higher for good. No
  adstock with a decay can represent this.
  - At decay 1, a normalised window becomes the campaign's average over the window: a "brand stock".
  - A traditional one becomes cumulative GRPs, which only ever rises, like a trend.

**Why a long carryover is hard to measure.** *Illustrative:* four years of weekly bursts, with the
budget rising each year.

| Half-life | Window needed | Share of the adstocked column that intercept + trend + seasonality can reproduce | … with 2 years of earlier media supplied | Share of the effect landing inside the panel |
|---|---|---|---|---|
| 1 week | 4 | 6% | 5% | 99% |
| 4 weeks | 17 | 20% | 16% | 97% |
| 13 weeks | 56 | 69% | 42% | 90% |
| 26 weeks | 112 | 89% | 59% | 81% |
| 52 weeks | 224 | 93% | 68% | 76% |

The 26- and 52-week rows were computed with the window capped at 104 weeks.

Three lessons:

1. **Beyond a ~13-week half-life, the campaign looks like baseline.**
   - Most of its movement can be reproduced by the intercept, trend and seasonality, so the data
     cannot split them, and the priors decide.
   - Codebase 1 met the same problem with a UCM brand-equity series (codebase 1
     `docs/METHODOLOGY.md` §2b).
2. **History from before the window helps most.** Without it, a long adstock spends its first year
   climbing from zero, and that climb looks exactly like a trend.
3. **Part of the effect has not happened yet.** At a 26-week half-life, about a fifth of the effect
   of activity inside the panel lands after the last week. The contribution over the window
   therefore understates the long-term effect.

**The options, best first:**

| Option | How | For | Against | Verdict |
|---|---|---|---|---|
| **A. A dedicated long-term variable** | what the team does today, a separate column for the campaign, given: a long normalised adstock (half-life 13–52 weeks); a window of up to 104 weeks; no Hill; a positive sign; an informative prior; and media history from before week 1 | fits the design: one extra coefficient and one decay | competes with the trend; the prior carries much of the answer | **default** |
| **B. Short + long carryover on one channel** | the channel enters twice, through a fast and a slow adstock, each with its own coefficient, plus a prior on the long-to-short ratio | separates the quick sales effect from base-building | without a strong ratio prior, two coefficients on one column trade off almost perfectly | P3 experiment |
| **C. A two-stage brand model** | stage 1 explains a brand-health series (awareness, consideration, a UCM brand-equity series) with long-adstocked media; stage 2 puts that series into the sales model as a baseline driver | the most defensible: brand data move slowly by design, so the slow effect is measured where it lives | needs a brand-tracking series, and two models | **best, when tracking data exist**. Stage 2 is codebase 1 METHODOLOGY §2b |
| **D. A moving baseline, explained afterwards** | let the baseline flex (more trend knots), then regress the baseline on long-adstocked media | adds nothing to the sales model | circular: the baseline was estimated from the same sales | no |
| **E. Koyck** | add last week's sales as a regressor | one parameter | forces one decay on every driver *and* on the baseline; last week's sales soaks up any persistence; biased with region intercepts on short panels | no |
| **F. Traditional adstock** | un-normalised weights | — | the same as A in a linear term, and worse with Hill | not needed |

Whichever you choose, judge it by two things:
- the posterior correlation between the long-term coefficient and the trend;
- cross-validation (`METHODOLOGY.md` §7).

Do not judge it by the in-sample fit, which any slow series improves.

---

## 5. Saturation: diminishing returns

### 5.1 What it does

Saturation turns adstocked media into **how much of the channel's maximum effect** you are getting,
between 0 and 1:

```
response = activity^slope ÷ (activity^slope + EC50^slope)
```

At zero activity the response is 0. At EC50 it is 0.5. It approaches 1 and never passes it.

With EC50 at 0.6 typical weeks:

| Activity (typical weeks) | 0.25 | 0.5 | 1.0 | 2.0 | 4.0 |
|---|---|---|---|---|---|
| Response | 0.294 | 0.455 | 0.625 | 0.769 | 0.870 |

Doubling activity from 1.0 to 2.0 buys 23% more response, not 100%. Without saturation, the model
would promise that a doubled budget doubles the effect.

### 5.2 The parameters

| Parameter | Plain meaning |
|---|---|
| **EC50** | the activity at which you get half the channel's maximum effect |
| **slope** | the shape: 1 is a plain diminishing-returns curve; above 1 is an S-curve with a slow start |

What the slope does, at EC50 0.6:

| Activity (typical weeks) | 0.1 | 0.25 | 0.5 | 1.0 | 2.0 | 4.0 |
|---|---|---|---|---|---|---|
| slope 1 | 0.143 | 0.294 | 0.455 | 0.625 | 0.769 | 0.870 |
| slope 2 | 0.027 | 0.148 | 0.410 | 0.735 | 0.917 | 0.978 |
| slope 3 | 0.005 | 0.067 | 0.367 | 0.822 | 0.974 | 0.997 |

At slope 2 the curve is an S:
- light weeks do almost nothing;
- the curve is steepest at 0.35 typical weeks;
- it is nearly flat from 2 typical weeks on.

### 5.3 The forms of saturation

| Curve | Formula | Ceiling | Shape | Learned | Used by |
|---|---|---|---|---|---|
| **Hill** | x^s ÷ (x^s + EC50^s) | yes | concave at slope ≤ 1, an S-curve above 1 | EC50, slope | **Meridian, codebase 2**, Robyn, Jin et al. (2017). It is the same curve as ADBUDG (Little, 1970); at slope 1 it is Michaelis–Menten |
| Negative exponential | 1 − e^(−x/c) | yes | concave only | c | common in agency models |
| Logistic, tanh | e.g. tanh(x/c) | yes | concave and through zero; an S-curve if shifted | c | PyMC-Marketing |
| Log | log(1 + x/c) | **no** | concave, never flat | c | econometric (elasticity) models |
| Power | x^b, with 0 < b < 1 | **no** | concave, never flat | b | log-log models |
| None | x | no | a straight line | — | channels with no curve to learn |

### 5.4 The choice matters outside the data, not inside it

*Illustrative:* each curve fitted to the same Hill curve (EC50 0.6, slope 1) over the range a
channel actually runs at, 0–2 typical weeks.

| Curve | Largest gap inside 0–2 | At 4 typical weeks (Hill: 0.870) | At 8 (Hill: 0.930) | Extra response per extra unit at x = 2, the top of the range (Hill: 0.089) |
|---|---|---|---|---|
| Negative exponential | 0.022 | 0.771 | 0.772 | 0.041 |
| Log | 0.023 | 0.973 | 1.160 | 0.130 |
| Power | 0.079 | 1.112 | 1.519 | 0.183 |
| Hill, slope 2 | 0.098 | 0.740 | 0.744 | 0.024 |

- **Inside the data the curves agree.** The first two match Hill to within about 3%, so the fit
  cannot choose between them.
- **Outside the data they disagree.** At twice the heaviest week they differ from Hill by −11% to
  +28%, and the curves without a ceiling keep rising.
- **The marginal return differs most.** At the top of the observed range, an extra unit is worth
  0.024 under one curve and 0.183 under another: a factor of 8.

So choose the curve for how it **extrapolates** and for the **marginal returns** it implies, which
is the budget question. Do not choose it for its fit. A curve with a ceiling is the conservative
choice.

### 5.5 What Meridian uses, and why

| Choice | Meridian | Source |
|---|---|---|
| Curve | Hill for every paid channel; `none` per channel as an option | `model/spec.py` (`saturation_spec`); `model/adstock_hill.py::_hill` |
| Slope | fixed at 1 | `model/prior_distribution.py`: `slope_m = Deterministic(1.0)` |
| EC50 | `TruncatedNormal(0.8, 0.8)` on [0.1, 10], on media divided by its median | `prior_distribution.py`: `ec_m` |
| Order | adstock first, then Hill | `spec.py`: `hill_before_adstock = False` |

**Why the slope is fixed.** If you change it, Meridian warns that this "may lead to convex Hill
curves. This may lead to poor MCMC convergence and budget optimization may no longer produce a
global optimum" (`prior_distribution.py`, lines 843–847).
- At slope 1, every extra unit is worth less than the one before, so there is one best budget
  split.
- The exception: reach-and-frequency channels **learn** their slope (`slope_rf ~ LogNormal(0.7,
  0.4)`). Their curve is over frequency, where a threshold ("effective frequency") is the point.
- We have no reach-and-frequency data.

**Why Hill at all.**
- It has a ceiling: no channel delivers unlimited sales.
- It is zero at zero.
- Its EC50 is a readable number on the "typical week" axis.
- One more parameter turns it into an S-curve when there is evidence for one.

### 5.6 What we use

- **Hill with slope 1 by default.** EC50 is learned within a range you write in GRPs (or
  impressions, clicks), converted for you.
- **Learn the slope only** where both hold:
  - a channel's weekly weight varies widely, with heavy weeks several times its typical week;
  - there is a business reason for a threshold, such as TV wear-in.
- **`saturation: none`** where a curve cannot be learned or is not needed:
  - competitor TV;
  - BTL;
  - small lines;
  - channels whose active weeks are all about the same size.
- **No log or power curves for media.** With no ceiling, they give over-optimistic answers for
  bigger budgets. Log is useful for price and distribution, where the question is an elasticity
  (§7).
- **Adstock first, then Hill**, as in Meridian. Hill-before-adstock (P3) suits a channel that
  saturates within the week it airs, and whose response, rather than its pressure, carries over.

---

## 6. Adstock and saturation together

The two transforms are not independent. Carryover spreads activity across weeks, which lowers the
peaks, and lower peaks lose less to saturation.

Same 16 weeks of GRPs, same EC50 of 0.6; only the decay changes:

| Decay | Half-life | Highest week (adstocked) | Response in that week | Total response over the window |
|---|---|---|---|---|
| 0.0 (none) | — | 1.23 | 0.672 | 4.32 |
| 0.3 | 0.6 wk | 1.07 | 0.641 | 4.93 (+14%) |
| 0.6 | 1.4 wk | 0.87 | 0.591 | 5.33 (+23%) |
| 0.8 | 3.1 wk | 0.61 | 0.505 | 5.30 (+23%) |

1. **The same GRPs deliver more when they are spread**, because less of each burst is wasted on the
   flat part of the curve. That is a real media effect, not an artefact.
2. **Decay and coefficient can trade places.** A longer carryover with a smaller coefficient draws
   almost the same fitted line. The model shows this as a correlation between the two parameters;
   pin the decay when the data cannot separate them.
3. **The total and the shape come from different places.**
   - The total over the window is held mostly by the prior and the data.
   - The transforms mostly decide its timing and its marginal value.
   - So two decays often give similar totals and different advice about next week's budget.

---

## 7. Trade and baseline variables

Media and expert activity are bursts of bought pressure, so their effect lingers (adstock) and
saturates (Hill).

Trade and baseline variables are different. They describe the conditions of the week: the shelf
price, how many stores carry the brand, whether a discount is running. Their effect is immediate,
and it is already measured in its own units. So by default they enter **linearly, in the same week,
with no adstock and no Hill**.

That is Meridian's rule too:
- its **non-media treatments**, "running a promotion, the price of a product and a change in a
  product's packaging", have "no Adstock and Hill effects" (`data/input_data.py`,
  `non_media_treatments`);
- its **controls** are linear as well (`model/posterior_sampler.py`, lines 443–502);
- it reports these levers against their **minimum** by default
  (`model/equations.py::compute_non_media_treatments_baseline`).

Where we depart from linear, there is a reason, given below.

### 7.1 TPR, temporary price reduction (Trade)

`sales_brand_all-benefit_trade_tpr_<sub-brand>`; positive; Trade pillar.

- **No adstock.** A price cut works while it runs.
  - Afterwards sales usually **dip** below normal, because shoppers stocked up (pantry loading).
    That is the opposite of carryover.
  - A positive adstock would credit TPR with sales in exactly the weeks where its true effect is
    negative.
- **No Hill.** Response to the depth of a discount is usually S-shaped, not concave:
  - small cuts go unnoticed, mid-range cuts work, and very deep cuts add little;
  - a concave Hill curve gets the start wrong;
  - learning an S-curve needs many promotions at different depths;
  - so linear is the honest first model.
- **Options.** Test each by cross-validation, one at a time.
  - *The post-promotion dip:* add last week's TPR as its own column, with a negative or free sign.
    The net effect of a promotion is then the lift minus the dip.
  - *Depth and breadth:* if the data has both (the % off, and the share of stores on promotion),
    their product is usually the cleanest single driver.
  - *Always-on TPR:* if TPR is a share of volume and is non-zero almost every week, treat it like a
    level variable and centre it (§7.2).
  - *Monthly data:* the dip falls inside the month and nets out, so keep it linear.
- **Watch the overlap with BTL price promotions.**
  - `btl_price-promotions_*_invest` may be the money behind the same price cuts that TPR measures.
    With both in the model, they compete for one effect.
  - The TPR source is still pending (probably trade Price Promotions). Settle it before both go in.

### 7.2 TDP, total distribution points (baseline)

`sales_brand_all-benefit_distribution_tdp_<sub-brand>`; positive; baseline. `ACV_WD_Any Merch` in
the retailer datacube is the same kind of variable.

- **No adstock.** Distribution is already a stock: the shelves that carry the brand this week, which
  persist by themselves.
- **No Hill.** TDP moves slowly within a narrow range, so a curve cannot be learned.
  - Diminishing returns are real: the next listing is worth less than the first.
  - If you want them, use **log TDP**. With sales divided by their region mean (§9), the
    coefficient on log TDP reads as the elasticity at an average week.
- **Centre it** (`center_mode: mean`) when it is always on and barely moves.
  - Otherwise it duplicates the intercept: the cause of codebase 1's broken first run.
  - Report it against zero (`contribution_reference: zero`) to match the vendors' "versus zero"
    decomposition.
- **It is a condition, not a lever.** Retailers list what sells, so part of the causality runs from
  sales to TDP.

### 7.3 Base price (baseline)

`sales_brand_all-benefit_price_base-price_<sub-brand>`; negative; baseline.

- **No adstock.** Price acts at once.
  - A price change has a short "reference price" shock while shoppers get used to it.
  - Most of that is carried by TPR, which holds the temporary cuts; base price holds the level.
- **No Hill.** Price response does not saturate.
  - The standard shape is a constant elasticity: a 10% rise loses the same share of sales at any
    price.
  - **log price** gives exactly that.
- **Options.**
  - Price **relative** to competitors (ours ÷ theirs). This removes inflation, and the
    near-collinearity between the two prices.
  - On a 4-year panel, **real** rather than nominal price. Nominal prices rise with inflation and
    copy the trend.
- **Centre it**, like TDP.
  - Measured against zero, price produces the vendors' large negative contribution (−38% of sales
    in one vendor model).
  - The blueprint proposes measuring it against its highest level instead (`contribution_reference:
    max`, P2): "the sales we keep by pricing below our highest price".
- **A log column measured against zero means "against a value of 1".** Use `min` or `mean` as the
  reference for a log column.

### 7.4 Category volume and competitor price (baseline)

- `sales_market_all-benefit_base_sales-volume`: positive.
- `sales_market_all-benefit_price_base-price_Tabs`: positive, because competitors pricing higher
  helps us.

How they enter:
- **Linear, centred, no transforms**, for the same reasons as TDP and price.
- **Category volume must not contain our own sales.** If it does, the regressor contains the KPI,
  and it will take credit for our own media. Use the category excluding our brand.
- **Category volume carries the category's seasonality.** With it in the model, the Fourier terms
  can usually be fewer.
- **Check the v7 labels.**
  - In v7 both carry `baseline = 0`, with the pillars "Competitor Sales" and "Competitor Price".
    The blueprint puts them in the baseline.
  - A positive sign on "Competitor Sales" reads as category demand. Confirm what the series is.

### 7.5 BTL invest (Trade)

`btl_price-promotions_*_invest_<sub-brand>` and `btl_shopper_*_invest_<sub-brand>`; positive;
Trade.

- **Short adstock (window ≤ 4 weeks), no Hill.** Decided 2026-09-14.
- Why the short carryover: in-store activity has a short tail, and spend is often booked unevenly
  across the weeks it covers. A short carryover absorbs both.
- Why no curve: the lines are small and measured only in spend, so there is no range to learn a
  curve from.

### 7.6 Competitor TV (baseline)

`media_competitor-tv_…_grps`; negative; baseline.

- **Adstock, no Hill.** Decided 2026-09-14.
- Why adstock: it is advertising, so it carries over like ours.
- Why no Hill: we need its drag, not its response curve, and a curve for a small negative effect
  could not be learned.

### 7.7 Dummies (baseline)

`dummy_*` (21); free sign; one shared coefficient (`global`).

- **No transform:** 1 in the event weeks, 0 elsewhere.
- **Option: a decaying dummy** (adstock on the 0/1 column), for an event with an aftermath such as
  a stock-out, a recall or a launch. It costs a learned decay, so use it only with a reason.
- What a dummy means in our data is still pending.

### 7.8 Summary

| Variable | Adstock | Hill | Scaling | Optional transform |
|---|---|---|---|---|
| TPR | no | no | as delivered; centred if always on | last week's TPR (the dip); depth × breadth |
| TDP / ACV | no | no | centred | log |
| Base price | no | no | centred | log; relative to competitors; real |
| Category volume | no | no | centred | log; excluding our brand |
| Competitor price | no | no | centred | as a ratio to ours |
| BTL invest | ≤ 4 weeks | no | ÷ typical active week | — |
| Competitor TV | yes | no | ÷ typical active week | — |
| Dummies | no | no | none | a decaying dummy |

---

## 8. What the coefficient means, and its prior

| | Codebase 1 | Codebase 2 |
|---|---|---|
| What multiplies β | the pre-transformed column | Hill channels: **the response, 0 to 1**. Carryover-only: the adstocked column. Everything else: the column as delivered |
| What β means | effect per unit of that column | Hill channels: the effect at full saturation. Everything else: as in codebase 1 |
| Contribution in a week | β × column × dv_scale | β × response × dv_scale for Hill channels |

**A media coefficient is no longer "sales per GRP".** It is "what this channel would add in a week
if it were completely saturated". The response says how much of that you actually got: a channel at
a response of 0.6 is delivering 60% of its β this week.

**That is why the prior moved for saturating channels.**
- A prior written directly on β would mean something different at every decay and EC50 the sampler
  tries.
- A prior on the channel's **contribution volume**, the units it delivered over the window, means
  the same thing on every draw.
- So that is what we write, and the model works out the coefficient on each draw.

**Every other variable keeps codebase 1's coefficient priors.** A linear or normalised-adstock
column keeps its total (§4.4, point 3).

**No ROI.** Everything stays in sales volume.

---

## 9. Units

| Column | Divided by | So that |
|---|---|---|
| Sales | its region's mean week (`dv_scale: mean`; the generated priors assume this) | a coefficient reads as a fraction of the region's average week |
| Media, expert, BTL, competitor TV | its own median active week; never centred | 1.0 = a typical active week, and zero stays zero |
| TDP, price, category | nothing; centred on their mean where always on | priors per raw unit, as in codebase 1 |
| TPR, dummies | nothing | priors per raw unit |

On the media axis, **EC50 is in typical weeks.** An EC50 of 0.6 means 0.6 of a typical active
week: 78 GRPs if the typical week is 130.

**If a scaling changes, every EC50 range and every prior must change with it.** That is the one trap
in this area, and it is the same trap as codebase 1's KPI scaling rule.

---

## 10. What is learned, and what you fix

| | Learned from the data | Fixed by you |
|---|---|---|
| Every variable | the coefficient β, per region, pooled | sign, pooling, baseline flag, pillar |
| Carried-over variables | decay α (and peak lag θ for `delayed`), once per channel or `transform_group`, shared by the regions | the form, the window, `min_lag`, the half-life range, normalisation |
| Saturating channels | EC50 (and the slope, if released), shared by the regions | the curve, the EC50 range |

- **The data must contain what you ask it to learn.**
  - A decay needs weeks off air.
  - A curve needs weeks of very different weight.
  - Without them the prior gives the answer, and you pin the parameter (`fix_alpha`, `fix_ec`).
    Pinning every transform at today's preprocessing values is also how codebase 2 reproduces
    codebase 1.
- **Decay and EC50 are shared by the regions** because 104 weeks cannot support a separate curve
  per region. Meridian shares them for the same reason.
- **The uncertainty is honest.** Codebase 1's intervals carried coefficient uncertainty only.
  Codebase 2's also carry the transforms' uncertainty, so they are wider.

---

## Glossary

| Term | One line |
|---|---|
| **Adstock** | carryover: this week's effective media is a weighted average of recent weeks |
| **Traditional (Broadbent) adstock** | `A_t = x_t + α·A_{t−1}`: no window; the weights add up to 1/(1−α) |
| **Normalised adstock** | the weights add up to 1, so the decay changes timing, not size |
| **Window** (`min_lag` … `max_lag`) | the past weeks allowed to carry weight |
| **Decay (α)** | the share of an effect that survives into the next week |
| **Half-life** | the weeks until half the effect is gone; α = 0.5^(1 / half-life) |
| **Peak lag (θ)** | the weeks until a delayed channel's effect peaks |
| **Binomial adstock** | Meridian's alternative shape: it holds, then falls to zero at the window's edge |
| **Weibull adstock** | Robyn's two-parameter shapes: a changing fade, or a later peak |
| **Koyck** | last week's sales as a regressor; one decay for everything |
| **Saturation / Hill** | diminishing returns: a response from 0 to 1 |
| **EC50** | the activity giving half the channel's maximum effect |
| **Slope** | the curve's shape: 1 = plain diminishing returns, above 1 = an S-curve |
| **Typical active week** | the median of a channel's non-zero weeks; the unit media is scaled in |
| **Response** | the saturated value, 0 to 1, that the coefficient multiplies |
| **Contribution volume** | the units of sales a driver delivered over the window; what a saturating channel's prior is written on |
| **TPR** | temporary price reduction: a promotional price cut |
| **TDP** | total distribution points: how many stores, times how many items, carry the brand |
| **Elasticity** | the % change in sales for a 1% change in a driver; what a log transform measures |
