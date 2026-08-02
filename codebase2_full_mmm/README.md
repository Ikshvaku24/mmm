# Codebase 2 — Full MMM: raw media, transforms learned in-model

The Meridian-style upgrade of `production_code.py`: feed **raw** weekly media
(GRPs / clicks / impressions) and the model applies **Adstock → Hill inside the
likelihood**, learning the decay, half-saturation, peak lag and (optionally) slope by
NUTS — then reports exactly what it learned and what ranges the data covered.

Covers **all** goals: 1 (GPU/vectorised), 2 (hierarchical), 3 (stage outputs),
4 (in-model transforms, controllable per feature, with a learned-parameter report),
4.2 (Meridian-style EDA).

> **The one rule:** never feed pre-adstocked / pre-saturated media. That would
> double-transform it (same rule as running Meridian on our data).

## Data contract

One long DataFrame: `date, region, dv` + **raw** media execution columns + (optional)
spend columns + control columns. Panel must be rectangular in the KPI; missing
media/spend cells are zero-filled with a warning (`zero_fill_media`).

## Configuration

**Media channels** (`ChannelSpec`) — this is the per-feature transform control:

| field | meaning |
|---|---|
| `adstock` | `"geometric"` (Meridian default), `"delayed"` (learns peak-lag θ — this is the learned **lag**), `"none"` (linear, no adstock/Hill) |
| `max_lag` | carryover window in periods (per channel) |
| `learn_slope` | learn the Hill slope (default fixed at 1.0, like Meridian) |
| `fix_alpha / fix_ec / fix_slope / fix_theta` | pin any parameter instead of learning it (e.g. to reproduce today's hand-set decay and compare) |
| `spend_col` | enables the ROI report |
| `prior_beta_mean/sd`, `regional_sd` | hierarchical effect-size prior |

**Controls / trade levers** (`FeatureSpec`): linear, with `sign` (positive / negative /
free) and `hierarchical` flags — price/promo/distribution belong here (Meridian's
`non_media_treatments` + `controls` buckets).

Priors on the transforms (defaults follow Meridian): decay `Uniform(0,1)`,
`ec ~ TruncatedNormal(0.8, 0.8, [0.1, 10])` on median-scaled media, slope fixed 1.0.

## Run

```python
from config import ChannelSpec, FeatureSpec, ModelConfig, RunConfig, SamplerConfig
from run_pipeline import run
result = run(df, ModelConfig(channels=[...], controls=[...]),
             RunConfig(run_name="pilot", holdout_periods=13),
             SamplerConfig(sampler="numpyro"))
```

Smoke test / transform recovery: `python synthetic_example.py` — generates raw media
with **known** decay and saturation and shows the model recovering them.

## Outputs (`outputs/<run_name>/`)

| folder | contents |
|---|---|
| `01_data` | panel summary, media scaling medians, KPI plots |
| `02_eda` | `eda_report.md` + CSVs/PNGs: panel gaps, variable summary, media spend shares & zero-weeks, correlation heatmap + high-correlation pairs, VIF, **spend-vs-execution consistency** (catches "spend booked while channel dark"), outliers incl. partial-final-week detection |
| `03_convergence` | sampling log, R-hat/ESS/divergences, prior-posterior contraction, prior-predictive check |
| `04_transforms` | **`transform_parameters.csv`** — learned decay (+ half-life), peak lag, ec in scaled AND raw units, slope, all with 90% intervals; **`adstock_ranges.csv`** — per channel×region: the adstocked ranges actually used, % of active weeks above ec (how far up the saturation curve you operate), carryover share; decay-curve and saturation-curve plots |
| `05_coefficients` | coefficient report (median, HDI, P(>0), population row) + forest plots |
| `06_fit` | R²/MAPE/wMAPE/MAE/coverage per region, train & holdout; fit and residual plots |
| `07_contributions` | contribution totals + shares with HDIs, decomposition chart, **ROI per channel** with credible intervals (if spend given; set `revenue_per_unit` if dv is units, not revenue) |

## Honest caveats (state these when presenting)

- **ROI priors are not replicated.** Meridian's headline calibration trick — putting the
  prior on ROI and inverting it into coefficients (`calculate_beta_x`) — is not
  implemented here; priors are on effect sizes. ROI is *reported* with uncertainty, but
  not *calibrated*. If ROI-prior calibration matters, that is an argument for Meridian
  itself, not for extending this code.
- The trend/seasonality baseline is Fourier + linear trend, simpler than Meridian's
  spline knots with automatic selection.
- No reach/frequency channels, no budget optimizer — again, Meridian features.
- A model that learns transforms needs more data than one that fixes them: watch the
  `02_eda` data/parameter ratio and the `03_convergence` contraction report — channels
  with tiny spend will come back prior-driven (contraction ≈ 0) and should be dropped
  or pinned (`fix_*`).
