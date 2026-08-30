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
from plotting import annotate, figsize, save_fig, units_note

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
        az.plot_energy(idata, figsize=figsize(7, 4))
        f = plt.gcf()
        ax = f.axes[0] if f.axes else None
        if ax is not None:
            annotate(ax, "energy (nats; unitless HMC diagnostic)",
                     "density (normalised)",
                     "Energy plot - marginal vs transition energy")
        units_note(f, "The two densities should overlap. A narrower transition "
                      "energy than marginal energy means the sampler cannot "
                      "traverse the posterior's tails - reparameterise or "
                      "tighten priors.")
        save_fig(f, os.path.join(outdir, "energy_plot.png"))
        plt.close("all")
    except Exception:  # noqa: BLE001
        pass
    try:
        base_vars = sorted({i.split("[")[0] for i in worst.index[:3]})
        az.plot_trace(idata, var_names=base_vars, compact=True,
                      figsize=figsize(10, 2.2 * max(1, len(base_vars))))
        f = plt.gcf()
        for i, ax in enumerate(f.axes):
            # arviz alternates (density, trace) per row
            if i % 2 == 0:
                annotate(ax, "parameter value (sampled axis)", "density")
            else:
                annotate(ax, "draw number (post-warmup)",
                         "parameter value (sampled axis)")
        units_note(f, "Worst-Rhat parameters. Chains should overlap and look "
                      "like white noise; a drifting or separated chain is "
                      "non-convergence.")
        f.tight_layout()
        save_fig(f, os.path.join(outdir, "trace_worst_rhat.png"))
        plt.close("all")
    except Exception:  # noqa: BLE001
        pass
    return summ


# Parameters whose contraction is not interpretable as "did the data teach us
# anything about this driver". They are still written (with this role) so the
# file is complete, but they are excluded from the plot and the warning count.
_ROLE_RULES = (
    ("z_",                "non-centred offset (N(0,1) by construction)"),
    ("mu_logbeta_",       "population mean, LOG scale"),
    ("mu_beta_",          "population mean"),
    ("tau_logbeta_",      "cross-region spread, LOG scale"),
    ("tau_beta_",         "cross-region spread"),
    ("pop_beta_",         "population effect"),
    ("glogbeta_",         "coefficient (global pooling, log scale)"),
    ("gbeta_",            "coefficient (global pooling)"),
    ("logbeta_",          "coefficient (independent pooling, log scale)"),
    ("beta_fourier",      "seasonality"),
    ("beta_trend",        "trend"),
    ("mu_trend",          "trend"),
    ("tau_trend",         "trend"),
    ("beta_",             "coefficient (region level)"),
    ("mu_alpha",          "intercept"),
    ("tau_alpha",         "intercept"),
    ("alpha_region",      "intercept"),
    ("sigma_region",      "noise"),
    ("mu_log_sigma",      "noise"),
    ("tau_log_sigma",     "noise"),
    ("adstock_",          "adstock transform"),
    ("hill_",             "hill transform"),
)

# roles that are structurally uninformative - see _ROLE_RULES
_UNINFORMATIVE = ("non-centred offset (N(0,1) by construction)",)


def _role_of(var: str) -> str:
    for prefix, role in _ROLE_RULES:
        if var.startswith(prefix):
            return role
    return "other"


