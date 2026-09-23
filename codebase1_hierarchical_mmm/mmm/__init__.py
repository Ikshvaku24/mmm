"""Hierarchical MMM on pre-transformed features.

Entry points live at the codebase root (`run_real_data.py`,
`synthetic_example.py`); everything importable lives here:

    core       config objects, the config.yaml front end, version shims
    data       panel preparation and the pre-model prior builder
    modelling  the PyMC model and the sampler wrapper
    reporting  coefficients, fit, contributions, the benchmark sheet
    checks     convergence, collinearity, assumptions, cross-validation
    pipeline   run_pipeline.run() ties the stages together

Version stamp
-------------
`__version__` is printed at the start of every entry point, and the modules
the pre-model step depends on carry the same stamp (`__codebase__`). A copy of
the folder that was only PARTLY re-uploaded - a new settings.py next to an old
prior_builder.py - then says so at startup instead of silently running old
logic. That is how "the mapping file has contributions but it said none were
found" happened: the prior builder on the cluster predated the mapping file.

When you ship a change, bump `__version__` here and `__codebase__` in each
module listed in `_STAMPED`. The test suite fails if they disagree.
"""

__version__ = "2026.09.24"

# the modules whose logic the pre-model step and the config front end depend
# on; each carries `__codebase__` equal to `__version__`
_STAMPED = ("mmm.core.config", "mmm.core.settings", "mmm.data.mapping",
            "mmm.data.prior_builder", "mmm.checks.warnings_report")


def check_sync() -> list:
    """(module, its stamp) for every stamped module that disagrees with the
    package version. Empty when the copy is consistent."""
    import importlib

    stale = []
    for name in _STAMPED:
        try:
            mod = importlib.import_module(name)
        except Exception as e:  # noqa: BLE001 - report, do not crash here
            stale.append((name, f"import failed: {e}"))
            continue
        stamp = getattr(mod, "__codebase__", "none - older than 2026.09.24")
        if stamp != __version__:
            stale.append((name, stamp))
    return stale


def announce() -> None:
    """Print the version line, and a loud block if the copy is out of sync."""
    print(f"[mmm] codebase {__version__}")
    stale = check_sync()
    if stale:
        lines = "\n".join(f"    {m}: {s}" for m, s in stale)
        print("[mmm] !!! OUT OF SYNC - these modules are not from "
              f"{__version__}:\n{lines}\n"
              "[mmm] !!! Re-upload the WHOLE mmm/ folder. Mixed versions run "
              "old logic silently.")
