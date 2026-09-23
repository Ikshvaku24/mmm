"""Pre-model workflow: turn what the client gave you into a feature-prior file.

Two input files, both optional, both named in `config.yaml`:

  data.mapping_file   vendor_variable, our_variable[, region][, contribution]
                      which vendor variable is which of ours (samples/mapping_sample.csv)
  data.share_file     section, pillar, pillar_share_pct, variable, spend,
                      variable_share_pct[, sign_constraint]
                      what share of sales each piece is expected to carry
                      (samples/share_sample.csv)

Four cases, in order of precedence:

  a. mapping WITH contributions        -> invert the vendor decomposition
  b. mapping without contributions,    -> build from the shares
     plus a share file
  c. a share file only                 -> build from the shares
  d. neither                           -> the SKELETON: one row per datacube
                                          variable, means and signs blank

If a mapping carries contributions AND a share file is given, the contributions
win for the MEANS wherever they exist - they are evidence, the shares are an
assumption. A variable the vendor never reported falls back to its share-based
prior rather than a blank, and each row's `basis` says which it got. The share
file always supplies the pillar names, the baseline flag and the default signs,
because those are definitions rather than estimates.

A. Inverting a vendor decomposition
-----------------------------------
A contribution is `beta x SUM(x) x dv_scale`, so the coefficient that
reproduces it is

    prior_mean[group, region] = contribution / support / dv_agg

A GROUP is a connected component of the mapping (see `mmm.data.mapping`): one
vendor variable and the several of ours it was split into, usually. Its support
is the SUM of its members' supports, and the one mean it produces is replicated
to every member. A contribution given without a region is national, and is
allocated to the regions in proportion to support, so each region still gets
its own `dv_agg`.

National and regional
---------------------
Every route ends with one coefficient PER REGION. Two files are written from
them, because which one applies is the modeller's pooling decision:

  feature_priors_national.csv   one row per variable. The mean is the SUM of
                                the per-region coefficients over the regions
                                with support, DIVIDED BY THAT COUNT - two
                                regions with support out of five divide by 2.
                                For pooling=hierarchical (the default).
  feature_priors_regional.csv   the same national rows, plus one override row
                                per region with that region's own coefficient.
                                Written with pooling=independent.

A sign-constrained variable cannot carry a region whose coefficient has the
opposite sign (the mean is a magnitude; the sign is feature-level), so that
region gets no override row and falls back to the national one - flagged.

Units. The mean is per RAW unit of the variable, per unit of the region's KPI
`dv_agg`. That is the model's unit only with `scale_mode=none` on the variable
(written for you, and now the default), `run.dv_scale: mean` and
`run.dv_scale_scope: region`. `run_pre_model` warns when the run is set up
otherwise. `dv_agg` is taken over the model's own scaling window
(`run.scaling_window`: the training window by default, the whole panel under
"full"), so it is exactly the number the model divides the KPI by.

B/C. Building from shares
-------------------------
The share file has five SECTIONS, each optional, each with its own formula
(`sales` is the region's total KPI over the window):

  media       pillars inside media, each with a share; spend per variable.
              C = pillar_share x sales x spend / pillar_spend
              (pillar_spend is the sum over that pillar's variables - computed,
              never typed)
  expert      exactly like media: pillars, a share each, spend per variable
  comp_media  a share per variable, no spend split.   C = variable_share x sales
  trade       trade IS a pillar, nothing inside it.   C = variable_share x sales
  baseline    the whole baseline share, and each variable's share WITHIN it -
              which is what the stage-1 baseline-only run (METHODOLOGY section
              1) gives you.     C = baseline_share x variable_share x sales

and then, for every section, `region_coef = C / support / dv_agg`, averaged
over the regions that have support (C carries the variable's sign).

Signs
-----
  vendor contribution   negative -> negative. Positive -> `free` when the
                        variable is a dummy ("dummy" in its name), else
                        positive.
  share file            its own `sign_constraint` column. A blank falls back
                        to: a negative share -> negative; a dummy -> free;
                        comp_media -> negative; everything else -> positive.

For a signed variable the mean is a magnitude. For a `free` one it keeps its
sign, because a free coefficient is Normal(mean, sd) - no exponential.

Names. Every variable named in either file must be in the feature prior file
(`data.feature_priors`) - the prior file may carry MORE variables than the
mapping or share file, never fewer. With no prior file configured yet, the
datacube's columns are the list instead. Every model variable appears in the
output - the ones neither file covers get a blank mean, so nothing silently
falls out of the model.
"""
from __future__ import annotations

__codebase__ = "2026.09.24"   # must equal mmm.__version__

import os
import warnings

import numpy as np
import pandas as pd

from mmm.data.mapping import (ALL_REGIONS, DATACUBE, PRIOR_FILE, _read_any,
                              _sniff, align_regions, check_names,
                              group_contributions, group_members,
                              has_contribution, is_dummy, load_mapping_table)

VALID_DV_AGG = ("mean", "sum", "median")
SECTIONS = ("media", "expert", "comp_media", "trade", "baseline")
# sections whose variables are split by SPEND inside a pillar
SPEND_SECTIONS = ("media", "expert")
# sections where each variable carries its OWN share of sales
DIRECT_SECTIONS = ("comp_media", "trade")
DEFAULT_PILLAR = {"comp_media": "Competitor Media", "trade": "Trade",
                  "baseline": "Baseline"}
DEFAULT_SIGN = {"comp_media": "negative"}

_SECTION_HINTS = ("section", "type", "data_piece", "piece", "block")
_PILLAR_HINTS = ("pillar", "group")
_PSHARE_HINTS = ("pillar_share_pct", "pillar_share", "pillar_pct")
_VAR_HINTS = ("variable", "feature", "our_variable", "name")
_SPEND_HINTS = ("spend", "feature_spend", "investment", "cost")
_VSHARE_HINTS = ("variable_share_pct", "variable_share", "share_pct",
                 "var_share")
_SIGN_HINTS = ("sign_constraint", "sign")


