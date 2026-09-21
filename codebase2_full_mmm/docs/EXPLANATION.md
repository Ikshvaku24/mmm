# Modelling 101 for codebase 2 — what the transforms do, and why

> **Status:** planning, 2026-09-21.
>
> **Who this is for:** you already know the model from codebase 1 — one hierarchical regression
> over regions, sales explained by a baseline plus drivers. This document explains the part that is
> new: **adstock and saturation**, what their parameters mean, how they sit inside the model, and
> how they change the numbers you report.
>
> **Companions:** `METHODOLOGY.md` (the order to build in) · `PHASE2_ARCHITECTURE.md` (every
> default and table) · `Codebase_2_blueprint.html` (one page).
>
> Every number below is computed from the same worked example: one channel, 16 weeks, a typical
> active week of 130 GRPs.

## Contents

1. What the model is doing
2. Why raw media cannot go straight into a regression
3. Adstock: carryover
4. Saturation: diminishing returns
5. The two together
6. What the coefficient means now
7. Scaling, and why EC50 is not in GRPs
8. What changes because the model learns the transforms
9. How the transforms move the numbers you report
10. Reading the transform outputs
11. Questions people actually ask

---

## 1. What the model is doing

For every region and every week:

```
sales = baseline + what each driver added + noise
```

- The **baseline** is what would have happened anyway: the level of the business, its trend, its
  seasonality, plus the always-on conditions (distribution, price, the category, competitors).
- The **drivers** are the things you do: media, trade.
- Each region gets its own coefficients, pulled toward a shared average. Small regions borrow
  strength from big ones.

That much is codebase 1. The difference in codebase 2 is what happens to a media column **before**
it is multiplied by its coefficient.

---

## 2. Why raw media cannot go straight into a regression

Two things everyone in the room already believes:

1. **Advertising keeps working after the week it ran.** A burst in week 10 still sells something in
   weeks 11 and 12.
2. **Twice the advertising in one week does not sell twice as much.** The tenth GRP in a week is
   worth less than the first.

A plain regression on raw GRPs assumes the opposite of both: that this week's GRPs only affect this
week, and that each extra GRP is worth exactly as much as the last.

So media gets two transforms before it enters the model:

```
raw GRPs ──▶ ADSTOCK ──▶ SATURATION ──▶ × coefficient ──▶ contribution
             (carryover)  (diminishing
                           returns)
```

Codebase 1 did both in preprocessing, with numbers chosen by hand and then treated as facts.
Codebase 2 does both **inside** the model and **learns** the numbers, with the data's uncertainty
about them carried into every reported contribution.

---

## 3. Adstock: carryover

### 3.1 What it is

Adstock turns "what ran this week" into "what is working this week":

```
effective media this week = w₀ × this week + w₁ × last week + w₂ × two weeks ago + …
```

The weights `w` fall away with distance, and **they add up to 1**. So adstocked media is a
**weighted average of recent weeks**, on the same scale as the original.

### 3.2 The parameters

| Parameter | Plain meaning | Who sets it |
|---|---|---|
| **decay, α** | how much of the effect survives into the next week | learned, inside a range you set |
| **lag window**, `min_lag` … `max_lag` | which weeks are allowed to carry any weight at all | you, fixed |
| **peak lag, θ** | for a "delayed" channel: how many weeks until the effect peaks | learned, optional |

### 3.3 Decay, read as a half-life

Decay on its own is abstract. Half-life — "how long until half the effect is gone" — is not.

| Half-life | 1 week | 2 weeks | 3 weeks | 4 weeks | 6 weeks |
|---|---|---|---|---|---|
| decay α | 0.500 | 0.707 | 0.794 | 0.841 | 0.891 |

This is the number to argue about with the business: *how long does TV keep working?* You answer in
weeks, and the range of decays follows.

### 3.4 Where one week of media actually lands

With decay 0.6 and a window of 0–8 weeks, one week's GRPs are spread like this:

| Lands in | Share | Running total |
|---|---|---|
| the same week | 40.4% | 40.4% |
| +1 week | 24.2% | 64.7% |
| +2 weeks | 14.5% | 79.2% |
| +3 weeks | 8.7% | 87.9% |
| +4 weeks | 5.2% | 93.2% |
| … +8 weeks | 0.7% | 100% |

**Only 40% of a burst is felt in the week it ran.** That is the whole point of carryover, and it is
why a model without it credits the wrong weeks and usually underestimates the channel.

