"""Pre-modelling EDA (Meridian-style) for the full MMM.

Runs on the RAW long DataFrame before any modelling. Produces, under 02_eda/:
  eda_report.md                     headline findings + pointers
  panel_gaps.csv                    missing (region, date) cells
  variable_summary.csv              mean/sd/min/max/zero%/missing% per variable
  media_summary.csv (+ .png)        activity & spend share, zero-week share
  correlation_matrix.png / .csv     feature x feature (+ dv) correlations
  high_correlations.csv             |r| > 0.7 pairs (collinearity risk)
  vif.csv                           variance inflation factors
  cost_consistency.csv              spend/execution ratio outliers (catches
                                    'spend booked while channel dark' artefacts)
  outliers.csv                      robust-z |z| > 3.5 observations
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

from config import ModelConfig, RunConfig
from outputs import save_fig


def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    scale = 1.4826 * mad if mad > 0 else (x.std() if x.std() > 0 else 1.0)
    return (x - med) / scale


def run_eda(df: pd.DataFrame, run_cfg: RunConfig, cfg: ModelConfig,
            outdir: str) -> dict:
    os.makedirs(outdir, exist_ok=True)
    d = df.copy()
    dc, rc, yc = run_cfg.date_col, run_cfg.region_col, run_cfg.dv_col
    d[dc] = pd.to_datetime(d[dc])

    ch_cols = {c.name: c.col for c in cfg.channels}
    spend_cols = {c.name: c.spend_col for c in cfg.channels if c.spend_col}
    ctl_cols = [s.name for s in cfg.controls]
    all_vars = [yc] + list(ch_cols.values()) + list(spend_cols.values()) + ctl_cols
    findings = []

    # ---- 1. panel completeness --------------------------------------------
    regions = sorted(d[rc].unique())
    dates = sorted(d[dc].unique())
    full = pd.MultiIndex.from_product([regions, dates], names=[rc, dc])
    have = pd.MultiIndex.from_frame(d[[rc, dc]])
    gaps = full.difference(have).to_frame(index=False)
    gaps.to_csv(os.path.join(outdir, "panel_gaps.csv"), index=False)
    if len(gaps):
        findings.append(f"PANEL: {len(gaps)} missing (region, date) cells "
                        "- see panel_gaps.csv")
    diffs = pd.Series(dates).diff().dropna()
    if diffs.nunique() > 1:
        findings.append(f"PANEL: irregular date spacing detected "
                        f"({diffs.value_counts().to_dict()})")

    # ---- 2. variable summary ------------------------------------------------
    rows = []
    for v in all_vars:
        if v not in d.columns:
            continue
        x = pd.to_numeric(d[v], errors="coerce")
        rows.append({"variable": v, "mean": x.mean(), "sd": x.std(),
                     "min": x.min(), "max": x.max(),
                     "zero_pct": float((x == 0).mean() * 100),
                     "missing_pct": float(x.isna().mean() * 100)})
    vs = pd.DataFrame(rows)
    vs.to_csv(os.path.join(outdir, "variable_summary.csv"), index=False)
    for _, r in vs.iterrows():
        if r["missing_pct"] > 0:
            findings.append(f"MISSING: {r['variable']} has "
                            f"{r['missing_pct']:.1f}% missing values")

    # ---- 3. media summary ----------------------------------------------------
    med_rows = []
    for name, col in ch_cols.items():
        x = d[col].fillna(0)
        row = {"channel": name, "zero_week_pct": float((x == 0).mean() * 100),
               "total_activity": float(x.sum())}
        if name in spend_cols:
            row["total_spend"] = float(d[spend_cols[name]].fillna(0).sum())
        med_rows.append(row)
    ms = pd.DataFrame(med_rows)
    if "total_spend" in ms.columns and ms["total_spend"].sum() > 0:
        ms["spend_share_pct"] = ms["total_spend"] / ms["total_spend"].sum() * 100
        tiny = ms[ms["spend_share_pct"] < 1.0]
        for _, r in tiny.iterrows():
            findings.append(f"MEDIA: {r['channel']} carries "
                            f"{r['spend_share_pct']:.1f}% of spend - a channel this "
                            "small is usually prior-driven; consider dropping it")
    ms.to_csv(os.path.join(outdir, "media_summary.csv"), index=False)
    dead = ms[ms["total_activity"] == 0]["channel"].tolist()
    if dead:
        findings.append(f"MEDIA: channels with zero activity overall: {dead} - drop")
    if len(ms):
        fig, ax = plt.subplots(figsize=(7, 3.5))
        ax.bar(ms["channel"], ms.get("spend_share_pct", ms["total_activity"]))
        ax.set_ylabel("spend share %" if "spend_share_pct" in ms.columns
                      else "total activity")
        plt.xticks(rotation=45, ha="right", fontsize=8)
        fig.tight_layout()
        save_fig(fig, os.path.join(outdir, "media_summary.png"))

    # ---- 4. collinearity (on per-region standardised values) -----------------
    feats = list(ch_cols.values()) + ctl_cols
    z = d[[rc] + feats + [yc]].copy()
    for v in feats + [yc]:
        z[v] = pd.to_numeric(z[v], errors="coerce")
        z[v] = z.groupby(rc)[v].transform(
            lambda s: (s - s.mean()) / (s.std() if s.std() > 0 else 1.0))
    corr = z[feats + [yc]].corr()
    corr.to_csv(os.path.join(outdir, "correlation_matrix.csv"))
    fig, ax = plt.subplots(figsize=(max(6, 0.45 * len(corr)),) * 2)
    im = ax.imshow(corr.values, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(corr)))
    ax.set_xticklabels(corr.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(len(corr)))
    ax.set_yticklabels(corr.columns, fontsize=7)
    fig.colorbar(im, shrink=0.8)
    fig.tight_layout()
    save_fig(fig, os.path.join(outdir, "correlation_matrix.png"))

    hi = []
    cols = corr.columns.tolist()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if abs(r) > 0.7:
                hi.append({"var_1": cols[i], "var_2": cols[j], "corr": round(r, 3)})
    pd.DataFrame(hi).to_csv(os.path.join(outdir, "high_correlations.csv"), index=False)
    for h in hi:
        findings.append(f"COLLINEARITY: {h['var_1']} ~ {h['var_2']} "
                        f"(r={h['corr']}) - their split of credit will be uncertain")

    # VIF from the correlation matrix of features only
    try:
        R = z[feats].corr().values
        vif = np.diag(np.linalg.pinv(R))
        vdf = pd.DataFrame({"variable": feats, "vif": vif}).sort_values(
            "vif", ascending=False)
        vdf.to_csv(os.path.join(outdir, "vif.csv"), index=False)
        for _, r in vdf[vdf["vif"] > 10].iterrows():
            findings.append(f"VIF: {r['variable']} = {r['vif']:.1f} (>10)")
    except Exception:  # noqa: BLE001
        pass

    # ---- 5. spend vs execution consistency -----------------------------------
    cc_rows = []
    for name, scol in spend_cols.items():
        ecol = ch_cols[name]
        sub = d[[rc, dc, ecol, scol]].copy()
        sub = sub[(sub[ecol].fillna(0) > 0) | (sub[scol].fillna(0) > 0)]
        if not len(sub):
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            cpu = sub[scol] / sub[ecol]
        med_cpu = np.nanmedian(cpu[np.isfinite(cpu)])
        for _, r in sub.iterrows():
            spend_v = float(r[scol]) if pd.notna(r[scol]) else 0.0
            exec_v = float(r[ecol]) if pd.notna(r[ecol]) else 0.0
            flag = None
            if spend_v > 0 and exec_v == 0:
                flag = "spend_without_execution"
            elif exec_v > 0 and med_cpu and np.isfinite(med_cpu) and med_cpu > 0:
                ratio = (spend_v / exec_v) / med_cpu
                if ratio > 3 or (spend_v > 0 and ratio < 1 / 3):
                    flag = f"cost_per_unit_{ratio:.1f}x_median"
            if flag:
                cc_rows.append({"channel": name, "region": r[rc],
                                "date": r[dc], "execution": exec_v,
                                "spend": spend_v, "flag": flag})
    cc = pd.DataFrame(cc_rows)
    cc.to_csv(os.path.join(outdir, "cost_consistency.csv"), index=False)
    if len(cc):
        by_ch = cc.groupby("channel").size().to_dict()
        findings.append(f"COST: {len(cc)} spend/execution mismatch weeks {by_ch} "
                        "- see cost_consistency.csv (classic 'booked monthly, "
                        "aired differently' artefact)")

    # ---- 6. outliers -----------------------------------------------------------
    out_rows = []
    for v in [yc] + feats:
        for reg, grp in d.groupby(rc):
            x = pd.to_numeric(grp[v], errors="coerce").to_numpy(dtype=float)
            ok = np.isfinite(x)
            if ok.sum() < 8:
                continue
            zscores = _robust_z(x[ok])
            idx = np.where(np.abs(zscores) > 3.5)[0]
            dates_ok = grp[dc].to_numpy()[ok]
            for i in idx:
                out_rows.append({"variable": v, "region": reg,
                                 "date": pd.Timestamp(dates_ok[i]),
                                 "value": x[ok][i], "robust_z": round(zscores[i], 2)})
    od = pd.DataFrame(out_rows)
    od.to_csv(os.path.join(outdir, "outliers.csv"), index=False)
    if len(od):
        last_date = pd.to_datetime(d[dc]).max()
        last_wk = od[od["date"] == last_date]
        if len(last_wk):
            findings.append("OUTLIER: anomalies in the FINAL period "
                            f"({len(last_wk)} vars) - classic partial last week; "
                            "consider dropping it")
        findings.append(f"OUTLIER: {len(od)} robust-z>3.5 observations "
                        "- see outliers.csv")

    # ---- report ------------------------------------------------------------
    n_obs = len(d)
    n_params_rough = (len(ch_cols) * (3 + len(regions)) +
                      len(ctl_cols) * (2 + len(regions)) + 2 * len(regions))
    ratio = n_obs / max(n_params_rough, 1)
    header = [
        "# EDA report",
        f"- observations: {n_obs} ({len(regions)} regions x {len(dates)} periods)",
        f"- rough data/parameter ratio: {ratio:.1f} "
        f"({'adequate' if ratio > 10 else 'LOW - expect wide posteriors'})",
        "",
        f"## Findings ({len(findings)})", ""]
    body = [f"- {f}" for f in findings] or ["- no flags raised"]
    with open(os.path.join(outdir, "eda_report.md"), "w") as f:
        f.write("\n".join(header + body) + "\n")
    print(f"[eda] {len(findings)} findings -> {os.path.join(outdir, 'eda_report.md')}")
    return {"findings": findings, "n_findings": len(findings)}
