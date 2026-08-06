"""Convergence and prior-vs-posterior diagnostics.

Replaces the invalid `t = mean/mcse_mean` p-value of production_code.py
(mcse shrinks as you sample longer, so 'significance' inflates with more
draws) with the correct tools: R-hat, ESS, divergences, and HDIs.
"""
from __future__ import annotations

import os

import arviz as az
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

from compat import get_group, has_group

RHAT_WARN = 1.01
RHAT_FAIL = 1.05
ESS_WARN = 400
DIVERGENCE_FRAC_FAIL = 0.01   # >1% divergent transitions = geometry problem


def quick_convergence_checks(idata) -> dict:
    """Cheap numeric checks (no report files) - used per CV fold and by the
    guardrail. NaN R-hat/ESS from constant Deterministics is ignored."""
    out = {"max_rhat": np.nan, "min_ess_bulk": np.nan,
           "divergences": 0, "divergence_frac": 0.0, "n_chains": 0}
    try:
        rhat = az.rhat(idata)
        vals = np.concatenate([np.ravel(v.values) for v in rhat.data_vars.values()])
        if np.isfinite(vals).any():
            out["max_rhat"] = float(np.nanmax(vals))
        ess = az.ess(idata)
        evals = np.concatenate([np.ravel(v.values) for v in ess.data_vars.values()])
        if np.isfinite(evals).any():
            out["min_ess_bulk"] = float(np.nanmin(evals))
    except Exception:  # noqa: BLE001  (e.g. single-chain ADVI posterior)
        pass
    try:
        ss = get_group(idata, "sample_stats")
        if "diverging" in ss:
            div = ss["diverging"].values
            out["divergences"] = int(div.sum())
            out["divergence_frac"] = float(div.mean())
    except Exception:  # noqa: BLE001
        pass
    try:
        out["n_chains"] = int(get_group(idata, "posterior").sizes.get("chain", 0))
    except Exception:  # noqa: BLE001
        pass
    return out


def enforce_convergence(checks: dict, policy: str = "warn") -> None:
    """PE-style guardrail: 'fail' raises instead of silently persisting an
    unconverged fit (the PE methodology flags warn-and-continue as a
    production risk)."""
    problems = []
    if np.isfinite(checks.get("max_rhat", np.nan)) and checks["max_rhat"] > RHAT_FAIL:
        problems.append(f"max R-hat {checks['max_rhat']:.3f} > {RHAT_FAIL}")
    if checks.get("divergence_frac", 0.0) > DIVERGENCE_FRAC_FAIL:
        problems.append(f"{checks['divergences']} divergences "
                        f"({checks['divergence_frac'] * 100:.1f}% of transitions)")
    if not problems:
        return
    msg = "convergence guardrail: " + "; ".join(problems)
    if policy == "fail":
        raise RuntimeError(msg + " (on_convergence_failure='fail')")
    print(f"[diagnostics] WARNING - {msg}")


