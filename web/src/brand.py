"""The app's look: which company's theme is shown, the logos, light / dark.

BRIDGE is co-branded with HALEON and CAPGEMINI. Each company's theme is one
file in web/themes/ (haleon.toml, capgemini.toml): Streamlit's own colours,
fonts and corners, in light and dark, each with its side panel.

* The DEPLOYMENT's company - shown to anyone who has not chosen - is ONE
  setting, Streamlit's `theme.base`: app.yml's STREAMLIT_THEME_BASE on
  Databricks (.streamlit/config.toml on a laptop). Haleon by default.
* EACH VIEWER can switch: the two logos at the foot of the side panel are
  buttons - press Capgemini's and the app turns Capgemini, for that viewer
  only, at once and without a redeploy; press Haleon's to go back. The choice
  is remembered for that person (by login e-mail) while the app runs.
* Light / dark: the button in the top bar (render_theme_toggle).

How a viewer gets another company's theme: Streamlit sends every session its
theme at the start of each run (the NewSession message), built from the
server's config - one theme for everyone. `_install_session_themes` wraps
that one method: for a session that chose the other company, the theme in
the message is refilled from that company's file, by Streamlit's own code
(`_populate_theme_msg`, run against that file instead of the config). The
browser applies a changed theme at once and keeps the person's light / dark
choice. Nothing else of Streamlit is touched; should a future Streamlit
change that method, the hook stays out of the way (the page keeps the
deployment's theme, the logos and accents still follow the choice).

What Streamlit's theme cannot reach follows the same choice: the accents in
the page's CSS (src/styles.py), the logo buttons and the charts' font.

The side panel carries the product at the top - AOMMM, "Always-on MMM"
(assest/aommm.png, which the team copies in). The companies' logos are their
own (web/brand/, made from snapshots/company theme/), with white copies for
dark panels. Nothing here changes what the app does.
"""
import base64
import collections
import functools
import os
import threading

import streamlit as st

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_DIR = os.path.join(WEB_DIR, "brand")
AOMMM_LOGO = os.path.join(WEB_DIR, "assest", "aommm.png")
DEFAULT = "haleon"
CHOICE_KEY = "bridge_brand"          # session_state: the company this viewer chose

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
# the logo buttons' height in the panel (each logo's own proportions)
LOGO_HEIGHT = {"haleon": "0.85rem", "capgemini": "1.3rem"}


# --------------------------------------------------------------------------- #
# which company: the deployment's, or the viewer's own choice
# --------------------------------------------------------------------------- #
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
def deployed_key() -> str:
    """"haleon" or "capgemini" - the deployment's company (theme.base): the
    theme Streamlit itself was started with."""
    chosen = os.path.basename(_theme_setting().replace("\\", "/")).lower()
    for name, spec in BRANDS.items():
        if chosen == os.path.basename(spec["theme_file"]):
            return name
    return DEFAULT


def key() -> str:
    """The company this viewer sees: their own choice, else the deployment's."""
    try:
        chosen = st.session_state.get(CHOICE_KEY)
    except Exception:  # noqa: BLE001 - no session (a test, a worker): the default
        chosen = None
    return chosen if chosen in BRANDS else deployed_key()


def mode() -> str:
    """"light" or "dark" - the viewer's theme (light while unknown)."""
    theme = getattr(getattr(st, "context", None), "theme", None)
    kind = getattr(theme, "type", None)
    if kind is None and isinstance(theme, dict):
        kind = theme.get("type")
    return "dark" if kind == "dark" else "light"


def tokens(which=None, kind=None) -> dict:
    """A company's accents for a mode (default: this viewer's company and
    mode), with its name, font and partner."""
    which = which or key()
    spec = BRANDS[which]
    kind = kind or mode()
    out = dict(spec[kind])
    out.update(name=spec["name"], font=spec["font"], key=which,
               partner=spec["partner"], dark=kind == "dark")
    return out


def panel_kinds(which=None) -> set:
    """Which side panels the company's theme has across light and dark:
    {"light"}, {"dark"} or both."""
    spec = BRANDS[which or key()]
    return {"dark" if spec[kind]["sidebar_dark"] else "light" for kind in ("light", "dark")}


# the company each live session chose (the session-theme hook reads it), and
# each person's last choice (a new session of theirs starts with it)
_LOCK = threading.Lock()
SESSION_BRAND = collections.OrderedDict()      # session id -> company
BRAND_BY_EMAIL = {}                            # login e-mail -> company
_MAX_SESSIONS = 2000


