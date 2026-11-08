"""A Studio session on the local smoke page: pick elements by where they are, run steps on the live page."""
from __future__ import annotations

import asyncio

from webapp.services.studio import StudioError, StudioSession


async def centre(session, selector):
    box = await session.page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_pick_and_run_steps_live(site_url: str):
    async def scenario():
        s = StudioSession()
        await s.start(f"{site_url}/index.html")
        try:
            assert (await s.screenshot()).startswith("data:image/jpeg;base64,")
            search = await s.pick(*await centre(s, "#q"), taken=set())
            assert search.cfg == {"locator": "#q"} and search.type == "text" and search.tag == "input"
            assert search.identity["fingerprint"]["label"] == "Search"
            assert {"locator": "input[name='q']"} in search.identity["locators"]
            assert search.field["identity"] is search.identity

            notes = await s.pick(*await centre(s, "#notes"), taken={search.name})
            assert notes.cfg == {"locator": "#notes"} and notes.name != search.name
            assert await s.pick(5, 5, taken=set()) is None  # the page's margin: nothing to pick

            await s.perform(search.cfg, "fill", "playwright")
            assert await s.page.input_value("#q") == "playwright"
            await s.perform(notes.cfg, "fill", "line one")
            assert await s.page.input_value("#notes") == "line one"
            try:
                await s.perform({"locator": "#nope"}, "click")
                raise AssertionError("a missing element must fail the step")
            except StudioError as e:
                assert "Timeout" in str(e) or "waiting" in str(e)
        finally:
            await s.close()
        assert not s.open

    asyncio.run(scenario())
