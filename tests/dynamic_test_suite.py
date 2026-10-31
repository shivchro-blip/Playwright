import pytest
import pytest_asyncio
import json
import os
from datetime import datetime
import time
import asyncio
import logging
import tempfile
import random
from playwright.async_api import async_playwright, expect
from pages.dynamic_page import DynamicPage
from utils.test_data_loader import load_test_cases_from_folder, load_all_test_cases
from utils.data_sets import expand as expand_data_sets
from utils.auth_helper import login_if_needed
from utils.errors import addFail, addWarn, allure_step, attach_allure_failure_details
from utils.dictionary import Dictionary
from utils.api_helper import run_api_request, get_token
from utils.context_manager import ContextManager
from utils.workspace import resolve_workspace_dir, resolve_dotenv_path, resolve_systemdata_path
from dotenv import load_dotenv
from allure_commons.types import AttachmentType
import allure
import shutil
import pathlib
import secrets
import sys

reportDescription = ""

def is_frozen_run():
    return bool(getattr(sys, "frozen", False))

def get_pytest_target_args():
    module_name = __name__
    if module_name == "__main__":
        module_name = os.getenv("PYTEST_MODULE", "tests.dynamic_test_suite")
    return ["--pyargs", module_name]


def _normalize_run_tags(raw_value):
    return [tag.strip() for tag in str(raw_value or "").split(",") if tag.strip()]


workspaceDir = resolve_workspace_dir()
cwd = workspaceDir
dotenv_path = resolve_dotenv_path(workspaceDir)
SYSTEMDATA_FILE = resolve_systemdata_path(workspaceDir)

if os.path.exists(dotenv_path):
    # override=False: per-run env vars (runner/web app) win over .env defaults
    load_dotenv(dotenv_path=dotenv_path, override=False)
else:
    print(f"[DynamicSuite] WARNING: .env file not found at {dotenv_path}", flush=True)

# Fallback only - when --alluredir is passed, get_allure_results_dir() uses that instead
ALLURE_RESULTS_DIR = os.getenv("reportsDir") or os.path.join(workspaceDir, "reports", "allure-results")


def get_bool_env(envVariable, default=False):
    envValue = os.getenv(envVariable)
    if envValue is None:
        return default
    return envValue.strip().lower() in ("true", "1", "yes")


# -----------------------------
#  Logging Configuration
# -----------------------------
import logging.handlers

loggerLevelString = os.getenv("TestSuiteLoggerLevel", "INFO").upper()
loggerLevel = getattr(logging, loggerLevelString, logging.INFO)

# Setup comprehensive logging
logger = logging.getLogger(__name__)
logger.setLevel(loggerLevel)

log_dir = os.path.join(workspaceDir, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "automation.log")

file_handler = logging.handlers.RotatingFileHandler(
    log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setLevel(loggerLevel)
file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(loggerLevel)
console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)

if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(console_handler)

logging.getLogger("playwright").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

logger.info(
    "[DynamicSuite] Bootstrap workspace=%s cwd=%s dotenv=%s systemData=%s",
    workspaceDir,
    os.getcwd(),
    dotenv_path,
    SYSTEMDATA_FILE,
)

if not os.path.exists(SYSTEMDATA_FILE):
    raise FileNotFoundError(
        "systemData.json not found for the selected workspace. "
        f"workspace={workspaceDir} systemData={SYSTEMDATA_FILE}"
    )

with open(SYSTEMDATA_FILE, "r", encoding="utf-8") as f:
    login_data = json.load(f)

dictionary = Dictionary()
logger.info("dictionary: %s", dictionary)

logger.info("~~~ Load Test Cases ~~~")
del_allure = get_bool_env("del_allure", False)

recordingConfig = os.getenv("recording", "false").lower()
tracingConfig = os.getenv("tracing", "false").lower()