```
 decay 0.3 (half-life 0.6 wk)      decay 0.7 (half-life 1.9 wk)
 lag 0 ████████████████████ 0.70   lag 0 █████████ 0.31
 lag 1 ██████ 0.21                 lag 1 ██████ 0.22
 lag 2 ██ 0.06                     lag 2 ████ 0.15
 lag 3 ▌ 0.02                      lag 3 ███ 0.11
 lag 4   0.01                      lag 4 ██ 0.08
 fast: almost all in week 0        slow: still working a month later
```

### 3.5 Why the weights add to 1

If they did not, changing the decay would change the **size** of the media column, and the
coefficient would silently absorb it. Because they do add to 1:

- adstocked media stays on the same axis as the input, so "1.0" still means a typical active week;
- the total over the whole window is roughly preserved, so changing the decay redistributes
  contribution **in time** rather than inventing or destroying it;
- the half-saturation point stays comparable across different decays.

### 3.6 The lag window

`max_lag` is the last week that can carry weight. It matters more than people expect:

- **Too short**, and you have silently truncated the carryover. A decay of 0.8 with an 8-week window
  throws away 13.4% of the effect; a 13-week window throws away 4.4%.
- The window therefore **caps the decay** you can honestly express: an 8-week window supports a
  decay of about 0.72 (a two-week half-life), no more.

`min_lag` is the first week that can carry weight — the "lag" applied in preprocessing today. It is
fixed rather than learned, because the sampler works in continuous numbers and "start at week 1"
is a whole number. You choose between 0, 1 and 2 by cross-validation.

**Peak lag θ** is the softer version: with a delayed channel, the effect builds and peaks θ weeks
after the activity, instead of starting at its maximum. Useful for OOH or a campaign that needs
time to be noticed.

---

## 4. Saturation: diminishing returns

### 4.1 What it is

Saturation takes adstocked media and returns **how much of the channel's maximum effect** you are
getting, between 0 and 1:

```
response = activity^slope ÷ (activity^slope + EC50^slope)
```

- At zero activity, the response is 0.
- At EC50, the response is exactly 0.5 — half of everything the channel could do.
- However much you spend, the response approaches 1 and never passes it.

### 4.2 The parameters

| Parameter | Plain meaning |
|---|---|
| **EC50** | the level of activity at which you get half the channel's maximum effect |
| **slope** | the shape: 1 is a plain diminishing-returns curve; above 1 makes an S-curve with a slow start |

Slope is fixed at 1 by default (as in Meridian), because a 104-week panel rarely has enough range to
tell the two shapes apart.

### 4.3 What the curve does to an extra GRP

With EC50 at 0.6 typical weeks:

| Activity (typical weeks) | 0.25 | 0.5 | 1.0 | 2.0 | 4.0 |
|---|---|---|---|---|---|
| Response | 0.294 | 0.455 | 0.625 | 0.769 | 0.870 |

Read the middle of that row: **doubling activity from 1.0 to 2.0 buys 23% more response**, not
100%. Down at the quiet end, doubling from 0.25 to 0.5 buys 55% more. That single fact is why
saturation belongs in the model: without it, the model would promise that a doubled budget doubles
the effect.

```
 response
   1.0 ┤                          ································
       ┤                  ·········
       ┤            ······
   0.5 ┤·······•← EC50: half the maximum effect
       ┤   ····
   0.0 ┤···
       └──────────────────────────────────────────────────────────
        0     EC50            2×            3×            4×  activity
```

### 4.4 Where you sit on the curve is the whole story

Two channels can share the same curve and be worth completely different amounts, because they sit in
different places on it:

- A channel running at **a quarter of its EC50** is on the steep part. More money buys almost
  proportionally more.
- A channel running at **four times its EC50** is flat. More money buys very little; the honest
  recommendation is to move budget away, or change the creative rather than the weight.

This is why `04_transforms/adstock_ranges.csv` reports, per region, how many active weeks sit above
EC50. It answers "are we buying on the steep part or the flat part?".

---

## 5. The two together

They are not independent. Carryover **spreads** activity across weeks, which **lowers the peaks**,
and lower peaks lose **less** to saturation.

Same 16 weeks of GRPs, same EC50 of 0.6, only the decay changed:

| Decay | Half-life | Highest week (adstocked) | Response in that week | Total response over the window |
|---|---|---|---|---|
| 0.0 (none) | — | 1.23 | 0.672 | 4.32 |
| 0.3 | 0.6 wk | 1.07 | 0.641 | 4.93 (+14%) |
| 0.6 | 1.4 wk | 0.87 | 0.591 | 5.33 (+23%) |
| 0.8 | 3.1 wk | 0.61 | 0.505 | 5.30 (+23%) |