# --------------------------------------------------------------------------- #
# the share file
# --------------------------------------------------------------------------- #
def load_share_file(path: str, known_columns=None,
                    against: str = DATACUBE) -> pd.DataFrame:
    """section / pillar / pillar_share_pct / variable / spend /
    variable_share_pct / sign_constraint - validated per section."""
    if not path:
        return pd.DataFrame()
    if not os.path.exists(path):
        raise SystemExit(f"share file not found: {path}. Fix "
                         "`data.share_file` in config.yaml, or remove it.")
    df = _read_any(path)
    sec = _sniff(df.columns, _SECTION_HINTS)
    var = _sniff(df.columns, _VAR_HINTS, exclude={sec})
    if sec is None or var is None:
        raise SystemExit(
            f"{path}: need a `section` column and a `variable` column; found "
            f"{list(df.columns)}. See samples/share_sample.csv.")
    pil = _sniff(df.columns, _PILLAR_HINTS, exclude={sec, var})
    psh = _sniff(df.columns, _PSHARE_HINTS, exclude={sec, var, pil})
    spd = _sniff(df.columns, _SPEND_HINTS, exclude={sec, var, pil, psh})
    vsh = _sniff(df.columns, _VSHARE_HINTS, exclude={sec, var, pil, psh, spd})
    sgn = _sniff(df.columns, _SIGN_HINTS, exclude={sec, var, pil, psh, spd, vsh})

    def col(name, numeric=False):
        # an optional column that is absent still has to be a Series, so the
        # string methods chained on it below work either way
        if name is None:
            return pd.Series(np.nan if numeric else "", index=df.index,
                             dtype=float if numeric else object)
        s = df[name]
        return pd.to_numeric(s, errors="coerce") if numeric \
            else s.fillna("").astype(str).str.strip()

    out = pd.DataFrame({
        "section": col(sec).str.lower().str.replace(" ", "_")
        .str.replace("-", "_"),
        "pillar": col(pil),
        "pillar_share_pct": col(psh, True),
        "variable": col(var),
        "spend": col(spd, True),
        "variable_share_pct": col(vsh, True),
        "sign_constraint": col(sgn).str.lower(),
    })
    out = out[(out["variable"] != "") & (out["variable"].str.lower() != "nan")]
    bad = sorted(set(out["section"]) - set(SECTIONS))
    if bad:
        raise SystemExit(
            f"{path}: unknown section(s) {bad}. Use one of {list(SECTIONS)}. "
            "(Consumption data such as TDP and price belongs in `baseline`.)")
    dup = out["variable"][out["variable"].duplicated()].unique()
    if len(dup):
        raise SystemExit(f"{path}: variables listed more than once: "
                         f"{list(dup)[:8]}. Each variable belongs to exactly "
                         "one section.")
    check_names(out["variable"], known_columns, path, against=against)

    # default pillars for the sections that are a pillar in themselves
    for s_, p_ in DEFAULT_PILLAR.items():
        m = (out["section"] == s_) & (out["pillar"] == "")
        out.loc[m, "pillar"] = p_

    # a share that belongs to a PILLAR must be one number per pillar
    for s_ in SPEND_SECTIONS + ("baseline",):
        sub = out[out["section"] == s_]
        if sub.empty:
            continue
        n = sub.groupby("pillar")["pillar_share_pct"].nunique(dropna=True)
        clash = n[n > 1]
        if len(clash):
            raise SystemExit(
                f"{path}: section {s_!r}, pillar(s) {list(clash.index)} carry "
                "more than one pillar_share_pct. The share belongs to the "
                "pillar - write it once, or repeat the same number.")
        filled = (sub.groupby("pillar")["pillar_share_pct"]
                  .transform(lambda x: x.ffill().bfill()))
        out.loc[sub.index, "pillar_share_pct"] = filled
        missing = sub.loc[filled.isna(), "pillar"].unique()
        if len(missing):
            raise SystemExit(f"{path}: section {s_!r} pillar(s) {list(missing)} "
                             "have no pillar_share_pct.")

    for s_ in DIRECT_SECTIONS + ("baseline",):
        sub = out[out["section"] == s_]
        if len(sub) and sub["variable_share_pct"].isna().any():
            raise SystemExit(
                f"{path}: section {s_!r} needs variable_share_pct on every row "
                f"({list(sub.loc[sub['variable_share_pct'].isna(), 'variable'])[:5]} "
                "have none).")

    base = out[out["section"] == "baseline"]
    if len(base) and base["variable_share_pct"].abs().sum() > 100 + 1e-6:
        warnings.warn(
            "baseline variable shares add up to "
            f"{base['variable_share_pct'].abs().sum():.1f}% of the baseline. "
            "They are shares WITHIN the baseline, so they should total at most "
            "100 (less, when the intercept/trend/seasonality takes some).")
    total = _implied_total(out)
    print(f"[share] {len(out)} variables across "
          f"{out['section'].nunique()} sections; implied total "
          f"{total:.1f}% of sales")
    if total > 100 + 1e-6:
        warnings.warn(
            f"the shares imply {total:.1f}% of sales - more than all of it. "
            "The generated priors will over-claim before the model has seen "
            "anything.")
    return out.reset_index(drop=True)


def _implied_total(s: pd.DataFrame) -> float:
    """How much of sales the share file claims in total, in percent."""
    tot = 0.0
    for sec_, sub in s.groupby("section"):
        if sec_ in SPEND_SECTIONS:
            tot += sub.drop_duplicates("pillar")["pillar_share_pct"].abs().sum()
        elif sec_ in DIRECT_SECTIONS:
            tot += sub["variable_share_pct"].abs().sum()
        elif sec_ == "baseline":
            tot += float(sub["pillar_share_pct"].abs().iloc[0])
    return float(tot)


def _sign_for(row) -> str:
    """The share file's sign: its own column, else the fallbacks in order."""
    s = str(row.get("sign_constraint", "")).strip().lower()
    if s in ("positive", "negative", "free"):
        return s
    share = row["variable_share_pct"] if pd.notna(row.get("variable_share_pct")) \
        else row.get("pillar_share_pct")
    if pd.notna(share) and float(share) < 0:
        return "negative"
    if is_dummy(row.get("variable", "")):
        return "free"
    return DEFAULT_SIGN.get(row["section"], "positive")


def sign_from_contribution(total: float, name: str) -> str:
    """The vendor-contribution rule. Negative is negative whatever the name;
    a positive dummy is left free - a dummy marks an event whose direction the
    data should decide, and a vendor's positive number for it is thin
    evidence."""
    if pd.notna(total) and float(total) < 0:
        return "negative"
    return "free" if is_dummy(name) else "positive"


