# Codebase 1 — Hierarchical MMM on pre-transformed data

The rebuild of `production_code.py`. It keeps the team's workflow — adstock,
saturation and lag applied **in preprocessing**, transformed data handed to the
model — but replaces the per-region loop of independent PyMC regressions with
**one joint Bayesian model over all regions**:

- sampled with NumPyro (JAX, GPU on Databricks);
- each feature's coefficient shared across regions in one of three ways: fully,
  partially, or not at all;
- full stage-by-stage outputs, so every reported number can be re-derived by
  hand.

It is the backend of the **BRIDGE** web app (`../web/`), which runs it as a
Databricks job.

Version: `mmm/__init__.py` → `__version__` (`2026.10.14.2` as of this writing).
In-model transforms (learned adstock / Hill) are codebase 2, not this.

**Where to read more:**

| You want | Open |
|---|---|
| What the model is: equation, pooling, priors, sampler | `docs/MODEL_GUIDE.md` |
| Every `config.yaml` key | `docs/CONFIG_GUIDE.md` |
| The prior file, and generating it | `docs/FEATURE_PRIOR_GUIDE.md` |
| The order to build a model in | `docs/METHODOLOGY.md` |
| Which setting changes what | `docs/TUNING_GUIDE.md` |
| Every output file and column | `docs/OUTPUTS_GUIDE.md` |
| Every check: what it means and what to change | `docs/CHECKS_GUIDE.md` |
| Problems already met, and their fixes | `docs/cases/` |
| Which file do I open? | `docs/PROJECT_STRUCTURE.md` |
| **Developing the code** | `CLAUDE.md` in this folder |

---

## Data contract

One long (stacked) table — the shape of `input_datacube.xlsx`. Excel, CSV and
Parquet are all read.

| Column | Meaning |
|---|---|
| `date` | the period; weekly or monthly, detected automatically |
| `region` | region / geo / retailer id; names must match the prior file exactly |
| `dv` | the KPI (sales) **raw**, in original units — do not standardise it; the pipeline scales internally |
| everything else | **pre-transformed** regressors, no NaN — zero-fill inactive periods |

Column names are set in `config.yaml` (`run.date_col`, `run.region_col`,
`run.dv_col`).

---

## Run it

### 1. From `config.yaml` — the usual way

`config.yaml` holds every setting with a line of help above it. Edit the YAML,
not the code.

```bash
python run_real_data.py config.yaml              # fit + every report
python -m mmm.core.settings config.yaml          # the same, without the summary prints
python -m mmm.core.settings --write my.yaml      # write a fresh template, all defaults
```

```python
from mmm.core.settings import run_from_yaml
result = run_from_yaml("config.yaml")
```

How the file behaves:

- A misspelled key **stops the run** and names the closest valid one.
- Omitted keys take their defaults.
- The effective settings are written to
  `outputs/<run_name>/01_data/resolved_config.yaml`.

**The team's `config.yaml` is not the dataclass defaults.** It runs:

- `model.likelihood: student_t`;
- `model.include_intercept: false`;
- `run.dv_center: none`, `run.dv_scale: mean`, `run.scaling_window: full`;
- `run.holdout_periods: null` (12.5% of the panel);
- `sampler.chain_method: vectorized`.

That is a decomposition **versus zero**, the vendor convention — see "The
intercept" below.

### 2. No prior file yet? Generate one

With `data.feature_priors` unset, `run_from_yaml` builds a prior file from the
datacube, prints its path and stops. You review it, then point the config at
it. To build it on its own:

```python
from mmm.data.prior_builder import build_priors
build_priors("config.yaml")          # or: python -m mmm.data.prior_builder config.yaml
```

| Input | Effect |
|---|---|
| `data.mapping_file` — vendor ↔ our variables, optionally with the vendor's contributions | contributions become prior means: `C / Σx / mean dv` per region |
| `data.share_file` — shares of sales by media / expert / comp_media / trade / baseline | shares become prior means |
| neither | a skeleton: every variable, blank means |

It always writes two files plus a workbook:

- `feature_priors_national.csv` (hierarchical);
- `feature_priors_regional.csv` (one row per region, independent);
- `prior_calculation.xlsx`, showing the arithmetic.

**The generated means are in the model's units only with
`run.dv_scale: mean`** — a warning says so otherwise. The full rules (signs,
`national_basis`, region matching) are in `docs/FEATURE_PRIOR_GUIDE.md` §5.

### 3. From Python

