from utils.dictionary import Dictionary
import utils.action_helper as ActionHelper
from utils.errors import addFail, addWarn, allure_step, attach_allure_failure_details
import asyncio
import logging
import re
import json
import pytest
from playwright.async_api import  Page,expect, async_playwright, Playwright
from playwright.async_api import Error as PlaywrightError
import warnings

import os

dictionary = Dictionary()
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

async def fillAction(page, dictionaryContext, element, action, target):
    try:
        value = action.get("value", "")
        oldValue = value
        await expect(element).to_be_visible()

        if "|" in value:
            parts = value.split('|')
            tempVal = []
            for part in parts:
                p = part.strip()
                if p.startswith("ENV."):
                    tempVal.append(os.getenv(p.replace("ENV.", "").strip(), ""))
                elif p.startswith("DICT."):
                    tempVal.append(dictionaryContext.get(p.replace("DICT.", "").strip()))
                else:
                    tempVal.append(p)
            value = " ".join([v for v in tempVal if v is not None]).strip()

        else:
            if isinstance(value, str) and value.startswith("ENV."):
                value = os.getenv(value.replace("ENV.", "").strip(), "")
                if value == "":
                    await attach_allure_failure_details(page, action=action, name="Fill Action Failure - ENV Var is Blank")
            elif isinstance(value, str) and value.startswith("DICT."):
                value = dictionaryContext.get(value.replace("DICT.", "").strip())
                if value == "":
                    await attach_allure_failure_details(page, action=action, name="Fill Action Failure - Dictionary Var is Blank")

        await element.fill(value)

        if "ENV." not in oldValue:
            logger.info(f"Filled '{target}' with '{value}'")
        else:
            logger.info(f"Filled '{target}' from ENV Variable: '{oldValue}'")
    except Exception as e:
        if "DICT." in value:
            logger.error(f"Current Dictionary values : {dictionaryContext.dump()}")
        await attach_allure_failure_details(page, action=action, name="Fill Action Failure")
        await addFail(f"Fill action failed for element {element}: {e}")

async def clickAction(page,dictionaryContext, element, target, waitForNewTab=False, focus=False, request=None):
    context = page.context
    await asyncio.sleep(.5)
    if waitForNewTab:
        try:
            new_page = asyncio.create_task(context.wait_for_event("page", timeout=3000))
        except TimeoutError:
            new_page = None
    try:
        await expect(element).to_be_visible()
        #await element.scroll_into_view_if_needed()
        #await element.evaluate("el => el.scrollIntoView({ behavior: 'auto', block: 'center', inline: 'center' })")
        if focus:
            logger.info(f"FOCUSING ELEMENT - {element}")
            await element.focus()
            await asyncio.sleep(3)

        if request != None:
            responseURL, key = request.split("|", 1)
            await saveResponse(page,element, responseURL,key, dictionaryContext)

        if not request:
            await element.click()
        await asyncio.sleep(1)
    except Exception as e:
        await attach_allure_failure_details(page, name="Click Action Failure")
        await addFail(f"Click action failed for element {element}: {e}")
    try:
        new_page = await new_page
    except Exception:
        new_page = None
    if new_page is not None:
        page = new_page
    logger.info(f"Clicked '{target}'")
    return page

async def kendodateAction(page,element,locator):
    try:
        await page.evaluate(f'document.getElementById("{locator}").value = "01/01/2000"')
        await asyncio.sleep(2)
    except Exception as e:
        await addFail(f"KendoDate action failed for element {element}: {e}")

async def setCheckedAction(page, element, locator):
    try:
        await page.evaluate(f'document.querySelector("{locator}").checked = true')
        await asyncio.sleep(1)
    except Exception as e:
        await addFail(f"SetChecked action failed for element {element}: {e}")

async def checkAction(page, element, target):
    try:
        #await page.wait_for_timeout(500)
        await element.check()
        logger.info(f"Checked '{target}'")
    except Exception as e:
        await attach_allure_failure_details(page, name="Check Action Failure")
        await addFail(f"Check action failed for element {element}: {e}")

