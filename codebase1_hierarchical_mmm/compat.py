"""Compatibility helpers: ArviZ InferenceData vs xarray DataTree.

Newer PyMC/ArviZ stacks can return sampling results as an xarray DataTree
instead of the classic InferenceData. These helpers make group access,
group merging and saving work identically on both.
"""
from __future__ import annotations


def has_group(idata, group_name: str) -> bool:
    """True when the inference object contains the requested group."""
    try:
        return group_name in idata
    except TypeError:
        return hasattr(idata, group_name)


def get_group(idata, group_name: str):
    """Return the group's xarray.Dataset from InferenceData or DataTree."""
    if not has_group(idata, group_name):
        raise KeyError(f"inference result has no group {group_name!r}")
    try:
        group = idata[group_name]
    except (KeyError, TypeError):
        group = getattr(idata, group_name)
    # a DataTree node exposes its Dataset via `.dataset`
    if hasattr(group, "dataset"):
        return group.dataset
    return group


def extend_idata(idata, other):
    """Merge the groups of `other` (e.g. prior samples) into `idata`."""
    if hasattr(idata, "extend"):
        idata.extend(other)
    else:  # DataTree
        idata.update(other)
    return idata


def save_idata(idata, path: str) -> None:
    """Write to netCDF; works for InferenceData and DataTree."""
    idata.to_netcdf(path)
