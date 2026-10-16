# Long-Term Effects (LTE) Modelling — Method Review and Proposal

*Current method · proposed improvements and validation · full pipeline ·
arithmetic · open questions*

16 October 2026 · **A proposal for discussion. Nothing has been changed yet.**

**Sources:**

- the Ekimetrics × Haleon *LTE Knowledge Transfer – In-Depth* deck;
- the team's LTE step-by-step methodology and training guide;
- the France Parodontax LTE study (FR PDX, Jan 2022 – Jun 2025);
- the team's UCM notebooks and outputs;
- three clarifications from the team (16 Oct 2026).

Numbers marked ✓ were recomputed from that material. The detailed
transcriptions are in `snapshots/UCM/**/understanding.md`; the implementation
plan is `plan.md`. Structural and radical options beyond this document,
including a one-stage alternative to nesting, are in `changes.md`.

---

## At a glance

**What LTE measures.**

- Short-term MMM explains how sales respond to activity within a 2-year window.
- LTE measures the slower effect of media on the brand's underlying base.
- It adds that to the short-term contribution, giving total (short + long)
  impact, long-term multipliers and ROI.

**What we keep.**

- **The 8 stages:** data stitching → UCM extraction → UCM modelling → UCM
  analysis → nested modelling → retrofit → nested model analysis → media wave
  analysis.
- **Nesting** the UCM trend into the AO MMM model with media locked.
- **The UCM-only retrofit.**
- **The wave attribution.**

**What we propose to change** — most important first:

1. **Fix a start-up fault in the UCM trend** that understates Year 1.
   - In the UCM outputs we have, the trend starts at 42% of sales (France) and
     52% (Colombia), and takes about six weeks to catch up.
   - The vendor's own example shows the same fault.
2. **Define trend smoothing in calendar time.** The current setting
   (λ = 300) removes swings shorter than about 6 months on weekly data, but
   about 26 months on monthly data.
3. **Report the multiplier on a stated basis.** FR PDX's TV multiplier is
   2.03 on one basis and about 2.4 on the other ✓.
4. **Split media waves at the stock, not at the saturated response.**
   - The current split under-credits spill-over into later years when the
     saturation curve is S-shaped.
   - Example: a carried-over stock holding 14% of the total gets 6% of the
     credit at S = 2 ✓.
5. **Show which numbers come from the data and which come from priors.**
   - The UCM's share of sales (20.3% in FR PDX) scales every long-term number.
   - That share, and the long-term shares, are set largely by priors.
   - We test them and label them.

**New: a validation layer.** The current method has no formal checks between
stages. We add one gate per stage, each with a pass mark agreed in advance:

- first, reproduce today's numbers exactly;
- check the UCM trend;
- placebo-test the long-term model;
- check that the nested model reproduces the AO MMM model;
- test the retrofit assumption in both directions;
- check that the waves add up;
- reconcile exactly;
- report sensitivity ranges.

**To decide** (section 7):

- pre-period handling;
- the smoothing cut-off;
- which AO MMM model to nest into (latest or earlier 2 years);
- brand level vs pools;
- the multiplier basis;
- the wave-split convention.

---

## 1. The current methodology

### 1.1 The idea

- **Sales move for two reasons:** short-term activation (weeks) and a slowly
  moving base (years). A 2-year MMM measures the first and puts the second in
  its baseline.
- **LTE works in three steps:**
  - it extracts the slow base of sales as a statistical trend, the **UCM
    trend**;
  - it explains that trend with long-memory media, the **UCM model**;
  - it measures how much of sales the trend accounts for, by adding it to the
    2-year MMM as one more variable — **nesting**.
- **The long-term contribution of a medium** = its share of the trend × the
  trend's share of sales.
- **Reporting:** Years 1–2 of the history, with the long-term contribution
  added to the short-term contribution already reported for those years.

### 1.2 The eight stages