async def hoverAction(page, element, target):
    try:
        await page.wait_for_timeout(500)
        await element.hover()
        logger.info(f"Hovered over '{target}'")
    except Exception as e:
        await attach_allure_failure_details(page, name="Hover Action Failure")
        await addFail(f"Hover action failed for element {element}: {e}")

async def dblclickAction(page, element, target):
    try:
        await element.dblclick()
        logger.info(f"Double-clicked '{target}'")
    except Exception as e:
        await attach_allure_failure_details(page, name="Double-Click Action Failure")
        await addFail(f"Double-click action failed for element {element}: {e}")

async def selectAction(page, dictionaryContext, element ,action, target, waitForNewTab):
    context = page.context
    await asyncio.sleep(.5)
    if waitForNewTab:
        try:
            new_page = asyncio.create_task(context.wait_for_event("page", timeout=3000))
        except TimeoutError:
            new_page = None
    try:
        value = action.get("value", "")
        
        # support pipe-join + ENV / DICT tokens
        if "|" in value:
            parts = value.split('|')
            out = []
            for part in parts:
                p = part.strip()
                if p.startswith("ENV."):
                    out.append(os.getenv(p.replace("ENV.", "").strip(), ""))
                elif p.startswith("DICT."):
                    out.append(dictionaryContext.get(p.replace("DICT.", "").strip()))
                else:
                    out.append(p)
            value = " ".join([v for v in out if v is not None]).strip()

        # single token cases
        if isinstance(value, str) and value.startswith("ENV."):
            value = os.getenv(value.replace("ENV.", "").strip(), "")
        elif isinstance(value, str) and value.startswith("DICT."):
            value = dictionaryContext.get(value.replace("DICT.", "").strip())

        if value.startswith("index="):
            idx = int(value.replace("index=", "").strip())
            await element.select_option(index=idx)
        else:
            await element.select_option(value)
            logger.info(f"Selected '{value}' for '{target}'")
    except Exception as e:
        await attach_allure_failure_details(page, action=action, name="Select Action Failure")
        await addFail(f"Select action failed for element {element}: {e}")
    try:
        new_page = await new_page
    except Exception:
        new_page = None
    if new_page is not None:
        page = new_page
    return page

async def numberAction(page, element, value):
    try:
        await element.press(value)
    except Exception as e:
        await attach_allure_failure_details(page, name="Number Action Failure")
        await addFail(f"Number action failed for element {element}: {e}")

async def sladocumentAction(action, page, element):
    try:
        value = action.get("value", "").replace("\\", "/")
        rows = await page.query_selector_all("tr button[alt='Click to upload file']")
        for row in range(0, len(rows)):
            #await asyncio.sleep(1)
            await page.wait_for_timeout(1000)
            async with page.expect_file_chooser() as fc_info:
                rowsButton = page.locator("tr button[alt='Click to upload file']").nth(row)
                await rowsButton.click()
                file_chooser = await fc_info.value
                await page.wait_for_timeout(1000)
                await file_chooser.set_files(value)
    except Exception as e:
        await attach_allure_failure_details(page, action=action, name="SLA Document Action Failure")
        await addFail(f"SLA Document upload failed for element {element}: {e}")

async def correctionDocumentAction(action, page, element):
    try:
        errorSelectLocator = "select[id='docErrorSelectId']"
        listLocator = "select[id='docErrorSelectId'] option"
        documentTypeLocator = "select[id='extraDocumentSelectId']"

        fileUploadLocator = action.get("locator")

        errorSelect = page.locator(errorSelectLocator)
        optionList = page.locator(listLocator)
        documentTypeSelect = page.locator(documentTypeLocator)

        count = await optionList.count()
        value = action.get("value", "").replace("\\", "/")

        for i in range(count):
            await expect(errorSelect).to_be_visible()
            await errorSelect.select_option(index=i)

            await expect(documentTypeSelect).to_be_visible()
            await documentTypeSelect.select_option(index=i)

            await asyncio.sleep(3)
            await page.wait_for_timeout(1000)
            async with page.expect_file_chooser() as fc_info:
                await page.click(fileUploadLocator)
                file_chooser = await fc_info.value
                await page.wait_for_timeout(1000)
                await file_chooser.set_files(value)
            await asyncio.sleep(5)
    except Exception as e:
        await attach_allure_failure_details(page, action=action, name="Correction Document Action Failure")
        await addFail(f"Correction Document upload failed for element {element}: {e}")

