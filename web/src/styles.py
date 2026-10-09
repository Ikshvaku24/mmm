"""The page's CSS - what Streamlit's theme (web/themes/*.toml) cannot do.

The colours, fonts and corners come from the company theme the viewer sees;
the accents here come from src/brand.py, so they follow the same choice. Kept
to what the app needs: Streamlit's top bar made click-through and its ⋮ menu
hidden (the cluster and the light / dark button sit there - the top bar), the
header's baseline, the page titles' marker, the text on primary buttons, the
side panel's logos (the company logos are buttons), the run counter and the
run list's fade-in on the Runs and results page.

Both modes' accents are in the sheet. The server knows the viewer's mode only
when the app runs, and Streamlit does not rerun on a theme change - so the
light / dark button's script marks the page (`<html data-bridge-mode="dark">`)
from the colour Streamlit actually painted, and the rules follow that mark at
once. Until the script has run, the server's guess applies.
"""
import streamlit as st

from src import brand

_COMMON = """
/* Streamlit's top bar: see-through, and it no longer catches clicks - a
   button scrolled up under it could not be pressed. Its ⋮ menu and the
   Deploy button are hidden: the cluster sits there instead (the top bar). */
[data-testid="stHeader"] { background: transparent; }
[data-testid="stHeader"],
[data-testid="stHeader"] [data-testid="stToolbar"],
[data-testid="stHeader"] [data-testid="stDecoration"] { pointer-events: none; }
[data-testid="stHeader"] button,
[data-testid="stHeader"] a,
[data-testid="stHeader"] [role="button"],
[data-testid="stHeader"] [data-testid="stToolbarActions"] > * { pointer-events: auto; }
[data-testid="stMainMenu"],
[data-testid="stAppDeployButton"] { display: none !important; }
.block-container { padding-top: 2.6rem; padding-bottom: 2.5rem; }

/* the top bar: who is signed in, and the cluster with its Start button -
   top right on every page */
.st-key-bridge_topbar {
    position: fixed; top: 0.62rem; right: 1rem; z-index: 999991;
    width: auto !important; flex-wrap: nowrap; align-items: center; gap: 0.6rem;
}
div:has(> .st-key-bridge_topbar) { min-height: 0; }
.st-key-bridge_topbar [data-testid="stMarkdownContainer"] p { margin: 0; }
/* Streamlit pulls every markdown block up by -1rem; in a row that only offsets
   the greeting and the cluster from the light / dark button */
.st-key-bridge_topbar [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
.bridge-hello {
    font-size: 0.8rem; font-weight: 600; color: var(--bridge-subtitle);
    white-space: nowrap; display: inline-block; max-width: 30vw;
    overflow: hidden; text-overflow: ellipsis; vertical-align: middle;
}
.bridge-cluster {
    display: inline-flex; align-items: center; gap: 0.42rem; white-space: nowrap;
    padding: 0.2rem 0.65rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600;
    border: 1px solid var(--bridge-rule); background: var(--bridge-surface);
    color: var(--bridge-ink); line-height: 1.35;
}
.bridge-cluster-dot { width: 0.55rem; height: 0.55rem; border-radius: 50%;
                      background: var(--bridge-off); flex: 0 0 auto; }
.bridge-cluster--good .bridge-cluster-dot { background: var(--bridge-good); }
.bridge-cluster--busy .bridge-cluster-dot { background: var(--bridge-busy);
                                            animation: bridge-pulse 1.4s ease-in-out infinite; }
.bridge-cluster--unknown .bridge-cluster-dot { background: transparent;
                                               border: 1.5px dashed var(--bridge-off); }
.bridge-cluster-note { font-size: 0.74rem; color: var(--bridge-subtitle); white-space: nowrap; }
.st-key-start_cluster_button button {
    min-height: 1.75rem; padding: 0 0.6rem; font-size: 0.78rem; white-space: nowrap;
}
/* the light / dark button - a pill like the cluster's */
.st-key-bridge_topbar [data-testid="stHtml"] { width: auto; }
.st-key-bridge_topbar [data-testid="stElementContainer"] { align-self: center; }
.st-key-bridge_topbar [data-testid="stHtml"] { display: flex; align-items: center; }
.bridge-theme-toggle {
    display: inline-flex; align-items: center; justify-content: center;
    width: 1.95rem; height: 1.6rem; padding: 0; cursor: pointer;
    border-radius: 999px; border: 1px solid var(--bridge-rule);
    background: var(--bridge-surface); color: var(--bridge-ink);
}
.bridge-theme-toggle::before {
    content: ""; width: 1rem; height: 1rem; background-color: currentColor;
    -webkit-mask: var(--bridge-toggle-icon) center / contain no-repeat;
    mask: var(--bridge-toggle-icon) center / contain no-repeat;
}
.bridge-theme-toggle:hover { border-color: var(--bridge-accent-ink); }
.bridge-theme-toggle:focus-visible { outline: 2px solid var(--bridge-accent-ink); outline-offset: 2px; }
@keyframes bridge-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }

/* the header: BRIDGE, what it stands for, and the baseline under it */
.bridge-header { margin: 0 0 1.2rem; }
.bridge-title { font-weight: 700; font-size: 2.05rem; line-height: 1.1;
                letter-spacing: 0.06em; color: var(--bridge-title); }
.bridge-subtitle { margin-top: 0.3rem; color: var(--bridge-subtitle); font-size: 0.9rem; }
.bridge-baseline { margin-top: 0.85rem; height: 4px; width: 100%; background: var(--bridge-baseline); }

/* the primary buttons' text - Streamlit always writes it white */
[data-testid="stBaseButton-primary"]:not(:disabled),
[data-testid="stBaseButton-primary"]:not(:disabled) p { color: var(--bridge-on-primary) !important; }
/* deleting a run is red */
[class*="st-key-delete_go_"] [data-testid="stBaseButton-primary"]:not(:disabled) {
    background: var(--bridge-danger) !important; border-color: var(--bridge-danger) !important;
}
[class*="st-key-delete_go_"] [data-testid="stBaseButton-primary"]:not(:disabled),
[class*="st-key-delete_go_"] [data-testid="stBaseButton-primary"]:not(:disabled) p {
    color: #FFFFFF !important;
}

/* the side panel: AOMMM on top, the page list, the two companies at the foot */
[data-testid="stSidebarHeader"] img { height: 2.7rem; max-width: 100%; }
[data-testid="stNavSectionHeader"] { text-transform: uppercase; letter-spacing: 0.08em;
                                     font-size: 0.72rem; }
[data-testid="stSidebarNavLink"][aria-current="page"] { box-shadow: inset 3px 0 0 var(--bridge-accent); }
/* the two companies: in the flow after the status (they never cover a
   button), held at the foot of the panel when it scrolls; each logo is a
   button that switches the viewer's page to that company's theme */
.st-key-bridge_cobrand {
    position: sticky; bottom: 0; z-index: 1; margin-top: 1.4rem;
    padding: 0.85rem 0.25rem 0.9rem; background: var(--bridge-sidebar-bg);
    border-top: 1px solid var(--bridge-rule); flex-wrap: nowrap;
}
.st-key-bridge_cobrand [data-testid="stMarkdownContainer"] p { margin: 0; }
.st-key-bridge_brand_haleon button, .st-key-bridge_brand_capgemini button {
    width: 5.6rem; min-height: 0; padding: 0; border: none; border-radius: 0;
    background: transparent no-repeat center / contain; cursor: pointer;
}
.st-key-bridge_brand_haleon button p, .st-key-bridge_brand_capgemini button p {
    position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0);
}
.st-key-bridge_brand_haleon button:hover, .st-key-bridge_brand_capgemini button:hover {
    opacity: 0.85;
}
.st-key-bridge_brand_haleon button:focus-visible, .st-key-bridge_brand_capgemini button:focus-visible {
    outline: 2px solid var(--bridge-accent-ink); outline-offset: 3px;
}
.bridge-cobrand-rule { display: block; width: 1px; height: 1.4rem; background: var(--bridge-rule); }

/* Runs and results: the counter, and the list fading in on every new choice
   (the list mounts under the other of two keys, so the animation restarts) */
.bridge-count {
    display: inline-block; padding: 0.12rem 0.6rem; margin-right: 0.6rem;
    border-radius: 999px; font-size: 0.8rem; font-weight: 700;
    background: var(--bridge-chip-bg); color: var(--bridge-chip-fg);
}
.bridge-count-note { color: var(--bridge-subtitle); font-size: 0.85rem; }
/* room above the table for its hover toolbar (it would cover Refresh) */
.st-key-rf_list_a [data-testid="stDataFrame"],
.st-key-rf_list_b [data-testid="stDataFrame"] { margin-top: 1.1rem; }
.st-key-rf_list_a { animation: bridge-in-a 0.32s cubic-bezier(0.2, 0.7, 0.2, 1) both; }
.st-key-rf_list_b { animation: bridge-in-b 0.32s cubic-bezier(0.2, 0.7, 0.2, 1) both; }
@keyframes bridge-in-a { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes bridge-in-b { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
    .st-key-rf_list_a, .st-key-rf_list_b, .bridge-cluster--busy .bridge-cluster-dot { animation: none; }
}

/* No grey flash while the page refreshes: Streamlit dims "stale" elements
   (element containers, expander and tab headers) while a rerun is in flight. */
[data-stale="true"],
[data-testid="stExpander"] summary,
[data-baseweb="tab-list"],
[data-baseweb="tab"] { opacity: 1 !important; transition: none !important; }

/* The contribution tree: each pillar is a full-width button with its + / -
   on the left - read like a list, not a button bar. */
[class*="st-key-ctree_"] button { justify-content: flex-start; text-align: left; }
[class*="st-key-ctree_"] button p { text-align: left; }

/* No "Running... Stop" badge on every click - the slow steps show their own
   spinner. */
[data-testid="stStatusWidget"] { visibility: hidden; }
"""