```python
from mmm.core.config import (ModelConfig, OutputConfig, RunConfig,
                             SamplerConfig, load_feature_config)
from mmm.run_pipeline import run

result = run(df,
             ModelConfig(features=load_feature_config("feature_priors.csv"),
                         likelihood="student_t", include_intercept=False),
             RunConfig(run_name="fy26", dv_center="none", dv_scale="mean"),
             SamplerConfig(sampler="numpyro", chain_method="vectorized"),
             out_cfg=OutputConfig(period_split="mat"))
```

The YAML is a front end onto exactly these dataclasses, with the same
validation.

### 4. From the web app (BRIDGE)

The app's **Run Model** starts the Databricks job, which runs `demo.ipynb`. The
notebook only reads its widgets and calls `mmm.app_job.run_app_job`, so:

- it runs the uploaded config with only the paths and the run name replaced
  (`JOB_OWNED_KEYS`);
- it never edits this folder's `config.yaml`;
- it publishes the outputs into the run's folder
  `<BMC>/<period> <modelling type>/<run name>/Outputs/` — also when the run
  fails;
- on compute without the `/dbfs/mnt` mount (the serverless GPU) it reads and
  writes ADLS itself, as the service principal in a secret scope
  (`mmm/adls.py`): the run's inputs are copied to local disk, the outputs and
  the live log back. The job parameter `storage` = `auto` (default) / `mount` /
  `adls`; `adls_secret_scope` names another scope.

The app also reads three files in this folder:

| File | Sets |
|---|---|
| `app_access.yaml` | who may change which settings |
| `modelling_types.yaml` | each type's settings: `Primary` keeps the intercept, `Secondary` drops it |
| `bmc_names.csv` | the BMC names the app offers |

Setup: `../web/README.md`.

### 5. Parameter recovery

```bash
python synthetic_example.py      # needs PyMC; checks the model recovers known coefficients
```

---

## The model in one screen

For region `g` and period `t`, on the scaled axis:

```
y[g,t] = α_g                       region intercept, pooled        (model.include_intercept)
       + Σ γ_k · fourier_k(t)      seasonality, shared             (model.fourier_order)
       + δ_g · t                   trend per region, pooled        (model.include_trend)
       + Σ_j β[g,j] · x[g,t,j]     every feature
       + noise                     Normal or Student-t, σ per region, pooled
```

Each feature chooses two things in the prior file:

| Column | Options |
|---|---|
| `sign_constraint` | `positive` / `negative` build β = ±exp(·), which can never cross zero; `free` builds β = normal |
| `pooling` | `hierarchical` (default) — regions pulled towards a shared centre, more when their own data is weak; `global` — one coefficient for every region; `independent` — every region on its own, with its own prior |

Everything — the equation, the shrinkage, the parameter names, the priors as
sampled, and a comparison with Meridian — is in `docs/MODEL_GUIDE.md`.

---

## The prior file

One row per feature, plus optional region rows:

```csv
variable,region,pooling,sign_constraint,global_prior_mean,global_prior_sd,prior_sd_basis,baseline,pillar
tdp_base,,global,positive,0.0042,0.02,relative,1,Baseline
tv_base,,hierarchical,positive,0.0008,0.3,relative,0,TV
tv_base,Base_Droguerias,,,0.0011,,,,
price_base,,global,negative,0.0031,0.02,relative,1,Baseline
event_dummy,,global,free,0,0.4,absolute,1,Baseline
```

- **Blank cells are legal** and take the documented defaults: `pooling`
  hierarchical, `sign_constraint` free, no centring and no scaling, `auto`
  contribution reference.
- **`prior_sd_basis: relative`** reads `0.2` as ±20%. `0.02` pins a coefficient
  (an assumption, not an estimate).
- **`center_mode` / `scale_mode`** default to `none` / `none`: the column is
  used exactly as it is in the datacube, so every prior is **per raw unit**. The
  old `center` column is gone; a file that still has it is rejected.
- **Region rows** override one region's prior:
  - under `independent` — that region's prior outright;
  - under `hierarchical` — a fixed shift of the centre it is pulled towards;
  - under `global` — refused.

  A region name that is not in the data stops the run.
- **`baseline=1`** folds a feature into the baseline; it is still reported on
  its own row. **`pillar`** is the reporting roll-up, **`sub_pillar`** the
  level under it (*Search* and *Social* inside *Online Media*), and **`label`**
  the readable name client-facing charts show. All three are reporting only;
  `05_contributions/contribution_by_sub_pillar.csv` rolls up by the sub-pillar.

Every column: `docs/FEATURE_PRIOR_GUIDE.md`.

---

## Units — the one rule

**The scale is the unit of every prior.** A coefficient means "moves the KPI
by β × `dv_scale` per unit of the feature as the model sees it". With the
defaults that is per **raw** feature unit, per unit of the region's KPI scale.

