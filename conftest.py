import pytest
import os
import shutil

from dotenv import load_dotenv
from utils.workspace import resolve_workspace_dir, resolve_dotenv_path


def _is_true(value):
    return str(value or "").strip().lower() in ("true", "1", "yes")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # Clear old Allure results once per session when del_allure=true.
    # Skip on xdist workers so parallel workers don't delete each other's results.
    if hasattr(config, "workerinput"):
        return

    dotenv_path = resolve_dotenv_path(resolve_workspace_dir())
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path, override=False)

    results_dir = config.getoption("allure_report_dir", default=None)
    if not results_dir or not _is_true(os.getenv("del_allure")):
        return

    os.makedirs(results_dir, exist_ok=True)
    for entry in os.listdir(results_dir):
        entry_path = os.path.join(results_dir, entry)
        if os.path.isdir(entry_path):
            shutil.rmtree(entry_path, ignore_errors=True)
        else:
            try:
                os.remove(entry_path)
            except OSError:
                pass


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
   # Hook to access test result
   outcome = yield
   rep = outcome.get_result()
   if rep.when == "call" and rep.failed:
       # Only take screenshot on test failure
       page = item.funcargs.get("page", None)
       if page:
           screenshot_dir = os.path.join("screenshots", item.nodeid.replace("::", "_"))
           os.makedirs(os.path.dirname(screenshot_dir), exist_ok=True)
           page.screenshot(path=f"{screenshot_dir}.png", full_page=True)
