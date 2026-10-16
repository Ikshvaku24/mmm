# LTE model — further changes worth considering

*16 October 2026. A shortlist for discussion; nothing here is built. This is not
a code changelog.*

`methodology.md` covers the small-to-medium improvements (A1–F3) and the
validation gates. This file adds the **structural and radical** options. For
each: what it changes, the evidence, the trade-offs, and how well it fits our
data and requirements.

---

## Verdict

**For production, keep the improved nesting method** (today's method plus
`methodology.md`). It fits our requirements best:

- long-term effects must **add to the historically reported short-term
  results**, and nesting does exactly that;
- multipliers must stay **comparable with the benchmark set** (oral care, all
  category), which was built with this method;
- 3–4 years of brand-level data is enough for it, and it runs in codebase 1
  without core changes.

Its weaknesses are structural: two stages, a trend built from sales itself,
and shares set largely by priors. The validation gates contain them; they do
not remove them.

**Next, run one radical change as a challenger: C1.** It is a one-stage model
in which long-term media builds the reported base directly.

- It removes four assumptions at once: the HP cut-off, the constant scalar,
  the retrofit, and the wave ratio rule.
- It keeps reported short-term results fixed.
- Switch only if it passes the same gates and either agrees with nesting
  within ranges, or the gap is explained.

**Add now, at low cost:** C6 (reverse causality), C9 (predictive test), and
the confounder sensitivities in C4–C5.

**Monthly BMCs:** with about 48 monthly points, neither method separates
long-term media from the base reliably.

- Anchor monthly results on portfolio priors (C7) and report wide ranges.
- Use weekly data where it exists.

---

## Summary

| # | Change | Stage | Kind | Impact | Effort | Fit to our data | Recommendation |
|---|---|---|---|---|---|---|---|
| C1 | One-stage model: long-term media builds the base | 2–8 | Radical | High | High | Good weekly; weak monthly | Challenger on FR PDX and one more weekly BMC |
| C2 | Persistence model (VARX, impulse responses) | checks | Method | Medium | Medium | Fair: weekly, few series | Triangulation only |
| C3 | Brand-health route: media → awareness / consideration → base | 3 | Data + method | High | High | Depends on trackers | Find out which BMCs have trackers |
| C4 | Category share as the target, or category as a driver | 3 | Data + method | High | Low–medium | Data gap today | Close the gap; sensitivity meanwhile |
| C5 | Distribution as a mediator of brand-building | 3 | Method | Medium | Low | Good | Report with and without distribution |
| C6 | Reverse causality: budgets that follow sales | 3 | Method | High | Low | Good | Test now; correct if it fires |
| C7 | Pooling across BMCs and across channels | 3 | Algorithm | High | Medium | Good with ≥ ~10 past studies | Collect past studies, then build |
| C8 | Learn the long-term memory (α, decay) from data | 3 | Algorithm | Medium | Medium | Fair weekly; poor monthly | Small grid, judged by C9 |
| C9 | Predictive test of long-term effects | checks | Validation | High | Low | Good on 4 years | Do now |
| C10 | Natural experiments: dark periods and bursts | checks | Validation | Medium | Low | Where they exist | Use when the data have them |

---

## The changes

### C1 — One-stage model: long-term media builds the base (radical)

**What.** One model replaces UCM extraction, the UCM model, nesting and the
retrofit. It explains the **reported baseline**, which is sales minus the
reported short-term contributions:

```
sales_t − ST_t = trend_t + Σ_i G_t,i + Σ_k γ_k·driver_t,k + ε_t   ST_t: reported short-term contributions, fixed
G_t,i  = φ_i·G_t−1,i + θ_i·h_i(a_t,i)                             brand stock built by channel i, fading at rate φ_i
trend_t = slow stochastic trend (local linear trend, or a Gaussian-process curve)
long-term contribution of channel i in week t = G_t,i
```

**Gains.**

- **One model, one posterior.** Uncertainty propagates, and there is no trend
  estimated in one step and treated as data in the next.
