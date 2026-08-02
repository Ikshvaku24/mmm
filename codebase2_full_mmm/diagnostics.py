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

RHAT_WARN = 1.01
RHAT_FAIL = 1.05
ESS_WARN = 400


def convergence_report(idata, outdir: str) -> pd.DataFrame:
    os.makedirs(outdir, exist_ok=True)
    summ = az.summary(idata, round_to=6)
    summ.to_csv(os.path.join(outdir, "posterior_summary_full.csv"))

    lines = []
    n_div = 0
    if hasattr(idata, "sample_stats") and "diverging" in idata.sample_stats:
        n_div = int(idata.sample_stats["diverging"].values.sum())
    worst_rhat = float(summ["r_hat"].max())
    min_ess = float(summ["ess_bulk"].min())
    lines.append(f"max R-hat        : {worst_rhat:.4f}  "
                 f"({'OK' if worst_rhat < RHAT_WARN else 'WARN' if worst_rhat < RHAT_FAIL else 'FAIL'})")
    lines.append(f"min ESS (bulk)   : {min_ess:.0f}  "
                 f"({'OK' if min_ess > ESS_WARN else 'WARN'})")
    lines.append(f"divergences      : {n_div}  ({'OK' if n_div == 0 else 'INVESTIGATE'})")
    try:
        bfmi = az.bfmi(idata)
        lines.append(f"min BFMI         : {float(np.min(bfmi)):.3f}  "
                     f"({'OK' if np.min(bfmi) > 0.3 else 'WARN (poor energy exploration)'})")
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
    if not hasattr(idata, "prior"):
        return
    rows = []
    for v in idata.prior.data_vars:
        if not any(v.startswith(p) for p in var_prefixes):
            continue
        if v not in idata.posterior:
            continue
        pr = idata.prior[v].stack(s=("chain", "draw")).values
        po = idata.posterior[v].stack(s=("chain", "draw")).values
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
