
import datetime
from datetime import date, timedelta
import logging
import asyncio
import re
import utils.action_helper as actionHelper
from playwright.async_api import async_playwright
from utils.dictionary import Dictionary

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
dictionary = Dictionary()

def strip_pipe(s):
    return s.split('|')[0]

async def customFieldHandler(dictionaryContext, pomFields, field_name, field_value):
    logger.info(f"Handling custom field: {field_name} with value: {field_value}")
    if field_name and '|' in field_name:
        field_name = strip_pipe(field_name)
    if field_name == 'CUSTOMFIELD_TYPEDATE':
        return await customField_KeyboardTypeDate_CreateAction(field_value)
    if field_name == 'CUSTOMFIELD_SAVEFIELD':
        return await customField_SaveField_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_SAVELOCATOR':
        return await customField_SaveLocator_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_FILLFIELD':
        return await customField_FillField_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_FILLLOCATOR':
        return await customField_FillLocator_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_WAITFORCONTAINS':
        return await customField_WaitForContains_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_WAITWHILELOADING':
        return await customField_WaitWhileLoading_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_CONFIRMTEXT':
        return await customField_ConfirmText_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_WAIT':
        return await customField_Wait_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_KEYBOARD':
        return await customField_Keyboard_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_CLICKELEMENTFROMLIST':
        return await customField_ClickElementFromList_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_CLICKINELEMENTFROMLIST':
        return await customField_ClickInElementFromList_CreateAction(dictionaryContext, pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_SAVELISTFIELD':
        return await customField_SaveListField_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_PAUSE':
        return await customField_Pause_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_CONFIRMICON':
        return await customField_ConfirmIcon_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_SEARCHREFRESH':
        return await customField_SearchRefresh_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_SCANFILE':
        return await customField_ScanFile_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_CLICKFIELD':
        return await customField_ClickField_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_DOWNLOAD':
        return await customField_DownloadFile_CreateAction(pomFields, field_value)
    elif field_name == 'CUSTOMFIELD_GATHER':
        return await customField_Gather_CreateAction(pomFields,field_value)
    elif field_name == 'CUSTOMFIELD_PAYMENT':
        return await customField_Payment_CreateAction(pomFields,field_value)
    
    return None
async  def customField_Gather_CreateAction(pomFields,field_value):
    action = {
        "type": "gather",
        "locator": field_value,
        "field": "",
        "value": ""
    }
    return action
async def customField_DownloadFile_CreateAction(pomFields, field_value):
    screenFieldName = field_value
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)
    action = {
        "type": "download",
        "locator": fieldConfig.get("locator"),
        "field": field_value,
        "value": ""
        }

    return action

async def customField_KeyboardTypeDate_CreateAction( field_value):
    currentDate = date.today()
    operator, timeDelta = field_value.split('|')
    timeDelta = int(timeDelta)
    if operator == '-':
        dateToFill = currentDate-timedelta(days=timeDelta)
    elif operator == '+':
        dateToFill = currentDate+timedelta(days=timeDelta)
    else:
        dateToFill = currentDate
    dateToFill = dateToFill.strftime("%m%d%Y")
    return {"type": "filldate", "value": dateToFill, "field": "", "locator": "main"}



async def customField_SaveField_CreateAction(pomFields, field_value):
    logger.info(f"customField_SaveField_CreateAction: field_value: {field_value} ")
    variableName, screenFieldName, regex = field_value.split('|')
    logger.info(f"customField_SaveField_CreateAction: variableName={variableName}, screenFieldName={screenFieldName}, regex={regex}")
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)
    action = {
        "type": "save",
        "field": screenFieldName,
        "value": variableName,
        "locator": fieldConfig.get("locator"),
        "role": fieldConfig.get("role"),
        "contains": fieldConfig.get("contains"),
        "regex": regex
    }
    return action

async def customField_FillField_CreateAction(pomFields, field_value):
    variableName, screenFieldName = field_value.split('|')
    logger.info(f"Custom FillField: variableName={variableName}, screenFieldName={screenFieldName}")
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)
    return {"type": "fillFromDictionary", "field": screenFieldName, "value": variableName,
            "locator": fieldConfig.get("locator")}

async def customField_SaveLocator_CreateAction(pomFields, field_value):
    variableName, screenLocator = field_value.split('|')
    logger.info(f"Custom SaveLocator: variableName={variableName}, screenLocator={screenLocator} | Action: save")

async def customField_FillLocator_CreateAction(pomFields, field_value):
    logger.info(f"Custom FillLocator: field_value={field_value} | Action: fill")

async def customField_WaitForContains_CreateAction(pomFields, field_value):
    screenFieldName, *contains = field_value.split('|')
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)
    action = {
        "type": "waitforcontains",
        "field": screenFieldName,
        "locator": fieldConfig.get("locator"),
        "role": fieldConfig.get("role"),
        "contains": contains
    }
    logger.info(f"Custom WaitForContains Action: {action}")
    return action

async def customField_WaitWhileLoading_CreateAction(pomFields, field_value):
    fieldConfig = await actionHelper.getFieldConfig(pomFields, field_value)
    action = {
        "type": "waitwhileloading",
        "field": field_value,
        "locator": fieldConfig.get("locator"),
        "role": fieldConfig.get("role")
    }
    logger.info(f"Custom WaitWhileLoading Action: {action}")
    return action

async def customField_WaitForContainsLoop_CreateAction(pomFields, field_value):
    screenFieldName, contains, loop = field_value.split('|')
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)
    action = {
        "type": "waitforcontainsloop",
        "field": screenFieldName,
        "locator": fieldConfig.get("locator"),
        "role": fieldConfig.get("role"),
        "contains": contains,
        "loop": loop
    }
    logger.info(f"Custom WaitForContainsLoop Action: {action}")
    return action

