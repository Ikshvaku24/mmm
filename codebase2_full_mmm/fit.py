"""Sampling with a GPU-capable backend and graceful fallback.

Preferred: nuts_sampler="numpyro" -> the model is JIT-compiled with JAX and
NUTS runs on GPU if one is visible to JAX (Databricks GPU cluster, Colab),
otherwise on JIT-compiled CPU (still typically several x faster than the
default PyMC sampler). Falls back to the default sampler if JAX/NumPyro is
not installed.

Every run writes sampling_log.json - a run manifest with package versions,
devices, the sampler REQUESTED vs the sampler actually USED, and timings.
Always check it: a successful run does not by itself mean the GPU was used.
"""
from __future__ import annotations

import importlib
import json
import os
import time

import pymc as pm

from config import SamplerConfig

_MANIFEST_PACKAGES = ("pymc", "pytensor", "arviz", "numpy", "pandas",
                      "xarray", "jax", "numpyro")


def package_versions() -> dict:
    out = {}
    for m in _MANIFEST_PACKAGES:
        try:
            out[m] = importlib.import_module(m).__version__
        except Exception:  # noqa: BLE001
            out[m] = "not installed"
    return out


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
    if scfg.sampler == "advi":
        return _fit_advi(model, scfg, outdir)
    common = dict(draws=scfg.draws, tune=scfg.tune, chains=scfg.chains,
                  target_accept=scfg.target_accept, random_seed=scfg.seed,
                  return_inferencedata=True,
                  idata_kwargs={"log_likelihood": scfg.store_log_likelihood})

    order = [scfg.sampler] + [s for s in ("numpyro", "pymc") if s != scfg.sampler]
    log = {
        "backend_info": describe_backend(),
        "versions": package_versions(),
        "sampler_requested": scfg.sampler,
        "nuts_kwargs": scfg.nuts_kwargs or {},
        "seed": scfg.seed,
        "draws": scfg.draws, "tune": scfg.tune, "chains": scfg.chains,
        "target_accept": scfg.target_accept,
        "store_log_likelihood": scfg.store_log_likelihood,
    }
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
            log["sampler_used"] = s
            log["wall_seconds"] = round(time.time() - t0, 1)
            break
        except Exception as e:  # noqa: BLE001
            err = f"{s}: {e.__class__.__name__}: {e}"
            log[f"failed_{s}"] = err
            continue
    if idata is None:
        raise RuntimeError(f"all samplers failed; last error: {err}")

    if outdir:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "sampling_log.json"), "w") as f:
            json.dump(log, f, indent=2, default=str)
    print(f"[fit] sampled with '{used}' in {log.get('wall_seconds', '?')}s "
          f"({log['backend_info']})")
    return idata


def _fit_advi(model: pm.Model, scfg: SamplerConfig, outdir: str | None = None):
    """Mean-field ADVI - the PE-package fast path for CV / exploration.

    Caveat (same as the PE methodology states): mean-field VI ignores posterior
    correlations, so uncertainty intervals are optimistic and R-hat is
    unavailable (single 'chain'). Use for relative comparison across folds or
    configs; refit the final model with NUTS.
    """
    t0 = time.time()
    with model:
        approx = pm.fit(n=scfg.advi_iters, method="advi", random_seed=scfg.seed,
                        progressbar=False)
        idata = approx.sample(draws=scfg.draws * scfg.chains)
    log = {"backend_info": describe_backend(), "versions": package_versions(),
           "sampler_requested": "advi", "sampler_used": "advi",
           "advi_iters": scfg.advi_iters,
           "posterior_draws": scfg.draws * scfg.chains, "seed": scfg.seed,
           "wall_seconds": round(time.time() - t0, 1),
           "note": "mean-field ADVI: intervals optimistic, no R-hat; "
                   "use NUTS for the final fit"}
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "sampling_log.json"), "w") as f:
            json.dump(log, f, indent=2, default=str)
    print(f"[fit] ADVI ({scfg.advi_iters} iters) in {log['wall_seconds']}s")
    return idata
