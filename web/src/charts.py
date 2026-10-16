"""The charts a finished run shows: a one-page summary (the due-to waterfall
by pillar, the top drivers), fit, contributions (pillar -> sub-pillar ->
variable), decomposition, collinearity (the pairs that move together, the
correlation matrix as its lower triangle, the VIFs in bands) and the run's
health (max R-hat, divergences) - built from the run's own output files.

Names people read: a variable's `label` from the prior file when it has one,
else its name shortened in the MIDDLE (names share long prefixes - the end
tells them apart) - and always UNIQUE: every chart places its rows and
columns by POSITION, labelled with those names, because two equal category
names make Plotly merge two rows into one (that put the correlation numbers
in the wrong cells).

Two layers, so the arithmetic can be tested without Plotly:
  * data functions (pure pandas) - one per chart, reading the CSVs codebase 1
    writes (codebase1_hierarchical_mmm/docs/OUTPUTS_GUIDE.md has every column);
  * figure functions - Plotly, imported only when a chart is drawn. Without
    Plotly `available()` is False and the page shows the tables instead.

Styling follows the team's data-viz rules: one y-axis per chart; categorical
colours in a fixed, validated order that follows the ENTITY - a pillar gets
its colour from its size over the whole run, so it keeps it in every region,
period and chart; the baseline core in a recessive grey with the drivers in
colour; 2px lines, ~10% washes, solid hairline grids; a hover readout that
lists every series; a legend for two or more series. The page puts a table
twin under every chart. The correlation heatmap is the one diverging scale:
blue (negative) - a neutral grey at 0 - red (positive), the two arms at
matching OKLCH lightness, stepped again for the dark theme (where 0 recedes
into the surface and strong correlations are the bright cells).
"""
from __future__ import annotations

import math
import re
import threading

import numpy as np
import pandas as pd

ALL = "All regions"
PORTFOLIO = "__portfolio__"
BASELINE_CORE = "Baseline core"
OTHER = "Other"
# variables with no pillar in the prior file - codebase 1 reports them as
# "Unassigned" (a blank cell is read the same way): ONE group, last, in grey
UNASSIGNED = "Unassigned"

# categorical slots, in the validated order (adjacent-pair CVD and
# normal-vision checks pass in both modes; light slots 3-5 are below 3:1 on
# the surface, so every chart has a table twin). Slot 8 (red) is left out:
# it sits next to the status red that warnings use.
SERIES = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"],
          "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]}
INK = {"light": {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
                 "grid": "#e1e0d9", "axis": "#c3c2b7", "surface": "#fcfcfb",
                 "neutral": "#c3c2b7"},
       "dark": {"primary": "#ffffff", "secondary": "#c3c2b7", "muted": "#898781",
                "grid": "#2c2c2a", "axis": "#383835", "surface": "#1a1a19",
                "neutral": "#4a4a46"}}
MAX_COLOURED = len(SERIES["light"])     # pillars past this many fold into "Other"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
# status colours (fixed, never themed) - only where a colour MEANS a state
# (a VIF band, a health check), and always beside a label saying it
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a",
          "critical": "#d03b3b"}


_THEME = threading.local()


def use_theme(font=None, surface=None):
    """The viewer's company font for every chart, and its page colours
    behind the heatmap's cells ({"light": ..., "dark": ...}) - from
    src/brand.py. Per thread: Streamlit runs each viewer's session in its own
    thread, and each viewer may see another company. The data colours stay
    the validated ones above."""
    _THEME.font = font or None
    _THEME.surface = dict(surface or {})


def _font():
    return getattr(_THEME, "font", None) or FONT


def _ink(mode):
    """_ink(mode), with this viewer's page colour as the surface."""
    surface = (getattr(_THEME, "surface", None) or {}).get(mode)
    return dict(INK[mode], surface=surface) if surface else INK[mode]


def available() -> bool:
    try:
        import plotly.graph_objects  # noqa: F401
        return True
    except ImportError:
        return False


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _text(v) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()


def _pillar(v) -> str:
    """A variable's pillar - "Unassigned" when the prior file gave it none."""
    return _text(v) or UNASSIGNED


# --------------------------------------------------------------------------- #
# names people read
# --------------------------------------------------------------------------- #
def label_map(*frames) -> dict:
    """{variable: label} - the readable names the prior file gives (the
    `label` column codebase 1 carries into contribution_summary.csv and
    contribution_timeseries.csv). Blank labels are left out."""
    out = {}
    for f in frames:
        if f is None or not len(f) or not {"feature", "label"} <= set(f.columns):
            continue
        for feat, lab in zip(f["feature"].astype(str), f["label"].map(_text)):
            if lab and feat not in out:
                out[feat] = lab
    return out


def readable_name(name) -> str:
    """A datacube column without its boilerplate, for charts: the `all-...`
    parts ("every placement / platform / campaign") tell two columns apart
    nowhere, and `__` reads as a space - so
    media__digital-display__all-plac__all-plat__impressions is "media
    digital-display impressions" and media__tv__all-plac_otv__all-camp__grps
    is "media tv otv grps". A name with no `all-` part and no `__` is left as
    it is; design columns get design_label. Tables keep the real names."""
    n = design_label(name)
    if n != str(name):
        return n
    parts = n.split("_")
    if "__" not in n and not any(p.startswith("all-") for p in parts):
        return n
    tokens = []
    for tok in n.split("__"):
        keep = [p for p in tok.split("_") if p and p != "all" and not p.startswith("all-")]
        if keep:
            tokens.append("_".join(keep))
    return " ".join(tokens) or n


NAME_CHARS = 40   # a chart shows a variable's name up to this long, then cuts it in the middle


def short_names(names, labels=None, n: int = 30) -> list:
    """A readable, UNIQUE name for each column, in order: its label when it
    has one, else readable_name - shortened in the middle when still longer
    than `n` (the start says the section, the end the measure). Names that
    still collide get " (2)", " (3)"."""
    labels = labels or {}
    out, seen = [], {}
    for name in names:
        base = labels.get(str(name)) or readable_name(name)
        if len(base) > n:
            head = (n - 1) // 2
            base = base[:head] + "…" + base[-(n - 1 - head):]
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
    return out


def compact_number(v) -> str:
    """1,234 / 12.3K / 1.0M / 589.2M / 2.1B; "∞" for infinity, "n/a" for none."""
    v = _num(v) if not (isinstance(v, float) and math.isinf(v)) else v
    if v is None:
        return "n/a"
    if math.isinf(v):
        return "∞"
    a = abs(v)
    if a < 100:
        return f"{v:,.1f}"
    if a < 10_000:
        return f"{v:,.0f}"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{v / div:,.1f}{suffix}"
    return f"{v:,.0f}"


def vif_text(v) -> str:
    """A VIF as people read it: 3.4 / 148.8 / 1,000 / 1.0M (past ~100 the size
    no longer matters - the variable is a near-duplicate)."""
    v = _num(v)
    if v is None:
        return "n/a"
    return f"{v:.1f}" if v < 1000 else compact_number(v)


# --------------------------------------------------------------------------- #
# data - fit (04_fit/fit_metrics.csv, 04_fit/actual_vs_predicted.csv)
# --------------------------------------------------------------------------- #
AGGREGATE = "__aggregate__"     # fit_metrics.csv: every region summed per date
POOLED = "__all__"              # fit_metrics.csv: every region x date pooled