| # | Stage | What is done | Key settings | Output |
|---|---|---|---|---|
| 1 | **Data stitching** | Historical data appended to the latest model database. All pools (sub-brand, store format, region) aggregated: "the UCM modelling is done at the Brand level" | At least **4 years** of sales, media and BTL (the team guide says 3.5–4); no step changes between data sources | Brand-level weekly panel |
| 2 | **UCM extraction** | Sales split into level, trend (growth), cycle and noise with a statistical state-space model: an unobserved-components model (UCM) with smooth trend, cycle and irregular, Powell optimiser. Level + growth then smoothed again with a Hodrick–Prescott (HP) filter | HP **λ = 300**; a seasonal component only if the ACF shows one; a missing pre-period year filled with the first year's average, against "steep growth from 0" | The **UCM trend** |
| 3 | **UCM modelling** | Bayesian regression of the UCM trend on long-term media (hero channels only), expert, and baseline drivers (market, distribution, price, competitor media). Also a linear trendline and a pre-period "media residual". **No trade or promotion** | Media pre-treated: **SES (α = 0.05) → Hill (K 0.3–0.9, S 1.1–3.0) → adstock (40–95% weekly)**, lag 0–2. Priors on contribution shares: baseline 70–80%, TV 20–25%, offline 2–5%, digital 2–5%, expert 0.1–0.5% | Each lever's contribution to the trend |
| 4 | **UCM analysis** | Each lever's share of the trend over the period, as a ratio of sums | — | Shares of the UCM trend |
| 5 | **Nested modelling** | The UCM trend added as one variable to a 2-year AO MMM model. Media, expert and negative-baseline variables **fixed**; positive-baseline variables re-estimated; the UCM prior iterated | The AO MMM model's 2 years — **latest or earlier** | The UCM's share of sales in that window |
| 6 | **Retrofit** | Scalar = UCM contribution ÷ trendline (e.g. 19%). The other years' UCM contribution = scalar × their trendline, **taken proportionally out of their positive baseline factors**. **Only the UCM is retrofitted**; every other variable keeps its reported short-term contribution | **Backward** (latest → earlier) or **forward** (earlier → latest). Assumes no step change at the boundary | UCM contribution over all years |
| 7 | **Nested model analysis** | Each week: a lever's long-term contribution = its share of that week's trend × the UCM's contribution that week | — | Weekly long-term contributions |
| 8 | **Media wave analysis** | Each channel's raw support split by **calendar year**, and each year's variable put through the same transformations, so it carries into later years. Each week's long-term contribution is then split in the ratio of these variables and summed per year of activity — the **"indexed"** long-term contribution | Calendar years ("media is planned on a calendar year basis"); Years 1–2 reported | Long-term contribution by year of activity; wave tables; multipliers; ROI |

### 1.3 How the results fit together — FR PDX ✓

| Step | Result |
|---|---|
| Short-term model alone | baseline 95.8% of sales; total media (TV + digital) 3.4% |
| Nested model | the UCM takes **20.3%** of sales, all of it from the baseline (75.5% + 20.3% = 95.8%); media unchanged |
| UCM model (shares of the trend, 2022–25) | TV 10.0%, digital 4.8%, expert 0.6%, baseline 84.7% |
| Final: TV | 2.1% + 10.0% × 20.3% = **4.1%** (reported 4.2%) |
| Final: digital | 1.3% + 4.8% × 20.3% = **2.3%** |
| Final: total media | 3.4% → **6.4%**; multiplier **1.90** |

### 1.4 The team's clarifications (16 Oct 2026), built in

| # | Clarification |
|---|---|
| T1 | In the retrofit, **only the UCM is retrofitted**. No other variable is: every other variable keeps the short-term contribution reported by the AO MMM model that covered those years |
| T2 | The retrofit can run **either way**: the latest 2 years retrofitted to the earlier 2, or the earlier 2 to the latest 2. FR PDX's primary model ended 2023 Q4, so it most likely went forward |
| T3 | Media waves: support split by year, the adstock applied to each year's variable so it flows into the next year, and each week's contribution divided in their ratio |

---

## 2. Proposed methodology

### 2.1 Principles

- **Keep** the structure and vocabulary of the current method. Nothing here
  changes *what* LTE reports.
- **First reproduce today's numbers exactly.** Then change one thing at a
  time, showing each change's effect on one BMC side by side.
- **Fix** what produces wrong numbers (*essential*). **Improve** what makes
  numbers fragile (*recommended*). Leave the rest *optional*.
- **Make every assumption-driven number visible**, with a range.

### 2.2 Improvements, stage by stage

#### A. UCM extraction

