import asyncio
import os
from playwright.async_api import expect
from utils.rand_gen import RandGen
from dotenv import load_dotenv
from utils.dictionary import Dictionary
from utils.workspace import resolve_workspace_dir
from utils.errors import RequiredFieldError, addWarn, attach_allure_failure_details, allure_step
import utils.custom_field_helper as customFieldHelper
import utils.action_helper as actionHelper
import utils.executables as executables
import pytest
import warnings
import re
import logging
import allure

load_dotenv()

workspaceDir = resolve_workspace_dir()

loggerLevelString = os.getenv("FormHandlerLoggerLevel", "INFO").upper()
loggerLevel = getattr(logging, loggerLevelString, logging.INFO)

logging.basicConfig(
    level=loggerLevel,  # Ignore INFO logs
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Main logger
logger = logging.getLogger(__name__)
logger.setLevel(loggerLevel)

# Suppress third-party noisy loggers
logging.getLogger('playwright').setLevel(logging.INFO)
logging.getLogger('asyncio').setLevel(logging.INFO)

# Initialize the processor with the JSON configuration file
randgen_config_path = os.path.join(workspaceDir, "Data", "rand_gen_rules.json")
# rand_gen_rules functions build paths like os.path.join(cwd, 'Data', 'Documents', ...), so cwd = workspace
processor = RandGen(randgen_config_path, workspaceDir)

dictionary = Dictionary()

FIELD_TYPE_DEFAULT_ACTION = {
    # clickable controls
    "button":            "click",
    "link":              "click",
    "checkbox":          "click",
    "radio":             "click",
    "table":             "click",

    # text inputs
    "text":              "fill",
    "input":             "fill",
    "textarea":          "fill",

    # selects & numbers
    "select":            "select",
    "number":            "type_number",

    # documents
    "document":          "document",
    "sladocument":       "sladocument",
    "correctionDocument":"correctionDocument",
    "fileChooserDocument": "fileChooserDocument",

    # calendar widgets: the value is a date (MM/DD/YYYY, YYYY-MM-DD or +30d / -7d from today)
    "datepicker":        "muidate",
}

# For Error Loggingg
current_page = ""
current_action = ""
current_actions = []

# Will return string before pipe (ignores everything after it)
def strip_pipe(s):
    return s.split('|')[0]

async def parse_field_override(dictionaryContext, field_name: str, pomFields):
    loop_type, loop_count = None, 0

    if '|' in field_name:
        clean_name, override = field_name.split('|', 1)
        override = override.strip()

        m = re.match(r'^(E|L)Loop(?:\(|\[)([^\)\]]+)(?:\)|\])$', override)
        if m:
            loop_type = 'ELoop' if m.group(1) == 'E' else 'LLoop'

            raw_count = m.group(2).strip()
            if raw_count.startswith("DICT."):
                dict_key = raw_count.split('.', 1)[1].strip()
                loop_count = await loadFromDictionary(dictionaryContext, dict_key)
                loop_count = int(loop_count)
            else:
                loop_count = int(raw_count)
        field_name = clean_name

    logger.info(f"PARSE FIELD OVERRIDE → name: {field_name}, type: {loop_type}, count: {loop_count}")
    field_config = await actionHelper.getFieldConfig(pomFields, field_name)
    #await asyncio.sleep(5)

    return field_config, field_name.strip(), loop_type, loop_count


def useRegex(text, pattern):
    match = re.search(pattern, text)
    return match.group(1) if match and match.lastindex else (match.group(0) if match else None)


async def get_table(row, col_name, table_id, page, button=None, Action=None, index=None):
    logger.info(f"Fetching table for column '{col_name}' using table_id: {table_id}")
    element = page.locator(table_id)
    await page.wait_for_selector(table_id, state="visible")
    all_rows = await element.locator('tr').all()
    if not all_rows:
        logger.error("No rows found in the table. Check POM table ID")
        return None
    columns = await all_rows[0].locator('th').all_inner_texts()
    table = {col: str(i + 1) for i, col in enumerate(columns)}
    if col_name not in table:
        logger.error(f"Column '{col_name}' not found. Mapping: {table}")
        return None
    col_index = table[col_name]
    logger.info(f"Table mapping: {table} | Column index for '{col_name}': {col_index}")
    if Action == "click":
        cell_locator = f"{table_id} [aria-rowindex='{row}'] [aria-colindex='{col_index}']"
        if button:
            cell_locator += f" [title='{button}']"
        logger.info(f"Final cell locator (click): {cell_locator}")
    elif Action == "save":
        cell_locator = f"{table_id} [aria-rowindex='{row}'] [aria-colindex='{col_index}']"
    else:
        cell_locator = f"{table_id} [aria-rowindex='{row}'] [aria-colindex='{col_index}'] [aria-label='{col_name} Filter']"
    element = page.locator(cell_locator)
    if index:
        logger.info("Using index for element selection")
        element = element.nth(index)
    return element


async def addWarn(msg):
    logger.warning(f"Adding Warning - {msg}")
    warnings.warn(UserWarning(f"Warning - {msg}"))


async def addFail(msg):
    logger.error(f"Adding Failure - {msg}")
    #await page.screenshot(path=f"{cwd}/screenshots/fail.png")
    pytest.fail(f"Failure - [Current Page = {current_page}] - [Current Action = {current_action}] - [Action List = {current_actions} - \n Failure Message: {msg}")

async def getFailInfo():
    return current_page, current_action, current_actions

def _exact_visible_regex(text: str):
    # Exact match on visible text, but tolerant to leading/trailing spaces/newlines.
    return re.compile(rf"^\s*{re.escape(str(text))}\s*$")

def _split_dirty_selector(selector: str):
    """
    Very lightweight split: take the last descendant combinator as parent/child boundary.
    Returns (parent_css, parent_text_or_None, child_css, child_text_or_None).
    If no child segment, child parts are None.
    """
    # Extract all :has-text('...') in order
    texts = re.findall(r":has-text\('([^']+)'\)", selector)

    # Drop :has-text(...) from CSS pieces
    clean = re.sub(r":has-text\('([^']+)'\)", "", selector).strip()

    # Split into parent and child on the last space (descendant)
    if " " in clean:
        parent_css, child_css = clean.rsplit(" ", 1)
    else:
        parent_css, child_css = clean, None

    # Assign texts to parent/child in order of appearance
    parent_text = texts[0] if len(texts) >= 1 else None
    child_text  = texts[1] if len(texts) >= 2 else None

    return parent_css.strip(), parent_text, (child_css.strip() if child_css else None), child_text

async def get_element(
        dictionaryContext,
        target: str,
        page,
        locator: str = None,
        role: str = None,
        index: int = None,
        placeholder: str = None,
        contains: str = None,
        nameF: str = None,
        text: str = None,
        label: str = None,
        nested: list = None,
        hasTextDirty: bool = None
):
    element = None

    if nested:
        current = page
        logger.info(f"[EA] Using nested locator chain for target: {target}")
        for step in nested:
            method = step.get("method")
            kwargs = step.get("options", {})
            if not hasattr(current, method):
                logger.error(f"[EA] ERROR: '{method}' is not a valid method on current element.")
                return None
            current = getattr(current, method)(**kwargs)
            logger.info(f"current: {current}")
        element = current
        logger.info(f"element: {element}")

    elif locator and not role:
        logger.info(f"[EA] Original locator: {locator}")

        # DICT tokens
        d_matches = re.findall(r"DICT\.([\w]+)", locator)
        for m in d_matches:
            v = await loadFromDictionary(dictionaryContext, m)
            if v is None:
                logger.error(f"[EA] ERROR: Dictionary key '{m}' not found.")
                return None
            locator = locator.replace(f"DICT.{m}", str(v))

        # ENV tokens
        e_matches = re.findall(r"ENV\.([\w]+)", locator)
        for m in e_matches:
            v = os.getenv(m)
            if v is None:
                logger.error(f"[EA] ERROR: ENV var '{m}' not set.")
                return None
            locator = locator.replace(f"ENV.{m}", str(v))

        # If not dirty, keep the original single-string behavior
        if not hasTextDirty:
            logger.info(f"[EA] Final resolved locator: {locator}")
            element = page.locator(locator)

        else:
            # hasTextDirty=True → interpret the selector and apply text filters via API
            try:
                parent_css, parent_text, child_css, child_text = _split_dirty_selector(locator)
                logger.info(f"[EA] Dirty-split → parent_css={parent_css!r}, parent_text={parent_text!r}, child_css={child_css!r}, child_text={child_text!r}")

                # Start from parent (or page if parent_css is empty)
                current = page.locator(parent_css) if parent_css else page

                # If parent had :has-text(), apply exact/whitespace-tolerant filter
                if parent_text:
                    current = current.filter(has=page.get_by_text(_exact_visible_regex(parent_text)))

                # If there is a child segment, descend to it and apply its :has-text()
                if child_css:
                    current = current.locator(child_css)
                    if child_text:
                        current = current.filter(has=page.get_by_text(_exact_visible_regex(child_text)))

                element = current
                logger.info(f"[EA] Built dirty element with API filters: {element}")

            except Exception as e:
                logger.warning(f"[EA] Dirty selector parse failed, falling back to raw locator. Error: {e}")
                element = page.locator(locator)  # last resort

        logger.info(f"[EA] Final resolved locator: {element}")


    elif placeholder:
        logger.info(f"[EA] Using placeholder: {placeholder}")
        element = page.get_by_placeholder(placeholder)

    elif contains:
        logger.info(f"[EA] Using contains: {contains}")
        element = page.locator(f"p:has-text('{contains}')")
        await expect(element).to_be_visible()

    elif label:
        logger.info(f"[EA] Using label: {label}")
        element = page.get_by_label(label)

    elif text:
        logger.info(f"[EA] Using text: {text}")
        if locator:
            element = page.locator(locator).get_by_text(text)
        else:
            element = page.get_by_text(text)

    elif role and nameF:
        logger.info(f"[EA] Using role: {role}, name: {nameF}")
        element = page.get_by_role(role, name=nameF)

    elif role and locator:
        logger.info(f"[EA] Using role: {role} with text: {locator}")
        element = page.get_by_role(role).get_by_text(locator, exact=True)

    elif role:
        logger.info(f"[EA] Using role only: {role}")
        element = page.get_by_role(role)

    else:
        logger.error(f"[EA] ERROR: Locator not found for field: {target}")
        return None

    if index is not None:
        logger.info(f"[EA] Using index: {index}")
        element = element.nth(index)

    logger.info(f"[EA] Final Element: {element}")
    return element


async def checkForErrorMessages(page, pageErrorLocators=None, pageErrorContains=None):
    try:
        logger.info("Checking for error messages")
        #await asyncio.sleep(2)
        logger.info(f"pageErrorLocations: {pageErrorLocators}")
        logger.info(f"pageErrorContains: {pageErrorContains}")
        for errorLocator in pageErrorLocators:
            error_elements = await page.query_selector_all(errorLocator)
            found_errors = []
            for error in error_elements:
                if error:
                    text = await error.text_content()
                    logger.info(f"Error: {error}")
                    for errorContains in pageErrorContains:
                        logger.info(f"text: {text} - - errorContains: {errorContains}")
                        #await asyncio.sleep(.2)
                        if errorContains in text:
                            logger.info("Found error message")
                            found_errors.append(text)
            #await asyncio.sleep(2)
            logger.info(f"found_errors: {found_errors}")
            #await asyncio.sleep(3)
            return found_errors
    except:
        logger.info("No error messages found")


async def saveToDictionary(dictionaryContext, key, value):
    dictionaryContext.set(key, value)
    return True


async def loadFromDictionary(dictionaryContext, key):
    return dictionaryContext.get(key)


async def get_tableActions(field_name, field_type, action_type, locator, field_value, focus, waitForNewTab, request):
    logger.info(f"Generating table action for field: {field_name} with action: {action_type}")
    fillValue = field_value['button']
    if locator == None:
        locator = ''
    if action_type == "click":
        return {"type": "click", "field": field_name, "value": fillValue, "row": field_value['row'],
                "col": field_value['col'], "locator": locator, "focus": focus, "waitForNewTab": waitForNewTab,"request":request }

    if action_type == "fill":
        return {"type": "fill", "field": field_name, "value": fillValue, "row": field_value['row'],
                "col": field_value['col'], "locator": locator, "focus": focus}
    if action_type == "save":
        return {"type": "save", "field": field_name, "value": fillValue, "row": field_value['row'],
                "col": field_value['col'], "locator": locator, "role": "", "contains": "", "focus": focus}


async def get_field_value_for_table(field_value):
    if '|' in field_value:
        parts = field_value.split("|")
        logger.info(f"Splitting table field value into parts: {parts}")
        if len(parts) == 3:
            field_value = {"action": parts[0], "row": parts[1], "col": parts[2]}
        elif len(parts) == 4:
            field_value = {"action": parts[0], "row": parts[1], "col": parts[2], "button": parts[3]}
    return field_value


    
###########################################################################################################################
###########################################################################################################################

async def auto_generate_actions(dictionaryContext, pomFields, testData=None):
    logger.info("Auto-generating actions")
    logger.info(f"Test Data: {testData}")
    actions = []
    if not isinstance(pomFields, dict):
        logger.error(f"Expected pomFields to be a dict, got {type(pomFields)}")
        return []

    if testData:
        actions = await createActionsFromTestData(dictionaryContext, pomFields, testData)

    else:
        actions = await createActionsFromPOM(dictionaryContext, pomFields)

    logger.debug(f"Generated Actions: {actions}")
    return actions


###########################################################################################################################
###########################################################################################################################

async def resolve_value_locator(dictionaryContext, field_name, locator, raw_value, auto=False):
    """A locator with {value} takes its answer from the test, so the page object stays free of test data:
    "TaxNotPaid": "No" clicks  [data-attribute-id='TaxNotPaid'] button:text-is('No').
    ENV.NAME and DICT.key work as the value. Returns the locator, or None to skip the field."""
    if auto:  # no test data (the page is being auto-run): there is no answer to put in
        logger.warning(f"Skipping '{field_name}': its locator contains {{value}} and no test data names the option")
        return None
    value = raw_value
    if isinstance(value, str) and value.startswith("ENV."):
        value = os.getenv(value[4:].strip(), "")
    elif isinstance(value, str) and value.startswith("DICT."):
        value = await loadFromDictionary(dictionaryContext, value[5:].strip())
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or not str(value).strip():
        await addFail(f"Field '{field_name}' needs a value: its locator contains {{value}}, so the test must say "
                      f"which option to choose, e.g. \"{field_name}\": \"No\".")
    text = str(value).strip().replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
    return locator.replace("{value}", text)


async def generate_action(dictionaryContext, field_name, raw_value, field_config, pomFields, auto=False):
    # 1) strip off any |ELoop(x) or |LLoop(x)
    field_config, clean_name, loop_type, loop_count = await parse_field_override(dictionaryContext, field_name, pomFields)    ## GET FIELD CONFIG AGAIN AFTER PARSE NAME!!!!
    if field_config:
        # 2) pull _all_ locator-config options
        locator     = field_config.get("locator")
        role        = field_config.get("role")
        placeholder = field_config.get("placeholder")
        contains    = field_config.get("contains")
        name        = field_config.get("name")
        text        = field_config.get("text")
        label       = field_config.get("label")
        nested      = field_config.get("nested")
        index       = field_config.get("index")
        tableId     = field_config.get("tableId")
        request     = field_config.get("request")
        waitForNewTab = field_config.get("waitForNewTab")
        focus       = field_config.get("focus")
        field_type  = field_config.get("type")
        auto_gen    = field_config.get("autoGen")
        picker      = field_config.get("picker")
        required    = field_config.get("required") or True
    else:
        await attach_allure_failure_details(name=f"Generate Action Failure - No Field Config for: {field_name}")
        await addFail(f"Failed to generate Actions - No Field Config for: {field_name}")

    if field_config and locator and "{value}" in str(locator):
        locator = await resolve_value_locator(dictionaryContext, clean_name, str(locator), raw_value, auto)
        if locator is None:
            return None
        raw_value = "click"  # the answer is in the locator now; choosing it is a click

    #if "DICT." in raw_value:
    #    strippedValue = raw_value.replace("DICT.", "").strip()
    #    finalValue = dictionary.load(strippedValue)
    #raw_value = finalValue.lstrip()

    if field_type == "table":
        field_value = await get_field_value_for_table(raw_value)

        action_type = field_value["action"]
        action = await get_tableActions(field_name, field_type, action_type, locator, field_value, focus, waitForNewTab, request)
        logger.info(f"TABLE ACTION: {action}")

        return action

    # 3) decide on action_type & value
    # Only strings are shorthand actions; dict values are explicit actions (handled below)
    lv = raw_value.lower() if isinstance(raw_value, str) else ""
    if lv in {"click","kendodate","setchecked","check","dblclick","hover"}:
        action_type, value = lv, None

    elif field_type in {"select","document","sladocument","correctionDocument","fileChooserDocument"} and isinstance(raw_value, str):
        action_type, value = field_type, raw_value

    elif isinstance(raw_value, dict):
        # Hardcoded Actionv in test Data + inject locator+loops
        return {
            **raw_value,
            "field": clean_name,
            "locator": locator, "role": role,
            "placeholder": placeholder, "contains": contains,
            "name": name, "text": text, "label": label,
            "nested": nested, "index": index, "tableId": tableId,
            **({"loopType": loop_type, "loop": loop_count} if loop_type else {})
        }

    else:
        # fallback to autoGen when missing/null
        if not raw_value and auto_gen:
            logger.info(f"auto_gen: {auto_gen}")
            gen = processor.generate_field_value(auto_gen)
            if gen:
                raw_value = gen

        action_type = FIELD_TYPE_DEFAULT_ACTION.get(field_type, "fill")
        value = raw_value if action_type in {"fill","select","type_number","document","sladocument","correctionDocument", "fileChooserDocument", "muidate"} else None

    # 4) assemble the action dict
    action = {
        "type":             action_type,
        "field":            clean_name,
        "locator":          locator,
        "role":             role,
        "focus":            focus,
        "waitForNewTab":    waitForNewTab,
        "request":          request,
        "placeholder":      placeholder,
        "contains":         contains,
        "name":             name,
        "text":             text,
        "label":            label,
        "nested":           nested,
        "index":            index,
        "tableId":          tableId,
        "required":         required,
        **({"picker": picker} if picker else {}),
        **({"value": value} if value is not None else {}),
    }

    # 5) attach loops if present
    if loop_type:
        action["loopType"] = loop_type
        action["loop"]     = loop_count

    return action

async def createActionsFromTestData(dictionaryContext, pomFields, testData):
    actions = []
    try:
        logger.info("Generating actions using provided test data")
        for field_name, field_value in testData.items():
            logger.info(f"Processing field '{field_name}' with value: {field_value}")
            field_config = await actionHelper.getFieldConfig(pomFields, field_name)
            ### {'locator': 'select[name="Forms"]', 'type': 'select', 'autoGen': 'appType'}

            if "CUSTOMFIELD" in field_name:
                logger.info("Found CUSTOM FIELD")
                action = await customFieldHelper.customFieldHandler(dictionaryContext, pomFields, field_name, field_value)
                logger.info(f"Custom action generated: {action}")
            else:
                logger.info(f"generate_action : field_name: {field_name} - field_value: {field_value} - field_config - {field_config}")
                action = await generate_action(dictionaryContext, field_name, field_value, field_config, pomFields)

            if action:
                logger.info(f"Append Action: {action}")
                actions.append(action)
        return actions
    except Exception as e:
        await addFail(f"Failed to generate Actions from test data - Error: {e} \n Test Data: {testData} \n pomFields: {pomFields}")


async def createActionsFromPOM(dictionaryContext, pomFields):
    actions = []
    try:
        logger.info(f"Generating actions automatically (no test data provided)\n - POM Fields: {pomFields}")
        for field_name, field_config in pomFields.items():
            logger.info(f"POM Field: {field_name} - config: {field_config}")
            field_value = None
            action = await generate_action(dictionaryContext, field_name, field_value, field_config, pomFields,
                                           auto=True)
            if action:
                actions.append(action)
    except Exception as e:
        await addFail(f"Failed to auto-generate Actions from POM - Error: {e} \n pomFields: {pomFields}")
        
    return actions




async def execute_actions(page, actions, pomFields, test_data, pageErrorLocators, pageErrorContains, dictionaryContext): 
    try:
        current_page = page
        current_actions = actions
        formData = test_data.get("formData", None)
        logger.info(f"Executing actions; test_data: {test_data}")
        if not actions:
            logger.error("No actions generated; attempting auto-generation")
            actions = await auto_generate_actions(dictionaryContext, pomFields, test_data.get("formData", {}))
            logger.info(f"Auto-generated actions: {actions}")
        if not actions:
            logger.error("No actions available; check screen_config['pomFields'] structure.")
            return page
        logger.info(f"Actions to execute: {actions}")
        actionCount = 0

        for action in actions:
            current_action = action
            actionCount += 1
            with allure.step(f"{actionCount}) {action['type'].upper()} - {action.get('field', 'UNKNOWN')}"):
                logger.info(f"Processing action #{actionCount}: {action}")
                action_type = action["type"]
                target = action["field"]
                locator = action["locator"]

                try:
                    loop = action.get("loop")
                    loopType = action.get("loopType")
                except:
                    logger.warn("No Loop or Loop Type found")

                role = pomFields.get(target, {}).get("role")
                index = pomFields.get(target, {}).get("index")
                placeholder = pomFields.get(target, {}).get("placeholder")
                contains = pomFields.get(target, {}).get("contains")
                name = pomFields.get(target, {}).get("name")
                text = pomFields.get(target, {}).get("text")
                label = pomFields.get(target, {}).get("label")
                tableId = pomFields.get(target, {}).get("tableId")
                nested = pomFields.get(target, {}).get("nested")
                hasTextDirty = pomFields.get(target, {}).get("hasTextDirty") or False

                if tableId:
                    row = action["row"]
                    col = action["col"]
                    button = action["value"]
                    element = await get_table(row, col, tableId, page, button, action_type, index)
                elif action_type not in ['keyboard','Payment']:
                    element = await get_element(dictionaryContext, target, page, locator, role, index, placeholder, contains, name, text,
                                                label, nested, hasTextDirty)

                logger.info(f"Performing {action_type.upper()} on '{target}' (locator: {locator})")
                await page.wait_for_load_state()
                try:
                    if action_type not in ['wait', 'keyboard', 'waitwhileloading', 'pause','filldate','Payment'] and loop is None:
                        element_ready = await actionHelper.check_element_ready(
                            page, element, target, locator, action_type,
                            formData=formData,
                            actionCount=actionCount,
                            action=action
                        )
                        if not element_ready:
                            continue
                except Exception as e:
                    logger.warning(f"Element Ready Check Exception - {e}")
                    continue

                logger.info(f"Execute Loop: {loop} - {loopType}")
                if loop:
                    for x in range(loop):
                        logger.info(f"x: {x}")
                        if loopType == 'LLoop':
                            if tableId:
                                row = action["row"]
                                col = action["col"]
                                button = action["value"]
                                element = await get_table(row, col, tableId, page, button, action_type, index)
                            else:
                                element = await get_element(dictionaryContext, target, page, locator, role, index, placeholder, contains, name, text, label, nested, hasTextDirty)
                            element = element.nth(x)
                            logger.info(f"Element: {element}")
                            page = await actionHelper.action_handler(page, dictionaryContext, action_type, locator, element=element, action=action, target=target)
                        elif loopType == 'ELoop':
                            page = await actionHelper.action_handler(page, dictionaryContext, action_type, locator, element=element, action=action, target=target)
                else:
                    try:
                        logger.info(f"Call Action Handler - page: {page}, action_type: {action_type}, locator: {locator}, element: {element}, action: {action}, target=target")
                        page = await actionHelper.action_handler(page, dictionaryContext, action_type, locator, element=element, action=action, target=target)
                    except Exception as e:
                        logger.error(f"Action Handler failed : {e}")
                        await attach_allure_failure_details(page, action=action, name="Action Failure")
   
        #await asyncio.sleep(2)
        found_errors = await checkForErrorMessages(page, pageErrorLocators, pageErrorContains) or []

        if found_errors:
            for errorMsg in found_errors:
                await attach_allure_failure_details(page, action=None, name="Field Error Found Screenshot", extra_label=f"Found Errors: {found_errors}")
                await addFail(f"Error on page {page}: {errorMsg}")
            raise RequiredFieldError(f"Field error found on page {page}: {errorMsg} \n - found_errors: {found_errors}")
        else:
            logger.info("No required errors found")
        return page

    except Exception as error:
        logger.error(f"FAILED TO EXECUTE ACTIONS: {error}")
        await attach_allure_failure_details(page, action=None, name="Failed to execute actions Screenshot", extra_label=f"Found Errors: {found_errors}")
        pytest.fail(f"Failed to execute actions on Page: {page} - {error}")
        await asyncio.sleep(5)
        return page