# --------------------------------------------------------------------------- #
# the datacube side
# --------------------------------------------------------------------------- #
def support_table(df: pd.DataFrame, features: list, region_col: str,
                  date_col: str = None, train_mask=None) -> pd.DataFrame:
    """SUM of each RAW feature per region - the denominator of the inversion.

    Raw, not scaled: the vendor's contribution is in raw units, so the
    coefficient that reproduces it has to be derived against the raw sum.
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
    return pd.DataFrame(rows, columns=["region", "feature", "support",
                                       "n_active"])


def dv_aggregate(df: pd.DataFrame, dv_col: str, region_col: str,
                 how: str = "mean") -> pd.Series:
    """The per-region KPI level a coefficient is expressed against."""
    if how not in VALID_DV_AGG:
        raise ValueError(f"dv_aggregation must be one of {VALID_DV_AGG}, "
                         f"got {how!r}")
    v = pd.to_numeric(df[dv_col], errors="coerce")
    return v.groupby(df[region_col].astype(str)).agg(how)


def dv_for_model(df: pd.DataFrame, run, how: str = "mean") -> pd.Series:
    """dv_agg as the MODEL will compute its dv_scale: over the same window.

    The divisor has to be the number the model divides the KPI by, and the
    model takes it over `run.scaling_window` - the training window by default,
    the whole panel under "full". The contribution and the support stay
    whole-panel: they are a matched pair (the vendor's number is over the
    whole panel, and so is SUM(x)), and only the KPI scale is the model's.
    """
    window = str(getattr(run, "scaling_window", "train"))
    full = dv_aggregate(df, run.dv_col, run.region_col, how)
    if window == "full":
        print("[prior] dv_agg = the KPI mean over the WHOLE panel "
              "(run.scaling_window: full) - exactly the model's dv_scale")
        return full
    from mmm.data.data_prep import split_train
    mask, holdout, plan = split_train(df[run.date_col], run)
    if holdout == 0:
        return full
    trn = dv_aggregate(df[np.asarray(mask)], run.dv_col, run.region_col, how)
    gap = float((trn / full - 1.0).abs().max() * 100)
    print(f"[prior] dv_agg = the KPI mean over the TRAINING window (the last "
          f"{holdout} {plan.unit} held out; run.scaling_window: train) - the "
          f"model's dv_scale. It differs from the whole-panel mean by up to "
          f"{gap:.1f}%; set run.scaling_window: full to use the whole panel "
          "for both")
    return trn


def region_sales(df: pd.DataFrame, dv_col: str, region_col: str) -> pd.Series:
    """Total KPI per region over the window - what a share is a share OF."""
    v = pd.to_numeric(df[dv_col], errors="coerce")
    return v.groupby(df[region_col].astype(str)).sum()


# --------------------------------------------------------------------------- #
# the shared averaging step
# --------------------------------------------------------------------------- #
VALID_NATIONAL_BASIS = ("average", "weighted")


def _average_over_supported(work: pd.DataFrame, key: str,
                            basis: str = "average") -> pd.DataFrame:
    """The NATIONAL coefficient, both ways, because they can differ by 4x.

    `average`   the SUM of the signed per-region coefficients over the regions
                that HAD support, divided by how many there were. The centre of
                the regions - right when each region gets its own coefficient
                (pooling hierarchical / independent).
    `weighted`  SUM(contribution) / SUM(support x dv_agg): the ONE coefficient
                that reproduces the NATIONAL TOTAL. Right under pooling=global,
                where a single beta serves every region.

    They agree only when the per-region coefficients are equal. When a vendor's
    contribution sits almost entirely in one region (say 99% of it) but the
    variable has support in four, the average divides that region's coefficient
    by four and the national total comes out ~4x short - which is exactly the
    shape of a gap that no amount of correcting can close, because the next
    refit re-imposes it. Both columns are always written so the ratio is
    visible.
    """
    basis = str(basis or "average").strip().lower()
    if basis not in VALID_NATIONAL_BASIS:
        raise ValueError(f"national_basis must be one of {VALID_NATIONAL_BASIS}, "
                         f"got {basis!r}")
    use = work[work["usable"]].copy()
    use["_w"] = use["support"] * use["dv_agg"]          # the total's denominator
    agg = (use.groupby(key, as_index=False)
           .agg(sum_of_region_coefs=("prior_mean_region", "sum"),
                n_regions_used=("prior_mean_region", "size"),
                _contrib=("contribution", "sum"),
                _weight=("_w", "sum")))
    n_all = (work.groupby(key, as_index=False)["region"].nunique()
             .rename(columns={"region": "n_regions_total"}))
    agg = n_all.merge(agg, on=key, how="left")
    agg["n_regions_used"] = agg["n_regions_used"].fillna(0).astype(int)
    agg["national_coef_average"] = np.where(
        agg["n_regions_used"] > 0,
        agg["sum_of_region_coefs"] / agg["n_regions_used"].replace(0, np.nan),
        np.nan)
    agg["national_coef_weighted"] = np.where(
        agg["_weight"].fillna(0) != 0,
        agg["_contrib"] / agg["_weight"].replace(0, np.nan), np.nan)
    agg["national_basis"] = basis
    agg["national_coef"] = agg[f"national_coef_{basis}"]
    # how far the two disagree: >1 means the plain average UNDER-delivers the
    # national total by that factor, which is the R you would otherwise chase
    agg["weighted_over_average"] = (agg["national_coef_weighted"]
                                    / agg["national_coef_average"].replace(0, np.nan))
    return agg.drop(columns=["_contrib", "_weight"])


def _prior_mean(coef: float, sign: str) -> float:
    """What goes in `global_prior_mean`: a magnitude for a signed variable,
    the signed value for a free one (Normal(mean, sd), no exponential)."""
    if pd.isna(coef):
        return np.nan
    return float(coef) if sign == "free" else abs(float(coef))


def _divide(work: pd.DataFrame) -> pd.DataFrame:
    """prior_mean_region = contribution / support / dv_agg (signed), with the
    reason recorded for every cell that could not be computed."""
    c, s, d = work["contribution"], work["support"], work["dv_agg"]
    ok = c.notna() & s.notna() & (s != 0) & d.notna() & (d != 0)
    work["usable"] = ok
    work["prior_mean_region"] = np.where(
        ok, c / s.replace(0, np.nan) / d.replace(0, np.nan), np.nan)
    work["skipped_because"] = np.select(
        [c.isna(), s.fillna(0) == 0, d.fillna(0) == 0],
        ["no contribution for this cell",
         "support is 0 - the feature never ran here, so it contributes "
         "nothing whatever its coefficient",
         "KPI aggregate is 0"], default="")
    work["formula"] = np.where(
        ok, (c.round(2).astype(str) + " / " + s.round(4).astype(str) + " / "
             + d.round(2).astype(str)), "")
    return work


# --------------------------------------------------------------------------- #
# A. invert a vendor decomposition
# --------------------------------------------------------------------------- #
def priors_from_mapping(mapping: pd.DataFrame, support: pd.DataFrame,
                        dv_agg: pd.Series, national_basis: str = "average"
                        ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(one row per OUR variable, the full working) from a mapping that
    carries contributions."""
    mem = group_members(mapping)
    contrib = group_contributions(mapping)
    sup = support.merge(mem.rename(columns={"our_variable": "feature"}),
                        on="feature", how="inner")
    grp_sup = (sup.groupby(["group", "region"], as_index=False)
               .agg(support=("support", "sum"),
                    n_members=("feature", "size"),
                    members=("feature", lambda x: " + ".join(sorted(x)))))

    # a national contribution is spread over the regions by support share, so
    # every region is still divided by its OWN dv_agg
    nat = contrib[contrib["region"] == ALL_REGIONS]
    reg = contrib[contrib["region"] != ALL_REGIONS]
    if len(nat):
        tot = grp_sup.groupby("group")["support"].transform("sum")
        alloc = grp_sup.assign(share=np.where(tot > 0, grp_sup["support"] / tot,
                                              0.0))
        alloc = alloc.merge(nat[["group", "contribution"]], on="group")
        alloc["contribution"] = alloc["contribution"] * alloc["share"]
        alloc["contribution_basis"] = "national, allocated by support"
        reg = pd.concat([reg.assign(contribution_basis="regional"),
                         alloc[["group", "region", "contribution",
                                "contribution_basis"]]], ignore_index=True)
    else:
        reg = reg.assign(contribution_basis="regional")

    work = grp_sup.merge(reg, on=["group", "region"], how="outer")
    work["dv_agg"] = work["region"].map(dv_agg)
    work = _divide(work)

    agg = _average_over_supported(work, "group", national_basis)
    signs = (work.dropna(subset=["contribution"]).groupby("group")
             ["contribution"].agg(total="sum",
                                  n_pos=lambda x: int((x > 0).sum()),
                                  n_neg=lambda x: int((x < 0).sum()))
             .reset_index())
    agg = agg.merge(signs, on="group", how="left")
    agg["mixed_signs"] = (agg["n_pos"].fillna(0) > 0) & (agg["n_neg"].fillna(0) > 0)

    out = mem.rename(columns={"our_variable": "feature"}).merge(
        agg, on="group", how="left")
    # the sign is decided per MEMBER: a group shares a contribution, but
    # whether a member is a dummy is a property of its own name
    out["sign_constraint"] = [
        sign_from_contribution(t, f"{f} {g}")
        for t, f, g in zip(out["total"], out["feature"], out["group"])]
    out["global_prior_mean"] = [
        _prior_mean(c, s) for c, s in zip(out["national_coef"],
                                          out["sign_constraint"])]
    # a signed variable whose regional coefficients disagree can average out
    # on the far side of zero from the sign of the total
    nc = out["national_coef"].fillna(0.0)
    wrong_side = (((out["sign_constraint"] == "positive") & (nc < 0))
                  | ((out["sign_constraint"] == "negative") & (nc > 0)))
    out["note"] = np.select(
        [out["n_regions_used"] == 0, wrong_side, out["mixed_signs"]],
        ["NO prior: support is 0 in every region - drop the feature or fix the "
         "extract",
         "the regional coefficients AVERAGE to the opposite sign of the total "
         "contribution - the magnitude is written, but review this variable",
         "the contribution sign DIFFERS across regions; the sign of the total "
         "was used"], default="")
    out["basis"] = "vendor contribution"
    out["from_combined_group"] = out.groupby("group")["feature"] \
        .transform("size") > 1
    cols = ["feature", "group", "global_prior_mean", "sign_constraint",
            "national_coef", "national_coef_average", "national_coef_weighted",
            "weighted_over_average", "national_basis", "sum_of_region_coefs",
            "n_regions_used", "n_regions_total", "mixed_signs",
            "from_combined_group", "basis", "note"]
    return out[cols].sort_values("feature"), work.sort_values(["group", "region"])


