"""The vendor <-> model variable mapping file - ONE place that reads it.

Vendor and model carry approximately the same variables. Where they differ it
is almost always because the model SPLITS a vendor variable - by period or by
sub-brand:

    vendor   media_digital-display_..._effervescent_hero_impressions
    ours     media_digital-display_..._effervescent_hero_impressions_mat1
             media_digital-display_..._effervescent_hero_impressions_mat2

and occasionally the reverse, where the vendor splits something the model keeps
whole. The mapping file records either direction in one long table:

    vendor_variable,our_variable,region,contribution
    digital_hero,digital_hero_mat1,Core,880000
    digital_hero,digital_hero_mat2,Core,880000     <- vendor name REPLICATED
    tv_a,tv,Core,500000
    tv_b,tv,Core,300000                            <- our name REPLICATED

`region` and `contribution` are both optional. The contribution belongs to the
VENDOR variable, so when a vendor row is replicated across several of ours the
same number is repeated (or given once and left blank on the rest); it is never
summed across the replicas.

How rows become groups
----------------------
The table is a bipartite graph - vendor names on one side, ours on the other -
and a GROUP is a connected component of it. That one rule covers every case:

    one vendor -> many ours     (the common case: period / sub-brand splits)
    many vendor -> one ours     (the vendor splits something we keep whole)
    many -> many                (both at once)

and each group is compared as ONE unit: the vendor total for the group against
the sum of our members. Both the pre-model prior builder and the post-run
benchmark sheet read groups from here, so the grouping used to BUILD a prior is
always the grouping used to CHECK it.
"""
from __future__ import annotations

__codebase__ = "2026.09.24"   # must equal mmm.__version__

import difflib
import os
import re

import numpy as np
import pandas as pd

_VENDOR_HINTS = ("vendor_variable", "vendor", "vendor_var", "benchmark_variable",
                 "their_variable")
_OURS_HINTS = ("our_variable", "our_variables", "our_variable_list",
               "model_variable", "our", "feature", "variable")
_REGION_HINTS = ("region", "retailer", "account", "market", "geo", "banner")
_CONTRIB_HINTS = ("contribution", "vendor_contribution", "volume", "value")

ALL_REGIONS = "__all__"


def _sniff(cols, hints, exclude=()):
    low = {str(c).strip().lower(): c for c in cols if c not in exclude}
    for h in hints:
        if h in low:
            return low[h]
    for h in hints:
        for k, c in low.items():
            if h in k:
                return c
    return None


def _read_any(path: str) -> pd.DataFrame:
    return (pd.read_csv(path) if str(path).lower().endswith(".csv")
            else pd.read_excel(path))


DATACUBE = "columns of the datacube"
PRIOR_FILE = "variables of the feature prior file (data.feature_priors)"


def is_dummy(name) -> bool:
    """A dummy is named as one. That is the whole rule, so it is one line."""
    return "dummy" in str(name).lower()


def check_names(names, known, source: str, what: str = "variable",
                against: str = DATACUBE) -> None:
    """Every name must be in `known`. Fail with suggestions.

    `known` is the feature prior file's variables when one is configured -
    the mapping and share files may cover FEWER variables than the prior file,
    never more - and the datacube's columns otherwise. A typo'd name would
    otherwise be silently skipped - its prior left blank, or its benchmark
    never matched - and nobody would learn why.
    """
    if known is None:
        return
    known = set(map(str, known))
    missing = sorted({str(n) for n in names if str(n) not in known})
    if not missing:
        return
    lines = []
    for m in missing[:25]:
        near = difflib.get_close_matches(m, list(known), n=2, cutoff=0.75)
        lines.append(f"  {m}" + (f"   (did you mean {' / '.join(near)}?)"
                                 if near else ""))
    more = f"\n  ... and {len(missing) - 25} more" if len(missing) > 25 else ""
    fix = ("\nThe feature prior file may carry MORE variables than this file, "
           "never fewer. Add the variable to data.feature_priors, or drop it "
           "from this file." if against == PRIOR_FILE else
           "\nNames must match the datacube EXACTLY. Fix the file, or rename "
           "the datacube columns.")
    raise SystemExit(
        f"{source}: {len(missing)} {what} name(s) are not {against}:\n"
        + "\n".join(lines) + more + fix)


