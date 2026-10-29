"""AI locator suggestions are verified before they are shown: on the saved DOM snapshot (no site visited)."""
from __future__ import annotations

from pathlib import Path

from webapp.services.locator_check import check_locator, count_on_html

SITE = Path(__file__).resolve().parent / "fixtures" / "site" / "index.html"


def test_counts_on_saved_html_and_live_capture(site_url):
    html = SITE.read_text(encoding="utf-8")
    assert count_on_html(html, [{"locator": "#q"}, {"label": "Notes"}, {"role": "textbox"},
                                {"locator": "input", "index": 1}, {"locator": "##bad["}, {}])[:4] == [1, 1, 3, 1]
    live = check_locator(f"{site_url}/index.html", {"locator": "#gone"}, capture_html=True)
    assert live.ok and live.count == 0 and 'id="q"' in live.html
    assert check_locator(f"{site_url}/index.html", {"locator": "#q"}).html == ""  # only when asked
