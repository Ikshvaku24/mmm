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
| `03_convergence` | `sampling_log.json` run manifest (versions, devices, sampler requested vs used, timings), R-hat / bulk+tail ESS / divergences / per-chain BFMI / tree-depth report, prior-posterior contraction, prior-predictive check |
| `04_transforms` | **`transform_parameters.csv`** — learned decay (+ half-life), peak lag, ec in scaled AND raw units, slope, all with 90% HDIs; **`adstock_ranges.csv`** — per channel×region: the adstocked ranges actually used, % of active weeks above ec (how far up the saturation curve you operate), carryover share; decay-curve and saturation-curve plots |
| `05_coefficients` | coefficient report: median, sd, true 90% HDI, P(>0), population row, **original-unit conversions** (linear features: KPI units per raw unit; media: KPI units at full saturation) and **data-support flags** with `support_warnings.txt` |
| `06_fit` | R²/MAPE/wMAPE/MAE per region, train & holdout, with **two coverage columns**: `coverage_90_pred_pct` (posterior predictive — judge holdout by this) and `coverage_90_mean_pct` (mean-response); fit plots show both bands |
| `07_contributions` | contribution totals + shares with HDIs, decomposition chart, **ROI per channel** with credible intervals (if spend given; set `revenue_per_unit` if dv is units, not revenue) |

## Cross-validation (expanding window / rolling origin)

```python
from config import CVConfig
from cross_validation import run_cv
cv = run_cv(df, cfg, RunConfig(run_name="pilot"),
            SamplerConfig(sampler="numpyro"), CVConfig(horizon=13, n_folds=5))
```

Same expanding-window design as codebase 1 (and the PE package's temporal CV): each
fold refits on an expanding training window — transform parameters re-learned per
fold, scaling stats recomputed, no leakage — and predicts the next `horizon` periods.
Outputs in `08_cross_validation/`: per-fold and mean±sd metrics (wMAPE,
region-weighted MAPE, CRPS, predictive coverage, convergence flags), coefficient
stability across folds, and — specific to this codebase — **transform stability**
(`cv_transform_stability.csv` + `.png`): if the learned adstock decay or half-saturation
point moves materially with the origin, the response curves (and any budget
conclusion) are not yet pinned down by the data — consider `fix_*` pins or more
history. `cv_report.md` gives the headline readout.

CV here is *more* important than in codebase 1: learned transforms add flexibility
and therefore overfitting risk. Each fold is a full refit — for sweeps use
`SamplerConfig(sampler="advi")` (PE convention: ADVI for CV speed, NUTS for the final
fit; ADVI understates uncertainty) or `CVConfig(draws=..., tune=...)`.

Not used, deliberately: purged/embargoed CV (adstock only carries the *past* forward
and media is known in advance — carryover into the test window is information, not
leakage), PSIS-LOO as primary (autocorrelation breaks exchangeability), and
leave-region-out CV.

## Convergence guardrail

`RunConfig(on_convergence_failure="fail")` makes the pipeline raise on max
R-hat > 1.05 or >1% divergent transitions instead of persisting a bad fit
(PE-style guardrail; default `"warn"`).

**Interpretation notes** (from the AI code review of codebase 1, applied here too):
intervals are true HDIs (`hdi_low`/`hdi_high`); holdout coverage uses posterior
predictive draws (parameter uncertainty + likelihood noise); scaled coefficients are
per-region-relative — use the `*_orig_units` columns for absolute comparisons; region
noise is partially pooled on the log scale by default (`pool_sigma=False` to disable).

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