systemConfig = (os.getenv("systemConfig") or "").strip()
envConfig = (os.getenv("envConfig") or "").strip()
# The test_case fixture reassigns the globals per test; keep the run-level values
RUN_SYSTEM_CONFIG, RUN_ENV_CONFIG = systemConfig, envConfig
runTagsConfig = _normalize_run_tags(os.getenv("runTags"))
slowmoConfig = os.getenv("slowmo") or 0

logger.info("System Config: %s", systemConfig)
logger.info("Env Config: %s", envConfig)
logger.info("Run Tags Config: %s", runTagsConfig)
logger.info("Slowmo Config: %s", slowmoConfig)

browserConfigWidth = int(os.getenv("browserConfigWidth", "1700"))
browserConfigHeight = int(os.getenv("browserConfigHeight", "750"))

browserConfigMaximized = get_bool_env("browserConfigMaximized", True)
headlessFlag = get_bool_env("headlessMode", False)

tests_folder = os.path.join(workspaceDir, "Tests", systemConfig)
test_cases = load_test_cases_from_folder(tests_folder, runTagsConfig, ["applications"], ["dev"])
# Optional narrowing to specific test IDs (web app "Run this test"); empty = all tag matches
runTestIdsConfig = set(_normalize_run_tags(os.getenv("runTestIds")))
if runTestIdsConfig:
    test_cases = [tc for tc in test_cases if tc.get("testCaseID") in runTestIdsConfig]
# A test case with a "dataSet" runs once per set of Data/TestData/<name>.json (or only runDataSet)
test_cases = expand_data_sets(test_cases, workspaceDir, (os.getenv("runDataSet") or "").strip())
selection_error_message = (
    "No runnable tests were selected. "
    f"workspace={workspaceDir} system={systemConfig or '<blank>'} "
    f"env={envConfig or '<blank>'} runTags={runTagsConfig or ['<blank>']} "
    f"tests_folder={tests_folder}"
)

logger.info(
    "[DynamicSuite] Selected %s test(s) from %s using runTags=%s",
    len(test_cases),
    tests_folder,
    runTagsConfig,
)
if not test_cases:
    logger.error("[DynamicSuite] %s", selection_error_message)
logger.info("~~~ Test Cases Loaded ~~~\n~~~ Test Cases Loaded ~~~")

reusableTestCases = {}
screen_config_cache = {}
commonActions = {}

reusableTestCasesLoaded = load_all_test_cases(os.path.join(workspaceDir, "Tests"), "all", ["applications"], ["dev"])
for reusableTestCase in reusableTestCasesLoaded:
    logger.debug("Loaded Reusable Test Case: %s - Steps: %d", reusableTestCase['testCaseID'], len(reusableTestCase['steps']))
    reusableTestCases[reusableTestCase["testCaseID"]] = reusableTestCase

for test_case in test_cases:
    logger.info("Loaded Test Case: %s - Steps: %d", test_case['testCaseID'], len(test_case['steps']))


allureErrorCategories = [
    {
        "name": "Visibility Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*LocatorAssertions.to_be_visible with timeout 30000ms.*"
    },
    {
        "name": "Action Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*action failed.*"
    },
    {
        "name": "Framework Error",
        "matchedStatuses": ["broken"],
        "traceRegex": ".*KeyError.*|.*TypeError.*|.*AttributeError.*"
    },
    {
        "name": "Executable Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Error executing external tool.*"
    },
    {
        "name": "Authentication Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Login failed.*|.*Invalid credentials.*"
    },
    {
        "name": "Assertion on non visible element",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Failed: Failure: confirmtext on Element .* not visible within .* expected to be visible Actual value: <element(s) not found> *"
    },
    {
        "name": "Confirm Text Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Unable to confirm text.*"
    },
    {
        "name": "Fill From Dictionary Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*FillFromDictionary.*"
    },
    {
        "name": "Common Action Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Common Action:.*"
    },
    {
        "name": "Reusable Test Case Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Reusable Test Case:.*"
    },
    {
        "name": "Field does not exist in POM",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Failed to generate Actions from test data - Error: 'NoneType' object has no attribute 'get'.*"
    },
    {
        "name": "Generate Actions Failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Failed to generate Actions.*"
    },
    {
        "name": "Page Object Errors",
        "messageRegex": ".*Page Config.*"
        #"traceRegex": ".*pages/.*\\.py.*"
    },
    {
        "name": "Search Refresh Failure",
        "messageRegex": ".*Failure: searchRefresh failed.*"
    },
    {
        "name": "Element is disabled",
        "messageRegex": ".*Locator.is_disabled: Timeout.*exceeded.*"
    },
    {
        "name": "Failed to Save",
        "messageRegex": ".*Failure: save on Element.*"
    },
    {
        "name": "Locator Resolved to Multiple Elements",
        "messageRegex": ".*Error: strict mode violation: locator(.*) resolved to .* elements.*"
    },
    {
        "name": "Fallback Failure",
        "matchedStatuses": ["failed"]
    }
]

