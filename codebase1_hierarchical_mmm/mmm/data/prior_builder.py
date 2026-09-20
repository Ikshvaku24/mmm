"""Pre-model workflow: turn what the client gave you into a feature-prior file.

Nobody should be hand-computing 65 prior means in a spreadsheet, and the two
ways of getting them are both mechanical:

**A. A vendor decomposition exists.** Invert it. A contribution is
`beta x SUM(x) x dv_scale`, so the coefficient that reproduces it is

    prior_mean[f, r] = contribution[f, r] / support[f, r] / dv_agg[r]

computed per region and then averaged across the regions that actually have
support. That average is the `global_prior_mean` the model samples around.

**B. No decomposition.** Assume marketing delivers a plausible share of sales,
split that share across pillars, and split each pillar across its features in
proportion to spend (METHODOLOGY.md section 2, "Setting a level-1 mean from
spend"). Deliberately gives every feature in a pillar the same implied
efficiency - the least-informative start that still has the right total. How far
each one moves from it afterwards is the result.

Neither path guesses. If the inputs are absent the workflow does nothing and the
run proceeds as usual with whatever prior file is already configured.

The awkward bits, handled explicitly
------------------------------------
* **Zero support.** A feature that never ran in a region contributes nothing
  there and `contribution / 0` is not a number. That region is skipped, and the
  average divides by however many regions DID have support - not by the region
  count. A feature with no support anywhere gets no prior and is reported.
* **Negative contributions.** The model reads `global_prior_mean` as a
  MAGNITUDE and takes direction from `sign_constraint`. A negative contribution
  therefore becomes `sign_constraint=negative` with the absolute value, never a
  negative mean.
* **Signs that disagree across regions.** Flagged, with the sign of the total
  used. A driver that genuinely helps in one region and hurts in another is not
  a prior problem.
* **Combined vendor variables.** A deck reports one "Digital" line where the
  model carries four columns. With a mapping file the SUPPORT is summed first,
  one prior mean is computed for the group, and that mean is replicated to every
  member - which is right, because they share an implied coefficient.

Everything is shown, never just asserted: `prior_calculation.xlsx` carries one
row per feature x region with every intermediate number and the arithmetic that
produced it.
"""
from __future__ import annotations

import os
import warnings

import numpy as np
import pandas as pd

VALID_DV_AGG = ("mean", "sum", "median")

# column sniffing - these files come from decks and clients, never a schema
_FEATURE_HINTS = ("feature", "variable", "driver", "channel", "name")
_REGION_HINTS = ("region", "retailer", "account", "market", "geo", "banner")
_CONTRIB_HINTS = ("contribution", "true_contribution", "vendor_contribution",
                  "volume", "value")
_PILLAR_HINTS = ("pillar", "group", "category")
_SHARE_HINTS = ("pillar_share_pct", "share_pct", "marketing_share_pct",
                "share", "target_share")
_SPEND_HINTS = ("feature_spend", "spend", "investment", "cost")


def _sniff(cols, hints, exclude=()):
    low = {str(c).strip().lower(): c for c in cols if c not in exclude}
    for h in hints:
        if h in low:
            return low[h]
    for h in hints:
        for k, c in low.items():
            if h in k:
                return c
    return None


def _read_any(path: str) -> pd.DataFrame:
    return (pd.read_csv(path) if str(path).lower().endswith(".csv")
            else pd.read_excel(path))


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def load_vendor_contribution(path: str, feature_col=None, region_col=None,
                             value_col=None) -> pd.DataFrame:
    """feature / region / contribution, in whatever shape the deck arrived."""
    df = _read_any(path)
    f = feature_col or _sniff(df.columns, _FEATURE_HINTS)
    r = region_col or _sniff(df.columns, _REGION_HINTS, exclude={f})
    v = value_col or _sniff(df.columns, _CONTRIB_HINTS, exclude={f, r})
    if f is None or v is None:
        raise SystemExit(
            f"{path}: need a feature column and a contribution column; found "
            f"{list(df.columns)}. Pass the column names explicitly.")
    out = pd.DataFrame({
        "feature": df[f].astype(str).str.strip(),
        "region": (df[r].astype(str).str.strip() if r else "__all__"),
        "contribution": pd.to_numeric(df[v], errors="coerce"),
    }).dropna(subset=["contribution"])
    print(f"[prior] vendor contribution: {len(out)} rows, "
          f"{out['feature'].nunique()} features, {out['region'].nunique()} regions")
    return out