def load_mapping_table(path: str, known_columns=None,
                       against: str = DATACUBE) -> pd.DataFrame:
    """vendor_variable / our_variable / region / contribution, one row per link.

    `known_columns` cross-checks every `our_variable` - the feature prior
    file's variables when one is configured (`against=PRIOR_FILE`), else the
    datacube's columns. The VENDOR names are the vendor's and are not checked.
    """
    if not path:
        return pd.DataFrame(columns=["vendor_variable", "our_variable",
                                     "region", "contribution"])
    if not os.path.exists(path):
        raise SystemExit(f"mapping file not found: {path}. Fix "
                         "`data.mapping_file` in config.yaml, or remove it.")
    df = _read_any(path)
    v = _sniff(df.columns, _VENDOR_HINTS)
    o = _sniff(df.columns, _OURS_HINTS, exclude={v})
    if v is None or o is None:
        raise SystemExit(
            f"{path}: need a vendor_variable column and an our_variable "
            f"column; found {list(df.columns)}. See "
            "samples/mapping_sample.csv.")
    r = _sniff(df.columns, _REGION_HINTS, exclude={v, o})
    c = _sniff(df.columns, _CONTRIB_HINTS, exclude={v, o, r})

    def text(col):
        # fillna BEFORE astype: under pandas 3 the string dtype keeps NaN
        # through astype(str), so a blank cell would never become "" - a blank
        # region would not read as national, and a blank name would survive
        return df[col].fillna("").astype(str).str.strip()

    out = pd.DataFrame({
        "vendor_variable": text(v),
        "our_variable": text(o),
        "region": (text(r) if r else ALL_REGIONS),
        "contribution": (pd.to_numeric(df[c], errors="coerce") if c
                         else np.nan),
    })
    out = out[(out["vendor_variable"] != "") & (out["our_variable"] != "")
              & (out["vendor_variable"].str.lower() != "nan")
              & (out["our_variable"].str.lower() != "nan")]
    out["region"] = out["region"].replace({"": ALL_REGIONS, "nan": ALL_REGIONS})
    check_names(out["our_variable"], known_columns, path, "our_variable",
                against)

    # a vendor contribution replicated across several of our rows must agree
    # with itself - two different numbers for one vendor cell is an error in
    # the file, not something to average away
    has = out.dropna(subset=["contribution"])
    clash = (has.groupby(["vendor_variable", "region"])["contribution"]
             .nunique())
    clash = clash[clash > 1]
    if len(clash):
        ex = ", ".join(f"{a}@{b}" for a, b in list(clash.index)[:5])
        raise SystemExit(
            f"{path}: these vendor variables carry MORE THAN ONE contribution "
            f"for the same region: {ex}. A vendor contribution is replicated "
            "across the rows that map it to several of ours, never split - "
            "repeat the same number, or give it once and leave the rest blank.")
    n_c = int(out["contribution"].notna().sum())
    print(f"[mapping] {len(out)} links: {out['vendor_variable'].nunique()} "
          f"vendor variables <-> {out['our_variable'].nunique()} of ours"
          + (f", contributions on {n_c} rows" if n_c else ", no contributions"))
    return out.reset_index(drop=True)


def _region_key(r) -> str:
    """Letters and digits only, lower-cased: "('Base', 'Super Cadena')",
    "Base_Super Cadena" and "base - supercadena" all become basesupercadena."""
    return "".join(ch for ch in str(r).lower() if ch.isalnum())


def _region_tokens(r) -> tuple:
    """The words, sorted - so "Droguerias_Base" matches "('Base', 'Droguerias')"."""
    return tuple(sorted(re.findall(r"[a-z0-9]+", str(r).lower())))