def get_allure_results_dir(config):
    """Directory allure-pytest actually writes to (--alluredir), else the configured fallback."""
    return config.getoption("allure_report_dir", default=None) or ALLURE_RESULTS_DIR


async def write_allure_categories(results_dir):
    try:
        logger.info(f"= Writing Allure Categories to {results_dir}")
        os.makedirs(results_dir, exist_ok=True)
        with open(os.path.join(results_dir, "categories.json"), "w", encoding="utf-8") as f:
            json.dump(allureErrorCategories, f, indent=2)
    except OSError as e:
        logger.error(f"Failed to write Allure categories to {results_dir}: {e}")


async def write_allure_environment(results_dir, system, env):
    try:
        os.makedirs(results_dir, exist_ok=True)
        with open(os.path.join(results_dir, "environment.properties"), "w", encoding="utf-8") as f:
            f.write(f"SYSTEM={system}\n")
            f.write(f"ENV={env}\n")
            f.write(f"BUILD_TIME={datetime.now().isoformat()}\n")
    except OSError as e:
        logger.error(f"Failed to write Allure environment to {results_dir}: {e}")

def build_screen_cache(systemConfig):
    for dirpath, dirnames, filenames in os.walk(os.path.join(workspaceDir, "Pages", systemConfig)):
        for filename in filenames:
            if filename.endswith(".json"):
                screen_name = filename[:-5]  # remove '.json'
                screen_config_cache[screen_name] = os.path.join(dirpath, filename)

def load_common_actions():
    global commonActions
    for dirpath, dirnames, filenames in os.walk(os.path.join(workspaceDir, "Tests", "com")):
        for filename in filenames:
            if filename.endswith(".json"):
                filepath = os.path.join(dirpath, filename)
                logger.info(f"Loading common actions from: {filepath}")
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        file_common_actions = data.get("commonActions", {}) 

                    for action_name, action_data in file_common_actions.items():
                        commonActions[action_name] = action_data
                        logger.debug(f"Loaded action: {action_name}")

                except FileNotFoundError:
                    logger.error(f"File not found: {filepath}")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in file: {filepath}")
                except Exception as e:
                    logger.error(f"Error processing file {filepath}: {e}")

    #logger.info(f"All commonActions: {commonActions}")

build_screen_cache(systemConfig)
load_common_actions()

current_login_state = {"system": None, "base_url": None}

async def loadFromDictionary(dictionaryContext, key):
    return dictionaryContext.get(key)