def load_pillar_spend(path: str) -> pd.DataFrame:
    """pillar / feature / feature_spend / pillar_share_pct.

    One row per feature. `pillar_share_pct` is a property of the PILLAR, so it
    may be written once or repeated on every row of that pillar; a pillar whose
    rows disagree is an error rather than a silent pick.
    """
    df = _read_any(path)
    f = _sniff(df.columns, _FEATURE_HINTS)
    p = _sniff(df.columns, _PILLAR_HINTS, exclude={f})
    sh = _sniff(df.columns, _SHARE_HINTS, exclude={f, p})
    sp = _sniff(df.columns, _SPEND_HINTS, exclude={f, p, sh})
    if f is None or p is None or sh is None:
        raise SystemExit(
            f"{path}: need feature, pillar and pillar_share_pct columns; found "
            f"{list(df.columns)}. See docs/FEATURE_PRIOR_GUIDE.md for the "
            "expected layout.")
    out = pd.DataFrame({
        "feature": df[f].astype(str).str.strip(),
        "pillar": df[p].astype(str).str.strip(),
        "pillar_share_pct": pd.to_numeric(df[sh], errors="coerce"),
        "feature_spend": (pd.to_numeric(df[sp], errors="coerce")
                          if sp else np.nan),
    })
    per_pillar = out.groupby("pillar")["pillar_share_pct"].nunique(dropna=True)
    bad = per_pillar[per_pillar > 1]
    if len(bad):
        raise SystemExit(
            f"{path}: these pillars have more than one pillar_share_pct: "
            f"{list(bad.index)}. The share belongs to the pillar - write it "
            "once, or repeat the same number on every row of that pillar.")
    out["pillar_share_pct"] = out.groupby("pillar")["pillar_share_pct"] \
        .transform(lambda x: x.ffill().bfill())
    total = out.drop_duplicates("pillar")["pillar_share_pct"].sum()
    print(f"[prior] pillar spend: {len(out)} features across "
          f"{out['pillar'].nunique()} pillars, shares total {total:.1f}%")
    if total > 100.0 + 1e-6:
        warnings.warn(
            f"pillar shares total {total:.1f}%, which is more than all of "
            "sales. The generated priors will imply a decomposition above "
            "100% before the model has seen anything.")
    return out


def support_table(df: pd.DataFrame, features: list, region_col: str,
                  date_col: str = None, train_mask=None) -> pd.DataFrame:
    """SUM of each RAW feature per region - the denominator of the inversion.

    Raw, not scaled: the vendor's contribution is in raw units, so the
    coefficient that reproduces it has to be derived against the raw sum. The
    pipeline's own scaling is applied later and `prior_summary.csv` reports
    both sides.
    """
    rows = []
    d = df if train_mask is None else df[train_mask]
    for region, g in d.groupby(region_col, sort=False):
        for f in features:
            if f not in g.columns:
                continue
            v = pd.to_numeric(g[f], errors="coerce").fillna(0.0)
            rows.append({"region": str(region), "feature": str(f),
                         "support": float(v.sum()),
                         "n_active": int((v != 0).sum())})
    return pd.DataFrame(rows)


def dv_aggregate(df: pd.DataFrame, dv_col: str, region_col: str,
                 how: str = "mean") -> pd.Series:
    """The per-region KPI level the contribution is expressed against."""
    if how not in VALID_DV_AGG:
        raise ValueError(f"dv_aggregation must be one of {VALID_DV_AGG}, "
                         f"got {how!r}")
    v = pd.to_numeric(df[dv_col], errors="coerce")
    return v.groupby(df[region_col].astype(str)).agg(how)