- **No HP filter, no λ.** How much of the base the media built is estimated.
  The prior on the trend's flexibility still matters, but it is explicit and
  testable (C9).
- **No scalar, no retrofit.** All years are fitted together, so the "no step
  change" assumption goes away.
- **Reported short-term results are untouched.** G is carved out of the
  reported positive baseline, as the UCM is today.
- **Waves are exact.** Each week's activity belongs to one year, and the stock
  is linear in it. That includes the tail after the data, so there is no ratio
  rule and no Hill-split issue (E1 in `methodology.md`).
- **No start-up fault**, because there is no extraction step.

**Trade-offs.**

- **It needs reported short-term decompositions for every modelled year**
  (two AO MMM cycles for 4 years). T1 already needs these for a backward
  retrofit. A specification change between cycles shows up as a step in the
  base and needs a level-shift term.
- **Short-term misses land in the base.** Anything the short-term models
  missed can be credited to long-term media. That risk exists today too; here
  it sits in one visible place.
- **Identification is not better by itself.** Media stock and free trend
  still trade off, and it must pass gates 3 and 9 (C9).
- **Saturation moves.** It acts on weekly activity, not on a smoothed stock.
  That is a different behavioural assumption from today's SES → Hill → adstock
  chain.
- **It is a new model type in codebase 1, not just a new run.** Medium-to-high
  build effort, and new skills for the team.
- **No comparability with past LTE studies** or the benchmark multipliers
  until the two methods are run side by side.

**Fit.** Good on weekly data with 3.5–4 years (≈ 180–210 points). Weak on
monthly data (48 points).

**Evidence.**

- Ataman, van Heerde & Mela (2010) model marketing as driving the evolution of
  base sales, in a dynamic linear model on 5 years of weekly data for 70 brands
  in France. For non-price marketing, they find long-term effects more than
  four times the short-term.
- Cain (2022, 2025) treats brand-building as advertising's effect on a
  stochastic base. He recommends testing for a permanent component first.
- PyMC-Marketing already offers a Gaussian-process time-varying intercept. Note
  that it reverts to the prior out of sample, which is fine for decomposition.

### C2 — Persistence model (VARX with impulse responses), for triangulation

- **What.** Unit-root tests, then a vector autoregression of sales and a few
  aggregated media series. The long-term effect is the cumulative impulse
  response once the "dust settles", which separates permanent from temporary
  effects.
- **Why.** It captures feedback that a one-way regression ignores: sales
  driving budgets, and competitive reactions. Hanssens' review finds truly
  permanent effects rare; most long-term effects are long-lasting temporary
  ones. That is a direct check on LTE claims.
- **Trade-offs.**
  - Many parameters, so only 2–4 series.
  - Results depend on how the shocks are identified.
  - Output is elasticities, not a decomposition that adds to 100%.
  - Harder to explain.
  - Not a production replacement.
- **Fit.** Fair on weekly brand-level data.

### C3 — Brand-health route: media → brand KPIs → base

- **What.** An intermediate layer: media moves tracker KPIs (awareness,
  consideration, preference), and the KPIs move the base. Cain (2022) uses a
  cointegrating mechanism linking mindset metrics to long-term base sales.
  Srinivasan, Vanhuele & Pauwels (2010) find mindset metrics explain about a
  third of sales variance across 60+ brands.
- **Why.** It is the only **independent** evidence of brand-building. Today
  the long-term signal is built from sales itself, which is the method's core
  weakness.
- **Trade-offs.**
  - Tracker data are often monthly or quarterly, carry sampling noise, and
    change questionnaires.
  - Availability varies by BMC.
  - A two-equation model, and a longer project.
- **Fit.** Unknown: find out which BMCs have trackers. Even without a model,
  plot the UCM trend against tracker KPIs; the team's guide already asks for
  this.

### C4 — Category share as the long-term target, or category as a driver

- **What.** Model the brand's share of its category, or add category volume
  as a driver of the base. Then market growth is not credited to the brand's
  media.