async def finalize_and_attach_videos(context, test_id: str | None, save_dir: str = "reports/videos"):
    """
    Close each page to finalize its video, copy to a stable location with a UNIQUE name,
    and attach to Allure. Call BEFORE closing context/browser.
    """     

    os.makedirs(save_dir, exist_ok=True)

    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    pid = os.getpid()

    def unique_base(label: str | None):
        ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        ms = int((time.time() % 1) * 1_000_000)  
        rnd = secrets.token_hex(3)               
        parts = [label] if label else []
        parts += [worker, str(pid), f"{ts}-{ms:06d}", rnd]
        return "__".join(parts)

    # Snapshot pages now; closing mutates context.pages
    pages = list(context.pages)

    for idx, p in enumerate(pages, start=1):
        video = getattr(p, "video", None)

        # Close page FIRST so Playwright finalizes the .webm
        try:
            await p.close()
        except Exception:
            pass

        if not video:
            continue

        try:
            src_path = await video.path()  # valid only after close
        except Exception:
            src_path = None

        if not src_path or not os.path.exists(src_path):
            continue

        if os.path.getsize(src_path) <= 0:
            continue

        base = unique_base(test_id)
        filename = f"{base}__page{idx}.webm"
        dest_path = os.path.join(save_dir, filename)

        try:
            shutil.copy2(src_path, dest_path)  # stable copy for artifacts
        except Exception:
            dest_path = src_path  # fallback

        # Attach as WEBM (Chromium default)
        try:
            allure.attach.file(dest_path, name=filename, attachment_type=AttachmentType.WEBM)
        except Exception:
            pass

def mkdir_per_worker(base="reports/videos/raw"):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    d = pathlib.Path(base) / worker
    d.mkdir(parents=True, exist_ok=True)
    return str(d)

async def run_login_pretest(page, browser, dictionaryContext, system_needed):
    sys_env = login_data["systems"].get(system_needed, {}).get(envConfig, {})
    if not sys_env:
        await addFail(f"Missing login data for system '{system_needed}' in env '{envConfig}'")

    base_url        = sys_env.get("base_url")
    logged_in_check = sys_env.get("logged_in_check")   # selector used to verify login success
    login_pre       = sys_env.get("login_pre")         # {"type":"steps","path":"../Tests/<SYSTEM>/auth/login.json"}

    if not base_url or not logged_in_check:
        await addFail(f"Login config requires 'base_url' and 'logged_in_check' for {system_needed}/{envConfig}")

    # go to root; quick short-circuit if already logged in
    await page.goto(base_url)
    try:
        if logged_in_check and await page.locator(logged_in_check).is_visible():
            return base_url
    except Exception:
        pass

    # execute JSON-driven pre-steps (same engine)
    if login_pre and login_pre.get("type") == "steps":
        steps_path = login_pre.get("path")
        if not steps_path:
            await addFail(f"login_pre.steps requires 'path' for {system_needed}/{envConfig}")
        if not os.path.isabs(steps_path):
            normalized_steps_path = steps_path.replace("/", os.sep).replace("\\", os.sep)
            if normalized_steps_path.startswith(f"..{os.sep}"):
                normalized_steps_path = normalized_steps_path.split(os.sep, 1)[1]
            steps_path = os.path.normpath(os.path.join(workspaceDir, normalized_steps_path))
        if not os.path.exists(steps_path):
            await addFail(f"Login pre-test file not found: {steps_path}")

        try:
            with open(steps_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            steps = raw["steps"] if isinstance(raw, dict) and "steps" in raw else raw
        except Exception as e:
            await addFail(f"Failed to load login pre-test JSON: {e}")

        for step in steps:
            screen_name = step["screen"]
            with allure.step(f"- [LOGIN] Screen: {screen_name} -"):
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.2)
                page = await run_single_step(step, page, base_url, browser, system_needed, dictionaryContext)

    else:
        # legacy fallback if present
        try:
            from utils.auth_helper import login_if_needed
            await login_if_needed(page, system_needed, envConfig)
        except Exception as e:
            await addFail(f"Legacy login failed: {e}")

    # verify success
    try:
        await expect(page.locator(logged_in_check)).to_be_visible(timeout=30000)
    except Exception as e:
        await attach_allure_failure_details(page, name="Login Verification Failure")
        await addFail(f"Login pre-test completed but verification failed. Selector: {logged_in_check} | Error: {e}")

    return base_url


