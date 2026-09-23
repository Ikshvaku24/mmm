"""Collect the run's warnings, group them by CATEGORY, and write one document
per category instead of a wall of text in the notebook.

The problem this solves
-----------------------
Almost every warning in this codebase is raised **per feature**. With 65
features in the prior file, one mistake in one column of `feature_priors.csv`
produces 65 near-identical paragraphs in the cell output - so the one warning
that mattered is invisible, and the ones that are merely informational are
indistinguishable from it. The text is also the wrong shape: what you want is
"these 44 features are pinned, here they are in a table, here is the fix",
not the same 6 lines 44 times.

So the run captures its warnings, matches each to a category, and writes

    00_warnings/00_INDEX.md          counts per category + severity, read first
    00_warnings/<category>.md        what it means, why it fires, the fix, and
                                     the table of affected features/regions
    00_warnings/all_warnings.csv     every warning as a row, for filtering

and prints ONE line per category to the console.

Nothing is suppressed - every warning still reaches the CSV verbatim. The
console just stops being the place you are expected to read them.
"""
from __future__ import annotations

import contextlib
import os
import re
import warnings

import pandas as pd

# --------------------------------------------------------------------------- #
# categories
# --------------------------------------------------------------------------- #
# Each rule: (slug, severity, match-substrings, title, what it means, the fix).
# `match` is checked against the warning text; the FIRST rule that matches wins,
# so order them most-specific first. Anything unmatched lands in "other", which
# is deliberately loud - an uncategorised warning is one nobody has triaged.
_RULES = (
    dict(
        slug="prior_pins_coefficient",
        severity="high",
        match=("the data cannot move it", "The data cannot move it"),
        title="Prior is so tight the data cannot move the coefficient",
        means=(
            "`prior_sd` for these features converts to a log-scale sigma below "
            "0.05, i.e. the coefficient is pinned to within about +/-5% of "
            "`prior_mean`. The posterior will come back as the prior wearing a "
            "hat, and the contribution you report is simply the number you "
            "wrote in the prior file - not something the model learned."),
        why=(
            "The usual cause is writing `0.2 * prior_mean` when you meant "
            "\"+/-20% uncertainty\". For a sign-constrained feature `prior_sd` "
            "is on the LOG scale, so 20% uncertainty is `prior_sd=0.2` with "
            "`prior_sd_basis=relative` - not 0.2 times the mean."),
        fix=(
            "In `feature_priors.csv`, set `prior_sd_basis=relative` and write "
            "the fraction you actually mean (0.2 = +/-20%). If the pin is "
            "deliberate - you are imposing a benchmark coefficient rather than "
            "estimating one - this warning is the receipt, and the feature's "
            "`contraction` in 02_convergence will be ~0. Say so when you "
            "present it. See docs/TUNING_GUIDE.md section 1.3."),
    ),
    dict(
        slug="prior_deliberately_pinned",
        severity="info",
        match=("is a deliberate",),
        title="Coefficient is fixed by the prior on purpose",
        means=(
            "`prior_sd_basis` is `relative` or `absolute` and the band written "
            "is under +/-5%. That is not a units mistake - it says exactly what "
            "was meant - so nothing here needs fixing."),
        why=(
            "It is recorded because it changes what the number IS. A "
            "coefficient this tightly bounded is not estimated from the data: "
            "its posterior is its prior, and its contribution is an assumption "
            "you imposed. `contraction` for these features will be ~0."),
        fix=(
            "Nothing, if the pin is intended. Just describe these contributions "
            "as inputs rather than findings - and do not cite them as the model "
            "agreeing with the benchmark, because they were set from it. To let "
            "the data speak, widen `global_prior_sd` toward 0.2."),
    ),
    dict(
        slug="near_constant_mutual",
        severity="high",
        match=("collinear with EACH OTHER",),
        title="Several near-constant features are collinear with each other",
        means=(
            "With `include_intercept: false` there is no intercept for a "
            "near-constant column to fight, but two or more such columns are "
            "all approximately the same constant, so only their SUM is "
            "identified."),
        why=(
            "The split between them is then decided by the priors rather than "
            "the data - which looks like a result and is not one."),
        fix=(
            "Centre them (`center_mode=mean`), or keep one and drop the rest. "
            "Check `01_data/collinearity_pairs.csv` for the actual correlations."),
    ),
    dict(
        slug="pooling_collapsed",
        severity="medium",
        match=("hierarchical pooling collapses",),
        title="regional_sd is so small that hierarchical pooling collapses",
        means=(
            "`regional_sd_prior` below 0.02 leaves essentially no room for "
            "regions to differ, so `beta_g = mu + tau*z_g` becomes one shared "
            "coefficient in all but name."),
        why=(
            "That is only correct if every region truly responds identically "
            "on the SCALED axis - which cannot hold if the regions were scaled "
            "by one global number (`dv_scale_scope='global'`)."),
        fix=(
            "Raise `regional_sd_prior`, or say what you mean directly with "
            "`pooling=global` - which is honest about fitting one coefficient "
            "and costs one parameter instead of G. TUNING_GUIDE section 1.5/1.6."),
    ),
    dict(
        slug="per_region_prior_sd_ignored",
        severity="medium",
        match=("per-region prior_sd is ignored",),
        title="A per-region prior_sd was written but is not used",
        means=(
            "Under `pooling='hierarchical'` the spread across regions is "
            "governed by the pooled `regional_sd`, so a per-region `prior_sd` "
            "on an override row has nowhere to act and was dropped."),
        why="The per-region MEAN is still applied; only the sd is ignored.",
        fix=(
            "Delete the `global_prior_sd` cell on those override rows, or "
            "switch the feature to `pooling=independent`, where each region "
            "genuinely gets its own prior. TUNING_GUIDE section 1.7."),
    ),
    dict(
        slug="prior_mean_not_a_magnitude",
        severity="high",
        match=("need prior_mean > 0", "(a magnitude)"),
        title="A sign-constrained feature was given a non-positive prior_mean",
        means=(
            "For `sign_constraint=positive|negative` the coefficient is built "
            "as `+/-exp(...)`, so `prior_mean` is a MAGNITUDE and must be > 0. "
            "The direction comes from `sign_constraint`, never from the sign "
            "of the mean."),
        why=(
            "Writing a negative mean for a negative feature is the common "
            "reading, and it is wrong here - the value was replaced by a "
            "default (0.05) or fell back to the feature-level prior, so the "
            "model is NOT using the number you wrote."),
        fix=(
            "Write the positive magnitude and set `sign_constraint=negative`. "
            "TUNING_GUIDE section 1.4."),
    ),
    dict(
        slug="negative_values_uncentred",
        severity="high",
        match=("assumes non-negative values",),
        title="A sign-constrained feature has negative values but is not centred",
        means=(
            "Scaling without centring divides by the mean of positive values "
            "and assumes the column never goes negative. These columns do, so "
            "the sign constraint is being applied to an axis that crosses "
            "zero and the constraint no longer means what it says."),
        why=(
            "Typically a level variable (price index, distribution) or a "
            "de-meaned column arriving where a spend column was expected."),
        fix=(
            "Set `center=1` (or `center_mode=mean`) for these features. If you "
            "do, also set `contribution_reference=zero` or their contribution "
            "collapses to ~0, because the centred column sums to zero over the "
            "training window. TUNING_GUIDE sections 4.2 and 5.1."),
    ),
    dict(
        slug="collinear_with_intercept",
        severity="high",
        match=("almost collinear with the region intercept",),
        title="An always-on feature is nearly constant and fights the intercept",
        means=(
            "After scaling without centring these columns sit at ~1.0 every "
            "period, which is exactly what the region intercept already is. "
            "The sampler cannot separate the two."),
        why=(
            "This is the defect that broke real_data_v1: coefficients of +31 "
            "and -33 on TDP/AVP, a decomposition of +91% / -97% that cancelled "
            "out, max R-hat 1.26 and tree depth saturated in 100% of steps."),
        fix=(
            "Set `center_mode=mean` for these features (the legacy `center=1` "
            "flag is a no-op when an explicit `center_mode` is present) and "
            "pair it with `contribution_reference=zero`. Centring is a "
            "reparameterisation: it fixes the geometry without changing the "
            "attribution. TUNING_GUIDE section 4.2."),
    ),
    dict(
        slug="degenerate_feature_column",
        severity="high",
        match=("The column is constant, empty or non-positive",),
        title="A feature column is degenerate over the training window",
        means=(
            "The scaling factor came out zero, negative or non-finite, so 1.0 "
            "was used instead. The column is constant, all-zero, or entirely "
            "non-positive in the training window."),
        why=(
            "Usually a column that does not belong in the model at all - the "
            "v1 run had three coupon columns whose non-zero values were all "
            "~1e-15, i.e. float noise being fitted as a regressor."),
        fix=(
            "Drop these rows from `feature_priors.csv`, or check they are the "
            "columns you meant. `run.zero_threshold_rel: 1.0e-6` snaps "
            "adstock-tail dust to exact zero; `run.min_feature_scale` rejects "
            "a column whose own scale is dust."),
    ),
    dict(
        slug="seasonality_overfit_risk",
        severity="medium",
        match=("risk of overfitting", "is high for"),
        title="fourier_order is high for the number of training periods",
        means=(
            "Each harmonic costs two parameters. With few training periods the "
            "seasonal block can chase noise, and it competes with any explicit "
            "seasonal dummies you already have."),
        why="Rule of thumb: at least 6 training periods per harmonic.",
        fix=(
            "Lower `model.fourier_order`, or drop it to 0 if seasonality is "
            "already carried by dummies. TUNING_GUIDE section 2.3."),
    ),
    dict(
        slug="cadence_ambiguous",
        severity="medium",
        match=("neither weekly nor monthly",),
        title="The date spacing is neither weekly nor monthly",
        means=(
            "Cadence was inferred as monthly as a fallback. Every downstream "
            "period count - holdout length, MAT block size, CV horizon and "
            "minimum training window - follows from it."),
        why="Irregular dates, or a panel that is not on a calendar grid.",
        fix="Set `run.cadence` (and `cv.cadence`) explicitly rather than 'auto'.",
    ),
    dict(
        slug="intercept_without_centering",
        severity="high",
        match=("include_intercept=False with dv_center",),
        title="The intercept was removed but the KPI still carries its level",
        means=(
            "`model.include_intercept: false` leaves no term to hold the level "
            "of the KPI, and `run.dv_center: none` leaves that level in the "
            "data. Every coefficient will be dragged upwards to fake an "
            "intercept."),
        why="The two settings are only coherent together one way round.",
        fix=(
            "Set `run.dv_center: mean`. The baseline then becomes the fixed "
            "training mean instead of a free parameter. TUNING_GUIDE section 2.2."),
    ),
    dict(
        slug="scaling_uses_holdout",
        severity="review",
        match=("scaling_window='full'",),
        title="The scaling statistics include the holdout",
        means=(
            "`run.scaling_window: full` computes every centre and scale - the "
            "KPI's included - on the whole panel. The holdout rows of "
            "fit_metrics.csv therefore saw one number per region and column "
            "from the test window; the KPI centre in particular carries the "
            "holdout's sales level."),
        why="Chosen deliberately, usually so dv_scale matches the window the "
            "vendor contributions and generated priors were computed over.",
        fix=("Nothing to fix if intended. Judge out-of-sample accuracy with "
             "cross-validation (`cv.enabled: true`), which always scales on "
             "each fold's own training window. CONFIG_GUIDE, run section."),
    ),
    dict(
        slug="generated_prior_units",
        severity="high",
        match=("the generated priors are in different units",),
        title="The generated prior file is in different units from this run",
        means=(
            "The pre-model step wrote each mean as contribution / SUM(raw x) / "
            "the region's MEAN KPI. The model reads a coefficient against "
            "`run.dv_scale`, per `run.dv_scale_scope`. When those differ, every "
            "contribution fitted with the generated file is off by the ratio "
            "of the two scales - and still reconciles to 100%, so no later "
            "check can see it."),
        why="`run.dv_scale` is not `mean`, `dv_scale_scope` is not `region`, "
            "or `data.dv_aggregation` is not `mean`.",
        fix=(
            "Set `run.dv_scale: mean`, `run.dv_scale_scope: region` and keep "
            "`data.dv_aggregation: mean` before fitting with the generated "
            "file. FEATURE_PRIOR_GUIDE section 5."),
    ),
    dict(
        slug="generated_prior_blank",
        severity="medium",
        match=("have no generated prior",),
        title="Some variables got no generated prior mean",
        means=(
            "The pre-model step left these variables' `global_prior_mean` "
            "blank: neither the mapping nor the share file covers them, or "
            "they have no support in any region."),
        why="The feature prior file lists more variables than the two input "
            "files cover - which is allowed - or the extract is empty.",
        fix="Fill the mean by hand, add the variable to the share file, or "
            "drop it before fitting. FEATURE_PRIOR_GUIDE section 5.",
    ),
    dict(
        slug="regional_prior_sign_skipped",
        severity="review",
        match=("run AGAINST their variable's sign",),
        title="Some regions have no row in the regional prior file",
        means=(
            "In these regions the implied coefficient has the opposite sign "
            "to the variable's `sign_constraint`. A region row holds a "
            "magnitude and the sign is feature-level, so the region falls "
            "back to the national prior."),
        why="The vendor's contribution changes sign across regions.",
        fix="Decide whether the variable is really signed; if the sign is "
            "genuinely regional, make it `free`. FEATURE_PRIOR_GUIDE section 5.",
    ),
)