def _session_id():
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        return ctx.session_id if ctx else None
    except Exception:  # noqa: BLE001 - no runtime (tests)
        return None


def _remember(sid, which):
    if not sid:
        return
    with _LOCK:
        SESSION_BRAND[sid] = which
        SESSION_BRAND.move_to_end(sid)
        while len(SESSION_BRAND) > _MAX_SESSIONS:
            SESSION_BRAND.popitem(last=False)


def _person(email) -> str:
    """A login e-mail to remember a choice by ("" for none - e.g. a laptop
    run, where nobody is signed in)."""
    email = str(email or "").strip().lower()
    return email if "@" in email else ""


def choose(which, email=""):
    """This viewer chose `which`: from the next run on, the page is that
    company's (the caller reruns)."""
    if which not in BRANDS:
        return
    st.session_state[CHOICE_KEY] = which
    _remember(_session_id(), which)
    if _person(email):
        with _LOCK:
            BRAND_BY_EMAIL[_person(email)] = which


def sync_session(email="") -> bool:
    """At the top of every run: a new session starts with the person's last
    choice. True when this run's theme (sent before the script started) was
    another company's - the caller then reruns once, so the browser gets the
    right one."""
    ss = st.session_state
    if CHOICE_KEY not in ss:
        with _LOCK:
            remembered = BRAND_BY_EMAIL.get(_person(email)) if _person(email) else None
        ss[CHOICE_KEY] = remembered if remembered in BRANDS else deployed_key()
    want = ss[CHOICE_KEY] if ss[CHOICE_KEY] in BRANDS else deployed_key()
    sid = _session_id()
    if not sid:
        return False
    with _LOCK:
        sent = SESSION_BRAND.get(sid, deployed_key())
    _remember(sid, want)
    return sent != want and _HOOK["installed"]


# --------------------------------------------------------------------------- #
# the session-theme hook: another company's theme for the sessions that chose it
# --------------------------------------------------------------------------- #
_HOOK = {"installed": False, "error": ""}


def theme_sections(which) -> dict:
    """A company's theme file as Streamlit's config sections ("theme",
    "theme.light", "theme.dark.sidebar", ...) - checked by Streamlit's own
    theme-file loader, as at start-up."""
    from streamlit import config, config_util
    path = os.path.join(WEB_DIR, BRANDS[which]["theme_file"])
    content = config_util._load_theme_file(path, config._config_options_template)
    theme = dict(content.get("theme") or {})
    out = {"theme": {k: v for k, v in theme.items() if not isinstance(v, dict)}}
    for sub in ("light", "dark", "sidebar"):
        block = dict(theme.get(sub) or {})
        out[f"theme.{sub}"] = {k: v for k, v in block.items() if not isinstance(v, dict)}
        if sub in ("light", "dark"):
            out[f"theme.{sub}.sidebar"] = dict(block.get("sidebar") or {})
    return out


class _ThemeConfig:
    """Streamlit's config, but the theme sections answer from one company's
    file - for a private copy of Streamlit's theme filler."""

    def __init__(self, sections, real):
        self._sections, self._real = sections, real

    def get_options_for_section(self, section):
        return dict(self._sections.get(section) or {})

    def __getattr__(self, name):
        return getattr(self._real, name)


@functools.lru_cache(maxsize=4)
def _filler(which):
    """Streamlit's own `_populate_theme_msg`, compiled once more with `config`
    answering from `which`'s theme file (its own function object: the real
    one, used by every other session, is untouched)."""
    import inspect
    import textwrap

    from streamlit import config
    from streamlit.runtime import app_session
    source = textwrap.dedent(inspect.getsource(app_session._populate_theme_msg))
    namespace = dict(vars(app_session))
    namespace["config"] = _ThemeConfig(theme_sections(which), config)
    exec(compile(source, f"<bridge theme {which}>", "exec"), namespace)  # noqa: S102
    return namespace["_populate_theme_msg"]


def fill_theme(custom_theme, which):
    """Refill a NewSession message's theme with `which`'s theme file - the
    same sections, in the same order, as Streamlit fills it from config."""
    fill = _filler(which)
    custom_theme.Clear()
    fill(custom_theme, "theme")
    fill(custom_theme.light, "theme.light")
    fill(custom_theme.dark, "theme.dark")
    fill(custom_theme.sidebar, "theme.sidebar")
    fill(custom_theme.light.sidebar, "theme.light.sidebar")
    fill(custom_theme.dark.sidebar, "theme.dark.sidebar")


