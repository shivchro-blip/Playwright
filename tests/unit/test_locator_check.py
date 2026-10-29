"""Locator check helpers that need no browser: page URLs, locator precedence, input validation."""
from __future__ import annotations

import pytest

from webapp.services.locator_check import build_locator, check_locator, page_url


class FakePage:
    """Records the Playwright calls build_locator makes."""

    def __init__(self, chain: str = "page"):
        self.chain = chain

    def __getattr__(self, name):
        def call(*args, **kwargs):
            parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
            return FakePage(f"{self.chain}.{name}({', '.join(parts)})")
        return call


def test_page_url_prefers_path_then_fullpath():
    assert page_url("https://x.test", {"path": "/a"}) == "https://x.test/a"
    assert page_url("https://x.test", {"fullpath": "https://y.test/b"}) == "https://y.test/b"
    assert page_url("https://x.test", {}) == "https://x.test"


@pytest.mark.parametrize("cfg, expected", [
    ({"locator": "#q"}, "page.locator('#q')"),
    ({"placeholder": "Email", "label": "E"}, "page.get_by_placeholder('Email')"),
    ({"label": "Email"}, "page.get_by_label('Email')"),
    ({"text": "Hi", "locator": "#q", "role": "link"}, "page.locator('#q').get_by_text('Hi')"),
    ({"role": "button", "name": "Save"}, "page.get_by_role('button', name='Save')"),
    ({"role": "row", "locator": "Ann"}, "page.get_by_role('row').get_by_text('Ann', exact=True)"),
    ({"role": "button"}, "page.get_by_role('button')"),
])
def test_build_locator_follows_get_element_precedence(cfg, expected):
    el, desc = build_locator(FakePage(), cfg)
    assert desc == expected and el.chain == expected


@pytest.mark.parametrize("cfg", [{}, {"type": "text"}, {"nested": [{"locator": "#a"}]}])
def test_build_locator_rejects_configs_it_cannot_check(cfg):
    with pytest.raises(ValueError):
        build_locator(FakePage(), cfg)


@pytest.mark.parametrize("url", ["", "file:///etc/passwd", "javascript:alert(1)", "http://"])
def test_check_locator_needs_an_http_url(url):
    result = check_locator(url, {"locator": "#q"})
    assert not result.ok and "http(s)" in result.error
