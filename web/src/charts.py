"""The charts a finished run shows: fit, contributions, decomposition and
prior vs posterior - built from the run's own output files.

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
twin under every chart.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

ALL = "All regions"
PORTFOLIO = "__portfolio__"
BASELINE_CORE = "Baseline core"
OTHER = "Other"

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


# --------------------------------------------------------------------------- #
# data - fit (04_fit/fit_metrics.csv, 04_fit/actual_vs_predicted.csv)
# --------------------------------------------------------------------------- #
def fit_tiles(fit: pd.DataFrame) -> list:
    """[(label, value text, help)] - the headline fit numbers (the __all__ rows)."""
    if fit is None or fit.empty or not {"region", "dataset"} <= set(fit.columns):
        return []
    rows = fit[fit["region"].astype(str) == "__all__"]
    by = {str(r["dataset"]): r for _, r in rows.iterrows()}
    out = []

    def add(label, dataset, col, fmt, help_text):
        row = by.get(dataset)
        v = _num(row.get(col)) if row is not None else None
        if v is not None:
            out.append((label, fmt(v), help_text))

    add("R² within region · training", "train", "r2_within_region", lambda v: f"{v:.2f}",
        "R² against each region's own mean - the honest R² (the pooled one is "
        "inflated by the gaps in level between regions).")
    add("R² within region · holdout", "test", "r2_within_region", lambda v: f"{v:.2f}",
        "The same on the held-out weeks. Below 0 = worse than each region's own "
        "average.")
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
            .groupby(d["pillar"].map(_text).replace("", OTHER)).sum())
    order = [p for p in size.sort_values(ascending=False, kind="stable").index if p != OTHER]
    return order + ([OTHER] if OTHER in size.index else [])


def colour_map(pillars: list, mode: str = "light") -> dict:
    """{pillar: colour}: the first MAX_COLOURED pillars take the categorical
    slots in order; the rest - and "Other" - share the muted grey; the
    baseline core is the recessive neutral."""
    slots, ink = SERIES[mode], INK[mode]
    named = [p for p in pillars if p != OTHER]
    out = {BASELINE_CORE: ink["neutral"], OTHER: ink["muted"]}
    for i, p in enumerate(named):
        out[p] = slots[i] if i < MAX_COLOURED else ink["muted"]
    return out


def shown_groups(pillars: list) -> list:
    """The decomposition's series: the coloured pillars, then "Other" when
    anything folds into it."""
    named = [p for p in pillars if p != OTHER]
    keep = named[:MAX_COLOURED]
    return keep + ([OTHER] if len(named) > MAX_COLOURED or OTHER in pillars else [])


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
        "pillar": body["pillar"].map(_text).replace("", OTHER).values if "pillar" in body
        else OTHER,
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
                      comp["pillar"].map(_text).replace("", OTHER))
    comp["series"] = [s if (s == BASELINE_CORE or s in groups) else OTHER for s in series]
    wide = comp.pivot_table(index="date", columns="series", values="volume",
                            aggfunc="sum").fillna(0.0)
    order = [BASELINE_CORE] + groups + ([OTHER] if OTHER not in groups else [])
    wide = wide[[g for g in order if g in wide.columns]]
    return wide, actual


# --------------------------------------------------------------------------- #
# data - prior vs posterior (02_convergence/prior_posterior_contraction.csv)
# --------------------------------------------------------------------------- #
def contraction_points(contr: pd.DataFrame) -> pd.DataFrame:
    """One point per feature (per region under independent pooling): the
    parameter the delta arithmetic uses (`use_for_delta`), with its
    contraction and shift."""
    if contr is None or contr.empty or "parameter" not in contr:
        return pd.DataFrame()
    d = contr[contr["use_for_delta"].map(_truthy)] if "use_for_delta" in contr else contr
    if d.empty:
        return pd.DataFrame()
    blank = pd.Series("", index=d.index)
    feature = (d["feature"] if "feature" in d else d.get("name", blank)).map(_text)
    region = d.get("region", blank).map(_text)
    out = pd.DataFrame({
        "key": d["parameter"].astype(str).values,
        "label": [f + (f" · {r}" if r else "") for f, r in zip(feature, region)],
        "feature": feature.values, "region": region.values,
        "variable": d.get("variable", blank).map(_text).values,
        "scale": d.get("scale", blank).map(_text).values,
        "prior_mean": pd.to_numeric(d["prior_mean"], errors="coerce").values,
        "prior_sd": pd.to_numeric(d["prior_sd"], errors="coerce").values,
        "posterior_mean": pd.to_numeric(d["posterior_mean"], errors="coerce").values,
        "posterior_sd": pd.to_numeric(d["posterior_sd"], errors="coerce").values,
        "contraction": pd.to_numeric(d["contraction"], errors="coerce").values,
        "shift": pd.to_numeric(d["mean_shift_in_prior_sd"], errors="coerce").values,
    })
    out = out.dropna(subset=["contraction", "shift"])
    return out.sort_values("label", kind="stable").reset_index(drop=True)


def data_curve(point):
    """(mean, sd) of what the data alone says - recovered from the prior and
    the posterior (both ~Normal), as codebase 1's own three-curve charts do:
        1/sd_data^2 = 1/sd_post^2 - 1/sd_prior^2
        mu_data     = sd_data^2 * (mu_post/sd_post^2 - mu_prior/sd_prior^2)
    None when the posterior is not narrower than the prior (unidentified)."""
    pm, ps = _num(point.get("prior_mean")), _num(point.get("prior_sd"))
    qm, qs = _num(point.get("posterior_mean")), _num(point.get("posterior_sd"))
    if None in (pm, ps, qm, qs) or ps <= 0 or qs <= 0:
        return None
    precision = 1.0 / qs ** 2 - 1.0 / ps ** 2
    if precision <= 1e-12:
        return None
    var = 1.0 / precision
    return var * (qm / qs ** 2 - pm / ps ** 2), math.sqrt(var)


def density_curves(point, n: int = 400):
    """{"x", "prior", "posterior", "data"}: Normal curves from the means and
    sds ("data" is None when it cannot be recovered), or None when an sd is
    missing or not positive."""
    pm, ps = _num(point.get("prior_mean")), _num(point.get("prior_sd"))
    qm, qs = _num(point.get("posterior_mean")), _num(point.get("posterior_sd"))
    if None in (pm, ps, qm, qs) or ps <= 0 or qs <= 0:
        return None
    lo, hi = min(pm - 4 * ps, qm - 4 * qs), max(pm + 4 * ps, qm + 4 * qs)
    data = data_curve(point)
    if data is not None and data[1] <= 3 * ps:          # an informative data curve
        lo, hi = min(lo, data[0] - 4 * data[1]), max(hi, data[0] + 4 * data[1])
    x = np.linspace(lo, hi, n)

    def pdf(m, s):
        return np.exp(-0.5 * ((x - m) / s) ** 2) / (s * math.sqrt(2 * math.pi))

    return {"x": x, "prior": pdf(pm, ps), "posterior": pdf(qm, qs),
            "data": pdf(*data) if data is not None else None}


def contraction_reading(point) -> str:
    """What the two numbers say, in one or two sentences."""
    c, s = _num(point.get("contraction")), _num(point.get("shift"))
    if c is None or s is None:
        return ""
    learned = ("the data determined it" if c >= 0.7 else
               "the data sharpened it" if c >= 0.2 else
               "the posterior is mostly your prior - report it as an assumption, "
               "not a finding" if c >= 0 else
               "the posterior came out WIDER than the prior - the data is fighting "
               "the model")
    moved = ("it sits far from the prior mean you gave (|shift| > 2) - check the "
             "units or the prior" if abs(s) > 2 else "it stayed close to the prior mean")
    text = f"Contraction {c:.2f}, shift {s:+.2f} prior sd: {learned}; {moved}."
    if str(point.get("scale")) == "log":
        sign = -1.0 if str(point.get("variable", "")).endswith("neg") else 1.0
        pm, qm = _num(point.get("prior_mean")), _num(point.get("posterior_mean"))
        if pm is not None and qm is not None:
            text += (f" As a coefficient (median, scaled units): prior {sign * math.exp(pm):.4g}"
                     f" → posterior {sign * math.exp(qm):.4g}.")
    return text


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


def flagged(points: pd.DataFrame, limit: int = 8) -> pd.Series:
    """The points worth a label: contraction < 0.2 or |shift| > 2, worst
    first, at most `limit` (the rest keep their hover and the table)."""
    c, s = points["contraction"], points["shift"].abs()
    bad = (c < 0.2) | (s > 2)
    worst = (s.where(s > 2, 0) + (0.2 - c).clip(lower=0) * 10)[bad]
    keep = worst.sort_values(ascending=False, kind="stable").index[:limit]
    return points.index.isin(keep)


def contraction_figure(points: pd.DataFrame, selected: str | None, mode: str = "light"):
    """Every feature as a point: contraction (x) against shift (y). The
    guides mark the two warnings - contraction < 0.2 and |shift| > 2 - and
    the points past them are labelled."""
    import plotly.graph_objects as go
    ink, slots = INK[mode], SERIES[mode]
    x = points["contraction"].clip(lower=-1.0)
    fig = go.Figure()
    fig.add_vline(x=0.2, line_color=ink["axis"], line_width=1,
                  annotation_text="0.2", annotation_position="top",
                  annotation_font=dict(color=ink["muted"], size=11))
    for y in (2.0, -2.0):
        fig.add_hline(y=y, line_color=ink["axis"], line_width=1,
                      annotation_text=f"{y:+.0f} sd", annotation_position="right",
                      annotation_font=dict(color=ink["muted"], size=11))
    hover = ("<b>%{customdata[1]}</b><br>contraction %{customdata[2]:.2f}"
             "<br>moved %{y:+.2f} prior sd<extra></extra>")
    label = np.where(flagged(points), points["label"], "")
    for name, mask, colour, size in (
            ("Variables", points["key"] != (selected or ""), slots[0], 11),
            ("Selected", points["key"] == (selected or ""), slots[1], 15)):
        if not mask.any():
            continue
        text = np.where(mask & (points["key"] == (selected or "")), points["label"], label)[mask]
        fig.add_trace(go.Scatter(
            x=x[mask], y=points["shift"][mask], mode="markers+text", name=name,
            text=text, textposition="top center",
            textfont=dict(color=ink["secondary"], size=11),
            marker=dict(size=size, color=colour, line=dict(color=ink["surface"], width=2)),
            customdata=np.stack([points["key"][mask], points["label"][mask],
                                 points["contraction"][mask]], axis=-1),
            hovertemplate=hover, selected=dict(marker=dict(opacity=1)),
            unselected=dict(marker=dict(opacity=1))))
    _style(fig, mode, 340, x_title="Contraction - how much the data sharpened the prior",
           y_title="Shift from the prior mean, prior sd", legend=False)
    fig.update_layout(clickmode="event+select", dragmode=False, hovermode="closest",
                      hoverdistance=24, margin=dict(r=48))
    lo = float(min(-0.1, x.min() - 0.08))
    fig.update_xaxes(range=[lo, 1.08])
    ymax = float(max(3.0, points["shift"].abs().max() * 1.15))
    fig.update_yaxes(range=[-ymax, ymax])
    return fig


def prior_posterior_figure(point, mode: str = "light"):
    import plotly.graph_objects as go
    curves = density_curves(point)
    if curves is None:
        return None
    ink, slots = INK[mode], SERIES[mode]
    fig = go.Figure()
    series = [("Prior", curves["prior"], ink["muted"], 0.10),
              ("Data", curves["data"], slots[1], 0.0),
              ("Posterior", curves["posterior"], slots[0], 0.12)]
    for name, y, colour, wash in series:
        if y is None:
            continue
        fig.add_trace(go.Scatter(
            x=curves["x"], y=y, mode="lines", name=name,
            line=dict(color=colour, width=2),
            fill="tozeroy" if wash else None, fillcolor=_alpha(colour, wash) if wash else None,
            hovertemplate="%{x:.3f}<extra>" + name.lower() + "</extra>"))
    log = str(point.get("scale") or "") == "log"
    _style(fig, mode, 300, x_title="log coefficient" if log else "coefficient (scaled units)",
           y_title="density")
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(showticklabels=False, rangemode="tozero")
    return fig


PLOTLY_CONFIG = {"displaylogo": False,
                 "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d",
                                            "toggleSpikelines", "hoverCompareCartesian",
                                            "hoverClosestCartesian"]}