def _install_session_themes():
    """Wrap AppSession._create_new_session_message once per process: a
    session that chose another company than the deployment's gets that
    company's theme in its NewSession message."""
    if _HOOK["installed"]:
        return
    try:
        from streamlit.runtime.app_session import AppSession
        original = AppSession._create_new_session_message
    except Exception as e:  # noqa: BLE001 - no Streamlit runtime here (tests)
        _HOOK["error"] = str(e)
        return
    if getattr(original, "_bridge", False):
        _HOOK["installed"] = True
        return

    def create_new_session_message(self, *args, **kwargs):
        msg = original(self, *args, **kwargs)
        try:
            with _LOCK:
                which = SESSION_BRAND.get(self.id)
            if which and which != deployed_key():
                fill_theme(msg.new_session.custom_theme, which)
        except Exception as e:  # noqa: BLE001 - the deployment's theme, never a crash
            _HOOK["error"] = f"{type(e).__name__}: {e}"
        return msg

    create_new_session_message._bridge = True
    AppSession._create_new_session_message = create_new_session_message
    _HOOK["installed"] = True


_install_session_themes()


# --------------------------------------------------------------------------- #
# the logos
# --------------------------------------------------------------------------- #
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


def logo_order() -> tuple:
    """The two companies in the panel: the deployment's first - a fixed order,
    so the logos do not jump when a viewer switches."""
    first = deployed_key()
    return first, BRANDS[first]["partner"]


def render_cobrand(email=""):
    """The two companies at the foot of the side panel - each logo a button
    that switches this viewer's page to that company's theme (the shown one
    is underlined). The logo itself is the button's picture (src/styles.py)."""
    current = key()
    with st.container(key="bridge_cobrand", horizontal=True, vertical_alignment="center",
                      horizontal_alignment="center", gap="medium"):
        for i, company in enumerate(logo_order()):
            if i:
                st.markdown('<span class="bridge-cobrand-rule" aria-hidden="true"></span>',
                            unsafe_allow_html=True)
            name = BRANDS[company]["name"]
            shown = company == current
            if st.button(name, key=f"bridge_brand_{company}", type="tertiary") and not shown:
                choose(company, email)
                st.rerun()


def logo_css(which=None) -> str:
    """The logo buttons: each company's logo as its button's picture - the
    colour version on a light panel, the white one on a dark panel (for the
    shown company's panel in each mode) - and the shown one underlined."""
    which = which or key()
    out = []
    for company in BRANDS:
        sel = f".st-key-bridge_brand_{company} button"
        light, dark = _logo_uri(company, False), _logo_uri(company, True)
        out.append(f"{sel} {{ height: {LOGO_HEIGHT[company]}; }}")
        for scope, kind in (("", mode()), (':root[data-bridge-mode="light"] ', "light"),
                            (':root[data-bridge-mode="dark"] ', "dark")):
            uri = dark if BRANDS[which][kind]["sidebar_dark"] else light
            if uri:
                out.append(f'{scope}{sel} {{ background-image: url("{uri}"); }}')
    out.append(f".st-key-bridge_brand_{which} button {{ box-shadow: 0 2px 0 0 var(--bridge-accent); }}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# the light / dark button (top bar)
# --------------------------------------------------------------------------- #
# the button's icons, as CSS masks (src/styles.py: they follow the page's light /
# dark mark) - st.html's sanitiser drops SVG, and drops a whole script whose text
# looks like markup, so neither the button nor its script carries any
_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
        "stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
        "{}</svg>")
MOON_SVG = _SVG.format("<path d='M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z'/>")
SUN_SVG = _SVG.format("<circle cx='12' cy='12' r='4'/><path d='M12 2v2M12 20v2M4.93 4.93l1.41 "
                      "1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 "
                      "6.34l1.41-1.41'/>")


def icon_url(svg) -> str:
    """An SVG as a CSS url() - percent-encoded, so no markup is left in it."""
    from urllib.parse import quote
    return 'url("data:image/svg+xml,' + quote(svg, safe="") + '")'