Change `dv_scale` or a `scale_mode` and every prior mean must change by the
same factor. A units mismatch still reconciles to exactly 100% — no
reconciliation check can catch it. The v5 run is the cautionary tale:
`docs/TUNING_GUIDE.md` §4.

---

## The intercept — and a decomposition versus zero

| `include_intercept` | `dv_center` | What you get |
|---|---|---|
| `true` | either | the default model: an estimated region intercept carries the level |
| **`false`** | **`none`** | **a decomposition versus zero** — the vendor convention, the team's config and the `Secondary` type. There is no intercept: the uncentred always-on level variables (distribution, TDP, price, category) carry the level of sales, and every contribution is measured against zero |
| `false` | `mean` | the training mean becomes a fixed baseline; the drivers explain the movement around it |

**In a decomposition versus zero, keep the level variables uncentred**
(`center_mode: none`). Without an intercept, centring takes a variable's level
out of the model. If nothing is left to carry the level, the run warns
(`no_level_term`) and names the regions.

With an intercept, the advice flips: centre the always-on level variables
(`center_mode: mean` + `contribution_reference: zero`). Uncentred, they fight
the intercept — the defect that broke the first real run.

`output.report_intercept: false` hides the intercept parameters from the
contraction report without changing the model. Which to use when:
`docs/TUNING_GUIDE.md` §2.1–2.2.

---

## The modelling period

**A year is always 52 weeks or 12 months.** Everything else scales with the
panel:

- the holdout is 12.5% of the periods (13 of 104 weeks; 3 of 24 months);
- the CV horizon is 12.5%; the CV minimum training window is 50%, never under a
  year.

An absolute number (`run.holdout_periods: 26`) beats a fraction, which beats
that default.

`output.period_split: mat` reports one block per year of data, counted back
from the last date and numbered oldest-first (MAT 1 = the earliest whole year).
A remainder goes to `Pre-MAT`; a panel under a year is split in half. The other
options:

- `week` — one block per date;
- `year` — calendar years;
- `none` — the window only.

Every block reconciles to 100% of its actual sales.

---

## Sampling

| Setting | Default | Note |
|---|---|---|
| `draws` | 1000 | kept per chain |
| `tune` | 1000 | warm-up, i.e. burn-in, per chain — discarded |
| `chains` | 4 | |
| `target_accept` | 0.92 | raise towards 0.99 if there are divergences |
| `max_treedepth` | 10 | at most 2^depth − 1 steps per draw; saturation means fix the geometry first |
| `dense_mass` | `false` | `true` helps when coefficients trade off |
| `jitter` | `true` | chains start from different points |
| `sampler` | `numpyro` | `numpyro` (JAX/GPU), `pymc` (CPU) or `advi` (fast approximation; no R-hat) |

**On one GPU set `chain_method: vectorized`** — its own setting, not a
`nuts_kwargs` entry. `nuts_kwargs` is only for other kernel arguments, such as
`step_size`.

**After every run check `02_convergence/sampling_log.json`:**

- `sampler_used`;
- `chain_method_applied`;
- `kernel_kwargs_used`.

Set `run.on_convergence_failure: fail` to stop a run with R-hat above 1.05 or
more than 1% divergences, instead of reporting it.

More: `docs/MODEL_GUIDE.md` §9.

---

## Outputs — `outputs/<run_name>/`

| Folder | What is in it |
|---|---|
| `00_warnings` | every warning, grouped by category: `00_INDEX.md`, one `<category>.md` each, `all_warnings.csv`. Read the high-severity ones first |
| `01_data` | panel summary, scaling statistics, the model's input row by row, `prior_summary.csv` (what each prior really means), collinearity on the model's own design (`collinearity_summary` / `_vif` / `_pairs` / `_matrix`, heatmaps), `resolved_config.yaml` |
| `02_convergence` | `convergence_report.txt` (R-hat, ESS, divergences, BFMI, tree depth), `sampling_log.json`, `prior_posterior_contraction.csv` + plots (did the data or the prior decide each coefficient?), prior predictive check |
| `03_coefficients` | `coefficient_report.csv` (median, 90% HDI, `t_stat`, `p_value` + `p_value_basis`, `prob_negligible`, original-unit effect, `data_support`), `support_warnings.txt`, forest plots |
| `04_fit` | `fit_metrics.csv` (read `r2_within_region`, not the pooled `r2`; judge holdout on `coverage_90_pred_pct`), actual vs predicted, residual and assumption checks, posterior correlation, confounding, exogeneity at leads and lags, aggregate PPP and P(baseline < 0) |
| `05_contributions` | contribution totals (median of each total, with an HDI), the vendor-style volume and % table, the weekly decomposition, `contribution_math.csv` (recompute any number by hand), `contribution_reconciliation.csv` + `_chain.csv` (components → fitted → actual, the median gap traced), `benchmark_comparison.xlsx` (paste a benchmark; the formulas do the rest) |
| `06_cross_validation` | only with `cv.enabled: true` — fold metrics, coefficient stability across folds, the scorecard used to choose between models |