async def run_steps(steps, page, browser, dictionaryContext, testSystem, current_login_state, system_needed, parent_override=None):
    global reportDescription
    if current_login_state.get("system") != system_needed:
        logger.info(f"[LOGIN SWITCH] CURRENT = {current_login_state.get('system')} → NEW = {system_needed}")

        system_data = login_data["systems"].get(system_needed, {}).get(envConfig, {})
        if not system_data:
            raise ValueError(f"Missing login data for system '{system_needed}' in env '{envConfig}'")

        build_screen_cache(system_needed)
        base_url = await run_login_pretest(page, browser, dictionaryContext, system_needed)
        #await asyncio.sleep(2)
        current_login_state["system"] = system_needed
        current_login_state["base_url"] = base_url
    else:
        base_url = current_login_state.get("base_url")

    try:
        await asyncio.sleep(5)
        await get_token(page, dictionaryContext, "token", "authToken")
    except Exception:
        # Normal for systems without a token in storage (e.g. public sites)
        logger.warning("No auth token found in page storage - continuing without one")


    for step in steps:
        if "commonAction" in step:
            commonAction = step["commonAction"]
            allure.dynamic.feature(f"Common Action: {commonAction}")
            reportDescription += f"\nCommon Action: {commonAction}"
            logger.info("Executing Common Action: %s", commonAction)

            with allure.step(f"- Common Action: {commonAction} -"):
                comActSteps = commonActions.get(commonAction, {}).get("steps", [])
                if not comActSteps:
                    logger.error("[ERROR] Common Action '%s' not found!", commonAction)
                    await attach_allure_failure_details(page, step, name="CommonActionFailure", extra_label="Common Action Not Found!")
                    await addFail(f"Common Action: {commonAction} not found!")

                override = step.get("override", parent_override)
                oScreen, oField, oData = (override.split('|') if override else (None, None, None))

                loop = step.get("loop", 1)
                if isinstance(loop, str) and loop.startswith("DICT."):
                    dict_key = loop.split('.', 1)[1].strip()
                    loop = await loadFromDictionary(dictionaryContext, dict_key)

                for _ in range(loop):
                    for comStep in comActSteps:
                        if oScreen and comStep.get("screen") == oScreen:
                            comStep["formData"][oField] = oData
                            logger.info("Overrode field: %s", comStep)
                        with allure.step(f"- Screen: {comStep.get('screen')} -"):
                            page = await run_single_step(comStep, page, base_url, browser, system_needed, dictionaryContext)

        elif "testCase" in step:
            tcID = step["testCase"]
            allure.dynamic.feature(f"Reused Test Case: {tcID}")
            reportDescription += f"\n(Reusing Test Case: {tcID})"
            logger.info("Reusing Test Case: %s", tcID)
            with allure.step(f"** Reusing Test Case: {tcID} **"):
                nested_tc = reusableTestCases.get(tcID)
                if not nested_tc:
                    await attach_allure_failure_details(page, step, name="ReusableTestCaseFailure", extra_label="Reusable Test Case Not Found!")
                    await addFail(f"Reusable Test Case: {tcID} not found!")
    
                # Allow nested test to specify its own system/env; fallback to parent
                nested_system = nested_tc.get("system", system_needed)
                nested_env = nested_tc.get("env") or nested_tc.get("envConfig") or envConfig

                # Rebuild screen cache for that system
                build_screen_cache(nested_system)

                nested_system_data = login_data["systems"].get(nested_system, {}).get(nested_env, {})

                # Decide if we need per-context http auth
                http_credentials = None
                if nested_system_data.get("loginType") == "urlEmbed":
                    username = os.getenv(nested_system_data.get("username_env", ""))
                    password = os.getenv(nested_system_data.get("password_env", ""))
                    http_credentials = {"username": username, "password": password}

                # Create a fresh context for the nested case (so Basic Auth/header state is isolated)
                context_args = {}
                if http_credentials:
                    context_args["http_credentials"] = http_credentials

                if browserConfigMaximized:
                    nested_context = await browser.new_context(no_viewport=True, **context_args)
                else:
                    nested_context = await browser.new_context(
                        viewport={'width': browserConfigWidth, 'height': browserConfigHeight},
                        **context_args
                    )
                new_page = await nested_context.new_page()
                await new_page.goto("about:blank")

                allure.dynamic.label("nestedTestCase", tcID)
                logger.info(f"[NESTED CASE] Running on new page for system: {nested_system} / env: {nested_env}")
                try:
                    # Fresh login tracker for nested world
                    new_login_state = {"system": None, "base_url": None}
                    #await new_page.goto("about:blank") #Maybe Need
                    new_page = await run_steps(
                        nested_tc.get("steps", []),
                        new_page,
                        browser,
                        dictionaryContext,
                        testSystem,
                        new_login_state,
                        nested_system
                    )
                finally:
                    try:
                        if os.getenv("recording","false").lower() == "true":
                            await finalize_and_attach_videos(nested_context, tcID, save_dir="reports/videos")
                    except Exception as e:
                        logger.warning(f"[NESTED] Video finalize failed -{e}")

                    await nested_context.close()
                    build_screen_cache(testSystem)

                    logger.info(f"[NESTED CASE] Closed nested context for testCase: {tcID}")

        elif "API_REQUEST" in step:
            api_key = step["API_REQUEST"]
            allure.dynamic.feature(f"API Request: {api_key}")
            reportDescription += f"\nAPI Request: {api_key}"
            logger.info("Calling API Endpoint: %s", api_key)

            with allure.step(f"API Request: {api_key}"):
                try:
                    await run_api_request(api_key, dictionaryContext)
                except Exception as e:
                    await attach_allure_failure_details(page, step, name="APIRequestFailure", extra_label=str(e))
                    await addFail(f"API Request Failure: '{api_key}' - {e}")
                    
        else:
            screen_name = step["screen"]
            with allure.step(f"- Screen: {screen_name} -"):
                page = await run_single_step(step, page, base_url, browser, system_needed, dictionaryContext)
    return page


