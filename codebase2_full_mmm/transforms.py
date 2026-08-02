"""Adstock / Hill transforms - pytensor versions (inside the model graph,
parameters learned by NUTS) and numpy twins (post-fit reporting).

MMM translation:
  adstock alpha  = carryover / decay rate per period (0 = none, 0.9 = long tail)
  half-life      = periods until carried-over effect halves = ln(0.5)/ln(alpha)
  theta          = peak-lag: with "delayed" adstock the effect peaks theta
                   periods after the activity (this is the learned "lag")
  Hill ec        = half-saturation point (EC50) in median-scaled media units
  Hill slope     = curvature; 1.0 = simple diminishing returns (Meridian default)
"""
from __future__ import annotations

import numpy as np

try:  # the numpy twins below work without pytensor (e.g. offline reporting)
    import pytensor.tensor as pt
except ModuleNotFoundError:  # pragma: no cover
    pt = None

EPS = 1e-9


# --------------------------------------------------------------------------
# pytensor (in-model)
# --------------------------------------------------------------------------
def adstock_weights_pt(alpha, theta, L: int, delayed_mask: np.ndarray,
                       lag_mask: np.ndarray):
    """alpha, theta: tensors (C,). Returns normalised weights (L, C)."""
    l = pt.arange(L).astype("float64")[:, None]              # (L,1)
    w_geo = alpha[None, :] ** l
    w_del = alpha[None, :] ** ((l - theta[None, :]) ** 2)
    w = pt.where(pt.constant(delayed_mask.astype(bool))[None, :], w_del, w_geo)
    w = w * pt.constant(lag_mask.astype(float))
    return w / (w.sum(axis=0, keepdims=True) + EPS)


def apply_adstock_pt(Mlag, w):
    """Mlag: numpy constant (G,T,L,C); w tensor (L,C) -> (G,T,C)."""
    return (pt.constant(Mlag) * w[None, None, :, :]).sum(axis=2)


def hill_pt(x, ec, slope):
    """x (G,T,C) >= 0; ec, slope tensors (C,). Saturates into [0,1)."""
    xs = (x + EPS) ** slope[None, None, :]
    return xs / (xs + ec[None, None, :] ** slope[None, None, :])


# --------------------------------------------------------------------------
# numpy twins (exactly the same maths, for post-fit reporting/prediction)
# --------------------------------------------------------------------------
def adstock_weights_np(alpha, theta, L: int, delayed: bool,
                       max_lag: int) -> np.ndarray:
    """alpha, theta: arrays (S,). Returns (S, L) normalised weights."""
    l = np.arange(L, dtype=float)[None, :]
    if delayed:
        w = alpha[:, None] ** ((l - theta[:, None]) ** 2)
    else:
        w = alpha[:, None] ** l
    w[:, max_lag:] = 0.0
    return w / (w.sum(axis=1, keepdims=True) + EPS)


def apply_adstock_np(Mlag_c: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Mlag_c (G,T,L) one channel; w (S,L) -> (S,G,T)."""
    return np.einsum("gtl,sl->sgt", Mlag_c, w)


def hill_np(x: np.ndarray, ec, slope) -> np.ndarray:
    """x (S,G,T); ec, slope arrays (S,) -> (S,G,T)."""
    xs = (x + EPS) ** slope[:, None, None]
    return xs / (xs + (ec ** slope)[:, None, None])


def half_life(alpha) -> np.ndarray:
    """Periods for the geometric carryover to halve."""
    alpha = np.clip(np.asarray(alpha, dtype=float), 1e-12, 1 - 1e-12)
    return np.log(0.5) / np.log(alpha)