def _labelled_slices(da_prior, da_post):
    """Yield (label, prior_draws, posterior_draws) with REAL coordinate names.

    A parameter like `mu_logbeta_hpos` has dims (chain, draw, feat_hpos), and
    `feat_hpos` carries the feature names as coordinates - which is why this
    walks the xarray coords instead of numbering the slices. The old version
    emitted `mu_logbeta_hpos[3]`, which is unreadable and, worse, silently
    reorders if a bucket's membership changes between runs.

    Two extra dims (e.g. `beta_hpos` is (chain, draw, region, feat_hpos)) are
    flattened to "feature @ region".
    """
    dims = [d for d in da_prior.dims if d not in ("chain", "draw")]
    pr = da_prior.stack(s=("chain", "draw"))
    po = da_post.stack(s=("chain", "draw"))
    if not dims:
        yield "", np.asarray(pr.values).ravel(), np.asarray(po.values).ravel()
        return

    def _coords(dim):
        try:
            return [str(c) for c in da_prior.coords[dim].values]
        except Exception:  # noqa: BLE001 - dim without coords: fall back to index
            return [str(i) for i in range(da_prior.sizes[dim])]

    if len(dims) == 1:
        for i, lab in enumerate(_coords(dims[0])):
            yield (lab, np.asarray(pr.isel({dims[0]: i}).values).ravel(),
                   np.asarray(po.isel({dims[0]: i}).values).ravel())
        return
    # >=2 dims: label as "<last dim> @ <first dim>", i.e. "feature @ region"
    d0, d1 = dims[0], dims[1]
    for i, l0 in enumerate(_coords(d0)):
        for j, l1 in enumerate(_coords(d1)):
            yield (f"{l1} @ {l0}",
                   np.asarray(pr.isel({d0: i, d1: j}).values).ravel(),
                   np.asarray(po.isel({d0: i, d1: j}).values).ravel())


def prior_posterior_report(idata, outdir: str, out_cfg=None,
                           skip_prefixes: tuple = ()) -> None:
    """How much did the data move each parameter away from its prior?

    Two numbers per parameter, answering two different questions:

      contraction = 1 - posterior_var/prior_var
          "did the data SHARPEN this?" Near 1: the data did the work. Near 0:
          the posterior is your prior wearing a hat - report it as an
          assumption, not a finding. Negative: the posterior is WIDER than the
          prior, which normally means a mis-specified or fighting likelihood.

      mean_shift_in_prior_sd = (posterior_mean - prior_mean) / prior_sd
          "did the data MOVE this, and where to?" This is the prior-data
          conflict statistic, and it is the one to look at when a posterior
          median disagrees with the prior mean you supplied. |shift| > 2 means
          the data pulled the parameter more than two prior sds away: either
          the prior is wrong or something upstream (usually a scaling mismatch)
          is feeding the model different units than the prior was built on.

    Contraction alone cannot tell those apart - a parameter can contract hard
    around a value nowhere near its prior mean - which is why both are written.

    Every parameter shared by the prior and posterior groups is reported.
    Earlier versions filtered on a prefix allowlist that silently dropped
    `pooling="global"` coefficients (`gbeta_*`/`glogbeta_*`),
    `pooling="independent"` coefficients (`beta_*`/`logbeta_*`) and
    `beta_fourier` entirely; the `role` column replaces that filter so nothing
    is hidden, only labelled.
    """
    if not has_group(idata, "prior"):
        return
    prior = get_group(idata, "prior")
    posterior = get_group(idata, "posterior")
    rows = []
    for v in prior.data_vars:
        if v not in posterior:
            continue
        if skip_prefixes and any(str(v).startswith(p) for p in skip_prefixes):
            continue
        role = _role_of(str(v))
        try:
            slices = list(_labelled_slices(prior[v], posterior[v]))
        except Exception:  # noqa: BLE001 - never let a diagnostic kill a run
            continue
        for lab, pr, po in slices:
            pv, qv = float(np.var(pr)), float(np.var(po))
            p_mean, q_mean = float(np.mean(pr)), float(np.mean(po))
            p_sd = float(np.sqrt(pv))
            rows.append({
                "parameter": f"{v}[{lab}]" if lab else str(v),
                "variable": str(v),
                "name": lab or str(v),          # the FEATURE/region name alone
                "role": role,
                "prior_mean": p_mean,
                "posterior_mean": q_mean,
                "prior_sd": p_sd,
                "posterior_sd": float(np.sqrt(qv)),
                "contraction": 1.0 - qv / pv if pv > 0 else np.nan,
                "mean_shift_in_prior_sd": ((q_mean - p_mean) / p_sd
                                           if p_sd > 0 else np.nan),
                "informative": role not in _UNINFORMATIVE,
            })
    if not rows:
        return
    df = pd.DataFrame(rows).sort_values(["informative", "contraction"],
                                        ascending=[False, True])
    df.to_csv(os.path.join(outdir, "prior_posterior_contraction.csv"), index=False)

    inf = df[df["informative"]]
    low = inf[inf["contraction"] < 0.2]
    conflict = inf[inf["mean_shift_in_prior_sd"].abs() > 2]
    if len(low) or len(conflict):
        with open(os.path.join(outdir, "convergence_report.txt"), "a") as f:
            if len(low):
                f.write(f"\n\nWARNING: {len(low)} parameters have contraction "
                        "< 0.2 (posterior ~= prior; the data says little about "
                        "them - report them as assumptions, not findings).\n")
                for _, r in low.head(10).iterrows():
                    f.write(f"    {r['parameter']}: contraction "
                            f"{r['contraction']:.2f}\n")
            if len(conflict):
                f.write(f"\nWARNING: {len(conflict)} parameters moved more than "
                        "2 prior sds (PRIOR-DATA CONFLICT). Check the prior is "
                        "in the same units as the model - a scaling mismatch "
                        "looks exactly like this.\n")
                for _, r in conflict.head(10).iterrows():
                    f.write(f"    {r['parameter']}: prior "
                            f"{r['prior_mean']:.4g} -> posterior "
                            f"{r['posterior_mean']:.4g} "
                            f"({r['mean_shift_in_prior_sd']:+.1f} prior sd)\n")
            f.write("See prior_posterior_contraction.csv\n")

    _contraction_plot(inf, outdir, out_cfg)