# --------------------------------------------------------------------------- #
# B/C. build from shares
# --------------------------------------------------------------------------- #
def priors_from_shares(shares: pd.DataFrame, support: pd.DataFrame,
                       dv_agg: pd.Series, sales: pd.Series,
                       national_basis: str = "average"
                       ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(one row per variable in the share file, the full working)."""
    rows = []
    for sec_, sub in shares.groupby("section", sort=False):
        pillar_spend = (sub.groupby("pillar")["spend"]
                        .transform(lambda x: x.fillna(0).sum()))
        for idx, r in sub.iterrows():
            f = r["variable"]
            split, note = 1.0, ""
            if sec_ in SPEND_SECTIONS:
                share = r["pillar_share_pct"] / 100.0
                ps = float(pillar_spend.loc[idx])
                sp = float(r["spend"]) if pd.notna(r["spend"]) else 0.0
                if ps <= 0:
                    note = ("pillar has no spend at all - cannot split its "
                            "share. Add spend, or move the variables to a "
                            "direct section")
                    split = np.nan
                elif sp <= 0:
                    note = "no spend given for this variable"
                    split = np.nan
                else:
                    split = sp / ps
                formula = (f"{r['pillar_share_pct']:g}% x sales x "
                           f"{sp:,.0f}/{ps:,.0f}")
            elif sec_ in DIRECT_SECTIONS:
                share = r["variable_share_pct"] / 100.0
                formula = f"{r['variable_share_pct']:g}% x sales"
            else:  # baseline
                share = (r["pillar_share_pct"] / 100.0) \
                    * (r["variable_share_pct"] / 100.0)
                formula = (f"{r['pillar_share_pct']:g}% x "
                           f"{r['variable_share_pct']:g}% x sales")
            sign = _sign_for(r)
            # the coefficient carries the RESOLVED sign: an explicit
            # `negative` on a positive share is negative; `free` keeps the
            # share's own sign
            direction = {"positive": 1.0, "negative": -1.0}.get(
                sign, -1.0 if share < 0 else 1.0)
            for region, s_r in sales.items():
                sup = support[(support["feature"] == f)
                              & (support["region"] == str(region))]
                rows.append({
                    "variable": f, "group": f, "section": sec_,
                    "pillar": r["pillar"], "region": str(region),
                    "share_of_sales": abs(share) if np.isfinite(split) else np.nan,
                    "spend_split": split,
                    "sales_region": float(s_r),
                    "contribution": (direction * abs(share) * split * float(s_r)
                                     if np.isfinite(split) else np.nan),
                    "support": float(sup["support"].iloc[0]) if len(sup) else 0.0,
                    "dv_agg": float(dv_agg.get(str(region), np.nan)),
                    "share_formula": formula, "row_note": note,
                    "sign_constraint": sign,
                })
    work = _divide(pd.DataFrame(rows))
    agg = _average_over_supported(work, "variable", national_basis)
    meta = (work.drop_duplicates("variable")
            [["variable", "section", "pillar", "sign_constraint", "row_note"]])
    out = agg.merge(meta, on="variable", how="left")
    out["global_prior_mean"] = [
        _prior_mean(c, s) for c, s in zip(out["national_coef"],
                                          out["sign_constraint"])]
    out["note"] = np.where(
        out["row_note"] != "", out["row_note"],
        np.where(out["n_regions_used"] == 0,
                 "NO prior: support is 0 in every region - drop the feature "
                 "or fix the extract", ""))
    out = out.rename(columns={"variable": "feature"})
    out["group"] = out["feature"]
    out["basis"] = "share of sales"
    out["from_combined_group"] = False
    out["mixed_signs"] = False
    cols = ["feature", "group", "section", "pillar", "global_prior_mean",
            "sign_constraint", "national_coef", "national_coef_average",
            "national_coef_weighted", "weighted_over_average",
            "national_basis", "sum_of_region_coefs", "n_regions_used",
            "n_regions_total", "basis", "note"]
    return out[cols].sort_values(["section", "feature"]), \
        work.sort_values(["section", "variable", "region"])


# --------------------------------------------------------------------------- #
# the prior file
# --------------------------------------------------------------------------- #
PRIOR_COLUMNS = ["variable", "region", "pooling", "sign_constraint",
                 "global_prior_mean", "global_prior_sd", "regional_sd_prior",
                 "baseline", "pillar", "contribution_reference",
                 "center_mode", "scale_mode", "prior_sd_basis",
                 "prior_mean_basis"]


def to_prior_file(priors: pd.DataFrame | None = None,
                  all_features: list | None = None,
                  shares: pd.DataFrame | None = None,
                  sd_basis: str = "relative", mean_basis: str = "median",
                  pooling: str = "") -> pd.DataFrame:
    """The feature-prior CSV: one row per MODEL variable, filled only where
    something was actually given.

    The file is a TEMPLATE, and a blank cell is a real answer - "nobody has
    said, so the model's default applies". Only four columns are ever written:

      variable            every column of the datacube (or every configured
                          feature), so nothing silently falls out of the model
      sign_constraint     from the vendor contribution's sign, or the share
                          file's column. BLANK when neither covered it
      global_prior_mean   the generated coefficient. BLANK likewise - which is
                          exactly the state of a NEW variable you are testing
      prior_sd_basis /    `relative` and `median`, so a sd you type later reads
      prior_mean_basis    as a percentage and a mean reads as the median

    `pillar` and `baseline` are carried over when the SHARE file states them,
    because that file says so in as many words. Everything else - `pooling`,
    `global_prior_sd`, `regional_sd_prior`, `contribution_reference`,
    `center_mode`, `scale_mode` - is left blank for the modeller, and blank
    means the documented default (`hierarchical`, no centring, no scaling,
    `auto` reference). Those defaults are what the generated means are in the
    units of, so an untouched file is already consistent.
    """
    priors = pd.DataFrame(columns=["feature", "global_prior_mean",
                                   "sign_constraint"]) if priors is None \
        else priors
    feats = list(dict.fromkeys(list(all_features or []) + list(priors["feature"])))
    p = priors.drop_duplicates("feature", keep="last").set_index("feature")
    meta = shares.set_index("variable") if shares is not None and len(shares) \
        else None
    rows = []
    for f in feats:
        has = f in p.index
        mean = p.loc[f, "global_prior_mean"] if has else np.nan
        sign = p.loc[f, "sign_constraint"] if has else ""
        pillar, baseline = "", ""
        if meta is not None and f in meta.index:
            pillar = meta.loc[f, "pillar"]
            baseline = 1 if meta.loc[f, "section"] == "baseline" else ""
            if not sign:
                sign = _sign_for(meta.loc[f].to_dict()
                                 | {"section": meta.loc[f, "section"],
                                    "variable": f})
        rows.append({
            "variable": f, "region": "", "pooling": pooling,
            "sign_constraint": sign or "",
            "global_prior_mean": (round(float(mean), 12)
                                  if pd.notna(mean) else np.nan),
            "global_prior_sd": np.nan, "regional_sd_prior": np.nan,
            "baseline": baseline, "pillar": pillar or "",
            "contribution_reference": "", "center_mode": "",
            "scale_mode": "", "prior_sd_basis": sd_basis,
            "prior_mean_basis": mean_basis})
    out = pd.DataFrame(rows, columns=PRIOR_COLUMNS)
    blank = out["global_prior_mean"].isna()
    if blank.any() and len(priors):
        names = list(out.loc[blank, "variable"])
        warnings.warn(
            f"{int(blank.sum())} variables have no generated prior "
            f"({names[:6]}{'...' if len(names) > 6 else ''}) - they are in "
            "neither file, or have no support. Their mean is left BLANK: fill "
            "it by hand, or leave it if this is a variable you are testing "
            "(it then starts free and centred on zero).")
    return out


def region_coefficients(priors: pd.DataFrame, work: pd.DataFrame
                        ) -> pd.DataFrame:
    """feature / region / region_coef (signed): every usable per-region cell,
    replicated to each member of its group."""
    w = work[work["usable"]]
    if w.empty:
        return pd.DataFrame(columns=["feature", "region", "region_coef"])
    return (priors[["feature", "group"]]
            .merge(w[["group", "region", "prior_mean_region"]], on="group")
            .rename(columns={"prior_mean_region": "region_coef"})
            [["feature", "region", "region_coef"]])


def regional_prior_file(national: pd.DataFrame, region_coefs: pd.DataFrame,
                        pooling: str = "independent"
                        ) -> tuple[pd.DataFrame, list]:
    """The national rows plus one override row per region with support.

    Returns (the file, the region cells that could NOT be written). A signed
    variable's region row holds a magnitude and the sign is feature-level, so
    a region whose coefficient runs the other way cannot be expressed - it
    gets no row and falls back to the national prior.
    """
    base = national.assign(pooling=pooling)
    signs = base.set_index("variable")["sign_constraint"]
    has_mean = set(base.loc[base["global_prior_mean"].notna(), "variable"])
    rows, skipped = [], []
    for f, region, coef in region_coefs.itertuples(index=False):
        if f not in has_mean or pd.isna(coef):
            continue
        sign = str(signs.get(f) or "free")     # blank = free, as the loader reads it
        if (sign == "positive" and coef <= 0) or (sign == "negative" and coef >= 0):
            skipped.append((f, region, float(coef)))
            continue
        rows.append({"variable": f, "region": str(region),
                     "global_prior_mean": round(_prior_mean(coef, sign), 12)})
    reg = pd.DataFrame(rows, columns=PRIOR_COLUMNS)
    out = pd.concat([base, reg], ignore_index=True)
    order = {v: i for i, v in enumerate(base["variable"])}
    out["_o"] = out["variable"].map(order)
    out["_r"] = out["region"].fillna("").astype(str) != ""
    out = (out.sort_values(["_o", "_r", "region"], kind="stable")
           .drop(columns=["_o", "_r"]).reset_index(drop=True))
    return out, skipped


def write_calculation_workbook(work: pd.DataFrame, priors: pd.DataFrame,
                               outdir: str, basis: str) -> str:
    """The working, so a prior can be checked rather than believed."""
    os.makedirs(outdir, exist_ok=True)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        for i, (title, frame) in enumerate((("calculation", work),
                                            ("prior_mean", priors))):
            ws = wb.active if i == 0 else wb.create_sheet(title)
            ws.title = title
            ws.append(list(frame.columns))
            for c in range(1, len(frame.columns) + 1):
                ws.cell(row=1, column=c).font = Font(bold=True)
            for _, r in frame.iterrows():
                ws.append([None if (isinstance(v, float) and not np.isfinite(v))
                           else (bool(v) if isinstance(v, np.bool_) else v)
                           for v in r.tolist()])
            for j, name in enumerate(frame.columns, start=1):
                ws.column_dimensions[get_column_letter(j)].width = (
                    42 if name in ("feature", "variable", "group", "members",
                                   "formula", "share_formula",
                                   "skipped_because", "note", "row_note")
                    else 16)
            ws.freeze_panes = "B2"
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
        "    region_coef[group, region] = contribution / support / dv_agg",
        "    national_coef[group]       = SUM(region_coef over regions with",
        "                                 support) / n_regions_used",
        "",
        "  group         a connected component of the mapping file: one vendor",
        "                variable and the several of ours it was split into",
        "                (by period or sub-brand), or the reverse",
        "  contribution  the vendor's number for that group in that region. A",
        "                vendor number replicated across several of our rows is",
        "                counted ONCE. A national number is allocated to the",
        "                regions in proportion to support",
        "  support       SUM of the group members' RAW values over the window",
        "  dv_agg        the region's KPI aggregate (mean by default)",
        "",
        "NATIONAL vs REGIONAL. national_coef DIVIDES BY THE NUMBER OF REGIONS",
        "THAT HAD SUPPORT - not by the region count: contribution and support",
        "in 2 regions out of 5 means the sum of 2 coefficients divided by 2.",
        "feature_priors_national.csv carries it (pooling=hierarchical).",
        "feature_priors_regional.csv adds one override row per region with",
        "that region's own region_coef (pooling=independent).",
        "",
        "The group's one mean is replicated to every member. That is right:",
        "they share an implied coefficient, and splitting it by support would",
        "invent a difference the vendor never measured.",
        "",
        "SIGNS. A negative contribution -> sign_constraint=negative. A positive",
        "one -> free for a dummy ('dummy' in the name), else positive. For a",
        "signed variable global_prior_mean is the MAGNITUDE; for a free one it",
        "keeps its sign. Where signs disagree across regions the sign of the",
        "total is used and the row is flagged; a region on the other side of",
        "zero gets no override row in the regional file.",
        "",
        "UNITS. The mean is per RAW unit of the variable (scale_mode=none,",
        "written for you) per unit of the region's mean KPI. It matches the",
        "model only with run.dv_scale: mean and run.dv_scale_scope: region.",
        "",
        "global_prior_sd is written WIDE (0.5) on purpose. A prior derived from",
        "a benchmark and then pinned reproduces the benchmark and validates",
        "nothing - the agreement is circular. See docs/FEATURE_PRIOR_GUIDE.md.",
    ],
    "share": [
        "HOW THE PRIOR MEAN WAS COMPUTED - shares of sales",
        "",
        "    media / expert   C = pillar_share x sales x spend / pillar_spend",
        "    comp_media       C = variable_share x sales",
        "    trade            C = variable_share x sales",
        "    baseline         C = baseline_share x variable_share x sales",
        "",
        "    region_coef[variable, region] = C / support / dv_agg",
        "    national_coef[variable]       = SUM(region_coef over regions with",
        "                                    support) / n_regions_used",
        "",
        "  sales         the region's total KPI over the window",
        "  pillar_spend  the SUM of spend over that pillar's variables -",
        "                computed, never typed",
        "  baseline      variable_share is the share WITHIN the baseline, from",
        "                the stage-1 baseline-only run (METHODOLOGY section 1)",
        "",
        "feature_priors_national.csv carries national_coef; the regional file",
        "adds one override row per region with support.",
        "",
        "SIGNS come from the share file's sign_constraint column. A blank one",
        "falls back to: negative share -> negative; a dummy -> free;",
        "comp_media -> negative; otherwise positive. C carries that sign.",
        "",
        "UNITS. Per RAW unit (scale_mode=none) per unit of the region's mean",
        "KPI - run.dv_scale: mean, run.dv_scale_scope: region.",
        "",
        "Inside a media or expert pillar every variable gets the SAME implied",
        "efficiency. That is not a claim that they are equally efficient - it",
        "is the least-informative start that still has the right total. With a",
        "wide global_prior_sd the data has room to move them apart, and HOW FAR",
        "EACH ONE MOVES IS THE RESULT.",
        "",
        "SANITY-CHECK THE TOTAL. If the shares say marketing is 15% of sales",
        "and the fit returns 7%, that is a finding (usually a missing category",
        "or competitor variable), not a prior to force.",
    ],
}


# --------------------------------------------------------------------------- #
# the workflow
# --------------------------------------------------------------------------- #
def decide_case(mapping: pd.DataFrame, shares: pd.DataFrame) -> str:
    """a / b / c / d - see the module docstring."""
    if has_contribution(mapping):
        return "a"
    if shares is not None and len(shares):
        return "b" if len(mapping) else "c"
    return "d"


CASE_TEXT = {
    "a": "vendor contribution in the mapping file -> inverting it",
    "b": "mapping without contributions + a share file -> building from shares",
    "c": "share file only -> building from shares",
    "d": "neither a contribution nor a share file -> the SKELETON, one row "
         "per datacube variable with the means and signs blank",
}


def warn_concentrated(priors: pd.DataFrame, basis: str = "average",
                      tol: float = 1.25) -> pd.DataFrame:
    """Flag the variables where the two national aggregations disagree.

    The plain average and the total-preserving weighted value differ when the
    contribution is concentrated in a few regions but the variable has support
    in many. Under `pooling: global` - ONE coefficient for every region - the
    average then under-delivers the national total by exactly that ratio, and
    the gap survives every correction because the next refit re-imposes it.
    """
    if "weighted_over_average" not in priors.columns:
        return pd.DataFrame()
    r = pd.to_numeric(priors["weighted_over_average"], errors="coerce")
    bad = priors[(r > tol) | (r < 1 / tol)].copy()
    if not len(bad):
        return bad
    ex = ", ".join(
        f"{f} x{v:.1f}" for f, v in zip(bad["feature"], bad["weighted_over_average"])
    if pd.notna(v))
    warnings.warn(
        f"{len(bad)} variables whose contribution is CONCENTRATED in a few "
        f"regions: the total-preserving coefficient differs from the plain "
        f"average by more than {tol:.2f}x ({ex[:300]}). You are generating "
        f"national_basis={basis!r}. Under pooling: global - one coefficient "
        "for every region - the average under-delivers the national total by "
        "that factor, and no correction closes it because the next refit "
        "re-imposes it. Use data.national_basis: weighted for a global model, "
        "or give the variable per-region priors "
        "(feature_priors_regional.csv + pooling: independent).")
    return bad


def run_pre_model(settings, df=None, outdir: str | None = None) -> dict:
    """Generate the feature prior file (and its working) from whatever was given.

    The variable list comes from the DATACUBE - `date`, `region` and `dv`
    aside, every column is a row - so there is no chicken-and-egg: you do not
    need a feature prior file to generate a feature prior file. When one IS
    configured it is the list instead, and the mapping/share files must sit
    inside it.

    With neither a mapping nor a share file - or a mapping with no usable
    contributions (case d) - it still writes the SKELETON: one row per
    variable, means and signs blank. It always writes to `pre_model_dir`, never
    over `data.feature_priors`.

    Call `build_priors(config_path)` rather than this from a notebook: it also
    captures the warnings into `<pre_model_dir>/00_warnings/` instead of the
    cell output.
    """
    data = settings.data
    map_path, share_path = data.get("mapping_file"), data.get("share_file")
    # ALWAYS write a file. With nothing to build from it is the skeleton, and
    # it goes to pre_model_dir - data.feature_priors is never overwritten.
    if df is None:
        from mmm.core.settings import load_panel
        df = load_panel(settings)
    run = settings.run
    known = [c for c in df.columns
             if c not in (run.date_col, run.region_col, run.dv_col)]
    # The feature prior file is the list the mapping and share files must sit
    # INSIDE: it may carry more variables than they do, never fewer. With no
    # prior file configured yet, the datacube is the list.
    configured = [s.name for s in settings.model.features]
    allowed, against = (configured, PRIOR_FILE) if configured else \
        (known, DATACUBE)

    mapping = load_mapping_table(map_path, allowed, against) if map_path else \
        load_mapping_table(None)
    # a region spelled differently from the datacube ("('Base', 'Droguerias')"
    # vs "Base_Droguerias") would match nothing and silently drop every
    # contribution - align it, or stop and say which ones
    mapping = align_regions(mapping, df[run.region_col].astype(str).unique(),
                            map_path or "mapping")
    shares = (load_share_file(share_path, allowed, against) if share_path
              else pd.DataFrame())
    case = decide_case(mapping, shares)
    print(f"[prior] case {case}: {CASE_TEXT[case]}")
    if case == "a" and len(shares):
        print("[prior] the share file supplies pillars, the baseline flag and "
              "default signs; the MEANS come from the vendor contribution, "
              "which is evidence rather than an assumption")
    dv_how = data.get("dv_aggregation") or "mean"
    nat_basis = data.get("national_basis") or "average"
    check_units(run, dv_how)

    outdir = outdir or data.get("pre_model_dir") or "pre_model_outputs"
    os.makedirs(outdir, exist_ok=True)
    features = configured or known
    sup = support_table(df, features, run.region_col)
    dv = dv_for_model(df, run, dv_how)

    if case == "a":
        priors, work = priors_from_mapping(mapping, sup, dv, nat_basis)
        rcoef = region_coefficients(priors, work)
        basis = "contribution"
        if len(shares):
            # the vendor contribution wins WHERE IT EXISTS. A variable the
            # vendor never reported (a channel they did not model) has nothing
            # to conflict with, so it takes its share-based prior rather than a
            # blank - each row's `basis` says which it got.
            sp, sw = priors_from_shares(
                shares, sup, dv, region_sales(df, run.dv_col, run.region_col),
                nat_basis)
            have = set(priors.loc[priors["global_prior_mean"].notna(), "feature"])
            fill = sp[~sp["feature"].isin(have) & sp["global_prior_mean"].notna()]
            if len(fill):
                fw = sw[sw["variable"].isin(fill["feature"])]
                priors = pd.concat([priors[~priors["feature"].isin(
                    fill["feature"])], fill], ignore_index=True)
                work = pd.concat([work, fw], ignore_index=True)
                rcoef = pd.concat([rcoef[~rcoef["feature"].isin(fill["feature"])],
                                   region_coefficients(fill, fw)],
                                  ignore_index=True)
                print(f"[prior] {len(fill)} variables the vendor did not report "
                      "took their share-based prior instead of a blank")
    elif case == "d":
        # nothing to build FROM: write the skeleton so the modeller has the
        # variable list and the two basis columns, and fills the rest in
        priors = work = rcoef = None
        basis = "skeleton"
    else:
        priors, work = priors_from_shares(
            shares, sup, dv, region_sales(df, run.dv_col, run.region_col),
            nat_basis)
        rcoef = region_coefficients(priors, work)
        basis = "share"

    if priors is not None:
        warn_concentrated(priors, nat_basis)
    national = to_prior_file(priors, all_features=features, shares=shares)
    nat_path = os.path.join(outdir, "feature_priors_national.csv")
    national.to_csv(nat_path, index=False)
    n_ok = int(national["global_prior_mean"].notna().sum())
    if case == "d":
        print(f"[prior] skeleton: {len(national)} variables from the datacube, "
              f"means and signs BLANK -> {nat_path}")
        print("[prior] fill in global_prior_mean / sign_constraint / "
              "global_prior_sd, then point data.feature_priors at it. A blank "
              "mean is a variable that starts free and centred on zero.")
        return {"case": case, "basis": basis,
                "feature_priors_national": nat_path}
    regional, skipped = regional_prior_file(national, rcoef)
    reg_path = os.path.join(outdir, "feature_priors_regional.csv")
    regional.to_csv(reg_path, index=False)
    calc = write_calculation_workbook(work, priors, outdir, basis)
    n_reg = int((regional["region"].fillna("").astype(str) != "").sum())
    print(f"[prior] national priors for {n_ok}/{len(national)} variables -> "
          f"{nat_path}")
    print(f"[prior] regional: the same plus {n_reg} region rows -> {reg_path}")
    against = [x for x in skipped if x[2] != 0]
    zeros = [x for x in skipped if x[2] == 0]
    if against:
        ex = ", ".join(f"{f}@{r}" for f, r, _ in against[:5])
        warnings.warn(
            f"{len(against)} region cells run AGAINST their variable's sign "
            f"({ex}{'...' if len(against) > 5 else ''}) and have no row in the "
            "regional file - a signed variable's region row is a magnitude. "
            "Those regions fall back to the national prior.")
    if zeros:
        ex = ", ".join(f"{f}@{r}" for f, r, _ in zeros[:5])
        warnings.warn(
            f"{len(zeros)} region cells run AGAINST their variable's sign - "
            f"they are exactly ZERO in the vendor decomposition while the "
            f"variable has support there ({ex}{'...' if len(zeros) > 5 else ''})."
            " A signed coefficient cannot be 0, so they have no row and fall "
            "back to the national prior - which gives them an effect the "
            "vendor says is zero. If the variable truly does nothing in those "
            "regions, zero it in the datacube there. Under pooling: global use "
            "data.national_basis: weighted, or these zeros drag the average "
            "down.")
    print(f"[prior] the working -> {calc}")
    print("[prior] REVIEW before pointing data.feature_priors at either file - "
          "national for pooling=hierarchical, regional for independent. Only "
          "the variable, the mean, the sign and the two basis columns are "
          "filled; global_prior_sd is BLANK, so write the width you can "
          "defend (0.02 pins it, 0.5 lets the data speak), and consider "
          "center_mode=mean on level variables (TDP, price, category).")
    return {"case": case, "basis": basis,
            "feature_priors_national": nat_path,
            "feature_priors_regional": reg_path,
            "regional_rows_skipped": skipped, "prior_calculation": calc}


def check_units(run, dv_how: str = "mean") -> list:
    """Warn when the run would read the generated means in different units.

    A generated mean is `C / SUM(raw x) / dv_agg`: per raw unit of the
    variable, per unit of the region's KPI aggregate. The model reads a
    coefficient as `beta x SUM(x_scaled) x dv_scale`. Those agree only when
    the KPI scale IS that aggregate, per region. Otherwise every contribution
    comes out wrong by a constant multiple - and still reconciles to 100%,
    so no downstream check can see it (the v5 BMC failure).
    """
    problems = []
    if dv_how != "mean":
        problems.append(
            f"data.dv_aggregation is {dv_how!r}, but no run.dv_scale divides "
            "the KPI by that - use 'mean'")
    if getattr(run, "dv_scale", "mean") != "mean":
        problems.append(
            f"run.dv_scale is {run.dv_scale!r}; the generated means are per "
            "unit of the region's MEAN KPI - set run.dv_scale: mean")
    if getattr(run, "dv_scale_scope", "region") != "region":
        problems.append(
            f"run.dv_scale_scope is {run.dv_scale_scope!r}; each region was "
            "divided by its OWN KPI - set run.dv_scale_scope: region")
    if problems:
        warnings.warn(
            "the generated priors are in different units from this run:\n  - "
            + "\n  - ".join(problems)
            + "\nFix config.yaml before fitting with them, or every mean is "
            "off by the ratio of the two scales.")
    return problems


def build_priors(config_path: str = "config.yaml", outdir: str | None = None,
                 verbose: bool = False) -> dict:
    """THE way to generate priors, from a notebook or the command line.

        from mmm.data.prior_builder import build_priors
        build_priors("config.yaml")

    Loads the settings, builds the prior file(s), and sends EVERY warning -
    the ones raised while loading an existing prior file included - to
    `<pre_model_dir>/00_warnings/` (one document per category, as a run
    does), printing a single summary instead of a wall of text. Starts by
    printing the codebase version and refuses to hide a partly re-uploaded
    copy.
    """
    import mmm
    from mmm.checks.warnings_report import (collect_warnings,
                                            print_warning_summary,
                                            write_warning_docs)
    from mmm.core.settings import load_settings

    mmm.announce()
    with collect_warnings() as caught:
        settings = load_settings(config_path)
        res = run_pre_model(settings, outdir=outdir)
    out = outdir or settings.data.get("pre_model_dir") or "pre_model_outputs"
    wdir = os.path.join(out, "00_warnings")
    df = write_warning_docs(list(caught), wdir, run_name="pre-model")
    print_warning_summary(df, wdir, verbose=verbose)
    res["warnings_dir"] = wdir
    return res


if __name__ == "__main__":
    import sys

    build_priors(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