| # | Today | Issue (evidence) | Proposal | Benefit | Cost / trade-off | Priority |
|---|---|---|---|---|---|---|
| A1 | Sales fitted in raw units with the library's default start-up setting | **The trend starts far below sales:** 42% of sales in week 1 for France, about six weeks to reach 90%; 52% for Colombia; the vendor's own example jumps from about 20K to 90K. The fault falls **inside reported Year 1**: about 5% of France's Year-1 trend, concentrated in Q1. The cause: a default start-up variance (10⁶) that is tiny next to sales of 10⁵–10⁷ | Scale sales before fitting (÷ mean); use exact ("diffuse") start-up initialisation; keep the pre-period fill as a safeguard | A correct trend from the first week; Year 1 no longer understated | Low effort. Numbers move against past studies — shown side by side once. (FR PDX's own trend chart does not show the fault, so that study may have corrected it by hand.) | **Essential** |
| A2 | No dates given to the model; the cycle always included | The "cycle" is not a business cycle. In the brand-level series it is a few thousandths of a percent of sales (France; Colombia's total), and its length is unconstrained — about 3 weeks in the France output | Give the model dates (so cycles are bounded to 1.5–12 years); keep the cycle only if it is material (> 0.5% of the level) or improves the fit; report it | Interpretable components; a cleaner trend | Low. A 4-year series cannot identify a business cycle anyway, so the cycle will usually drop out | **Essential** |
| A3 | HP λ = 300 for every cadence | λ = 300 removes swings shorter than **≈ 6 months on weekly data** but **≈ 26 months on monthly data** ✓. "Long-term" then means different things by BMC. Keeping the 6-month meaning on monthly data needs λ ≈ 1 ✓ | Agree the cut-off **in months**; derive λ for each cadence; report the sensitivity at ×½ and ×2 | Same meaning across BMCs; documented | A decision. Long-term shares move with the cut-off — shown as a range | **Essential** (decision) |
| A4 | The method says to test for seasonality; the notebooks never do | Seasonality left in the trend is "mid-term fluctuation" that the HP filter then has to remove | Test automatically (ACF, fit comparison); include a seasonal component when it improves the fit; report the decision | A cleaner trend; less reliance on the HP filter | Weekly seasonality on 4 years is weakly identified — keep it simple (few harmonics) | Recommended |
| A5 | Never tested | Reading long-term effects off a trend assumes sales have a **persistent** component. If not, the "trend" is smoothed noise | Test it (trend variance > 0; compare with a straight line; standard unit-root tests); flag BMCs that fail | Stops long-term claims on series that cannot support them | Low | Recommended |
| A6 | HP on the full sample | The filter is least reliable at the **end** of the data — where the latest nested window sits. Each refresh revises last year's trend | Extend the series by a short forecast before smoothing and trim after; report how much the trend moved at each refresh | A more stable UCM in the latest window; revisions visible | Moderate | Recommended |
| A7 | With short history, 6 months back-cast with a time-reversed seasonal forecast | Sales can be back-cast; **media cannot**. If back-cast months enter a model, media effects are biased towards zero | Use back-cast months **only to warm up the trend**; drop them before any model | No invented data in any regression | None | **Essential** (rule) |

#### B. UCM modelling

| # | Today | Issue (evidence) | Proposal | Benefit | Cost / trade-off | Priority |
|---|---|---|---|---|---|---|
| B1 | Fit statistics reported (FR PDX: R² 98.5%, MAPE 1.0%) | One smooth series regressed on other smooth series always fits well, so fit does not validate the shares. The share priors (baseline 70–80%, TV 20–25%) do much of the work | For every coefficient, report whether the data moved it away from its prior. Run a **placebo** (media shifted in time) and a **baseline-only** model. Label shares the data did not move | Readers know which long-term shares are evidence and which are assumptions | Two extra small fits | **Essential** |
| B2 | Trend taken from total sales | Long flights or promotions can leak into the trend and be credited to long-term media — **double counting** | Where a full-period short-term decomposition exists, also extract the trend from sales minus short-term media and trade, as a sensitivity | Less double counting | Needs that decomposition; differs from the vendor's trend | Optional |

#### C. Nested modelling

| # | Today | Issue (evidence) | Proposal | Benefit | Cost / trade-off | Priority |
|---|---|---|---|---|---|---|
| C1 | UCM prior iterated "after reviewing the results" | The UCM is a smoothed piece of sales itself, so it can absorb almost anything. Its share (FR PDX 20.3%) is driven by its prior and by which variables may give up volume. Repeated prior corrections do not converge — we saw this on a previous project | Keep the locks (media, expert, negative baseline). Allow **one** prior correction at most. Re-run with the UCM prior at ×0.5 / ×1 / ×1.5 and report the range. Audit where the UCM's volume came from (positive baseline only). Label the UCM share "assumption-driven" when the data does not move it | The one number that scales every long-term result becomes transparent and bounded | 2–3 extra nested fits | **Essential** |

#### D. Retrofit

| # | Today | Issue (evidence) | Proposal | Benefit | Cost / trade-off | Priority |
|---|---|---|---|---|---|---|
| D1 | The scalar from one window applied to the other ("no step change") | The key assumption of the retrofit is never checked. The retrofitted UCM sits in a decomposition that never contained it | Flag any week where the UCM would exceed the positive baseline, and any jump at the boundary. **Where AO MMM models cover both 2-year windows, nest into both and compare the two scalars** — a direct test, possible because the retrofit can run either way | The assumption is tested, not assumed | One extra nested fit where two models exist | Recommended |

#### E. Media waves

| # | Today | Issue (evidence) | Proposal | Benefit | Cost / trade-off | Priority |
|---|---|---|---|---|---|---|
| E1 | Each year's support goes through the **whole** chain, saturation (Hill) included; the week is split in the ratio of the results | Hill is nonlinear, so the split depends on the curve's **shape**: a carried-over stock holding 14% of the total gets 23% of the credit at S = 1, 6% at S = 2, 1% at S = 3 ✓. The method expects S > 1 for long-term media, so **spill-over — what waves measure — is under-credited**. The deck's worked example has no Hill, so it does not show this | Split the linear steps (SES, adstock, lag) exactly by year. At the Hill step, give each year its **share of the stock** | Exact, adds up, independent of the curve; **identical to today when there is no Hill** | Needs each year's pre-Hill stock from the transformation step. Spill-over shares rise for S-shaped channels — run side by side first | Recommended (decision) |
| E2 | Years 1–2 reported; the effect beyond the data ignored | Part of a wave's effect lies after the last date: the method estimates 13% / 47% for Years 3 / 4. With 3–3.5 years, even Year 2 is truncated — silently | Project each wave's remaining effect with no further activity; report "% unobserved" | An honest Year-2 wave | Assumes the scalar and coefficients hold after the data | Optional |

#### F. Reporting

| # | Today | Issue (evidence) | Proposal | Benefit | Cost / trade-off | Priority |
|---|---|---|---|---|---|---|
| F1 | One multiplier, basis unstated | The method says long-term contributions are **"indexed"** — re-booked to the year of the activity. FR PDX's waves are indexed (TV ≈ 2.4 ✓). Its ROI slide is not: TV 2.03, matching long-term effect *realised within* 2022–23 (≈ 2.1 ✓) | Report both, labelled. **Indexed** = what the year's money bought (the investment view). **Non-indexed** = how much of the year's sales is long-term (the calendar view) | No ambiguity in the headline multiplier | None | **Essential** |
| F2 | Point estimates only | No sense of robustness | Show the range from the sensitivity runs (cut-off ×½ / ×2, seasonal on / off, UCM prior ×0.5–1.5) beside every headline number | Decision-makers see how firm each number is | Compute only | Recommended |
| F3 | Method: brand level. One notebook runs it per pool | Pool-level trends do not add up to the brand's (4% gap ✓) | Brand level by default. In a pooled nested model, enter the brand trend in every pool and sum the results | Adds up by construction | None | Decision |

### 2.3 Validation strategy (new)

The current method has no formal validation between stages. We propose a
**gate at every stage**:

- a check, with a pass mark agreed in advance;
- a defined action if it fails.

The gates are reported in every study.

| Gate | Check | Pass mark (proposed) | If it fails |
|---|---|---|---|
| **0. Reproduce today** | Re-run the current method on FR PDX and match its published outputs | UCM trend to 6 decimals; final shares ±0.1 pp; wave and multiplier tables exact | Change nothing until it passes |
| **1. Data** | Continuity; step changes between stitched sources; coverage of the main drivers; ≥ 4 years | No unexplained break; a level shift at a source boundary < 15% | Fix upstream; document restatements (as FR PDX did) |
| **2. UCM extraction** | Optimiser converged and stable from several starting points; residual tests (autocorrelation, normality, constant variance); start-up check; permanent-component test; comparison with an alternative trend model | First-week trend within ±5% of the local sales level; tests pass or are flagged | Refit; flag; no long-term claims on a non-persistent trend |
| **3. UCM model** | Sampler convergence; prior vs posterior for every coefficient; **placebo** (media shifted ±26 weeks); baseline-only comparison; residual autocorrelation | Real media beat ≥ 95% of placebo fits; coefficients the data did not move are labelled | Re-specify with fewer channels; label as assumption |
| **4. Nested model** | Without the UCM, it reproduces the AO MMM model; donor audit; UCM prior sweep; the scalar is constant | Each locked variable within ±3% of the AO MMM model; the UCM's volume comes only from the positive baseline; the UCM-share range is reported | Fix the locked priors; restrict the donors |
| **5. Retrofit** | UCM ≤ positive baseline in every week; continuity at the boundary; **two-way scalar comparison** where possible | No flagged week; the two scalars within an agreed band (e.g. ±20%) | Investigate the step change (restatement, distribution change); report the gap |
| **6. Waves** | The wave variables add up to the total; each wave sums to 100%; the unobserved tail | Additivity within ±1% (or the stock split used); 100% exactly | Use the stock split; report the tail |
| **7. Reconciliation** | Final decomposition = actual sales every week; shares sum to 100% | Exact | Stop — an arithmetic error |
| **8. Robustness** | Sensitivity grid; refresh stability (drop or add one quarter) | Year-1 long-term share moves less than an agreed band (e.g. ±1 pp) | Report the range; widen the caveat |
| **9. External** | Multipliers vs benchmarks (oral care / all category, FR PDX S14); the trend vs brand-health indicators | Within benchmark ranges, or explained | Explain, or revisit the specification |

**Before any client study — a simulation test.** Simulate a brand with a
**known** long-term media effect, run the whole pipeline, and check it is
recovered within a stated band. This tells us the method's limits before a
client number depends on them.

---

## 3. Current vs proposed — comparison

| Stage | Current | Proposed | Why | Expected effect on reported numbers |
|---|---|---|---|---|
| 1 Data stitching | ≥ 3.5–4 years; brand level; back-cast or year-average fill | ≥ 4 years (3–3.5 flagged); brand level; back-cast only as warm-up; step-change check | No invented data in models | None on clean data |
| 2 UCM extraction | Raw-unit fit with default start-up; no dates; cycle always on; λ = 300 for any cadence | Scaled fit with exact start-up; dated; cycle only if material; seasonal tested; cut-off in months; end-of-sample handling; diagnostics | Removes the Year-1 fault; same meaning across BMCs | Year-1 UCM and long-term contributions **rise** (France: the trend's first quarter was understated); the rest depends on the agreed cut-off |
| 3 UCM modelling | Share priors; fit statistics | Same, plus prior-vs-data evidence, placebo, baseline-only, labels | Evidence and assumption separated | None — labels only |
| 4 UCM analysis | Ratio of sums | Same | — | None |
| 5 Nested modelling | Locks; UCM prior iterated | Same locks; one correction at most; prior sweep; donor audit; reproduction of the AO MMM model | A bounded, transparent UCM share | The UCM share is reported **with a range** |
| 6 Retrofit | UCM only; either direction; scalar × trendline from the positive baseline | Same, plus boundary flags and a two-way scalar test | The key assumption is tested | None unless a check fails |
| 7 Nested model analysis | Weekly share × UCM | Same | — | None |
| 8 Media waves | Each year's variable through the full chain; weekly ratio | Split at the stock; tail projected; additivity reported | Removes a curve-shape bias | Spill-over into later years **rises** for S-shaped channels |
| 9 Reporting | One multiplier, basis unstated; point estimates | Indexed **and** non-indexed; ranges | No ambiguity | e.g. FR PDX TV: 2.03 (non-indexed) and ≈ 2.4 (indexed), both shown |

---

## 4. The complete modelling pipeline (with the improvements)

```
 Model database (≥ 4 yrs, raw)
        │
 [0] Feasibility + window plan ── nested window N = the AO MMM model's 2 years (latest OR earlier);
        │                          retrofit window R = the rest; reported = first 2 calendar years
 [1] Data stitching ──────────── brand-level sales; step-change checks                    ◄ gate 1
        │
 [2] UCM extraction ──────────── (warm-up back-cast) → scaled fit, exact start, dated
        │                          → seasonal test → cycle only if material
        │                          → Level + growth → smoothing at the agreed cut-off
        │                          → end-of-sample handling → UCM trend τ                ◄ gate 2
        ├───────────────────────────────────────┐
 [3] UCM modelling (long-term model)       [5] Nested modelling (AO MMM model + UCM)
     τ ~ long-term media + expert              Stage A: reproduce the AO MMM model    ◄ gate 4
       + drivers + linear trend                Stage B: add the UCM; lock media,
       + pre-period media residual                      expert, negative baseline;
     priors: contribution shares                        positive baseline donates
     placebo + baseline-only     ◄ gate 3              prior sweep; ≤ 1 correction
        │                                              → UCM contribution U, scalar κ = U ÷ τ
 [4] UCM analysis: share of the trend          │
        │                                      │
        └──────────────────┬───────────────────┘
 [6] Retrofit ─────────────────── U = κ × τ in the other years (backward or forward);
        │                          carved proportionally from the reported positive baseline;
        │                          boundary flags; two-way κ where possible              ◄ gate 5
 [7] Nested model analysis ────── weekly LT = κ × (contribution to the trend);
        │                          final = ST + LT; exact reconciliation                 ◄ gate 7
 [8] Media wave analysis ──────── per-year variables → share of the stock each week →
        │                          indexed LT per activity year; tail; additivity        ◄ gate 6
 [9] Reporting ───────────────── ST, LT, total by lever and year; multipliers indexed +
                                   non-indexed; ROI (optional); ranges; labels; gates    ◄ gates 8–9
```

**Which years are which:**

```
                 Year 1          Year 2          Year 3          Year 4
 Backward:   [  retrofit ◄──────────────── ] [  nested (AO MMM model)  ]
 Forward:    [  nested (AO MMM model)  ] [ ────────────────► retrofit  ]
 Reported:   [  Year 1   ] [  Year 2   ]   controlled for, not reported
```

| Stage | Inputs | What happens | Outputs | Gate |
|---|---|---|---|---|
| 0 Feasibility and windows | Model database; the AO MMM model to nest into | Check ≥ 4 years (3–3.5 flagged; < 3 refused); set N, R and the reported years | Window plan | — |
| 1 Data stitching | Historical + latest data | Append; aggregate pools to the brand; check step changes | Brand-level panel | 1 |
| 2 UCM extraction | Brand sales (+ optional warm-up back-cast) | The improved extraction (A1–A7) | UCM trend τ; diagnostics | 2 |
| 3 UCM modelling | τ; long-term media (SES → Hill → adstock, from the transformation step); expert; drivers; linear trend; media residual | Bayesian regression with share priors; placebo; baseline-only | Contribution of each lever to τ | 3 |
| 4 UCM analysis | Stage 3 | Shares of τ (ratio of sums), by period | Share table | — |
| 5 Nested modelling | AO MMM model (its data, coefficients); τ | Stage A reproduces it; Stage B adds the UCM with locks and donors; prior sweep; at most one correction | UCM contribution in N; scalar κ; donor audit | 4 |
| 6 Retrofit | κ; τ in R; the reported short-term decomposition of R | U = κ·τ; positive baseline reduced proportionally; checks | UCM over all years | 5 |
| 7 Nested model analysis | Stages 3–6 | L = κ × contribution to τ, weekly; final = ST + LT | Long-term and total contributions; reconciliation | 7 |
| 8 Media wave analysis | L; per-year wave variables (and their pre-Hill stock) | Stock split; indexed LT; tail | Wave tables; indexed LT | 6 |
| 9 Reporting | All of the above; optional spend and price | Tables by lever and year; multipliers on both bases; ROI (optional); sensitivity ranges; labels | The LTE report | 8, 9 |

---

## 5. Contribution calculations and reconciliation

### 5.1 The chain

1. **UCM model** → each lever's contribution to the trend each week (c), and
   the trend itself (τ).
2. **Share of the trend:** p = c ÷ τ each week. Over a period, Σc ÷ Στ — a
   ratio of sums, not an average of weekly shares.
3. **Nested model** → the UCM's contribution to sales in the nested window, U.
4. **Scalar:** κ = U ÷ τ. It is constant over the nested window (FR PDX: the
   UCM is 20.3% of sales; the deck's example scalar is 19% of the trendline).
5. **Retrofit:** U = κ × τ in every week of the other years.
   - The positive baseline factors of the reported decomposition are reduced
     proportionally, so the reported total is unchanged.
   - Nothing else is touched.
6. **Long-term contribution** of lever i: L = p × U = κ × c — every week, in
   both windows.
7. **Final decomposition:**
   - lever i = its short-term contribution + L;
   - baseline = the short-term baseline after the UCM's carve-out, plus the UCM
     model's baseline share of U;
   - the UCM row disappears;
   - the total = actual sales.
8. **Waves:** each week's L is split by year of activity. Summed per year, it
   gives the **indexed** long-term contribution.
9. **Multipliers:** (ST + LT) ÷ ST, on both bases. **ROI** = value ÷ spend
   (optional; needs a price).

### 5.2 Worked example — FR PDX total decomposition ✓

| Lever | Short-term (nested) | + share of trend × UCM share | = computed | Reported |
|---|---|---|---|---|
| Baseline | 75.5% | 84.7% × 20.3% = 17.2% | 92.7% | 92.7% |
| TV | 2.1% | 10.0% × 20.3% = 2.0% | 4.1% | 4.2% |
| Expert | 0.6% | 0.6% × 20.3% = 0.1% | 0.7% | 0.7% |
| Digital media | 1.3% | 4.8% × 20.3% = 1.0% | 2.3% | 2.3% |
| Trade | 0.1% | — | 0.1% | 0.1% |
| **Total media** (TV + digital) | **3.4%** | 14.8% × 20.3% = 3.0% | **6.4%** | 6.4–6.5% |

### 5.3 Worked example — one week (the methodology deck) ✓

UCM model, week of 6 Jan 2020: TV contributes 2,193 to a trend of 253,063. The
nested model's UCM contribution that week is 51,363.

> TV long-term = 2,193 ÷ 253,063 × 51,363 = **445.10** units

### 5.4 Worked example — media waves (the Ekimetrics deck) ✓

| Year | Week's long-term contribution | Wave variables (W1, W2, W3, W4) | Shares | Split |
|---|---|---|---|---|
| 1 | 100 | 10, 0, 0, 0 | 100%, 0, 0, 0 | 100, 0, 0, 0 |
| 2 | 150 | 2.5, 15, 0, 0 | 14%, 86%, 0, 0 | 21, 129, 0, 0 |
| 3 | 200 | 0.5, 4.5, 20, 0 | 2%, 18%, 80%, 0 | 4, 36, 160, 0 |
| 4 | 250 | 0, 1, 6.5, 25 | 0, 3%, 20%, 77% | 0, 8, 50, 192 |
| **Total** | **700** | | | **125, 172, 210, 192** |

- Wave 1's indexed long-term contribution is 125: realised as 100 in year 1,
  21 in year 2 and 4 in year 3.
- Exact figures: 125.4 / 172.3 / 210.0 / 192.3, summing to 700.

### 5.5 Worked example — multipliers ✓

- **FR PDX overall** (effect-weighted) = Σ total effect ÷ Σ short-term effect
  = 7.237M ÷ 3.811M = **1.90**.
- **FR PDX TV:**

| Basis | TV multiplier |
|---|---|
| Non-indexed (long-term realised within 2022–23) | **2.03** — reported |
| Indexed (all long-term effect of the 2022 activity) | 100 ÷ 41 = **2.44** |
| Indexed (all long-term effect of the 2023 activity) | 100 ÷ 42 = **2.38** |

### 5.6 Reconciliation checks — all exact

1. The nested model's components add to fitted sales; with the residual, to
   actual sales.
2. The scalar κ is the same in every week of the nested window.
3. The UCM model's contributions add to the trend.
4. The long-term contributions across levers, plus the long-term baseline,
   equal the UCM contribution — every week.
5. In retrofitted years, the reported total is unchanged. Only the positive
   baseline is reduced, by exactly the UCM's contribution.
6. The final decomposition equals actual sales in every week.
7. The wave splits add up to each week's long-term contribution. Each wave's
   table sums to 100%.
8. Percentages sum to 100% in every period.

**Two arithmetics, kept apart.**

- Weekly tables are sums of weekly central estimates: they add up exactly.
- Uncertainty intervals come from the full posterior and are reported
  separately. They do not add, by design.

---

## 6. Mathematical definitions

**Notation** — t = week; i = a media or expert lever; g = a pool of the AO MMM
model; N = the nested window; R = the retrofit window; H = N ∪ R.

| Symbol | Meaning |
|---|---|
| S_t | Observed sales |
| τ_t | UCM trend (brand level) |
| c_{t,i} | Contribution of lever i to the trend (UCM model) |
| C_{t,i} | Short-term contribution of i to sales (nested model in N; reported in R) |
| U_t | The UCM's contribution to sales |
| κ | The UCM scalar |
| L_{t,i} | Long-term contribution of i to sales |
| a_{t,i} | Raw support of channel i |

**UCM extraction — smooth trend.**

```
S_t = μ_t + [γ_t] + ψ_t + ε_t          level μ, seasonal γ (if tested in), cycle ψ, noise ε
μ_{t+1} = μ_t + β_t                     the level has no shock of its own
β_{t+1} = β_t + ζ_t                     the growth (slope) drifts slowly
τ⁰_t = μ̂_t + β̂_t                        "Level + Trend"  (= next week's level)
```

**HP smoothing, and the cut-off.**

```
τ = argmin Σ (τ⁰_t − τ_t)² + λ Σ (τ_{t+1} − 2τ_t + τ_{t−1})²
half-gain period  P(λ) = 2π / arccos(1 − 1 / (2√λ))      P(300) = 26.1 periods  ✓
same cut-off on another cadence:  λ_monthly = λ_weekly × (12/52)⁴
```

**Long-term media pre-treatment** (supplied by the transformation step).

```
SES       e_t = α·a_t + (1 − α)·e_{t−1}          α = 0.05; half-life ≈ 13.5 periods  ✓
Hill      h = e^S / (e^S + K^S)                   K = half-saturation, S = steepness
Adstock   m_t = h_t + λ_a·m_{t−1}                 half-life = ln 0.5 / ln λ_a
order     SES → Hill → adstock (deck) — to be confirmed (Q5)
```

**UCM model and UCM analysis.**

```
τ_t = intercept + slope·t + Σ_i b_i·m_{t,i} + drivers + media residual + e_t
c_{t,i} = b_i·m_{t,i}
share of the trend over a period P:   Σ_{t∈P} c_{t,i} ÷ Σ_{t∈P} τ_t
```

**Nested model and the scalar.**

```
S_t = Σ_j β_j·x_{t,j} + β_u·τ_t + e_t                  (media, expert, negative baseline fixed)
U_t = β_u·τ_t ;   κ = U_t ÷ τ_t = Σ_N U ÷ Σ_N τ       constant over N
pooled AO MMM model:  the brand τ in every pool;  κ = Σ_g κ_g
```

**Retrofit — UCM only, either direction.**

```
U_t = κ·τ_t                                           every week of R (before or after N)
C'_{t,j} = C_{t,j}·(1 − U_t ÷ P⁺_t)                    j ∈ positive baseline;  P⁺_t = Σ_{j∈B⁺} C_{t,j}
every other reported C_{t,j} unchanged
flag U_t > P⁺_t, and any jump in U ÷ P⁺ at the N–R boundary
```

**Long-term and final contributions.**

```
L_{t,i} = (c_{t,i} ÷ τ_t)·U_t = κ·c_{t,i}             every week of H
Final_{t,i} = C_{t,i} + L_{t,i}
Final_{t,Baseline} = Σ C'_{t,baseline} + (U_t − Σ_i L_{t,i}) + residual
Σ_levers Final_t = S_t
over a period:  LT%_i = share of the trend_i × UCM%       (the FR PDX identity ✓)
```

**Media waves.**

```
wave variable (today):   w^{(y)}_t = T(a·1[t ∈ year y])_t     T = the whole chain
share:                   s_{y,t} = w^{(y)}_t ÷ Σ_k w^{(k)}_t
split:                   L_{y,t} = s_{y,t}·L_t
indexed LT of year y:    Σ_t L_{y,t}        realised in year Y:  Σ_{t∈Y} L_{y,t}
proposed (stock split):  linear steps split exactly by year; at the Hill step
                         h^{(y)}_t = h_t·e^{(y)}_t ÷ e_t  (e = the stock entering Hill);
                         identical to today when the chain has no Hill
```

**Multipliers and ROI.**

```
non-indexed (window W):  (Σ_W C_i + Σ_W L_i) ÷ Σ_W C_i
indexed (activity year): (ST_{i,y} + indexed LT_{i,y}) ÷ ST_{i,y}
overall:                 Σ_i total ÷ Σ_i short-term        (effect-weighted)
ROI:                     value ÷ spend,   value = volume × price   (optional)
```

---

## 7. Risks and unresolved questions

### 7.1 Methodological risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| The UCM's share of sales is prior-driven | It scales **every** long-term number | Locks; one correction at most; prior sweep; labelled; range reported |
| Long-term shares are prior-driven | A smooth target carries little information | Prior-vs-data evidence per coefficient; placebo; baseline-only comparison |
| The UCM is built from sales itself | It can absorb almost anything in the nested model | Only the positive baseline may donate; donor audit |
| Start-up fault in Year 1 | Understates the reported year | Fix at source (A1); side by side once |
| Short-term effects counted as long-term | Long flights leak into the trend | Base-sales sensitivity (B2); the cut-off choice |
| The retrofit assumes no step change | Distribution or data-source shifts break it | Boundary flags; two-way scalar test (D1) |
| Restated data | Scale shifts between sources | Fix upstream; step-change gate (FR PDX had this) |
| Wave split depends on the Hill curve | Spill-over under-credited for S-shaped channels | Stock split (E1); additivity reported |
| Two multipliers for one channel | ≈ 2.0 vs ≈ 2.4 for FR PDX TV | Both shown, labelled (F1) |
| Truncated waves | Year 2 understated with < 4 years | Tail projection, labelled (E2) |
| The trend is an estimate, not data | Intervals look tighter than they are | Sensitivity ranges (F2) |

### 7.2 Decisions needed

| # | Question | Options | Recommendation | When |
|---|---|---|---|---|
| Q1 | Pre-period handling | Fill a year with the first year's average (deck); back-cast 6 months (team sheet); none, with the A1 fix | A1 fix; back-cast optional, **warm-up only**; never modelled | Before build |
| Q2 | The smoothing cut-off | λ = 300 for every cadence; one calendar cut-off | Agree the cut-off in months (today's weekly practice ≈ 6 months); derive λ; report ×½ / ×2 | Before build |
| Q3 | Which AO MMM model to nest into | The latest 2 years (retrofit backward); the earlier 2 years (retrofit forward) | Whichever model is used; where models cover both windows, nest into both and compare | Before build |
| Q4 | Brand level or pools | Brand level (method); per pool (one notebook) | Brand level; the brand trend in every pool of the nested model | Before build |
| Q5 | Order of the long-term transformations, and the basis of K | SES → Hill → adstock; SES → adstock → Hill | Confirm with the owner of the transformation step. It matters for the waves | Before build |
| Q6 | Multiplier and ROI basis | Indexed (method's stated basis); non-indexed (FR PDX ROI slide) | Report both, labelled; choose the headline | Before reporting |
| Q7 | Wave split when Hill is in the chain | Today's per-year response; share of the stock | Share of the stock, after one side-by-side BMC | Before reporting |
| Q8 | Sales series for the UCM; restatements | Total sales; base sales | Total sales, with base sales as a sensitivity; restatements fixed upstream and documented | Before build |
| Q9 | Minimum history | 4 years (Ekimetrics); 3.5 (FR PDX); 3 | 4 by default; 3–3.5 allowed with flags; < 3 refused | Before build |
| Q10 | ROI in value | Volume only (our models today); value ROI (FR PDX) | Multipliers always (they need no price); ROI when spend and a price are supplied | Before reporting |
| Q11 | The pre-period "media residual" | A decaying stock from the window start; a first-pass residual; supplied | A generated decaying stock — never the model's own residual (circular) | Before build |
| Q12 | Default UCM settings | Reproduce today's exactly; the improved extraction | Improved, after one side-by-side BMC; the old settings kept for comparison | Before reporting |

### 7.3 Data we need

1. **Per-year media variables** for each long-term channel, from the
   transformation step — plus each year's pre-saturation stock, for the stock
   split. Otherwise the raw support and the long-term parameters.
2. **The reported short-term decomposition** of the retrofit years, from the
   AO MMM model that covered them.
3. **The AO MMM model** covering the nested window — ideally models for both
   2-year windows (the two-way test).
4. **The original inputs** of the UCM notebooks (France 182 weeks; Colombia 54
   months × 13 series), to reproduce today's numbers exactly.
5. **Spend and a price**, only if ROI is wanted. Benchmark multipliers, if
   they are to be shown.

---

## Appendix — glossary (LTE term → MMM meaning)

| Term | Meaning |
|---|---|
| UCM trend | The slow base of sales: an unobserved-components model's level + growth, smoothed |
| HP filter, λ | A smoother; λ sets the shortest swing it keeps |
| SES (α) | Single exponential smoothing — a long-memory adstock whose weights sum to 1 |
| Hill (K, S) | Saturation curve: K = half-saturation point, S = steepness (S > 1 = S-shaped) |
| UCM model | The long-term model: the UCM trend regressed on long-term media |
| AO MMM model | The team's 2-year short-term sales MMM |
| Nesting | Adding the UCM trend to the AO MMM model as one more (baseline) variable |
| Positive / negative baseline | Baseline drivers with positive (distribution, category) or negative (competitor media, price) effects |
| Donor | A variable allowed to give up volume when the UCM is added |
| Scalar κ | The UCM's contribution ÷ the trendline; carries the UCM to the other years |
| Retrofit | Carrying the UCM contribution — and only it — to the years outside the nested window |
| Media wave | One calendar year of a channel's activity, followed into later years |
| Indexed contribution | Long-term effect booked back to the year of the activity that caused it |
| Non-indexed contribution | Long-term effect booked to the week it is realised |
| Multiplier | (Short-term + long-term) ÷ short-term |
| Contraction (prior vs data) | How far the data moved a coefficient away from its prior. Near zero = the number is the prior |
| Placebo test | Re-fitting with media shifted in time: a real effect should beat it |