- **Why.** A trend regression credits any common upward drift to whatever else
  trends upward, media budgets included. Category is the largest omitted
  driver we know of: on the retailer panel the vendors attributed about 56% of
  sales to "Category", a variable our datacube does not have.
- **Trade-offs.**
  - Needs category data.
  - Converting share back to volume assumes the brand's media does not grow
    the category. That is fine for a small brand; not for a category leader.
- **Fit.** Blocked by data today. Meanwhile, run the long-term model with and
  without a category proxy and report the difference.

### C5 — Distribution as a mediator of brand-building

- **What.** Distribution can be an independent cause of base growth, controlled
  for as today. It can also be partly an outcome of brand-building: retailers
  list brands that shoppers ask for.
  - Now: report long-term media effects with and without distribution in the
    long-term model, as bounds.
  - Later: a two-equation path model, media → distribution → base.
- **Why.** FR PDX attributes its base growth largely to distribution gains for
  the premium sub-brand. Ataman et al. (2010) find distribution has the largest
  long-term effect in the mix: a total elasticity of 0.74, against 0.13 for
  advertising. If part of it is media-driven, controlling for distribution
  under-credits media.
- **Trade-offs.**
  - The direction is uncertain: listings also follow trade spend.
  - A path model adds assumptions.
- **Fit.** Good. The data exist, and the bounds cost one extra fit.

### C6 — Reverse causality: budgets that follow sales

- **What.** Test whether the trend **leads** media, i.e. budgets follow sales
  growth. If it does:
  - use only lagged media;
  - or a cost-based instrument, such as media price indices;
  - or C2.
- **Why.** A growing brand often gets more budget. In a trend regression that
  looks like media causing growth, and it inflates long-term effects.
- **Trade-offs.**
  - Credible instruments are scarce, and weak ones make things worse.
  - Lagged media loses efficiency.
- **Fit.** Good, and cheap. Codebase 1 already computes residual
  cross-correlations at leads and lags; the same test applies to the trend
  against media.

### C7 — Pooling across BMCs and across channels

- **What.** A hierarchical model of long-term multipliers (or LT ÷ ST ratios)
  by channel type, fitted across past LTE studies, used as each new BMC's prior.
  Within a BMC, long-term coefficients are pooled across channels in the same
  group.
- **Why.**
  - Per-BMC long-term effects are weakly identified, and today's priors are
    generic (baseline 70–80%, TV 20–25%).
  - External anchors put the multiplier near 2:
    - a meta-analysis of 56 studies puts long-term advertising elasticity at
      about twice the short-term (0.24 against 0.12);
    - UK *Profit Ability 2* finds £1.87 short-term rising to £4.11 over 24
      months (≈ 2.2×);
    - FR PDX gives 1.90.
- **Trade-offs.**
  - Needs past studies' outputs in one format.
  - Shrinkage hides genuine outliers.
  - If past studies share the method's biases, pooling inherits them.
  - Published elasticities may be inflated by publication bias: a
    bias-corrected re-analysis finds much smaller effects. External anchors
    should guide the priors, not set them.
- **Fit.** Good with about ten or more past studies. FR PDX's benchmark set
  suggests they exist.

### C8 — Learn the long-term memory from the data

- **What.** Today SES α = 0.05, and the long-term adstock and Hill, are fixed.
  Instead, choose them per channel group on a small grid, judged by C9. The
  benchmark table supplies the priors.
- **Why.** α sets how long "long-term" lasts: a half-life of about 13.5 periods
  at 0.05. One value for every channel and BMC is an assumption.
- **Trade-offs.**
  - A smooth target says little about α.
  - Decay and coefficient trade off: the Phase 2 α–β problem.
  - More runs.
- **Fit.** Fair on weekly data; poor on monthly.

### C9 — Predictive test of long-term effects

- **What.** Fit on the first years. Forecast the last year's base from earlier
  media only, and compare with a no-media forecast. If the long-term effects
  are real, they improve the forecast.
