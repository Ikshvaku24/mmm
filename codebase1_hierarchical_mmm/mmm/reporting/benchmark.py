"""A benchmark-comparison sheet you fill in yourself, with the formulas already in it.

You paste the vendor's (or last year's, or anyone's) contribution and every
diagnostic recalculates live in the spreadsheet - the gap, the ratio, how far
the data wants to move away from your prior, and the corrected prior mean to
write back into the prior file, nationally and per region.

Why this rather than a Python script that reads a comparison file: the benchmark
never arrives in a fixed schema. It is a column in a deck, a tab in someone's
workbook, a number read off a slide. Handing you a sheet with the run's own
numbers already laid out and the formulas pre-written means the only step left
is paste - and you can see every intermediate quantity instead of trusting a
script's join.

Layout - regions across the columns
-----------------------------------
    A-E    row_type, group, feature, pillar, members
    F-N    TOTAL (national): our, benchmark, pct_diff, ratio, contraction,
           current_prior, suggested_prior, delta, verdict
    O...   one block per region: our, benchmark, pct_diff, ratio,
           contraction, current_prior, suggested_prior

Three kinds of row:

    feature   a variable compared on its own - everything on one row
    group     a mapping GROUP (a vendor variable we split, by period or
              sub-brand). Carries the benchmark, the gap and the ratio R.
    member    one of the group's variables. Carries its OWN contraction and
              prior, and its correction uses the GROUP's R.

Where to paste. A national benchmark goes in the TOTAL `benchmark` cell; each
region's `benchmark` cell then spreads it by OUR contribution share, so every
region shows the national gap - which is the honest statement, because a
national number cannot tell you where the gap is. A regional benchmark goes
over the region cells (the spread formula is overwritten), and TOTAL then needs
their sum. With a mapping file that carries contributions all of this is
pre-filled.

The arithmetic in the formulas
------------------------------
For a sign-constrained feature the coefficient is `beta = exp(Normal(mu, sigma))`
and the reported contribution is the posterior MEDIAN, so

    contribution = exp(mu_post) * SUM(x_scaled + shift) * dv_scale

With a conjugate update `mu_post = (1-c)*mu_prior + c*mu_like`, where c is the
`contraction`. If the prior was derived FROM the benchmark, then

    ln(ours / theirs) = -c * delta

so `delta` - how far below (or above) your prior the data wants the coefficient,
in log units - is recoverable as `ln(R) / c`, and the prior mean that would land
on the benchmark is

    new_prior_mean = old_prior_mean * R^(1 / (1 - max(c, 0)))

**A split variable.** The benchmark only exists for the GROUP, so R is computed
once, on the group: R = benchmark / SUM(our members). Contraction exists only
per MEMBER, so each member's correction uses its own c with the shared R. A
member with c <= 0 has a posterior no narrower than its prior - the data is not
moving it - so its contribution scales one-for-one with its prior and the
exponent is 1: new = old x R. A member the data does pin (c > 0) needs a larger
move, R^(1/(1-c)), because the data pulls part of any prior change back. Every
member closing R on its own contribution closes R on their sum, so the group
lands on the benchmark.

**Reading `delta` across features is the point.** If it clusters around one
value, a single global constraint is pushing every driver the same way - the
usual cause being a term the benchmark does not have (a free region intercept)
claiming a share of a fixed total. Correcting prior means one at a time cannot
fix that: the next refit re-imposes the same shortfall on the corrected numbers.
If `delta` scatters, the gaps really are per-feature and `suggested_prior` is
the fix.

Output
------
`05_contributions/benchmark_comparison.xlsx` when openpyxl is available (it is
on Databricks): sheets `summary`, `comparison`, `what each column means`.
Otherwise `benchmark_comparison.csv` with the summary below the table - Excel
evaluates `=` formulas in a CSV on open, so it behaves the same way.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# the column layout
# --------------------------------------------------------------------------- #
LEFT_COLS = [
    ("row_type", "feature = compared alone; group = a mapping group (the "
                 "benchmark lives here); member = one variable of the group "
                 "above it"),
    ("group", "the mapping group - the vendor's variable name"),
    ("feature", "our variable, exactly as in the prior file (blank on a group "
                "row)"),
    ("pillar", "reporting group"),
    ("members", "on a group row: the variables it sums"),
]
TOTAL_COLS = [
    ("our", "what this run reports, KPI units. Group rows: the sum of their "
            "members"),
    ("benchmark", "<<< PASTE HERE >>> the benchmark contribution"),
    ("pct_diff", "(ours - theirs) / |theirs| * 100"),
    ("ratio", "R = theirs / ours - the multiplier to close the gap. A member "
              "row shows its GROUP's R"),
    ("contraction", "1 - posterior_var/prior_var for this variable. Near 0 = "
                    "the posterior IS the prior"),
    ("current_prior", "the implied median coefficient your prior file "
                      "produces"),
    ("suggested_prior", "current * R^(1/(1-max(c,0))) - write this back. c<=0 "
                        "means the prior scales directly by R. Exact for a "
                        "signed variable; for a FREE one only when c is small "
                        "(see prior_posterior_contraction_fitting.md 2b)"),
    ("delta", "ln(R)/c - how far the data wants to move, log units. Blank "
              "when c is outside (0,1)"),
    ("verdict", "what to do about this row"),
]
REGION_COLS = TOTAL_COLS[:7]
TOTAL = "TOTAL"
FILL = "benchmark"


def _col(n: int) -> str:
    """1-based column number -> spreadsheet letters (1 -> A, 27 -> AA)."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class Layout:
    """Where every cell lives, so formulas and tests agree on one map."""

    HEADER_ROWS = 2                     # block titles, then column names

    def __init__(self, regions: list):
        self.regions = list(regions)
        self.n_left = len(LEFT_COLS)

    def start(self, block: str) -> int:
        if block == TOTAL:
            return self.n_left + 1
        b = self.regions.index(block)
        return self.n_left + len(TOTAL_COLS) + b * len(REGION_COLS) + 1

    def col(self, block: str, name: str) -> str:
        names = [c for c, _ in (TOTAL_COLS if block == TOTAL else REGION_COLS)]
        return _col(self.start(block) + names.index(name))

    def ref(self, block: str, name: str, r: int) -> str:
        return f"{self.col(block, name)}{r}"

    @property
    def width(self) -> int:
        return self.n_left + len(TOTAL_COLS) + len(self.regions) * len(REGION_COLS)

    def first_data_row(self) -> int:
        return self.HEADER_ROWS + 1


