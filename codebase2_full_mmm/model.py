"""The full MMM: adstock + Hill learned inside the model (Codebase 2).

Per region g, time t, on the scaled data (compare Meridian's
_joint_dist_base_logic):

    y[g,t] = alpha_g                                   region intercept (pooled)
           + Fourier seasonality + trend_g * t         baseline (Meridian: mu_t)
           + sum_c beta[g,c] * Hill(Adstock(media))    media, transforms LEARNED
           + sum_k gamma[g,k] * X_lin[g,t,k]           controls / linear channels
    y ~ Normal(mu, sigma_g)  or StudentT

Transform parameters (per channel, shared across regions - same as Meridian):
    adstock_alpha ~ Uniform(0,1)            carryover decay
    adstock_theta ~ Uniform(0,theta_max)    peak lag (delayed adstock only)
    hill_ec       ~ TruncatedNormal(0.8,0.8,[0.1,10])   half-saturation (EC50)
    hill_slope    = 1.0 fixed by default (Meridian default), optionally learned
Any of these can be pinned per channel via fix_* in ChannelSpec.
"""
from __future__ import annotations

import numpy as np
import pymc as pm
import pytensor.tensor as pt

from config import ModelConfig
from data_prep import PanelData
from transforms import adstock_weights_pt, apply_adstock_pt, hill_pt


def _bucket_betas(model, bname, specs, n_regions):
    """Vectorised coefficient block for linear features; tensor (region, k)."""
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
            pm.Deterministic(f"pop_beta_{bname}", sgn * pt.exp(mu), dims=dim)
        else:
            lb = pm.Normal(f"glogbeta_{bname}", mu=loc, sigma=sd, dims=dim)
            beta = sgn * (pt.ones((n_regions, 1)) * pt.exp(lb)[None, :])
    return pm.Deterministic(f"beta_{bname}", beta, dims=("region", dim))