Two things to take from this table:

1. **The same GRPs deliver more when they are spread**, because less of each burst is wasted on the
   flat part of the curve. That is a real media effect, not an artefact.
2. **Decay and coefficient can trade places.** A longer carryover with a smaller coefficient draws
   almost the same fitted line. The model reports that honestly as a correlation between the two
   parameters; it is the main reason we check `corr(decay, β)` and pin decay when the data cannot
   separate them.

---

## 6. What the coefficient means now

This is the part that catches people who know codebase 1.

| | Codebase 1 | Codebase 2 |
|---|---|---|
| What multiplies the coefficient | the pre-transformed column | the **saturated** value, between 0 and 1 |
| What β means | effect per unit of that column | effect **at full saturation** |
| Contribution in a week | β × column × dv_scale | β × response × dv_scale |

So a media coefficient is no longer "sales per GRP". It is "what this channel would add in a week if
it were completely saturated", and the response says how much of that you actually got. A channel
running at a response of 0.6 is delivering 60% of its β this week.

This is also why the **prior** moved. A prior written directly on β would mean something different
at every decay and EC50 the sampler tries. A prior written on the channel's **contribution volume**
— how many units it delivered over the window — means the same thing on every draw, so that is what
we write, and the model works out the coefficient that achieves it.

---

## 7. Scaling, and why EC50 is not in GRPs

Before any of this, each media column is divided by **its own median active week**.

- If TV's typical active week is 130 GRPs, then 130 GRPs becomes 1.0.
- A quiet week of 65 GRPs becomes 0.5; a heavy week of 260 becomes 2.0.
- Zero stays zero. Nothing is centred, because a week with no advertising must stay a week with no
  advertising.

Everything on the media axis is then in "typical weeks", which is why:

- **EC50 of 0.6 means 0.6 of a typical active week**, or 78 GRPs here. To think in GRPs, multiply by
  the median active week; the report prints both.
- The same default range works for GRPs, impressions and clicks, which are otherwise incomparable.
- **If the scaling changes, every EC50 bound changes with it.** That is the one trap in this area,
  and it is the same trap as the KPI scaling rule from codebase 1.

The KPI is standardised per region, so coefficients are in "region standard deviations of sales",
and contributions are converted back into units sold for reporting.

---

## 8. What changes because the model learns the transforms

| | Fixed transforms (codebase 1) | Learned transforms (codebase 2) |
|---|---|---|
| Who chose the decay | an analyst, upstream | the data, inside a range you set |
| If that choice was wrong | the error is invisible: the model never sees the alternative | the posterior shows the range the data supports |
| Uncertainty in the reported contribution | coefficient uncertainty only | coefficient **and** transform uncertainty |
| Intervals | narrower, and overconfident | wider, and honest |
| What can go unidentified | collinearity between columns | that, plus decay against coefficient |
| What the data must contain | variation in the column | variation **and** weeks off air **and** range above a typical week |

The last row is the practical one. To learn a carryover the channel has to stop sometimes: if it
runs at a steady level every week, the model cannot tell a long carryover from a big coefficient. To
learn a curve it has to vary in weight: if every active week is about the same size, the model
cannot see where it bends. Both are checked before fitting (`EDA_CHECKS.md`).

---

## 9. How the transforms move the numbers you report

| If … | Then the contribution … | And the business reading is |
|---|---|---|
| the decay is **longer** | shifts later in time, and gets slightly bigger overall (less lost to saturation) | "the channel keeps working for weeks, so judge it over a quarter, not a week" |
| the decay is **shorter** | concentrates in the burst weeks | "it is a switch: what you spend is what you get, when you spend it" |
| EC50 is **lower** (saturates early) | flattens: heavy weeks add little | "we are over-buying in peak weeks; spread the money" |
| EC50 is **higher** (saturates late) | tracks the activity nearly proportionally | "we are still on the steep part; more weight would work" |
| the **window is too short** | is truncated, and the decay is pushed to the bound | not a reading at all — a setting to fix |
| a transform is **pinned** | is whatever the pin implies | say so when presenting: that part is an assumption |

The important invariance: the **total** contribution over the window is anchored by the prior and by
the data, while the transforms mostly decide **its shape in time and its marginal value**. Two
models with different decays often report similar totals and very different advice about next week's
budget.

---

## 10. Reading the transform outputs

