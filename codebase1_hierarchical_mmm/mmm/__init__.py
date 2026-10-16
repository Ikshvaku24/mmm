"""Hierarchical MMM on pre-transformed features.

Entry points live at the codebase root (`run_real_data.py`,
`synthetic_example.py`, `demo.ipynb`); everything importable lives here:

    core       config objects, the config.yaml front end, version shims
    data       panel preparation and the pre-model prior builder
    modelling  the PyMC model and the sampler wrapper
    reporting  coefficients, fit, contributions, the benchmark sheet
    checks     convergence, collinearity, assumptions, cross-validation
    pipeline   run_pipeline.run() ties the stages together
    release    `python -m mmm.release YYYY.MM.DD.N` - stamps a release

Version stamp - the guard against a partial upload
---------------------------------------------------
EVERY module file in this package carries `__codebase__`, equal to
`__version__` here. The entry points (`run_from_yaml`, `build_priors`, and
through `run_from_yaml` the web app's job) call `announce()` first. It reads
the stamps from the FILES - nothing is imported, so it works without PyMC, in
the web app too - and STOPS with `OutOfSyncError` when any file is from
another release or carries no stamp.

That is what a partial upload looks like: a Databricks copy where some files
were re-uploaded and others were not. Until 2026.10.14.2 only 7 modules were
stamped and a mismatch only printed, so a stale `fit.py`, `diagnostics.py` or
`reconciliation.py` ran old logic silently or failed half-way with a confusing
error. (The original incident, 2026-09-24: "the mapping file has contributions
but it said none were found" - the prior builder on the cluster predated the
mapping file.)

Set the environment variable `MMM_ALLOW_OUT_OF_SYNC=1` to run a mixed copy
anyway; the list is still printed.

To ship a change: `python -m mmm.release YYYY.MM.DD.N` from the codebase
folder. It rewrites `__version__` and every `__codebase__`, and stamps a new
module that has none. `test_v18` fails if any file disagrees.
"""
from __future__ import annotations

import os as _os
import re as _re

__version__ = "2026.10.16.2"

ALLOW_ENV = "MMM_ALLOW_OUT_OF_SYNC"

_PKG_DIR = _os.path.dirname(_os.path.abspath(__file__))
_STAMP_RE = _re.compile(r'^__codebase__\s*=\s*["\']([^"\']+)["\']', _re.M)
_VERSION_RE = _re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', _re.M)
_STAMP_CACHE: dict = {}


class OutOfSyncError(RuntimeError):
    """The mmm/ folder mixes files from different releases - a partial upload."""


def module_files(pkg_dir: str | None = None) -> list:
    """(dotted module name, path) for every .py file of the package except its
    root `__init__.py` (which holds `__version__`), sorted by name."""
    pkg_dir = _os.path.abspath(pkg_dir or _PKG_DIR)
    base = _os.path.dirname(pkg_dir)
    root_init = _os.path.join(pkg_dir, "__init__.py")
    out = []
    for root, dirs, files in _os.walk(pkg_dir):
        dirs[:] = sorted(d for d in dirs
                         if d != "__pycache__" and not d.startswith("."))
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = _os.path.join(root, f)
            if _os.path.abspath(path) == root_init:
                continue
            name = _os.path.relpath(path, base)[:-3].replace(_os.sep, ".")
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            out.append((name, path))
    return sorted(out)


def file_stamp(path: str) -> str | None:
    """The `__codebase__` string of a module FILE, read as text - never imported.

    None when the file has no stamp (it predates 2026.10.14.2, or it is a new
    module nobody stamped)."""
    try:
        st = _os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        if key in _STAMP_CACHE:
            return _STAMP_CACHE[key]
        with open(path, encoding="utf-8") as fh:
            m = _STAMP_RE.search(fh.read())
    except (OSError, UnicodeDecodeError) as e:
        return f"unreadable ({e.__class__.__name__})"
    stamp = m.group(1) if m else None
    _STAMP_CACHE[key] = stamp
    return stamp


def package_version(pkg_dir: str | None = None) -> str:
    """`__version__` of this package, or of another copy's `__init__.py`."""
    if pkg_dir is None or _os.path.abspath(pkg_dir) == _PKG_DIR:
        return __version__
    with open(_os.path.join(pkg_dir, "__init__.py"), encoding="utf-8") as fh:
        m = _VERSION_RE.search(fh.read())
    return m.group(1) if m else "none"


def check_sync(pkg_dir: str | None = None, version: str | None = None) -> list:
    """(module, its stamp) for every module FILE whose `__codebase__` is not the
    package version - from another release, or missing. Empty when the copy is
    consistent. Reads the files; imports nothing.

    `version` overrides the version compared against (the release tool checks
    the files it just wrote, before the running `__version__` changes)."""
    want = version or package_version(pkg_dir)
    stale = []
    for name, path in module_files(pkg_dir):
        stamp = file_stamp(path)
        if stamp != want:
            stale.append((name, stamp or "no stamp - older than 2026.10.14.2"))
    return stale


# every module the check covers - kept as a name because older code and the
# tests read it
_STAMPED = tuple(name for name, _ in module_files())


def announce(strict: bool = True, quiet: bool = False) -> None:
    """Print the version line (unless `quiet`). On a mixed copy, print which
    files are stale and - when `strict` - stop with `OutOfSyncError`, unless
    the environment variable MMM_ALLOW_OUT_OF_SYNC is set. `quiet=True` is the
    silent check `run()` makes, so a direct Python call is guarded too."""
    if not quiet:
        print(f"[mmm] codebase {__version__}")
    stale = check_sync()
    if not stale:
        return
    lines = "\n".join(f"    {m}: {s}" for m, s in stale)
    print(f"[mmm] !!! OUT OF SYNC - these files are not from {__version__}:\n"
          f"{lines}\n"
          "[mmm] !!! Re-upload the WHOLE mmm/ folder. A mixed copy runs old "
          "logic silently, or fails half-way.")
    if not strict:
        return
    if _os.environ.get(ALLOW_ENV, "").strip().lower() in ("1", "true", "yes"):
        print(f"[mmm] !!! {ALLOW_ENV} is set - running ANYWAY on a mixed copy")
        return
    names = ", ".join(m for m, _ in stale[:8]) + (" ..." if len(stale) > 8 else "")
    raise OutOfSyncError(
        f"codebase {__version__}: {len(stale)} file(s) in mmm/ are from another "
        f"release or carry no version stamp ({names}). That is a partial "
        "upload - re-upload the WHOLE mmm/ folder. To run a mixed copy anyway, "
        f"set the environment variable {ALLOW_ENV}=1.")
