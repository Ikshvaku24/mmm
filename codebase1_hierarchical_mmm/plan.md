# LTE (long-term effects) modelling in codebase 1 — plan

> **Status: PLAN. Nothing in this document is built.** Written 2026-10-15
> against codebase `2026.10.14.2`.
>
> **Inputs:**
> - the reference material in `../snapshots/UCM/`, transcribed in an
>   `understanding.md` per folder (index: `../snapshots/UCM/understanding.md`);
> - codebase 1 as read on 2026-10-15;
> - published UCM / state-space practice (§7, Appendix A).
>
> **Scope:** codebase 1 (`mmm/`) and the BRIDGE web app (`../web/`). Codebase 2
> is out of scope.

| Tag | Meaning |
|---|---|
| **[S]** | Stated in the reference material; the source is named (e.g. "deck S24" = LTE Methodology slide 24, "FR PDX S21" = France Parodontax slide 21) |
| **[C]** | Checked by us with the reference material's own numbers |
| **[V]** | Verified in the codebase on 2026-10-15 (file and line given) |
| **[R]** | Reported in `CLAUDE.md` / docs; not re-verified here |
| **[I]** | Our interpretation or proposal |
| **[?]** | Open — needs the team; see the D-numbers in §15 |
| **[D]** | A team decision of record — not re-opened here |

**How to read it:**

- **§1** is the one-page version.
- **§15** lists the decisions the team must take before Phase 1.
- **§16** is the order of work.
- **§6** holds every formula. The other sections refer to it rather than
  repeat it.

## Contents

1. Executive summary
2. Existing codebase architecture
3. Reference documentation summary
4. Current versus proposed workflow
5. LTE modelling pipeline
6. Mathematical definitions
7. UCM best practices and improvements
8. Data requirements and contracts
9. Code reuse and change inventory
10. Model execution and orchestration
11. Contribution calculations and reconciliation
12. Wave analysis design
13. Testing and validation strategy
14. Web application changes
15. Risks and unresolved questions
16. Implementation roadmap
- Appendix A — sources

---

## 1. Executive summary

### 1.1 Objective

Bring the team's **LTE workflow** into codebase 1, as defined in the LTE
Methodology deck and applied in the France Parodontax (FR PDX) study. One
audited pipeline should then produce a BMC's:

- **long-term** contributions;
- **total (short + long)** contributions;
- **multipliers**;
- **media waves**.

It is the same pipeline that already produces the BMC's short-term model.

### 1.2 The method, as the sources define it [S]

1. **Stitch** at least 3.5–4 years of sales, media and drivers.
2. **Extract a UCM trend** from sales: statsmodels `UnobservedComponents`
   (smooth trend + cycle + irregular); Level + Trend; refined with a
   Hodrick–Prescott (HP) filter, λ = 300.
3. **Regress that trend** (the long-term model) on long-term-transformed media,
   expert and a few baseline drivers:
   - the media are transformed SES (α = 0.05) → Hill → adstock;
   - the priors are on contribution shares.
4. **Nest the trend** as one extra variable (`ucm`) into the latest 2-year
   short-term model:
   - media, expert and negative-baseline variables **fixed**;
   - positive-baseline variables re-estimated.
5. **Retrofit** the UCM contribution to the earlier years with a scalar:
   *UCM contribution ÷ UCM trendline*.
6. **Long-term contribution of a lever** = its share of the UCM trend × the
   UCM's contribution to sales, week by week.
7. **Report:**
   - Years 1–2 as ST + LT;
   - multipliers (Total ÷ ST) and, optionally, ROI;
   - media waves: each year's activity, followed into later years.

### 1.3 The shape of the solution [I]

Four steps. Two of them are **ordinary codebase 1 model runs**:

| Step | What it is | New code |
|---|---|---|
| ① **UCM extraction** | Back-cast (optional burn-in) → UCM per region → HP → refined trend. Writes the datacubes for steps ② and ③ | **New** module `mmm/lte/ucm.py` (statsmodels) |
| ② **Long-term model** | A normal codebase 1 run; `dv` = refined UCM trend; features = long-term media, expert and drivers | No model change. A stage preset plus a share-based prior generator |
| ③ **Nested model** | A normal codebase 1 run, Secondary-style; `dv` = sales over the latest 2 years; the primary model's features plus `ucm` | No model change. A LOCKED / DONOR / CANDIDATE prior generator (METHODOLOGY §2c) |
| ④ **LTE results** | Retrofit, long-term contributions, final decomposition, multipliers, (ROI), waves, reconciliation | **New** module `mmm/lte/combine.py`, `waves.py` (pandas / numpy) |

- `model.py`, `fit.py`, the diagnostics and the decomposition are **not
  changed**.
- Both models inherit every existing output and check: convergence,
  contraction, collinearity, residual battery, reconciliation, CV.
- The steps talk through **files** (`05_contributions/contribution_timeseries.csv`
  and friends). Each step is also a separate run folder in the web app, so
  the deck's iterative loop ("set the UCM prior after reviewing the results")
  re-runs only step ③ and then ④.

### 1.4 What the analysis found

1. **FR PDX's final numbers are fully reproducible** [C]. Final_i = ST_i +
   share_i(UCM trend) × UCM share of sales, on all 7 levers of FR PDX S21
   (§11.3).
2. **One formula covers both windows** [I]. In the proposed setup — `ucm`
   uncentred, `scale_mode: mean`, measured against zero — the UCM scalar κ_g
   is a constant per region and is read directly from the nested coefficient.
   The long-term contribution in sales is then simply
   `L = κ_g × (contribution to the UCM trend)`, in the nested window and the
   retrofit window alike (§6.7).
3. **Every UCM output in the material has a start-up artefact** [C]: Level ≈ 0
   for 1–2 periods, the refined trend starting at 40–50% of sales.
   - The cause [V statsmodels source]: the default *approximate diffuse* start
     (variance 1e6, i.e. sd 1,000) on unscaled sales of 10⁵–10⁷.
   - It sits **inside the reported Years 1–2**. Fixing it is essential (U1);
     the deck's tip (a burn-in year) only pads around it.
4. **The brief's Step 3 differs from the sources** [S]. SES is applied to
   **media**; the UCM trend is refined with an **HP filter** (deck S11–S14;
   guide §6).
5. **Waves cannot be derived from the transformed media alone.** They need raw
   activity plus the long-term transformation parameters, or per-year columns
   from upstream (§12.2).