def build_model(p: PanelData, cfg: ModelConfig) -> pm.Model:
    G, T_tr = p.n_regions, p.T_train
    tch = cfg.transformed_channels
    C = len(tch)
    coords = {"region": p.region_names}
    if C:
        coords["channel"] = p.channel_names

    with pm.Model(coords=coords) as model:
        # ---- baseline -----------------------------------------------------
        mu_a = pm.Normal("mu_alpha", 0.0, cfg.alpha_prior_sd)
        tau_a = pm.HalfNormal("tau_alpha", cfg.alpha_regional_sd)
        z_a = pm.Normal("z_alpha", 0.0, 1.0, dims="region")
        alpha = pm.Deterministic("alpha_region", mu_a + tau_a * z_a, dims="region")
        mu = alpha[:, None] * pt.ones((1, T_tr))

        if p.X_fourier is not None:
            model.add_coord("fourier", p.fourier_names)
            bf = pm.Normal("beta_fourier", 0.0, 0.3, dims="fourier")
            mu = mu + pt.dot(pt.constant(p.X_fourier[:T_tr]), bf)[None, :]

        if cfg.include_trend:
            mu_t = pm.Normal("mu_trend", 0.0, 0.2)
            tau_t = pm.HalfNormal("tau_trend", 0.2)
            z_t = pm.Normal("z_trend", 0.0, 1.0, dims="region")
            btr = pm.Deterministic("beta_trend_region", mu_t + tau_t * z_t,
                                   dims="region")
            mu = mu + btr[:, None] * pt.constant(p.t[:T_tr])[None, :]

        # ---- media: learned adstock -> Hill -> hierarchical betas ----------
        if C:
            a_l, th_l, ec_l, sl_l = [], [], [], []
            for ch in tch:
                a = (pt.constant(float(ch.fix_alpha)) if ch.fix_alpha is not None
                     else pm.Uniform(f"raw_alpha_{ch.name}", 0.0, 1.0))
                a_l.append(a)
                if ch.adstock == "delayed":
                    th = (pt.constant(float(ch.fix_theta)) if ch.fix_theta is not None
                          else pm.Uniform(f"raw_theta_{ch.name}", 0.0,
                                          min(cfg.theta_max, ch.max_lag - 1.0)))
                else:
                    th = pt.constant(0.0)
                th_l.append(th)
                ec = (pt.constant(float(ch.fix_ec)) if ch.fix_ec is not None
                      else pm.TruncatedNormal(f"raw_ec_{ch.name}", mu=cfg.ec_mu,
                                              sigma=cfg.ec_sigma, lower=cfg.ec_lower,
                                              upper=cfg.ec_upper))
                ec_l.append(ec)
                if ch.learn_slope:
                    sl = pm.LogNormal(f"raw_slope_{ch.name}", 0.0, 0.35)
                else:
                    sl = pt.constant(float(ch.fix_slope if ch.fix_slope else 1.0))
                sl_l.append(sl)

            alpha_v = pm.Deterministic("adstock_alpha", pt.stack(a_l), dims="channel")
            theta_v = pm.Deterministic("adstock_theta", pt.stack(th_l), dims="channel")
            ec_v = pm.Deterministic("hill_ec", pt.stack(ec_l), dims="channel")
            slope_v = pm.Deterministic("hill_slope", pt.stack(sl_l), dims="channel")
            pm.Deterministic("adstock_half_life",
                             pt.log(0.5) / pt.log(pt.clip(alpha_v, 1e-12, 1 - 1e-12)),
                             dims="channel")

            delayed_mask = np.array([ch.adstock == "delayed" for ch in tch])
            w = adstock_weights_pt(alpha_v, theta_v, p.L, delayed_mask, p.lag_mask)
            ads = apply_adstock_pt(p.Mlag[:, :T_tr], w)      # (G,T_tr,C)
            sat = hill_pt(ads, ec_v, slope_v)                # (G,T_tr,C) in [0,1)

            loc = np.log(np.array([ch.prior_beta_mean for ch in tch]))
            sd = np.array([ch.prior_beta_sd for ch in tch])
            mu_lb = pm.Normal("mu_logbeta_media", mu=loc, sigma=sd, dims="channel")
            tau_lb = pm.HalfNormal("tau_logbeta_media",
                                   sigma=np.array([ch.regional_sd for ch in tch]),
                                   dims="channel")
            z_m = pm.Normal("z_beta_media", 0.0, 1.0, dims=("region", "channel"))
            beta_media = pm.Deterministic(
                "beta_media", pt.exp(mu_lb[None, :] + tau_lb[None, :] * z_m),
                dims=("region", "channel"))
            pm.Deterministic("pop_beta_media", pt.exp(mu_lb), dims="channel")
            mu = mu + (sat * beta_media[:, None, :]).sum(axis=2)

        # ---- linear features (controls + adstock='none' channels) ----------
        for bname, specs in p.buckets.items():
            if not specs:
                continue
            cols = [p.lin_index[s.name] for s in specs]
            X_b = p.X_lin[:, :T_tr, :][:, :, cols]
            beta = _bucket_betas(model, bname, specs, G)
            mu = mu + (pt.constant(X_b) * beta[:, None, :]).sum(axis=2)

        # ---- likelihood ------------------------------------------------------
        # region noise: partially pooled on the log scale by default
        if cfg.pool_sigma:
            mu_ls = pm.Normal("mu_log_sigma", -0.5, 1.0)
            tau_ls = pm.HalfNormal("tau_log_sigma", 0.5)
            z_ls = pm.Normal("z_log_sigma", 0.0, 1.0, dims="region")
            sigma = pm.Deterministic("sigma_region",
                                     pt.exp(mu_ls + tau_ls * z_ls), dims="region")
        else:
            sigma = pm.HalfNormal("sigma_region", 1.0, dims="region")
        if cfg.likelihood == "student_t":
            nu_raw = pm.Exponential("nu_minus_2", 0.1)
            nu = pm.Deterministic("nu", nu_raw + 2.0)
            pm.StudentT("y_obs", nu=nu, mu=mu, sigma=sigma[:, None],
                        observed=p.y[:, :T_tr])
        else:
            pm.Normal("y_obs", mu=mu, sigma=sigma[:, None],
                      observed=p.y[:, :T_tr])
    return model