async def run_single_step(step, page, base_url, browser, systemConfig, dictionaryContext):
    screen_name = step["screen"]
    logger.info("Executing Screen: %s", screen_name)

    screen_config_path = screen_config_cache.get(screen_name)
    if not screen_config_path:
        logger.error("Page config not found: %s", f"{screen_name}.json")
        await attach_allure_failure_details(page, step, name="POMFailure", extra_label=f"Page Config - {screen_name} Not Found!")
        await addFail(f"Page Config: {screen_name} not found!")
        #return page

    with open(screen_config_path, "r", encoding="utf-8") as file:
        screen_config = json.load(file)

    dynamic_page = DynamicPage(page, screen_name, screen_config, browser)
    await dynamic_page.navigate(base_url)
    #await page.wait_for_load_state("networkidle")
    page = await dynamic_page.execute_actions(step, dictionaryContext)
    return page

# ---------------------------------
# Browser list from env
# ---------------------------------

def get_browsers_from_env() -> list[str]:
    """
    Read a comma-separated list of browsers from env.
    Example in .env (or OS env):

        browsers=chrome,edge,firefox

    Defaults to ['chrome'] if empty/missing.
    """
    raw = os.getenv("browsers", "chrome")   # case-insensitive key is fine
    parts = [p.strip().lower() for p in raw.split(",")]
    browsers = [p for p in parts if p]

    if not browsers:
        browsers = ["chrome"]

    logger.info(f"[config] BROWSERS from env = {browsers}")
    return browsers

# ---------------------------------
# browser_name fixture
# ---------------------------------

@pytest.fixture(scope="session", params=get_browsers_from_env())
def suite_browser(request):
    """
    One test run per browser in `browsers` env variable.
    e.g. browsers=chrome,edge → session runs twice.
    """
    return request.param

# ---------------------------------
# Allure parameter helper
# ---------------------------------

