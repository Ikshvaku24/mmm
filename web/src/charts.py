"""The charts a finished run shows: fit, contributions, decomposition and
collinearity (the design's correlation heatmap and its VIFs) - built from the
run's own output files.

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


def use_theme(font=None, surface=None):
    """The lead company's font for every chart, and its page colours behind
    the heatmap's cells ({"light": ..., "dark": ...}) - from src/brand.py.
    The data colours stay the validated ones above."""
    global FONT
    if font:
        FONT = font
    for kind, colour in (surface or {}).items():
        if kind in INK and colour:
            INK[kind]["surface"] = colour


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
    slots, ink = SERIES[mode], INK[mode]
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


def contribution_tree(bars: pd.DataFrame, open_pillars=()) -> pd.DataFrame:
    """The contribution chart as a tree, top to bottom: each pillar (largest
    first, "Unassigned" last) followed - when it is open - by its variables,
    largest first. Columns: label (unique), pillar, feature, level (0 =
    pillar, 1 = variable), pct, volume, n."""
    rows = []
    opened = set(open_pillars or ())
    for p in pillar_rows(bars).itertuples(index=False):
        rows.append({"label": p.pillar, "pillar": p.pillar, "feature": "", "level": 0,
                     "pct": float(p.pct), "volume": float(p.volume), "n": int(p.n)})
        if p.pillar in opened:
            members = bars[bars["pillar"] == p.pillar].sort_values(
                "pct", ascending=False, kind="stable")
            for m in members.itertuples(index=False):
                rows.append({"label": f"↳ {m.feature}", "pillar": p.pillar,
                             "feature": m.feature, "level": 1, "pct": float(m.pct),
                             "volume": float(m.volume) if m.volume == m.volume else 0.0,
                             "n": 1})
    return pd.DataFrame(rows, columns=["label", "pillar", "feature", "level", "pct",
                                       "volume", "n"])


# --------------------------------------------------------------------------- #
# data - decomposition (05_contributions/contribution_timeseries.csv)
# --------------------------------------------------------------------------- #
def decomposition_frame(ts: pd.DataFrame, pillars: list, region: str = ALL):
    """(wide, actual): weekly volume of the baseline core and of each shown
    pillar group (columns: Baseline core first, then `shown_groups` order),
    and the actual sales line - for one region or summed over all."""
    d = ts.copy()
    d["date"] = pd.to_datetime(d["date"])
    if region != ALL:
        d = d[d["region"].astype(str) == region]
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


def collinearity_tiles(summary: pd.DataFrame, region: str) -> list:
    """[(label, value text, help)] - one region's collinearity in four numbers."""
    if summary is None or summary.empty:
        return []
    rows = summary[summary["region"].astype(str) == str(region)]
    if rows.empty:
        return []
    r = rows.iloc[0]
    out = []
    cond = _num(r.get("condition_number"))
    if cond is not None or str(r.get("condition_number")) == "inf":
        out.append(("Condition number", "∞" if cond is None or math.isinf(cond)
                    else f"{cond:,.0f}",
                    "How close the whole design is to singular. Above 10 warns, above "
                    "30 is severe; ∞ = some columns are exact combinations of others."))
    mx = _num(r.get("max_vif"))
    out.append(("Largest VIF", "n/a" if mx is None else f"{mx:,.1f}",
                "The worst variable's variance inflation: 5 warns, 10 is severe. n/a = "
                "not computable (more design columns than training periods)."))
    if _text(r.get("worst_column")):
        out.append(("Worst variable", _text(r.get("worst_column")),
                    "Explained by: " + (_text(r.get("worst_explained_by")) or "-")))
    out.append(("Verdict", _text(r.get("verdict")) or "-",
                "ok / moderate / severe - codebase 1's reading of the condition number "
                "and the VIFs together."))
    return out


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
    ink = INK[mode]
    fig.update_layout(
        template="none", height=height,
        margin=dict(l=8, r=16, t=40 if legend else 12, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=ink["secondary"], size=12),
        hoverlabel=dict(font=dict(family=FONT, size=12)),
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
    ink, blue = INK[mode], SERIES[mode][0]
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
    ink = INK[mode]
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
    its variables (when open) under it in a lighter shade of the same colour.
    The y labels name every bar, so there is no legend."""
    import plotly.graph_objects as go
    ink = INK[mode]
    fig = go.Figure()
    order = list(tree["label"])
    for pillar in dict.fromkeys(tree["pillar"]):
        part = tree[tree["pillar"] == pillar]
        base = colours.get(pillar, SERIES[mode][0])
        fills = [base if lv == 0 else _alpha(base, 0.5) for lv in part["level"]]
        fig.add_trace(go.Bar(
            x=part["pct"], y=part["label"], orientation="h", name=pillar,
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
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=order[::-1],
                     tickfont=dict(color=ink["secondary"]))
    return fig


def decomposition_figure(wide: pd.DataFrame, actual: pd.Series, colours: dict,
                         mode: str = "light"):
    import plotly.graph_objects as go
    ink = INK[mode]
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


def correlation_heatmap_figure(labels: list, grid, mode: str = "light", mark: float = 0.8):
    """The design's correlation matrix as a heatmap: -1 blue, 0 neutral, +1
    red. Only the cells at |r| >= `mark` carry their number (the rest have a
    hover), and the diagonal - a column with itself - is left blank."""
    import plotly.graph_objects as go
    ink = INK[mode]
    z = np.array(grid, dtype=float)
    n = len(labels)
    if n:
        np.fill_diagonal(z, np.nan)
    short = [_short(x) for x in labels]
    text = [["" if (i == j or not np.isfinite(z[i, j]) or abs(z[i, j]) < mark
                    or n > 30) else f"{z[i, j]:.2f}" for j in range(n)] for i in range(n)]
    fig = go.Figure(go.Heatmap(
        z=z, x=short, y=short, zmin=-1, zmax=1, colorscale=DIVERGING[mode],
        xgap=2, ygap=2, text=text, texttemplate="%{text}",
        textfont=dict(size=10),
        customdata=np.array([[[labels[i], labels[j]] for j in range(n)] for i in range(n)],
                            dtype=object) if n else None,
        hovertemplate="<b>%{z:.2f}</b>  %{customdata[0]} × %{customdata[1]}<extra></extra>",
        colorbar=dict(title=dict(text="r", font=dict(color=ink["secondary"])), thickness=10,
                      tickvals=[-1, -0.5, 0, 0.5, 1], tickfont=dict(color=ink["muted"]),
                      outlinewidth=0)))
    side = max(320, 22 * n + 140)
    _style(fig, mode, side, legend=False)
    fig.update_layout(margin=dict(l=8, r=8, t=12, b=8), plot_bgcolor=ink["surface"])
    fig.update_xaxes(showgrid=False, tickangle=-60, side="bottom", automargin=True,
                     tickfont=dict(color=ink["secondary"], size=10))
    fig.update_yaxes(showgrid=False, autorange="reversed", automargin=True,
                     tickfont=dict(color=ink["secondary"], size=10))
    return fig


def vif_figure(points: pd.DataFrame, mode: str = "light", warn: float = VIF_WARN,
               bad: float = VIF_BAD):
    """Each variable's VIF as a dot on a log axis (VIFs run from 1 to the
    thousands - a bar from an arbitrary log baseline would mislead), worst at
    the top, with the 5 and 10 guides. One series, so one colour and no
    legend; the value is in the hover and the table."""
    import plotly.graph_objects as go
    ink, blue = INK[mode], SERIES[mode][0]
    d = points.dropna(subset=["vif"])
    d = d[d["vif"] > 0]
    fig = go.Figure()
    # on a log axis a shape is placed in data units but an annotation in log10
    # units - add_vline's own label would land at 10^10, so they go separately
    for x, label in ((warn, f"{warn:g} warn"), (bad, f"{bad:g} severe")):
        fig.add_shape(type="line", x0=x, x1=x, xref="x", y0=0, y1=1, yref="paper",
                      line=dict(color=ink["axis"], width=1, dash="dot"))
        fig.add_annotation(x=math.log10(x), y=1, yref="paper", yanchor="bottom",
                           text=label, showarrow=False,
                           font=dict(color=ink["muted"], size=11))
    fig.add_trace(go.Scatter(
        x=d["vif"], y=[_short(c, 34) for c in d["column"]], mode="markers",
        marker=dict(size=10, color=blue, line=dict(color=ink["surface"], width=2)),
        customdata=np.stack([d["column"].astype(str),
                             d.get("duplicates", pd.Series([""] * len(d))).fillna("").astype(str),
                             d.get("explained_by", pd.Series([""] * len(d))).fillna("").astype(str)],
                            axis=-1) if len(d) else None,
        hovertemplate="<b>VIF %{x:,.1f}</b>  %{customdata[0]}<br>duplicates: "
                      "%{customdata[1]}<br>explained by: %{customdata[2]}<extra></extra>"))
    _style(fig, mode, max(220, 24 * len(d) + 80), x_title="VIF (log scale)", legend=False)
    fig.update_layout(hovermode="closest", margin=dict(t=28))
    top = max(float(d["vif"].max()) if len(d) else bad, bad) * 1.8
    ticks = [m * 10 ** e for e in range(0, 7) for m in (1, 2, 5) if m * 10 ** e <= top]
    fig.update_xaxes(type="log", range=[math.log10(0.8), math.log10(top)], tickvals=ticks,
                     ticktext=[f"{t:,.0f}" for t in ticks])
    fig.update_yaxes(showgrid=False, autorange="reversed",
                     tickfont=dict(color=ink["secondary"]))
    return fig


PLOTLY_CONFIG = {"displaylogo": False,
                 "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d",
                                            "toggleSpikelines", "hoverCompareCartesian",
                                            "hoverClosestCartesian"]}
