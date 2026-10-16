"""Stamp a release: `python -m mmm.release YYYY.MM.DD.N` from the codebase folder.

Sets `__version__` in `mmm/__init__.py` and `__codebase__` in EVERY other
module of the package, adding the line to a module that has none (a new
file). After it, `mmm.check_sync()` reads the same string from every file, and
an upload that leaves any file behind is caught at the next run
(`mmm.OutOfSyncError`).

    python -m mmm.release 2026.10.15.1     stamp every module
    python -m mmm.release --check          list the files that disagree

The web app's `MIN_CODEBASE` (`../web/src/codebase.py`) is a separate decision:
raise it only when the app needs something new from this release.
"""
from __future__ import annotations

__codebase__ = "2026.10.16.2"   # must equal mmm.__version__ (python -m mmm.release)

import ast
import os
import re
import sys

VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.\d+$")
_STAMP_LINE_RE = re.compile(r"^__codebase__\s*=.*$", re.M)
_VERSION_LINE_RE = re.compile(r"^__version__\s*=.*$", re.M)
STAMP_LINE = ('__codebase__ = "{v}"   # must equal mmm.__version__ '
              '(python -m mmm.release)')


def _insert_after(src: str) -> int:
    """How many lines come before a new stamp: the module docstring and any
    `from __future__` imports, which must stay first."""
    body = ast.parse(src).body
    end, i = 0, 0
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)):
        end, i = body[0].end_lineno, 1
    while (i < len(body) and isinstance(body[i], ast.ImportFrom)
           and body[i].module == "__future__"):
        end, i = body[i].end_lineno, i + 1
    return end


def stamp_source(src: str, version: str) -> str:
    """`src` with its `__codebase__` line set to `version` (added if missing)."""
    line = STAMP_LINE.format(v=version)
    if _STAMP_LINE_RE.search(src):
        return _STAMP_LINE_RE.sub(line, src, count=1)
    lines = src.splitlines(keepends=True)
    at = _insert_after(src)
    if at == 0:
        return line + "\n\n" + src
    head = "".join(lines[:at])
    if not head.endswith("\n"):
        head += "\n"
    return head + "\n" + line + "\n" + "".join(lines[at:])


def bump(version: str, pkg_dir: str | None = None) -> list:
    """Stamp every module of the package at `pkg_dir` (default: this one) with
    `version`. Returns the files it changed. Raises if the result is not in
    sync - a file that could not be stamped must not ship."""
    if not VERSION_RE.match(str(version)):
        raise ValueError(f"version must look like YYYY.MM.DD.N, got {version!r}")
    import mmm

    pkg_dir = os.path.abspath(pkg_dir or os.path.dirname(os.path.abspath(__file__)))
    changed = []
    init = os.path.join(pkg_dir, "__init__.py")
    with open(init, encoding="utf-8") as fh:
        src = fh.read()
    if not _VERSION_LINE_RE.search(src):
        raise ValueError(f"{init} has no __version__ line")
    new = _VERSION_LINE_RE.sub(f'__version__ = "{version}"', src, count=1)
    if new != src:
        with open(init, "w", encoding="utf-8") as fh:
            fh.write(new)
        changed.append(init)
    for _name, path in mmm.module_files(pkg_dir):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        new = stamp_source(src, version)
        if new != src:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            changed.append(path)
    mmm._STAMP_CACHE.clear()
    stale = mmm.check_sync(pkg_dir, version=version)
    if stale:
        raise RuntimeError(f"still out of sync after stamping: {stale}")
    return changed


if __name__ == "__main__":
    import mmm

    args = sys.argv[1:]
    if args and args[0] == "--check":
        stale = mmm.check_sync()
        print("in sync" if not stale else
              "\n".join(f"{m}: {s}" for m, s in stale))
        sys.exit(1 if stale else 0)
    if len(args) != 1:
        print(__doc__)
        sys.exit(2)
    done = bump(args[0])
    print(f"stamped {args[0]} into {len(done)} file(s); "
          f"{len(mmm.module_files())} modules + __init__ now agree")