# Streamlit has no Python call for the viewer's light / dark, and its own
# switch is in the Settings dialog of the ⋮ menu - which the page hides (the
# cluster sits there). So the button works that same switch, out of sight: it
# opens the hidden menu, Settings, picks Light or Dark and closes the dialog -
# live, the session and everything on the pages intact, and Streamlit
# remembers the choice in the browser. Then it marks the page light or dark
# (the CSS shows that mode's accents and logos at once) and reruns the app, so
# the charts are drawn again for the new mode (Streamlit does not rerun on a
# theme change). It needs the menu in the page: client.toolbarMode "viewer"
# (app.yml), not "minimal".
_TOGGLE_JS = r"""
(() => {
  const app = () => document.querySelector('.stApp') || document.body;
  const isDark = () => {
    const m = (getComputedStyle(app()).backgroundColor || '').match(/[0-9.]+/g);
    if (!m) return false;
    const [r, g, b] = m.map(Number);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128;
  };
  const paint = () => {
    const dark = isDark();
    document.documentElement.setAttribute('data-bridge-mode', dark ? 'dark' : 'light');
    document.querySelectorAll('.bridge-theme-toggle').forEach((b) => {
      const label = dark ? 'Switch to light mode' : 'Switch to dark mode';
      b.setAttribute('aria-label', label);
      b.title = label;
    });
  };
  const wait = (test, ms) => new Promise((resolve, reject) => {
    const t0 = performance.now();
    (function poll() {
      let v = null;
      try { v = test(); } catch (e) { v = null; }
      if (v) return resolve(v);
      if (performance.now() - t0 > (ms || 4000)) return reject(new Error('timeout'));
      requestAnimationFrame(poll);
    })();
  });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // a menu item's own label (an item can also show its shortcut, "Rerun  R")
  const label = (o) => ((o.querySelector('span') || o).innerText || '').trim();
  const menuItem = async (text) => {
    document.querySelector('[data-testid="stMainMenu"] button').click();
    const item = await wait(() => [...document.querySelectorAll(
      '[data-testid="stMainMenuList"] [role="option"]')].find((o) => label(o) === text));
    item.click();
  };
  const escape = () => document.dispatchEvent(
    new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  const setTheme = async (want) => {
    await menuItem('Settings');
    const box = await wait(() => document.querySelector(
      '[role="dialog"] [data-testid="stSelectbox"] [data-baseweb="select"] > div'));
    box.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
    box.click();
    const opt = await wait(() => [...document.querySelectorAll('[role="option"]')]
                                   .find((o) => label(o) === want));
    opt.click();
    await sleep(120);
    const close = document.querySelector('[role="dialog"] button[aria-label="Close"]');
    if (close) close.click(); else escape();
    await wait(() => !document.querySelector('[role="dialog"]'), 2000).catch(() => null);
  };
  let busy = false;
  const toggle = async () => {
    if (busy) return;
    busy = true;
    // the menu, the dialog and its dropdown stay out of sight the whole time
    const hide = document.createElement('style');
    hide.textContent = '[data-baseweb="popover"], [data-baseweb="modal"], [role="dialog"]'
                       + ' { opacity: 0 !important; }';
    document.head.appendChild(hide);
    try {
      await setTheme(isDark() ? 'Light' : 'Dark');
      await sleep(80);
      paint();
      await sleep(150);
      await menuItem('Rerun');            // the charts, drawn again for the new mode
    } catch (e) {
      console.warn('BRIDGE: the light / dark switch failed', e);
      escape();
    } finally {
      setTimeout(() => hide.remove(), 300);
      busy = false;
    }
  };
  if (!window.__bridgeThemeToggle) {
    window.__bridgeThemeToggle = true;
    document.addEventListener('click', (e) => {
      const b = e.target && e.target.closest && e.target.closest('.bridge-theme-toggle');
      if (b) { e.preventDefault(); toggle(); }
    });
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)')
        .addEventListener('change', () => setTimeout(paint, 150));
    }
    new MutationObserver(() => paint())
      .observe(app(), {attributes: true, attributeFilter: ['class', 'style']});
  }
  paint();
})();
"""


def toggle_script() -> str:
    """The light / dark button's script (see _TOGGLE_JS). It must not look like
    markup anywhere (a "<" before a letter or "/"): Streamlit's sanitiser would
    drop the whole script."""
    return _TOGGLE_JS.strip()


def render_theme_toggle():
    """The light / dark button, in the top bar (see _TOGGLE_JS); its sun or
    moon is drawn by the CSS."""
    dark = mode() == "dark"
    label = "Switch to light mode" if dark else "Switch to dark mode"
    st.html(f'<button type="button" class="bridge-theme-toggle" aria-label="{label}" '
            f'title="{label}"></button><script>{toggle_script()}</script>',
            width="content", unsafe_allow_javascript=True)
