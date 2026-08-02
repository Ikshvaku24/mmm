"""Sampling with a GPU-capable backend and graceful fallback.

Preferred: nuts_sampler="numpyro" -> the model is JIT-compiled with JAX and
NUTS runs on GPU if one is visible to JAX (Databricks GPU cluster, Colab),
otherwise on JIT-compiled CPU (still typically several x faster than the
default PyMC sampler). Falls back to the default sampler if JAX/NumPyro is
not installed.
"""
from __future__ import annotations

import json
import os
import time

import pymc as pm

from config import SamplerConfig


def describe_backend() -> str:
    try:
        import jax
        devs = jax.devices()
        return f"jax devices: {[str(d) for d in devs]}"
    except Exception as e:  # noqa: BLE001
        return f"jax not available ({e.__class__.__name__})"


def sample_prior(model: pm.Model, scfg: SamplerConfig):
    with model:
        return pm.sample_prior_predictive(draws=scfg.prior_predictive_draws,
                                          random_seed=scfg.seed)


def fit(model: pm.Model, scfg: SamplerConfig, outdir: str | None = None):
    common = dict(draws=scfg.draws, tune=scfg.tune, chains=scfg.chains,
                  target_accept=scfg.target_accept, random_seed=scfg.seed,
                  return_inferencedata=True, idata_kwargs={"log_likelihood": False})

    order = [scfg.sampler] + [s for s in ("numpyro", "pymc") if s != scfg.sampler]
    log = {"backend_info": describe_backend()}
    idata, used, err = None, None, None
    for s in order:
        t0 = time.time()
        try:
            with model:
                if s == "pymc":
                    idata = pm.sample(init="adapt_diag", **common)
                else:
                    kw = dict(common)
                    if scfg.nuts_kwargs:
                        kw["nuts_sampler_kwargs"] = scfg.nuts_kwargs
                    idata = pm.sample(nuts_sampler=s, **kw)
            used = s
            log["sampler"] = s
            log["wall_seconds"] = round(time.time() - t0, 1)
            break
        except Exception as e:  # noqa: BLE001
            err = f"{s}: {e.__class__.__name__}: {e}"
            log[f"failed_{s}"] = err
            continue
    if idata is None:
        raise RuntimeError(f"all samplers failed; last error: {err}")

    log["draws"] = scfg.draws
    log["tune"] = scfg.tune
    log["chains"] = scfg.chains
    log["target_accept"] = scfg.target_accept
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "sampling_log.json"), "w") as f:
            json.dump(log, f, indent=2)
    print(f"[fit] sampled with '{used}' in {log.get('wall_seconds', '?')}s "
          f"({log['backend_info']})")
    return idata
