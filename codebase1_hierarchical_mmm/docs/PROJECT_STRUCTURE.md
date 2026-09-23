# Project structure

Twenty modules in one flat folder had stopped being navigable. They now sit in a
small package tree grouped by **what stage of the run they belong to**.

```
codebase1_hierarchical_mmm/
├── run_real_data.py          ENTRY POINT - reads config.yaml, fits, reports
├── synthetic_example.py      ENTRY POINT - parameter recovery (needs PyMC)
├── preview_dv_scaling.py     ENTRY POINT - what does dv_scale do to my KPI?
├── config.yaml               every setting at its default, with help text
├── feature_priors_*.csv      the prior table (one row per feature)
│
├── mmm/                      the package
│   ├── run_pipeline.py       run() - ties the stages together
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
| decide which lever to pull | `docs/TUNING_GUIDE.md` |
| know what order to build in | `docs/METHODOLOGY.md` |
| add a scaling mode | `mmm/data/data_prep.py :: resolve_scaling` |
| add a model term | `mmm/modelling/model.py :: build_model` |
| add an output file | `mmm/reporting/reconciliation.py` + an `OutputConfig` flag |
| add a diagnostic | `mmm/checks/` |
| change a diagnostic threshold | `config.yaml` under `assumptions:` — **not** code |

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
pre_model_outputs/        (or data.pre_model_dir) - cases a-c only
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
| `MERIDIAN_ASSUMPTIONS.md` | how Meridian handles all this, with source pointers |
| `PROJECT_STRUCTURE.md` | this file |

## Tests

`../tests/run_all.py` — 1293 checks, ~10s, no PyMC needed. They import the
package the same way an entry point does (`sys.path.insert(0, CB1)` then
`import mmm.core.config`).

> **Codebase 2 is still FLAT.** `test_no_pymc.py` imports from both, so it
> switches `sys.path` halfway through and uses bare names for codebase 2. When
> Phase 2 restructures, that file is the one to update.