@pytest.fixture(autouse=True)
def allure_browser_param(suite_browser):
    """
    Makes `browser` show in the Parameters section in Allure.
    Autouse so all tests that use suite_browser indirectly pick it up.
    """
    try:
        allure.dynamic.parameter("browser", suite_browser)
    except Exception:
        # Older allure versions may not support dynamic.parameter
        pass

# ---------------------------------
# Browser resolver (sync)
# ---------------------------------

def resolve_browser_type(p, browser_name: str):
    name = (browser_name or "chrome").lower()
 
    if name in ("chrome",):
        return p.chromium, {"channel": "chrome"}
 
    if name in ("edge", "msedge", "microsoft edge"):
        return p.chromium, {"channel": "msedge"}
 
    if name in ("chromium",):
        return p.chromium, {}
 
    if name in ("firefox", "ff"):
        return p.firefox, {}
 
    if name in ("webkit", "safari"):
        return p.webkit, {}
 
    logger.warning(f"[browser_context] Unknown browser '{browser_name}', defaulting to Chrome channel.")
    return p.chromium, {"channel": "chrome"}

# ---------------------------------
# Browser context fixture
# ---------------------------------

@pytest_asyncio.fixture
async def browser_context(test_case, suite_browser):
    """
    Creates a Playwright browser/page/context based on:
      - test_case.system / env
      - browser_name (chrome/edge/firefox/...)
      - your existing recording + viewport logic
    """
    global slowmoConfig

    system = test_case.get("system") or test_case.get("systemConfig")
    env    = test_case.get("env")    or test_case.get("envConfig")

    recording_on = str(os.getenv("recording", recordingConfig)).lower() == "true"

    if is_frozen_run() and recording_on:
        logger.info("[browser_context] Packaged runner detected - disabling video recording.")
        recording_on = False

    video_dir = mkdir_per_worker() if recording_on else None
    logger.info(
        f"[browser_context] browser={suite_browser} | recording={recording_on} | video_dir={video_dir}"
    )

    async with async_playwright() as p:

        system_data = login_data.get("systems", {}).get(system, {}).get(env, {})
        http_credentials = None
        if system_data.get("loginType") == "urlEmbed":
            user_env = system_data.get("username_env")
            pass_env = system_data.get("password_env")
            username = os.getenv(user_env) if user_env else None
            password = os.getenv(pass_env) if pass_env else None
            if username and password:
                http_credentials = {"username": username, "password": password}
                logger.info("[browser_context] Using urlEmbed http_credentials")
            else:
                logger.warning(
                    "[browser_context] urlEmbed selected but username/password env vars are missing"
                )

        await asyncio.sleep(random.uniform(3, 9))

        maximized = (
            browserConfigMaximized
            if isinstance(browserConfigMaximized, bool)
            else str(browserConfigMaximized).lower() == "true"
        )

        browser_type, extra_launch = resolve_browser_type(p, suite_browser)

        launch_args = {
            "headless": headlessFlag,
            "slow_mo": int(slowmoConfig),
            **extra_launch,  # channel, etc.
        }

        try:
            if maximized:
                launch_args.setdefault("args", [])
                launch_args["args"].append("--start-maximized")

                browser = await browser_type.launch(**launch_args)
                context = await browser.new_context(
                    http_credentials=http_credentials if http_credentials else None,
                    no_viewport=True,
                    record_video_dir=str(video_dir) if video_dir else None,
                    record_video_size={"width": 1280, "height": 720} if recording_on else None,
                )
            else:
                browser = await browser_type.launch(**launch_args)
                context = await browser.new_context(
                    http_credentials=http_credentials if http_credentials else None,
                    viewport={
                        "width": int(browserConfigWidth),
                        "height": int(browserConfigHeight),
                    },
                    record_video_dir=str(video_dir) if video_dir else None,
                    record_video_size={"width": 1280, "height": 720} if recording_on else None,
                )
        except Exception as e:
            logger.warning(f"[browser_context] Failed to initialize browser/context: {e}")
            raise

        page = await context.new_page()

        try:
            yield page, browser, context
        finally:
            # Don't close context here if your recording logic relies on later teardown
            pass
