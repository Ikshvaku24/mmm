"""Runnable smoke test + transform-recovery check for Codebase 2.

Generates RAW media with KNOWN adstock decay and Hill saturation, runs the full
pipeline (EDA -> fit -> transform report), then prints true vs learned adstock
decay / half-life / ec. This is the proof that the model learns the transforms
that production preprocessing currently fixes by hand.

Run:  python synthetic_example.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import ChannelSpec, FeatureSpec, ModelConfig, RunConfig, SamplerConfig
from run_pipeline import run
from transforms import adstock_weights_np, apply_adstock_np, half_life, hill_np

RNG = np.random.default_rng(11)

TRUE = {
    "tv": {"alpha": 0.70, "ec": 1.2, "beta": 0.35, "max_lag": 13},
    "digital": {"alpha": 0.25, "ec": 0.8, "beta": 0.20, "max_lag": 8},
}


def make_data(G=3, T=140):
    regions = [f"R{i+1}" for i in range(G)]
    dates = pd.date_range("2023-01-02", periods=T, freq="W-MON")

    def raw_media(scale):
        x = np.zeros(T)
        for _ in range(RNG.integers(8, 12)):
            start = RNG.integers(0, T - 6)
            x[start:start + RNG.integers(2, 6)] += RNG.gamma(4, scale)
        return x

    rows = []
    L = 13
    for g, r in enumerate(regions):
        media = {name: raw_media(30 * (g + 1)) for name in TRUE}
        price = 100 + np.cumsum(RNG.normal(0, 0.25, T))

        # ---- generate y using the SAME transform maths the model assumes ----
        y_sc = np.zeros(T)
        for name, tp in TRUE.items():
            m = media[name]
            pos = m[m > 0]
            m_sc = m / np.median(pos)                       # median scaling
            Mlag = np.zeros((1, T, L, 1))
            for l in range(L):
                Mlag[0, l:, l, 0] = m_sc[:T - l]
            w = adstock_weights_np(np.array([tp["alpha"]]), np.array([0.0]),
                                   L, False, tp["max_lag"])
            ads = apply_adstock_np(Mlag[0, :, :, 0][None, :, :], w)[0, 0]
            sat = (ads + 1e-9) / ((ads + 1e-9) + tp["ec"])  # hill slope=1
            beta_g = tp["beta"] * np.exp(RNG.normal(0, 0.2))
            y_sc += beta_g * sat
        price_sc = (price - price.mean()) / price.std()
        y_sc += -0.30 * price_sc
        y_sc += 0.25 * np.sin(2 * np.pi * np.arange(T) / 52.18)
        y_sc += RNG.normal(0, 0.25, T)

        m_g, s_g = 5000 * (g + 1), 600 * (g + 1)
        y = y_sc * s_g + m_g
        for t in range(T):
            rows.append({"date": dates[t], "region": r, "dv": y[t],
                         "tv_grps": media["tv"][t],
                         "tv_spend": media["tv"][t] * 900,
                         "digital_impr": media["digital"][t],
                         "digital_spend": media["digital"][t] * 40,
                         "price_index": price[t]})
    return pd.DataFrame(rows)


def main():
    df = make_data()
    cfg = ModelConfig(
        channels=[
            ChannelSpec("tv", col="tv_grps", spend_col="tv_spend",
                        adstock="geometric", max_lag=13),
            ChannelSpec("digital", col="digital_impr", spend_col="digital_spend",
                        adstock="geometric", max_lag=8),
        ],
        controls=[FeatureSpec("price_index", sign="negative", prior_mean=0.3)],
        fourier_order=2, include_trend=False,
    )
    res = run(df, cfg,
              RunConfig(run_name="synthetic_check", holdout_periods=13),
              SamplerConfig(draws=600, tune=800, chains=2, sampler="numpyro"))

    tp = res["transforms"].set_index("channel")
    print("\n=== transform recovery (posterior median [90% interval] vs truth) ===")
    for name, t in TRUE.items():
        row = tp.loc[name]
        print(f"{name}: decay true={t['alpha']:.2f}  "
              f"est={row['decay_median']:.2f} "
              f"[{row['decay_hdi_5']:.2f},{row['decay_hdi_95']:.2f}]   "
              f"half-life true={half_life(t['alpha']):.1f}  "
              f"est={row['half_life_periods_median']:.1f}   "
              f"ec true={t['ec']:.2f}  est={row['hill_ec_scaled_median']:.2f} "
              f"[{row['hill_ec_hdi_5']:.2f},{row['hill_ec_hdi_95']:.2f}]")
    print("\nSee outputs/synthetic_check/04_transforms/ for curves and ranges.")


if __name__ == "__main__":
    main()