def align_regions(table: pd.DataFrame, known_regions, source: str = "mapping"
                  ) -> pd.DataFrame:
    """Rewrite the file's region names to the DATACUBE's spelling, or stop.

    A mapping built in pandas often carries a region as a printed tuple -
    "('Base', 'Droguerias')" - while the datacube says "Base_Droguerias". An
    exact join then matches nothing, every contribution lands in a region the
    model does not have, and the only symptom is "no contribution found". So
    each region is matched exactly, then on its letters and digits, then on
    its sorted words; whatever is left over STOPS the run with both lists,
    rather than being silently dropped.

    Also refuses a vendor variable given BOTH regionally and nationally - the
    national row would be allocated on top of the regional ones and count the
    same volume twice (a vendor table's `_All` column pasted in as a region).
    """
    if table is None or not len(table):
        return table
    known = [str(k) for k in known_regions]
    by_key, by_tok = {}, {}
    for k in known:
        by_key.setdefault(_region_key(k), []).append(k)
        by_tok.setdefault(_region_tokens(k), []).append(k)

    wanted = [r for r in table["region"].unique() if r != ALL_REGIONS]
    rename, unmatched = {}, []
    for r in wanted:
        if r in known:
            continue
        hit = by_key.get(_region_key(r), [])
        if len(hit) != 1:
            hit = by_tok.get(_region_tokens(r), [])
        if len(hit) == 1:
            rename[r] = hit[0]
        else:
            unmatched.append(r)
    if unmatched:
        keys = {_region_key(k): k for k in known}
        lines = []
        for r in unmatched[:15]:
            near = difflib.get_close_matches(_region_key(r), list(keys), n=1,
                                             cutoff=0.6)
            lines.append(f"  {r!r}" + (f"   (closest: {keys[near[0]]!r})"
                                       if near else ""))
        raise SystemExit(
            f"{source}: {len(unmatched)} region name(s) are not regions of the "
            "datacube, even ignoring case, spaces and punctuation:\n"
            + "\n".join(lines)
            + f"\nThe datacube's regions are: {known}\n"
            "Fix the spelling, or leave `region` BLANK for a national number. "
            "A region the model does not have would otherwise drop its "
            "contribution without a word.")
    out = table.copy()
    if rename:
        out["region"] = out["region"].replace(rename)
        ex = ", ".join(f"{a!r} -> {b!r}" for a, b in list(rename.items())[:3])
        print(f"[mapping] matched {len(rename)} region spelling(s) to the "
              f"datacube: {ex}{' ...' if len(rename) > 3 else ''}")

    has = out.dropna(subset=["contribution"])
    both = (has.assign(_nat=has["region"] == ALL_REGIONS)
            .groupby("vendor_variable")["_nat"].nunique())
    both = list(both[both > 1].index)
    if both:
        raise SystemExit(
            f"{source}: {len(both)} vendor variable(s) have contributions "
            f"BOTH per region and national ({both[:5]}). The national row "
            "would be allocated on top of the regional ones and count the "
            "same volume twice. Keep one: the regional rows, or a single "
            "national row with `region` blank.")
    return out


def has_contribution(table: pd.DataFrame) -> bool:
    return (table is not None and len(table)
            and bool(table["contribution"].notna().any()))


# --------------------------------------------------------------------------- #
# groups = connected components of the vendor <-> ours graph
# --------------------------------------------------------------------------- #
def groups(table: pd.DataFrame) -> dict:
    """our_variable -> group label, via the connected components of the table.

    The label is the vendor name when a group has one vendor variable (the
    common case), otherwise the vendor names joined with " + ", so the sheet
    says exactly what it is comparing.
    """
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for v, o in zip(table["vendor_variable"], table["our_variable"]):
        union(("v", v), ("o", o))

    members: dict = {}
    for node in list(parent):
        members.setdefault(find(node), []).append(node)
    label_of_root = {}
    for root, nodes in members.items():
        vendors = sorted(n for k, n in nodes if k == "v")
        label_of_root[root] = " + ".join(vendors)
    return {o: label_of_root[find(("o", o))]
            for o in table["our_variable"].unique()}


def group_contributions(table: pd.DataFrame) -> pd.DataFrame:
    """group / region / contribution - the VENDOR total for each group.

    Each vendor variable's contribution is counted ONCE per region however many
    of our rows it was replicated across, then summed over the vendor variables
    in the group.
    """
    if not has_contribution(table):
        return pd.DataFrame(columns=["group", "region", "contribution"])
    g = groups(table)
    t = table.dropna(subset=["contribution"]).copy()
    t["group"] = t["our_variable"].map(g)
    per_vendor = t.drop_duplicates(["vendor_variable", "region"])
    return (per_vendor.groupby(["group", "region"], as_index=False)
            ["contribution"].sum())


def group_members(table: pd.DataFrame) -> pd.DataFrame:
    """group / our_variable, one row per member - for the prior builder."""
    g = groups(table)
    return pd.DataFrame({"our_variable": list(g), "group": list(g.values())})