async def customField_ConfirmText_CreateAction(pomFields, field_value):
    screenFieldName, contains = field_value.split('|')
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)
    action = {
        "type": "confirmtext",
        "field": screenFieldName,
        "locator": fieldConfig.get("locator"),
        "role": fieldConfig.get("role"),
        "contains": contains
    }
    logger.info(f"Custom ConfirmText Action: {action}")
    return action

async def customField_Payment_CreateAction(pomFields, field_value):
        return {"type": "Payment", "field": "payment", "value": "key", "locator": ""}
     
async def customField_ConfirmIcon_CreateAction(pomFields, field_value):
    screenIconName, contains = field_value.split('|')
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenIconName)
    action = {
        "type": "confirmicon",
        "field": screenIconName,
        "locator": fieldConfig.get("locator"),
        "role": fieldConfig.get("role"),
        "title": fieldConfig.get("title"),
        "contains":contains
    }
    logger.info(f"Custom confirmicon Action: {action}")
    return action

async def customField_Wait_CreateAction(pomFields, field_value):
    logger.info(f"Custom Wait: wait time={field_value}")
    return {"type": "wait", "value": field_value, "field": "", "locator": "main"}

async def customField_Keyboard_CreateAction(pomFields, field_value):
    field, key = field_value.split('|')
    logger.info(f"Custom Keyboard: key={key}")
    return {"type": "keyboard", "field": field, "value": key, "locator": ""}

async def customField_ClickElementFromList_CreateAction(pomFields, field_value):
    screenFieldName, element = field_value.split('|')
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)

    action = {
        "type": "clickElementFromList",
        "field": screenFieldName,
        "locator": fieldConfig.get("locator"),
        "element": element
    }

    logger.info(f"Custom ClickElementFromList Action: {action}")
    return action

async def customField_ClickInElementFromList_CreateAction(dictionaryContext, pomFields, field_value):
    screenFieldName, text, value, actionToClick = field_value.split('|')
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)

    if "DICT." in value:
        strippedValue = value.replace("DICT.", "").strip()
        logger.info(f"Stripped Value: {strippedValue}")
        value = dictionaryContext.get(strippedValue)
        logger.info(f"Loaded Value from dictionary: {value}")

    action = {
        "type": "clickInElementFromList",
        "field": screenFieldName,
        "locator": fieldConfig.get("locator"),
        "text": text,
        "value": value,
        "actionToClick": actionToClick
    }

    logger.info(f"Custom ClickInElementFromList Action: {action}")
    return action

async def customField_SaveListField_CreateAction(pomFields, field_value):
    logger.info(f"customField_SaveListField_CreateAction: field_value: {field_value} ")
    variableName, screenFieldName, regex = field_value.split('|') 
    logger.info(f"customField_SaveListField_CreateAction: variableName={variableName}, screenFieldName={screenFieldName}, regex={regex}")
    fieldConfig = await actionHelper.getFieldConfig(pomFields, screenFieldName)
    action = {
        "type": "saveList",
        "field": screenFieldName,
        "value": variableName,
        "locator": fieldConfig.get("locator"),
        "role": fieldConfig.get("role"),
        "contains": fieldConfig.get("contains"),
        "regex": regex
    }
    return action

async def customField_Pause_CreateAction(pomFields, field_value):
    logger.info(f"customField_Pause_CreateAction")
    action = {
        "type": "pause",
        "value": field_value,
        "field": "",
        "locator": "main"
    }
    return action

async def customField_SearchRefresh_CreateAction(pomFields,field_value):
    logger.info(f"customField_SearchRefresh_CreateAction: field_value: {field_value}")
    count = field_value.count('|')

    if count == 3:
        searchBoxFieldName, searchValue, confirmFieldName, retryCount = field_value.split('|')

    elif count == 4:
        searchBoxFieldName, searchValue, confirmFieldName, searchButtonFieldName, retryCount = field_value.split('|')
        searchButtonFieldConfig = await actionHelper.getFieldConfig(pomFields, searchButtonFieldName)
        hasSearchButton = True

    searchBoxFieldConfig = await actionHelper.getFieldConfig(pomFields, searchBoxFieldName)
    confirmFieldConfig = await actionHelper.getFieldConfig(pomFields, confirmFieldName)
    

    action = {
        "type": "searchRefresh",
        "value": searchValue,
        "searchFieldLocator": searchBoxFieldConfig.get("locator"),
        "confirmFieldLocator": confirmFieldConfig.get("locator"),
        "searchButtonFieldLocator": searchButtonFieldConfig.get("locator") or "",

        "searchFieldIndex": searchBoxFieldConfig.get("index") or 0,
        "confirmFieldIndex": confirmFieldConfig.get("index") or 0,
        "searchButtonFieldIndex": searchButtonFieldConfig.get("index") or 0,

        "hasSearchButton": hasSearchButton,

        "retryCount": retryCount,
        "field": searchBoxFieldName,
        "locator": searchBoxFieldConfig.get("locator")
    }
    return action

async def customField_ScanFile_CreateAction(pomFields,field_value):
    logger.info(f"customField_ScanFile_CreateAction: field_value: {field_value}")
    
    fieldConfig = await actionHelper.getFieldConfig(pomFields,field_value)
    action = {
        "type": "scanFile",
        "locator": fieldConfig.get("locator"),
        "field": field_value,
        "value": ""
    }
    return action

async def customField_ClickField_CreateAction(pomFields, field):
    logger.info(f"customField_ClickField_CreateAction: field: {field}")
    
    fieldConfig = await actionHelper.getFieldConfig(pomFields,field)
    action = {
        "type": "click",
        "locator": fieldConfig.get("locator"),
        "field": field,
        "value": "",
        "focus": True
    }
    return action