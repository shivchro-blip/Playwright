import os
import json
import logging
import allure
from playwright.async_api import expect, TimeoutError as PlaywrightTimeoutError
from utils.auth_helper import login_if_needed
from playwright.async_api import expect
from utils.errors import addFail, attach_allure_failure_details, addWarn
import utils.executables as Executables
from utils.workspace import resolve_workspace_dir

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

async def action_handler(page, dictionaryContext, action_type, locator=None, element=None, action=None, target=None):
    logger.info(f"Action_Handler - action_type: {action_type}")
    logger.debug(f"Dictionary Context Root: {dictionaryContext.get_root()}")
    logger.debug(f"Dictionary Context Dump: {dictionaryContext.dump()}")

    with allure.step(f"Performing Action - {action_type}"):
        if action_type == "fill":
            await Executables.fillAction(page, dictionaryContext, element, action, target)

        elif action_type == "click":
            page = await Executables.clickAction(page,dictionaryContext, element, target, action["waitForNewTab"], action["focus"], action["request"])
        
        elif action_type == "kendodate":
            await Executables.kendodateAction(page,element,locator)

        elif action_type == "setchecked":
            await Executables.setCheckedAction(page, element, locator)

        elif action_type == "check":
            await Executables.checkAction(page, element, target)

        elif action_type == "hover":
            await Executables.hoverAction(page, element, target)

        elif action_type == "dblclick":
            await Executables.dblclickAction(page, element, target)

        elif action_type == "select":
            page = await Executables.selectAction(page, dictionaryContext, element, action, target, action["waitForNewTab"])

        elif action_type == "number":
            await Executables.numberAction(page, element, action["value"])

        elif action_type == "sladocument":
            await Executables.sladocumentAction(action, page, element)

        elif action_type == "correctionDocument":
            await Executables.correctionDocumentAction(action, page, element)

        elif action_type == "document":
            await Executables.documentAction(action, page, element)

        elif action_type == "fileChooserDocument":
            await Executables.fileChooserDocumentAction(action,page,element)

        elif action_type == "fillFromDictionary":
            await Executables.fillFromDictionaryAction(page, dictionaryContext, action, element, target)

        elif action_type == "save":
            await Executables.saveAction(page, dictionaryContext, action, element, locator)

        elif action_type == "waitforcontains":
            await Executables.waitForContainsAction(action, dictionaryContext, element, page)

        elif action_type == "waitwhileloading":
            await Executables.waitWhileLoadingAction(action, element, page)

        elif action_type == "confirmtext":
            await Executables.confirmTextAction(page, dictionaryContext, action, element)

        elif action_type == "Payment":
            await Executables.Payment(action,page)


        elif action_type == 'wait':
            await Executables.waitAction(action)

        elif action_type == 'keyboard':
            await Executables.keyboardAction(action, page)

        elif action_type == 'clickElementFromList':
            logger.info("clickElementFromList")
            await Executables.clickElementFromList(action, page)

        elif action_type == 'clickInElementFromList':
            logger.info("clickInElementFromList")
            await Executables.clickInElementFromList(action, page)

        elif action_type == 'saveList': 
            logger.info("saveList")
            await Executables.saveListAction(page, dictionaryContext, action, element)

        elif action_type == 'pause': 
            logger.info("pause")
            await Executables.pauseAction(action, page)
        
        elif action_type == "confirmicon":
            logger.info("confirmicon")
            await Executables.confirmIconAction(action, dictionaryContext, element)

        elif action_type == "filldate":
            logger.info("fillDate")
            await Executables.fillDate(action,page)

        elif action_type == "searchRefresh":
            logger.info("searchRefresh")
            await Executables.searchRefresh(action, dictionaryContext, page)

        elif action_type == "scanFile":
            logger.info("scanFile")
            await Executables.scanFile(action, element, page)

        elif action_type == "download":
            logger.info("download")
            await Executables.downloadFile(action, element, page)

        elif action_type == "gather":
            logger.info("gather")
            await Executables.gathersnap(action,  page)

        await check_for_toaster(page)#Checks for toaster notification after an action is completed
    return page

