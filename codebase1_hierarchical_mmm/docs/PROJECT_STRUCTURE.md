# Project structure

Twenty modules in one flat folder had stopped being navigable. They now sit in a
small package tree grouped by **what stage of the run they belong to**.

```
codebase1_hierarchical_mmm/
├── run_real_data.py          ENTRY POINT - reads config.yaml, fits, reports
├── synthetic_example.py      ENTRY POINT - parameter recovery (needs PyMC)
├── preview_dv_scaling.py     ENTRY POINT - what does dv_scale do to my KPI?
├── demo.ipynb                ENTRY POINT - the Databricks job the web app runs
│                             (widgets -> mmm/app_job.py); blank widgets = a
│                             hand run of this folder's config.yaml
├── config.yaml               every setting at its default, with help text
├── app_access.yaml           who may do what in the web app: four levels (full
│                             access, every setting, editable + advanced, editable
│                             only), who may mark a reported run and who may
│                             rename a run / edit its note (RBAC)
├── bmc_names.csv             the standard BMC names the web app offers
├── modelling_types.csv       the modelling types (LTE, Primary, Secondary ...)
│                             and the settings each one sets (section.key columns)
├── feature_priors_*.csv      the prior table (one row per feature)
│
├── mmm/                      the package
│   ├── run_pipeline.py       run() - ties the stages together
│   ├── app_job.py            what demo.ipynb does: the uploaded config with only
│   │                         the job-owned keys replaced, run, publish to the
│   │                         run folder's Outputs/ (<BMC>/<period type>/<run>/);
│   │                         the name rules, the standard-name CSV reader and
│   │                         each type's settings (config.yaml < type < run)
│   ├── core/
│   │   ├── config.py         every config dataclass + the prior-unit maths
│   │   ├── settings.py       the config.yaml front end (run_from_yaml)
│   │   └── compat.py         InferenceData vs DataTree shims
│   ├── data/
│   │   ├── data_prep.py      validation, scaling, folds -> PreparedData
│   │   ├── mapping.py        the ONE reader of the mapping file: vendor <->
│   │   │                     our variables, groups, vendor contributions
│   │   └── prior_builder.py  PRE-MODEL: generate a prior file from the
│   │                         mapping's contributions or from the share file
│   ├── modelling/
│   │   ├── model.py          the joint vectorised PyMC model
│   │   └── fit.py            NumPyro/JAX sampling, with fallbacks
│   ├── reporting/
│   │   ├── outputs.py        decomposition, coefficients, fit metrics
│   │   ├── reconciliation.py the audit-trail files (OutputConfig)
│   │   ├── benchmark.py      the paste-your-benchmark sheet (regions across)
│   │   ├── plotting.py       ONE owner of figure size/dpi/labels
│   │   └── prior_plots.py    prior / likelihood / posterior charts
│   └── checks/
│       ├── diagnostics.py    R-hat, ESS, divergences, contraction
│       ├── assumptions.py    collinearity + the regression assumptions
│       ├── warnings_report.py warnings grouped into per-category documents
│       └── cross_validation.py expanding-window CV + model selection
│
├── samples/                  the two optional input files, to copy
│   ├── mapping_sample.csv    vendor_variable,our_variable,region,contribution
│   └── share_sample.csv      section,pillar,pillar_share_pct,variable,spend,...
└── docs/                     see the index below
```

## Importing

Entry points live at the **codebase root**, so Python puts that folder on
`sys.path` and the package resolves without any setup:

```python
from mmm.core.settings import run_from_yaml
from mmm.core.config import ModelConfig, RunConfig
from mmm.checks.cross_validation import compare_cv_runs
```

On Databricks, `sys.path.append("/Workspace/.../codebase1_hierarchical_mmm")`
then import the same way.

Modules can also be run directly:

```bash
python -m mmm.data.prior_builder config.yaml     # pre-model prior builder
python -m mmm.core.settings --write config.yaml  # regenerate the template
```

## Which file do I open?