async def documentAction(action, page, element):
    try:
        value = action.get("value", "").replace("\\", "/")
        await asyncio.sleep(3)
        await page.wait_for_timeout(1000)
        await element.set_input_files(value)
    except Exception as e:
        await attach_allure_failure_details(page, action=action, name="Document Action Failure")
        await addFail(f"Document upload failed for element {element}: {e}")

async def fileChooserDocumentAction(action, page, element):
    try:
        value = action.get("value", "").replace("\\", "/")
        logger.info(f"fileChooserDocumentAction - value: {value}")
        locator = action.get("locator")
        logger.info(f"fileChooserDocumentAction - locator: {locator}")
        try:
            index = action.get("index")
            if (index == None):
                index = 0
            logger.info(f"fileChooserDocumentAction - index: {index}")
        except:
            index = 0
            logger.info(f"No Index on fileChooserDocumentAction")
        #await asyncio.sleep(1)
        await page.wait_for_timeout(1000)
        
        logger.info(f"fileChooserDocumentAction - page.expect_file_chooser")
        async with page.expect_file_chooser() as fc_info:
            button = page.locator(locator).nth(index)
            logger.info(f"fileChooserDocumentAction - clicking button: {button}")
            await button.click()
            file_chooser = await fc_info.value
            await page.wait_for_timeout(1000)
            logger.info(f"fileChooserDocumentAction - set files: ")
            await file_chooser.set_files(value)
            logger.info(f"fileChooserDocumentAction - files set - COMPLETE: ")
    except Exception as e:
        await attach_allure_failure_details(page, action=action, name="File Chooser Document Action Failure")
        await addFail(f"File Chooser Document Document upload failed for element {element}: {e}")

async def fillFromDictionaryAction(page, dictionaryContext, action,element,target):
    try:
        fieldName = action.get("field")
        variable = action.get("value")
        fillValue = dictionaryContext.get(variable)
        await element.fill(fillValue)
        logger.info(f"Filled '{target}' from dictionary with value '{fillValue}'")
    except Exception as e:
        logger.error(f"Current Dictionary values : {dictionaryContext.dump()}")
        await attach_allure_failure_details(page, action=action, name="Fill From Dictionary Action Failure")
        await addFail(f"FillFromDictionary action failed for field {fieldName}: {e}")

async def saveAction(page, dictionaryContext, action,element, locator = None):
    logger.info(f"saveAction: - action: {action} \n- element: {element} \n- locator: {locator}")
    try:
        saveValue = await ActionHelper.extract_value(element)
        logger.info(f"saveValue: {saveValue}")
        try:            
            regex = action.get("regex")
        except:
            regex = "[^]*"
        variable = action.get("value")
        if regex:
            match = re.search(regex, saveValue)
            if match:
                logger.info(f"match was found {match.group(0)}")
                result = match.group(1)
                logger.info(f" resulting regex is {result}")
                dictionaryContext.set(variable, result)
                logger.info(f"Extracted and saved '{result}' to '{variable}'")
            else:
                logger.info("No regex match found; saving raw value")
        else:
            logger.info("No regex provided; skipping extraction")
            saveValue = saveValue.replace("Application ID:", "").strip()
            if "EXQ-" in saveValue:
                saveValue = saveValue.replace("EXQ-", "").strip()
            if "PIN:" in saveValue:
                saveValue = saveValue.split(':')[1]
            dictionaryContext.set(variable, saveValue)
            logger.info(f"Saved '{saveValue}' to '{variable}'")
    except Exception as e:
        try:
            saveValue = saveValue.replace("Application ID:", "").strip()
            if "EXQ-" in saveValue:
                saveValue = saveValue.replace("EXQ-", "").strip()
            dictionaryContext.set(variable, saveValue)
            logger.info(f"Saved '{saveValue}' to '{variable}'")
        except Exception as e:
            await attach_allure_failure_details(page, action=action, name="Save Action Failure")
            await addFail(f"Save action failed for element {element}: {e}")