async def runCommonAction(browser, page, commonAction):
    logger.info(f"Executing Common Action: {commonAction}")
    commonActions = json.load(open(f"{workspaceDir}\\Tests\\com\\commonActions.json", "r", encoding="utf-8"))["commonActions"]
    comActSteps = commonActions.get(commonAction['commonAction'])
    if not comActSteps:
        logger.error(f"[ERROR] Common Action '{commonAction['commonAction']}' not found in commonActions.json!")
    systemConfig = comActSteps['system']
    envConfig = comActSteps['env']
    base_url = await login_if_needed(page, systemConfig, envConfig)
    for comActStep in comActSteps:
        screen_name = comActStep["screen"]
        logger.info(f"-> Common Action Step: Navigating to {screen_name}")
        screen_config_path = f"{workspaceDir}\\Pages\\{systemConfig}\\{screen_name}.json"
        if not os.path.exists(screen_config_path):
            logger.error(f"ERROR: Screen config not found: {screen_config_path}")
            continue
        with open(screen_config_path, "r", encoding="utf-8") as file:
            screen_config = json.load(file)
        logger.info(f"DEBUG: Loaded JSON for {screen_name} -> {json.dumps(screen_config, indent=2)}")
        # Local import to prevent circular import issues
        from pages.dynamic_page import DynamicPage
        dynamic_page = DynamicPage(page, screen_name, screen_config, browser)
        await dynamic_page.navigate(base_url)
        page = await dynamic_page.execute_actions(comActStep)

async def getFieldConfig(pomFields, fieldName):
    try:
        logger.info(f"Fetching field config for: {fieldName}")
        fieldConfig = pomFields.get(fieldName)
        logger.info(f"Field config for {fieldName}: {fieldConfig}")
        return fieldConfig
    except Exception as e:
        logger.error(f"Error fetching field config for {fieldName}: {e}")
        return None

async def getElementFromListByText(page, locator, text, value):
    elements = page.locator(locator)
    logger.info(f"getElement - elements: {elements}")
    count = await elements.count()
    logger.info(f"getElement - count: {count}")
    for i in range(count):
        element = elements.nth(i)
        logger.info(f"getElement - Check Element: {i}")
        foundTextElement = element.locator(f'p:has-text("{text}")')
        logger.info(f"getElement - foundTextElement: {foundTextElement}")
        elementText = await foundTextElement.text_content() or ""
        logger.info(f"getElement - element text_context: {elementText}")
        elementValue = elementText.replace(text, "").strip()
        logger.info(f"getElement - element text .strip: {elementValue}")
        if (elementValue and value == elementValue):
            logger.info(f"Found element w/ Matching value: {element}")
            return element  # Return Element if match
        else:
            logger.info(f"Failed to find element w/ Matching value: {value} - element: {element}")
    return None

async def getElementFromListByElement(page, locator, elementToFind):
    elements = page.locator(locator)
    logger.info(f"getElement - elements: {elements}")
    count = await elements.count()
    logger.info(f"getElement - count: {count}")
    for i in range(count):
        element = elements.nth(i)
        logger.info(f"getElement - Check Element: {i}")
        try:
            foundElement = element.locator(elementToFind)
            await expect(foundElement).to_be_visible(timeout=500)
            logger.error(f"FOUND ELEMENT : {foundElement}")
            return element
        except Exception as e:
            logger.info(f"Failed to find element in list that contains element : {elementToFind} \n Error: {e}")
    return None

