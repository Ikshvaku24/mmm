"""Driver for the real dataset (input_datacube.xlsx + feature_priors.csv).

Databricks / notebook usage: run cells in order, or `python run_real_data.py`.
The feature_priors.csv is the source of truth for which columns are modelled -
its `variable` values must match the data column names EXACTLY.
"""
from __future__ import annotations

import pandas as pd

from config import (CVConfig, ModelConfig, RunConfig, SamplerConfig,
                    load_feature_config)
from run_pipeline import run

INPUT_PATH = "input_datacube.xlsx"
PRIOR_PATH = "feature_priors.csv"

# ---- 1. load + sanity-check the data --------------------------------------
df = pd.read_excel(INPUT_PATH, engine="openpyxl")
features = load_feature_config(PRIOR_PATH)

feat_names = [s.name for s in features]
missing = [c for c in feat_names if c not in df.columns]
if missing:
    close = {m: [c for c in df.columns if c.lower().replace(" ", "")
                 == m.lower().replace(" ", "")] for m in missing}
    raise SystemExit(f"feature_priors.csv names not found in data: {missing}\n"
                     f"possible matches (fix the CSV or rename columns): {close}")

n_dates = df["date"].nunique()
print(f"rows={len(df)}  regions={df['region'].nunique()}  periods={n_dates}  "
      f"({pd.to_datetime(df['date']).min().date()} .. "
      f"{pd.to_datetime(df['date']).max().date()})")
print("\nnon-zero weeks per feature (lowest 8 - candidates to drop/pin):")
print((df[feat_names] != 0).sum().sort_values().head(8))

# ---- 2. configure -----------------------------------------------------------
model_cfg = ModelConfig(
    features=features,
    likelihood="student_t",       # robust to promo/holiday spikes
    fourier_order=2,              # annual seasonality baseline
    include_trend=True,
    pool_sigma=True,
)

run_cfg = RunConfig(
    run_name="real_data_v1",
    holdout_periods=13,           # ~1 quarter OOS; needs >= 23 total periods
    report_draws=400,
    on_convergence_failure="warn",
    zero_threshold_rel=1e-6,      # snaps adstock-tail dust (e.g. 5e-17) to zero
)

sampler_cfg = SamplerConfig(
    draws=1000, tune=1000, chains=4, target_accept=0.95, seed=42,
    sampler="numpyro",
    chain_method="vectorized",    # all chains in one GPU kernel
    allow_sampler_fallback=False, # fail loudly instead of burning CPU hours
)

# ---- 3. fit + full report ---------------------------------------------------
result = run(df, model_cfg, run_cfg, sampler_cfg, save_trace=True)
print("outputs ->", result["output_dir"])
print(result["metrics"][result["metrics"]["region"] == "__all__"])

# ---- 4. (after the single run looks sane) expanding-window CV ---------------
# from cross_validation import run_cv
# cv = run_cv(df, model_cfg, run_cfg, sampler_cfg,
#             CVConfig(horizon=13, n_folds=4, min_train_periods=52,
#                      draws=600, tune=600))
# print(open(f"{cv['output_dir']}/cv_report.md").read())