async def waitForContainsAction(action, dictionaryContext, element, page):
    found = False
    contains = action.get("contains")
    for _ in range(10):
        if found:
            break
        try:
            for val in contains:
                try:
                    if "DICT." in val:
                        strippedValue = val.replace("DICT.", "").strip()
                        val = dictionaryContext.get(strippedValue)
                    await expect(element).to_contain_text(val)
                    found = True
                    break
                except:
                    continue
            if not found:
                raise Exception(f"Contains failed: {contains} - val: {val}")
        except Exception as e:
            await page.reload()
    if not found:
        await attach_allure_failure_details(page, action=action, name="Wait For Contains Action Failure")
        logger.error(f"Contains '{contains}' not found on element: {element} - Done waiting")

async def waitWhileLoadingAction(action,element, page):
    timeout = 30
    end_time = asyncio.get_event_loop().time() + timeout
    logger.info(f"waitWhileLoadingAction - element: {element} - action: {action}")
    while True:
        count = await element.count()
        logger.info(f"waitWhileLoadingAction - count: {count}")
        if count == 0:
            return
        
        logger.info(f"waitWhileLoadingAction - asyncio.get_event_loop().time():  {asyncio.get_event_loop().time()}  ?=? end_time:  {end_time}")
        if asyncio.get_event_loop().time() > end_time:
            raise TimeoutError(f"Still loading after {timeout}s")
        await asyncio.sleep(0.25)

async def confirmTextAction(page, dictionaryContext, action, element):
    found = False
    logger.info(f"confirmTextAction - element: {element} - action: {action}")
    contains = action.get("contains")
    if "DICT." in contains:
        strippedValue = contains.replace("DICT.", "").strip()
        contains = dictionaryContext.get(strippedValue)
    for _ in range(10):
        try:
            await expect(element).to_contain_text(contains)
            found = True
            break
        except Exception as e:
            await addWarn(f"Attempt to confirm text '{contains}' in element {element} failed")
            #await asyncio.sleep(3)

    if not found:
        await attach_allure_failure_details(page, action=action, name="Confirm Text Action Failure")
        await addFail(f"Unable to confirm text on screen: {contains}")


async def Payment(action,page):
    frame = page.frame_locator("#id_payWithConvergeFrame")

    md_card = frame.locator("md-card#id_modal")
    await md_card.wait_for(state="visible")
    await md_card.locator("#ssl_card_number").fill("4111111111111111")
    await md_card.locator("#ssl_exp_date").fill("0229")
    await md_card.locator("#ssl_cvv2cvc2").fill("6467")
    await md_card.locator("#ssl_avs_address").fill("1220 Washington Ave Albany NY")
    await md_card.locator("#ssl_avs_zip").fill("12206")
    await md_card.locator("button:has-text('Pay')").click()

async def fillDate(action,page):
    date = action.get("value")
    await page.keyboard.type(date)

async def confirmIconAction(action, dictionaryContext, element):
    found = False
    logger.info(f"confirmIconAction - element: {element} - action: {action}")
    contains = action.get("contains")
    if "DICT." in contains:
        strippedValue = contains.replace("DICT.", "").strip()
        contains = dictionaryContext.get(strippedValue)
    for _ in range(10):
        try:
            await expect(element).to_have_class(contains)
            found = True
            break
        except Exception as e:
            await addWarn(f"Attempt to confirm icon '{contains}' in element {element} failed")
            #await asyncio.sleep(3)

    if not found:
        await attach_allure_failure_details(action=action, name="Confirm Icon Action Failure")
        await addFail(f"Unable to confirm icon on screen: {contains}")

