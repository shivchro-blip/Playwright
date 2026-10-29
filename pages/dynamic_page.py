import logging
from utils.dynamic_form_handler import execute_actions, auto_generate_actions
from playwright.async_api import expect
from utils.errors import RequiredFieldError, addFail, attach_allure_failure_details
import pytest
from dotenv import load_dotenv
import os
import utils.snapshot_helper as snap
load_dotenv()

loggerLevelString = os.getenv("PageLoggerLevel", "INFO").upper()
loggerLevel = getattr(logging, loggerLevelString, logging.INFO)

# Configure module-level logger
logging.basicConfig(
    level=loggerLevel,  # Ignore INFO logs
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Main logger
logger = logging.getLogger(__name__)
logger.setLevel(loggerLevel)

# Suppress third-party noisy loggers
logging.getLogger('playwright').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

pageWaitUntil = os.getenv("pageWaitUntil", "domcontentloaded")
logger.info(f"pageWaitUntil : {pageWaitUntil}")

class DynamicPage:
    def __init__(self, page, screen_name, screens_config, browser):
        self.page = page
        self.screen_name = screen_name
        self.screen_config = screens_config
        self.browser = browser

    async def navigate(self, base_url):
        try:
            path = self.screen_config.get("path", None)
        except:
            logger.info("No Screen Path")
        try:
            fullpath = self.screen_config.get("fullpath", None)
        except:
            logger.info("No Screen Full Path")

        # Navigate ONLY IF `path` exists
        if path:
            full_url = f"{base_url}{path}"
            logger.info(f"-> Navigating to: {full_url}")
            await self.page.goto(full_url, wait_until=pageWaitUntil)
            await self.page.wait_for_url(full_url)
        elif fullpath:
            logger.info(f"-> Navigating to: {fullpath}")
            await self.page.goto(fullpath, wait_until=pageWaitUntil)
            await self.page.wait_for_url(fullpath)
        else:
            logger.info(f"-> No direct navigation needed for {self.screen_name}")

    async def execute_actions(self, test_data, dictionaryContext):
        logger.info("--- IN EXECUTE ACTIONS --- ")
        fields = self.screen_config.get("fields", {})
        logger.info("")
        #logger.info("fields :")
        #logger.info(fields)
        # FIX: Pass `fields` FIRST to `auto_generate_actions()`
        actions = test_data.get("actions") or await auto_generate_actions(dictionaryContext, fields, test_data.get("formData", None))
        logger.info("------------------")
        logger.info(f"-> Executing Actions for {self.screen_name}")
        logger.debug("Actions to execute:")
        logger.debug(actions)
        logger.info("------------------")
        try:
            pageTitleLocator = self.screen_config.get('titleLocator')
            pageTitleContains = self.screen_config.get('titleContains')
        except Exception:
            logger.warning("Screen Config doesn't contain Title'")
        try:
            pageErrorLocators = self.screen_config.get('errorLocators')
            pageErrorContains = self.screen_config.get('errorContains')
        except Exception:
            logger.warning("Screen Config doesn't contain Error Config'")

        logger.debug(f"pageTitleLocator: {pageTitleLocator}")
        logger.debug(f"pageTitleContains: {pageTitleContains}")

        if pageTitleLocator and pageTitleContains:
            try:
                titleElement = self.page.locator(pageTitleLocator)
                logger.debug(f"titleElement: {titleElement}")
                await expect(titleElement).to_contain_text(pageTitleContains)
                logger.debug("PAGE LOADED SUCCESSFULLY")
            except AssertionError as ae:
                await attach_allure_failure_details(self.page, name="Page Title Assertion Failure")
                await addFail(f"Assertion Error for Title: {pageTitleContains}: {ae}")
            except RequiredFieldError as e:
                logger.error(f"[FAIL] : {e} \n Couldn't find page title on {self.screen_name}")
                pytest.fail(str(e))  # this FAILS the test and STOPS it

        def get_bool_env(envVariable, default=False):
            envValue = os.getenv(envVariable)
            if envValue is None:
                return default
            return envValue.strip().lower() in ("true", "1", "yes")

        TakeSnapshot = get_bool_env("Snapshots", False)
        if TakeSnapshot:
            snapshot = await snap.takeSnapshot(self.page)
            await snap.save_yaml_file(snapshot,self.screen_name,"data/Snapshots")
            jsonfile =await snap.convert_yaml_to_json(snapshot)
            await snap.save_json_file(jsonfile, self.screen_name, "data/Snapshots")
            print(jsonfile)



        try:
            page = await execute_actions(self.page, actions, fields, test_data, pageErrorLocators, pageErrorContains, dictionaryContext)

        except Exception as e:
            logger.error(f"[FAIL] : {e} \n Exception detected on screen {self.screen_name}")
            await attach_allure_failure_details(self.page, name="Page Execute Actions Failure")
            pytest.fail(str(e))  # this FAILS the test and STOPS it
        return page