Everything below lands in `04_transforms/` (blueprint §15).

| File or column | What it answers |
|---|---|
| `transform_parameters.csv` → `decay_median`, `half_life_periods_median` | how long does this channel keep working, with a 90% interval |
| → `hill_ec_scaled_median` and the raw-unit columns | at what weekly weight does it half-saturate, in typical weeks and in GRPs |
| → `at_lower_bound` / `at_upper_bound` | did the data choose this, or did my range? |
| → `ec_vs_p95_active` | did we ever buy heavily enough to see the curve bend? |
| `adstock_ranges.csv` → `pct_active_weeks_above_ec` | how often are we on the flat part |
| → `carryover_share_of_effect_pct` | how much of the effect lands after the week it ran |
| `adstock_decay_curves.png`, `saturation_curves.png` | the same two answers, as pictures with uncertainty bands |
| `prior_posterior_contraction.csv` → `raw_alpha_*`, `raw_ec_*` | did the data have an opinion, or is this the prior |
| `cv_transform_stability.csv` | does the answer survive cutting the data somewhere else |

A short reading rule: **decay and EC50 are only findings when their contraction is clearly above
zero and they are not sitting on a bound.** Otherwise they are assumptions, and should be presented
as such.

---

## 11. Questions people actually ask

**What is adstock, in one sentence?**
A way of saying that advertising keeps working after it runs: this week's effective media is a
weighted average of this week's and recent weeks' activity.

**Why not just shift the column by two weeks, as we sometimes do?**
A shift says the whole effect arrives on one later week. Adstock says it arrives spread over several
weeks. You can have both: `min_lag` shifts the start, the decay spreads what follows.

**Why is EC50 0.6 rather than 60?**
Because media is divided by its own typical active week first. 0.6 means "0.6 of a typical week",
which here is 78 GRPs. The report gives both.

**Can decay differ by region?**
Not in this design. Decay and EC50 are learned once per channel (or per channel family) and shared
across regions; only the coefficient varies by region. With 104 weeks there is not enough
information to learn a separate curve per region, and Meridian shares them for the same reason.

**Why can't the model learn the lag as a whole number?**
The sampler moves in continuous steps and cannot search over "1 week or 2 weeks". That is why
`min_lag` is fixed and chosen by cross-validation, while the peak lag θ — which is continuous — can
be learned.

**What if a channel is never off air?**
Then the data cannot separate its carryover from its size. Expect a decay with almost no
contraction, and pin it at a defensible value instead.

**Why did my contribution change when I changed `max_lag`?**
Because the window decides how much carryover exists at all. A longer window lets more of each burst
land later; a short window truncates it and pushes the decay against its bound.

**Is a longer decay better because it fits better?**
Not on its own. Longer decay with a smaller coefficient fits almost identically, so check
`corr(decay, β)` and the cross-validation stability before believing either version.

**What does "at bound" mean?**
The 90% interval is pressed against the edge of the range you allowed. The range is deciding the
answer, not the data. Widen it if the wider value is defensible, or say that it is a constraint.

**Do we need spend for any of this?**
No. Contributions and priors are in sales volume. Spend is used only for data-quality checks, and
as the variable itself where a channel has nothing else (OOH, trade).

**Can I just keep the numbers we use in preprocessing today?**
Yes — pin them with `fix_alpha`, `fix_ec` and `fix_slope`. That is also the bridge test: with
everything pinned, codebase 2 should reproduce codebase 1 (`METHODOLOGY.md` §8).

---

## Glossary

| Term | One line |
|---|---|
| **Adstock** | carryover: this week's effective media is a weighted average of recent weeks |
| **Decay (α)** | the share of an effect that survives into the next week |
| **Half-life** | weeks until half the effect is gone; a friendlier way to state the decay |
| **Lag window** | `min_lag` … `max_lag`: which past weeks may carry weight |
| **Peak lag (θ)** | weeks until a delayed channel's effect peaks |
| **Saturation / Hill** | diminishing returns: response from 0 to 1 |
| **EC50** | the activity level giving half the channel's maximum effect |
| **Slope** | the curve's shape; 1 = plain diminishing returns, above 1 = S-curve |
| **Typical active week** | the median of a channel's non-zero weeks; the unit media is scaled in |
| **Response** | the saturated value, 0 to 1, that the coefficient multiplies |
| **Contribution volume** | units of sales a driver delivered over the window; what our priors are written on |
| **Contraction** | how far the data moved a parameter from its prior; near 0 means the prior answered |