@pytest.fixture
def test_case(request, monkeypatch):
    """
    Receives each JSON test case via indirect param.
    Applies per-test system/env overrides and exposes the case to other fixtures.
    """
    case = request.param

    # Test-level system/env win; otherwise keep the run's selection (not None)
    system = case.get("system") or case.get("systemConfig") or RUN_SYSTEM_CONFIG
    env = case.get("env") or case.get("envConfig") or RUN_ENV_CONFIG

    global systemConfig, envConfig
    systemConfig = system
    envConfig = env

    monkeypatch.setenv("systemConfig", system or "")
    monkeypatch.setenv("envConfig", env or "")

    return case


if test_cases:
    @pytest.mark.parametrize(
        "test_case",
        test_cases,
        ids=[f"{case['testCaseID']}" for case in test_cases],
        indirect=True,
    )
    @pytest.mark.asyncio
    async def test_dynamic_execution(test_case, browser_context, suite_browser, pytestconfig):
        global reportDescription, envConfig

        # del_allure cleanup runs once per session (conftest.py / --clean-alluredir), not per test
        allure_results_dir = get_allure_results_dir(pytestconfig)

        logger.info(f"Type of browser_context: {type(browser_context)}")
        page, browser, context = browser_context

        testCaseId = test_case['testCaseID']
        testSystem = test_case['system']
        testModule = test_case['module']

        logger.info(f"testCaseId: {testCaseId}")

        dictionaryContext = ContextManager()
        dictionaryContext.start_root(testCaseId)
        logger.info(f"Dictionary Context Root: {dictionaryContext.get_root()}")

        dictionaryContext.populateDictionary()

        allure.dynamic.parent_suite(f"{testSystem} - {envConfig}")
        allure.dynamic.suite(f"{testCaseId}")
        allure.dynamic.sub_suite(suite_browser.capitalize())
        allure.dynamic.title(f"{testCaseId} [{suite_browser}]")

        allure.dynamic.label("module", testModule)
        allure.dynamic.label("system", testSystem)
        allure.dynamic.label("environment", envConfig)
        for tag in test_case.get("tags", []):
            allure.dynamic.tag(tag)

        reportDescription += f"\nTest Case: {testCaseId} - System: {testSystem} | Env: {envConfig}"
        await write_allure_environment(allure_results_dir, testSystem, envConfig)
        await write_allure_categories(allure_results_dir)

        logger.info("LOGIN IF NEEDED - systemConfig: %s  - envConfig: %s", testSystem, envConfig)
        current_login_state = {"system": None, "base_url": None}
        if tracingConfig == "true":
            await page.context.tracing.start(screenshots=True, snapshots=True)

        try:
            page = await run_steps(test_case["steps"], page, browser, dictionaryContext, testSystem, current_login_state, testSystem)

        finally:
            try:
                if str(os.getenv("recording", recordingConfig)).lower() == "true" and not is_frozen_run():
                    logger.info("[test_dynamic_execution] Recording is on - Attempting to attach videos to allure report!")
                    await finalize_and_attach_videos(context, testCaseId, save_dir="reports/videos")
            except Exception as e:
                logger.warning(f"[test_dynamic_execution] Failed to attach videos to allure report \nError: {e}")

            try:
                await context.close()
                logger.info("[test_dynamic_execution] Closed Context")
            except Exception as e:
                logger.error(f"[test_dynamic_execution] Failed to close context \nError: {e}")

            try:
                await browser.close()
                logger.info("[test_dynamic_execution] Closed Browser")
            except Exception as e:
                logger.error(f"[test_dynamic_execution] Failed to close Browser \nError: {e}")
else:
    def test_dynamic_execution_selection_error():
        pytest.fail(selection_error_message)