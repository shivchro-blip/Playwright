"""Stands in for `playwright codegen` in tests: writes a canned recording to the -o file and exits."""
import sys
from pathlib import Path

args = sys.argv[1:]
assert args[0] == "codegen", args
out = Path(args[args.index("-o") + 1])
url = args[-1]
out.write_text(f'''from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("{url}")
    page.locator("#q").click()
    page.locator("#q").fill("recorded search")
    page.get_by_label("Notes").fill("typed by the recorder")
    page.locator("#phone").fill("5551234567")

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
''', encoding="utf-8")
