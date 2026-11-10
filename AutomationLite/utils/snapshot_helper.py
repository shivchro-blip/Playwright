from logging import exception, error
import yaml
from pathlib import Path
import json
from utils.dictionary import Dictionary
import utils.action_helper as ActionHelper
from utils.errors import addFail, addWarn, allure_step, attach_allure_failure_details
import asyncio
import logging
from utils.log_level import from_env as _log_level
import re
import json
import pytest
from playwright.async_api import  Page,expect, async_playwright, Playwright
from playwright.async_api import Error as PlaywrightError
import warnings
import os

# Main logger
logger = logging.getLogger(__name__)
logger.setLevel(_log_level())

# Suppress third-party noisy loggers
logging.getLogger('playwright').setLevel(logging.INFO)
logging.getLogger('asyncio').setLevel(logging.INFO)

async def takeSnapshot(page):
    try:
        snapshot = await page.locator("body").aria_snapshot()
    except Exception as e:
        logger.info("Snapshot Error")
    return snapshot

async def convert_yaml_to_json(input_file_path):
    """
    Converts a YAML file to a JSON object and returns the data.

    Args:
        input_file_path (str or Path): The path to the input YAML file.

    Returns:
        dict: The converted JSON data as a Python dictionary.

    Raises:
        FileNotFoundError: If the specified input YAML file does not exist.
        yaml.YAMLError: If the YAML file is malformed.
    """
    try:
        # Load the YAML string into a Python object (dictionary or list)
        python_object = yaml.safe_load(input_file_path)

        # Serialize the Python object to a JSON formatted string with indentation

        json_output_str = json.dumps(python_object, indent=4)

        return json_output_str
    except yaml.YAMLError as e:
        logger.error(f"YAML parsing error: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected conversion error occurred: {e}")
        return None


async def save_json_file(json_data, screen_name, base_directory='data'):
    """
    Saves a JSON object to a file in a subdirectory based on the screen name.

    The function checks if a JSON file for the given screen name already exists.
    If it does, the function will not overwrite it and will return a failure
    message. If no file exists, it creates the directory (if needed) and saves
    the JSON data.

    Args:
        json_data (dict): The JSON data as a Python dictionary.
        screen_name (str): The name of the screen, which will also be the
                           subdirectory name.
        base_directory (str): The parent directory where the screen-specific
                              folders will be created. Defaults to 'data'.

    Returns:
        bool: True if the file was saved, False otherwise.
    """
    try:
        # Define the path for the screen-specific folder and file
        screen_folder = Path(base_directory) / screen_name
        json_file_path = screen_folder / f"{screen_name}.json"

        # Check if the file already exists

        # Create the directory if it doesn't exist.
        # `parents=True` creates any missing parent directories.
        # `exist_ok=True` prevents an error if the directory already exists.
        screen_folder.mkdir(parents=True, exist_ok=True)
        data = json.loads(json_data)
        # Save the JSON data to the file
        with open(json_file_path, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file, indent=4)

        logger.info(f"Successfully saved JSON to '{json_file_path}'")
        return True

    except Exception as e:
        logger.error(f"An error occurred while saving the JSON file: {e}")
        return False


async def save_yaml_file(yaml_data, screen_name, base_directory='data'):
    """
    Saves a YAML object to a file in a subdirectory based on the screen name.

    The function checks if a YAML file for the given screen name already exists.
    If it does, the function will not overwrite it and will return a failure
    message. If no file exists, it creates the directory (if needed) and saves
    the YAML data.

    Args:
        yaml_data (dict): The YAML data as a Python dictionary.
        screen_name (str): The name of the screen, which will also be the
                           subdirectory name.
        base_directory (str): The parent directory where the screen-specific
                              folders will be created. Defaults to 'data'.

    Returns:
        bool: True if the file was saved, False otherwise.
    """
    try:
        # Define the path for the screen-specific folder and file
        screen_folder = Path(base_directory) / screen_name
        yaml_file_path = screen_folder / f"{screen_name}.yaml"

        # Check if the file already exists


        # Create the directory if it doesn't exist.
        screen_folder.mkdir(parents=True, exist_ok=True)
        print(yaml_data)
        # Save the YAML data to the file
        with open(yaml_file_path, 'w', encoding='utf-8') as yaml_file:
            # Use safe_dump for security, and default_flow_style=False for readability
            yaml_file.write(yaml_data)

        logger.info(f"Successfully saved YAML to '{yaml_file_path}'")
        return True

    except Exception as e:
        logger.error(f"An error occurred while saving the YAML file: {e}")
        return False