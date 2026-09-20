"""Per-parameter prior / likelihood / posterior densities - the three-curve chart.

The classic Bayesian picture: what you asserted (Prior), what the data alone
says (Data), and the two combined (Posterior). It is the single most useful
chart for answering "is this coefficient a finding or an assumption?", because
the answer is visible rather than inferred from a contraction number:

    Data narrow and far from Prior   -> the data is driving; trust the posterior
    Data wide / absent               -> the number IS your prior
    Data and Prior far apart         -> prior-data conflict; check units first

`idata` carries the prior and posterior draws. The likelihood is not stored, but
for a Gaussian prior and an approximately Gaussian posterior it can be backed
out exactly - see `implied_likelihood`.

Written by `diagnostics.prior_posterior_report` into
`02_convergence/prior_posterior/`, controlled by
`OutputConfig.prior_posterior_plots` / `.prior_posterior_max`.
"""
from __future__ import annotations

import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np

from mmm.reporting.plotting import annotate, figsize, save_fig, units_note


def _kde(x: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Gaussian KDE with Silverman bandwidth. Local, so SciPy stays optional."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return np.zeros_like(grid)
    sd = float(np.std(x))
    q75, q25 = np.percentile(x, [75, 25])
    iqr = float(q75 - q25)
    a = min(sd, iqr / 1.349) if iqr > 0 else sd
    bw = 0.9 * a * n ** (-0.2)
    if not np.isfinite(bw) or bw <= 0:
        return np.zeros_like(grid)
    z = (grid[:, None] - x[None, :]) / bw
    return np.exp(-0.5 * z * z).sum(axis=1) / (n * bw * np.sqrt(2.0 * np.pi))


def implied_likelihood(mu_pr: float, sd_pr: float, mu_po: float, sd_po: float):
    """The Gaussian likelihood that turns this prior into this posterior.

    For a Gaussian prior and an approximately Gaussian posterior, precisions add:

        1 / sd_post^2      = 1 / sd_prior^2      + 1 / sd_like^2
        mu_post / sd_post^2 = mu_prior / sd_prior^2 + mu_like / sd_like^2

    so "what the data alone says", with the prior removed, is recoverable from
    the two things the trace already holds. That is the Data curve.

    Returns None when the posterior is NOT narrower than the prior (contraction
    <= 0). There is then no proper likelihood to draw: the data added nothing,
    or the parameter is unidentified and the posterior actually widened.
    """
    v_pr, v_po = float(sd_pr) ** 2, float(sd_po) ** 2
    if not (np.isfinite(v_pr) and np.isfinite(v_po)) or v_pr <= 0 or v_po <= 0:
        return None
    prec = 1.0 / v_po - 1.0 / v_pr
    if prec <= 1e-12:
        return None
    v_l = 1.0 / prec
    mu_l = v_l * (float(mu_po) / v_po - float(mu_pr) / v_pr)
    if not (np.isfinite(mu_l) and np.isfinite(v_l)):
        return None
    return float(mu_l), float(np.sqrt(v_l))


def verdict_for(contraction: float, shift: float) -> str:
    """One line saying how to report this parameter."""
    if not np.isfinite(contraction):
        return "undetermined"
    if contraction <= 0:
        v = "UNIDENTIFIED - posterior wider than prior"
    elif contraction < 0.2:
        v = "PRIOR-DRIVEN - report as an assumption, not a finding"
    elif contraction > 0.5:
        v = "data-driven"
    else:
        v = "mixed prior and data"
    if np.isfinite(shift) and abs(shift) > 2:
        v += " | PRIOR-DATA CONFLICT"
    return v


def one_parameter_plot(name: str, pr: np.ndarray, po: np.ndarray, role: str,
                       path: str, logged: bool = False) -> None:
    """Draw prior / implied likelihood / posterior for one parameter."""
    mu_pr, sd_pr = float(np.mean(pr)), float(np.std(pr))
    mu_po, sd_po = float(np.mean(po)), float(np.std(po))
    if not (np.isfinite(mu_pr) and np.isfinite(sd_pr) and sd_pr > 0):
        return
    contraction = 1.0 - (sd_po ** 2) / (sd_pr ** 2)
    shift = (mu_po - mu_pr) / sd_pr
    like = implied_likelihood(mu_pr, sd_pr, mu_po, sd_po)

    lo = min(mu_pr - 4 * sd_pr, mu_po - 4 * sd_po)
    hi = max(mu_pr + 4 * sd_pr, mu_po + 4 * sd_po)
    if like is not None:
        lo = min(lo, like[0] - 4 * like[1])
        hi = max(hi, like[0] + 4 * like[1])
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return
    grid = np.linspace(lo, hi, 400)

    fig, ax = plt.subplots(figsize=figsize(7.5, 4.6))
    ax.plot(grid, _kde(pr, grid), ls="-.", color="tab:blue", lw=1.6,
            label=f"Prior      mean {mu_pr:+.4g}  sd {sd_pr:.4g}")
    if like is not None:
        mu_l, sd_l = like
        dens = np.exp(-0.5 * ((grid - mu_l) / sd_l) ** 2) / (
            sd_l * np.sqrt(2.0 * np.pi))
        ax.plot(grid, dens, ls="-", color="black", lw=1.6,
                label=f"Data       mean {mu_l:+.4g}  sd {sd_l:.4g}")
    else:
        ax.plot([], [], ls="-", color="black", lw=1.6,
                label="Data       none - posterior is not narrower than\n"
                      "           the prior, so no proper likelihood exists")
    ax.plot(grid, _kde(po, grid), ls="--", color="tab:red", lw=1.9,
            label=f"Posterior  mean {mu_po:+.4g}  sd {sd_po:.4g}")

    box = (f"contraction   {contraction:+.3f}\n"
           f"mean shift    {shift:+.2f} prior sd\n"
           f"{verdict_for(contraction, shift)}")
    if logged:
        box += (f"\ncoefficient   {np.exp(mu_pr):.4g} -> {np.exp(mu_po):.4g}"
                f"  ({np.exp(mu_po - mu_pr) - 1:+.1%})")
    ax.text(0.015, 0.975, box, transform=ax.transAxes, va="top", ha="left",
            fontsize=7.5, family="monospace",
            bbox=dict(boxstyle="round", fc="#f6f6f6", ec="#bbbbbb", lw=0.6))

    axis = ("log-coefficient, log(beta)  -  a shift of 0.7 is a FACTOR of 2"
            if logged else
            "coefficient (scaled axis: KPI scale per feature scale)")
    annotate(ax, axis, "probability density", name, legend=True,
             legend_fontsize=7)
    units_note(fig,
               "Prior = what you asserted. Data = the likelihood implied by the "
               "prior and posterior, i.e. what the data alone says. Posterior = "
               "the two combined. Data far from Prior with Posterior between "
               "them means your prior is fighting the data; a missing or very "
               f"wide Data curve means this number is your prior.   role: {role}")
    fig.tight_layout()
    save_fig(fig, path)


def write_prior_posterior_plots(pairs, outdir: str, cap: int = 60) -> int:
    """`pairs` = iterable of (label, prior_draws, posterior_draws, role, logged).

    Sorted by |mean shift| so the most contentious parameters are drawn first,
    then capped. Returns how many charts were written.
    """
    cand = []
    for lab, pr, po, role, logged in pairs:
        sd_pr = float(np.std(pr))
        if not np.isfinite(sd_pr) or sd_pr <= 0:
            continue
        shift = abs(float(np.mean(po)) - float(np.mean(pr))) / sd_pr
        cand.append((shift, lab, pr, po, role, logged))
    cand.sort(key=lambda t: -t[0])

    os.makedirs(outdir, exist_ok=True)
    n = 0
    for _, lab, pr, po, role, logged in cand[:cap]:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(lab))[:120]
        try:
            one_parameter_plot(str(lab), pr, po, role,
                               os.path.join(outdir, f"{safe}.png"), logged)
            n += 1
        except Exception as e:  # noqa: BLE001 - a plot must never kill a fit
            print(f"[prior_plots] WARNING: failed for {lab}: {e}")
    if n:
        print(f"[prior_plots] wrote {n} prior/likelihood/posterior charts "
              f"-> {outdir}")
        if len(cand) > cap:
            print(f"[prior_plots] {len(cand) - cap} more not drawn - raise "
                  "OutputConfig.prior_posterior_max")
    return n