_OTHER = dict(
    slug="other",
    severity="review",
    match=(),
    title="Uncategorised warnings",
    means="These did not match any known category.",
    why=("An uncategorised warning is one nobody has triaged yet - read it in "
         "full rather than assuming it is routine."),
    fix=("If it turns out to be a recurring, understood condition, add a rule "
         "for it in warnings_report.py so it gets its own document and its "
         "own fix instructions."),
)

SEVERITY_ORDER = {"high": 0, "medium": 1, "review": 2, "low": 3, "info": 4}


def classify(message: str) -> dict:
    """Which category rule does this warning text belong to?"""
    text = str(message)
    for rule in _RULES:
        if any(m in text for m in rule["match"]):
            return rule
    return _OTHER


# `name: rest` or `name[region]: rest` - every per-feature warning in this
# codebase is written that way, which is what makes the tables possible.
_SUBJECT = re.compile(r"^\s*([^\s:][^:]{0,200}?)(?:\[([^\]]+)\])?\s*:\s")


def split_subject(message: str) -> tuple[str, str, str]:
    """(feature, region, remaining text) for a per-feature warning."""
    text = str(message).strip()
    m = _SUBJECT.match(text)
    if not m:
        return "", "", text
    feature = (m.group(1) or "").strip()
    # a sentence that merely contains a colon is not a subject line
    if " " in feature and len(feature.split()) > 6:
        return "", "", text
    return feature, (m.group(2) or "").strip(), text[m.end():].strip()