# --------------------------------------------------------------------------- #
# A. invert a vendor decomposition
# --------------------------------------------------------------------------- #
def priors_from_contribution(contrib: pd.DataFrame, support: pd.DataFrame,
                             dv_agg: pd.Series, mapping: dict | None = None
                             ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-feature priors, the full working) from a vendor decomposition.

    Returns the calculation table too, because a prior nobody can check is
    just a number somebody typed.
    """
    mapping = mapping or {}
    sup = support.copy()
    sup["group"] = sup["feature"].map(mapping).fillna(sup["feature"])
    # combined vendor line -> sum the support of its members FIRST, so the one
    # reported contribution is divided by the volume that actually produced it
    grp_sup = (sup.groupby(["group", "region"], as_index=False)
               .agg(support=("support", "sum"),
                    n_active=("n_active", "sum"),
                    members=("feature", lambda x: " + ".join(sorted(x))),
                    n_members=("feature", "size")))

    c = contrib.copy()
    c["group"] = c["feature"].map(mapping).fillna(c["feature"])
    c = (c.groupby(["group", "region"], as_index=False)["contribution"].sum())

    work = c.merge(grp_sup, on=["group", "region"], how="outer")
    work["dv_agg"] = work["region"].map(dv_agg)
    ok = (work["support"].notna() & (work["support"] != 0)
          & work["dv_agg"].notna() & (work["dv_agg"] != 0)
          & work["contribution"].notna())
    work["usable"] = ok
    work["prior_mean_region"] = np.where(
        ok, work["contribution"] / work["support"].replace(0, np.nan)
        / work["dv_agg"].replace(0, np.nan), np.nan)
    work["skipped_because"] = np.where(
        work["contribution"].isna(), "no vendor contribution for this cell",
        np.where(work["support"].fillna(0) == 0,
                 "support is 0 - the feature never ran here, so it contributes "
                 "nothing whatever the coefficient",
                 np.where(work["dv_agg"].fillna(0) == 0,
                          "KPI aggregate is 0", "")))
    work["formula"] = np.where(
        ok, ("contribution / support / dv_agg = "
             + work["contribution"].round(2).astype(str) + " / "
             + work["support"].round(4).astype(str) + " / "
             + work["dv_agg"].round(2).astype(str)), "")

    # average over the regions that HAVE support - divide by that count, not by
    # the number of regions
    agg = (work[work["usable"]]
           .groupby("group", as_index=False)
           .agg(prior_mean_abs=("prior_mean_region",
                                lambda x: float(np.mean(np.abs(x)))),
                n_regions_used=("prior_mean_region", "size"),
                total_contribution=("contribution", "sum"),
                n_positive=("contribution", lambda x: int((x > 0).sum())),
                n_negative=("contribution", lambda x: int((x < 0).sum()))))
    n_all = work.groupby("group", as_index=False)["region"].nunique() \
        .rename(columns={"region": "n_regions_total"})
    agg = agg.merge(n_all, on="group", how="right")
    agg["prior_mean_abs"] = agg["prior_mean_abs"].astype(float)
    agg["n_regions_used"] = agg["n_regions_used"].fillna(0).astype(int)

    # the model reads global_prior_mean as a MAGNITUDE; direction is the
    # sign_constraint. So a negative contribution becomes negative + |mean|.
    agg["sign_constraint"] = np.where(
        agg["total_contribution"].fillna(0) < 0, "negative", "positive")
    agg["mixed_signs"] = (agg["n_positive"].fillna(0) > 0) & \
                         (agg["n_negative"].fillna(0) > 0)
    agg["note"] = np.where(
        agg["n_regions_used"] == 0,
        "NO prior: support is 0 in every region - drop the feature or fix the "
        "extract",
        np.where(agg["mixed_signs"],
                 "contribution sign DIFFERS across regions; the sign of the "
                 "total was used", ""))

    # replicate a group's mean back onto each member feature
    members = sup[["feature", "group"]].drop_duplicates()
    out = members.merge(agg, on="group", how="left")
    out = out.rename(columns={"prior_mean_abs": "global_prior_mean"})
    out["from_combined_group"] = out["group"] != out["feature"]
    cols = ["feature", "group", "global_prior_mean", "sign_constraint",
            "n_regions_used", "n_regions_total", "total_contribution",
            "mixed_signs", "from_combined_group", "note"]
    return out[cols].sort_values("feature"), work.sort_values(["group", "region"])


# --------------------------------------------------------------------------- #
# B. spend-share priors, when there is no decomposition
# --------------------------------------------------------------------------- #
def priors_from_spend(pillar_spend: pd.DataFrame, support: pd.DataFrame,
                      dv_agg: pd.Series, total_sales: float
                      ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """METHODOLOGY section 2: a pillar's share of sales, split by spend.

        contribution_f = pillar_share_pct/100 * total_sales * spend_f/pillar_spend
        prior_mean_f   = contribution_f / support_f / dv_agg

    A pillar whose features have no spend (a baseline pillar usually does not)
    is split by SUPPORT share instead, which is the only other thing that
    distinguishes its features.
    """
    ps = pillar_spend.copy()
    sup_tot = (support.groupby("feature", as_index=False)["support"].sum()
               .rename(columns={"support": "support_total"}))
    ps = ps.merge(sup_tot, on="feature", how="left")
    ps["support_total"] = ps["support_total"].fillna(0.0)

    dv_mean = float(np.mean(list(dv_agg.values))) if len(dv_agg) else np.nan
    rows = []
    for pillar, g in ps.groupby("pillar", sort=False):
        share = float(g["pillar_share_pct"].iloc[0]) / 100.0
        spend = g["feature_spend"].fillna(0.0)
        basis, weights = "spend", spend
        if spend.sum() <= 0:
            # no spend in this pillar (baseline drivers, dummies): split by the
            # only other thing that distinguishes its features
            basis, weights = "support", g["support_total"]
        wsum = float(weights.sum())
        for (_, r), w in zip(g.iterrows(), weights):
            frac = (float(w) / wsum) if wsum > 0 else (1.0 / len(g))
            contribution = share * float(total_sales) * frac
            sup = float(r["support_total"])
            pm = (contribution / sup / dv_mean
                  if sup > 0 and np.isfinite(dv_mean) and dv_mean != 0
                  else np.nan)
            rows.append({
                "feature": r["feature"], "pillar": pillar,
                "pillar_share_pct": r["pillar_share_pct"],
                "split_basis": basis,
                "feature_spend": r["feature_spend"],
                "weight": float(w), "pillar_weight_total": wsum,
                "share_of_pillar": frac,
                "implied_contribution": contribution,
                "support_total": sup, "dv_agg_mean": dv_mean,
                "global_prior_mean": pm,
                "formula": (f"{share:.4f} * {total_sales:,.0f} * {frac:.4f}"
                            f" / {sup:,.4f} / {dv_mean:,.2f}"),
                "note": ("" if sup > 0 else
                         "NO prior: support is 0 - drop the feature"),
            })
    work = pd.DataFrame(rows)
    out = work[["feature", "pillar", "global_prior_mean", "note"]].copy()
    out["sign_constraint"] = "positive"     # a spend-derived prior is a lift
    out["group"] = out["feature"]
    out["from_combined_group"] = False
    return out.sort_values("feature"), work.sort_values(["pillar", "feature"])


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
PRIOR_COLUMNS = ["variable", "region", "pooling", "sign_constraint",
                 "global_prior_mean", "global_prior_sd", "regional_sd_prior",
                 "baseline", "pillar", "contribution_reference",
                 "center_mode", "scale_mode", "prior_sd_basis",
                 "prior_mean_basis"]


def to_prior_file(priors: pd.DataFrame, default_sd: float = 0.5,
                  regional_sd: float = 0.3, pillars: dict | None = None,
                  sd_basis: str = "relative") -> pd.DataFrame:
    """Lay the computed means out as a real feature-prior CSV.

    `global_prior_sd` deliberately defaults to 0.5 (wide), not 0.02. A prior
    derived from a benchmark and then pinned reproduces the benchmark and
    validates nothing - the agreement is circular. Start wide, read
    `contraction`, and tighten only where you can name the evidence.
    """
    pillars = pillars or {}
    out = pd.DataFrame({"variable": priors["feature"]})
    out["region"] = ""
    out["pooling"] = "hierarchical"
    out["sign_constraint"] = priors.get("sign_constraint", "positive")
    out["global_prior_mean"] = priors["global_prior_mean"].round(10)
    out["global_prior_sd"] = default_sd
    out["regional_sd_prior"] = regional_sd
    out["baseline"] = 0
    out["pillar"] = priors["feature"].map(
        pillars) if pillars else priors.get("pillar", "")
    out["contribution_reference"] = "auto"
    out["center_mode"] = ""
    out["scale_mode"] = ""
    out["prior_sd_basis"] = sd_basis
    out["prior_mean_basis"] = "median"
    out = out[PRIOR_COLUMNS]
    missing = priors["global_prior_mean"].isna()
    if missing.any():
        names = list(priors.loc[missing, "feature"])
        warnings.warn(
            f"{int(missing.sum())} features have no computable prior "
            f"({names[:6]}{'...' if len(names) > 6 else ''}). They are written "
            "with a blank global_prior_mean so the file still loads - either "
            "drop them or fill the mean by hand.")
    return out


def write_calculation_workbook(work: pd.DataFrame, priors: pd.DataFrame,
                               outdir: str, basis: str) -> str:
    """The working, so a prior can be checked rather than believed."""
    os.makedirs(outdir, exist_ok=True)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "calculation"
        ws.append(list(work.columns))
        for c in range(1, len(work.columns) + 1):
            ws.cell(row=1, column=c).font = Font(bold=True)
        for _, r in work.iterrows():
            ws.append([None if (isinstance(v, float) and not np.isfinite(v))
                       else v for v in r.tolist()])
        for j, name in enumerate(work.columns, start=1):
            ws.column_dimensions[get_column_letter(j)].width = \
                40 if name in ("feature", "group", "members", "formula",
                               "skipped_because", "note") else 16
        ws.freeze_panes = "B2"

        ws2 = wb.create_sheet("prior_mean")
        ws2.append(list(priors.columns))
        for c in range(1, len(priors.columns) + 1):
            ws2.cell(row=1, column=c).font = Font(bold=True)
        for _, r in priors.iterrows():
            ws2.append([None if (isinstance(v, float) and not np.isfinite(v))
                        else v for v in r.tolist()])
        for j, name in enumerate(priors.columns, start=1):
            ws2.column_dimensions[get_column_letter(j)].width = \
                40 if name in ("feature", "group", "note") else 16

        ws3 = wb.create_sheet("how this was computed")
        for line in _EXPLAIN[basis]:
            ws3.append([line])
        ws3.column_dimensions["A"].width = 110
        path = os.path.join(outdir, "prior_calculation.xlsx")
        wb.save(path)
        return path
    except ImportError:
        work.to_csv(os.path.join(outdir, "prior_calculation.csv"), index=False)
        priors.to_csv(os.path.join(outdir, "prior_calculation_summary.csv"),
                      index=False)
        with open(os.path.join(outdir, "prior_calculation_README.txt"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(_EXPLAIN[basis]))
        return os.path.join(outdir, "prior_calculation.csv")


_EXPLAIN = {
    "contribution": [
        "HOW THE PRIOR MEAN WAS COMPUTED - inverting a vendor decomposition",
        "",
        "A contribution is  beta x SUM(x) x dv_scale, so the coefficient that",
        "reproduces it is:",
        "",
        "    prior_mean[feature, region] = contribution / support / dv_agg",
        "",
        "  contribution  the vendor's number for that feature in that region",
        "  support       SUM of the RAW feature over the modelling window",
        "  dv_agg        the region's KPI aggregate (mean by default)",
        "",
        "The per-region values are then averaged, DIVIDING BY THE NUMBER OF",
        "REGIONS THAT HAD SUPPORT - not by the region count. A feature that",
        "never ran in a region contributes nothing there whatever its",
        "coefficient, so including a zero would drag the average down.",
        "",
        "SIGNS. The model reads global_prior_mean as a MAGNITUDE and takes",
        "direction from sign_constraint. A negative contribution therefore",
        "becomes sign_constraint=negative with the absolute value. Where the",
        "sign differs across regions the sign of the TOTAL is used and the row",
        "is flagged.",
        "",
        "COMBINED VENDOR VARIABLES. Where the deck reports one line for several",
        "model columns, the SUPPORT of the members is summed first, one mean is",
        "computed for the group, and that mean is replicated to every member.",
        "The `members` column names them.",
        "",
        "WHAT TO DO NEXT. global_prior_sd is written WIDE (0.5) on purpose. A",
        "prior derived from a benchmark and then pinned reproduces the benchmark",
        "and validates nothing - the agreement is circular. Fit, read",
        "`contraction` in 02_convergence, and tighten only where you can name",
        "the evidence. See docs/METHODOLOGY.md section 2.",
    ],
    "spend": [
        "HOW THE PRIOR MEAN WAS COMPUTED - spend shares, no decomposition",
        "",
        "    contribution[f] = pillar_share_pct/100 x total_sales",
        "                      x spend[f] / total spend of that pillar",
        "    prior_mean[f]   = contribution[f] / support[f] / dv_agg",
        "",
        "This deliberately gives every feature in a pillar the SAME implied",
        "efficiency. That is not a claim that they are equally efficient - it is",
        "the least-informative starting point that still has the right total.",
        "With a wide global_prior_sd the data has room to move them apart, and",
        "HOW FAR EACH ONE MOVES IS THE RESULT.",
        "",
        "A pillar whose features carry no spend (baseline drivers, dummies) is",
        "split by SUPPORT share instead - the only other thing distinguishing",
        "them. The `split_basis` column says which was used.",
        "",
        "SANITY-CHECK THE TOTAL, NOT THE PARTS. If the pillar shares say",
        "marketing is 15% of sales and the fitted model returns 7%, that is a",
        "finding worth investigating (usually a missing category or competitor",
        "variable), not a prior to force.",
        "",
        "See docs/METHODOLOGY.md section 2 and docs/FEATURE_PRIOR_GUIDE.md.",
    ],
}


# --------------------------------------------------------------------------- #
# the workflow
# --------------------------------------------------------------------------- #
def run_pre_model(settings, df=None, outdir: str | None = None) -> dict:
    """Generate a sample prior file (and its working) from whatever was given.

    Returns {} and does nothing when neither a vendor contribution nor a
    pillar/spend file is configured - the run then proceeds with the prior file
    already named by `data.feature_priors`.
    """
    data = settings.data
    contrib_path = data.get("vendor_contribution")
    spend_path = data.get("pillar_spend")
    if not contrib_path and not spend_path:
        return {}

    from mmm.reporting.benchmark import load_mapping
    if df is None:
        from mmm.core.settings import load_panel
        df = load_panel(settings)
    outdir = outdir or data.get("pre_model_dir") or "pre_model_outputs"
    os.makedirs(outdir, exist_ok=True)

    run = settings.run
    features = [s.name for s in settings.model.features] or [
        c for c in df.columns
        if c not in (run.date_col, run.region_col, run.dv_col)]
    mapping = load_mapping(data.get("benchmark_mapping")) \
        if data.get("benchmark_mapping") else {}
    sup = support_table(df, features, run.region_col)
    dv = dv_aggregate(df, run.dv_col, run.region_col,
                      data.get("dv_aggregation") or "mean")

    written = {}
    if contrib_path:
        contrib = load_vendor_contribution(contrib_path)
        priors, work = priors_from_contribution(contrib, sup, dv, mapping)
        basis = "contribution"
        # the vendor numbers, in the canonical shape benchmark.py can pre-fill
        contrib.to_csv(os.path.join(outdir, "benchmark_contribution.csv"),
                       index=False)
        written["benchmark_contribution"] = os.path.join(
            outdir, "benchmark_contribution.csv")
    else:
        ps = load_pillar_spend(spend_path)
        total_sales = float(pd.to_numeric(df[run.dv_col],
                                          errors="coerce").sum())
        priors, work = priors_from_spend(ps, sup, dv, total_sales)
        basis = "spend"

    pillars = {}
    if basis == "spend":
        pillars = dict(zip(priors["feature"], priors["pillar"]))
    pf = to_prior_file(priors, pillars=pillars)
    prior_path = os.path.join(outdir, "feature_priors_sample.csv")
    pf.to_csv(prior_path, index=False)
    calc = write_calculation_workbook(work, priors, outdir, basis)

    written.update({"feature_priors_sample": prior_path,
                    "prior_calculation": calc, "basis": basis})
    n_ok = int(pf["global_prior_mean"].notna().sum())
    print(f"[prior] {basis}-based priors for {n_ok}/{len(pf)} features "
          f"-> {prior_path}")
    print(f"[prior] the working -> {calc}")
    print("[prior] REVIEW IT before pointing data.feature_priors at it - "
          "global_prior_sd is deliberately wide (0.5), and a benchmark-derived "
          "prior that is then pinned validates nothing.")
    return written


if __name__ == "__main__":
    import sys

    from mmm.core.settings import load_settings

    cfg = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    res = run_pre_model(load_settings(cfg))
    if not res:
        print("[prior] neither data.vendor_contribution nor data.pillar_spend "
              "is set - nothing to build. Model as usual.")