- **Why.** It is the only test of long-term claims on data the model has not
  seen. Fit statistics on a smooth target prove little.
- **Trade-offs.**
  - It holds back a year from fitting.
  - One forecast origin is noisy, so use 2–3 origins.
- **Fit.** Good on 4 weekly years; marginal on 3.

### C10 — Natural experiments

- **What.** Use periods where a channel goes dark or bursts to check the
  predicted fade of the base afterwards. FR PDX: Social at 0% in H1 2025.
- **Why.** It is the closest thing to an experiment without running one.
- **Trade-offs.**
  - Such periods are few.
  - Other things change at the same time.
- **Fit.** Where the data contain such periods.

---

## Considered and not recommended

- **Replacing the HP filter with Hamilton's regression filter.** The evidence
  is mixed: a forecast-extended HP does at least as well at the series ends.
  A3 and A6 in `methodology.md` address the real issues.
- **Re-estimating short-term effects in one 4-year model.** It is coherent,
  but it breaks the requirement that long-term effects add to the reported
  short-term results. Only if the business accepts restated short-term numbers.
- **ROI-based priors, as in Meridian.** The team's decision is a volume basis,
  and spend-based priors do not help separate long-term from short-term.
- **Instruments without a credible source of outside variation.** Worse than
  none.

---

## Sources

- Ataman, van Heerde & Mela (2010), *The long-term effect of marketing strategy
  on brand sales*, JMR 47(5) — [Tilburg](https://research.tilburguniversity.edu/en/publications/the-long-term-effect-of-marketing-strategy-on-brand-sales/),
  [MSI working paper](https://www.msi.org/working-papers/the-longterm-effect-of-marketing-strategy-on-brand-sales/)
- Cain (2022), *Modelling short- and long-term marketing effects in the
  consumer purchase journey*, IJRM 39(1) — [IDEAS](https://ideas.repec.org/a/eee/ijrema/v39y2022i1p96-116.html)
- Cain (2025), *Long-term advertising effects: the Adstock illusion*, Applied
  Marketing Analytics 11(1) — [IDEAS](https://ideas.repec.org/a/aza/ama000/y2025v11i1p23-42.html)
- Srinivasan, Vanhuele & Pauwels (2010), *Mind-set metrics in market response
  models*, JMR 47(4) — [HAL](https://hal-hec.archives-ouvertes.fr/hal-00528411)
- Sethuraman, Tellis & Briesch (2011), *How well does advertising work?
  Generalizations from meta-analysis of brand advertising elasticities*, JMR
  48(3), 457–71 — [PDF](https://www.Gwern.net/doc/economics/advertising/2011-sethuraman.pdf)
- *Meta-analysis of advertising effectiveness: new insights from improved bias
  corrections* (Erasmus University) — [record](https://pure.eur.nl/en/publications/meta-analysis-of-advertising-effectiveness-new-insights-from-impr/)
- Dekimpe & Hanssens, persistence modelling — [Marketing Science 1995](https://ideas.repec.org/a/inm/ormksc/v14y1995i1p1-21.html),
  [Hanssens, what is known about long-term effects (MASB)](https://themasb.org/wp-content/uploads/2010/10/J.-What-is-known-about-Long-Term-Hanssens-8.09F.pdf)
- Thinkbox, *Profit Ability 2* (2024) — [Thinkbox](https://www.thinkbox.tv/research/thinkbox-research/profit-ability-2-the-new-business-case-for-advertising),
  [Ebiquity](https://ebiquity.com/news-insights/press/profit-ability-2-the-new-business-case-for-advertising/)
- PyMC-Marketing, time-varying parameters — [docs](https://pymc-marketing.readthedocs.io/en/latest/notebooks/mmm/mmm_tvp_example.html)
- Hall & Thomson (2021), HP vs Hamilton at the end-points — [PDF](https://crawford.anu.edu.au/sites/default/files/2025-01/71_2020_hall_thomson.pdf)
