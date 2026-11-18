"""typeKeys: a box whose page reacts only to key presses (a split SSN combined on keyup) needs typing, not fill."""
import asyncio

from playwright.async_api import async_playwright

from utils import executables

PAGE = """<input id="a" maxlength="3"> - <input id="b" maxlength="2"> - <input id="c" maxlength="4">
<input type="hidden" id="all" value="">
<script>
 const parts = ['a','b','c'].map(i => document.getElementById(i));
 parts.forEach(p => p.addEventListener('keyup', () => {   // the page combines the boxes on keyup only
   document.getElementById('all').value = parts.map(x => x.value).join('-'); }));
</script>"""


class Ctx(dict):
    def get(self, key):
        return super().get(key)


async def _hidden_after(type_keys: bool) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(PAGE)
        for box, value in (("a", "123"), ("b", "45"), ("c", "6789")):
            action = {"value": value, **({"typeKeys": True} if type_keys else {})}
            await executables.fillAction(page, Ctx(), page.locator(f"#{box}"), action, box)
        hidden = await page.locator("#all").input_value()
        await browser.close()
        return hidden


def test_fill_alone_does_not_reach_a_page_that_listens_for_keys():
    assert asyncio.run(_hidden_after(False)) == ""


def test_typeKeys_types_the_value_so_the_page_sees_it():
    assert asyncio.run(_hidden_after(True)) == "123-45-6789"