def fit_tiles(fit: pd.DataFrame, region: str = ALL) -> list:
    """[(label, value text, help)] - the fit numbers of the series the chart
    shows: with all regions, the AGGREGATE (the regions summed per date, one
    national series - codebase 1's __aggregate__ rows); with one region, that
    region's own. R² is of that series against its own mean. A run older
    than the __aggregate__ rows falls back to the within-region R² of all
    rows."""
    if fit is None or fit.empty or not {"region", "dataset"} <= set(fit.columns):
        return []
    names = fit["region"].astype(str)
    if region != ALL:
        rows, r2_col, what = fit[names == str(region)], "r2", f"{region}'s own series"
    elif (names == AGGREGATE).any():
        rows, r2_col, what = fit[names == AGGREGATE], "r2", \
            "the aggregate - every region summed per date, one national series"
    else:
        rows, r2_col, what = fit[names == POOLED], "r2_within_region", \
            "every region against its own mean (this run has no aggregate rows)"
    by = {str(r["dataset"]): r for _, r in rows.iterrows()}
    out = []

    def add(label, dataset, col, fmt, help_text):
        row = by.get(dataset)
        v = _num(row.get(col)) if row is not None and col in row else None
        if v is not None:
            out.append((label, fmt(v), help_text + f" Series: {what}."))

    add("R² · training", "train", r2_col, lambda v: f"{v:.2f}",
        "How much of the sales' movement around its own average the model explains "
        "on the weeks it was fitted on.")
    add("R² · holdout", "test", r2_col, lambda v: f"{v:.2f}",
        "The same on the held-out weeks the model never saw. Below 0 = worse than "
        "the series' own average.")
    add("MAPE · holdout", "test", "mape_pct", lambda v: f"{v:.1f}%",
        "Mean absolute % error on the held-out weeks.")
    add("Holdout inside the 90% band", "test", "coverage_90_pred_pct", lambda v: f"{v:.0f}%",
        "Share of held-out weeks inside the 90% prediction band - about 90% is "
        "well calibrated; far below means over-confident.")
    return out


def fit_regions(avp: pd.DataFrame) -> list:
    if avp is None or avp.empty or "region" not in avp:
        return [ALL]
    return [ALL] + sorted(avp["region"].astype(str).unique())


def fit_series(avp: pd.DataFrame, region: str = ALL):
    """(frame, holdout) for one region, or summed over all regions.

    frame: date, actual, fitted[, pred_lo90, pred_hi90] - the band only for
    one region (a quantile of a sum is not the sum of the quantiles).
    holdout: (first, last) date of the test weeks, or None."""
    d = avp.copy()
    d["date"] = pd.to_datetime(d["date"])
    if region != ALL:
        d = d[d["region"].astype(str) == region]
        keep = [c for c in ("date", "actual", "fitted", "pred_lo90", "pred_hi90", "dataset")
                if c in d.columns]
        frame = d[keep].sort_values("date").reset_index(drop=True)
    else:
        agg = {"actual": ("actual", "sum"), "fitted": ("fitted", "sum")}
        if "dataset" in d.columns:
            agg["dataset"] = ("dataset", "first")
        frame = d.groupby("date").agg(**agg).reset_index().sort_values("date")
        frame = frame.reset_index(drop=True)
    holdout = None
    if "dataset" in frame.columns:
        test = frame[frame["dataset"].astype(str) == "test"]
        if len(test):
            holdout = (test["date"].min(), test["date"].max())
        frame = frame.drop(columns=["dataset"])
    return frame, holdout


# --------------------------------------------------------------------------- #
# data - pillars and their colours (shared by contributions and decomposition)
# --------------------------------------------------------------------------- #
def _components(summary: pd.DataFrame, region: str, period: str) -> pd.DataFrame:
    d = summary[(summary["region"].astype(str) == region)
                & (summary["period"].astype(str) == period)]
    if "row_type" in d:
        d = d[d["row_type"].astype(str) == "component"]
    return d


def pillar_order(summary: pd.DataFrame) -> list:
    """Pillars largest first by their size over the WHOLE run (portfolio,
    Total) - the order that fixes each pillar's colour everywhere."""
    if summary is None or summary.empty:
        return []
    d = _components(summary, PORTFOLIO, "Total")
    if d.empty:                              # no portfolio rows: every region's Total
        d = summary[summary["period"].astype(str) == "Total"]
        if "row_type" in d:
            d = d[d["row_type"].astype(str) == "component"]
    d = d[~d["feature"].astype(str).str.startswith("__")]
    size = (pd.to_numeric(d["volume"], errors="coerce").abs()
            .groupby(d["pillar"].map(_pillar)).sum())
    order = [p for p in size.sort_values(ascending=False, kind="stable").index
             if p not in (OTHER, UNASSIGNED)]
    return order + [p for p in (UNASSIGNED, OTHER) if p in size.index]


def colour_map(pillars: list, mode: str = "light") -> dict:
    """{pillar: colour}: the first MAX_COLOURED pillars take the categorical
    slots in order; the rest - and "Other" and "Unassigned" - share the muted
    grey; the baseline core is the recessive neutral."""
    slots, ink = SERIES[mode], _ink(mode)
    named = [p for p in pillars if p not in (OTHER, UNASSIGNED)]
    out = {BASELINE_CORE: ink["neutral"], OTHER: ink["muted"], UNASSIGNED: ink["muted"]}
    for i, p in enumerate(named):
        out[p] = slots[i] if i < MAX_COLOURED else ink["muted"]
    return out


def shown_groups(pillars: list) -> list:
    """The decomposition's series: the coloured pillars, then "Other" when
    anything folds into it (pillars past the colours - and then "Unassigned"
    too, so two grey series never sit side by side), else "Unassigned" when
    there is one."""
    named = [p for p in pillars if p not in (OTHER, UNASSIGNED)]
    keep = named[:MAX_COLOURED]
    if len(named) > MAX_COLOURED or OTHER in pillars:
        return keep + [OTHER]
    return keep + ([UNASSIGNED] if UNASSIGNED in pillars else [])


# --------------------------------------------------------------------------- #
# data - contributions (05_contributions/contribution_summary.csv)
# --------------------------------------------------------------------------- #
def summary_choices(summary: pd.DataFrame):
    """(regions, periods) to offer: the portfolio first, Total first. Weekly
    blocks (period_split: week) are for timing, so only Total is offered."""
    regions = list(dict.fromkeys(summary["region"].astype(str)))
    regions = ([PORTFOLIO] if PORTFOLIO in regions else []) + sorted(r for r in regions
                                                                     if r != PORTFOLIO)
    periods = list(dict.fromkeys(summary["period"].astype(str)))
    periods = (["Total"] if "Total" in periods else []) + [p for p in periods if p != "Total"]
    if len(periods) > 12:
        periods = periods[:1]
    return regions, periods


def contribution_bars(summary: pd.DataFrame, region: str = PORTFOLIO, period: str = "Total"):
    """(bars, totals) - every driver's share of sales in one region x period.

    bars: feature, pillar, pct, volume - the baseline features and the
    incremental drivers, smallest first (Plotly draws the first at the bottom).
    totals: % of sales carried by the baseline core (intercept + seasonality +
    trend), by the drivers, and by the residual + median-gap lines - the three
    add to 100."""
    d = _components(summary, region, period)
    feature = d["feature"].astype(str)
    special = feature.str.startswith("__")
    body = d[~special]
    bars = pd.DataFrame({
        "feature": body["feature"].astype(str).values,
        "pillar": body["pillar"].map(_pillar).values if "pillar" in body else UNASSIGNED,
        "sub_pillar": body["sub_pillar"].map(_text).values if "sub_pillar" in body else "",
        "label": body["label"].map(_text).values if "label" in body else "",
        "pct": pd.to_numeric(body["contribution_pct"], errors="coerce").values,
        "volume": pd.to_numeric(body["volume"], errors="coerce").values,
    })
    bars = bars.dropna(subset=["pct"]).sort_values("pct", kind="stable").reset_index(drop=True)
    pct = pd.to_numeric(d["contribution_pct"], errors="coerce")
    core = float(pct[feature == "__baseline_core__"].sum())
    rest = float(pct[special & (feature != "__baseline_core__")].sum())
    return bars, {"core": core, "drivers": float(bars["pct"].sum()), "other": rest}


def by_pillar(bars: pd.DataFrame) -> pd.DataFrame:
    """The bars rolled up to one per pillar (the vendor-deck view)."""
    if bars.empty:
        return bars
    out = bars.groupby("pillar", as_index=False)[["pct", "volume"]].sum()
    out["feature"] = out["pillar"]
    return out.sort_values("pct", kind="stable").reset_index(drop=True)[
        ["feature", "pillar", "pct", "volume"]]