def _contraction_plot(df: pd.DataFrame, outdir: str, out_cfg=None,
                      top_n: int = 40) -> None:
    """Two panels: how much each parameter learned, and whether it moved."""
    if (out_cfg is not None
            and not getattr(out_cfg, "contraction_plot", True)):
        return
    d = df[df["contraction"].notna()]
    if not len(d):
        return
    d = d.sort_values("contraction").head(top_n).iloc[::-1]
    n = len(d)
    try:
        fig, axes = plt.subplots(
            1, 2, figsize=figsize(11, max(3.0, 0.28 * n + 1.2)),
            sharey=True, squeeze=False)
        ax0, ax1 = axes[0][0], axes[0][1]
        y = np.arange(n)

        # left: contraction, coloured by whether the data actually informed it
        colours = ["tab:red" if c < 0.2 else "tab:orange" if c < 0.5
                   else "tab:green" for c in d["contraction"]]
        ax0.barh(y, d["contraction"], color=colours)
        ax0.axvline(0.2, color="grey", ls="--", lw=0.9,
                    label="0.2 = prior-dominated below this")
        ax0.set_yticks(y)
        ax0.set_yticklabels(d["parameter"], fontsize=7)
        ax0.set_xlim(min(-0.05, float(d["contraction"].min()) - 0.05), 1.0)
        annotate(ax0,
                 "contraction = 1 - posterior_var/prior_var  "
                 "(0 = learned nothing, 1 = fully data-driven)",
                 "parameter  [name from the model coords]",
                 "How much the data SHARPENED each parameter", legend=True,
                 legend_fontsize=7)

        # right: prior-data conflict
        shift = d["mean_shift_in_prior_sd"].fillna(0.0)
        ax1.barh(y, shift,
                 color=["tab:red" if abs(s) > 2 else "tab:blue" for s in shift])
        ax1.axvline(0, color="grey", lw=0.9)
        for xv in (-2, 2):
            ax1.axvline(xv, color="tab:red", ls="--", lw=0.9)
        annotate(ax1,
                 "(posterior mean - prior mean) / prior sd   "
                 "(red dashes at +/-2 = prior-data conflict)",
                 "",
                 "Where the data MOVED it, in prior sds")
        fig.suptitle("Prior -> posterior: contraction and prior-data conflict",
                     fontsize=11)
        units_note(fig, "Parameters are on the axis the model samples them on: "
                        "anything named *logbeta* is a LOG-scale coefficient, "
                        "so a shift of 0.7 there is a factor of 2 in KPI units. "
                        "Non-centred z_* offsets are excluded (N(0,1) by "
                        "construction).")
        fig.tight_layout()
        save_fig(fig, os.path.join(outdir, "prior_posterior_contraction.png"))
    except Exception as e:  # noqa: BLE001 - a plot must never kill a fit
        print(f"[diagnostics] WARNING: contraction plot failed: {e}")