# --------------------------------------------------------------------------- #
# the formulas
# --------------------------------------------------------------------------- #
def verdict_formula(pct: str, c: str) -> str:
    """The reading of one variable, from the gap it sits in and its own c."""
    return (f'=IF({pct}="","paste the benchmark",'
            f'IF(ABS({pct})<10,"ok",'
            f'IF({c}="","no contraction for this variable",'
            f'IF({c}<-0.2,"UNIDENTIFIED - posterior wider than prior; fix '
            f'collinearity before trusting any prior",'
            f'IF({c}<0.2,"prior-driven - write suggested_prior back (the '
            f'result will be your prior)",'
            f'IF({c}>0.5,"data disagrees - impose (tighten sd) or accept the '
            f'gap","mixed - check delta against the other rows"))))))')


def row_formulas(L: Layout, block: str, r: int, kind: str,
                 group_row: int | None = None) -> dict:
    """The formula for every computed cell of row `r` in `block`.

    `kind` is feature / group / member. Every formula is guarded on an empty
    benchmark, so an unfilled sheet shows blanks rather than #DIV/0!.
    """
    ours, bench = L.ref(block, "our", r), L.ref(block, FILL, r)
    ratio, c = L.ref(block, "ratio", r), L.ref(block, "contraction", r)
    prior = L.ref(block, "current_prior", r)
    f = {}
    if kind in ("feature", "group"):
        f["pct_diff"] = (f'=IF(OR({bench}="",{bench}=0),"",'
                         f'({ours}-{bench})/ABS({bench})*100)')
        f["ratio"] = f'=IF(OR({bench}="",{ours}=0),"",{bench}/{ours})'
    else:
        # a member has no benchmark of its own: its R is the GROUP's
        g = L.ref(block, "ratio", group_row)
        f["ratio"] = f'=IF({g}="","",{g})'
    if kind in ("feature", "member"):
        f["suggested_prior"] = (
            f'=IF(OR({ratio}="",{prior}="",{c}="",{ratio}<=0,{c}>=0.999),"",'
            f'{prior}*{ratio}^(1/(1-MAX({c},0))))')
    if block == TOTAL:
        if kind == "group":
            pct = L.ref(TOTAL, "pct_diff", r)
            f["verdict"] = (f'=IF({pct}="","paste the benchmark",'
                            f'IF(ABS({pct})<10,"ok","gap - each member row '
                            f'below carries its own fix"))')
        else:
            f["delta"] = (f'=IF(OR({ratio}="",{c}="",{ratio}<=0,{c}<=0.001,'
                          f'{c}>=0.999),"",LN({ratio})/{c})')
            pct = L.ref(TOTAL, "pct_diff",
                        group_row if kind == "member" else r)
            f["verdict"] = verdict_formula(pct, c)
    return f


