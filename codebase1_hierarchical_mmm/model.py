"""One joint hierarchical model over all regions (Codebase 1).

Replaces the per-region loop of production_code.py with a single vectorised
PyMC model. Structure per region g, time t (all on the scaled data):

    y[g,t] = alpha_g                                  region intercept (pooled)
           + Fourier seasonality (optional, global)
           + trend_g * t (optional, pooled)
           + sum_j beta[g,j] * X[g,t,j]               features, pooled by config
    y ~ Normal(mu, sigma_g)   or StudentT

Coefficients are built in vectorised "buckets" (hierarchical/global x
positive/negative/free) using the non-centred parameterisation
(mu + tau * z, z~N(0,1)) - the same construction as Meridian's
beta_gm = beta_m + eta_m * N(0,1) and the PE model's hierarchy.
Sign constraints are structural: beta = +/- exp(...) can never cross zero.
"""
from __future__ import annotations

import numpy as np
import pymc as pm
import pytensor.tensor as pt

from config import ModelConfig
from data_prep import PreparedData


def _bucket_betas(model, bname, specs, n_regions):
    """Create one vectorised coefficient block; returns tensor (region, k)."""
    dim = f"feat_{bname}"
    model.add_coord(dim, [s.name for s in specs])
    hier = specs[0].hierarchical
    sign = specs[0].sign

    if sign == "free":
        loc = np.array([s.prior_mean for s in specs])
        sd = np.array([s.prior_sd for s in specs])
        if hier:
            mu = pm.Normal(f"mu_beta_{bname}", mu=loc, sigma=sd, dims=dim)
            tau = pm.HalfNormal(f"tau_beta_{bname}",
                                sigma=np.array([s.regional_sd for s in specs]), dims=dim)
            z = pm.Normal(f"z_beta_{bname}", 0.0, 1.0, dims=("region", dim))
            beta = mu[None, :] + tau[None, :] * z
            pm.Deterministic(f"pop_beta_{bname}", mu, dims=dim)
        else:
            b = pm.Normal(f"gbeta_{bname}", mu=loc, sigma=sd, dims=dim)
            beta = pt.ones((n_regions, 1)) * b[None, :]
    else:
        sgn = 1.0 if sign == "positive" else -1.0
        loc = np.log(np.array([s.prior_mean for s in specs]))
        sd = np.array([s.prior_sd for s in specs])
        if hier:
            mu = pm.Normal(f"mu_logbeta_{bname}", mu=loc, sigma=sd, dims=dim)
            tau = pm.HalfNormal(f"tau_logbeta_{bname}",
                                sigma=np.array([s.regional_sd for s in specs]), dims=dim)
            z = pm.Normal(f"z_beta_{bname}", 0.0, 1.0, dims=("region", dim))
            beta = sgn * pt.exp(mu[None, :] + tau[None, :] * z)
            # population MEDIAN effect (log-normal hierarchy: exp(mu) is the median)
            pm.Deterministic(f"pop_beta_{bname}", sgn * pt.exp(mu), dims=dim)
        else:
            lb = pm.Normal(f"glogbeta_{bname}", mu=loc, sigma=sd, dims=dim)
            beta = sgn * (pt.ones((n_regions, 1)) * pt.exp(lb)[None, :])
    return pm.Deterministic(f"beta_{bname}", beta, dims=("region", dim))


def build_model(pdata: PreparedData, cfg: ModelConfig) -> pm.Model:
    m_tr = pdata.train_mask
    reg = pdata.region_idx[m_tr]
    G = len(pdata.region_names)
    coords = {"region": pdata.region_names}

    with pm.Model(coords=coords) as model:
        terms = []

        # baseline: hierarchical region intercept (Meridian: tau_g)
        mu_a = pm.Normal("mu_alpha", 0.0, cfg.alpha_prior_sd)
        tau_a = pm.HalfNormal("tau_alpha", cfg.alpha_regional_sd)
        z_a = pm.Normal("z_alpha", 0.0, 1.0, dims="region")
        alpha = pm.Deterministic("alpha_region", mu_a + tau_a * z_a, dims="region")
        terms.append(alpha[reg])

        # seasonality (light version of Meridian's spline mu_t)
        if pdata.X_fourier is not None:
            model.add_coord("fourier", pdata.fourier_names)
            bf = pm.Normal("beta_fourier", 0.0, 0.3, dims="fourier")
            terms.append(pt.dot(pdata.X_fourier[m_tr], bf))

        if cfg.include_trend:
            mu_t = pm.Normal("mu_trend", 0.0, 0.2)
            tau_t = pm.HalfNormal("tau_trend", 0.2)
            z_t = pm.Normal("z_trend", 0.0, 1.0, dims="region")
            btr = pm.Deterministic("beta_trend_region", mu_t + tau_t * z_t, dims="region")
            terms.append(btr[reg] * pdata.t[m_tr])

        # feature buckets
        for bname, specs in pdata.buckets.items():
            if not specs:
                continue
            cols = [pdata.feature_index[s.name] for s in specs]
            X_b = pdata.X[m_tr][:, cols]
            beta = _bucket_betas(model, bname, specs, G)
            terms.append((pt.constant(X_b) * beta[reg]).sum(axis=1))

        mu = sum(terms)

        # noise: one sigma per region (production code had one per region too,
        # but estimated in isolation; here they share one joint fit)
        sigma = pm.HalfNormal("sigma_region", 1.0, dims="region")
        s_obs = sigma[reg]

        if cfg.likelihood == "student_t":
            nu_raw = pm.Exponential("nu_minus_2", 0.1)
            nu = pm.Deterministic("nu", nu_raw + 2.0)
            pm.StudentT("y_obs", nu=nu, mu=mu, sigma=s_obs, observed=pdata.y[m_tr])
        else:
            pm.Normal("y_obs", mu=mu, sigma=s_obs, observed=pdata.y[m_tr])

    return model
