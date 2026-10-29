import warnings
import pytest
import logging
import allure
import os
from datetime import datetime
import time

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

recordingConfig = os.getenv("recording", "false").lower()
tracingConfig = os.getenv("tracing", "false").lower()

class RequiredFieldError(Exception):
    pass

async def addWarn(msg):
    logger.warning(f"Adding Warning - {msg}")
    warnings.warn(UserWarning(f"Warning - {msg}"))

async def addFail(msg):
    #current_page, current_action, current_actions = getFailInfo()
    logger.error(f"Adding Failure - {msg}")
    #pytest.fail(f"Failure - [Current Page = {current_page}] - [Current Action = {current_action}] - [Action List = {current_actions} - \n Failure Message: {msg}")
    pytest.fail(f"Failure: {msg}")

async def attach_allure_failure_details(page=None, action=None, name="Failure Screenshot", extra_label=None):
    try:
        if not page:
            logger.warning("Page is None, skipping allure attachment.")
            return

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.makedirs("reports/screenshots", exist_ok=True)
        os.makedirs("reports/traces", exist_ok=True)

        if tracingConfig == "true":
            # 1. Stop and save trace immediately
            trace_path = f"reports/traces/{name}_{timestamp}_trace.zip"
            try:
                await page.context.tracing.stop(path=trace_path)
                page._trace_path = trace_path
            except Exception as e:
                logger.warning(f"[WARN] Failed to stop/save trace: {e}")

        # 2. Screenshot
        screenshot_path = f"reports/screenshots/{name}_{timestamp}.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        allure.attach.file(screenshot_path, name=name, attachment_type=allure.attachment_type.PNG)

        # 3. Attach Video (if recorded)
        if recordingConfig == "true" and hasattr(page, "video") and page.video:
            try:
                logger.info("Trying to attach video from within errors.py")
                video = getattr(page, "video", None)
                logger.info(f"Video : {video}")
                if video:
                    page._deferred_video = video
                    logger.info(f"page._deferred_video : {page._deferred_video}")
                #video_path = await page.video.path()
                #page._deferred_video = page.video if hasattr(page, "video") else None
                #allure.attach.file(video_path, name=f"{name}_Recording", attachment_type=allure.attachment_type.MP4)
            except Exception as e:
                logger.warning(f"[WARN] Could not attach video: {e}")

        # 4. Attach Trace (if available)
        if hasattr(page, "_trace_path") and os.path.exists(page._trace_path):
            try:
                allure.attach.file(page._trace_path, name=f"{name}_Trace", attachment_type="application/zip")
                trace_note = f"To view this trace:\n\nnpx playwright show-trace \"{page._trace_path}\""
                allure.attach(trace_note, name="HowToOpen_Trace", attachment_type=allure.attachment_type.TEXT)
            except Exception as e:
                logger.warning(f"Failed to attach trace file: {e}")

        # 5. Attach Action JSON (if available)
        if action:
            allure.attach(str(action), name="Action JSON", attachment_type=allure.attachment_type.TEXT)

        # 6. Attach page URL
        try:
            url = await page.url
            allure.attach(url, name="Page URL", attachment_type=allure.attachment_type.TEXT)
        except:
            logger.warning("Page URL unavailable")

        # 7. Attach full HTML DOM snapshot
        try:
            dom_html = await page.content()
            allure.attach(dom_html, name="DOM Snapshot", attachment_type=allure.attachment_type.HTML)
        except:
            logger.warning("DOM snapshot unavailable")

        # 8. Extra context label
        if extra_label:
            allure.dynamic.label("context", extra_label)

    except Exception as e:
        logger.error(f"Failed to attach Allure diagnostics: {e}")


# Standard Allure step wrapper
def allure_step(title):
    return allure.step(title)