def total_our_formula(L: Layout, r: int) -> str:
    return "=SUM(" + ",".join(L.ref(g, "our", r) for g in L.regions) + ")"


def total_bench_sum_formula(L: Layout, r: int) -> str:
    cells = ",".join(L.ref(g, FILL, r) for g in L.regions)
    return f'=IF(COUNT({cells})=0,"",SUM({cells}))'


def spread_formula(L: Layout, region: str, r: int) -> str:
    """A region's share of a national benchmark, by OUR contribution share."""
    tb, to = L.ref(TOTAL, FILL, r), L.ref(TOTAL, "our", r)
    return (f'=IF(OR({tb}="",{to}=0),"",'
            f'{tb}*{L.ref(region, "our", r)}/{to})')


def summary_formulas(L: Layout, first: int, last: int,
                     sheet: str | None = None) -> list:
    """(label, formula) pairs for the read-first block."""
    p = f"'{sheet}'!" if sheet else ""

    def rng(name):
        return f"{p}{L.col(TOTAL, name)}{first}:{L.col(TOTAL, name)}{last}"

    pd_rng, dl_rng = rng("pct_diff"), rng("delta")
    return [
        ("comparison rows filled in", f'=COUNT({rng(FILL)})'),
        ("median % diff", f'=IFERROR(MEDIAN({pd_rng}),"")'),
        ("rows BELOW the benchmark", f'=COUNTIF({pd_rng},"<0")'),
        ("rows ABOVE the benchmark", f'=COUNTIF({pd_rng},">0")'),
        ("median delta", f'=IFERROR(MEDIAN({dl_rng}),"")'),
        ("delta spread (IQR)",
         f'=IFERROR(QUARTILE({dl_rng},3)-QUARTILE({dl_rng},1),"")'),
        ("implied shrink vs your priors",
         f'=IFERROR(TEXT(EXP(-MEDIAN({dl_rng}))-1,"0.0%"),"")'),
        ("reading", f'=IF(COUNT({dl_rng})<3,"need >=3 variables with a delta",'
         f'IF(IFERROR(QUARTILE({dl_rng},3)-QUARTILE({dl_rng},1),9)<0.1,'
         '"delta CLUSTERS -> ONE global constraint (usually a free intercept '
         'the benchmark does not have). Fixing prior means one by one will not '
         'hold.",'
         '"delta SCATTERS -> per-variable prior errors. suggested_prior is the '
         'fix."))'),
    ]


# --------------------------------------------------------------------------- #
# the mapping file (read by mmm.data.mapping - the same groups built the priors)
# --------------------------------------------------------------------------- #
def _sniff(cols, hints, exclude=()) -> str | None:
    """Find a column by name. Files come from people, not schemas."""
    low = {str(c).strip().lower(): c for c in cols if c not in exclude}
    for h in hints:                       # exact match first
        if h in low:
            return low[h]
    for h in hints:                       # then substring
        for k, c in low.items():
            if h in k:
                return c
    return None


def load_mapping(path: str, known_columns=None) -> dict:
    """our_variable -> group label, from the mapping file. {} when no path."""
    if not path:
        return {}
    from mmm.data.mapping import groups, load_mapping_table
    return groups(load_mapping_table(path, known_columns))


def benchmark_values(mapping_path: str) -> tuple[dict, dict]:
    """(regional {(group, region): value}, national {group: value}) from the
    mapping file's contributions - {} and {} when it carries none."""
    if not mapping_path or not os.path.exists(mapping_path):
        return {}, {}
    from mmm.data.mapping import (ALL_REGIONS, group_contributions,
                                  has_contribution, load_mapping_table)
    tbl = load_mapping_table(mapping_path)
    if not has_contribution(tbl):
        return {}, {}
    gc = group_contributions(tbl)
    reg = {(g, r): float(v) for g, r, v in gc[gc["region"] != ALL_REGIONS]
           .itertuples(index=False)}
    nat = {g: float(v) for g, _, v in gc[gc["region"] == ALL_REGIONS]
           .itertuples(index=False)}
    return reg, nat


