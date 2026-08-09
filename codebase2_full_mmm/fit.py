"""Sampling with a GPU-capable backend and controlled fallback.

Preferred: nuts_sampler="numpyro" -> the model is JIT-compiled with JAX and
NUTS runs on GPU if one is visible to JAX (Databricks GPU cluster, Colab),
otherwise on JIT-compiled CPU. On a single GPU set
SamplerConfig(chain_method="vectorized") to run all chains in one kernel -
otherwise NumPyro warns "not enough devices to run parallel chains" and runs
them sequentially.

Fallback to the default PyMC sampler only happens when
allow_sampler_fallback=True (off by default: a failed GPU run should raise,
not silently burn CPU hours). The PyMC fallback uses cores=1 because
forking worker processes after JAX has initialised can deadlock.

Every run writes sampling_log.json - a run manifest with package versions,
devices, the sampler REQUESTED vs the sampler actually USED, chain method and
timings. Always check it: a successful run does not by itself mean the GPU
was used.
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
                  return_inferencedata=True)

    if scfg.allow_sampler_fallback:
        order = [scfg.sampler] + [s for s in ("numpyro", "pymc")
                                  if s != scfg.sampler]
    else:
        order = [scfg.sampler]

    log = {
        "backend_info": describe_backend(),
        "versions": package_versions(),
        "sampler_requested": scfg.sampler,
        "chain_method": scfg.chain_method,
        "nuts_kwargs": scfg.nuts_kwargs or {},
        "seed": scfg.seed,
        "draws": scfg.draws, "tune": scfg.tune, "chains": scfg.chains,
        "target_accept": scfg.target_accept,
        "store_log_likelihood": scfg.store_log_likelihood,
        "allow_sampler_fallback": scfg.allow_sampler_fallback,
    }
    idata, used, last_error = None, None, None
    for sampler in order:
        t0 = time.time()
        try:
            with model:
                if sampler == "pymc":
                    # cores=1: forking after JAX initialises can deadlock
                    idata = pm.sample(init="adapt_diag", cores=1, **common)
                else:
                    idata = _sample_external(model, sampler, common, scfg, log)
            used = sampler
            log["sampler_used"] = sampler
            log["wall_seconds"] = round(time.time() - t0, 1)
            break
        except Exception as e:  # noqa: BLE001
            last_error = f"{sampler}: {e.__class__.__name__}: {e}"
            log[f"failed_{sampler}"] = last_error
            print(f"[fit] WARNING: sampler '{sampler}' failed - {last_error}")
    if idata is None:
        raise RuntimeError(f"all configured samplers failed; last error: {last_error}")

    if scfg.store_log_likelihood:
        with model:
            result = pm.compute_log_likelihood(idata, extend_inferencedata=True)
            if result is not None:
                idata = result

    if outdir:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "sampling_log.json"), "w",
                  encoding="utf-8") as f:
            json.dump(log, f, indent=2, default=str)
    if used == "pymc":
        cm_desc = "n/a (pymc sampler)"
    elif log.get("chain_method_applied"):
        cm_desc = scfg.chain_method
    else:
        cm_desc = f"{scfg.chain_method} requested but NOT applied"
    print(f"[fit] sampled with '{used}' in {log.get('wall_seconds', '?')}s "
          f"({log['backend_info']}, chain_method={cm_desc})")
    return idata


def _sample_jax_direct(model, sampler: str, common: dict,
                       scfg: SamplerConfig):
    """Call PyMC's JAX sampling entry point directly.

    Some PyMC versions ACCEPT chain_method through pm.sample but silently
    drop it before it reaches NumPyro - the chains then run sequentially and
    NumPyro warns "There are not enough devices to run parallel chains".
    The entry point in pymc.sampling.jax takes chain_method explicitly, so
    this route is the only one that guarantees the hint lands. Raises
    ImportError/AttributeError/TypeError on API drift; the caller falls back
    to pm.sample.
    """
    from pymc.sampling import jax as pmjax

    kwargs = dict(draws=common["draws"], tune=common["tune"],
                  chains=common["chains"],
                  target_accept=common["target_accept"],
                  random_seed=common["random_seed"], model=model,
                  chain_method=scfg.chain_method)
    if scfg.nuts_kwargs:
        kwargs["nuts_kwargs"] = scfg.nuts_kwargs
    if hasattr(pmjax, "sample_jax_nuts"):        # unified entry, pymc >= 5.16
        return pmjax.sample_jax_nuts(nuts_sampler=sampler, **kwargs)
    return getattr(pmjax, f"sample_{sampler}_nuts")(**kwargs)


def _sample_external(model, sampler: str, common: dict, scfg: SamplerConfig,
                     log: dict):
    """Sample with an external (JAX) sampler across PyMC API versions.

    Route 1 calls pymc.sampling.jax directly (chain_method guaranteed).
    Routes 2-4 go through pm.sample: newest form (chain_method direct,
    kernel args via `nuts=`), legacy form (everything inside
    nuts_sampler_kwargs), then no chain hint at all. Only signature errors
    (TypeError etc.) trigger the next attempt.
    """
    try:
        idata = _sample_jax_direct(model, sampler, common, scfg)
        log["sampling_route"] = "pymc.sampling.jax (direct)"
        log["chain_method_applied"] = True
        return idata
    except (ImportError, AttributeError, TypeError) as e:
        log["direct_route_error"] = f"{e.__class__.__name__}: {e}"
        print(f"[fit] NOTE: direct JAX route unavailable "
              f"({e.__class__.__name__}) - falling back to pm.sample")

    newest = {"chain_method": scfg.chain_method}
    if scfg.nuts_kwargs:
        newest["nuts"] = scfg.nuts_kwargs
    legacy_inner = {"chain_method": scfg.chain_method}
    if scfg.nuts_kwargs:
        legacy_inner["nuts_kwargs"] = scfg.nuts_kwargs
    attempts = [newest, {"nuts_sampler_kwargs": legacy_inner}, {}]

    sig_error = None
    for extra in attempts:
        try:
            idata = pm.sample(nuts_sampler=sampler, **common, **extra)
            log["sampling_route"] = "pm.sample"
            # passed to pm.sample, but some versions drop it silently -
            # sequential progress bars / a NumPyro "not enough devices"
            # warning mean it did NOT take effect
            log["chain_method_applied"] = bool(extra)
            if not extra:
                print("[fit] NOTE: this PyMC version accepted no chain_method "
                      "argument - chains ran with the backend default")
            return idata
        except TypeError as e:
            sig_error = e
            continue
    raise sig_error


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
