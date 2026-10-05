"""Shared caching and timing for the app. Nothing here imports Streamlit.

A Databricks App is ONE Python process, and Streamlit runs each user's
session as a thread inside it - so module-level state is shared by every
user. `SharedCache` keeps the results of repeated calls (the cluster's
state, a run's status, a BMC's run list, codebase 1's schema and checks, a
run's output files) for EVERYONE, keyed by content, instead of each session
fetching its own copy. Two sessions asking for the same thing at the same
moment get ONE computation: the second waits for the first's result.

`timed` / `log_timing` write how long each backend, ADLS and Jobs call took
to the app's log - the Logs tab of the Databricks App - so slow steps can be
found with real users, not guessed:

    [timing] codebase.generate_priors      3.42s  worker
    [timing] adls.download                 0.81s  Secondary Modelling/...

Settings (environment variables, all optional):
  BRIDGE_TIMING_MIN       log calls slower than this many seconds (0.3);
                          0 logs every call
  BRIDGE_CACHE_TTL_SCALE  multiplies every cache lifetime (1). 0 turns the
                          caches off - the tests use it
"""
from __future__ import annotations

import copy
import functools
import hashlib
import os
import threading
import time
from collections import OrderedDict

import numpy as np
import pandas as pd


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


TIMING_MIN_SECONDS = _env_float("BRIDGE_TIMING_MIN", 0.3)
TTL_SCALE = _env_float("BRIDGE_CACHE_TTL_SCALE", 1.0)

# --------------------------------------------------------------------------- #
# timing
# --------------------------------------------------------------------------- #
STATS = {}                    # label -> [calls, total seconds, slowest]
_STATS_LOCK = threading.Lock()


def log_timing(label: str, started: float, detail: str = "") -> float:
    """Record a call that began at `started` (time.perf_counter()); print it
    when it took at least TIMING_MIN_SECONDS. Returns the seconds taken."""
    seconds = time.perf_counter() - started
    with _STATS_LOCK:
        s = STATS.setdefault(label, [0, 0.0, 0.0])
        s[0] += 1
        s[1] += seconds
        s[2] = max(s[2], seconds)
    if seconds >= TIMING_MIN_SECONDS:
        print(f"[timing] {label:<30} {seconds:6.2f}s  {detail}".rstrip(), flush=True)
    return seconds