Three readings to keep straight:

- **Sign-constrained features.** `p_value` is 0 by construction —
  `p_value_basis` says so. Use the HDI, `t_stat` and the contribution's share
  and interval.
- **Two arithmetics.** Contribution totals are medians of totals (they have
  intervals but don't add); the reconciliation tables are sums of weekly
  medians (they add, with the median gap on its own line).
- **Baseline features appear twice** in `contribution_totals.csv`. Filter on
  `group` before summing.

Every column: `docs/OUTPUTS_GUIDE.md`. What every check means and what to change
when it fails: `docs/CHECKS_GUIDE.md`.

---

## Cross-validation

Expanding-window CV refits the model once per fold, each scaled on its own
training window. It reports accuracy across folds and **how much each
coefficient moves** as the window moves — the trust check that matters for a
decomposition.

```yaml
cv: {enabled: true}          # in config.yaml; horizon and folds follow the panel
```

```python
from mmm.core.config import CVConfig
from mmm.checks.cross_validation import run_cv, compare_cv_runs
run_cv(df, model_cfg, run_cfg, sampler_cfg, CVConfig(enabled=True))
compare_cv_runs(["outputs/run_a", "outputs/run_b"])   # pick between finished runs
```

`compare_cv_runs` applies the selection rule in order:

1. admissibility is a gate (convergence, coverage);
2. accuracy, but only beyond one standard error of the fold spread;
3. coefficient stability;
4. parsimony.

---

## Deploying on Databricks

- **Re-upload the WHOLE `mmm/` folder, every time.** Every module carries a
  version stamp. Every entry point checks that all of them match before doing
  anything, and stops with `OutOfSyncError` if a file was left behind. The
  error names each stale file.
  - Run `python -m mmm.release --check` to list them yourself.
  - Setting the environment variable `MMM_ALLOW_OUT_OF_SYNC=1` runs a mixed
    copy anyway (not recommended).
- **`requirements.txt` pins nothing** — each job install takes the newest
  release. After a PyMC or ArviZ release, rerun the real-PyMC test (below) or
  pin.
- Prefer local disk for outputs: `/Workspace` drops rapid PNG writes. The job
  writes locally, then copies.

---

## Development

```bash
python ../tests/run_all.py            # every local check, ~1 minute, no PyMC needed
python ../tests/test_v24_real_pymc.py # samples the REAL model; needs pymc, numpyro, jax
python -m mmm.release 2026.10.15.1    # ship a change: stamps every module with the new version
```

- On Windows Git Bash, set `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` before
  `run_all.py`.
- `CLAUDE.md` in this folder has the module map, the invariants, step-by-step
  recipes for common changes, the web app's dependencies on this code, and the
  open issues.

---

## What changed from `production_code.py` — talking points

1. **One model, all regions.** Partial pooling lets small regions borrow
   strength; a feature that never runs in a region simply contributes 0 there.
2. **Speed.** One compiled graph instead of one per region; JAX JIT, GPU, all
   chains in one kernel.
3. **Valid inference.** `t = mean / mcse` is gone: `mcse` shrinks as you sample
   longer, so "significance" grew with run time. `t_stat` uses the posterior
   sd. The HDI, `p_value_basis` and the contraction report say what the data
   actually decided.
4. **Sign control and a structured baseline.** Signs are built in; optional
   seasonality and trend stop media absorbing the seasonal cycle. Baseline
   variables can be reported inside an expandable baseline.
5. **Priors from evidence.** Generated from vendor contributions or shares, in
   the model's own units, with the arithmetic in a workbook.
6. **Everything checkable.** Reconciliation files, collinearity on the model's
   own design, residual and structural checks, cross-validation, and warnings
   grouped by category.

## Caveats to keep stating

- **Pre-transformed inputs hide transform errors.** Adstock, saturation and lag
  are chosen by hand upstream, so an error there is invisible to this model:
  the fit looks fine and the coefficients are biased. Codebase 2 (learned
  transforms) exists to fix that.
- **A pinned prior passes every check.** A coefficient fixed by its prior
  (contraction ≈ 0) is an input, not a finding — say so when reporting it.
