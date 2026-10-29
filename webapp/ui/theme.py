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


def register_assets() -> None:
    """Serve webapp/static once per app (call before the first page is built)."""
    app.add_static_files(ASSETS_URL, STATIC_DIR, max_cache_age=3600)


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