async def waitAction(action):
    try:
        wait = int(action.get("value"))
        await asyncio.sleep(wait)
        logger.info("Wait completed")
    except (TypeError, ValueError):  # bad wait value: log and carry on (cancellation must still propagate)
        logger.error(f"Failed to wait - Action: {action}")
        await attach_allure_failure_details(page=None, action=action, name="Wait Action Failure")
        #await addFail(f"Failed to wait - Action:{action}")   - Should we fail here?

async def keyboardAction(action, page):
    try:
        key = action.get("value")
        await asyncio.sleep(.5)
        await page.keyboard.press(key)
    except:
        logger.error(f"Failed to use keyboard - Action: {action}")
        await attach_allure_failure_details(page, action=action, name="Keyboard Action Failure")

async def clickElementFromList(action, page):

    try:
        locator = action["locator"]
        element = action["element"]
        
        print(f"action: {action}") 
        element = await ActionHelper.getElementFromListByElement(page, locator, element)

        print(f"element To Click: {element}")
        await element.click()

    except:
        logger.error(f"Failed to click in Element - Action: {action}")
        await attach_allure_failure_details(page, action=action, name="Click On Element From List Action Failure")

async def clickInElementFromList(action, page):
    try:
        locator = action["locator"]
        text = action["text"]
        value = action["value"]
        actionToClick = action["actionToClick"]
        
        print(f"action: {action}") 
        element = await ActionHelper.getElementFromListByText(page, locator, text, value)
        print(f"element: {element}")

        await ActionHelper.performElementAction(element,actionToClick)

    except:
        logger.error(f"Failed to click in Element - Action: {action}")
        await attach_allure_failure_details(page, action=action, name="Click In Element From List Action Failure")

async def saveListAction(page, dictionaryContext, action, element): 
    logger.info(f"saveListAction: - action: {action} \n- element: {element}")
    try:
        saveValue = await ActionHelper.extract_value(element)
        logger.info(f"saveValue: {saveValue}")
        
        x = 0
        if saveValue: 
            valueList = saveValue.strip().split(" ")
            for value in valueList:
                variable = action.get("value")
                x = x + 1
                variable = f"{variable}[{x}]"
                dictionaryContext.set(variable,value) 
                logger.info(f"Saved {value} to variable '{variable}'")
    except Exception as e: 
        logger.error(f"saveListAction failed for element {element}: {e}")
        await attach_allure_failure_details(page, action=action, name="Save List Action Failure")
        await addFail(f"saveListAction failed for element {element}: {e}")

async def pauseAction(action, page):
    try:
        await page.pause()
        logger.info("Pause completed")
    except:
        logger.error(f"Failed to Pause - Action: {action}")
        await attach_allure_failure_details(page, action=action, name="Pause Action Failure")