# the company's own marks on top of the common rules
_HALEON = """
/* Haleon: page titles carry the green bar of the E (as Haleon's slides do) */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] h3:not([data-testid="stExpander"] h3)::before {
    content: ""; display: inline-block; width: 0.85em; height: 0.3em; margin-right: 0.55em;
    vertical-align: 0.2em; background: var(--bridge-accent);
}
"""
_CAPGEMINI = """
/* Capgemini: page titles carry a Vibrant Blue rule */
[data-testid="stMain"] [data-testid="stMarkdownContainer"] h3:not([data-testid="stExpander"] h3) {
    border-left: 3px solid var(--bridge-accent); padding-left: 0.6rem;
}
"""


def _pairs(t) -> dict:
    """The CSS variables for one company and one mode."""
    dark = bool(t.get("dark"))
    status = ({"good": "#3DD68C", "busy": "#F0B429", "off": "#8D979D"} if dark else
              {"good": "#1E8E3E", "busy": "#C98500", "off": "#8D979D"})
    baseline = (f"linear-gradient(90deg, {t['accent']} 0 7rem, {t['rule']} 7rem 100%)"
                if t["key"] == "haleon" else
                f"linear-gradient(90deg, #0070AD 0 6rem, {t['accent']} 6rem 11rem, "
                f"{t['rule']} 11rem 100%)")
    ink = "#F2F2F2" if dark else "#1B1819"
    return {"toggle-icon": brand.icon_url(brand.SUN_SVG if dark else brand.MOON_SVG),
            "accent": t["accent"], "accent-ink": t["accent_ink"], "title": t["title"],
            "subtitle": t["subtitle"], "rule": t["rule"], "on-primary": t["on_primary"],
            "chip-bg": t["chip_bg"], "chip-fg": t["chip_fg"], "danger": t["danger"],
            "sidebar-bg": t["sidebar_bg"], "surface": t["surface"], "ink": ink,
            "baseline": baseline, "good": status["good"], "busy": status["busy"],
            "off": status["off"]}


