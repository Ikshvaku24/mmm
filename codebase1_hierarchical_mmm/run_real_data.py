"""Driver for the real dataset. Reads `config.yaml` - no config lives in here.

Databricks / notebook usage: run the cells in order, or `python run_real_data.py`
(optionally `python run_real_data.py my_config.yaml`).

Everything this script used to hard-code - model, run, sampler, output and CV
settings, and the paths to the data and the prior file - is now in
`config.yaml`, which lists EVERY setting at its default with a line of help
above it. Change the YAML, not this file. What is left here is the part that is
genuinely a *script*: the pre-flight checks on the data, and the summary prints
after the fit.

    CONFIG = "config.yaml"        # <- the only thing to point at a different run

Cross-validation runs only when `cv.enabled: true` in the YAML.

Two things the first real run (real_data_v1) got wrong in the CONFIG, both now
detected by prepare_data and written to 00_warnings/ - fix them in the prior
file before re-running:

  1. Always-on LEVEL variables need `center_mode=mean`: TDP, AVP, ACV_WD_Any
     Merch. Without it they are scaled to ~1.0 every week, collinear with the
     region intercept. That run produced coefficients of +31 / -33 on TDP / AVP
     and a decomposition of +91% / -97% that cancelled out, with max R-hat 1.26,
     min ESS 13 and tree depth saturated in 100% of steps.
     NOTE the legacy `center=1` flag is a NO-OP when an explicit `center_mode`
     column is present in the file - set `center_mode`.
  2. Columns that are pure numerical dust must be dropped: Coupon-Digital,
     Coupon-FSI and Coupon-Ibotta had every non-zero value at ~1e-15, so the
     model was fitting float noise (and reporting 1e18 "per unit" effects).

The prior file (`data.feature_priors` in the YAML) is the source of truth for
which columns are modelled - its `variable` values must match the data column
names EXACTLY. It also carries:
  pooling   hierarchical (pool regions) | independent (own prior per region) |
            global (one shared coefficient)
  baseline  1 = fold into the BASELINE rather than report as an incremental
            effect. TDP, AVP and ACV_WD_Any Merch are flagged, so
            contribution_totals.csv reports __baseline__ (core + these) and
            still lists each of them separately (group = baseline_part) so the
            baseline can be expanded.
  region    fill it in on an EXTRA row to override the prior for one region,
            e.g. a stronger TV prior in Walmart:
                TV_GM,,hierarchical,positive,0.05,1.0,0.5,0,0
                TV_GM,1-Walmart+FamilyDollar,,,0.20,,,,
            Region names must match the data exactly - the run stops otherwise.

See `docs/TUNING_GUIDE.md` for which knob to reach for in which situation.
"""
from __future__ import annotations

import sys

import pandas as pd

from settings import load_panel, load_settings, run_from_yaml
from warnings_report import collect_warnings

CONFIG = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"

# ---- 1. load + sanity-check the data ---------------------------------------
# Reading the settings resolves every feature spec, which is where the
# per-feature prior warnings are raised. Capturing them here is what keeps them
# OUT of this cell: they are handed to the run below and written to
# 00_warnings/ with one document per category instead.
with collect_warnings() as config_warnings:
    settings = load_settings(CONFIG)
df = load_panel(settings)

feat_names = [s.name for s in settings.model.features]
missing = [c for c in feat_names if c not in df.columns]
if missing:
    close = {m: [c for c in df.columns if c.lower().replace(" ", "")
                 == m.lower().replace(" ", "")] for m in missing}
    raise SystemExit(
        f"{settings.data['feature_priors']}: names not found in the data: "
        f"{missing}\npossible matches (fix the prior file or rename the "
        f"columns): {close}")

dc, rc = settings.run.date_col, settings.run.region_col
n_dates = df[dc].nunique()
print(f"config    {CONFIG}")
print(f"rows={len(df)}  regions={df[rc].nunique()}  periods={n_dates}  "
      f"({pd.to_datetime(df[dc]).min().date()} .. "
      f"{pd.to_datetime(df[dc]).max().date()})")
print(f"features={len(feat_names)}  run_name={settings.run.run_name}  "
      f"cv={'on' if settings.cv.enabled else 'off'}")
print("\nnon-zero periods per feature (lowest 8 - candidates to drop/pin):")
print((df[feat_names] != 0).sum().sort_values().head(8))

# ---- 2. fit + full report ---------------------------------------------------
# Nothing warning-shaped is printed here. Everything goes to 00_warnings/:
# 00_INDEX.md ranks the categories, each <category>.md states the message ONCE
# and then tables the features it applies to. Only high-severity headlines
# reach this cell.
result = run_from_yaml(CONFIG, df=df, settings=settings,
                       extra_warnings=list(config_warnings))
print("outputs ->", result["output_dir"])
print(result["metrics"][result["metrics"]["region"] == "__all__"])

# the one-line reconciliation: do the reported drivers add up to actual sales?
recon = pd.read_csv(f"{result['output_dir']}/05_contributions/"
                    "contribution_reconciliation.csv")
print(recon[(recon.scope == "all") & (recon.region == "__portfolio__")].T)

# ---- 3. cross-validation ----------------------------------------------------
# Set `cv.enabled: true` in the YAML (and optionally horizon / n_folds /
# min_train_periods / draws / tune) - it then runs automatically above and the
# results land in 06_cross_validation/. Each fold is a full refit, so expect
# roughly n_folds times the cost of the headline run.
if "cv" in result:
    print(open(f"{result['cv']['output_dir']}/cv_report.md").read())
