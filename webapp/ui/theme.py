"""Design tokens and static assets (fonts, Lucide icon sprite, stylesheet) for the redesigned UI.

Tokens live as CSS custom properties in webapp/static/automateny.css. Icons are Lucide SVG symbols in
webapp/static/icons.svg, used through Quasar's `svguse:` icon names so every q-icon/q-btn accepts them.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from nicegui import app, ui

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ASSETS_URL = "/assets"


def _version(name: str) -> str:
    """Content hash used as a cache-busting query string."""
    return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]


CSS_URL = f"{ASSETS_URL}/automateny.css?v={_version('automateny.css')}"
SPRITE_URL = f"{ASSETS_URL}/icons.svg?v={_version('icons.svg')}"


# Quasar brand palette = the CSS tokens (accent, failed, passed, warning). Mirrors automateny.css :root.
BRAND = {
    "primary": "#2851E6", "secondary": "#344054", "accent": "#2851E6", "dark": "#0E1525",
    "positive": "#157043", "negative": "#B42318", "info": "#1F3EAB", "warning": "#B25E00",
}


def register_assets() -> None:
    """Serve webapp/static and set app-wide component defaults (call before the first page is built)."""
    app.add_static_files(ASSETS_URL, STATIC_DIR, max_cache_age=3600)
    select_props = (f'outlined dense options-dense dropdown-icon="{icon("chevron-down")}" '
                    f'clear-icon="{icon("circle-x")}" popup-content-class="any-menu"')
    ui.select.default_props(select_props)
    for element in (ui.input, ui.number, ui.textarea, ui.input_chips):
        element.default_props(f'outlined dense clear-icon="{icon("circle-x")}"')
    ui.button.default_props("unelevated no-caps")
    ui.expansion.default_props(f'expand-icon="{icon("chevron-down")}"')


def apply_brand() -> None:
    """Per page: Quasar colour palette from the tokens."""
    ui.colors(**BRAND)


def add_head() -> None:
    """Per page: stylesheet and the font preloads that are visible above the fold."""
    preload = "".join(
        f'<link rel="preload" href="{ASSETS_URL}/fonts/{f}.woff2" as="font" type="font/woff2" crossorigin>'
        for f in ("ibm-plex-sans-latin-500-normal", "ibm-plex-sans-latin-600-normal", "ibm-plex-mono-latin-400-normal")
    )
    ui.add_head_html(f'{preload}<link rel="stylesheet" href="{CSS_URL}">')


def icon(name: str) -> str:
    """Quasar icon name for a Lucide symbol, e.g. ui.icon(icon('play')) or ui.button(icon=icon('save'))."""
    return f"svguse:{SPRITE_URL}#{name}|0 0 24 24"


def svg(name: str, cls: str = "") -> str:
    """Inline <svg><use></svg> markup for Vue slot templates (decorative, aria-hidden)."""
    return (f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            f'<use href="{SPRITE_URL}#{name}"></use></svg>')
