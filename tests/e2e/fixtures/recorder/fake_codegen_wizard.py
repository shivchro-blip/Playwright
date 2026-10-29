"""Stands in for `playwright codegen` on the wizard fixture page: a three-screen flow written the way codegen
writes it when started with --test-id-attribute data-attribute-id."""
import sys
from pathlib import Path

args = sys.argv[1:]
assert args[0] == "codegen", args
assert args[args.index("--test-id-attribute") + 1] == "data-attribute-id", args
out = Path(args[args.index("-o") + 1])
url = args[-1]
out.write_text(f'''from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("{url}")
    page.get_by_test_id("LSI_DEMO_BusName").locator("input").click()
    page.get_by_test_id("LSI_DEMO_BusName").locator("input").fill("Acme Widgets")
    page.get_by_test_id("LSI_DEMO_HomeBased").get_by_role("button", name="No").click()
    page.get_by_role("button", name="Save & Continue").click()
    page.get_by_test_id("LSI_DEMO_Zip").locator("input").fill("12206")
    page.get_by_test_id("LSI_DEMO_Taxed").get_by_role("button", name="Yes").click()
    page.get_by_role("button", name="Save & Continue").click()
    page.get_by_role("button", name="Submit Application").click()

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
''', encoding="utf-8")
