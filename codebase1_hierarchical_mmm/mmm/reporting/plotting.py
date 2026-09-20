"""Shared figure helpers: one place that owns size, resolution and labelling.

Every chart this codebase writes goes through `save_fig`, and every axis is
labelled through `annotate` so a PNG can be read on its own - without the CSV
next to it - by someone who was not in the room. Three rules:

  1. BOTH axes always carry a name AND the unit it is measured in. "contribution"
     is not a label; "contribution (KPI units, summed over window)" is. The unit
     matters more than the name here because almost everything in an MMM exists
     on three different axes (raw, scaled, original KPI units) and a chart that
     does not say which one it is on is worse than no chart.
  2. Anything with a colour or a line style carries a legend.
  3. Figures are sized from `FIG["scale"]`, so one config change makes every
     output bigger without touching a single call site.

`FIG` is module-level mutable state deliberately: `save_fig` is called from
data_prep, diagnostics, outputs and cross_validation, most of them without an
OutputConfig in scope. `run_pipeline` sets it once from `OutputConfig` at the
top of a run; nothing else writes to it.
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# dpi: 160 renders legibly when a PNG is pasted into a deck at half width.
# scale: multiplies every figsize, so charts stay readable when projected.
FIG = {"dpi": 160, "scale": 1.4}


def set_figure_defaults(dpi: int | None = None, scale: float | None = None) -> None:
    """Set output size/resolution for the whole run. Called once by run_pipeline."""
    if dpi is not None:
        FIG["dpi"] = int(dpi)
    if scale is not None:
        FIG["scale"] = float(scale)


def figsize(w: float, h: float) -> tuple[float, float]:
    """Scale a base (width, height) in inches by FIG['scale']."""
    s = float(FIG.get("scale", 1.0) or 1.0)
    return (w * s, h * s)


def annotate(ax, xlabel: str, ylabel: str, title: str | None = None,
             legend: bool = False, legend_fontsize: int = 8) -> None:
    """Label an axis. `xlabel`/`ylabel` must name the quantity AND its unit."""
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    if title:
        ax.set_title(title, fontsize=10)
    if legend:
        ax.legend(fontsize=legend_fontsize)
    ax.tick_params(labelsize=8)


def units_note(fig, text: str) -> None:
    """Footnote under a figure saying what the numbers are measured in.

    Used where the unit needs a sentence rather than an axis label - e.g. "all
    coefficients are on the scaled axis; multiply by dv_scale/feature_scale for
    KPI units".
    """
    fig.text(0.005, 0.005, text, fontsize=7, va="bottom", ha="left",
             color="#444444")


def save_fig(fig, path: str, dpi: int | None = None) -> None:
    """Robust savefig: ensure the directory exists, retry once (the Databricks
    /Workspace filesystem can transiently fail rapid PNG writes), and on final
    failure warn-and-continue - a plot must never kill a finished fit.

    bbox_inches="tight" keeps rotated tick labels and the units footnote inside
    the image; without it long feature names get clipped off the edge.
    """
    import time as _time
    dpi = FIG["dpi"] if dpi is None else dpi
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for attempt in (1, 2):
        try:
            fig.savefig(path, dpi=dpi, bbox_inches="tight")
            break
        except TypeError:
            # a stubbed savefig in the test suite may not accept the kwargs
            try:
                fig.savefig(path)
            except Exception:  # noqa: BLE001
                pass
            break
        except OSError as e:
            if attempt == 2:
                print(f"[plotting] WARNING: could not save {path}: {e}")
            else:
                _time.sleep(0.5)
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    plt.close(fig)
