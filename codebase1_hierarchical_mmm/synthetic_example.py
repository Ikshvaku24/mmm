"""Runnable smoke test + parameter-recovery check for Codebase 1.

Generates a synthetic panel with KNOWN regional coefficients, runs the full
pipeline, and prints true vs recovered coefficients.

Truth is reported on the scale the model actually estimates: the pipeline
standardises the KPI per region, so the recoverable coefficient is
raw_beta / sd(latent KPI) - the "effective truth". (Comparing against raw
betas would mislabel correct fits as misses.)

Run:  python synthetic_example.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import FeatureSpec, ModelConfig, RunConfig, SamplerConfig
from run_pipeline import run

RNG = np.random.default_rng(7)


def make_data(G=4, T=156, holdout_periods=13):
    regions = [f"R{i + 1}" for i in range(G)]
    dates = pd.date_range("2023-01-02", periods=T, freq="W-MON")
    train_mask = np.arange(T) < (T - holdout_periods)

    def media_series():
        x = np.zeros(T)
        for _ in range(RNG.integers(6, 10)):
            start = RNG.integers(0, T - 8)
            x[start:start + RNG.integers(3, 8)] += RNG.gamma(3, 2)
        return np.convolve(x, [0.5, 0.3, 0.2], mode="same")

    feature_signs = {"tv": "positive", "digital": "positive", "print": "positive",
                     "price_index": "negative", "distribution": "free",
                     "temperature": "free"}

    rows, effective_truth = [], {}
    for g, region in enumerate(regions):
        X = {
            "tv": media_series(),
            "digital": media_series(),
            "print": media_series() * 0.3,
            "price_index": 100 + np.cumsum(RNG.normal(0, 0.3, T)),
            "distribution": 60 + np.cumsum(RNG.normal(0, 0.15, T)),
            "temperature": 20 + 8 * np.sin(2 * np.pi * np.arange(T) / 52.18)
                           + RNG.normal(0, 1.5, T),
        }
        raw_beta = {
            "tv": np.exp(RNG.normal(np.log(0.10), 0.25)),
            "digital": np.exp(RNG.normal(np.log(0.06), 0.25)),
            "print": np.exp(RNG.normal(np.log(0.02), 0.25)),
            "price_index": -np.exp(RNG.normal(np.log(0.30), 0.20)),
            "distribution": RNG.normal(0.25, 0.05),
            "temperature": RNG.normal(0.10, 0.03),
        }

        # replicate the pipeline's feature scaling (train-window stats)
        X_scaled = {}
        for name, sign in feature_signs.items():
            v = X[name]
            if sign == "free":
                mu, sd = v[train_mask].mean(), v[train_mask].std()
                X_scaled[name] = (v - mu) / (sd if sd > 0 else 1.0)
            else:
                pos = v[train_mask][v[train_mask] > 0]
                X_scaled[name] = v / (pos.mean() if len(pos) else 1.0)

        latent = sum(raw_beta[n] * X_scaled[n] for n in feature_signs)
        latent = latent + 0.25 * np.sin(2 * np.pi * np.arange(T) / 52.18)
        latent = latent + RNG.normal(0, 0.30, T)

        # the pipeline standardises the KPI per region, so the coefficient the
        # model estimates is raw_beta / sd(latent) - store THAT as the truth
        l_mu = latent[train_mask].mean()
        l_sd = latent[train_mask].std() or 1.0
        effective_truth[region] = {n: raw_beta[n] / l_sd for n in raw_beta}

        y = 1000 * (g + 2) + 150 * (g + 1) * (latent - l_mu) / l_sd
        for t in range(T):
            rows.append({"date": dates[t], "region": region, "dv": y[t],
                         **{n: X[n][t] for n in X}})
    return pd.DataFrame(rows), effective_truth, feature_signs


def main():
    df, true, feats = make_data()
    features = [FeatureSpec(name=n, hierarchical=True, sign=s,
                            prior_mean=(0.05 if s != "free" else 0.0))
                for n, s in feats.items()]
    res = run(df,
              ModelConfig(features=features, fourier_order=2),
              RunConfig(run_name="synthetic_check", holdout_periods=13,
                        report_draws=400),
              SamplerConfig(draws=1000, tune=1000, chains=4, sampler="numpyro",
                            chain_method="vectorized",
                            allow_sampler_fallback=True))

    coef = res["coefficients"]
    print("\n=== parameter recovery (posterior median vs effective truth) ===")
    n_ok = n_tot = 0
    for name in feats:
        sub = coef[(coef.feature == name) & (coef.region != "__population__")]
        for _, row in sub.iterrows():
            t = true[row.region][name]
            hit = row["hdi_low"] <= t <= row["hdi_high"]
            n_ok += hit
            n_tot += 1
            print(f"{name:14s} {row.region}: true={t:+.3f}  est={row['median']:+.3f} "
                  f"[{row['hdi_low']:+.3f},{row['hdi_high']:+.3f}] "
                  f"{'OK' if hit else 'MISS'}")
    print(f"\n{n_ok}/{n_tot} coefficients inside the 90% HDI "
          f"(expect roughly {int(0.9 * n_tot)} at nominal coverage)")


if __name__ == "__main__":
    main()
