"""Plays the person recording: fills the application on recording.html the way someone would."""


def drive(page):
    page.get_by_label("First name").fill("Ann")
    page.get_by_label("Last name").fill("Lee")
    page.get_by_label("Name", exact=True).fill("Lee Bakery")
    page.get_by_placeholder("Email address").fill("ann@example.com")
    page.get_by_test_id("phone-number").fill("5185550100")
    page.get_by_label("State").select_option("TX")
    page.get_by_label("I agree to the terms").check()
    page.get_by_label("Contact me by phone").check()
    page.get_by_label("Password").fill("hunter2")
    page.locator("#billing button").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Finish").click()
    page.wait_for_function("document.title === 'Done'")
