"""Preview of the KPI scaling the pipeline applies INTERNALLY.

DO NOT standardise dv before feeding it to the model - pass the raw values.
prepare_data() already standardises per region using TRAIN-window stats
(y_scaled = (dv - mean_g) / sd_g) and every report converts back to original
units. Pre-standardising would (a) make contributions/fit metrics come out in
meaningless units, (b) leak holdout information if you use full-series stats,
and (c) dividing by the regional MAX is worse still - one spike week would
compress the whole region's scale.

This script only SHOWS what the pipeline will do, so you can sanity-check it.
The same numbers appear after a run in 01_data/panel_summary.csv
(kpi_center_used / kpi_scale_used).

Run:  python preview_dv_scaling.py
"""
import pandas as pd

INPUT_PATH = "input_datacube.xlsx"
HOLDOUT_PERIODS = 13          # must match RunConfig.holdout_periods

df = pd.read_excel(INPUT_PATH, engine="openpyxl")
df["date"] = pd.to_datetime(df["date"])

dates = sorted(df["date"].unique())
cutoff = dates[-HOLDOUT_PERIODS] if HOLDOUT_PERIODS > 0 else None
train = df[df["date"] < cutoff] if cutoff is not None else df

stats = train.groupby("region")["dv"].agg(kpi_center_used="mean",
                                          kpi_scale_used="std")
print("per-region KPI scaling the model will use (train window only):\n")
print(stats.round(1))

prev = df.merge(stats, on="region")
prev["dv_scaled"] = ((prev["dv"] - prev["kpi_center_used"])
                     / prev["kpi_scale_used"])
print("\nscaled dv per region (train mean ~0, sd ~1; holdout may drift):\n")
print(prev.groupby("region")["dv_scaled"]
      .describe()[["mean", "std", "min", "max"]].round(2))
print("\nIf any region shows extreme min/max (|z| > 4), inspect those weeks "
      "(partial weeks, one-off events) before fitting.")