def pillar_rows(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per pillar, largest first: pillar, pct, volume, n (variables).
    "Unassigned" - the variables without a pillar - comes last."""
    if bars.empty:
        return pd.DataFrame(columns=["pillar", "pct", "volume", "n"])
    out = (bars.groupby("pillar", as_index=False)
           .agg(pct=("pct", "sum"), volume=("volume", "sum"), n=("feature", "size")))
    out["_last"] = out["pillar"].isin([UNASSIGNED, OTHER])
    return (out.sort_values(["_last", "pct"], ascending=[True, False], kind="stable")
            .drop(columns="_last").reset_index(drop=True))


def sub_key(pillar, sub) -> str:
    """The key an open sub-pillar is remembered by (pillar + sub-pillar)."""
    return f"{pillar}\x1f{sub}"


def has_sub_pillars(bars: pd.DataFrame, pillar) -> bool:
    """Does any of the pillar's variables name a sub-pillar?"""
    if bars.empty or "sub_pillar" not in bars:
        return False
    return bool((bars.loc[bars["pillar"] == pillar, "sub_pillar"].map(_text) != "").any())


def sub_pillar_rows(bars: pd.DataFrame, pillar) -> pd.DataFrame:
    """One pillar's sub-pillars, largest first: sub_pillar, pct, volume, n. A
    variable with no sub-pillar sits in one named like its pillar (as in
    codebase 1's contribution_by_sub_pillar.csv)."""
    members = bars[bars["pillar"] == pillar].copy()
    if members.empty:
        return pd.DataFrame(columns=["sub_pillar", "pct", "volume", "n"])
    sub = members["sub_pillar"].map(_text) if "sub_pillar" in members else ""
    members["_sub"] = [x or pillar for x in (sub if len(sub) else [""] * len(members))]
    out = (members.groupby("_sub", as_index=False)
           .agg(pct=("pct", "sum"), volume=("volume", "sum"), n=("feature", "size"))
           .rename(columns={"_sub": "sub_pillar"}))
    return out.sort_values("pct", ascending=False, kind="stable").reset_index(drop=True)


def _members(bars, pillar, sub=None):
    m = bars[bars["pillar"] == pillar]
    if sub is not None:
        names = m["sub_pillar"].map(_text) if "sub_pillar" in m else pd.Series("", index=m.index)
        m = m[[(x or pillar) == sub for x in names]]
    return m.sort_values("pct", ascending=False, kind="stable")


def display_name(row) -> str:
    """A bar's variable as people read it: its label, else readable_name."""
    return _text(getattr(row, "label", "")) or readable_name(row.feature)


def contribution_tree(bars: pd.DataFrame, open_pillars=(), open_subs=()) -> pd.DataFrame:
    """The contribution chart as a tree, top to bottom: each pillar (largest
    first, "Unassigned" last); an OPEN pillar lists its sub-pillars when its
    variables name any (else its variables), and an open sub-pillar
    (`open_subs`: sub_key values) lists its variables - largest first at every
    level. Columns: label, pillar, sub_pillar, feature, level (0 pillar, 1
    sub-pillar or variable, 2 variable), pct, volume, n."""
    rows = []
    opened, opened_subs = set(open_pillars or ()), set(open_subs or ())
    pad = "\u00a0\u00a0\u00a0"

    def variable(m, pillar, sub, level):
        return {"label": pad * (level - 1) + f"↳ {display_name(m)}", "pillar": pillar,
                "sub_pillar": sub, "feature": m.feature, "level": level,
                "pct": float(m.pct),
                "volume": float(m.volume) if m.volume == m.volume else 0.0, "n": 1}

    for p in pillar_rows(bars).itertuples(index=False):
        rows.append({"label": p.pillar, "pillar": p.pillar, "sub_pillar": "", "feature": "",
                     "level": 0, "pct": float(p.pct), "volume": float(p.volume),
                     "n": int(p.n)})
        if p.pillar not in opened:
            continue
        if has_sub_pillars(bars, p.pillar):
            for sp in sub_pillar_rows(bars, p.pillar).itertuples(index=False):
                rows.append({"label": f"↳ {sp.sub_pillar}", "pillar": p.pillar,
                             "sub_pillar": sp.sub_pillar, "feature": "", "level": 1,
                             "pct": float(sp.pct), "volume": float(sp.volume),
                             "n": int(sp.n)})
                if sub_key(p.pillar, sp.sub_pillar) in opened_subs:
                    for m in _members(bars, p.pillar, sp.sub_pillar).itertuples(index=False):
                        rows.append(variable(m, p.pillar, sp.sub_pillar, 2))
        else:
            for m in _members(bars, p.pillar).itertuples(index=False):
                rows.append(variable(m, p.pillar, "", 1))
    return pd.DataFrame(rows, columns=["label", "pillar", "sub_pillar", "feature", "level",
                                       "pct", "volume", "n"])


def sales_split(summary: pd.DataFrame, region: str = PORTFOLIO,
                period: str = "Total") -> dict:
    """{"base", "incremental", "other"}: shares of actual sales - the base
    (intercept + trend + seasonality AND the baseline drivers such as
    distribution and price), what every other pillar drives, and what the
    model does not explain (residual + median gap). They add up to 100."""
    bars, totals = contribution_bars(summary, region, period)
    base_drivers = float(bars.loc[bars["pillar"] == "Baseline", "pct"].sum())
    return {"base": totals["core"] + base_drivers,
            "incremental": totals["drivers"] - base_drivers,
            "other": totals["other"]}


def waterfall_steps(summary: pd.DataFrame, region: str = PORTFOLIO,
                    period: str = "Total", include_base: bool = False) -> pd.DataFrame:
    """The due-to waterfall, top to bottom: every pillar but the baseline,
    largest first ("Unassigned" last), each a share of actual sales, building
    to the incremental total. The base is ~90% of sales and would flatten
    every pillar into a sliver, so it is left out unless `include_base`:
    then the base (intercept + trend + seasonality) and the baseline drivers
    lead, "Not explained" follows, and the total is all of sales (100%).
    Columns: label, value, kind (core | pillar | other | total), pillar,
    start, end."""
    bars, totals = contribution_bars(summary, region, period)
    rows = pillar_rows(bars)
    steps = []
    if include_base:
        steps.append(("Base", totals["core"], "core", BASELINE_CORE))
        steps += [("Baseline drivers", float(r.pct), "pillar", r.pillar)
                  for r in rows.itertuples(index=False) if r.pillar == "Baseline"]
    steps += [(r.pillar, float(r.pct), "pillar", r.pillar)
              for r in rows.itertuples(index=False) if r.pillar != "Baseline"]
    if include_base and abs(totals["other"]) > 1e-9:
        steps.append(("Not explained", totals["other"], "other", OTHER))
    out, run = [], 0.0
    for label, value, kind, pillar in steps:
        out.append({"label": label, "value": float(value), "kind": kind, "pillar": pillar,
                    "start": run, "end": run + float(value)})
        run += float(value)
    out.append({"label": "Total sales" if include_base else "Incremental sales",
                "value": run, "kind": "total", "pillar": "", "start": 0.0, "end": run})
    return pd.DataFrame(out, columns=["label", "value", "kind", "pillar", "start", "end"])


def top_drivers(summary: pd.DataFrame, region: str = PORTFOLIO, period: str = "Total",
                n: int = 10) -> pd.DataFrame:
    """The `n` largest incremental drivers (not the baseline drivers) by their
    share of sales, smallest first (Plotly draws the first at the bottom),
    with a readable unique `name`."""
    bars, _totals = contribution_bars(summary, region, period)
    d = bars[bars["pillar"] != "Baseline"]
    d = d.reindex(d["pct"].abs().sort_values(ascending=False, kind="stable").index).head(n)
    d = d.assign(name=short_names(list(d["feature"]),
                                  dict(zip(d["feature"], d["label"].map(_text))), NAME_CHARS))
    return d.iloc[::-1].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# data - decomposition (05_contributions/contribution_timeseries.csv)
# --------------------------------------------------------------------------- #
def drill_series(ts: pd.DataFrame, pillar: str) -> list:
    """The series a pillar splits into over time - its sub-pillars when its
    variables name any, else its variables (by label) - largest first over
    the WHOLE run, so each keeps its colour in every region."""
    d = ts[(ts["pillar"].map(_pillar) == pillar)
           & ~ts["feature"].astype(str).str.startswith("__")].copy()
    if d.empty:
        return []
    d["series"] = _drill_names(d, pillar)
    size = pd.to_numeric(d["volume"], errors="coerce").abs().groupby(d["series"]).sum()
    return list(size.sort_values(ascending=False, kind="stable").index)


def _drill_names(d, pillar):
    subs = d["sub_pillar"].map(_text) if "sub_pillar" in d else pd.Series("", index=d.index)
    if (subs != "").any():
        return [x or pillar for x in subs]
    labels = d["label"].map(_text) if "label" in d else pd.Series("", index=d.index)
    return [lab or readable_name(f) for lab, f in zip(labels, d["feature"].astype(str))]


def decomposition_frame(ts: pd.DataFrame, pillars: list, region: str = ALL,
                        pillar: str | None = None):
    """(wide, actual): weekly volume of the baseline core and of each shown
    pillar group (columns: Baseline core first, then `shown_groups` order),
    and the actual sales line - for one region or summed over all.

    With `pillar`, that pillar alone, split into its sub-pillars (or its
    variables): columns in `drill_series` order, the first MAX_COLOURED
    coloured and the rest folded into "Other"; no actual line."""
    d = ts.copy()
    d["date"] = pd.to_datetime(d["date"])
    if region != ALL:
        d = d[d["region"].astype(str) == region]
    if pillar is not None:
        order = drill_series(ts, pillar)
        keep = order[:MAX_COLOURED]
        part = d[(d["pillar"].map(_pillar) == pillar)
                 & ~d["feature"].astype(str).str.startswith("__")].copy()
        part["volume"] = pd.to_numeric(part["volume"], errors="coerce")
        part["series"] = [s if s in keep else OTHER for s in _drill_names(part, pillar)]
        wide = part.pivot_table(index="date", columns="series", values="volume",
                                aggfunc="sum").fillna(0.0)
        cols = [c for c in keep + [OTHER] if c in wide.columns]
        return wide[cols], pd.Series(dtype=float)
    feature = d["feature"].astype(str)
    d["volume"] = pd.to_numeric(d["volume"], errors="coerce")
    actual = d[feature == "__actual__"].groupby("date")["volume"].sum()
    comp = d[~feature.str.startswith("__") | (feature == "__baseline_core__")].copy()
    groups = shown_groups(pillars)
    series = np.where(comp["feature"].astype(str) == "__baseline_core__", BASELINE_CORE,
                      comp["pillar"].map(_pillar))
    comp["series"] = [s if (s == BASELINE_CORE or s in groups) else OTHER for s in series]
    wide = comp.pivot_table(index="date", columns="series", values="volume",
                            aggfunc="sum").fillna(0.0)
    order = [BASELINE_CORE] + groups + ([OTHER] if OTHER not in groups else [])
    wide = wide[[g for g in order if g in wide.columns]]
    return wide, actual


# --------------------------------------------------------------------------- #
# data - collinearity (01_data/collinearity_matrix.csv, _vif.csv, _summary.csv)
# --------------------------------------------------------------------------- #
VIF_WARN, VIF_BAD = 5.0, 10.0     # codebase 1's assumption defaults (vif_warn, vif_bad)
DESIGN_EXTRAS = ("__intercept__", "__trend__")


def is_design_extra(name) -> bool:
    """The model's own columns - intercept, seasonality (Fourier), trend."""
    n = str(name)
    return n in DESIGN_EXTRAS or n.startswith("__fourier__")


def collinearity_regions(*frames) -> list:
    """The regions any of the collinearity files covers, in file order."""
    seen = []
    for f in frames:
        if f is not None and len(f) and "region" in f:
            seen += [r for r in f["region"].astype(str) if r not in seen]
    return seen


def correlation_grid(matrix: pd.DataFrame, region: str, top: int | None = 25,
                     with_extras: bool = True):
    """(labels, grid) - one region's correlation matrix as a square array, for
    the heatmap. `top` keeps the columns most correlated with another (their
    largest |r|), in design order (seasonality and trend first, then the
    features as the model lists them); None keeps all. `with_extras` False
    leaves out the seasonality / trend columns."""
    d = matrix[matrix["region"].astype(str) == str(region)]
    if d.empty:
        return [], np.zeros((0, 0))
    order = list(dict.fromkeys(d["column_a"].astype(str)))
    if not with_extras:
        order = [c for c in order if not is_design_extra(c)]
    wide = (d.assign(column_a=d["column_a"].astype(str), column_b=d["column_b"].astype(str))
            .pivot_table(index="column_a", columns="column_b", values="correlation",
                         aggfunc="first")
            .reindex(index=order, columns=order))
    if top and len(order) > top:
        off = wide.abs().where(~np.eye(len(order), dtype=bool))
        strength = off.max(axis=1).fillna(0.0)
        keep = set(strength.sort_values(ascending=False, kind="stable").index[:top])
        order = [c for c in order if c in keep]
        wide = wide.reindex(index=order, columns=order)
    return order, wide.to_numpy(dtype=float)


def strongest_pairs(matrix: pd.DataFrame, region: str, limit: int = 10,
                    with_extras: bool = True) -> pd.DataFrame:
    """The most correlated pairs of one region, |r| largest first (each pair
    once, no column with itself); `with_extras` False: features only."""
    d = matrix[matrix["region"].astype(str) == str(region)].copy()
    d = d[d["column_a"].astype(str) < d["column_b"].astype(str)]
    if not with_extras:
        d = d[~d["column_a"].map(is_design_extra) & ~d["column_b"].map(is_design_extra)]
    d["abs"] = pd.to_numeric(d["correlation"], errors="coerce").abs()
    return (d.sort_values("abs", ascending=False, kind="stable").head(limit)
            .drop(columns=["abs"]).reset_index(drop=True))


def vif_points(vif: pd.DataFrame, region: str) -> pd.DataFrame:
    """One region's VIFs, worst first: column, vif, vif_uncentred, duplicates,
    explained_by, vif_note (blank VIFs - not computable - last, with their
    note)."""
    if vif is None or vif.empty:
        return pd.DataFrame(columns=["column", "vif", "vif_uncentred", "duplicates",
                                     "explained_by", "vif_note"])
    d = vif[vif["region"].astype(str) == str(region)].copy()
    for col in ("vif", "vif_uncentred"):
        if col in d:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    keep = [c for c in ("column", "vif", "vif_uncentred", "duplicates", "explained_by",
                        "vif_note") if c in d.columns]
    return (d.sort_values("vif", ascending=False, na_position="last", kind="stable")[keep]
            .reset_index(drop=True))


VIF_CAP = 30.0                      # the VIF chart's axis ends here; larger bars say "»"
PAIR_MARK = 0.8                     # |r| from which two columns are hard to separate


def pair_count(matrix: pd.DataFrame, region: str, mark: float = PAIR_MARK) -> int:
    """How many pairs of VARIABLES (not seasonality / trend) have |r| >= mark."""
    if matrix is None or matrix.empty:
        return 0
    d = strongest_pairs(matrix, region, limit=10 ** 6, with_extras=False)
    return int((pd.to_numeric(d["correlation"], errors="coerce").abs() >= mark).sum())


def collinearity_tiles(summary: pd.DataFrame, region: str, matrix=None,
                       labels=None) -> list:
    """[(label, value text, help)] - one region's collinearity in four numbers:
    the verdict, the largest VIF (as people read it - past ~100 it only says
    "near-duplicate"), the pairs of variables at |r| >= 0.8, the condition
    number (compact)."""
    if summary is None or summary.empty:
        return []
    rows = summary[summary["region"].astype(str) == str(region)]
    if rows.empty:
        return []
    r = rows.iloc[0]
    out = [("Verdict", _text(r.get("verdict")) or "-",
            "ok / moderate / severe - codebase 1's reading of the VIFs and the condition "
            "number together.")]
    mx = _num(r.get("max_vif"))
    worst = _text(r.get("worst_column"))
    worst_name = short_names([worst], labels, 60)[0] if worst else ""
    explained = " + ".join(short_names([x.strip() for x in
                                        _text(r.get("worst_explained_by")).split("+")
                                        if x.strip()], labels, 40))
    out.append(("Largest VIF", vif_text(mx),
                "VIF = 1 / (1 - R²) of a variable regressed on all the others: how much "
                "its coefficient's uncertainty is inflated by them. 1 = none, above 5 worth "
                "a look, above 10 barely identified. It is unbounded: a variable that "
                "(nearly) copies another reaches thousands or millions - any value past "
                "~100 just means \"near-duplicate\"."
                + (f" Here: {worst_name}" + (f", explained by {explained}." if explained
                                             else ".") if worst_name else "")))
    if matrix is not None:
        out.append((f"Variable pairs above {PAIR_MARK:g}", str(pair_count(matrix, region)),
                    "Pairs of variables (seasonality and trend left out) whose correlation "
                    f"is {PAIR_MARK:g} or more either way: the model can hardly tell them "
                    "apart."))
    cond = _num(r.get("condition_number"))
    if cond is not None or str(r.get("condition_number")) == "inf":
        out.append(("Condition number", "∞" if cond is None or math.isinf(cond)
                    else compact_number(cond),
                    "How close the whole design is to singular (Belsley, on the uncentred "
                    "design): above 10 warns, above 30 is severe, in the thousands or more "
                    "some columns are near-exact combinations of others."))
    return out


def pair_bars(matrix: pd.DataFrame, region: str, limit: int = 12,
              with_extras: bool = False, labels=None) -> pd.DataFrame:
    """The most correlated pairs, |r| largest first, each with a readable
    "A ↔ B" label (`pair`) - column_a, column_b, correlation, pair."""
    p = strongest_pairs(matrix, region, limit, with_extras).copy()
    if p.empty:
        return pd.DataFrame(columns=["column_a", "column_b", "correlation", "pair"])
    p["correlation"] = pd.to_numeric(p["correlation"], errors="coerce")
    cols = list(dict.fromkeys(list(p["column_a"].astype(str)) + list(p["column_b"].astype(str))))
    short = dict(zip(cols, short_names(cols, labels, NAME_CHARS)))
    p["pair"] = [f"{short[str(a)]} ↔ {short[str(b)]}"
                 for a, b in zip(p["column_a"], p["column_b"])]
    return p.reset_index(drop=True)


def collinearity_findings(vif: pd.DataFrame, matrix: pd.DataFrame, region: str,
                          labels=None, limit: int = 4) -> list:
    """What the collinearity means, in plain sentences (no markup): the pairs
    of variables that move together (|r| >= 0.8), then any other variable
    the rest explain (VIF > 10) - or that there is nothing to worry about."""
    out, named = [], set()
    pairs = (strongest_pairs(matrix, region, limit=50, with_extras=False)
             if matrix is not None and len(matrix) else pd.DataFrame())
    every = list(dict.fromkeys(
        (list(pairs["column_a"].astype(str)) + list(pairs["column_b"].astype(str)))
        if len(pairs) else []))
    vp = vif_points(vif, region) if vif is not None else pd.DataFrame()
    every += [str(c) for c in vp.get("column", []) if str(c) not in every]
    short = dict(zip(every, short_names(every, labels, 48)))
    for row in pairs.itertuples(index=False):
        r = _num(row.correlation)
        if r is None or abs(r) < PAIR_MARK or len(out) >= limit:
            continue
        a, b = short[str(row.column_a)], short[str(row.column_b)]
        if round(abs(r), 2) >= 0.95:
            out.append(f"{a} and {b} move together almost exactly (r = {r:.2f}): the model "
                       "cannot tell their effects apart. Combine them into one variable, "
                       "drop one, or hold one with a tight prior.")
        else:
            out.append(f"{a} and {b} move together strongly (r = {r:.2f}): how their joint "
                       "effect is split between them is uncertain.")
        named |= {str(row.column_a), str(row.column_b)}
    for row in vp.itertuples(index=False) if len(vp) else []:
        v = _num(getattr(row, "vif", None))
        if len(out) >= limit or v is None or v <= VIF_BAD or str(row.column) in named \
                or is_design_extra(row.column):
            continue
        parts = [x.strip() for x in _text(getattr(row, "explained_by", "")).split("+")
                 if x.strip()]
        by = " + ".join(short.get(x, short_names([x], labels, 48)[0]) for x in parts)
        out.append(f"{short[str(row.column)]} is largely explained by "
                   f"{by or 'the other variables'} (VIF {vif_text(v)}): its own effect is "
                   "hard to pin down.")
    if not out:
        out.append(f"No worrying overlap: no pair of variables moves together at "
                   f"{PAIR_MARK:g} or more and every VIF is under {VIF_BAD:g}.")
    return out


def vif_bars(points: pd.DataFrame, labels=None, cap: float = VIF_CAP) -> pd.DataFrame:
    """The VIF chart's rows, worst first: column, name (readable, unique), vif,
    band (ok <= 5 < watch <= 10 < high), shown (the bar, capped at `cap`),
    capped, text (the value at the bar's end - "» 1.0M" past the cap)."""
    cols = ["column", "name", "vif", "band", "shown", "capped", "text"]
    if points is None or points.empty:
        return pd.DataFrame(columns=cols)
    d = points.dropna(subset=["vif"]).copy()
    d = d[d["vif"] > 0]
    if d.empty:
        return pd.DataFrame(columns=cols)
    d["name"] = short_names(list(d["column"]), labels, NAME_CHARS)
    d["band"] = np.select([d["vif"] > VIF_BAD, d["vif"] > VIF_WARN], ["high", "watch"],
                          default="ok")
    d["shown"] = d["vif"].clip(upper=cap)
    d["capped"] = d["vif"] > cap
    d["text"] = [("» " if c else "") + vif_text(v) for v, c in zip(d["vif"], d["capped"])]
    return d[cols + [c for c in ("explained_by", "duplicates") if c in d]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# data - the run's health (02_convergence/convergence_report.txt, sampling_log.json)
# --------------------------------------------------------------------------- #
RHAT_GOOD, RHAT_BAD = 1.01, 1.05      # codebase 1's RHAT_WARN / RHAT_FAIL
DIVERGENCE_BAD = 0.01                 # its DIVERGENCE_FRAC_FAIL: 1% of draws


def run_health(report_text: str, sampling_log: dict | None = None) -> dict:
    """Max R-hat and divergences, from the two lines codebase 1 writes at the
    top of convergence_report.txt (every run has them), and the share of
    draws that diverged when sampling_log.json says how many there were.
    Statuses: good / warning / critical, or "" when not known."""
    text = report_text or ""
    m = re.search(r"max R-hat\s*:\s*([0-9]*\.?[0-9]+)", text)
    d = re.search(r"divergences\s*:\s*([0-9]+)", text)
    rhat = float(m.group(1)) if m else None
    div = int(d.group(1)) if d else None
    log = sampling_log or {}
    draws = None
    try:
        if log.get("draws") and log.get("chains"):
            draws = int(log["draws"]) * int(log["chains"])
        elif log.get("posterior_draws"):
            draws = int(log["posterior_draws"])
    except (TypeError, ValueError):
        draws = None
    frac = (div / draws) if (div is not None and draws) else None
    out = {"rhat": rhat, "divergences": div, "draws": draws, "divergence_frac": frac,
           "rhat_status": "", "divergence_status": ""}
    if rhat is not None:
        out["rhat_status"] = ("good" if rhat < RHAT_GOOD else
                              "warning" if rhat <= RHAT_BAD else "critical")
    elif "n/a" in text and "R-hat" in text:
        out["rhat_status"] = "warning"            # ADVI / one chain: not measurable
    if div is not None:
        out["divergence_status"] = ("good" if div == 0 else
                                    "critical" if frac is not None and frac >= DIVERGENCE_BAD
                                    else "warning")
    return out


HEALTH_WORDS = {"good": "✓ converged", "warning": "! check", "critical": "✕ not converged"}
DIVERGENCE_WORDS = {"good": "✓ none", "warning": "! a few", "critical": "✕ too many"}


# --------------------------------------------------------------------------- #
# data - warnings (00_warnings/all_warnings.csv, warning_texts.csv)
# --------------------------------------------------------------------------- #
SEVERITY_ORDER = {"high": 0, "medium": 1, "review": 2, "info": 3}
NOT_A_VARIABLE = "(not about one variable)"


def _warning_frame(table: pd.DataFrame) -> pd.DataFrame:
    t = table.copy()
    for col in ("severity", "category", "feature", "region"):
        if col not in t:
            t[col] = ""
        t[col] = t[col].map(_text)
    return t


def warning_summary(table: pd.DataFrame) -> pd.DataFrame:
    """One row per category: severity, category, `variables` - how many
    DIFFERENT variables it is about (a variable warned in five regions counts
    once), `regions` - in how many regions, `warnings` - how many rows the
    file has. Worst severity first, then most variables."""
    cols = ["severity", "category", "variables", "regions", "warnings"]
    if table is None or table.empty:
        return pd.DataFrame(columns=cols)
    t = _warning_frame(table)
    rows = []
    for (severity, category), d in t.groupby(["severity", "category"], sort=False):
        rows.append({"severity": severity, "category": category,
                     "variables": int(d.loc[d["feature"] != "", "feature"].nunique()),
                     "regions": int(d.loc[d["region"] != "", "region"].nunique()),
                     "warnings": int(len(d))})
    out = pd.DataFrame(rows, columns=cols)
    out["_rank"] = out["severity"].map(lambda v: SEVERITY_ORDER.get(str(v).lower(), 9))
    return (out.sort_values(["_rank", "variables", "warnings"], ascending=[True, False, False],
                            kind="stable").drop(columns="_rank").reset_index(drop=True))


def warning_detail(table: pd.DataFrame, category: str) -> pd.DataFrame:
    """One row per VARIABLE a category is about: variable, regions (which),
    warnings (rows). A warning that names no variable is one row of its own."""
    cols = ["variable", "regions", "warnings"]
    if table is None or table.empty:
        return pd.DataFrame(columns=cols)
    t = _warning_frame(table)
    d = t[t["category"] == str(category)]
    rows = []
    for feature, g in d[d["feature"] != ""].groupby("feature", sort=False):
        regions = list(dict.fromkeys(r for r in g["region"] if r))
        rows.append({"variable": feature,
                     "regions": ", ".join(regions) if regions else "-",
                     "warnings": int(len(g))})
    unnamed = d[d["feature"] == ""]
    if len(unnamed):
        regions = list(dict.fromkeys(r for r in unnamed["region"] if r))
        rows.append({"variable": NOT_A_VARIABLE,
                     "regions": ", ".join(regions) if regions else "-",
                     "warnings": int(len(unnamed))})
    return pd.DataFrame(rows, columns=cols)


def warning_examples(texts: pd.DataFrame, category: str, limit: int = 3) -> list:
    """What a category's warnings actually SAY - one example message per
    kind (warning_texts.csv), most frequent first."""
    if texts is None or texts.empty or "category" not in texts:
        return []
    d = texts[texts["category"].map(_text) == str(category)]
    if "n" in d:
        d = d.sort_values("n", ascending=False, kind="stable")
    col = "example" if "example" in d else ("template" if "template" in d else None)
    return [_text(v) for v in d[col].head(limit)] if col else []


# --------------------------------------------------------------------------- #
# figures (Plotly - imported only here)
# --------------------------------------------------------------------------- #
def _alpha(hex_colour: str, a: float) -> str:
    h = hex_colour.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def _style(fig, mode, height, y_title="", x_title="", legend=True):
    ink = _ink(mode)
    fig.update_layout(
        template="none", height=height,
        margin=dict(l=8, r=16, t=40 if legend else 12, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_font(), color=ink["secondary"], size=12),
        hoverlabel=dict(font=dict(family=_font(), size=12)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
                    font=dict(color=ink["secondary"]), bgcolor="rgba(0,0,0,0)"))
    axis = dict(showgrid=True, gridcolor=ink["grid"], gridwidth=1, zeroline=False,
                showline=False, ticks="", tickfont=dict(color=ink["muted"]),
                title_font=dict(color=ink["secondary"]), automargin=True)
    fig.update_xaxes(**axis, title_text=x_title)
    fig.update_yaxes(**axis, title_text=y_title)
    return fig


def fit_figure(frame: pd.DataFrame, holdout, mode: str = "light"):
    import plotly.graph_objects as go
    ink, blue = _ink(mode), SERIES[mode][0]
    fig = go.Figure()
    if holdout is not None:
        fig.add_vrect(x0=holdout[0], x1=holdout[1], fillcolor=ink["neutral"],
                      opacity=0.25, line_width=0, layer="below",
                      annotation_text="Holdout", annotation_position="top left",
                      annotation_font=dict(color=ink["muted"], size=11))
    if {"pred_lo90", "pred_hi90"} <= set(frame.columns):
        fig.add_trace(go.Scatter(x=frame["date"], y=frame["pred_hi90"], mode="lines",
                                 line=dict(width=0), hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(x=frame["date"], y=frame["pred_lo90"], mode="lines",
                                 line=dict(width=0), fill="tonexty",
                                 fillcolor=_alpha(blue, 0.12), name="90% prediction band",
                                 hoverinfo="skip", legendrank=3))
    fig.add_trace(go.Scatter(x=frame["date"], y=frame["actual"], mode="lines", name="Actual",
                             line=dict(color=ink["primary"], width=2), legendrank=1,
                             hovertemplate="<b>%{y:,.0f}</b>  actual<extra></extra>"))
    fig.add_trace(go.Scatter(x=frame["date"], y=frame["fitted"], mode="lines", name="Fitted",
                             line=dict(color=blue, width=2), legendrank=2,
                             hovertemplate="<b>%{y:,.0f}</b>  fitted<extra></extra>"))
    _style(fig, mode, 380, y_title="Sales")
    # a filled band flips Plotly's default legend order; keep Actual, Fitted, band
    fig.update_layout(hovermode="x unified", legend_traceorder="normal")
    fig.update_yaxes(tickformat="~s")
    return fig


def pct_label(v: float) -> str:
    """A share of sales for a bar end: 1.7% / 0.13% / -0.03% (two decimals
    under 1%, where most drivers sit)."""
    return f"{v:.1f}%" if abs(v) >= 1 or v == 0 else f"{v:.2f}%"


def contribution_figure(bars: pd.DataFrame, colours: dict, mode: str = "light"):
    """Horizontal bars, one per driver (or pillar), coloured by pillar; the
    legend lists the pillars in their fixed order."""
    import plotly.graph_objects as go
    ink = _ink(mode)
    fig = go.Figure()
    present = list(dict.fromkeys(bars["pillar"]))
    pillars = [p for p in colours if p in present] + [p for p in present if p not in colours]
    for pillar in pillars:
        b = bars[bars["pillar"] == pillar]
        fig.add_trace(go.Bar(
            x=b["pct"], y=b["feature"], orientation="h", name=pillar,
            marker=dict(color=colours.get(pillar, SERIES[mode][0])),
            text=[pct_label(v) for v in b["pct"]], textposition="outside",
            cliponaxis=False, textfont=dict(color=ink["secondary"], size=11),
            customdata=np.stack([b["pillar"].astype(str),
                                 b["volume"].fillna(0).astype(float)], axis=-1),
            hovertemplate="<b>%{x:.2f}%</b> of sales  ·  %{customdata[1]:,.0f}"
                          "<br>%{y} (%{customdata[0]})<extra></extra>"))
    n = max(len(bars), 1)
    one_per_pillar = bool((bars["feature"] == bars["pillar"]).all())
    _style(fig, mode, max(240, 24 * n + 90), x_title="Share of sales, %",
           legend=len(pillars) > 1 and not one_per_pillar)
    fig.update_layout(barmode="overlay", bargap=0.3, barcornerradius=4,
                      hovermode="closest")
    lo, hi = float(min(0.0, bars["pct"].min())), float(max(0.0, bars["pct"].max()))
    pad = (hi - lo) * 0.14 or 1.0
    fig.update_xaxes(range=[lo - (pad if lo < 0 else 0), hi + pad], zeroline=True,
                     zerolinecolor=ink["axis"], zerolinewidth=1)
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=list(bars["feature"]),
                     tickfont=dict(color=ink["secondary"]))
    return fig


def contribution_tree_figure(tree: pd.DataFrame, colours: dict, mode: str = "light"):
    """Horizontal bars for contribution_tree: a pillar's bar in its colour,
    its sub-pillars (when open) in a lighter shade of the same colour and
    their variables lighter still. Rows are placed by POSITION (a sub-pillar
    name can repeat across pillars); the y labels name every bar, so there
    is no legend."""
    import plotly.graph_objects as go
    ink = _ink(mode)
    fig = go.Figure()
    tree = tree.reset_index(drop=True)
    shade = {0: 1.0, 1: 0.6, 2: 0.35}
    for pillar in dict.fromkeys(tree["pillar"]):
        part = tree[tree["pillar"] == pillar]
        base = colours.get(pillar, SERIES[mode][0])
        fills = [base if lv == 0 else _alpha(base, shade.get(int(lv), 0.35))
                 for lv in part["level"]]
        fig.add_trace(go.Bar(
            x=part["pct"], y=list(part.index), orientation="h", name=pillar, width=0.62,
            marker=dict(color=fills), showlegend=False,
            text=[pct_label(v) for v in part["pct"]], textposition="outside",
            cliponaxis=False, textfont=dict(color=ink["secondary"], size=11),
            customdata=np.stack([part["pillar"].astype(str),
                                 part["volume"].fillna(0).astype(float),
                                 np.where(part["level"] == 0,
                                          part["n"].astype(str) + " variable(s)",
                                          part["feature"].astype(str))], axis=-1),
            hovertemplate="<b>%{x:.2f}%</b> of sales  ·  %{customdata[1]:,.0f}"
                          "<br>%{customdata[2]} (%{customdata[0]})<extra></extra>"))
    _style(fig, mode, max(240, 26 * max(len(tree), 1) + 70), x_title="Share of sales, %",
           legend=False)
    fig.update_layout(barmode="overlay", bargap=0.3, barcornerradius=4, hovermode="closest")
    lo, hi = float(min(0.0, tree["pct"].min())), float(max(0.0, tree["pct"].max()))
    pad = (hi - lo) * 0.14 or 1.0
    fig.update_xaxes(range=[lo - (pad if lo < 0 else 0), hi + pad], zeroline=True,
                     zerolinecolor=ink["axis"], zerolinewidth=1)
    fig.update_yaxes(showgrid=False, autorange="reversed", tickmode="array",
                     tickvals=list(tree.index), ticktext=list(tree["label"]),
                     tickfont=dict(color=ink["secondary"]))
    return fig


def waterfall_figure(steps: pd.DataFrame, colours: dict, mode: str = "light"):
    """The due-to waterfall as floating horizontal bars, top to bottom: each
    step starts where the last ended (a negative one steps back), coloured
    by its pillar - the base in the recessive neutral, "not explained" in the
    muted grey - and the total from zero in ink. Its value at the bar's end."""
    import plotly.graph_objects as go
    ink = _ink(mode)
    d = steps.reset_index(drop=True)
    fill = []
    for row in d.itertuples(index=False):
        fill.append(ink["neutral"] if row.kind == "core" else
                    ink["muted"] if row.kind == "other" else
                    ink["secondary"] if row.kind == "total" else
                    colours.get(row.pillar, SERIES[mode][0]))
    lo = np.minimum(d["start"], d["end"])
    text = [pct_label(v) if k in ("total", "core") else
            ("+" if v > 0 else "") + pct_label(v) for v, k in zip(d["value"], d["kind"])]
    fig = go.Figure(go.Bar(
        x=(d["end"] - d["start"]).abs(), base=lo, y=list(d.index), orientation="h",
        width=0.62,
        marker=dict(color=fill), text=text, textposition="outside", cliponaxis=False,
        textfont=dict(color=ink["secondary"], size=11), showlegend=False,
        customdata=np.stack([d["label"].astype(str), d["value"].astype(float),
                             d["end"].astype(float)], axis=-1),
        hovertemplate="<b>%{customdata[0]}</b>  %{customdata[1]:+.2f}% of sales"
                      "<br>running total %{customdata[2]:.1f}%<extra></extra>"))
    # hairline connectors: where one step ends the next begins
    for i in range(len(d) - 2):
        fig.add_shape(type="line", x0=d.loc[i, "end"], x1=d.loc[i, "end"],
                      y0=i + 0.35, y1=i + 0.65, line=dict(color=ink["axis"], width=1))
    _style(fig, mode, max(260, 34 * len(d) + 70), x_title="Share of sales, %", legend=False)
    fig.update_layout(bargap=0.35, barcornerradius=4, hovermode="closest")
    lo_x = float(min(0.0, d[["start", "end"]].min().min()))
    hi_x = float(max(0.0, d[["start", "end"]].max().max()))
    pad = (hi_x - lo_x) * 0.12 or 1.0
    fig.update_xaxes(range=[lo_x - (pad if lo_x < 0 else 0), hi_x + pad], zeroline=True,
                     zerolinecolor=ink["axis"], zerolinewidth=1)
    fig.update_yaxes(showgrid=False, autorange="reversed", tickmode="array",
                     tickvals=list(d.index), ticktext=list(d["label"]),
                     tickfont=dict(color=ink["secondary"]))
    return fig


def drivers_figure(d: pd.DataFrame, colours: dict, mode: str = "light"):
    """The top drivers (top_drivers): one bar each, coloured by pillar, the
    pillars in the legend in their fixed order, the share at the bar's end."""
    import plotly.graph_objects as go
    ink = _ink(mode)
    d = d.reset_index(drop=True)
    fig = go.Figure()
    present = list(dict.fromkeys(d["pillar"]))
    for pillar in [p for p in colours if p in present] + [p for p in present
                                                           if p not in colours]:
        part = d[d["pillar"] == pillar]
        fig.add_trace(go.Bar(
            x=part["pct"], y=list(part.index), orientation="h", name=pillar, width=0.62,
            marker=dict(color=colours.get(pillar, SERIES[mode][0])),
            text=[pct_label(v) for v in part["pct"]], textposition="outside",
            cliponaxis=False, textfont=dict(color=ink["secondary"], size=11),
            customdata=np.stack([part["name"].astype(str), part["feature"].astype(str)],
                                axis=-1),
            hovertemplate="<b>%{x:.2f}%</b> of sales<br>%{customdata[0]}"
                          " (%{customdata[1]})<extra></extra>"))
    _style(fig, mode, max(240, 28 * len(d) + 90), x_title="Share of sales, %",
           legend=len(present) > 1)
    fig.update_layout(barmode="overlay", bargap=0.3, barcornerradius=4, hovermode="closest")
    lo, hi = float(min(0.0, d["pct"].min())), float(max(0.0, d["pct"].max()))
    pad = (hi - lo) * 0.14 or 1.0
    fig.update_xaxes(range=[lo - (pad if lo < 0 else 0), hi + pad], zeroline=True,
                     zerolinecolor=ink["axis"], zerolinewidth=1)
    fig.update_yaxes(showgrid=False, tickmode="array", tickvals=list(d.index),
                     ticktext=list(d["name"]), tickfont=dict(color=ink["secondary"]))
    return fig


def decomposition_figure(wide: pd.DataFrame, actual: pd.Series, colours: dict,
                         mode: str = "light"):
    import plotly.graph_objects as go
    ink = _ink(mode)
    fig = go.Figure()
    for g in wide.columns:
        fig.add_trace(go.Bar(x=wide.index, y=wide[g], name=g,
                             marker=dict(color=colours.get(g, ink["muted"]),
                                         line=dict(color=ink["surface"], width=0.5)),
                             hovertemplate="<b>%{y:,.0f}</b>  " + str(g) + "<extra></extra>"))
    if len(actual):
        fig.add_trace(go.Scatter(x=actual.index, y=actual.values, mode="lines",
                                 name="Actual sales", line=dict(color=ink["primary"], width=2),
                                 hovertemplate="<b>%{y:,.0f}</b>  actual<extra></extra>"))
    _style(fig, mode, 420, y_title="Sales")
    fig.update_layout(barmode="relative", bargap=0.15, hovermode="x unified")
    fig.update_yaxes(tickformat="~s", zeroline=True, zerolinecolor=ink["axis"],
                     zerolinewidth=1)
    return fig


# the diverging scale for correlations: equal steps per arm, the arms at
# matching OKLCH lightness (blue = the sequential ramp; red stepped to match),
# a neutral grey at 0 - and, on the dark surface, 0 receding into it
DIVERGING = {
    "light": [(0.0, "#104281"), (0.25, "#2a78d6"), (0.425, "#9ec5f4"), (0.5, "#f0efec"),
              (0.575, "#f1aea8"), (0.75, "#c74845"), (1.0, "#762221")],
    "dark": [(0.0, "#86b6ef"), (0.25, "#2a78d6"), (0.425, "#184f95"), (0.5, "#383835"),
             (0.575, "#892b2a"), (0.75, "#c74845"), (1.0, "#ea9a93")],
}


def design_label(name) -> str:
    """A design column as a modeller reads it: __fourier__sin_1 -> seasonality
    sin 1, __trend__ -> trend, __intercept__ -> intercept."""
    n = str(name)
    if n.startswith("__fourier__"):
        return "seasonality " + n[len("__fourier__"):].replace("_", " ")
    if n in ("__trend__", "__intercept__"):
        return n.strip("_")
    return n


def _short(label: str, n: int = 28) -> str:
    label = design_label(label)
    return label if len(label) <= n else label[:n - 1] + "…"


def pair_figure(pairs: pd.DataFrame, mode: str = "light", mark: float = PAIR_MARK):
    """The most correlated pairs (pair_bars) as horizontal bars from -1 to 1,
    largest |r| at the top: red = move together, blue = move opposite (the
    diverging pair's mid steps), the value at the bar's end, hairline guides
    at +-`mark`. One row per pair - nothing shown twice."""
    import plotly.graph_objects as go
    ink = _ink(mode)
    d = pairs.reset_index(drop=True)
    red, blue = DIVERGING[mode][5][1], DIVERGING[mode][1][1]
    r = d["correlation"].astype(float)
    fig = go.Figure(go.Bar(
        x=r, y=list(d.index), orientation="h", width=0.62,
        marker=dict(color=[red if v >= 0 else blue for v in r]),
        text=[f"{v:.2f}" for v in r], textposition="outside", cliponaxis=False,
        textfont=dict(color=ink["secondary"], size=11), showlegend=False,
        customdata=np.stack([d["column_a"].map(design_label).astype(str),
                             d["column_b"].map(design_label).astype(str)], axis=-1),
        hovertemplate="<b>r = %{x:.2f}</b><br>%{customdata[0]}<br>%{customdata[1]}"
                      "<extra></extra>"))
    for x in (-mark, mark):
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, yref="paper",
                      line=dict(color=ink["axis"], width=1, dash="dot"))
    fig.add_annotation(x=mark, y=1, yref="paper", yanchor="bottom", showarrow=False,
                       text=f"±{mark:g}", font=dict(color=ink["muted"], size=11))
    _style(fig, mode, max(240, 42 * len(d) + 80), x_title="Correlation (r)", legend=False)
    fig.update_layout(bargap=0.35, barcornerradius=4, hovermode="closest", margin=dict(t=28))
    fig.update_xaxes(range=[-1.18, 1.18], tickvals=[-1, -0.5, 0, 0.5, 1], zeroline=True,
                     zerolinecolor=ink["axis"], zerolinewidth=1)
    # each pair on two lines - the names stay whole and the bars keep the width
    fig.update_yaxes(showgrid=False, autorange="reversed", tickmode="array",
                     tickvals=list(d.index),
                     ticktext=[str(x).replace(" ↔ ", "<br>↔ ") for x in d["pair"]],
                     tickfont=dict(color=ink["secondary"]))
    return fig


MATRIX_NUMBERED = 20    # up to this many columns every cell of the matrix carries its number


def correlation_triangle_figure(columns: list, grid, names: list | None = None,
                                mode: str = "light",
                                every_number_up_to: int = MATRIX_NUMBERED):
    """The design's correlation matrix as its LOWER TRIANGLE - every pair once,
    no diagonal: -1 blue, 0 neutral, +1 red. Every cell carries its number
    (beyond `every_number_up_to` columns, the cells at |r| >= 0.5). Rows and
    columns sit at POSITIONS labelled with unique names (`names`, else
    short_names) - two equal labels on a category axis make Plotly merge them
    and the numbers land in the wrong cells."""
    import plotly.graph_objects as go
    ink = _ink(mode)
    n = len(columns)
    if n < 2:
        return None
    names = list(names) if names is not None else short_names(columns, None, NAME_CHARS)
    z = np.array(grid, dtype=float)[1:, :-1].copy()
    z[np.triu_indices(n - 1, k=1)] = np.nan               # keep j <= i only
    dense = n <= every_number_up_to
    text = [["" if not np.isfinite(v) or (not dense and abs(v) < 0.5) else f"{v:.2f}"
             for v in row] for row in z]
    custom = np.array([[[design_label(columns[i + 1]), design_label(columns[j])]
                        for j in range(n - 1)] for i in range(n - 1)], dtype=object)
    pos = list(range(n - 1))
    fig = go.Figure(go.Heatmap(
        z=z, x=pos, y=pos, zmin=-1, zmax=1, colorscale=DIVERGING[mode], xgap=2, ygap=2,
        text=text, texttemplate="%{text}", textfont=dict(size=10 if dense else 9),
        customdata=custom, hoverongaps=False,
        hovertemplate="<b>r = %{z:.2f}</b><br>%{customdata[0]}<br>%{customdata[1]}"
                      "<extra></extra>",
        colorbar=dict(title=dict(text="r", font=dict(color=ink["secondary"])), thickness=10,
                      tickvals=[-1, -0.5, 0, 0.5, 1], tickfont=dict(color=ink["muted"]),
                      outlinewidth=0)))
    side = max(300, 26 * n + 150)
    _style(fig, mode, side, legend=False)
    fig.update_layout(margin=dict(l=8, r=8, t=12, b=8), plot_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(showgrid=False, tickmode="array", tickvals=pos, ticktext=names[:-1],
                     tickangle=-45, automargin=True,
                     tickfont=dict(color=ink["secondary"], size=10))
    fig.update_yaxes(showgrid=False, autorange="reversed", tickmode="array", tickvals=pos,
                     ticktext=names[1:], automargin=True,
                     tickfont=dict(color=ink["secondary"], size=10))
    return fig


def correlation_heatmap_figure(labels: list, grid, mode: str = "light", mark: float = 0.8):
    """Kept for callers of the old square heatmap: the lower triangle now."""
    return correlation_triangle_figure(labels, grid, None, mode)


VIF_BAND_TEXT = {"ok": "OK (VIF ≤ 5)", "watch": "Watch (5 - 10)", "high": "High (> 10)"}


def vif_figure(bars: pd.DataFrame, mode: str = "light", warn: float = VIF_WARN,
               bad: float = VIF_BAD, cap: float = VIF_CAP):
    """Each variable's VIF (vif_bars) as a horizontal bar, worst at the top,
    on a LINEAR axis that ends at `cap`: a larger VIF fills the bar and its
    label says so ("» 1.0M") - past ~100 its size means nothing more than
    "near-duplicate", and a log axis made 1.0M look like a modest dot. The
    bands are status colours with their names in the legend (OK in the
    recessive neutral, watch amber, high red); hairline guides at 5 and 10."""
    import plotly.graph_objects as go
    ink = _ink(mode)
    d = bars.reset_index(drop=True)
    fill = {"ok": ink["neutral"], "watch": STATUS["warning"], "high": STATUS["critical"]}
    fig = go.Figure()
    for band in ("high", "watch", "ok"):
        part = d[d["band"] == band]
        if part.empty:
            continue
        by = part["explained_by"].fillna("").astype(str) if "explained_by" in part \
            else pd.Series([""] * len(part), index=part.index)
        fig.add_trace(go.Bar(
            x=part["shown"], y=list(part.index), orientation="h", width=0.62,
            name=VIF_BAND_TEXT[band], marker=dict(color=fill[band]),
            text=part["text"], textposition="outside", cliponaxis=False,
            textfont=dict(color=ink["secondary"], size=11),
            customdata=np.stack([part["name"].astype(str), part["text"].astype(str),
                                 by.map(lambda x: x or "-")], axis=-1),
            hovertemplate="<b>VIF %{customdata[1]}</b>  %{customdata[0]}"
                          "<br>explained by: %{customdata[2]}<extra></extra>"))
    for x in (warn, bad):
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, yref="paper",
                      line=dict(color=ink["axis"], width=1, dash="dot"))
    _style(fig, mode, max(240, 26 * len(d) + 110), x_title="VIF", legend=True)
    fig.update_layout(barmode="overlay", bargap=0.3, barcornerradius=4,
                      hovermode="closest", legend_traceorder="reversed")
    ticks = [t for t in (0, 5, 10, 20, 30, 50, 100) if t <= cap]
    fig.update_xaxes(range=[0, cap * 1.22], tickvals=ticks,
                     ticktext=[f"{t:g}" if t < cap else f"{t:g}+" for t in ticks])
    fig.update_yaxes(showgrid=False, autorange="reversed", tickmode="array",
                     tickvals=list(d.index), ticktext=list(d["name"]),
                     tickfont=dict(color=ink["secondary"]))
    return fig


PLOTLY_CONFIG = {"displaylogo": False,
                 # the camera button saves the chart as a PNG at twice the screen
                 # size - sharp enough for a client deck
                 "toImageButtonOptions": {"format": "png", "scale": 2,
                                          "filename": "bridge_chart"},
                 "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d",
                                            "toggleSpikelines", "hoverCompareCartesian",
                                            "hoverClosestCartesian"]}