def convergence_report(idata, outdir: str) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    summ = az.summary(idata, round_to=6)
    summ.to_csv(os.path.join(outdir, "posterior_summary_full.csv"))

    lines = []
    n_div = 0
    if has_group(idata, "sample_stats"):
        ss0 = get_group(idata, "sample_stats")
        if "diverging" in ss0:
            n_div = int(ss0["diverging"].values.sum())
    worst_rhat = float(summ["r_hat"].max())
    min_ess = float(summ["ess_bulk"].min())
    min_ess_tail = float(summ["ess_tail"].min()) if "ess_tail" in summ else np.nan
    lines.append(f"max R-hat        : {worst_rhat:.4f}  "
                 f"({'OK' if worst_rhat < RHAT_WARN else 'WARN' if worst_rhat < RHAT_FAIL else 'FAIL'})")
    lines.append(f"min ESS (bulk)   : {min_ess:.0f}  "
                 f"({'OK' if min_ess > ESS_WARN else 'WARN'})")
    if np.isfinite(min_ess_tail):
        lines.append(f"min ESS (tail)   : {min_ess_tail:.0f}  "
                     f"({'OK' if min_ess_tail > ESS_WARN else 'WARN (interval endpoints unstable)'})")
    lines.append(f"divergences      : {n_div}  ({'OK' if n_div == 0 else 'INVESTIGATE'})")
    try:
        bfmi = az.bfmi(idata)
        per_chain = ", ".join(f"{b:.2f}" for b in np.atleast_1d(bfmi))
        lines.append(f"BFMI by chain    : [{per_chain}]  "
                     f"({'OK' if np.min(bfmi) > 0.3 else 'WARN (poor energy exploration)'})")
    except Exception:  # noqa: BLE001
        pass
    try:
        ss = get_group(idata, "sample_stats")
        for key in ("tree_depth", "depth"):
            if key in ss:
                td = ss[key].values
                lines.append(f"max tree depth   : {int(td.max())}  "
                             f"(saturated in {float((td >= td.max()).mean()) * 100:.1f}% "
                             "of steps)" if td.max() >= 10 else
                             f"max tree depth   : {int(td.max())}  (OK)")
                break
    except Exception:  # noqa: BLE001
        pass
    lines.append("")
    lines.append("Worst parameters by R-hat:")
    worst = summ.sort_values("r_hat", ascending=False).head(10)
    lines.append(worst[["mean", "sd", "ess_bulk", "r_hat"]].to_string())
    lines.append("")
    lines.append("NOTE: do not use mean/mcse t-stats for significance. Use the HDI "
                 "and P(direction) columns in the coefficient report instead.")
    report = "\n".join(lines)
    with open(os.path.join(outdir, "convergence_report.txt"), "w") as f:
        f.write(report)
    print("[diagnostics]\n" + report)

    # plots (best effort)
    try:
        az.plot_energy(idata)
        plt.gcf().savefig(os.path.join(outdir, "energy_plot.png"), dpi=130)
        plt.close("all")
    except Exception:  # noqa: BLE001
        pass
    try:
        base_vars = sorted({i.split("[")[0] for i in worst.index[:3]})
        az.plot_trace(idata, var_names=base_vars, compact=True)
        plt.gcf().tight_layout()
        plt.gcf().savefig(os.path.join(outdir, "trace_worst_rhat.png"), dpi=120)
        plt.close("all")
    except Exception:  # noqa: BLE001
        pass
    return summ


def prior_posterior_report(idata, outdir: str,
                           var_prefixes=("pop_beta_", "mu_", "tau_",
                                         "adstock_", "hill_", "sigma_region")) -> None:
    """Contraction = 1 - posterior_var/prior_var. Near 1: data did the work.
    Near 0: the number is your prior, not a finding (report it as such)."""
    if not has_group(idata, "prior"):
        return
    prior = get_group(idata, "prior")
    posterior = get_group(idata, "posterior")
    rows = []
    for v in prior.data_vars:
        if not any(v.startswith(p) for p in var_prefixes):
            continue
        if v not in posterior:
            continue
        pr = prior[v].stack(s=("chain", "draw")).values
        po = posterior[v].stack(s=("chain", "draw")).values
        pr = pr.reshape(-1, pr.shape[-1]) if pr.ndim > 1 else pr.reshape(1, -1)
        po = po.reshape(-1, po.shape[-1]) if po.ndim > 1 else po.reshape(1, -1)
        for i in range(pr.shape[0]):
            pv, qv = float(np.var(pr[i])), float(np.var(po[i]))
            rows.append({"parameter": v if pr.shape[0] == 1 else f"{v}[{i}]",
                         "prior_sd": np.sqrt(pv), "posterior_sd": np.sqrt(qv),
                         "contraction": 1.0 - qv / pv if pv > 0 else np.nan})
    if rows:
        df = pd.DataFrame(rows).sort_values("contraction")
        df.to_csv(os.path.join(outdir, "prior_posterior_contraction.csv"), index=False)
        low = df[df["contraction"] < 0.2]
        if len(low):
            with open(os.path.join(outdir, "convergence_report.txt"), "a") as f:
                f.write(f"\n\nWARNING: {len(low)} parameters have contraction < 0.2 "
                        "(posterior ~= prior; the data says little about them). "
                        "See prior_posterior_contraction.csv\n")