def _block(selector, pairs) -> str:
    return selector + " {\n" + "\n".join(f"    --bridge-{k}: {v};"
                                          for k, v in pairs.items()) + "\n}\n"


def _variables(t) -> str:
    """The server's guess (the viewer's mode as the app last knew it), then
    each mode's variables under the page's light / dark mark."""
    which = t["key"]
    return (_block(":root", _pairs(t))
            + "".join(_block(f':root[data-bridge-mode="{kind}"]',
                             _pairs(brand.tokens(which, kind)))
                      for kind in ("light", "dark")))


def _aommm_rule(dark_panel: bool, scope: str) -> str:
    """AOMMM blends into a light panel (its file has a white ground) and sits
    on a small white card on a dark one."""
    logo = f'{scope}[data-testid="stSidebarHeader"] img'
    if dark_panel:
        return (f"{logo} {{ background: #FFFFFF; padding: 0.2rem 0.45rem; "
                "border-radius: 0.4rem; mix-blend-mode: normal; }\n")
    return f"{logo} {{ mix-blend-mode: multiply; background: none; padding: 0; }}\n"


def _logo_rules(t) -> str:
    """AOMMM for the server's guess, then for each mode under the page's
    light / dark mark; then the company logo buttons (src/brand.py)."""
    which = t["key"]
    out = _aommm_rule(bool(t["sidebar_dark"]), "")
    for kind in ("light", "dark"):
        out += _aommm_rule(bool(brand.tokens(which, kind)["sidebar_dark"]),
                           f':root[data-bridge-mode="{kind}"] ')
    return out + brand.logo_css(which)


def global_css() -> str:
    """The whole style sheet for the company the viewer sees - both modes."""
    t = brand.tokens()
    own = _HALEON if t["key"] == "haleon" else _CAPGEMINI
    return _variables(t) + _logo_rules(t) + _COMMON + own


def inject_global_styles():
    st.markdown(f"<style>{global_css()}</style>", unsafe_allow_html=True)