def timed(label: str, detail=None):
    """Decorator: log the call's duration under `label`. `detail(*args,
    **kwargs)`, when given, adds a short note (e.g. the path read)."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                note = ""
                if detail is not None:
                    try:
                        note = str(detail(*args, **kwargs))[:120]
                    except Exception:  # noqa: BLE001 - a log note never breaks a call
                        note = ""
                log_timing(label, t0, note)
        return inner
    return wrap


def timing_summary() -> list:
    """[(label, calls, total s, mean s, slowest s)], slowest total first."""
    with _STATS_LOCK:
        rows = [(k, v[0], v[1], v[1] / v[0] if v[0] else 0.0, v[2])
                for k, v in STATS.items()]
    return sorted(rows, key=lambda r: -r[2])


# --------------------------------------------------------------------------- #
# content keys
# --------------------------------------------------------------------------- #
def _feed(h, obj, depth=0):
    if depth > 12:
        h.update(b"<deep>")
        return
    if obj is None or isinstance(obj, (bool, int, float, str)):
        h.update(f"{type(obj).__name__}:{obj!r};".encode())
    elif isinstance(obj, (bytes, bytearray, memoryview)):
        h.update(b"bytes:" + hashlib.sha1(bytes(obj)).digest())
    elif isinstance(obj, pd.DataFrame):
        h.update(b"df:" + repr((list(map(str, obj.columns)),
                                list(map(str, obj.dtypes)), obj.shape)).encode())
        try:
            h.update(pd.util.hash_pandas_object(obj, index=True).values.tobytes())
        except TypeError:                   # unhashable cells (lists, dicts)
            h.update(obj.to_csv(index=True).encode())
    elif isinstance(obj, pd.Series):
        h.update(b"series:" + str(obj.name).encode())
        try:
            h.update(pd.util.hash_pandas_object(obj, index=True).values.tobytes())
        except TypeError:
            h.update(obj.to_csv().encode())
    elif isinstance(obj, np.ndarray):
        h.update(b"nd:" + repr((obj.dtype.str, obj.shape)).encode()
                 + np.ascontiguousarray(obj).tobytes())
    elif isinstance(obj, dict):
        h.update(b"{")
        for k in sorted(obj, key=lambda x: repr(x)):
            _feed(h, k, depth + 1)
            _feed(h, obj[k], depth + 1)
        h.update(b"}")
    elif isinstance(obj, (list, tuple)):
        h.update(b"[" if isinstance(obj, list) else b"(")
        for v in obj:
            _feed(h, v, depth + 1)
        h.update(b"]")
    elif isinstance(obj, (set, frozenset)):
        h.update(b"set{")
        for v in sorted(obj, key=lambda x: repr(x)):
            _feed(h, v, depth + 1)
        h.update(b"}")
    else:
        h.update(f"{type(obj).__module__}.{type(obj).__name__}:{obj!r};".encode())


def content_key(*parts) -> str:
    """A stable key for any mix of strings, numbers, bytes, dicts, lists and
    DataFrames - equal content, equal key (dict order does not matter)."""
    h = hashlib.sha1()
    for p in parts:
        _feed(h, p)
    return h.hexdigest()


def approx_size(obj, depth=0) -> int:
    """Rough bytes held by a cached value (for the cache's memory budget)."""
    if depth > 6:
        return 64
    if isinstance(obj, (bytes, bytearray)):
        return len(obj)
    if isinstance(obj, str):
        return len(obj)
    if isinstance(obj, pd.DataFrame):
        try:
            return int(obj.memory_usage(index=True, deep=False).sum())
        except Exception:  # noqa: BLE001
            return 1024
    if isinstance(obj, np.ndarray):
        return int(obj.nbytes)
    if isinstance(obj, dict):
        return 64 + sum(approx_size(k, depth + 1) + approx_size(v, depth + 1)
                        for k, v in obj.items())
    if isinstance(obj, (list, tuple, set)):
        return 64 + sum(approx_size(v, depth + 1) for v in obj)
    if hasattr(obj, "__dict__"):
        return 64 + approx_size(vars(obj), depth + 1)
    return 64


# --------------------------------------------------------------------------- #
# the shared cache
# --------------------------------------------------------------------------- #
_ALL_CACHES = []


class SharedCache:
    """A thread-safe TTL + LRU cache shared by every session of the app.

    get_or_compute(key, compute) returns (value, hit). While one thread
    computes a key, other threads asking for it wait and take its result
    (single flight), so ten users opening the same run cost one ADLS read.
    Values are deep-copied on the way out, so no session can change another
    session's copy. `ttl` may be a number or a function of the value (e.g.
    a finished run's status is kept longer than a running one's)."""

    def __init__(self, name, ttl=60.0, maxsize=256, max_bytes=None, copy_values=True):
        self.name, self.ttl, self.maxsize = name, ttl, maxsize
        self.max_bytes, self.copy_values = max_bytes, copy_values
        self._data = OrderedDict()           # key -> (expires, value, size)
        self._bytes = 0
        self._lock = threading.Lock()
        self._flights = {}                   # key -> threading.Event
        self.hits = self.misses = 0
        _ALL_CACHES.append(self)

    # -- internals -------------------------------------------------------
    def _out(self, value):
        return copy.deepcopy(value) if self.copy_values else value

    def _lifetime(self, value, ttl):
        t = self.ttl if ttl is None else ttl
        if callable(t):
            t = t(value)
        return max(0.0, float(t or 0.0)) * TTL_SCALE

    def _drop(self, key):
        item = self._data.pop(key, None)
        if item is not None:
            self._bytes -= item[2]

    def _evict(self):
        while self._data and (len(self._data) > self.maxsize or
                              (self.max_bytes and self._bytes > self.max_bytes)):
            k = next(iter(self._data))
            self._drop(k)

    # -- API ---------------------------------------------------------------
    def peek(self, key):
        """(True, value) for a live entry, else (False, None). No compute."""
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return False, None
            if item[0] < time.monotonic():
                self._drop(key)
                return False, None
            self._data.move_to_end(key)
            return True, self._out(item[1])

    def put(self, key, value, ttl=None):
        life = self._lifetime(value, ttl)
        if life <= 0:
            return
        size = approx_size(value) if self.max_bytes else 0
        if self.max_bytes and size > self.max_bytes / 4:
            return                            # never let one entry fill the cache
        with self._lock:
            self._drop(key)
            self._data[key] = (time.monotonic() + life, value, size)
            self._bytes += size
            self._evict()

    def get_or_compute(self, key, compute, ttl=None, cache_if=None):
        while True:
            hit, value = self.peek(key)
            if hit:
                with self._lock:
                    self.hits += 1
                return value, True
            with self._lock:
                flight = self._flights.get(key)
                if flight is None:
                    flight = self._flights[key] = threading.Event()
                    leader = True
                else:
                    leader = False
            if not leader:
                flight.wait(timeout=900)
                hit, value = self.peek(key)
                if hit:
                    with self._lock:
                        self.hits += 1
                    return value, True
                continue                      # the leader failed or did not cache: try ourselves
            try:
                value = compute()
                with self._lock:
                    self.misses += 1
                if cache_if is None or cache_if(value):
                    self.put(key, value, ttl)
                return self._out(value), False
            finally:
                with self._lock:
                    self._flights.pop(key, None)
                flight.set()

    def invalidate(self, key=None, prefix=None):
        """Forget one key, every key starting with `prefix`, or (neither) all."""
        with self._lock:
            if key is None and prefix is None:
                self._data.clear()
                self._bytes = 0
                return
            for k in [k for k in self._data
                      if k == key or (prefix is not None and str(k).startswith(prefix))]:
                self._drop(k)

    def __len__(self):
        with self._lock:
            return len(self._data)

    def nbytes(self):
        with self._lock:
            return self._bytes


def clear_all_caches():
    """Empty every SharedCache (tests; an admin 'reload')."""
    for c in list(_ALL_CACHES):
        c.invalidate()
