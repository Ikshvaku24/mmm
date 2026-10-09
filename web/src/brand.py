"""The app's look: which company's theme leads, and the logos.

BRIDGE is co-branded. HALEON leads by default - the app runs on Haleon's
Databricks, on Haleon's data, for Haleon's modellers - and CAPGEMINI's theme
is the alternative, for a Capgemini-led deployment. Each company's theme is
one file in web/themes/ (haleon.toml, capgemini.toml): Streamlit's own
colours, fonts and corners, in light and dark. Which one leads is ONE
setting, Streamlit's `theme.base`: app.yml's STREAMLIT_THEME_BASE on
Databricks (.streamlit/config.toml when the app runs on a laptop).

This module reads that same setting, so what Streamlit's theme cannot reach
follows it too: a few accents in the page's CSS (src/styles.py - the green
or blue bars, the text on primary buttons, the run counter), the order of
the two company logos, and the charts' font.

The side panel carries the product at the top - AOMMM, "Always-on MMM"
(assest/aommm.png, which the team copies in) - and the two companies at the
bottom, side by side, the lead company first. Their logos are the companies'
own (web/brand/, made from snapshots/company theme/), with white copies for
dark backgrounds. Nothing here changes what the app does.
"""
import base64
import functools
import os

import streamlit as st

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_DIR = os.path.join(WEB_DIR, "brand")
AOMMM_LOGO = os.path.join(WEB_DIR, "assest", "aommm.png")
DEFAULT = "haleon"

# what the theme files cannot say, per company and light / dark mode
BRANDS = {
    "haleon": {
        "name": "Haleon",
        "theme_file": "themes/haleon.toml",
        "partner": "capgemini",
        "font": 'Verdana, Geneva, "DejaVu Sans", sans-serif',
        "light": {"accent": "#30EA03", "accent_ink": "#1B6E00", "title": "#000000",
                  "subtitle": "#5F6A71", "rule": "#D3D7DA", "on_primary": "#30EA03",
                  "chip_bg": "#1B1819", "chip_fg": "#30EA03", "danger": "#C62828",
                  "sidebar_dark": False, "sidebar_bg": "#F3F3F5", "surface": "#FFFFFF"},
        "dark": {"accent": "#30EA03", "accent_ink": "#7DF55C", "title": "#FFFFFF",
                 "subtitle": "#A8B0B5", "rule": "#333F48", "on_primary": "#000000",
                 "chip_bg": "#30EA03", "chip_fg": "#000000", "danger": "#D93A30",
                 "sidebar_dark": True, "sidebar_bg": "#000000", "surface": "#111213"},
    },
    "capgemini": {
        "name": "Capgemini",
        "theme_file": "themes/capgemini.toml",
        "partner": "haleon",
        "font": '"Ubuntu", "Segoe UI", sans-serif',
        "light": {"accent": "#12ABDB", "accent_ink": "#0070AD", "title": "#0070AD",
                  "subtitle": "#535773", "rule": "#D3D5E0", "on_primary": "#FFFFFF",
                  "chip_bg": "#E5F5FB", "chip_fg": "#0070AD", "danger": "#C62828",
                  "sidebar_dark": True, "sidebar_bg": "#272936", "surface": "#FFFFFF"},
        "dark": {"accent": "#12ABDB", "accent_ink": "#7FD3EE", "title": "#12ABDB",
                 "subtitle": "#868AA8", "rule": "#383B4E", "on_primary": "#FFFFFF",
                 "chip_bg": "#0B3A57", "chip_fg": "#E7E8EE", "danger": "#D93A30",
                 "sidebar_dark": True, "sidebar_bg": "#0E0F15", "surface": "#15161E"},
    },
}
# each company's logo: (on a light background, on a dark one) - PNG, because
# Streamlit will not show an SVG data: image (Haleon's PNGs are its SVG, drawn
# by a browser: web/brand/haleon*.svg are the sources)
LOGOS = {"haleon": ("haleon.png", "haleon_white.png"),
         "capgemini": ("capgemini.png", "capgemini_white.png")}


def _theme_setting() -> str:
    """The theme file Streamlit was started with: STREAMLIT_THEME_BASE (app.yml)
    first - it wins over the config file - else .streamlit/config.toml's
    [theme] base. (Streamlit itself replaces theme.base with "light" once it
    has read the file, so it cannot be asked afterwards.)"""
    env = os.environ.get("STREAMLIT_THEME_BASE", "").strip()
    if env:
        return env
    for folder in (os.getcwd(), WEB_DIR):
        path = os.path.join(folder, ".streamlit", "config.toml")
        if not os.path.exists(path):
            continue
        try:
            import toml
            with open(path, encoding="utf-8") as fh:
                return str((toml.load(fh).get("theme") or {}).get("base") or "")
        except Exception:  # noqa: BLE001 - an unreadable file: the default brand
            return ""
    return ""


@functools.lru_cache(maxsize=1)
def key() -> str:
    """"haleon" or "capgemini" - the company whose theme leads."""
    chosen = os.path.basename(_theme_setting().replace("\\", "/")).lower()
    for name, spec in BRANDS.items():
        if chosen == os.path.basename(spec["theme_file"]):
            return name
    return DEFAULT


def mode() -> str:
    """"light" or "dark" - the viewer's theme (light while unknown)."""
    theme = getattr(getattr(st, "context", None), "theme", None)
    kind = getattr(theme, "type", None)
    if kind is None and isinstance(theme, dict):
        kind = theme.get("type")
    return "dark" if kind == "dark" else "light"


def tokens(which=None, kind=None) -> dict:
    """The lead company's accents for the current mode, with its name and font."""
    spec = BRANDS[which or key()]
    kind = kind or mode()
    out = dict(spec[kind])
    out.update(name=spec["name"], font=spec["font"], key=which or key(),
               partner=spec["partner"], dark=kind == "dark")
    return out


@functools.lru_cache(maxsize=8)
def _logo_uri(company: str, dark: bool) -> str:
    """The company's logo as a data: URI (light or dark version)."""
    name = LOGOS[company][1 if dark else 0]
    path = os.path.join(BRAND_DIR, name)
    try:
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{data}"


def render_logo():
    """AOMMM - Always-on MMM - at the top of the side panel (and in the corner
    while the panel is closed): assest/aommm.png, when it has been copied in."""
    if os.path.exists(AOMMM_LOGO):
        st.logo(AOMMM_LOGO, size="large")


def render_cobrand():
    """The two companies, side by side at the foot of the side panel - the
    lead company first, each in its own logo (white on a dark panel)."""
    t = tokens()
    first, second = t["key"], t["partner"]
    imgs = []
    for company in (first, second):
        uri = _logo_uri(company, bool(t["sidebar_dark"]))
        if uri:
            imgs.append(f'<img class="bridge-cobrand-logo bridge-cobrand-logo--{company}" '
                        f'src="{uri}" alt="{BRANDS[company]["name"]}">')
    if not imgs:
        return
    rule = '<span class="bridge-cobrand-rule" aria-hidden="true"></span>'
    st.markdown(f'<div class="bridge-cobrand" role="group" '
                f'aria-label="{" and ".join(BRANDS[c]["name"] for c in (first, second))}">'
                + rule.join(imgs) + "</div>", unsafe_allow_html=True)