| I want to… | File |
|---|---|
| change a setting | `config.yaml` (reference: `docs/CONFIG_GUIDE.md`) |
| change a feature's prior | the prior CSV (reference: `docs/FEATURE_PRIOR_GUIDE.md`) |
| generate a prior file from a vendor decomposition or shares | `mmm/data/prior_builder.py` |
| change how vendor and our variables are matched | `mmm/data/mapping.py` |
| understand an output column | `docs/OUTPUTS_GUIDE.md` |
| read a check (VIF … exogeneity, CV) and decide what to change | `docs/CHECKS_GUIDE.md` |
| find out whether a problem has happened before | `docs/cases/README.md` |
| decide which lever to pull | `docs/TUNING_GUIDE.md` |
| know what order to build in | `docs/METHODOLOGY.md` |
| add a scaling mode | `mmm/data/data_prep.py :: resolve_scaling` |
| add a model term | `mmm/modelling/model.py :: build_model` |
| add an output file | `mmm/reporting/reconciliation.py` + an `OutputConfig` flag |
| add a diagnostic | `mmm/checks/` |
| change a diagnostic threshold | `config.yaml` under `assumptions:` — **not** code |
| change what the web app's job does (paths, publishing) | `mmm/app_job.py` (+ `demo.ipynb`, which only reads widgets) |
| add a setting to the web app's editor | the dataclass field + its `HELP` line (enums in `settings.CHOICES`), then list it under `editable:` (or `advanced:`) in `app_access.yaml` so people may change it |
| choose which settings people may change in the app | `app_access.yaml` → `editable:` for everyone, `advanced:` for `config_advanced_access` (allow-lists; the rest stay at `config.yaml`'s values) |
| give someone every setting / the admin tools / the Advanced options | `app_access.yaml` → `config_full_access:` / `full_access:` / `config_advanced_access:` |
| add a BMC name or a modelling type the app offers | `bmc_names.csv` / `modelling_types.csv` (one per line) |
| choose who may mark the reported run | `app_access.yaml` → `mark_reported:` |
| make a modelling type set a setting (e.g. Primary with an intercept) | `modelling_types.csv` → a `section.key` column, one value per type (blank = `config.yaml`'s) |
| choose who may rename a run or edit its note | `app_access.yaml` → `edit_runs:` (levels, and `submitter`) |

## The web app is the frontend, this folder the backend

`../web/` (the BRIDGE Streamlit app, a Databricks App) never copies this code.
It loads it LIVE from the workspace - the folder of the notebook the model job
runs, i.e. this one - and re-checks it every few minutes, so re-uploading this
folder updates the app, the job and anyone running it by hand at once. The app
needs `mmm.__version__` >= the version in `web/src/codebase.py :: MIN_CODEBASE`.
Everything it calls: `web/src/codebase.py`; setup: `web/README.md`.

Each run the app starts has its own folder, `Secondary Modelling/<BMC>/<period>
<modelling type>/<run name>/` (the middle level, e.g. `2025Q1-2025Q4
Secondary`, is the RUN GROUP), with its inputs (`Config/ Data/ Prior/
[Mapping/ Share/]`), `run_request.json`, the modeller's `note.txt` and
`Outputs/`. The job gets `bmc_name`, `run_group` and `run_name`;
`app_job.run_folder` builds the path, and `app_job.NAME_PATTERN`,
`group_problem` and `run_name_problem` are the name rules the app checks too.
A blank `run_group` runs directly under the BMC (runs from before the groups);
with everything blank, the job uses the old shared folders. When someone marks
the run a period's results were reported from, the APP moves it into
`<run group>/Results Reported/` and the group's other runs into `<run
group>/Archived/` (never while one of them runs); the job never moves
anything.
The saved `Config/config.yaml` holds only the settings the person may change
(`app_access.yaml`); `app_job.merge_config` lays it over this folder's
`config.yaml`. While the job runs, `app_job` copies `job_log.txt` into
`Outputs/` every 30 s (`LIVE_LOG_SECONDS`), so the app can show the log live.

The app's result charts read these files - renaming a column in them means
updating `web/src/charts.py` (and its tests, v21/v22):
`04_fit/fit_metrics.csv`, `04_fit/actual_vs_predicted.csv`,
`05_contributions/contribution_summary.csv`,
`05_contributions/contribution_timeseries.csv`,
`02_convergence/prior_posterior_contraction.csv`,
`02_convergence/convergence_report.txt`, `00_warnings/all_warnings.csv`.

## Output tree

```
outputs/<run_name>/
├── 00_warnings/          one document per warning CATEGORY, plus the index
├── 01_data/              resolved_config, scaling stats, collinearity, priors
├── 02_convergence/       R-hat/ESS/divergences, contraction, prior-posterior
├── 03_coefficients/      coefficient_report, forest plots
├── 04_fit/               metrics, actual vs predicted, assumptions, exogeneity
├── 05_contributions/     totals, summary, math, reconciliation, benchmark sheet
├── 06_cross_validation/  (only when cv.enabled) folds, stability, scorecard
└── trace.nc
```

Plus, when the pre-model step runs:

```
pre_model_outputs/        (or data.pre_model_dir) - build_priors("config.yaml")
├── 00_warnings/                  the step's warnings, by category
├── feature_priors_national.csv   one national mean per variable (hierarchical)
├── feature_priors_regional.csv   + one override row per region (independent)
└── prior_calculation.xlsx        every intermediate number and the formula
```

## Docs index

| File | Use it for |
|---|---|
| `CONFIG_GUIDE.md` | every `config.yaml` key: meaning, default, when to change |
| `FEATURE_PRIOR_GUIDE.md` | every prior-CSV column, and how to generate the file |
| `METHODOLOGY.md` | the staged build, the prior ladder, updating a prior with no benchmark, raising an under-credited variable, how to check collinearity |
| `TUNING_GUIDE.md` | situation → lever, recipes, anti-patterns |
| `OUTPUTS_GUIDE.md` | every output file and column |
| `CHECKS_GUIDE.md` | every check: theory, a real-run example, how to read it, and what to change (variables / config / priors); threshold table; symptom → fix |
| `cases/` | the casebook: one file per real problem (symptom, cause, how to confirm, what to do), plus a template for the next one |
| `MERIDIAN_ASSUMPTIONS.md` | how Meridian handles all this, with source pointers |
| `PROJECT_STRUCTURE.md` | this file |

## Tests

`../tests/run_all.py` — 2021 checks, ~1 min, no PyMC needed. They import the
package the same way an entry point does (`sys.path.insert(0, CB1)` then
`import mmm.core.config`).

> **Codebase 2 is still FLAT.** `test_no_pymc.py` imports from both, so it
> switches `sys.path` halfway through and uses bare names for codebase 2. When
> Phase 2 restructures, that file is the one to update.