# --------------------------------------------------------------------------- #
# assembling the run's own numbers
# --------------------------------------------------------------------------- #
def _truthy(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(("true", "1", "yes"))


def _contraction_lookup(contraction_df) -> tuple[dict, dict]:
    """({(feature, region): c}, {feature: c}) from the contraction report.

    Only the `use_for_delta` family - the log-scale Normal the delta
    arithmetic is valid on. A pooled feature has one row (no region), which is
    then its value in every region; an independent one has a row per region,
    and its national value is their mean.
    """
    if contraction_df is None or not len(contraction_df):
        return {}, {}
    c = contraction_df
    if "role" in c.columns:
        c = c[c["role"].astype(str).str.startswith("coefficient")]
    if "use_for_delta" in c.columns:
        sel = c[_truthy(c["use_for_delta"])]
        if len(sel):
            c = sel
    if "feature" not in c.columns and "name" in c.columns:
        parts = c["name"].astype(str).str.split(" @ ")
        c = c.assign(feature=parts.str[0].str.strip(),
                     region=parts.str[1].fillna("").str.strip())
    if "feature" not in c.columns or "contraction" not in c.columns:
        return {}, {}
    reg_col = c["region"].fillna("").astype(str) if "region" in c.columns \
        else pd.Series("", index=c.index)
    c = c.assign(_region=reg_col.replace({"nan": ""}))
    regional = {(f, r): float(v) for f, r, v in
                c.loc[c["_region"] != "", ["feature", "_region", "contraction"]]
                .itertuples(index=False) if pd.notna(v)}
    pooled = c[c["_region"] == ""]
    national = (pooled if len(pooled) else c).groupby("feature")[
        "contraction"].mean().dropna().to_dict()
    return regional, national


def _prior_lookup(prior_df) -> tuple[dict, dict]:
    """({(feature, region): implied median}, {feature: national}) from
    prior_summary.csv. National = the __population__ row, else the mean of
    the region rows (an independent feature has no population row)."""
    if prior_df is None or not len(prior_df):
        return {}, {}
    p = prior_df
    fcol = "variable" if "variable" in p.columns else "feature"
    if fcol not in p.columns or "implied_median" not in p.columns:
        return {}, {}
    reg = p["region"].astype(str) if "region" in p.columns \
        else pd.Series("__population__", index=p.index)
    pop = p[reg == "__population__"]
    rows = p[reg != "__population__"]
    regional = {(f, r): float(v) for f, r, v in zip(
        rows[fcol], reg[rows.index], rows["implied_median"]) if pd.notna(v)}
    national = rows.groupby(fcol)["implied_median"].mean().to_dict()
    national.update(pop.groupby(fcol)["implied_median"].mean().to_dict())
    return regional, national


def build_table(decomp, pdata, outdir_root: str = None,
                math_df: pd.DataFrame = None,
                contraction_df: pd.DataFrame = None,
                prior_df: pd.DataFrame = None,
                mapping: dict = None) -> pd.DataFrame:
    """One row per region x feature, carrying the group it belongs to and the
    regional and national contraction / prior. The sheet is laid out from
    this by `sheet_rows`."""
    if math_df is None:
        raise ValueError("build_table needs contribution_math (math_df)")
    m = math_df.rename(columns={"volume_median_of_total": "our_contribution"})
    out = pd.DataFrame({
        "region": m["region"].astype(str),
        "feature": m["feature"].astype(str),
        "pillar": (m["pillar"].fillna("").astype(str) if "pillar" in m.columns
                   else ""),
        "our_contribution": (pd.to_numeric(m["our_contribution"],
                                           errors="coerce")
                             if "our_contribution" in m.columns else np.nan),
    })
    mapping = mapping or {}
    out["group"] = out["feature"].map(mapping).fillna(out["feature"])
    c_reg, c_nat = _contraction_lookup(contraction_df)
    p_reg, p_nat = _prior_lookup(prior_df)
    out["contraction"] = [c_reg.get((f, r), c_nat.get(f, np.nan))
                          for f, r in zip(out["feature"], out["region"])]
    out["contraction_national"] = out["feature"].map(c_nat)
    out["current_prior"] = [p_reg.get((f, r), p_nat.get(f, np.nan))
                            for f, r in zip(out["feature"], out["region"])]
    out["current_prior_national"] = out["feature"].map(p_nat)
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# laying the sheet out
# --------------------------------------------------------------------------- #
def _num(v):
    """A cell value: a finite number, or None (a truly EMPTY cell, which the
    formulas' `=""` guards recognise)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def sheet_rows(table: pd.DataFrame, regional_bench: dict = None,
               national_bench: dict = None) -> tuple[Layout, list, list]:
    """(layout, header rows, data rows) - every cell a value, a formula or
    None (an empty cell). Rows are ordered by pillar, then group; a group's
    member rows follow it directly, which is what its SUM formulas rely on.
    """
    regional_bench, national_bench = regional_bench or {}, national_bench or {}
    regions = list(dict.fromkeys(table["region"]))
    L = Layout(regions)
    # header row 1: block titles; row 2: column names
    h1 = [""] * L.width
    h2 = [c for c, _ in LEFT_COLS] + [""] * (L.width - L.n_left)
    h1[L.start(TOTAL) - 1] = "TOTAL (national)"
    for j, (name, _) in enumerate(TOTAL_COLS):
        h2[L.start(TOTAL) - 1 + j] = name
    for g in regions:
        h1[L.start(g) - 1] = g
        for j, (name, _) in enumerate(REGION_COLS):
            h2[L.start(g) - 1 + j] = name

    first = [(p, g) for g, p in zip(table["group"], table["pillar"])]
    order = (pd.DataFrame(first, columns=["pillar", "group"])
             .drop_duplicates("group").sort_values(["pillar", "group"],
                                                   kind="stable"))
    rows = []
    r = L.first_data_row()
    for _, grow in order.iterrows():
        g = grow["group"]
        sub = table[table["group"] == g]
        members = list(dict.fromkeys(sub["feature"]))
        # a group row only when there is something to sum: a 1:1 rename
        # (vendor "TV" is our "tv") stays a single feature row
        is_group = len(members) > 1
        if is_group:
            group_row = r
            rows.append(_group_row(L, r, g, grow["pillar"], members,
                                   regional_bench, national_bench))
            r += 1
            for f in members:
                rows.append(_variable_row(L, r, "member", g, f,
                                          sub[sub["feature"] == f], {}, {},
                                          group_row))
                r += 1
        else:
            rows.append(_variable_row(L, r, "feature", g, members[0], sub,
                                      regional_bench, national_bench))
            r += 1
    return L, [h1, h2], rows


def _bench_cells(L, r, g, regional_bench, national_bench, cells):
    """Fill the benchmark cells of row r: pre-filled values where the mapping
    gave them, the spread / sum formulas otherwise."""
    has_reg = any((g, reg) in regional_bench for reg in L.regions)
    for reg in L.regions:
        idx = L.start(reg) - 1 + [c for c, _ in REGION_COLS].index(FILL)
        if has_reg:
            cells[idx] = _num(regional_bench.get((g, reg)))
        else:
            cells[idx] = spread_formula(L, reg, r)
    tidx = L.start(TOTAL) - 1 + [c for c, _ in TOTAL_COLS].index(FILL)
    if has_reg:
        cells[tidx] = total_bench_sum_formula(L, r)
    else:
        cells[tidx] = _num(national_bench.get(g))


def _put(L, cells, block, formulas):
    names = [c for c, _ in (TOTAL_COLS if block == TOTAL else REGION_COLS)]
    for k, v in formulas.items():
        cells[L.start(block) - 1 + names.index(k)] = v


def _group_row(L, r, g, pillar, members, regional_bench, national_bench):
    cells = [None] * L.width
    cells[:L.n_left] = ["group", g, "", pillar, " + ".join(members)]
    n = len(members)
    for reg in L.regions:
        # the group's contribution is the SUM of its member rows just below
        col = L.col(reg, "our")
        _put(L, cells, reg, {"our": f"=SUM({col}{r + 1}:{col}{r + n})"})
        _put(L, cells, reg, row_formulas(L, reg, r, "group"))
    _put(L, cells, TOTAL, {"our": total_our_formula(L, r)})
    _put(L, cells, TOTAL, row_formulas(L, TOTAL, r, "group"))
    _bench_cells(L, r, g, regional_bench, national_bench, cells)
    return cells


def _variable_row(L, r, kind, g, f, sub, regional_bench, national_bench,
                  group_row=None):
    cells = [None] * L.width
    pillar = sub["pillar"].iloc[0] if len(sub) else ""
    cells[:L.n_left] = [kind, g, f, pillar, ""]
    by_reg = sub.set_index("region")
    for reg in L.regions:
        vals = {"our": None, "contraction": None, "current_prior": None}
        if reg in by_reg.index:
            row = by_reg.loc[reg]
            vals = {"our": _num(row["our_contribution"]),
                    "contraction": _num(row["contraction"]),
                    "current_prior": _num(row["current_prior"])}
        _put(L, cells, reg, vals)
        _put(L, cells, reg, row_formulas(L, reg, r, kind, group_row))
    nat = sub.iloc[0] if len(sub) else None
    _put(L, cells, TOTAL, {
        "our": total_our_formula(L, r),
        "contraction": _num(nat["contraction_national"]) if nat is not None
        else None,
        "current_prior": _num(nat["current_prior_national"]) if nat is not None
        else None})
    _put(L, cells, TOTAL, row_formulas(L, TOTAL, r, kind, group_row))
    if kind == "feature":
        _bench_cells(L, r, g, regional_bench, national_bench, cells)
    return cells


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
NOTE = (
    "Paste a NATIONAL benchmark into the TOTAL `benchmark` column; each "
    "region's `benchmark` then spreads it by our contribution share. A "
    "REGIONAL benchmark goes over the region `benchmark` cells instead - put "
    "their sum in TOTAL. Only `feature` and `group` rows take a benchmark: a "
    "`member` row uses its group's ratio R with its OWN contraction. Read the "
    "summary FIRST: if delta clusters, the gap is one global constraint (a "
    "term the benchmark does not have) and correcting prior means one at a "
    "time will not hold. If delta scatters, suggested_prior is the corrected "
    "global_prior_mean (TOTAL) or region override (region blocks) to write "
    "back."
)

ROW_TYPES = [
    ("feature", "a variable compared on its own"),
    ("group", "a mapping group - the vendor variable we split (by period or "
              "sub-brand). The benchmark and the ratio R live here"),
    ("member", "one variable of the group above it. Its correction uses the "
               "GROUP's R and its OWN contraction: current * R^(1/(1-max(c,0)))"),
]


def write_benchmark_sheet(table: pd.DataFrame, outdir: str,
                          regional_bench: dict = None,
                          national_bench: dict = None) -> str:
    """Write the sheet with live formulas. .xlsx if openpyxl is here, else .csv."""
    os.makedirs(outdir, exist_ok=True)
    layout = sheet_rows(table, regional_bench, national_bench)
    try:
        import openpyxl  # noqa: F401
        return _write_xlsx(layout, outdir)
    except ImportError:
        return _write_formula_csv(layout, outdir)


def _csv_cell(v):
    return "" if v is None else v


def _write_formula_csv(layout, outdir: str) -> str:
    import csv

    L, headers, rows = layout
    path = os.path.join(outdir, "benchmark_comparison.csv")
    first, last = L.first_data_row(), L.first_data_row() + len(rows) - 1
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        for h in headers:
            w.writerow(h)
        for cells in rows:
            w.writerow([_csv_cell(v) for v in cells])
        w.writerow([])
        w.writerow(["SUMMARY - read this first"])
        for label, formula in summary_formulas(L, first, max(first, last)):
            w.writerow([label, formula])
        w.writerow([])
        w.writerow(["NOTE", NOTE])
    return path


def _write_xlsx(layout, outdir: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    L, headers, rows = layout
    path = os.path.join(outdir, "benchmark_comparison.xlsx")
    first, last = L.first_data_row(), L.first_data_row() + len(rows) - 1
    wb = Workbook()
    summ = wb.active
    summ.title = "summary"
    ws = wb.create_sheet("comparison")
    bold = Font(bold=True)
    yellow = PatternFill("solid", fgColor="FFF2CC")
    grey = PatternFill("solid", fgColor="EDEDED")
    band = PatternFill("solid", fgColor="DDEBF7")

    for h in headers:
        ws.append(h)
    for cells in rows:
        ws.append(cells)
    for j in range(1, L.width + 1):
        ws.cell(row=1, column=j).font = bold
        ws.cell(row=2, column=j).font = bold
    # block titles merged over their block
    for block, n in [(TOTAL, len(TOTAL_COLS))] + [(g, len(REGION_COLS))
                                                   for g in L.regions]:
        s = L.start(block)
        ws.merge_cells(start_row=1, start_column=s, end_row=1,
                       end_column=s + n - 1)
        ws.cell(row=1, column=s).alignment = Alignment(horizontal="center")
        ws.cell(row=1, column=s).fill = band
    # the paste cells are yellow; a member's benchmark cells are grey (n/a)
    blocks = [TOTAL] + L.regions
    for i, cells in enumerate(rows):
        r = first + i
        kind = cells[0]
        for b in blocks:
            c = ws[L.ref(b, FILL, r)]
            c.fill = grey if kind == "member" else yellow
        if kind == "group":
            for j in range(1, L.width + 1):
                ws.cell(row=r, column=j).font = bold
    widths = {"row_type": 10, "group": 34, "feature": 46, "pillar": 16,
              "members": 30}
    for j, (name, _) in enumerate(LEFT_COLS, start=1):
        ws.column_dimensions[_col(j)].width = widths.get(name, 16)
    for j in range(L.n_left + 1, L.width + 1):
        ws.column_dimensions[_col(j)].width = 14
    ws.column_dimensions[L.col(TOTAL, "verdict")].width = 52
    ws.freeze_panes = f"{_col(L.n_left + 1)}{first}"

    # summary sheet FIRST - it is what you read first
    summ.append(["SUMMARY - read this first", ""])
    summ.cell(row=1, column=1).font = bold
    for label, formula in summary_formulas(L, first, max(first, last),
                                           sheet="comparison"):
        summ.append([label, formula])
    summ.append([])
    summ.append(["NOTE", NOTE])
    summ.cell(row=summ.max_row, column=2).alignment = Alignment(
        wrap_text=True, vertical="top")
    summ.column_dimensions["A"].width = 34
    summ.column_dimensions["B"].width = 100

    doc = wb.create_sheet("what each column means")
    doc.append(["column", "block", "meaning"])
    for j in range(1, 4):
        doc.cell(row=1, column=j).font = bold
    for name, meaning in LEFT_COLS:
        doc.append([name, "left", meaning])
    for name, meaning in TOTAL_COLS:
        doc.append([name, "TOTAL and every region" if name in dict(REGION_COLS)
                    else "TOTAL only", meaning])
    doc.append([])
    doc.append(["row_type", "", ""])
    doc.cell(row=doc.max_row, column=1).font = bold
    for name, meaning in ROW_TYPES:
        doc.append([name, "", meaning])
    doc.column_dimensions["A"].width = 18
    doc.column_dimensions["B"].width = 24
    doc.column_dimensions["C"].width = 100
    wb.save(path)
    return path


def write_benchmark_comparison(decomp, pdata, outdir: str,
                               math_df=None, run_root: str = None,
                               mapping_path: str = None) -> str:
    """Called by the pipeline: assemble and write the sheet.

    `mapping_path` is the ONE mapping file: it groups our rows and, when it
    carries contributions, pre-fills the benchmark cells.
    """
    mapping = load_mapping(mapping_path) if mapping_path else {}
    contraction_df = prior_df = None
    if run_root:
        cp = os.path.join(run_root, "02_convergence",
                          "prior_posterior_contraction.csv")
        pp = os.path.join(run_root, "01_data", "prior_summary.csv")
        if os.path.exists(cp):
            contraction_df = pd.read_csv(cp)
        if os.path.exists(pp):
            prior_df = pd.read_csv(pp)
    table = build_table(decomp, pdata, math_df=math_df,
                        contraction_df=contraction_df, prior_df=prior_df,
                        mapping=mapping)
    reg, nat = benchmark_values(mapping_path)
    path = write_benchmark_sheet(table, outdir, reg, nat)
    n_groups = table["group"].nunique()
    n_filled = len({g for g, _ in reg} | set(nat))
    print(f"[benchmark] {os.path.basename(path)}: {n_groups} comparison rows, "
          f"{n_filled} pre-filled from the mapping; paste the rest into the "
          "TOTAL `benchmark` column (national) or the region blocks")
    return path
