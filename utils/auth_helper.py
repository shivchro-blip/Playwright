import json
import os
from dotenv import load_dotenv
from utils.workspace import resolve_workspace_dir, resolve_systemdata_path
import time
import logging

load_dotenv()  # Loads variables from .env

workspaceDir = resolve_workspace_dir()

logging.basicConfig(
    level=logging.INFO,  # Ignore INFO logs
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Main logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Suppress third-party noisy loggers
logging.getLogger('playwright').setLevel(logging.INFO)
logging.getLogger('asyncio').setLevel(logging.INFO)

SESSION_FILE = os.path.join(workspaceDir, "Data", "session.json")
SYSTEMDATA_FILE = resolve_systemdata_path(workspaceDir)

async def save_session(page):
    """Save browser cookies and storage state."""
    await page.context.storage_state(path=SESSION_FILE)


async def load_session(page):
    """Load session storage if available."""
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        await page.context.add_cookies(state.get("cookies", []))


async def is_logged_in(page):
    """Check if the user is already logged in."""
    # Replace with an appropriate selector for your application.
    return await page.locator("text= Find An Application").is_visible()


async def login_if_needed(page, system_name, environment):
    """Log in only if necessary, using JSON for URLs and .env for credentials."""
    with open(SYSTEMDATA_FILE, "r", encoding="utf-8") as file:
        system_data = json.load(file)
    system_data = system_data["systems"].get(system_name, {}).get(environment, {})
    if not system_data:
        raise ValueError(f"Login data for system '{system_name}' in environment '{environment}' not found.")
    base_url = system_data["base_url"]
    login_page_name = system_data["loginPage"]
    landing_page_name = system_data["landingPage"]
    logger.info(f"base_url : {base_url}")
    logger.info(f"login_page_name : {login_page_name}")
    logger.info(f"landing_page_name : {landing_page_name}")
    # Retrieve credentials from environment variables
    username = os.getenv(system_data["username_env"])
    password = os.getenv(system_data["password_env"])
    logger.info(f"username : {username}")
    # logger.info(f"password : {password}")
    if not base_url or not username or not password:
        raise ValueError(f"Missing credentials or URL for {system_name} in {environment}.")
    # Load login page configuration (this JSON file defines locators for the login page)
    with open(os.path.join(workspaceDir, "Pages", system_name, f"{login_page_name}.json"), "r", encoding="utf-8") as loginFile:
        login_page = json.load(loginFile)
    with open(os.path.join(workspaceDir, "Pages", system_name, f"{landing_page_name}.json"), "r", encoding="utf-8") as landingFile:
        landing_page = json.load(landingFile)
    fields = login_page["fields"]
    landFields = landing_page["fields"]
    # landPath = landing_page["path"]
    loginPath = login_page['path']
    loginDirect = login_page['direct']
    loginType = ""
    try:
        loginType = login_page['loginType']
    except:
        logger.info("No Login Type")
    # Load existing session if any
    await load_session(page)
    # Check if already logged in
    if await is_logged_in(page):
        return base_url
    if loginType == "urlEmbed":
        # Auth already handled in browser context
        await page.goto(base_url)
        logger.info("Used http_credentials via browser context for urlEmbed")
    else:
        try:
            # Navigate to login page and perform login
            logger.info(f"Login Page: {login_page}")
            if loginDirect:
                await page.goto(f"{loginPath}")
                # time.sleep(2)
            else:
                await page.goto(f"{base_url}{loginPath}")
                # time.sleep(2)
            if landFields["login"] == "":
                logger.info("No Landing Screen Login Button")
            else:
                await page.locator(landFields["login"]).click()
            time.sleep(2)
            if await is_logged_in(page):
                return base_url
            # CHECK IF AUTO-LOGS IN, SKIP THIS!
            await page.locator(fields["username"]).fill(username)
            await page.locator(fields["password"]).fill(password)
            await page.locator(fields["submit"]).click()
            # time.sleep(2)
            logger.info("Login")
            if landFields["login"] == "":
                logger.info("No Landing Screen Login Button")
            else:
                await page.locator(landFields["login"]).click()
        except Exception as e:
            logger.error(f"Already logged in \n error: {e}")
    # Save the session for reuse
    await save_session(page)
    return base_url