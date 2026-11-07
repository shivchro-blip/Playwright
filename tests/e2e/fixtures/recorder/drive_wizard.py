"""Plays the person recording the three-screen wizard on wizard.html."""


def drive(page):
    page.get_by_label("Business name").fill("Lee Bakery")
    page.locator("[data-attribute-id='LSI_DEMO_HomeBased']").get_by_role("button", name="No").click()
    page.locator("#s1").get_by_role("button", name="Save & Continue").click()
    page.get_by_label("Zip").fill("12207")
    page.locator("[data-attribute-id='LSI_DEMO_Taxed']").get_by_role("button", name="Yes").click()
    page.locator("#s2").get_by_role("button", name="Save & Continue").click()
    page.get_by_role("button", name="Submit Application").click()
    page.get_by_text("Application received").wait_for()