async def extract_value(locator, wantLabel=True):
    try:
        logger.info(f"Try to extract value - - - locator: {locator} - wantLabel: {wantLabel}")
        return await locator.evaluate("""
            (el, wantLabel) => {
                const tag = el.tagName.toLowerCase();

                if (tag === 'input') {
                    const type = (el.type || '').toLowerCase();
                    if (type === 'checkbox' || type === 'radio')
                        return el.checked;
                    return el.value;
                }

                if (tag === 'textarea')
                    return el.value;

                if (tag === 'select') {
                    const sel = el.selectedOptions;
                    if (wantLabel)
                        return el => el.selectedOptions[0]?.textContent.trim()
                        return el.multiple
                            ? Array.from(sel).map(o => o.textContent.trim())
                            : sel[0]?.textContent.trim(); 
                    return el.multiple
                        ? Array.from(sel).map(o => o.value)
                        : el.value;
                }

                return (el.innerText || '').trim();         // Fallback for plain text/label/contenteditable etc.
            }
        """)
    except Exception as e:
        logger.info(f"extract_value EXCEPT - {e}")
        value = await locator.get_attribute("value")
        if value is not None:
            return value
        return (await locator.inner_text()).strip()

async def performElementAction(element, action):
    logger.info(f"performElementAction - element: {element}")
    logger.info(f"performElementAction - action: {action}")
    actionElement = element.locator(f'text="{action}"')
    if await actionElement.count() == 0:
        raise ValueError(f'Action "{action}" not found in element')
    if await actionElement.count() > 1:
        raise ValueError(f'Action "{action}" found multiple times')
    logger.info(f"performElementAction - CLICK")
    await actionElement.first.click()

async def dump_visible(page, max_text=120):
    visible = page.locator(":scope *:visible")      # all descendants that are visible
    count   = await visible.count()

    for i in range(count):
        el  = visible.nth(i)
        tag = await el.evaluate("e => e.tagName.toLowerCase()")
        _id = await el.get_attribute("id")
        cls = await el.get_attribute("class")
        txt = (await el.inner_text()).strip().replace("\n", " ")
        if len(txt) > max_text:
            txt = txt[:max_text] + "…"

        print(f"{i:>3}  {tag:<10} {(_id or ''):<18} {txt}")






async def check_element_ready(page, element, target, locator, action_type, formData=None, actionCount=1, action=None):
    # Returns True if visible and enabled, False if optional field not visible.

    with allure.step(f"Checking visibility of element: '{target}'"):
        try:
            timeout = 30000 if formData or actionCount == 1 else 100    # Might update this to come dynmically from config?

            await check_for_toaster(page)#Checks if toaster notification is present before an action

            await expect(element).to_be_visible(timeout=timeout)
            logger.info(f"Element '{target}' is visible")

            await expect(element).not_to_be_hidden(timeout=3000)
            logger.info(f"Element '{target}' is not hidden")

            if action_type not in ['save', 'waitforcontains', 'waitwhileloading', 'confirmtext', 'saveList', 'confirmicon','Payment']:
                await expect(element).to_be_enabled(timeout=3000)
                logger.info(f"Element '{target}' is enabled")

            #if is_disabled and action_type != 'save':
            #    msg = f"Element '{target}' is DISABLED"
            #    logger.warning(msg)
            #    allure.attach(msg, name="Disabled Element", attachment_type=allure.attachment_type.TEXT)
            #    return False

            logger.info(f"Element '{target}' is visible and enabled")
            return True

        except Exception as e:
            msg = f"Element '{target}' not visible within {timeout}ms - \n{e}"
            logger.warning(msg)

            extra_label = "required-field" if formData or actionCount == 1 else "optional-skip"
            #if formData or actionCount == 1:
            if formData:
                await attach_allure_failure_details(
                    page=page,
                    action=action,
                    name=f"{target} Visibility Error",
                    extra_label=extra_label
                )

                msg = f"{action_type} on Element '{target}' failed action_helper.check_element_ready.\n Exception: {e}"

                allure.dynamic.label("category", "Visibility Failure")
                await addFail(msg)
            else:
                logger.warning(f"Optional element '{target}' not found; skipping")
                return False

async def check_for_toaster(page):
    try:
        await page.locator("#toast-container").wait_for(state="visible", timeout=70)
        is_visible = True
    except:
        is_visible = False

    if is_visible:
        logging.warning("toaster found with text:")
        toast_text = await page.locator("#toast-container").all_text_contents()
        for text in toast_text:
            logging.warning(text)
            allure.attach(text + "/n", name="TOASTER", attachment_type=allure.attachment_type.TEXT)