async def searchRefresh(action, dictionaryContext, page):
    await asyncio.sleep(.5)
    try:
        found = False
        searchValue = action.get("value")
        searchFieldLocator = action.get("searchFieldLocator")
        confirmFieldLocator = action.get("confirmFieldLocator")
        searchFieldIndex = action.get("searchFieldIndex")
        confirmFieldIndex = action.get("confirmFieldIndex")
        retryCount = int(action.get("retryCount"))
        hasSearchButton = action.get("hasSearchButton")

        if hasSearchButton:
            searchButtonLocator = action.get("searchButtonFieldLocator")
            searchButtonIndex = action.get("searchButtonFieldIndex")
            searchButton = page.locator(searchButtonLocator).nth(searchButtonIndex)

        logger.info(f"Search Refresh action: {action}")

        searchField = page.locator(searchFieldLocator).nth(searchFieldIndex)
        logger.info(f"Got search field - {searchField}")


        pattern = r"DICT\.([\w]+)"
        matches = re.findall(pattern, confirmFieldLocator)

        if matches:
            for match in matches:
                value = dictionaryContext.get(match)
                if value is None:
                    logger.error(f"[EA] ERROR: Dictionary key '{match}' not found.")
                    return None
                confirmFieldLocator = confirmFieldLocator.replace(f"DICT.{match}", str(value))

        confirmField = page.locator(confirmFieldLocator).nth(confirmFieldIndex)
        logger.info(f"Got Confirm Field - {confirmField}")

        if "DICT." in searchValue:
            strippedValue = searchValue.replace("DICT.", "").strip()
            searchValue = dictionaryContext.get(strippedValue)

        for tryCount in range(retryCount):
            try:    
                logger.info(f"Count #{tryCount}: Try to fill - {searchValue} in field {searchField}")
                await searchField.fill(searchValue)
                if hasSearchButton:
                    await asyncio.sleep(1)
                    await searchButton.click()
                logger.info(f"Check for text {searchValue} in field: {confirmField}")
                await asyncio.sleep(3)
                await expect(confirmField).to_contain_text(searchValue)
                logger.info(f"Found")
                found = True
                break
            except:
                logger.info(f"Try #{tryCount}: Didn't confirm {searchValue}'")
                #await page.reload()
                await searchField.click()
                await page.keyboard.press('Control+A') 
                await page.keyboard.press('Backspace')
                await asyncio.sleep(15)
        if not found:
            logger.error(f"Failed to searchRefresh: {searchValue} - Action: {action}")
            await attach_allure_failure_details(page, action=action, name="Search Refresh Action Failure")
            await addFail(f"searchRefresh failed")
    except Exception as e:
        logger.error(f"Failed to searchRefresh - Action: {action}")
        logger.error(f"Exception: {e}")
        await attach_allure_failure_details(page, action=action, name="Search Refresh Action Failure")
        await addFail(f"searchRefresh failed: {e}")

async def scanFile(action, element, page):
    from pywinauto import Application
    try:
        await asyncio.sleep(5)

        # Click "Select"
        app = Application(backend='win32').connect(title='Select Source')
        dlg = app['Select Source']
        dlg['ListBox'].select(0)
        dlg['Select'].click()

        # Focus TWAIN window
        await asyncio.sleep(5)
        app = Application(backend='uia').connect(title='TWAIN Sample DS')
        dlg = app.window(title='TWAIN Sample DS')
        dlg.set_focus()

        # Send TAB 16 times + Enter
        dlg.type_keys('{TAB 16}{ENTER}', pause=0.05)

    except Exception as e:
        logger.error(f"Failed to scanFile - Action: {action}")
        logger.error(f"Exception: {e}")
        await attach_allure_failure_details(page, action=action, name="Scan File Action Failure")
        await addFail(f"scanFile failed: {e}")
async def gathersnap(action,page):
    try:

        snapshot = await page.locator(action.get("locator")).aria_snapshot()
        print(snapshot)
    except Exception as e:
        logger.error(f"Failed to Snapshot - Action: {action}"),
        logger.error(f"Exception: {e}")
        await attach_allure_failure_details(page, action=action, name="Snapshot Failure")
        await addFail(f"Unable to snapshot {action.get("locator")}")


async def downloadFile(action, element, page):
    try:
        async with page.expect_download() as download_info:
           await page.locator(action.get("locator")).click()
        download = await download_info.value
        # The site picks suggested_filename: keep only the base name so it can't leave reports/downloads
        filename = os.path.basename(download.suggested_filename.replace("\\", "/")) or "download"
        if filename in (".", ".."):
            filename = "download"
        await download.save_as("./reports/downloads/" + filename)

    except Exception as e:
        logger.error(f"Failed to download - Action: {action}"),
        logger.error(f"Exception: {e}")
        await attach_allure_failure_details(page, action=action, name="Download Failure")
        await addFail(f"Unable to download file: {action.get("locator")}")


async def saveResponse(page, element, responseURL: str, key: str, dictionaryContext):
    async with page.expect_response(
            responseURL,
            timeout=10000  # Specify the timeout here
    ) as response_info:
        await element.click()

    response = await response_info.value
    response_body = await response.json()

    agenda_no = response_body.get(key)
    dictionaryContext.set(key, agenda_no)