6. **The nested `ucm` is endogenous by construction** — a smoothed piece of the
   dependent variable (codebase 1's own METHODOLOGY §2b). Its share is set
   largely by its prior and the donors, so it must be declared,
   sensitivity-tested and audited (U11).
7. **The retrofit source conflicts** [S]. The brief (6.1) uses coefficients ×
   earlier media; the deck (S31) and FR PDX (S3) use the historically reported
   short-term outputs (D-03).
8. **λ = 300 is not cadence-neutral** [C]. It cuts at ≈26 *periods*: ≈6 months
   on weekly data (France) but ≈2 years on monthly data (Colombia) (U4, D-08).
9. **FR PDX uses two different multiplier definitions** [I]. Slides 11–13 are
   window-based: ST and LT measured inside 2022–23. Slide 15's waves are
   activity-based: effects followed into later years. They give different
   numbers for the same channel (§11.6).

### 1.5 Decisions needed before building

The full list, with recommendations, is §15:

- **D-01** pre-period handling;
- **D-02** the order of the long-term transformations;
- **D-03** the retrofit source;
- **D-05** which sales series feeds the UCM, given restatements;
- **D-06** regional structure;
- **D-08** the smoothing cut-off.

Waves additionally need **raw activity plus transformation parameters**, a data
request.

### 1.6 Size [I]

| Phase | Size |
|---|---|
| 1 — UCM | M |
| 2 — config, data and priors | M |
| 3 — combination | M |
| 4 — waves | M |
| 5 — orchestration and job | S–M |
| 6 — web app | L |
| 7 — docs and release | S |

A **minimum viable LTE** is Phases 1–3 run by hand in a notebook. It gives
long-term contributions and multipliers for one BMC before waves and the app
(§16).

---

## 2. Existing codebase architecture

### 2.1 What one codebase 1 run is [V]

**One joint PyMC model over all regions** (`mmm/modelling/model.py`), on the
scaled axis:

```
y[g,t] = α_g (optional) + Σ_k γ_k·fourier_k(t) + δ_g·t (optional) + Σ_j β[g,j]·x[g,t,j]
y ~ Normal | StudentT(σ_g)
```

**The features:**

- They **enter pre-transformed**. Codebase 1 never applies adstock, Hill or
  lag.
- `center_mode` / `scale_mode` are linear rescalings, decided per feature.
- Coefficients come in pooling × sign buckets (hierarchical / independent /
  global × positive / negative / free).

**Sampling** is NUTS through NumPyro (`mmm/modelling/fit.py`).

**One run = one model = one output tree:**

```
<run>/
  00_warnings/
  01_data/
  02_convergence/
  03_coefficients/
  04_fit/
  05_contributions/
  [06_cross_validation/]
  trace.nc
```

**Entry points:**

| Entry point | Use |
|---|---|
| `settings.run_from_yaml(path)` | hand runs |
| `app_job.run_app_job(params)` | the Databricks job behind the web app |
| `run_pipeline.run(df, ...)` | Python |

### 2.2 The modules LTE touches

| Module | Role today | Role in LTE | Change |
|---|---|---|---|
| `run_pipeline.run` | one model end to end | runs the long-term and nested models | **none** |
| `core/settings.py` | YAML front end, schema for the app (`SECTIONS`, `HELP`, `CHOICES`, `config_schema`) | registers a new `lte:` section | extend |
| `core/config.py` | dataclasses, `PeriodPolicy` / `resolve_period_plan` | `LTEConfig`; the LTE window plan | extend |
| `data/data_prep.py` | validation, scaling, `split_train` (the ONE training window) | unchanged; each model still gets its own window | none |
| `data/prior_builder.py` | pre-model prior generator; share file (`priors_from_shares`), mapping file (`priors_from_mapping`) | **long-term priors from contribution shares** (deck S19); LOCKED priors from vendor contributions | reuse; thin wrappers |
| `modelling/model.py`, `fit.py` | the model and the sampler | both models | **none** |
| `reporting/outputs.py` | `compute_decomposition` → `Decomposition` | supplies weekly medians and per-draw totals | none |
| `reporting/reconciliation.py` | `contribution_timeseries.csv`, `contribution_summary.csv`, `period_labels` (MAT) | **the interface between stages**; MAT labels | none |
| `checks/*` | convergence, contraction, collinearity, residual battery, CV | applied to both models as they are | none (+ new warning rules) |
| `app_job.py` | one model per job; run folders; `modelling_types.yaml`; `_read_request` | dispatch on the LTE stage; resolve the upstream runs | extend |
| `modelling_types.yaml` | `LTE: {}` already listed [V] | team-level LTE settings | content |
| — | — | UCM extraction, back-cast, combination, waves, LTE reporting | **new** `mmm/lte/` |

### 2.3 Data flow of one run, and the files LTE reads [V]

**The path through the code:**

```
datacube ──load_panel──► df ──[pre-model: build_priors]──► prepare_data (split_train, resolve_scaling)
   ──► build_model ──► fit (NUTS) ──► compute_decomposition ──► coefficient / fit / contribution reports
```

**What `compute_decomposition` keeps** (`reporting/outputs.py:206-345`):

- per feature: **weekly medians** (`contrib_median`) and **per-draw region
  totals** (`contrib_totals`);
- `yhat`, baseline and core **per draw**;
- **not** per-draw weekly arrays for each feature.

**Consequence:** at weekly resolution, LTE arithmetic works on **medians** —
the sum-of-medians convention codebase 1's reconciliation files already use.
Intervals need the draws (§11.5).

Files LTE reads from a finished model run:

| File | Content used by LTE |
|---|---|
| `05_contributions/contribution_timeseries.csv` | `region, date, dataset, period, pillar, feature, group, volume`; rows `__fitted__`, `__actual__`, `__residual__`, `__median_gap__` (`reconciliation.py:437-479`) |
| `05_contributions/contribution_summary.csv` | % of **actual** sales by period × region (incl. `__portfolio__`); pillar totals; residual and gap rows (`reconciliation.py:482-569`) |
| `05_contributions/contribution_math.csv` | `beta × Σ(x+shift) × dv_scale` per region × feature, the audit trail |
| `03_coefficients/coefficient_report.csv` | coefficient median / HDI per region × feature, plus `__population__` rows |
| `01_data/panel_summary.csv` | `kpi_center_used`, `kpi_scale_used` per region (`data_prep.py:476-488`) |
| `01_data/feature_scaling_stats.csv` | centre and scale per region × feature (`data_prep.py:489`) |
| `trace.nc` | posterior draws, when per-draw LTE intervals are wanted |

### 2.4 Configuration [V]

- **YAML sections:** `SECTIONS = {model, run, sampler, output, assumptions, cv}`
  plus a free `data:` block (`settings.py:60-74`).
  - Every settable field needs a `HELP` line (`test_v11`).
  - Every enum needs a `CHOICES` entry; the app's editor is drawn from
    `config_schema()` (`test_v19`).
- **The prior CSV** (`load_feature_config`, `config.py:748`) is read **by
  column name**, so an extra column does not break it.
  - Features come from the prior file, not the YAML.
- **Precedence:** team `config.yaml` < the modelling type's settings
  (`modelling_types.yaml`) < the run's own partial config
  (`app_job.merge_config`).
  - `LTE: {}` is already a type with no settings (`modelling_types.yaml:30`).
- **Team defaults that matter for LTE** [V CLAUDE.md §4.2]: `include_intercept:
  false`, `dv_center: none`, `dv_scale: mean`, `likelihood: student_t`,
  holdout = the 12.5% policy.

### 2.5 What codebase 1 does not have today [V]

| Gap | Consequence | Proposed handling |
|---|---|---|
| **No date-window selection** (`RunConfig` has holdout settings only) | the nested model (latest 2 years) and the long-term model (all history) cannot share one datacube | step ① **writes window-cut datacubes**; no core change (§5.5) |
| **No time-series decomposition**; statsmodels is not in `requirements.txt` [V] | UCM extraction is new | `mmm/lte/ucm.py` + `statsmodels` in `requirements.txt` (unpinned, the team's rule) |
| **One model per job; no chaining** | the four steps need an order | `mmm/lte/pipeline.py` for hand runs; stage runs in the app (§10) |
| **No post-model combination across runs** | retrofit and LT arithmetic are new | `mmm/lte/combine.py` |
| **Nothing reproduces media transformations** (correct for fitting) | waves need origin-year series | `mmm/lte/transforms.py`, used **only** for wave attribution, never to transform model inputs (§12) |

### 2.6 Invariants LTE must respect

| # | Invariant (codebase 1 `CLAUDE.md` §6) | Where it bites in LTE |
|---|---|---|
| 1 | **The scaling rule: the scale is the unit of every prior** | nested LOCKED priors come from the primary model's coefficients: same columns, same dv scale, or converted (§5.7). The retrofit must reuse the **nested model's** scales, never recompute them on the earlier window (§6.6) |
| 2 | **One training window** (`split_train`) | each model run keeps its own; LTE windows are cut **before** the run, so `split_train` still decides holdout within each |
| 3 | **Never edit one side of a transform** | LTE never rescales a model's outputs; it reads original-unit volumes |
| 4 | **Two arithmetics** (median of totals vs sum of medians) | LTE tables are sum-of-medians and add up; intervals come separately (§11.5) |
| 5 | **The fit never depends on reporting settings** | the LTE combination is reporting only; it never refits |
| 10 | **Version stamps on every module** | new `mmm/lte/*.py` modules get `__codebase__` via `python -m mmm.release` |
| 11–12 | **The job never edits `config.yaml`; a generated prior file is never overwritten** | LTE prior generators write proposals the modeller points at |
| 14 | **No intercept + uncentred KPI is legitimate** [D] | the nested model is Secondary-style, so `ucm` and the level variables stay uncentred |

### 2.7 Which representation of media each stage needs (the modelling constraint)

The brief warns that the media arrive **already transformed** and must not be
transformed again. Traced through the design:

| Stage | Variable | Representation it needs | Produced by | What codebase 1 does with it |
|---|---|---|---|---|
| ① UCM | sales | **raw KPI volume**, the same measure and scope as the nested model's `dv`, consistent over the whole history (restatements fixed upstream, FR PDX S23–24) | datacube | reads it; no media involved |
| ② Long-term model | media, expert | **long-term-transformed**: SES (α ≈ 0.05) → Hill (K, S) → adstock (λ) [+ lag], with the **long-term** parameters (deck S12, S16; guide §14) | the upstream long-term transformation | enters **linearly**, `center_mode: none` / `scale_mode: none` (or a pure rescale). **Never re-transformed** |
| ② Long-term model | drivers (price, distribution, market, competitor media) | as built for the long-term baseline (deck S18) | upstream | linear |
| ② Long-term model | `media_residual` | a decaying pre-window media stock (deck S18) | generated by step ① or supplied (D-10) | linear, baseline |
| ③ Nested model | short-term media, expert, trade and baseline features | **exactly the primary model's columns**: same short-term transforms, same units, else the LOCKED priors are wrong (scaling rule) | the primary model's datacube | as in the primary model |
| ③ Nested model | `ucm` | refined UCM trend τ (sales units) | step ① | `scale_mode: mean`, uncentred, measured against zero, sign positive |
| ④ Retrofit (coefficients mode) | short-term features, earlier years | short-term-transformed **continuously through the window boundary**: transform the full history, then cut | upstream | β × x × the **nested** scales |
| ④ Waves | raw activity per channel | **untransformed** activity plus the long-term parameters and order | upstream (must be retained — a new data request) | re-runs the documented chain **per origin year, for attribution only**, after checking that it reproduces the supplied columns |

**Conclusions:**

- The **same channel appears twice**: as a short-term column (nested model)
  and as a long-term column (long-term model). They are different columns with
  different transforms and must never be swapped.
- Codebase 1 transforms **nothing** that enters a model.
- Raw activity is needed **only** for waves.

---

## 3. Reference documentation summary

Full transcriptions, checks and open points are in each folder's
`understanding.md`. The essentials:

| Source | What it establishes |
|---|---|
| `LTE Methodology/` (31 slides) | **The procedure.** 7 stages (S5); UCM spec and method (S8–S11); SES / Hill / adstock formulas and order (S12–S16); long-term model variables, including "Linear Trendline" and "Media Residuals", trade / promotion excluded (S17–S18); contribution-share priors (S19); share of the UCM trend = ratio of sums (S21); nesting steps (S22); retrofit by scalar, "taken proportionally from the positive Baseline factors" (S23–S24); the weekly long-term formula (S25); media waves (S26); report Years 1–2 only, aggregated with historically reported short-term (S28–S31) |
| `Step by Step Guide/` | Plain-language summary. **Per-channel benchmark ranges** for S, K, adstock and lag (§14): TV S 1.8–3, K 0.5–0.8, adstock 50–80, lag 0–2; search and e-commerce S 1.3–1.5, K 0.3–0.5, adstock 40–50, lag 0. Total ROI = ST + LT |
| `FR PDX/` (25 slides) | **What is reported, and the arithmetic, verified:** short-term by year (S8–S9); total decomposition (S11, S21); long vs short per channel (S12); ROI and multipliers with spend (S13); benchmarks (S14); waves (S15, S25); long-term model fit (S19) and decomposition (S20); data restatement and back-cast (S23–S24). 3.5 years: 3 Jan 2022 – 23 Jun 2025, weekly |
| `ucm code with smooth trend/` | The UCM notebook: `UnobservedComponents(level='smooth trend', cycle=True, irregular=True)`, Powell, components smoothed, `Original UCM Trend = Level + Trend`, `hpfilter(λ=300)` → refined trend. Colombia Dolex total, monthly, 2022–25 |
| `ucm code with smooth trend region based/` | The same, per region: 12 brand × channel + All, Jul 2021 – Dec 2025. Regional trends do **not** add to All (4.3% gap) [C] |
| `ucm code multiple pool with smooth trend/` | The same, looped over the columns of a wide file. The run shown is damaged: 648 empty rows extrapolated, three series going negative [C] |
| `ucm output/` | A 182-week output table (most likely FR PDX). Proves `Trend` = slope and `Original UCM Trend(t)` = `Level(t+1)` [C]; shows the start-up artefact (refined trend 5.16M vs sales 12.4M in week 1) |
| `data polation 6 months/` | A time-reversed seasonal forecast back-casts 26 weeks before the data, most likely Excel `FORECAST.ETS`. The window matches Colombia's Jul 2021 start ⇒ most likely a **burn-in** [I] |
| `SES.png` | SES with α = 0.05 on **media** columns (Colombia): geometric memory, half-life 13.5 periods |

Verified numbers used as golden tests (§13.2):

- FR PDX S21 from S11 + S20;
- deck S25, 445.10 units;
- deck S21, 71%;
- FR PDX S13, overall multiplier 1.90 = Σ total ÷ Σ ST;
- FR PDX S12 = S13 ROI × spend;
- FR PDX S15, wave rows sum to 100%.

---

## 4. Current versus proposed workflow

| Brief step | How it is done today (reference material) | What codebase 1 does today [V] | Proposed in codebase 1 | Notes / discrepancy |
|---|---|---|---|---|
| **1. Historical data** | ≥3.5–4 yrs (deck S6). The back-cast is a time-reversed `FORECAST.ETS` (sheet) or "fill with the earliest year's average" (deck S8). Restated data back-cast and scaled up by hand (FR PDX S24) | none | **S0**: validate and plan windows (§5.2), the deck's feasibility checklist as checks. **S1**: optional back-cast, **burn-in only** | three pre-period methods in the sources (D-01); 3-year policy §5.2 |
| **2. UCM components** | Databricks notebooks (statsmodels), run by hand per BMC; outputs copied to Excel; failed exports and broken runs seen | none | **S2** `mmm/lte/ucm.py`: per region, parity or improved mode, diagnostics, plots, CSVs | level = baseline line; "Trend" = **slope** [C] |
| **3. Refine the trend** | **HP filter λ = 300** on Level + slope (notebooks; deck S11) | none | **S2**: HP with an explicit cut-off (U4) | the brief says **SES** on the trend; the sources apply SES to **media** and HP to the trend [S] |
| **4. UCM modelling** (long-term model) | Bayesian regression of the trend on long-term media; contribution-share priors (deck S17–S19); vendor-side | can fit it as-is if given a datacube with `dv` = trend | **S3**: a normal codebase 1 run with the long-term preset (intercept + linear trend), share-file priors, `media_residual` | trade / promotion excluded [S] |
| **5. Nested modelling** | Duplicate the core MMM, add the UCM, fix media / expert / negative baseline, remodel positive baseline, iterate the UCM prior (deck S22) | **METHODOLOGY §2c already describes it**: LOCKED / DONOR / CANDIDATE, Stage A/B, donor audit | **S4**: a Secondary-style run plus the LOCKED / DONOR / CANDIDATE prior generator, Stage A reference, donor audit, at most one prior correction (§2d) | brief: "priors locked except baseline"; deck adds **negative** baseline to the locked set [S] |
| **6. Retrofit** | κ = UCM contribution ÷ trendline (19%) × earlier trendline; taken proportionally from positive baseline (deck S24). Earlier short-term = historically reported (S31) | none | **S5** `combine.py`: κ_g from the nested coefficients; two modes, `historical` and `coefficients`; proportional positive-baseline allocation | **6.1 conflicts with deck S31** (D-03) |
| **7. LT and ST contributions** | Weekly: share of the UCM trend × UCM contribution (deck S25); aggregate (FR PDX S21) | none | **S5**: L = κ_g × c^τ, weekly per region; exactly the deck's formula with denominator = trend (D-07) | the brief's product formula holds when κ is constant [C] (§6.9) |
| **8. Final calculations** | ST + LT per lever, UCM row redistributed, Total Media = TV + Digital; ROI (value) and multipliers (FR PDX S11–S13, S21) | none | **S5 / S7**: FR PDX-layout tables, multipliers both ways, ROI opt-in | ROI vs the team's volume-only rule (D-13); two multiplier definitions (D-14) |
| **9. Waves** | Short/long by activity year, spill-over into later years (deck S26; FR PDX S15, S25); method not documented | none | **S6** `waves.py`: exact origin-year attribution through SES / adstock, proportional through Hill; tail beyond the data | needs raw activity + parameters (D-02, §12.2) |

---

## 5. LTE modelling pipeline

### 5.1 Overview

```
            ┌──────────────────────────────────────────────────────────────┐
 datacube ─►│ S0 validate + window plan  ─►  S1 back-cast (optional burn-in)│
 (sales,    │                                     │                        │
  ST + LT   │                                     ▼                        │
  columns)  │                  S2 UCM extraction (per region) ─► τ, ucm    │
            └───────────────┬──────────────────────────────┬───────────────┘
                            │ lt datacube                  │ nested + retrofit datacubes
                            ▼                              ▼
             S3 long-term model (codebase 1 run)   S4 nested model (codebase 1 run)
              dv = τ ; LT media + drivers           dv = sales (latest 2 yrs);
              priors: contribution shares           primary features + ucm;
                            │                       LOCKED / DONOR / CANDIDATE
                            │                              │   (iterate ≤ 1 correction)
                            └──────────────┬───────────────┘
                                           ▼
                S5 combine: κ_g, retrofit, L = κ·c^τ, final decomposition,
                            multipliers, (ROI), reconciliation
                                           ▼
                S6 waves (needs raw activity + LT parameters)  ─►  S7 report
```

- S3 and S4 depend only on S2, so they can run **in parallel**.
- S5 needs both. S6 needs S5.

### 5.2 The window plan

Definitions, with `n` observed periods and a cadence of 52 weeks or 12 months a
year:

| Window | Definition | Source |
|---|---|---|
| **N** — nested | the **latest 2 years**: 104 weeks / 24 months | deck S22, S29; brief Step 5 [S] |
| **R** — retrofit | everything observed **before** N | deck S23–S24 [S] |
| **H** — long-term model | R ∪ N, all observed history | deck S28 ("over the 4-year period") [S] |
| **B** — burn-in | optional back-cast periods before H. **UCM extraction only; never modelled** | U14 [I] |
| **Reported years** | Year 1, Year 2 = the first two years of H; calendar or 52-week blocks (D-11) | deck S31; FR PDX S12–S15 [S] |

**What each history length gives** (weekly; monthly is the same in months):

| Observed history | n (wk) | R | N | H | Years reported (default) | Follow-up after Year 2 |
|---|---|---|---|---|---|---|
| 4.0 years | 208 | 104 | 104 | 208 | Y1, Y2 | 2 years: deck S4, ≈96% of a Year-2 effect observed |
| 3.5 years (FR PDX) | 182 | 78 | 104 | 182 | Y1, Y2 | 1.5 years |
| 3.0 years | 156 | 52 | 104 | 156 | **Y1**; Y2 **flagged** as truncated (or tail-projected, U15) | 1 year: ≈13% of a Year-2 effect unobserved in the deck's example |
| < 3 years | — | — | — | — | **refuse LTE** (a nested-only run is still possible) | — |

**Answer to the brief's 3-year question [I]:**

- The **same method** applies, on a 3-year window.
- A back-cast **cannot stand in for the missing year as modelling data**. Sales
  can be extrapolated; media cannot — they are decisions, not forecastable
  series. A long-term regression on a back-cast trend against zero or invented
  media would bias effects towards zero.
- So with 3 years:
  - the back-cast (any length, 6–12 months) is used only as a UCM burn-in;
  - the long-term model runs on the 3 observed years;
  - Year 1 is reported in full; Year 2 is reported with its truncation shown
    (U15).

**The same reasoning for 3.5 years:** the "extrapolate the earlier six months"
practice is treated as a burn-in.

- The data-polation sheet supports this: Colombia already had 4 full years and
  was *still* extended by 6 months (`data polation 6 months/understanding.md`
  §6).
- If the team instead models the back-cast months (D-01), the plan supports it
  behind a flag, with a warning.

### 5.3 S0 — validate and plan

| | |
|---|---|
| **Purpose** | Make the data "stable and auditable before any model is fitted" (deck S6); decide the windows |
| **Inputs** | the LTE datacube (§8.1); `lte:` settings |
| **Checks** | **Continuity**: regular dates, no gaps, no duplicates; a level-shift test on sales (the FR PDX restatement shows ≈20–30% jumps). **Variation**: per long-term channel, active periods per year and spend / timing variation. **Coverage**: media, price, distribution and market drivers present. **Traceability**: a source / owner / transformation note per input (manifest). **Window feasibility**: n ≥ 3 years; N fits; the reported years are fully inside H |
| **Outputs** | `00_feasibility/feasibility_report.csv` (check, level, detail), `window_plan.json` (B, H, N, R, reported years, cadence), `data_checks.md` |
| **Failure** | blocking checks stop the study, naming the column and region; others are warnings (`00_warnings`) |

### 5.4 S1 — back-cast (optional burn-in)

| | |
|---|---|
| **Purpose** | Give the UCM smoother history before the first modelled period, so its start-up does not land in the reported years |
| **Method** | **Time-reversed ETS**: reverse the series onto an ascending synthetic weekly timeline; fit Holt–Winters additive (error, trend and seasonality additive — the AAA form behind `FORECAST.ETS`) with seasonal period 52 / 12; forecast h periods; reverse back (§6.2). Alternative from deck S8: fill the burn-in with the **average of the earliest year** |
| **Inputs** | sales per region over H; `lte.backcast_periods` (0 = off), `lte.backcast_method` (`ets` \| `average_year`) |
| **Outputs** | `01_ucm/backcast.csv` (region, date, value, `is_backcast`); a chart |
| **Rule** | back-cast periods are dropped after S2; they never enter S3–S7 (U14) |
| **Note** | statsmodels' ETS will not reproduce Excel's `FORECAST.ETS` exactly, which uses its own AAA estimation (D-19). Parity is checked on the trend, not on the back-cast values |

### 5.5 S2 — UCM extraction

| | |
|---|---|
| **Purpose** | Produce the refined UCM trend τ per region: the long-term model's `dv` and the nested model's `ucm` |
| **Method — parity mode** | Exactly the notebooks: `UnobservedComponents(level='smooth trend', cycle=True, irregular=True)`, `fit(method='powell')`, raw scale, approximate diffuse start, no frequency; `Original = Level + Trend(slope)`; `hpfilter(λ = 300)` |
| **Method — improved mode** | **U1** scale (÷ mean) and exact diffuse start. **U2** a dated index with frequency, cycle bounded or dropped. **U5** seasonal tested. **U4** λ set from a stated cut-off. **U7** diagnostics. **U8** end-point handling (§7) |
| **Inputs** | sales (+ burn-in) per region; `lte.ucm_*` settings; region list |
| **Outputs** | `01_ucm/ucm_components.csv` (§8.3), `ucm_parameters.csv` (variances, cycle period, log-likelihood, AIC, optimiser status), `ucm_diagnostics.csv` (Ljung–Box, normality, heteroskedasticity, start-up check, permanent-component check), `ucm_<region>.png` (the vendor's 8 panels) |
| **Also writes** | the **window-cut datacubes** for S3 and S4 (§8.3): `lte_long_term_datacube.csv` (`dv` = τ, long-term columns, H); `lte_nested_datacube.csv` (`dv` = sales, short-term columns + `ucm` = τ, N); `lte_retrofit_datacube.csv` (sales, short-term columns + `ucm`, R) |
| **Checks** | identity checks (§13.7); τ > 0 everywhere; start-up check: \|τ_first / sales-level\| within a band; a warning when σ²_ζ = 0 (no permanent component, U6) |

### 5.6 S3 — the long-term model (a codebase 1 run)

| | |
|---|---|
| **Purpose** | Each lever's contribution to, and share of, the UCM trend (deck S17–S21; FR PDX S19–S20) |
| **Data** | `lte_long_term_datacube.csv` over H |
| **Features** | long-term media and expert ("hero" variables only, deck S18); baseline drivers (market, distribution, price, competitor media); `media_residual` (D-10). **Trade / promotion refused** (deck S18, a validation rule) |
| **Stage preset** (`mmm/lte/presets.py`) [I] | `include_intercept: true`, `include_trend: true` (together: deck S18's "Linear Trendline y = mx + c"); `fourier_order: 0` (τ has no seasonality); `dv_center: none`; `dv_scale: mean`; media `center_mode/scale_mode: none` |
| **Priors** | contribution shares (deck S19: Baseline 70–80%, TV 20–25%, Offline 2–5%, Digital 2–5%, Expert 0.1–0.5%, "dependent on the BMC's media and expert budget mix"), written as a **share file** (sections `media` / `expert` / `baseline`) and converted by the existing `priors_from_shares`. Shares are of the UCM trend because `dv` = τ |
| **Outputs** | the standard codebase 1 tree. The fit tiles reproduce FR PDX S19 (R², MAPE, holdout MAPE, actual vs predicted); `contribution_summary.csv` gives S20's table by pillar |
| **Extra checks** | spurious-regression guards (U10): Durbin–Watson / ACF from the existing residual battery, a placebo run, a baseline-only comparison, contraction per media coefficient |

### 5.7 S4 — the nested model (a codebase 1 run)

| | |
|---|---|
| **Purpose** | The UCM's contribution to sales in the latest 2 years, with media held at the primary model's values (deck S22) |
| **Data** | `lte_nested_datacube.csv` over N |
| **Features** | the primary (AO MMM) model's features, identical columns, plus `ucm` |
| **Stage preset** [I] | Secondary-style: `include_intercept: false`, `dv_center: none`, `dv_scale: mean`. `ucm`: `sign_constraint: positive`, `center_mode: none`, `scale_mode: mean` (so β^u reads as "share of the trendline", §6.5), `contribution_reference: zero`, `baseline: 1`, `pillar: UCM`. `include_trend: false` when the primary had a trend the UCM replaces (D-16) |
| **Priors** — the register (METHODOLOGY §2c) | **LOCKED** (`global_prior_sd 0.02`, relative): media, expert, trade, competitor, price and every negative baseline variable, plus any dummy not under test. **DONOR** (`0.3–0.5`): the positive baseline variables (auto: `baseline=1` and sign positive, or an explicit list, D-17). **CANDIDATE**: `ucm`, wide (`0.6–0.7`) at a neutral mean on the share-of-trendline scale |
| **Where the means come from** | a codebase 1 primary run: `coefficient_report.csv` medians, **converted when the dv scale differs** (§6.5); or a vendor model: the mapping file with contributions (`priors_from_mapping`, case a) |
| **Runs** | **Stage A** (no `ucm`): reproduce the primary within ±2–3% per LOCKED variable. Skipped when the primary is a codebase 1 run on the same data and window. **Stage B** (with `ucm`) |
| **Outputs** | two standard trees, plus `donor_audit.csv` (Δ of every variable, A → B, METHODOLOGY §2c step 5) |
| **Iteration** | the deck's "set the UCM prior after reviewing the results" = **at most one correction** of the candidate mean (METHODOLOGY §2d). Then declare the share as an assumption when contraction < 0.2, or pin it (§10.7) |

### 5.8 S5 — retrofit and combination (no sampling)

| | |
|---|---|
| **Purpose** | κ_g; the UCM contribution in R; long-term contributions in H; the final decomposition; multipliers; optional ROI; reconciliation |
| **Inputs** | S2 (τ), S3 and S4 output trees, the retrofit datacube; optionally the **history file** (`historical` mode) and spend / price (ROI) |
| **Calculations** | §6.5–§6.10 and §11 |
| **Retrofit modes** (D-03) | `historical`: the reported short-term decomposition of the earlier years plus κ·τ, with positive baseline reduced proportionally (deck S24, S31). `coefficients`: nested coefficients × earlier short-term columns plus κ·τ, the residual absorbed proportionally by the positive baseline (brief 6.1–6.3) |
| **Outputs** | `04_lte/ucm_scalar.csv`, `retrofit_timeseries.csv`, `lte_timeseries.csv`, `lte_summary.csv`, `lte_multipliers.csv`, `lte_roi.csv` (opt-in), `lte_reconciliation.csv`, `lte_contribution_math.csv` (§11.4) |
| **Checks** | κ constant over N (else the `ucm` was centred or re-referenced — stop); U ≤ positive baseline in R (else flag the week); every invariant in §13.7 |

### 5.9 S6 — media waves

See §12. Inputs: S5 outputs, raw activity, long-term parameters. Outputs:
`04_lte/lte_waves.csv`, `lte_wave_tail.csv`, the charts.

### 5.10 S7 — report

| | |
|---|---|
| **Files** | `04_lte/lte_report.md` (the FR PDX storyline as tables: S5 trend and growth, S8–S9 short-term, S11 / S21 total, S12 long vs short, S13 multipliers / ROI, S15 waves, S19–S20 long-term fit and decomposition, plus κ and the retrofit chart of deck S23–S24); the PNGs; `lte_manifest.json` |
| **Rules** | every table states its window, units (volume; value only with ROI) and arithmetic (sum of medians). Assumption-driven quantities are labelled (U11) |

---

## 6. Mathematical definitions

### 6.0 Notation

| Symbol | Meaning |
|---|---|
| g, t | region, period (week or month) |
| i, j | i = a long-term media / expert feature; j = any nested-model feature |
| ℓ | a lever (codebase 1 `pillar`): TV, Digital Media, Expert, Trade, Baseline… |
| S_{g,t} | observed sales (the KPI, volume) |
| H, N, R, B | windows (§5.2) |
| τ_{g,t} | refined UCM trend (S2) |
| c^τ_{g,t,i} | contribution of i to the UCM trend (long-term model, original units) |
| C_{g,t,j} | short-term contribution of j to sales (nested model, or history) |
| U_{g,t} | the UCM's contribution to sales |
| κ_g | the UCM scalar (share of the trendline credited to the UCM) |
| L_{g,t,i} | long-term contribution of i to sales |
| d^S_g, d^τ_g | dv scales of the nested and long-term models (`kpi_scale_used`) |
| s^u_g | the scale of the `ucm` column in the nested model (`feature_scaling_stats.csv`) |
| β^u_g, b_{g,i} | nested `ucm` coefficient; long-term coefficient of i |

### 6.1 UCM: the smooth-trend model (S2) [S code; I equations]

```
S_t      = μ_t + [γ_t] + ψ_t + ε_t              ε_t ~ N(0, σ²_ε)       (γ only if seasonal, U5)
μ_{t+1}  = μ_t + β_t                            the level has no shock of its own ("smooth")
β_{t+1}  = β_t + ζ_t                            ζ_t ~ N(0, σ²_ζ)        the slope is a random walk
ψ_t      = deterministic cycle, frequency λ_c   (optionally stochastic / damped)
```

Smoothed estimates μ̂_{t|T}, β̂_{t|T}, ψ̂_{t|T} (Kalman smoother). The
vendor's columns:

```
Level      = μ̂_t                     Trend = β̂_t   (the SLOPE, sales per period per period) [C]
Cycle      = ψ̂_t
Irregular  = S_t − μ̂_t − β̂_t − ψ̂_t   (vendor; the model's own irregular is S_t − μ̂_t − ψ̂_t [− γ̂_t])
τ⁰_t       = μ̂_t + β̂_t  = μ̂_{t+1|T} ("Original UCM Trend" = next period's level) [C]
```

### 6.2 HP refinement and back-cast

```
τ = argmin_τ  Σ_t (τ⁰_t − τ_t)²  +  λ Σ_t (τ_{t+1} − 2τ_t + τ_{t−1})²        refined UCM trend
Refined cycle = τ⁰ − τ

Half-gain period:   P(λ) = 2π / arccos(1 − 1/(2√λ))
    λ = 300   → P ≈ 26.1 periods (≈ 6 months weekly, ≈ 2.2 years monthly)       [C]
    same calendar cut-off across cadences (Ravn–Uhlig): λ_monthly = λ_weekly × (12/52)⁴
```

**Back-cast (S1).** Reverse r_k = S_{T+1−k} onto an ascending timeline. Fit
ETS(A, A, A) with seasonal period m. Forecast h steps: r̂_{T+1..T+h}. Then
Ŝ_{1−k} = r̂_{T+k}, k = 1..h.

### 6.3 Long-term media (supplied; reproduced only for waves) [S deck S12, S14, S16]

```
SES:      e_t = α·a_t + (1 − α)·e_{t−1}              started at the first NON-ZERO a_t (deck S14)
Hill:     h_t = e_t^S / (e_t^S + K^S)                K on the scale the upstream tool uses (deck S16:
                                                     "0 < K < 1", relative to max activity [?] D-02)
Adstock:  m̃_t = h_{t−lag} + λ·m̃_{t−1}                not normalised (deck S12)
Order:    SES → Hill → adstock (deck S12 banner, S17)   vs   SES → adstock → Hill (deck S12 formula)   [?] D-02
```

### 6.4 The long-term model (S3), in codebase 1's terms

With `dv_center: none`, `dv_scale: mean`, media `none/none`:

```
τ_{g,t} / d^τ_g = α_g + δ_g·t' + Σ_i b_{g,i}·m̃_{g,t,i} + Σ_k (drivers) + e_{g,t}

c^τ_{g,t,i}      = b_{g,i} · m̃_{g,t,i} · d^τ_g                                       (contrib_median)
c^τ_{g,t,base}   = τ_{g,t} − Σ_{i∈M} c^τ_{g,t,i}       core, drivers, media_residual, residual and gap
p_{g,t,i}        = c^τ_{g,t,i} / τ_{g,t}               share of the UCM trend; denominator = τ (D-07)
share_i^τ(P)     = Σ_{t∈P} c^τ_{g,t,i} / Σ_{t∈P} τ_{g,t}          ratio of sums (deck S21)       [S]
```

M = the media and expert features. The denominator alternative, D-07, is the
fitted ŷ^τ; it differs by the residual.

### 6.5 The nested model (S4) and the UCM scalar κ

With no intercept, `ucm` scale = its mean s^u_g, uncentred, reference zero:

```
S_{g,t} / d^S_g = Σ_j β_{g,j}·x_{g,t,j} + β^u_g·τ_{g,t}/s^u_g + e_{g,t}          t ∈ N

U_{g,t} = β^u_g · τ_{g,t} · d^S_g / s^u_g
κ_g     = U_{g,t} / τ_{g,t} = β^u_g · d^S_g / s^u_g            CONSTANT over t   (deck S24 "19%")
        = Σ_N U_{g,t} / Σ_N τ_{g,t}                            (the deck's way of computing it)
```

- Because d^S_g ≈ mean sales and s^u_g ≈ mean τ ≈ mean sales, **κ_g ≈ β^u_g**.
  The coefficient reads as "the share of the trendline credited to the UCM".
  That is why `scale_mode: mean` is proposed for `ucm`.
- **Prior conversion when the windows differ.** LOCKED means come from the
  primary model's coefficients. If the primary's dv scale d^S_prim differs
  from the nested model's, the means become:

  ```
  β_nested = β_prim × d^S_prim / d^S_nested
  ```

  This is the scaling rule; the generator does it and prints both scales.

### 6.6 Retrofit (S5, t ∈ R)

```
U_{g,t} = κ_g · τ_{g,t}                                                    (deck S24; brief 6.2)

historical mode (deck S31):     C_{g,t,j} = reported short-term contribution (history file)
coefficients mode (brief 6.1):  C_{g,t,j} = β̂_{g,j} · x_{g,t,j} · d^S_g        nested medians and NESTED scales
                                                                          (never re-scaled on R: the scaling rule)

Positive-baseline allocation (brief 6.3; deck S24 "taken proportionally from the positive Baseline factors"),
with B⁺ = the positive baseline features and P⁺_{g,t} = Σ_{j∈B⁺} C_{g,t,j}:

historical:    C'_{g,t,j} = C_{g,t,j} · (1 − U_{g,t} / P⁺_{g,t})                        j ∈ B⁺
coefficients:  r_{g,t} = S_{g,t} − Σ_j C_{g,t,j} − U_{g,t}     (the retrofit residual)
               C'_{g,t,j} = C_{g,t,j} · (1 + r_{g,t} / P⁺_{g,t})                        j ∈ B⁺
flags:   U_{g,t} > P⁺_{g,t}  (UCM larger than the positive baseline)    |r / P⁺| > 20%  (poor extrapolation)
```

### 6.7 Long-term contributions in sales (S5, t ∈ H = R ∪ N)

```
L_{g,t,i}    = p_{g,t,i} · U_{g,t}             the deck S25 formula, weekly
             = κ_g · c^τ_{g,t,i}               the same thing, since U = κ·τ in both windows   [I]
L_{g,t,base} = U_{g,t} − Σ_{i∈M} L_{g,t,i}     the long-term model's baseline share of U goes to Baseline
```

### 6.8 The final decomposition

```
Final_{g,t,i}    = C_{g,t,i} + L_{g,t,i}                     i ∈ M (media and expert)
Final_{g,t,j}    = C_{g,t,j}                                 every other incremental feature (trade, competitor…)
Final_{g,t,Base} = Σ_{j∈baseline, j≠ucm} C'_{g,t,j} + L_{g,t,base} + (residual, median gap)
UCM row: removed (fully redistributed)                                                   [S FR PDX S21]
Σ_levers Final_{g,t,·} = S_{g,t}       for every g, t                                     (invariant I7)
```

### 6.9 Aggregates and the FR PDX identity

For a period P and any set of regions:

```
ST%_i = Σ C_i / Σ S        LT%_i = Σ L_i / Σ S        Total%_i = ST%_i + LT%_i
UCM%  = Σ U / Σ S

Identity (per region, with κ constant and denominator τ):
   LT%_i(P) = share_i^τ(P) × UCM%(P)                                                     [C FR PDX S21]
   proof:  Σ_P L_i = κ Σ_P c^τ_i = (Σ_P c^τ_i / Σ_P τ) · κ Σ_P τ = share_i^τ · Σ_P U
Across regions with different κ_g the identity holds only τ-weighted.
⇒ always compute per region, then sum. Never apply a national share to regional U.
```

### 6.10 Multipliers and ROI

```
Window-based  (FR PDX S11–S13):   M_i = (Σ_{t∈W} C_i + Σ_{t∈W} L_i) / Σ_{t∈W} C_i
                                   overall M = Σ_i (…) / Σ_i Σ C_i      (effect-weighted; 1.90 = 7.237 / 3.811) [C]
Activity-based (from the waves):   M_i^wave(y) = W_{i,y} / ST_{i,y}     (§12)
ROI (opt-in, D-13):  ROI_i^{ST|LT|Total} = value_i / spend_i ,  value = volume × price
                     (FR PDX: value ≈ 0.07 × volume, constant across channels) [C]
```

### 6.11 Waves (S6) — summary; the full design is §12

```
m̃_{g,t,i} = Σ_y m̃^{(y)}_{g,t,i}                        origin-year split of the long-term input
L_{g,t,i,y} = L_{g,t,i} · m̃^{(y)}_{g,t,i} / m̃_{g,t,i}
W_{i,y} = ST_{i,y} + Σ_{Y ≥ y} LT_{i,y→Y} [+ projected tail];   reported as shares of W_{i,y}
```

### 6.12 Uncertainty [I]

- Every quantity above can be computed **per posterior draw**. The long-term
  and nested posteriors are independent, so draw d of one is paired with draw
  d of the other.
- Window totals then get medians and 90% HDIs, exactly as codebase 1's
  `contribution_totals.csv` does.
- These **do not add up**, by design (invariant 4). The weekly tables stay
  sum-of-medians and do.
- The UCM trend's own uncertainty (stage S2) is **not** in these intervals.
  U9 handles it.

---

## 7. UCM best practices and improvements

Grounded in: Harvey (1989); Harvey & Jaeger (1993) — the HP filter is the
smoother of a smooth-trend UCM with a fixed variance ratio, and mechanical HP
can create spurious cycles; Durbin & Koopman (2012); Commandeur & Koopman
(2007); Ravn & Uhlig (2002) on λ by frequency; Hamilton (2018) and Hall &
Thomson (2021) on HP end-points; Granger & Newbold (1974) on regressions of
smooth series; Cain (2025) on testing for a permanent component and modelling
long-term effects through the base; Dekimpe & Hanssens (1995) on persistence;
Scott & Varian (2014) on Bayesian structural time series; the statsmodels
documentation and source. Full list: Appendix A.

**Summary:**

| ID | Improvement | Essential? |
|---|---|---|
| U1 | Fix the start-up artefact at source: scale + exact diffuse start | **Essential** |
| U2 | Dated index with a frequency; bound or drop the cycle | **Essential** |
| U3 | Input hygiene: one series per region, regular dates, no empty rows | **Essential** |
| U4 | Make the smoothing cut-off explicit and cadence-consistent (λ) | **Essential (decision)** |
| U5 | Test for seasonality; model it when present | Recommended |
| U6 | Test for a permanent component before any long-term claim | Recommended |
| U7 | UCM diagnostics and an alternative-spec comparison | **Essential** |
| U8 | Handle the trend's end-points; track revisions | Recommended |
| U9 | Two-stage uncertainty: a sensitivity grid | Recommended |
| U10 | Guards against spurious regression in the long-term model | **Essential** (reporting); AR(1) errors optional |
| U11 | Control and declare the nested UCM's endogeneity | **Essential** |
| U12 | Extract the UCM from base sales where a full-window short-term decomposition exists | Optional |
| U13 | Regional consistency of UCMs | **Decision** |
| U14 | Back-cast = burn-in only; media never back-cast | **Essential** (rule) |
| U15 | Project the wave tail beyond the data | Optional |
| U16 | A vendor-parity mode | **Essential** (validation) |
| U17 | Column definitions (irregular, "Original" trend) | Optional (cosmetic) |

### U1 — Fix the start-up artefact at its source (essential)

1. **Current.** The notebooks fit raw sales of 10⁵–10⁷ with statsmodels'
   default *approximate diffuse* initialisation: initial state variance **1e6**
   [V statsmodels `initialization.py`], i.e. a prior sd of 1,000 on the
   starting level.
2. **Limitation.**
   - The level starts at ≈0 and needs 2–6 periods to reach the data. The
     irregular absorbs the first period's whole sales.
   - The refined trend starts at **5.16M vs 12.4M sales** (France) and **5.15M
     vs 9.87M** (Base_Droguerias) [C].
   - The HP filter spreads the error over months.
   - It lands in **Year 1, the reported year**. Through κ·τ and L = κ·c^τ it
     depresses Year 1's UCM contribution and long-term effects.
   - The deck's remedy (slide 8: fill a prior year with an average) pads
     around the symptom. The only clean series in the material, Ninos_Superetes
     at ~10⁴, has no artefact — consistent with the diagnosis.
3. **Proposal.**
   - Divide each series by its mean before fitting; rescale the components
     after.
   - Set `use_exact_diffuse=True`.
   - Keep the burn-in (U14) as an optional safeguard.
4. **Benefit.** A correct trend from the first period; no dependence on the
   back-cast; Year 1 no longer biased.
5. **Cost / trade-offs.** A few lines. Results change against the vendor's
   numbers, so a parity mode is kept (U16) and the difference is reported side
   by side once per BMC.
6. **Essential.**

### U2 — Dated index; bound or drop the cycle (essential)

1. **Current.** No frequency is set, so statsmodels warns "No frequency
   information".
2. **Limitation.**
   - Without dates statsmodels bounds the cycle only by frequency ∈ (0, π), so
     the estimated "cycle" has a period of **2–3 periods** and an amplitude of
     ±10s–1,000s on series of 10⁶–10⁷ [C].
   - It is noise, and a wasted parameter.
   - With dates, statsmodels' default bounds are **1.5–12 years** [statsmodels
     docs].
3. **Proposal.**
   - Index by date with `freq` (`W-MON` / `MS`), or pass `cycle_period_bounds`.
   - Report the estimated period and amplitude.
   - Drop the cycle (`cycle=False`) when its amplitude is < 0.5% of the level,
     or when AIC prefers no cycle.
   - Offer `stochastic_cycle` / `damped_cycle` as options.
4. **Benefit.** Interpretable components; a cleaner trend.
5. **Cost.** Low. A 3.5-year series cannot identify a 4–12-year business
   cycle anyway, so "no cycle" will often win — say so in the report.
6. **Essential** (cheap and corrects a visible error).

### U3 — Input hygiene (essential)

1. **Current.** Two of the three notebooks show broken runs:
   - 648 empty CSV rows read as NaN, so the Kalman filter **extrapolated the
     trend over them**, three series to negative values;
   - 13 regional series stacked into one 702-row "series";
   - failed exports (`NameError`, `OSError`).
2. **Limitation.** These failures are silent. Only a chart reveals them.
3. **Proposal.** The module validates before fitting:
   - one series per region;
   - strictly increasing, evenly spaced dates;
   - no NaN or zero-padded tails;
   - minimum length (≥ 2 years + burn-in).

   Outputs are written by codebase 1's robust writers (`save_fig`,
   `app_job.copy_tree`).
4. **Benefit.** No silent garbage.
5. **Cost.** Low.
6. **Essential.**

### U4 — An explicit, cadence-consistent smoothing cut-off (essential: a decision)

1. **Current.** λ = 300 for both weekly (France) and monthly (Colombia) data.
2. **Limitation.**
   - The HP half-gain period is ≈26 *periods* [C]: ≈6 months on weekly data,
     ≈2.2 years on monthly data.
   - "Long-term" therefore means 4× different things across BMCs.
   - The classic λ = 1600 (quarterly) and its Ravn–Uhlig equivalents
     (monthly 129,600) cut at ≈10 years — far smoother. The team's λ is a
     deliberate choice, but undocumented.
   - HP after a smooth-trend UCM smooths twice. HP *is* that model's smoother
     with a fixed variance ratio (Harvey & Jaeger 1993).
3. **Proposal.**
   - Express the setting as a **cut-off in calendar time**
     (`lte.trend_cutoff_months`) and derive λ per cadence (§6.2).
   - Print the implied cut-off in every run.
   - Keep `hp_lambda` as an explicit override for parity.
   - Optional: replace "UCM then HP" with one UCM whose slope variance ratio is
     fixed to the same cut-off.
4. **Benefit.** The same economic meaning across BMCs; documented.
5. **Cost.** A decision (D-08). Long-term shares change with the cut-off — that
   sensitivity is reported (U9).
6. **Essential** — the decision; the code is trivial.

### U5 — Test for seasonality (recommended)

1. **Current.** The deck says to test with ACF or periodogram and add
   `seasonal` only when found (S10). The notebooks never add one. The deck's
   own chart shows a 20-week seasonal changing the trend (S11).
2. **Limitation.** Untreated seasonality leaks into the level and irregular,
   leaving the "mid-term fluctuations" the deck then removes with HP (S11
   step 4).
3. **Proposal.**
   - Test automatically: ACF at the seasonal lag; the AIC of models with and
     without `freq_seasonal=[{period: 52.18 | 12, harmonics: h}]` (h = 2–6 for
     weekly).
   - Include the seasonal when it wins; report the decision.
4. **Benefit.** A cleaner trend; less reliance on HP.
5. **Cost.** Weekly seasonality on 3.5 years is weakly identified, with only
   3.5 cycles — use few harmonics and keep it deterministic when the
   stochastic version does not converge.
6. **Recommended.**

### U6 — Test for a permanent component (recommended)

1. **Current.** The trend is extracted and used whatever its nature.
2. **Limitation.** A long-term effect read off a trend presumes the series has
   a permanent (evolving) component. If σ²_ζ ≈ 0 (deterministic trend) or the
   series is stationary, the "UCM trend" is a smooth line through noise.
   - Cain (2025): "first test whether sales contain a permanent component".
   - Dekimpe & Hanssens (1995): persistence modelling starts from unit-root
     tests.
3. **Proposal.** Report:
   - σ̂²_ζ and its standard error;
   - an LR / AIC comparison of the smooth trend with a deterministic linear
     trend;
   - KPSS / ADF on sales.

   Warn when there is no permanent component (`ucm_no_permanent_component`).
4. **Benefit.** Stops long-term claims on series that cannot support them.
5. **Cost.** Low (statsmodels tests).
6. **Recommended.**

### U7 — Diagnostics and alternative specifications (essential)

1. **Current.** Charts only. Powell's convergence is not checked.
2. **Limitation.** A failed or local optimum goes unnoticed. Powell is
   derivative-free and can stop early.
3. **Proposal.**
   - `ucm_diagnostics.csv`: Ljung–Box, normality and heteroskedasticity on
     standardised residuals (`test_serial_correlation`, `test_normality`,
     `test_heteroskedasticity`); the optimiser's status (`mle_retvals`); the
     log-likelihood.
   - Refit from 3 starting points; flag disagreement.
   - Compare smooth trend vs local linear trend by AIC.
   - Keep the 8-panel chart.
4. **Benefit.** Each region's trend comes with evidence.
5. **Cost.** Low compute; tiny models.
6. **Essential.**

### U8 — End-points and revisions (recommended)

1. **Current.** HP on the full sample.
2. **Limitation.**
   - HP estimates at the ends of the sample are unreliable and revised as data
     arrive (Hamilton 2018).
   - The nested window **ends at the sample end**, so the most-recent `ucm`
     values are the least reliable.
   - Each refresh will move last year's trend.
3. **Proposal.**
   - Forecast-extend the trend by h periods before HP and trim after. Hall &
     Thomson (2021) found this improves end-point accuracy.
   - Or use the UCM smoother itself at the ends.
   - On a refresh, report the revision of τ over the overlap (`ucm_revision.csv`).
4. **Benefit.** A more stable `ucm` in the nested window; visible revisions.
5. **Cost.** Moderate.
6. **Recommended.**

### U9 — Two-stage uncertainty (recommended)

1. **Current.** τ is treated as data in S3 and S4.
2. **Limitation.** τ is an estimate (a generated variable). Intervals in S3 and
   S4 ignore its uncertainty, and the long-term results depend on S2's
   settings (λ, spec).
3. **Proposal.**
   - A **sensitivity grid**: for 3–5 S2 settings (λ / cut-off ×2 and ×½; with
     and without seasonal; parity vs improved), re-run S3 → S5 with S4 fixed.
     Report the range of LT%_i and multipliers.
   - Optional: draw τ from the statsmodels simulation smoother and propagate.
4. **Benefit.** An honest range for every long-term number.
5. **Cost.** Compute: the long-term model is small (≈1 min per fit on GPU).
   The grid needs no code beyond a loop.
6. **Recommended.**

### U10 — Spurious-regression guards for the long-term model (essential: reporting)

1. **Current.** A smooth `dv` (τ) regressed on smooth regressors (SES α = 0.05
   plus adstock) under an iid likelihood. FR PDX reports R² 98.5%, MAPE 1.0%.
2. **Limitation.**
   - Two smooth series correlate whether or not one drives the other (Granger
     & Newbold 1974).
   - The residuals are strongly autocorrelated, so the posterior is
     over-confident.
   - The effective sample is a handful of independent movements over 3.5
     years.
   - So the fit statistics do not validate the shares; **the priors (deck S19)
     do much of the work**.
3. **Proposal.** Report, every run:
   - Durbin–Watson and ACF (already in `04_fit/assumption_checks.csv`);
   - contraction per media coefficient (`prior_posterior_contraction.csv`) —
     c < 0.2 means "the share is the prior";
   - a **placebo**: media shifted by ±26 weeks or years permuted, refitted — a
     real effect should beat it;
   - a **baseline-only** model (intercept + trend + drivers) — the fit gained
     from media.

   Optional later: AR(1) errors in codebase 1's likelihood. That is a model
   change, needing `test_v24`.
4. **Benefit.** Readers know which long-term shares the data support.
5. **Cost.** Two extra small fits per study; no model change.
6. **Essential** (the reporting part).

### U11 — The nested UCM's endogeneity (essential)

1. **Current** [S]. The UCM extracted from sales is added as a regressor for
   sales (deck S22).
2. **Limitation.**
   - Codebase 1's METHODOLOGY §2b, step 6: a UCM decomposition of the KPI is
     "a smoothed piece of the dependent variable … endogenous by construction,
     it will absorb anything and its share means little".
   - The HP filter is two-sided, so τ_t contains information from later sales.
   - The UCM's share (FR PDX: 20.3%) is therefore set by its prior and by
     which variables are free to donate (§2c).
3. **Proposal.**
   - Keep the deck's locks: media, expert, negative baseline.
   - `ucm` sign positive and `scale_mode: mean` (β^u ≈ κ, a share prior that
     can be written down).
   - CANDIDATE prior; at most **one** mean correction (§2d).
   - A **prior-sensitivity sweep**: κ prior × {0.5, 1, 1.5}, reporting UCM% and
     LT%.
   - The **donor audit** (Stage A → B).
   - The UCM% labelled as **assumption-driven** when its contraction < 0.2.
4. **Benefit.** The single most influential number in the method (UCM%,
   which scales every long-term contribution) becomes transparent and
   bounded.
5. **Cost.** Two to three extra nested fits; reporting.
6. **Essential.**

### U12 — Extract the UCM from base sales (optional)

1. **Current.** The UCM is extracted from total sales.
2. **Limitation.** Sustained short-term media and trade effects (long flights,
   promotion periods) leak into the smooth trend. The long-term model can then
   credit them to long-term media — **double counting** short-term effects as
   long-term. HP removes swings under ~26 periods, not long flights.
3. **Proposal.** When a short-term decomposition covers all of H, extract the
   UCM from *sales − short-term incremental media and trade*: the model's base
   (deck S13: "impact onto the underlying base sales"). Otherwise, report the
   correlation of τ with short-term media volume over N.
4. **Benefit.** Less risk of double counting.
5. **Cost.** Needs a full-window short-term decomposition, which is rarely
   available for the retrofit years. A different trend from the vendor's.
6. **Optional.**

### U13 — Regional consistency (decision)

1. **Current.** Separate univariate UCMs per region and for "All". The regional
   trends do not sum to All's: 14.00M vs 14.64M in Jul 2021 [C].
2. **Limitation.** Mixing levels (a national long-term model with regional
   nesting) breaks the additivity of LT%.
3. **Proposal.**
   - The **same region list** in S2, S3 and S4; regional UCMs.
   - National numbers = sums of regional results (§6.9).
   - A national UCM only when every model is national.
   - Optional later: a pooled (hierarchical) Bayesian local linear trend
     (`pymc_extras.statespace`) to share the variances across regions.
4. **Benefit.** Additive, consistent reporting.
5. **Cost.** None for the default.
6. **Decision** (D-06).

### U14 — Back-cast = burn-in only (essential: a rule)

1. **Current.** Six months back-cast (time-reversed ETS) are appended before
   the data. They may enter the UCM input, and possibly the long-term window.
2. **Limitation.**
   - A back-cast holds an extrapolated seasonal pattern and level but **no
     media response**.
   - Media cannot be back-cast. Regressing a back-cast trend on media there
     biases effects towards zero.
3. **Proposal.** Back-cast periods are used only by S2; dropped after.
   Configurable length and method (D-01).
4. **Benefit.** No invented data in any model.
5. **Cost.** None.
6. **Essential.**

### U15 — The wave tail beyond the data (optional)

1. **Current.** The deck notes 13% / 47% of Year 3 / 4 effects are
   unobserved (S4). Truncation is handled by reporting only Years 1–2.
2. **Limitation.** Year 2's own tail is truncated with 3–3.5 years of data,
   silently.
3. **Proposal.** Project each wave forward with zero further activity through
   the same transformation (§12.5). Report "% unobserved" per wave.
4. **Benefit.** An honest Year-2 wave; a usable Year 2 at 3 years of data.
5. **Cost.** Needs raw activity and parameters, as waves do. The projection
   assumes κ and b stay at their last values.
6. **Optional.**

### U16 — Vendor-parity mode (essential for validation)

1. **Current.** The vendor / team notebooks are the reference.
2. **Limitation.** Without exact reproduction, nobody can tell an improvement
   from a bug.
3. **Proposal.** `lte.ucm_mode: parity` reproduces the notebooks bit for bit:
   raw scale, approximate diffuse start, no frequency, Powell, `Level + Trend`,
   HP λ = 300. Use it for acceptance (§13.4) and side-by-side reports.
4. **Benefit.** Trust; a controlled migration.
5. **Cost.** Low.
6. **Essential.**

### U17 — Column definitions (optional)

1. **Current.**
   - `Trend` is the slope.
   - "Original UCM Trend" = Level + slope = next period's level.
   - `Irregular` subtracts the slope [C].
2. **Limitation.** Easy to misread.
3. **Proposal.** Keep the vendor names in parity outputs. Add `slope`,
   `irregular_model` (S − μ − ψ) and a plain-language column guide in
   `OUTPUTS_GUIDE`.
4. **Benefit.** Clarity.
5. **Cost.** Trivial.
6. **Optional.**

---

## 8. Data requirements and contracts

### 8.1 Inputs

| # | Input | Required | Columns / content | Rules |
|---|---|---|---|---|
| 1 | **LTE datacube** (one per BMC) | yes | `date`, `region`, `dv` (sales volume), **short-term columns** (the primary model's, unchanged), **long-term columns** (identified by a suffix, default `_lt`, or listed in `lte.long_term_features`), long-term drivers | long format, one row per region × date; weekly or monthly; ≥ 3 years; the same sales measure over all of H; no NaN (zero-fill inactivity upstream, as `prepare_data` requires [V `data_prep.py:225-229`]); short-term columns transformed **continuously** over the full history |
| 2 | **Primary model reference** | yes | a codebase 1 primary run folder (its `coefficient_report.csv`, `panel_summary.csv`, `feature_scaling_stats.csv`, prior file), **or** a vendor contribution mapping file (the existing format) | covers window N (D-04) |
| 3 | **Long-term share priors** | yes (or a long-term prior CSV) | the share file format (`section, pillar, pillar_share_pct, variable, spend, variable_share_pct[, sign_constraint]`) with deck S19's ranges | sections `media`, `expert`, `baseline` |
| 4 | **History file** | `historical` retrofit mode only | `region, date, feature, volume` (+ `pillar`); a codebase 1 `contribution_timeseries.csv` is accepted as-is | covers R; lever mapping via the existing mapping-file reader where names differ (D-23) |
| 5 | **Wave inputs** | waves only | raw activity per channel (`region, date, channel, activity`); long-term parameters per channel (`channel, alpha, K, S, K_basis, lambda, lag, order`); optional short-term parameters | must reproduce the supplied `_lt` columns within 1e-6 relative (§12.3) |
| 6 | **Spend and price** | ROI only (opt-in) | spend per channel × year (× region); a price (value per volume unit) per region × year | D-13 |

### 8.2 The media representation contract

Restating §2.7 as rules:

- **(a)** Codebase 1 never transforms model inputs.
- **(b)** Long-term and short-term columns of one channel are different
  columns.
- **(c)** Long-term columns are the long-term chain (SES → Hill → adstock
  [+ lag]) with the long-term parameters (guide §14 ranges as checks, not
  defaults).
- **(d)** Short-term columns are the primary model's.
- **(e)** Raw activity enters only the wave attribution, after the
  reproduction check.
- **(f)** Trade and promotion columns are refused in the long-term model
  (deck S18).

### 8.3 Intermediate artefacts

| Stage | File | Columns / content |
|---|---|---|
| S0 | `00_feasibility/window_plan.json` | cadence, n, B / H / N / R date ranges, reported years and their definition |
| S0 | `00_feasibility/feasibility_report.csv` | check, level (block / warn / info), region, column, detail |
| S1 | `01_ucm/backcast.csv` | region, date, value, `is_backcast`, method |
| S2 | `01_ucm/ucm_components.csv` | region, date, `is_backcast`, observed, level, slope (vendor `Trend`), cycle, [seasonal], irregular (vendor), irregular_model, original_ucm_trend, refined_cycle, **refined_ucm_trend** (τ) |
| S2 | `01_ucm/ucm_parameters.csv` | region, mode, spec, σ²_ε, σ²_ζ, cycle period / amplitude, seasonal (y/n, harmonics), log-likelihood, AIC, optimiser, converged, λ, cut-off |
| S2 | `01_ucm/ucm_diagnostics.csv` | region, test, statistic, p-value, verdict (Ljung–Box, normality, heteroskedasticity, start-up, permanent component, start-points agree) |
| S2 | `01_ucm/lte_long_term_datacube.csv` | date, region, `dv` = τ, long-term columns, drivers, `media_residual`; H only |
| S2 | `01_ucm/lte_nested_datacube.csv` | date, region, `dv` = sales, short-term columns, `ucm` = τ; N only |
| S2 | `01_ucm/lte_retrofit_datacube.csv` | the same columns as nested; R only |
| S3, S4 | standard codebase 1 trees | as documented in `docs/OUTPUTS_GUIDE.md` |
| S4 | `03_nested/donor_audit.csv` | feature, role, contribution A, contribution B, Δ, Δ% |
| S5 | `04_lte/ucm_scalar.csv` | region, β^u (median, HDI), d^S, s^u, κ (median, HDI), check `max\|U/τ − κ\|` |
| S5 | `04_lte/retrofit_timeseries.csv` | region, date, feature, pillar, group, volume, source (history / coefficients / ucm / allocation) |
| S5 | `04_lte/lte_timeseries.csv` | region, date, window (R / N), lever, feature, `st_volume`, `lt_volume`, `total_volume`; rows `__ucm__`, `__lt_baseline__`, `__actual__` |
| S5 | `04_lte/lte_summary.csv` | period, region (incl. `__portfolio__`), lever, `st_pct`, `lt_pct`, `total_pct`, volumes — FR PDX S11 / S21 layout |
| S5 | `04_lte/lte_multipliers.csv` | channel, window, ST, LT, total, multiplier (window-based); overall |
| S5 | `04_lte/lte_roi.csv` (opt-in) | channel, spend, value ST / LT / total, ROI ST / LT / total |
| S5 | `04_lte/lte_reconciliation.csv` | the chain sales → short-term components → UCM → long-term redistribution → final, per region × period |
| S6 | `04_lte/lte_waves.csv` | channel, wave (activity year), year, short_pct, long_pct, volume |
| S6 | `04_lte/lte_wave_tail.csv` | channel, wave, observed_pct, projected_unobserved_pct |
| all | `lte_manifest.json` | input hashes (sha256), codebase version, statsmodels / PyMC versions, seeds, settings per stage, timings |

### 8.4 Validation rules

**Blocking:**

- dates irregular or duplicated;
- a region missing from one input;
- `dv` ≤ 0 or NaN;
- fewer than 3 years;
- the nested window not covered by the primary model;
- a long-term column with NaN;
- trade or promotion in the long-term feature list;
- τ ≤ 0 anywhere in H;
- κ not constant over N (> 1e-6 relative): `ucm` centred or re-referenced;
- the wave reproduction check failing.

**Warnings:**

- a level shift in sales: a step > 15% between the local means either side of
  a date — the FR PDX restatement pattern;
- sparse long-term channel (< 8 active periods a year);
- cycle dropped;
- no permanent component;
- start-up check failing in parity mode;
- U > P⁺ in a retrofit week;
- |r / P⁺| > 20%;
- Year 2 truncated;
- contraction < 0.2 for `ucm` or a long-term media coefficient;
- Stage A not reproducing the primary within 3%.

Each warning is a new `warnings_report._RULES` category (§9).

---

## 9. Code reuse and change inventory

### 9.1 Reused unchanged [V]

- `run_pipeline.run` and `settings.run_from_yaml`: both models.
- `modelling/model.py`, `fit.py` and all of `checks/` (diagnostics,
  assumptions, warnings, CV): both models.
- `reporting/outputs.py`, `reconciliation.py`, `benchmark.py`: both models.
  `benchmark_comparison.xlsx` doubles as the Stage A ↔ primary check.
- `data/prior_builder.py`: `priors_from_shares` for long-term priors (deck S19
  shares); `priors_from_mapping` for LOCKED priors from vendor contributions;
  `check_units`.
- `data/mapping.py`: lever / variable mapping for the history file.
- `config.PeriodPolicy` / `resolve_period_plan`: cadence, MAT and holdout
  inside each model.
- `app_job.run_folder`, `merge_config`, `type_settings`, `_read_request`,
  `copy_tree`, `tee_log`: stage runs.

### 9.2 Extended

| File | Change |
|---|---|
| `mmm/core/config.py` | `LTEConfig` dataclass (stage, windows, back-cast, UCM, λ / cut-off, mode, retrofit mode, denominators, wave settings, file keys) and its `VALID_*` enums. `resolve_lte_windows(dates, cadence, lte_cfg)` beside `resolve_period_plan` |
| `mmm/core/settings.py` | register `lte` in `SECTIONS`, plus `HELP` lines (test_v11), `CHOICES` (test_v19) and `DATA_KEYS` additions (`history_file`, `wave_file`, `spend_file`); `config_schema()` then carries the section to the app |
| `mmm/app_job.py` | read `lte_stage` / `lte_inputs` from `run_request.json` (`_read_request` already reads that file [V `app_job.py:417`]); dispatch to `mmm.lte.stages`; resolve upstream run names inside the group, following `renames.json` and the `Results Reported/` / `Archived/` subfolders; publish as today |
| `mmm/checks/warnings_report.py` | new `_RULES`: `lte_window`, `ucm_startup`, `ucm_cycle`, `ucm_no_permanent_component`, `ucm_diagnostics`, `lte_scalar`, `lte_retrofit`, `lte_wave_truncated`, `lte_assumption_driven`, `lte_donor_audit` |
| `modelling_types.yaml` | `LTE:` may carry team-level `lte:` settings (e.g. `trend_cutoff_months`) once `lte` is a section |
| `config.yaml` | the `lte:` section with defaults and help, in the same style |
| `requirements.txt` | `statsmodels` (unpinned) |
| `docs/` | §9.5 |

### 9.3 New: `mmm/lte/`

| Module | Content | Approx. size |
|---|---|---|
| `__init__.py` | public names | — |
| `windows.py` | window plan, feasibility checks (S0) | 200 |
| `backcast.py` | time-reversed ETS; average-year fill (S1) | 120 |
| `ucm.py` | per-region fit (parity and improved), HP, cut-off ↔ λ, diagnostics, plots, component CSVs, datacube writers (S2) | 450 |
| `presets.py` | the long-term and nested stage presets, with invariant checks (e.g. refuse a centred `ucm`) | 80 |
| `priors.py` | long-term priors from deck S19 ranges via `priors_from_shares`; nested LOCKED / DONOR / CANDIDATE from a primary run (with scale conversion, §6.5) or a mapping file; Stage A / B helpers; donor audit | 300 |
| `combine.py` | κ, retrofit (two modes), L, final decomposition, aggregates, multipliers, ROI (opt-in), reconciliation (S5) | 450 |
| `transforms.py` | numpy SES (first non-zero start), Hill, adstock, lag, origin-year decomposition. **Wave attribution only**. Conventions checked against codebase 2's `transforms.py` (`hill_np`, `apply_adstock_np`) but not imported from it (codebase 2 is not current; the direction of travel is codebase 1 → 2) | 150 |
| `waves.py` | wave tables, tail projection (S6) | 250 |
| `report.py` | FR PDX-layout tables, charts, `lte_report.md` (S7) | 300 |
| `pipeline.py` / `__main__.py` | `run_lte(config, stages=..., resume=...)`; `python -m mmm.lte config.yaml [--stage s2]`; manifest | 250 |

### 9.4 Deliberately not changed

- `model.py`, `fit.py`, `data_prep.py`, `outputs.py`, `reconciliation.py`.
  - Both LTE models are ordinary codebase 1 models; the LTE logic lives
    before and after them.
  - That keeps every invariant and test valid, and keeps `test_v24`'s real-PyMC
    audit meaningful.
- **Two possible later core changes**, each a separate decision:
  - AR(1) errors (U10);
  - a `run.date_start/date_end` window filter, which would replace the
    window-cut datacubes.

### 9.5 Documentation

| Doc | Change |
|---|---|
| **New** `docs/LTE_GUIDE.md` | method, stages, settings, reading the outputs, decisions; MMM language throughout |
| `docs/OUTPUTS_GUIDE.md` | every LTE file and column |
| `docs/CONFIG_GUIDE.md` | the `lte:` keys |
| `docs/CHECKS_GUIDE.md` | the UCM and LTE checks |
| `docs/METHODOLOGY.md` | §2b / §2c cross-reference to LTE nesting |
| `docs/PROJECT_STRUCTURE.md`, `README.md` | `mmm/lte/` |
| `docs/cases/` | the first real LTE study's problems |
| `CLAUDE.md` (codebase 1 and the build folder) | module map, invariants |

---

## 10. Model execution and orchestration

### 10.1 Order and dependencies

| Stage | Needs | Can run in parallel with |
|---|---|---|
| S0 → S1 → S2 | datacube | — |
| S3 long-term model | S2 | **S4** |
| S4 nested model (A, then B) | S2, primary reference | **S3** |
| S5 combine | S3, S4, (history) | — |
| S6 waves | S5, wave inputs | — |
| S7 report | S5, (S6) | — |

### 10.2 Two ways to run

1. **Hand runs and tests:**
   - `python -m mmm.lte config.yaml` runs S0–S7 into `outputs/<study>/{00_feasibility, 01_ucm, 02_long_term, 03_nested, 04_lte}`.
   - `--stage s3` runs one stage; `--resume` skips stages whose inputs are
     unchanged (by manifest hash).
2. **The web app:**
   - Each stage is **its own run folder** in one LTE group
     (`<BMC>/<period> LTE/<run>`): ① UCM, ② long-term, ③ nested, ④ results.
   - Each later run names its upstream runs (§14).
   - It matches the deck's iterative method: re-run ③ with a new `ucm` prior,
     then ④, without touching ①–②.

### 10.3 Configuration precedence for a stage run [I]

```
team config.yaml  <  modelling type "LTE" settings  <  the stage preset (presets.py)  <  the run's own values
```

- The stage presets encode **methodological invariants**: the long-term
  model's linear trendline; a non-centred, measured-from-zero `ucm`.
- A run that overrides one gets a `lte_preset_override` warning, or is refused
  where it breaks §6 (a centred `ucm`).

### 10.4 Sampling

- Both models use codebase 1's NUTS settings (`numpyro`, `vectorized` on GPU,
  `target_accept` 0.92).
- **The long-term model** is small: ~10–20 features, smooth `dv`.
  - Expect fast sampling, but strong posterior correlation between the
    intercept, the trend and slow media.
  - Check `posterior_correlation.csv`; `dense_mass: true` is the lever when
    tree depth saturates.
- **The nested model** is a Secondary-style run, the same as the v9
  experience.
- Seeds are fixed per stage and recorded in the manifest.

### 10.5 Reproducibility

`lte_manifest.json` records:

- the sha256 of every input;
- the codebase version (with `mmm.check_sync()` passing);
- statsmodels, PyMC, NumPyro and JAX versions;
- the resolved settings per stage;
- seeds and timings.

Each model stage also writes `01_data/resolved_config.yaml` (existing). A study
is reproducible from the manifest plus its inputs.

### 10.6 Error handling

| Stage | Failure | Behaviour |
|---|---|---|
| S0 | blocking data check | stop; `feasibility_report.csv` names the column and region |
| S2 | optimiser not converged / start-points disagree | retry: 3 starts, then L-BFGS; then warn and continue with the best log-likelihood, flagged |
| S2 | τ ≤ 0 | stop (`ucm` would flip sign) |
| S3 / S4 | convergence failure | codebase 1's `on_convergence_failure` (`warn` / `fail`) |
| S4 | Stage A off the primary by > 3% | warning plus the benchmark sheet; stop on > 10% (the LOCKED priors do not reproduce the primary) |
| S5 | κ not constant; inputs from different codebase versions, regions or windows | stop with the difference named |
| S6 | reproduction check fails | stop waves only; S5 outputs stand |
| any | — | outputs published even on failure, as `app_job` does today |

### 10.7 The UCM-prior loop, with a stopping rule

The deck (S22 step 5) iterates the `ucm` prior "after reviewing the model
results". METHODOLOGY §2d shows why repeated corrections oscillate. The rule
[I]:

1. Run Stage B with the CANDIDATE prior (wide).
2. Read the `ucm` contraction c (`use_for_delta` row).
   - **c ≥ 0.5**: the data has a view. Accept it.
   - **0.2 ≤ c < 0.5**: at most one correction toward the target share:
     `new = old × R^(1/(1−c))` (METHODOLOGY §2b step 3).
   - **c < 0.2**: the share is the prior. Pin it (`global_prior_sd` 0.02–0.05)
     at the agreed share, and label UCM% as assumption-driven (U11).
3. Never a third run to chase a number. Report the sensitivity sweep instead.

---

## 11. Contribution calculations and reconciliation

### 11.1 The chain, per region × week

| Step | Window | Quantity | Formula (§6) | Source file |
|---|---|---|---|---|
| 1 | N | short-term components C (incl. U) | nested model | `03_nested/.../contribution_timeseries.csv` |
| 2 | N | κ_g | U / τ (§6.5) | `ucm_scalar.csv` |
| 3 | R | U = κ·τ | §6.6 | `retrofit_timeseries.csv` |
| 4 | R | short-term components C | history or coefficients (§6.6) | `retrofit_timeseries.csv` |
| 5 | R | positive-baseline allocation | §6.6 | `retrofit_timeseries.csv` |
| 6 | H | c^τ, τ | long-term model | `02_long_term/.../contribution_timeseries.csv`, S2 |
| 7 | H | L = κ·c^τ; L_base = U − ΣL | §6.7 | `lte_timeseries.csv` |
| 8 | H | Final = C + L; UCM removed | §6.8 | `lte_timeseries.csv` |
| 9 | periods | ST%, LT%, Total%, UCM%, multipliers | §6.9–6.10 | `lte_summary.csv`, `lte_multipliers.csv` |

### 11.2 What each block means

| Block | Meaning |
|---|---|
| **Short-term** | the nested model's incremental contributions in N; history or coefficients in R. **Equal to the primary model's** for every LOCKED variable (Stage A check) |
| **Long-term** | L — the part of the UCM's sales contribution that the long-term model attributes to each media / expert lever |
| **Baseline** | the nested baseline minus the UCM (positive donors after the UCM took its share), plus the long-term model's baseline share of U (L_base) |
| **Retrofit** | the earlier years built from the scalar, with the UCM taken proportionally from the positive baseline |
| **Final** | ST + LT per lever; the UCM row redistributed; Σ = actual sales |

### 11.3 Worked checks against the reference material [C]

**FR PDX S21 rebuilt from S11 (short-term without UCM), S20 (long-term shares
2022–25) and UCM 20.3%:**

| Lever | Short-term (nested) | + share^τ × UCM% | = computed | FR PDX S21 |
|---|---|---|---|---|
| Baseline | 75.5 | 84.7 × 0.203 = 17.19 | 92.69 | 92.7 |
| TV | 2.1 | 10.0 × 0.203 = 2.03 | 4.13 | 4.2 |
| Expert | 0.6 | 0.6 × 0.203 = 0.12 | 0.72 | 0.7 |
| Digital Media | 1.3 | 4.8 × 0.203 = 0.97 | 2.27 | 2.3 |
| Trade | 0.1 | 0 | 0.1 | 0.1 |
| Total Media (TV + Digital) | 3.4 | 14.8 × 0.203 = 3.00 | 6.40 | 6.5 (S11: 6.4) |

- **The short-term column without UCM:** 95.8 = 75.5 + 20.3 (S11 vs S21). The
  UCM came only out of the baseline.
- **Deck S25:** LT_TV = 2,193 / 253,063 × 51,363 = **445.10** units.
- **Deck S21:** (11 + 92) / (11 + 92 + 6 + 30 + 6) = **71%**. The formula's
  denominator includes the baseline; the example's does not (deck D5).
- **Deck S24:** the scalar 19% applied to the 2020–21 trendline.

### 11.4 Invariants and reconciliation files

| # | Invariant | Tolerance |
|---|---|---|
| I1 | Nested window: components + median gap + residual = actual (codebase 1) | exact |
| I2 | κ_g constant over N | 1e-6 relative |
| I3 | Long-term model: Σ_i c^τ + c^τ_base = τ (residual in base) | exact |
| I4 | Σ_i L_{g,t,i} + L_{g,t,base} = U_{g,t} | exact |
| I5 | Retrofit window: Σ components' = actual sales | exact (by allocation) |
| I6 | Short-term contribution of every LOCKED variable in N = Stage A / primary | ≤ 3% (warning), ≤ 10% (stop) |
| I7 | Final: Σ levers = actual sales, every region × week | exact |
| I8 | Aggregate identity (§6.9) per region | 1e-9 relative |
| I9 | Waves: Σ_y L_{g,t,i,y} = L_{g,t,i}; each wave's shares sum to 100% | exact |
| I10 | `lte_summary.csv` percentages sum to 100 per period × region | exact |

`lte_reconciliation.csv` prints the chain (sales → short-term components → UCM
→ long-term redistribution → final) per region × period.
`lte_contribution_math.csv` prints κ_g, Σc^τ_i, L_i, C_i and Final_i side by
side, so any reported number is re-derivable by hand. This is codebase 1's
"show me the arithmetic" rule (`contribution_math.csv`).

### 11.5 Medians and draws — two arithmetics, kept apart

- **Weekly LTE tables:** built from weekly **medians** of each model's
  components. They add up exactly, with explicit gap rows.
- **Optional intervals:** per-draw computation (§6.12) gives `lte_totals.csv`,
  with the median and 90% HDI of each lever's window total. Its parts **do not
  add**.
- **The two are never mixed** (codebase 1 invariant 4).

### 11.6 Reporting periods and the two multipliers [I]

| | |
|---|---|
| **Periods** | `lte_summary.csv` reports per calendar year (FR PDX: CY 2022, CY 2023, CY 2024, 2025-H1), the reported window (Years 1–2) and the full H. D-11 decides calendar vs MAT; codebase 1's own `period_labels` (MAT) stays in the model trees |
| **Window-based multiplier** | (ST + LT measured **inside** the window) ÷ ST — FR PDX S11–S13. TV: 2.03 [S] |
| **Activity-based multiplier** | (all effects of the window's **activity**, including later spill-over) ÷ ST, from the waves. FR PDX S15's TV waves imply ≈2.4 (100/41 and 100/42) [C] |
| **They differ, and S13's 2.03 is the window-based one** | a quick reconstruction from the wave shares, assuming wave volume ∝ spend, gives ≈2.1 window-based [I] |
| **Proposal** | report both, labelled (D-14) |

---

## 12. Wave analysis design

### 12.1 What the wave table means [S]

FR PDX S15, S25 and deck S26. For one channel and one **activity year** (a
"wave"), the effect is split into:

- **short-term**, in its own year;
- **long-term**, in its own year;
- **long-term in each later year** ("spill-over").

Together they make 100%. Example — TV 2022: 41% short-term, 37% long-term in
2022, 20% in 2023, 2% in 2024. Short-term is booked to the activity year only.

### 12.2 Can it be derived from the existing transformed media? No [I]

- The long-term column m̃_t mixes the stock built by every earlier period's
  activity. The model sees only the sum.
- Splitting it by origin year needs the **raw activity** and the
  **transformation**:
  - for the linear steps (SES, adstock, lag), with the parameters, the split is
    exact;
  - Hill is nonlinear, so the split through it needs a convention.
- Without the raw activity, a purely linear chain (no Hill) can in principle be
  **deconvolved**. With Hill and unknown parameters it cannot.
- **So the media transformation pipeline must retain intermediate data.**
  Either:
  - **(a)** supply raw activity plus the long-term parameters (and the order,
    D-02), and codebase 1 reproduces the chain per origin year; or
  - **(b)** the upstream tool exports **per-wave long-term columns**
    (`tv_lt__w2022`, …) that sum to the long-term column.

  (a) is preferred: one source of truth, checked by reproduction.

### 12.3 Origin-year decomposition

For channel i, region g, activity year y, with the activity mask
`a^{(y)}_t = a_t·1[t ∈ y]`:

```
SES (linear):     e^{(y)}_t = α·a^{(y)}_t + (1−α)·e^{(y)}_{t−1}          ⇒ Σ_y e^{(y)}_t = e_t   (exact)
Hill (nonlinear): h^{(y)}_t = h_t · e^{(y)}_t / e_t                        proportional "share of stock"
                                                                           ⇒ Σ_y h^{(y)}_t = h_t   (exact by construction)
Adstock (linear): m̃^{(y)}_t = h^{(y)}_{t−lag} + λ·m̃^{(y)}_{t−1}          ⇒ Σ_y m̃^{(y)}_t = m̃_t   (exact)
```

**The SES start convention** ("first non-zero value", deck S14) is assigned to
the year of that first activity.

**Reproduction check (blocking):** the full chain on a_t must reproduce the
supplied `_lt` column within 1e-6 relative. Otherwise the parameters or the
order are not the ones used.

**For the other order** (SES → adstock → Hill): split linearly through SES and
adstock, then proportionally through Hill. The same code with the steps
reordered.

**Alternatives to "share of stock" for Hill:**

- *marginal removal* (x − x without year y), normalised;
- Shapley over years.

Share of stock is chosen because it is exact, cheap, order-free across years,
and equals the linear answer when Hill is near-linear. **The convention is
documented in every output.**

### 12.4 From long-term inputs to sales, by wave

```
c^τ_{g,t,i,y} = c^τ_{g,t,i} · m̃^{(y)}_{g,t,i} / m̃_{g,t,i}        (the long-term model is linear in m̃: exact)
L_{g,t,i,y}   = κ_g · c^τ_{g,t,i,y}
LT_{i,y→Y}    = Σ_{g} Σ_{t∈Y} L_{g,t,i,y}                         Y = y, y+1, … (calendar years of H)
ST_{i,y}      = Σ_{g} Σ_{t∈y} C_{g,t,i}                            booked to the activity year (FR PDX); D-24
W_{i,y}       = ST_{i,y} + Σ_Y LT_{i,y→Y}  [+ tail]
table cell    = ST_{i,y} / W_{i,y}  and  LT_{i,y→Y} / W_{i,y}
```

- **Option (D-24):** if the short-term raw activity and parameters are
  supplied, decompose the short-term adstock by origin too. A December flight's
  short-term carry-over into January then moves to its own wave.
- The FR PDX convention, 0% short-term in later years, is the default.

### 12.5 The tail beyond the data (U15)

1. Propagate m̃^{(y)} past the last date with zero activity, which is exact
   for the wave since its activity is over.
2. Convert with the last year's κ_g and b_{g,i}.
3. Sum to a horizon where the remaining stock is below 1%.
4. Report `projected_unobserved_pct` per wave.

This is the deck S4 / S28 "Future — yet to be observed" share, made explicit.
It is labelled a projection, under the assumption that κ and b stay at their
last values.

### 12.6 Outputs and dependencies

- **Outputs:** `lte_waves.csv` (the FR PDX S15 layout), `lte_wave_tail.csv`, a
  chart per channel (the deck S4 picture with the real numbers), and the
  activity-based multipliers (§11.6).
- **Dependencies:** S5 (κ, L, C) plus the wave inputs.
  - The **origin-year decomposition of m̃ needs no model**, so it can run
    alongside S2 / S3 ("alongside UCM modelling", as the brief allows).
  - Only the conversion to sales waits for S5.

### 12.7 If the raw activity and parameters cannot be supplied

- **Fallback 1:** upstream per-wave columns, (b) above.
- **Fallback 2:** report long-term contributions by **calendar year of
  effect**, not by activity year. These are available from S5 without waves.
  Say plainly that spill-over by activity year is not measured.
- Do **not** approximate waves from the transformed columns.

---

## 13. Testing and validation strategy

### 13.1 Unit tests

These need no PyMC. Statsmodels parts SKIP without it, as `test_v24` does for
PyMC.

| Area | What it checks |
|---|---|
| UCM identities | Irregular = S − L − T − C; Original = L + T; Original(t) = Level(t+1); refined cycle + refined trend = original — on the transcribed `ucm output` rows |
| HP | λ ↔ cut-off round trip; P(300) = 26.1 |
| Back-cast | reversal and re-reversal; length; flags; the average-year method |
| Window plan | 4 / 3.5 / 3 / < 3 years, weekly and monthly; reported years; calendar vs MAT |
| Transforms | SES first-non-zero start; Hill; adstock; lag; origin-year sums exact; Hill share-of-stock sums exact |
| κ | constant over N for an uncentred `ucm`; the run stops for a centred one |
| Retrofit | both modes; allocation keeps the totals; flags fire |
| Combination | I3–I10 on synthetic frames shaped like `contribution_timeseries.csv` |
| Priors | LOCKED / DONOR / CANDIDATE assignment; scale conversion (§6.5); share-file conversion of deck S19 ranges |

### 13.2 Golden tests (reference numbers) [C]

| # | Test |
|---|---|
| G1 | FR PDX S21 rebuilt from S11 + S20 + 20.3%, ±0.1 pp on all 7 rows |
| G2 | Deck S25 = 445.10 |
| G3 | Deck S21 = 71.0% |
| G4 | FR PDX S13: channel multipliers, and overall 1.90 = 7.237 / 3.811 |
| G5 | FR PDX S12 totals = S13 ROI × spend |
| G6 | FR PDX S15: wave rows sum to 100% (±1) |
| G7 | `ucm output` identities |

### 13.3 Synthetic end-to-end with known truth

Simulate a 4-year weekly panel, 3 regions:

- a base following a local linear trend, plus a **known long-term media
  effect** (raw activity → the long-term chain → b_i);
- seasonality;
- **known short-term media effects**: short adstock + Hill;
- noise.

Then run S0–S6 and check:

- **(a)** every invariant;
- **(b)** improved mode has no start-up artefact; parity mode shows it;
- **(c)** LT%_i recovered within an agreed band (e.g. ±25% relative — the
  identification limits of U10 / U11 are real, and the test documents them);
- **(d)** wave shares match the simulated truth;
- **(e)** the tail projection matches the simulated continuation.

The PyMC part runs in a `test_v24`-style real suite (SKIP without PyMC); the
rest uses stub models.

### 13.4 Parity with the vendor notebooks

With the **original input series** (to be requested — the full 182 France
weeks and Colombia's 54 months × 13), parity mode must reproduce:

- `ucm output` to 1e-6 relative;
- the region-based heads (13 regions × 5 rows) printed in the screenshots.

The back-cast is compared with the sheet's 26 values; exact equality is not
expected (D-19).

### 13.5 Historical backtests and stability

| Test | What it shows |
|---|---|
| **FR PDX re-run** | with the team's data, compare with S11 / S12 / S13 / S15 / S19 / S20 / S21 within an agreed tolerance (proposed: ±0.3 pp on shares, ±0.1 on multipliers) |
| **Refresh stability** | drop or add one quarter; Year-1 LT% must move less than a set band; τ revisions reported (U8) |
| **Sensitivity grid** (U9) and **UCM prior sweep** (U11) | the ranges go in the report |
| **CV** | codebase 1's CV on both models, read for the nested model mainly; long-term CV on a smooth `dv` is weakly informative (U10) |

### 13.6 Edge cases

- Monthly cadence; a single region.
- A region missing weeks; duplicated dates.
- A long-term channel with no activity in Year 1; new channels mid-history.
- A trade / promotion column wrongly listed for the long-term model.
- τ near zero or negative (small regions, parity mode).
- A primary model with an intercept or trend (D-16).
- A nested window not matching the primary.
- A history file with different lever names; a restated history scale (FR PDX
  S23–24).
- 3-year data; Year 2 truncated.
- Hill near-linear (wave sums still exact); λ = 0 adstock; α = 1 SES.
- Statsmodels not installed (clean SKIP / refusal message).

### 13.7 Numerical invariants

I1–I10 (§11.4), plus the UCM identities (§13.1) and the reproduction check
(§12.3). Each is asserted in tests **and** re-checked at run time, written to
the LTE warnings.

### 13.8 Where the tests live

New suites in `../tests/`, registered in `run_all.py`:

| Suite | Covers |
|---|---|
| `test_v25_lte_ucm` | statsmodels; SKIP without it |
| `test_v26_lte_combine` | pure pandas |
| `test_v27_lte_waves` | numpy |
| `test_v28_lte_pipeline` | orchestrator, presets, `app_job` dispatch, warning rules; stubbed models |
| `test_v29_lte_real` | real PyMC + statsmodels end to end; SKIP |
| web | additions to `test_v20` / `v21` / `v22`; LTE fixtures in `fixture_run_outputs.py` |

Locally: `pip install --target <dir> statsmodels` (and PyMC for v29), as for
`test_v24` (codebase 1 `CLAUDE.md` §8.2).

---

## 14. Web application changes

### 14.1 Principles (kept) [R]

- The app stays a **frontend**. It loads codebase 1 live from the workspace,
  with no copy.
- The job never edits `config.yaml`.
- Who may do what is `app_access.yaml`.
- Every new backend function bumps `MIN_CODEBASE`.
- Every new view is drawn from CSVs that codebase 1 writes, with Plotly charts
  and a table twin.

### 14.2 An LTE study is a group of stage runs

- The group folder is already `<BMC>/<period> <modelling type>/`, so an LTE
  study is `…/2022Q1-2025Q2 LTE/`.
- Inside it, runs have a **stage kind**: ① UCM, ② long-term, ③ nested, ④ LTE
  results.
- `run_request.json` gains `lte_stage` and `lte_inputs`
  (`{"ucm_run": …, "long_term_run": …, "nested_run": …, "primary_run": "<bmc>/<group>/<run>"}`).
  The job already reads this file. **No new job parameters are needed**, so
  `demo.ipynb` is unchanged.
- **Marking the reported run** works as today; the reported run of an LTE group
  is a ④ run.
- Deleting or renaming a run referenced by a later stage is **refused**, or
  followed through `renames.json` (the existing mechanism).

### 14.3 Page by page

| Page | Change |
|---|---|
| ① Project | when the type is LTE: a **stage selector**; for ②–④, **pickers of the upstream runs** (the group's finished runs of the required kind; ③ also the primary run, any group of the BMC); the window plan shown for a ① run |
| ② Data | ① uploads the LTE datacube, with the feasibility summary (blocking errors only, as the app does today) and the long-term column detection shown. ②/③ default to the datacube ① generated (no upload); an override is allowed |
| ③ Settings | the `lte:` section appears from `config_schema()`; which keys analysts may edit is `app_access.yaml` (**the team's call**); the stage preset's values shown read-only in the difference table ("set by the LTE stage") |
| ④ Mapping / Share | ② uses the **share file** for the deck S19 contribution priors; ③ uses the mapping file when the primary is a vendor model |
| ⑤ Prior | ③: generate the **LOCKED / DONOR / CANDIDATE** prior with a role column in the editor (the CSV is unchanged — the role is derived); the `ucm` row highlighted; the scale conversion shown when the primary's dv scale differs. ②: generate from shares |
| ⑥ Run | unchanged mechanics; the run panel shows the stage and its upstream runs; ④ also accepts the history / wave / spend files (three optional uploads, new folders `History/`, `Waves/`, `Spend/` under the run folder) |
| ⑦ Runs and results | a filter by stage; **stage-specific views** (§14.4) |

### 14.4 New result views (in `web/src/charts.py`)

| Stage | Views |
|---|---|
| ① UCM | the 8-panel components chart per region (the vendor layout); the diagnostics table; the start-up check; the cut-off and λ |
| ② Long-term | the standard views, plus **share of the UCM trend** by lever (FR PDX S20 table) and actual vs predicted (S19) |
| ③ Nested | the standard views, plus **κ per region** and the **donor audit** (A → B) |
| ④ Results | **total decomposition**, short-term vs long + short (S11 / S21: table and stacked area); **long vs short per channel** (S12); **multipliers**, both definitions, with benchmark columns when provided (S13–S14); **waves** (S15 table plus the deck S4 picture); **retrofit chart** (deck S23: retrofit vs core-model period); the reconciliation chain |

### 14.5 Backend contract (`web/src/`)

| File | Change |
|---|---|
| `codebase.py` | new heavy calls through the worker pool: `lte_preview(datacube)` (window plan + feasibility); `lte_prior_preview(primary_run, share_file)`. New layout constants: stage kinds, folder names. Bump `MIN_CODEBASE` |
| `projects.py` | stage kind and upstream references in run lists; guards for referenced runs; the group's runs by kind |
| `runs.py` | view picker per stage kind |
| `charts.py` | the views above, with `pillar_order` colours |
| `config_editor.py` | `lte` keys under `app_access.yaml` |
| `tests/fixture_run_outputs.py` | an LTE fixture tree |

### 14.6 Access control

The team decides who may:

- run LTE stages;
- edit `lte:` keys;
- generate nested priors;
- mark a ④ run reported.

Default proposal: as Secondary runs today. Only `full_access` /
`config_full_access` may change the stage presets' invariants.

### 14.7 Tests

| Suite | Additions |
|---|---|
| v20 | the codebase functions; references and guards in `projects.py` |
| v21 | the stage flow end to end against a fake Jobs API; every view's data arithmetic on the LTE fixture |
| v22 | real Streamlit; pages and views |

Check page persistence in a real browser (the Update 12 lesson).

---

## 15. Risks and unresolved questions

### 15.1 Decisions needed

P1 = before Phase 1; P2 = before reporting; P3 = can start on the default.

| # | Pri | Question | Options | Recommendation | Source |
|---|---|---|---|---|---|
| D-01 | P1 | Pre-period handling | deck S8 average-year fill; 26-period time-reversed ETS (sheet); none (with U1) | U1 fix; ETS back-cast optional as **burn-in only**; never modelled | deck S8; sheet; FR PDX S24 |
| D-02 | P1 | Order and parameters of the long-term transforms (and K's basis) | SES → Hill → adstock (deck banner, S17) vs SES → adstock → Hill (S12 formula) | confirm with the upstream owner; supply per channel (§8.1 #5). Matters for waves only | deck S12, S16, S17; guide §7, §9 |
| D-03 | P1 | Retrofit source for earlier-year short-term | historically reported (deck S31; FR PDX S3) vs nested coefficients × earlier media (brief 6.1) | `historical` when a reported decomposition exists (consistency with what the client saw), else `coefficients`. Both use κ·τ and the proportional allocation | brief 6.1; deck S24, S31 |
| D-04 | P1 | Nested window | latest 104 weeks / 24 months; must equal the latest primary (AO MMM) model's window | as stated; refuse a mismatch | deck S22, S29 |
| D-05 | P1 | Which sales series feeds the UCM | total sales (vendor) vs base sales (U12); and after a **restatement**, how earlier history and historical decompositions are rescaled | total sales (parity) + U12 as a sensitivity; restatements fixed upstream and documented; historical decompositions rescaled proportionally to restated sales, flagged | FR PDX S23–S24 |
| D-06 | P1 | Regional structure | UCM per region (Colombia) vs national (France); the long-term model regional or national | the same region list in S2–S4; national = sum | region-based notebook; U13 |
| D-07 | P2 | Denominator of the share of the UCM trend | τ (actual; residual → baseline) vs the fitted trend | τ: makes L = κ·c^τ exact and the shares sum to 100% | deck S21, S25 |
| D-08 | P1 | The smoothing cut-off | λ = 300 for every cadence vs one calendar cut-off | decide the cut-off in months; derive λ; keep 300 in parity mode | U4 |
| D-09 | P2 | Seasonal component in the UCM | never (notebooks) vs when tested (deck S10) | when tested (U5) | deck S10–S11 |
| D-10 | P2 | "Media Residuals" construction | geometric decay from the window start (retention from the long-term chain) vs the residual of a first-pass fit vs supplied | a generated decaying stock; never the model's own residual (circular) | deck S18 |
| D-11 | P2 | Year definition for waves and reporting | calendar (FR PDX) vs 52-week blocks from the start vs MAT from the end (codebase 1) | calendar for LTE reporting (FR PDX); codebase 1's MAT stays inside each model | FR PDX S12–S15; codebase 1 [D] |
| D-12 | P2 | Years reported with 3 / 3.5 years | Y1–2 vs Y1 only | 3.5 y: Y1–2; 3 y: Y1, plus Y2 with its tail shown (U15) | deck S4, S31 |
| D-13 | P2 | ROI | volume-only [D] vs FR PDX value ROI | opt-in with spend and price; the multiplier always (it needs neither) | FR PDX S12–S13; codebase 1 [D] |
| D-14 | P2 | Multiplier definition | window-based (FR PDX S11–S13) vs activity-based (waves) | report both, labelled | §11.6 |
| D-15 | P2 | UCM mode by default | parity vs improved | improved, after one side-by-side BMC; parity kept | U1, U16 |
| D-16 | P1 | Primary model with an intercept or trend | keep (competes with `ucm`) vs pin vs drop | the Secondary-style nested preset (no intercept); drop or lock the trend the UCM replaces | METHODOLOGY §2b step 4 |
| D-17 | P2 | Donor list | auto (baseline and positive) vs explicit | auto, shown and editable | METHODOLOGY §2c |
| D-18 | P2 | UCM prior and stopping rule | open iteration (deck) vs at most one correction (§2d) | §10.7 | deck S22; METHODOLOGY §2d |
| D-19 | P3 | `FORECAST.ETS` parity | exact Excel replication vs statsmodels ETS | statsmodels ETS; the back-cast is burn-in only, so parity on the trend suffices | sheet |
| D-20 | P3 | Trade / promotion in the long-term model | allowed vs refused | refused (deck S18) | deck S18 |
| D-21 | P3 | SES start | first non-zero (deck S14) vs first value (`SES.png`) | first non-zero — an upstream concern; codebase 1 uses it for reproduction | deck S14; SES.png |
| D-22 | P3 | Uncertainty in reports | medians only vs paired-draw intervals | medians in tables; intervals in a separate file (§11.5) | — |
| D-23 | P2 | History file format and lever names | codebase 1 `contribution_timeseries.csv` vs vendor tables | both, through the mapping reader | — |
| D-24 | P3 | Short-term carry-over in waves | booked to the activity year (FR PDX) vs decomposed by origin | FR PDX convention by default | FR PDX S15 |
| D-25 | P3 | "Total Media" definition | TV + Digital (FR PDX) vs including Expert | FR PDX: TV + Digital; Expert separate | FR PDX S9, S11 |

### 15.2 Methodological risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| **UCM% is prior-driven** (U11) | it scales every long-term number | lock the media; one correction at most; sensitivity sweep; label |
| **Long-term shares are prior-driven** (U10) | a smooth `dv` carries little information | contraction per coefficient; placebo; baseline-only comparison |
| **The start-up artefact in Year 1** (U1) | biases the reported year | fix at source; parity side by side |
| **Double counting short-term effects as long-term** (U12) | sustained flights leak into the trend | base-sales variant as a sensitivity; λ choice |
| **The retrofit assumes no step change at the window boundary** | deck S30 states the assumption | report the retrofit residual and the U > P⁺ flags; plot both windows (deck S23) |
| **Restated data** | scale shifts break κ·τ across the boundary | restate upstream; level-shift check (S0) |
| **Generated-regressor uncertainty** (U9) | intervals too narrow | sensitivity grid |
| **Truncated waves** (U15) | Year 2 is understated with < 4 years | tail projection, labelled |

### 15.3 Implementation risks

- **statsmodels** is a new dependency on Databricks (unpinned): the ML runtime
  ships it, but versions vary. Record the version in the manifest; parity tests
  catch API drift.
- **Concurrent sessions** edit this tree. Re-read files before editing; ship
  with `python -m mmm.release`.
- **App complexity**: four stage kinds in one group. Mitigate with views per
  kind, references validated by the backend, and real-browser checks.
- **Memory**: per-draw weekly LTE arrays (S × n_obs × features). Compute at
  lever level and only on request (§11.5).

### 15.4 Data still needed from the team

1. Raw activity per channel plus the long-term parameters and their order
   (waves) — or per-wave columns.
2. The original notebook input series (France 182 weeks; Colombia 54 months ×
   13) and the notebooks' exact statsmodels version (parity).
3. The primary (AO MMM) model covering the nested window: a codebase 1 run, or
   vendor contributions.
4. The historically reported short-term decomposition for the retrofit years
   (`historical` mode).
5. Spend and price (only if ROI is wanted).
6. The benchmark multipliers file (FR PDX S14) if benchmarks are to be shown.

---

## 16. Implementation roadmap

| Phase | Tasks | Depends on | Deliverables | Acceptance criteria |
|---|---|---|---|---|
| **0. Decisions and data** | Settle the P1 decisions (D-01–D-06, D-08, D-16); request the §15.4 data; agree tolerances | — | the decision log (this §15 updated); the data contract signed off (§8) | every P1 decision recorded; at least one BMC's full input set in hand |
| **1. UCM extraction** | `mmm/lte/windows.py`, `backcast.py`, `ucm.py` (parity + improved), diagnostics, plots, component CSVs; `statsmodels` in `requirements.txt`; warning rules | 0 | `test_v25`; `01_ucm/` on a real BMC | **parity** reproduces `ucm output` (1e-6 relative); **improved** shows no start-up artefact (first-period τ within ±5% of the local level); identities exact; the 3 / 3.5 / 4-year plans correct |
| **2. Config, data and priors** | `LTEConfig` + the `lte:` section (HELP, CHOICES, schema); `presets.py`; the datacube writers; `priors.py` (shares → long-term priors; LOCKED / DONOR / CANDIDATE with scale conversion; donor audit) | 1 | `test_v28` (part); long-term and nested runs through the **unchanged** `run()` | both models run end to end (stubbed in tests, real in v29); Stage A reproduces the primary within 3% per LOCKED variable on one BMC; `test_v11` / `v19` still pass |
| **3. Combination** | `combine.py` (κ, both retrofit modes, L, final, aggregates, multipliers, ROI opt-in), reconciliation files, `report.py` (tables) | 2 | `test_v26`; `04_lte/` on a real BMC | **G1–G5 golden tests pass**; I1–I10 hold on synthetic data; a real study's final shares sum to 100% per period and region |
| **4. Waves** | `transforms.py`, `waves.py`, tail, charts | 3 + wave data | `test_v27`; `lte_waves.csv` | reproduction check passes on real data; wave rows sum to 100%; synthetic truth recovered; G6 |
| **5. Orchestration and job** | `pipeline.py` / `__main__` (resume, manifest); `app_job` stage dispatch and upstream resolution (renames, Reported / Archived) | 3 (4 optional) | `test_v28` complete; a Databricks run of all stages for one BMC | one command runs S0–S7; a stage re-run reuses unchanged stages; failures publish outputs; manifest complete |
| **6. Web app** | §14 pages, views, references, guards, access; `MIN_CODEBASE` | 5 | v20 / v21 / v22 additions; a real-browser check | a full LTE study run from the app by an analyst; the reported ④ run marked; views match the CSVs |
| **7. Docs and release** | `LTE_GUIDE.md` and the guide updates (§9.5); a case file; `python -m mmm.release`; walkthrough | 3–6 | docs; release | docs reviewed by the team; the first real LTE study reproduced against the vendor's within the agreed tolerance, or the differences explained (start-up artefact, λ) |

**Minimum viable LTE:** Phases 0 → 1 → 2 → 3, run by hand (`python -m mmm.lte`
or a notebook).

- It delivers long-term and total contributions, κ, multipliers and the
  reconciliation for one BMC.
- Waves (Phase 4) wait for the raw activity and parameters.
- The app (Phase 6) comes after the method has been validated on real data.

**Not in scope until decided:**

- AR(1) errors;
- a pooled Bayesian UCM;
- a `run.date_start/date_end` core filter;
- ROI by default.

---

## Appendix A — sources

**Reference material** (transcribed): `../snapshots/UCM/understanding.md` and
the `understanding.md` of each folder:

- `LTE Methodology/`
- `Step by Step Guide/`
- `FR PDX/`
- `ucm code with smooth trend/`
- `ucm code with smooth trend region based/`
- `ucm code multiple pool with smooth trend/`
- `ucm output/`
- `data polation 6 months/`

**Codebase 1:**

- `CLAUDE.md` (module map, invariants, change recipes);
- `docs/METHODOLOGY.md` §2b (a UCM brand-equity series; endogeneity), §2c (the
  secondary model: LOCKED / DONOR / CANDIDATE), §2d (why corrections
  oscillate);
- the code as cited inline.

**Published practice:**

- Harvey, A. C. (1989). *Forecasting, Structural Time Series Models and the
  Kalman Filter*. Cambridge University Press.
- Harvey, A. C. & Jaeger, A. (1993). Detrending, stylized facts and the
  business cycle. *Journal of Applied Econometrics* 8(3), 231–247 —
  [IDEAS](https://ideas.repec.org/a/jae/japmet/v8y1993i3p231-47.html);
  reproduced in the
  [statsmodels example](https://www.statsmodels.org/dev/examples/notebooks/generated/statespace_structural_harvey_jaeger.html).
- Durbin, J. & Koopman, S. J. (2012). *Time Series Analysis by State Space
  Methods*, 2nd ed. Oxford University Press.
- Commandeur, J. J. F. & Koopman, S. J. (2007). *An Introduction to State Space
  Time Series Analysis*. Oxford University Press.
- Ravn, M. O. & Uhlig, H. (2002). On adjusting the Hodrick–Prescott filter for
  the frequency of observations. *Review of Economics and Statistics* 84(2) —
  [paper](https://home.uchicago.edu/huhlig/papers/uhlig.ravn.res.2002.pdf).
- Hamilton, J. D. (2018). Why you should never use the Hodrick–Prescott filter.
  *Review of Economics and Statistics* 100(5), 831–843 —
  [NBER w23429](https://www.nber.org/papers/w23429).
- Hall, V. & Thomson, P. (2021). Does Hamilton's OLS regression provide a
  "better alternative" to the Hodrick–Prescott filter? CAMA WP 71/2020 —
  [PDF](https://crawford.anu.edu.au/sites/default/files/2025-01/71_2020_hall_thomson.pdf).
- Granger, C. W. J. & Newbold, P. (1974). Spurious regressions in econometrics.
  *Journal of Econometrics* 2(2), 111–120 —
  [PDF](https://gwern.net/doc/economics/1974-granger.pdf).
- Dekimpe, M. G. & Hanssens, D. M. (1995). The persistence of marketing effects
  on sales. *Marketing Science* 14(1), 1–21 —
  [IDEAS](https://ideas.repec.org/a/inm/ormksc/v14y1995i1p1-21.html).
- Cain, P. M. (2025). Long-term advertising effects: the Adstock illusion.
  *Applied Marketing Analytics* 11(1), 23–42 —
  [IDEAS](https://ideas.repec.org/a/aza/ama000/y2025v11i1p23-42.html).
- Scott, S. L. & Varian, H. R. (2014). Predicting the present with Bayesian
  structural time series. *International Journal of Mathematical Modelling and
  Numerical Optimisation* 5(1/2) —
  [PDF](https://people.ischool.berkeley.edu/~hal/Papers/2013/pred-present-with-bsts.pdf).
- statsmodels `UnobservedComponents`
  ([docs](https://www.statsmodels.org/stable/generated/statsmodels.tsa.statespace.structural.UnobservedComponents.html)):
  `use_exact_diffuse` defaults to False; cycle bounds are 1.5–12 years with
  dates, otherwise frequency in (0, π). Default approximate-diffuse variance
  1e6
  ([`initialization.py`](https://raw.githubusercontent.com/statsmodels/statsmodels/main/statsmodels/tsa/statespace/initialization.py)).
- PyMC state-space structural components (`LevelTrendComponent`,
  `CycleComponent`) —
  [pymc-experimental docs](https://pymc-experimental.readthedocs.io/en/latest/statespace/models/generated/pymc_experimental.statespace.models.structural.LevelTrendComponent.html);
  an option for a pooled Bayesian UCM later (U13).
