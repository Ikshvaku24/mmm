"""One joint hierarchical model over all regions (Codebase 1).

Replaces the per-region loop of production_code.py with a single vectorised
PyMC model. Structure per region g, time t (all on the scaled data):

    y[g,t] = alpha_g                                  region intercept (pooled)
           + Fourier seasonality (optional, global)
           + trend_g * t (optional, pooled)
           + sum_j beta[g,j] * X[g,t,j]               features, pooled by config
    y ~ Normal(mu, sigma_g)   or StudentT

Coefficients are built in vectorised "buckets" (pooling mode x sign):

    hierarchical  beta_g = mu + tau * z_g       partial pooling, non-centred
                  (Meridian's beta_gm = beta_m + eta_m * N(0,1); the PE
                  package's mu[group] + sigma[group] * offset)
    independent   beta_g ~ its OWN per-region prior, no pooling
    global        one coefficient shared by every region

Sign constraints are structural: beta = +/- exp(...) can never cross zero.
Per-region priors are supported in both region-aware modes - under
"independent" they are the region's prior outright, under "hierarchical" the
prior mean becomes a fixed offset to the centre that region shrinks toward.
"""
from __future__ import annotations

import numpy as np
import pymc as pm
import pytensor.tensor as pt

from config import ModelConfig
from data_prep import PreparedData


def _region_prior_arrays(specs, region_names, log_scale: bool):
    """(G, k) arrays of per-region prior locations and sds.

    Cells without a per-region override fall back to the feature-level prior, so
    a feature with no overrides yields columns that are constant down the region
    axis - i.e. exactly the old behaviour.
    """
    loc = np.array([[s.prior_mean_for(r) for s in specs] for r in region_names],
                   dtype=float)
    sd = np.array([[s.prior_sd_for(r) for s in specs] for r in region_names],
                  dtype=float)
    return (np.log(loc) if log_scale else loc), sd


def _bucket_betas(model, bname, specs, region_names):
    """Create one vectorised coefficient block; returns tensor (region, k).

    Pooling modes (all specs in a bucket share one, by construction):
      hierarchical  beta_g = f(mu + tau * z_g)          partial pooling
      independent   beta_g = f(own prior per region)    no pooling
      global        beta_g = f(one shared coefficient)  complete pooling

    Under "hierarchical", a per-region prior_mean enters as a FIXED offset that
    shifts the centre region g shrinks toward, so analyst knowledge about a
    particular region is honoured without giving up pooling.
    """
    dim = f"feat_{bname}"
    model.add_coord(dim, [s.name for s in specs])
    n_regions = len(region_names)
    pooling = specs[0].pooling
    sign = specs[0].sign
    signed = sign != "free"
    sgn = 1.0 if sign == "positive" else -1.0
    pref = "logbeta" if signed else "beta"

    # feature-level (population) prior, and the per-region grid
    pop_loc = np.array([s.prior_mean for s in specs], dtype=float)
    pop_loc = np.log(pop_loc) if signed else pop_loc
    pop_sd = np.array([s.prior_sd for s in specs], dtype=float)
    reg_loc, reg_sd = _region_prior_arrays(specs, region_names, log_scale=signed)

    if pooling == "hierarchical":
        mu = pm.Normal(f"mu_{pref}_{bname}", mu=pop_loc, sigma=pop_sd, dims=dim)
        tau = pm.HalfNormal(f"tau_{pref}_{bname}",
                            sigma=np.array([s.regional_sd for s in specs]), dims=dim)
        z = pm.Normal(f"z_beta_{bname}", 0.0, 1.0, dims=("region", dim))
        eta = mu[None, :] + tau[None, :] * z
        # fixed per-region shift of the shrinkage centre (0 where no override)
        offset = reg_loc - pop_loc[None, :]
        if np.any(offset != 0):
            eta = eta + pt.constant(offset)
            pm.Deterministic(f"region_prior_offset_{bname}",
                             pt.constant(offset), dims=("region", dim))
        beta = sgn * pt.exp(eta) if signed else eta
        # population effect (log-normal hierarchy: exp(mu) is the MEDIAN)
        pm.Deterministic(f"pop_beta_{bname}",
                         sgn * pt.exp(mu) if signed else mu, dims=dim)
    elif pooling == "independent":
        # every region has its own prior and its own coefficient - no pooling
        raw = pm.Normal(f"{pref}_{bname}", mu=reg_loc, sigma=reg_sd,
                        dims=("region", dim))
        beta = sgn * pt.exp(raw) if signed else raw
    else:  # global - one coefficient shared by all regions
        b = pm.Normal(f"g{pref}_{bname}", mu=pop_loc, sigma=pop_sd, dims=dim)
        shared = sgn * pt.exp(b) if signed else b
        beta = pt.ones((n_regions, 1)) * shared[None, :]
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
            beta = _bucket_betas(model, bname, specs, pdata.region_names)
            terms.append((pt.constant(X_b) * beta[reg]).sum(axis=1))

        mu = sum(terms)

        # noise: one sigma per region. Default = partial pooling on the log scale
        # so short/noisy regions borrow strength for their noise level too.
        if cfg.pool_sigma:
            mu_ls = pm.Normal("mu_log_sigma", -0.5, 1.0)
            tau_ls = pm.HalfNormal("tau_log_sigma", 0.5)
            z_ls = pm.Normal("z_log_sigma", 0.0, 1.0, dims="region")
            sigma = pm.Deterministic("sigma_region",
                                     pt.exp(mu_ls + tau_ls * z_ls), dims="region")
        else:
            sigma = pm.HalfNormal("sigma_region", 1.0, dims="region")
        s_obs = sigma[reg]

        if cfg.likelihood == "student_t":
            nu_raw = pm.Exponential("nu_minus_2", 0.1)
            nu = pm.Deterministic("nu", nu_raw + 2.0)
            pm.StudentT("y_obs", nu=nu, mu=mu, sigma=s_obs, observed=pdata.y[m_tr])
        else:
            pm.Normal("y_obs", mu=mu, sigma=s_obs, observed=pdata.y[m_tr])

    return model