@contextlib.contextmanager
def collect_warnings():
    """Capture every warning raised inside the block, without swallowing it.

    Yields the list the warnings land in. `simplefilter("always")` is essential:
    the default filter shows a given warning once per source line, so 44
    features tripping the same check at the same line would be recorded once
    and the other 43 silently lost from the report.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield caught


def to_frame(caught) -> pd.DataFrame:
    """The captured warnings as one row each, with category and subject."""
    rows = []
    for w in caught or []:
        text = str(getattr(w, "message", w))
        rule = classify(text)
        feature, region, detail = split_subject(text)
        rows.append({
            "category": rule["slug"],
            "severity": rule["severity"],
            "feature": feature,
            "region": region,
            "warning_class": getattr(getattr(w, "category", None), "__name__", ""),
            "source": f"{os.path.basename(str(getattr(w, 'filename', '')))}"
                      f":{getattr(w, 'lineno', '')}",
            "message": " ".join(text.split()),
            "detail": " ".join(detail.split()),
            "template": template_of(detail),
            **{f"p_{k}": v for k, v in params_of(detail).items()},
        })
    cols = ["category", "severity", "feature", "region", "warning_class",
            "source", "message", "detail", "template"]
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=cols)
    extra = [c for c in df.columns if c.startswith("p_")]
    return df[[c for c in cols if c in df.columns] + sorted(extra)]


_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?%?")
_KV = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\s*=\s*'?([-+]?[\w.%/]+)'?")


def template_of(detail: str) -> str:
    """The message with every number blanked, so near-identical texts collapse.

    44 features tripping one check produce 44 messages that differ only in the
    numbers. Masking those gives a single template to print ONCE, with the
    numbers moved into table columns where they can actually be compared.
    """
    return _NUM.sub("#", " ".join(str(detail).split()))


def params_of(detail: str) -> dict:
    """`key=value` pairs from the DIAGNOSIS, for the per-feature table.

    Only the first sentence is read. The rest of a warning is advice, and advice
    quotes settings too: "...use prior_sd=0.2 with prior_sd_basis='relative'".
    Scanning the whole text made the table report `prior_sd_basis=relative` for
    a feature whose basis is `log` - the exact opposite of what it says - which
    is worse than having no column at all.
    """
    first = str(detail).split(". ")[0]
    out = {}
    for k, v in _KV.findall(first):
        if k not in out:
            out[k] = v
    return out


def _rule_by_slug(slug: str) -> dict:
    for r in _RULES:
        if r["slug"] == slug:
            return r
    return _OTHER


def _table(df: pd.DataFrame) -> list[str]:
    """The affected features - message text printed ONCE above, not per row.

    The old version repeated the whole warning beside every feature, which for
    44 features meant 44 copies of the same paragraph. Here the shared template
    is printed once and the table carries only what actually differs: the
    feature, the region, and whichever `key=value` numbers the message quoted.
    """
    out = []
    templates = df["template"].value_counts() if "template" in df.columns \
        else pd.Series(dtype=int)
    if len(templates):
        out += ["**The message** (identical for every feature below; `#` marks "
                "the numbers, which are in the table):", "",
                "> " + str(templates.index[0]), ""]
        for extra in templates.index[1:]:
            out += ["> " + str(extra), ""]

    named = df[df["feature"].astype(str) != ""]
    if named.empty:
        return out or ["(not tied to any single feature)"]

    pcols = [c for c in named.columns
             if c.startswith("p_") and named[c].notna().any()]
    has_region = (named["region"].astype(str) != "").any()
    head = ["feature"] + (["region"] if has_region else []) \
        + [c[2:] for c in pcols]
    out += ["| " + " | ".join(head) + " |",
            "|" + "|".join(["---"] * len(head)) + "|"]
    for _, r in named.iterrows():
        cells = [f"`{r['feature']}`"] + ([str(r["region"])] if has_region else [])
        cells += ["" if pd.isna(r[c]) else f"`{r[c]}`" for c in pcols]
        out.append("| " + " | ".join(cells) + " |")

    unnamed = df[df["feature"].astype(str) == ""]
    if len(unnamed):
        out += ["", f"Plus {len(unnamed)} not tied to a single feature."]
    return out


def write_warning_docs(caught, outdir: str, run_name: str = "") -> pd.DataFrame:
    """Write 00_INDEX.md, one <category>.md per category, and all_warnings.csv.

    Returns the frame so callers can print a summary. Writing an index even
    when there are no warnings matters: a missing file is ambiguous ("did it
    not run, or was it clean?"), an explicit "no warnings" is not.
    """
    df = to_frame(caught)
    os.makedirs(outdir, exist_ok=True)
    # Two files rather than one, so the prose is not repeated on every row:
    #   all_warnings.csv  one row per warning - who, where, and the numbers
    #   warning_texts.csv one row per distinct MESSAGE, with its full text
    rowcols = [c for c in df.columns if c not in ("message", "detail")]
    df[rowcols].to_csv(os.path.join(outdir, "all_warnings.csv"), index=False)
    if not df.empty:
        texts = (df.groupby(["category", "severity", "template"], as_index=False)
                 .agg(n=("template", "size"),
                      example=("message", "first")))
        texts.sort_values(["severity", "n"], ascending=[True, False]).to_csv(
            os.path.join(outdir, "warning_texts.csv"), index=False)

    if df.empty:
        with open(os.path.join(outdir, "00_INDEX.md"), "w", encoding="utf-8") as f:
            f.write(f"# Warnings{' - ' + run_name if run_name else ''}\n\n"
                    "No warnings were raised during this run.\n")
        return df

    counts = (df.groupby(["category", "severity"]).size()
              .reset_index(name="n"))
    counts["_ord"] = counts["severity"].map(SEVERITY_ORDER).fillna(9)
    counts = counts.sort_values(["_ord", "n"], ascending=[True, False])

    lines = [f"# Warnings{' - ' + run_name if run_name else ''}", "",
             f"{len(df)} warnings in {counts.shape[0]} categories. "
             "Read the high-severity ones first; each links to a document with "
             "the affected features and the fix.", "",
             "| severity | category | count | document |", "|---|---|---|---|"]
    for _, r in counts.iterrows():
        rule = _rule_by_slug(r["category"])
        lines.append(f"| **{r['severity']}** | {rule['title']} | {r['n']} | "
                     f"[{r['category']}.md]({r['category']}.md) |")
    lines += ["", "Every warning verbatim, one row each: `all_warnings.csv`.", "",
              "> These are warnings, not errors - the run completed. A "
              "high-severity one means a number in the report is probably not "
              "measuring what you think it measures.", ""]
    with open(os.path.join(outdir, "00_INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    for slug, grp in df.groupby("category"):
        rule = _rule_by_slug(slug)
        body = [f"# {rule['title']}", "",
                f"**Severity:** {rule['severity']} &nbsp;&nbsp; "
                f"**Raised {len(grp)} time(s)**"
                + (f" across {grp['feature'].nunique()} features"
                   if (grp['feature'].astype(str) != '').any() else ""), "",
                "## What it means", "", rule["means"], "",
                "## Why it fires", "", rule["why"], "",
                "## What to do", "", rule["fix"], "",
                "## Affected", ""]
        body += _table(grp)
        body += ["", "---", "",
                 f"Source: `{'`, `'.join(sorted(set(grp['source'])))}`"]
        with open(os.path.join(outdir, f"{slug}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(body) + "\n")
    return df


def print_warning_summary(df: pd.DataFrame, outdir: str,
                          verbose: bool = False) -> None:
    """A headline, not a transcript.

    The whole point of the folder is that the notebook stops being where you
    read warnings, so this prints the count, the folder, and (unless `verbose`)
    only the HIGH-severity categories - the ones that mean a reported number is
    probably not measuring what you think. Everything else is in 00_INDEX.md.
    """
    if df is None or df.empty:
        print("[warnings] none")
        return
    counts = (df.groupby(["category", "severity"]).size()
              .reset_index(name="n"))
    counts["_ord"] = counts["severity"].map(SEVERITY_ORDER).fillna(9)
    counts = counts.sort_values(["_ord", "n"], ascending=[True, False])
    shown = counts if verbose else counts[counts["severity"] == "high"]
    print(f"[warnings] {len(df)} in {len(counts)} categories "
          f"({len(counts) - len(shown)} not shown) -> {outdir}\00_INDEX.md")
    for _, r in shown.iterrows():
        rule = _rule_by_slug(r["category"])
        n_feat = df[(df["category"] == r["category"])
                    & (df["feature"].astype(str) != "")]["feature"].nunique()
        extra = f", {n_feat} features" if n_feat else ""
        print(f"  [{r['severity']:>6}] {r['n']:>4}x{extra:<16} "
              f"{rule['title']}  -> {r['category']}.md")
