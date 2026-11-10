import re
import requests
import logging
import json
import os
from utils.dictionary import Dictionary
from utils.workspace import data_path
from utils.errors import RequiredFieldError, addWarn, attach_allure_failure_details, allure_step, addFail

dictionary = Dictionary()
logger = logging.getLogger(__name__)

async def get_token(page, dictionaryContext, key="token", dictKey = "authToken"):
    script = f"() => window.localStorage.getItem('{key}')"
    value = await page.evaluate(script)

    if not value:
        raise ValueError(f"[TOKEN] Failed to extract token '{key}' from window.localStorage")
    dictionaryContext.set(dictKey, value)
    logger.info(f"[TOKEN] Extracted token '{key}' saved to DICT.{dictKey} ({len(value)} chars)")

def load_api_endpoints():
    with open(data_path("mappings", "apiEndpoints.json"), encoding="utf-8") as f:
        return json.load(f).get("endpoints", {})

def replace_env(url: str, envConfig: str):
    envConfig = envConfig.lower()
    
    matches = re.findall(r"(\[ENV])", url)

    if not matches:
        logger.debug("No Environment variable found in URL.")
        return url

    for key in matches:
        if envConfig is None:
            logger.error(f"Env not found")
            raise ValueError(f"[API_REQUEST] Environment variable not found.")

        url = url.replace(f"{key}", str(envConfig))
    
    logger.info(f"URL to match env: {url}")

    return url

def replace_url_placeholders(url: str, dictionaryContext) -> str:
    logger.info(f"[URL_PLACEHOLDER] Raw URL before replacement: {url}")
    matches = re.findall(r"{(.*?)}", url)

    if not matches:
        logger.debug("[URL_PLACEHOLDER] No placeholders found in URL.")
        return url

    logger.info(f"[URL_PLACEHOLDER] Found placeholders: {matches}")

    for key in matches:
        val = dictionaryContext.get(key)
        if val is None:
            logger.error(f"[URL_PLACEHOLDER] Placeholder '{key}' not found in DICT.")
            raise ValueError(f"[API_REQUEST] Placeholder '{key}' not found in DICT.")
        logger.info(f"[URL_PLACEHOLDER] Replacing '{key}' with '{val}'")
        url = url.replace(f"{{{key}}}", str(val))

    logger.info(f"[URL_PLACEHOLDER] Final resolved URL: {url}")
    return url

def resolve_dict_placeholders(text: str, dictionaryContext) -> str:
   pattern = r"DICT\.([a-zA-Z0-9_]+)"
   matches = re.findall(pattern, text)
   for key in matches:
       val = dictionaryContext.get(f"{key}")
       if val is None:
           raise ValueError(f"[DICT] Key 'DICT.{key}' not found for substitution.")
       text = text.replace(f"DICT.{key}", str(val))
   return text

async def run_api_request(endpoint_key: str, dictionaryContext):
    endpoints = load_api_endpoints()
    endpoint = endpoints.get(endpoint_key)

    if not endpoint:
        raise ValueError(f"[API_REQUEST] Endpoint '{endpoint_key}' not found!")

    method = endpoint.get("method", "GET").upper()

    raw_url = endpoint.get("url", "")
    url = ""

    envConfig = os.getenv('envConfig')

    if isinstance(raw_url, str):
        raw_url = replace_env(raw_url, envConfig)
        if raw_url.startswith("DICT."):
            url = dictionaryContext.get(raw_url)
        else:        
            url = replace_url_placeholders(raw_url, dictionaryContext)

    if not url:
        raise ValueError(f"[API_REQUEST] URL is invalid or empty for endpoint '{endpoint_key}'. Value: '{raw_url}'")

    headers = {
       k: resolve_dict_placeholders(v, dictionaryContext) if isinstance(v, str) else v
       for k, v in endpoint.get("headers", {}).items()
    }

    body = {
        k: dictionaryContext.get(v) if isinstance(v, str) and v.startswith("DICT.") else v
        for k, v in endpoint.get("body", {}).items()
    } if isinstance(endpoint.get("body", {}), dict) else {}

    try:
        # Header and body values can hold tokens/credentials: log names only
        logger.info(f"url: {url}\nheaders: {list(headers)}\nbody keys: {list(body)}")
        if method == "GET":
            res = requests.get(url, headers=headers, params=body)
        elif method == "POST":
            res = requests.post(url, headers=headers, json=body)
        else:
            raise Exception(f"Unsupported method: {method}")

        logger.info(f"res: {res}")
        res.raise_for_status()
        logger.info(f"[API_REQUEST] {method} {url} success! Status: {res.status_code}")
        logger.debug(f"[API_REQUEST] Response JSON: {res.text}")

        try:
            json_response = res.json()
        except Exception:
            logger.warning("[API_REQUEST] Response is not valid JSON.")
            json_response = {}

        save_map = endpoint.get("save", {})                         # Save response to DICT based on endpoint config
        logger.info(f"Save Map:\n{save_map}")
        for dict_key, source_key in save_map.items():
            if source_key == "$status_code":
                logger.info(f"Save From API {source_key}: Dictionary - {dict_key}: {res.status_code}")
                dictionaryContext.set(dict_key, res.status_code)
            elif source_key == "$raw":
                logger.info(f"Save From API {source_key}: Dictionary - {dict_key} ({len(res.text)} chars)")
                dictionaryContext.set(dict_key, res.text)
            else:
                val = json_response.get(source_key)
                if val is not None:
                    logger.info(f"Save From API {source_key}: Dictionary - {dict_key}")
                    dictionaryContext.set(dict_key, val)
                else:
                    logger.warning(f"[API_REQUEST] Key '{source_key}' not found in response.")

        # Store full response
        dictionaryContext.set("API_RESPONSE_JSON", json_response)

        return json_response

    except Exception as e:
        logger.error(f"[API_REQUEST] Failed: {e}")
        await attach_allure_failure_details(page=None, action=None, name="Field Error Found Screenshot", extra_label=f"Found Errors: {e}")
        await addFail(f"Error in API Request: {e}")
        raise
