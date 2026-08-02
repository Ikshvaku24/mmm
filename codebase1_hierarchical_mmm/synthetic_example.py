"""Runnable smoke test + parameter-recovery check for Codebase 1.

Generates a synthetic panel with KNOWN regional coefficients (pre-transformed
media proxies + controls), runs the full pipeline, and prints true vs
recovered coefficients. Run:  python synthetic_example.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import FeatureSpec, ModelConfig, RunConfig, SamplerConfig
from run_pipeline import run

RNG = np.random.default_rng(7)


def make_data(G=4, T=156):
    regions = [f"R{i+1}" for i in range(G)]
    dates = pd.date_range("2023-01-02", periods=T, freq="W-MON")

    # "pre-transformed" media proxies: campaign bursts smoothed a little
    def media_series():
        x = np.zeros(T)
        for _ in range(RNG.integers(6, 10)):
            start = RNG.integers(0, T - 8)
            x[start:start + RNG.integers(3, 8)] += RNG.gamma(3, 2)
        return np.convolve(x, [0.5, 0.3, 0.2], mode="same")

    feats = {"tv": "positive", "digital": "positive", "print": "positive",
             "price_index": "negative", "distribution": "free", "temperature": "free"}

    true = {}
    rows = []
    for g, r in enumerate(regions):
        X = {}
        X["tv"] = media_series()
        X["digital"] = media_series()
        X["print"] = media_series() * 0.3
        X["price_index"] = 100 + np.cumsum(RNG.normal(0, 0.3, T))
        X["distribution"] = 60 + np.cumsum(RNG.normal(0, 0.15, T))
        X["temperature"] = 20 + 8 * np.sin(2 * np.pi * np.arange(T) / 52.18) \
            + RNG.normal(0, 1.5, T)

        # true effects on the *scaled* axes the model uses
        bt = {"tv": np.exp(RNG.normal(np.log(0.10), 0.25)),
              "digital": np.exp(RNG.normal(np.log(0.06), 0.25)),
              "print": np.exp(RNG.normal(np.log(0.02), 0.25)),
              "price_index": -np.exp(RNG.normal(np.log(0.30), 0.20)),
              "distribution": RNG.normal(0.25, 0.05),
              "temperature": RNG.normal(0.10, 0.03)}
        true[r] = bt

        # replicate the pipeline's scaling to generate y consistently
        y_sc = RNG.normal(0.0, 0.05) * np.ones(T)  # small alpha_g
        for name, sign in feats.items():
            v = X[name]
            if sign == "free":
                v_sc = (v - v.mean()) / (v.std() if v.std() > 0 else 1)
            else:
                pos = v[v > 0]
                v_sc = v / (pos.mean() if len(pos) else 1.0)
            y_sc = y_sc + bt[name] * v_sc
        y_sc = y_sc + 0.25 * np.sin(2 * np.pi * np.arange(T) / 52.18)  # seasonality
        y_sc = y_sc + RNG.normal(0, 0.30, T)                            # noise

        m_g, s_g = 1000 * (g + 2), 150 * (g + 1)
        y = y_sc * s_g + m_g
        for t in range(T):
            rows.append({"date": dates[t], "region": r, "dv": y[t],
                         **{k: X[k][t] for k in X}})
    return pd.DataFrame(rows), true, feats


def main():
    df, true, feats = make_data()
    features = [FeatureSpec(name=n, hierarchical=True, sign=s,
                            prior_mean=(0.05 if s != "free" else 0.0))
                for n, s in feats.items()]
    res = run(df,
              ModelConfig(features=features, fourier_order=2),
              RunConfig(run_name="synthetic_check", holdout_periods=13),
              SamplerConfig(draws=600, tune=600, chains=2, sampler="numpyro"))

    coef = res["coefficients"]
    print("\n=== parameter recovery (posterior median vs truth) ===")
    for name in feats:
        sub = coef[(coef.feature == name) & (coef.region != "__population__")]
        for _, row in sub.iterrows():
            t = true[row.region][name]
            hit = row["hdi_5"] <= t <= row["hdi_95"]
            print(f"{name:14s} {row.region}: true={t:+.3f}  est={row['median']:+.3f} "
                  f"[{row['hdi_5']:+.3f},{row['hdi_95']:+.3f}] "
                  f"{'OK' if hit else 'MISS'}")


if __name__ == "__main__":
    main()
