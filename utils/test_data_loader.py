import json
import os
import logging
import asyncio
import time
from dotenv import load_dotenv
from utils.workspace import resolve_workspace_dir

load_dotenv()


def _normalize_selected_tags(selected_tags):
    if isinstance(selected_tags, str):
        raw_tags = selected_tags.split(",")
    else:
        raw_tags = selected_tags or []

    normalized = []
    for tag in raw_tags:
        clean = str(tag or "").strip()
        if clean:
            normalized.append(clean)

    return normalized

workspaceDir = resolve_workspace_dir()

# Set up logging configuration
logging.basicConfig(
    level=logging.WARNING,  # Ignore INFO logs
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Main logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# Suppress third-party noisy loggers
logging.getLogger('playwright').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)


def load_test_cases_from_json(file_path, selected_tags, selected_modules, selected_env):
    with open(file_path, "r", encoding="utf-8") as file:
      logger.info(f"Try to load test case: {file}")
    test_data = json.load(file)
    return [
        test_case for test_case in test_data["testCases"]
        if (not selected_tags or any(tag in selected_tags for tag in test_case["tags"]))
           and (not selected_modules or test_case["module"] in selected_modules)
           and (not selected_env or any(env in selected_env for env in test_case["environment"]))
    ]


def load_test_cases_from_folder(folder_path, selected_tags=[], selected_modules=[], selected_env=[]):
    logger.warning("~~~ Loading Test Cases From Folder ~~~")
    test_cases = []
    skipped_no_tags = []
    skipped_no_steps = []
    normalized_tags = _normalize_selected_tags(selected_tags)
    normalized_tag_set = {tag.casefold() for tag in normalized_tags}
    allow_all = "" in normalized_tag_set

    logger.warning(f"[DEBUG LOG] folder_path evaluated to: {folder_path}")
    logger.warning(f"[DEBUG LOG] selected_tags evaluated to: {normalized_tags}")
    
    if os.path.exists(folder_path):
        for dirpath, dirnames, filenames in os.walk(f"{folder_path}\\MasterRegressionSuite\\"):
            for file_name in filenames:
                if not file_name.endswith(".json"):
                    continue  # Skip non-JSON files
                logger.info("-> Reading Test File: %s", file_name)
                file_path = os.path.join(dirpath, file_name)    
                with open(file_path, "r", encoding="utf-8") as file:
                    test_data = json.load(file)
                    for test_case in test_data.get("testCases", []):
                        test_case_id = test_case.get('testCaseID', 'Unknown')
                        test_tags = test_case.get('tags', [])
                        logger.info("  -> Found Test Case: %s | Tags: %s", test_case_id, test_tags)
                        
                        test_case['filename'] = file_name
                        logger.info(f"TEST CASE FILE NAME: {test_case.get('filename')}")
                        
                        if not test_case.get("steps"):
                            logger.error("[ERROR] Test Case '%s' has NO STEPS!", test_case_id)
                            skipped_no_steps.append(test_case_id)
                            continue
                            
                        foundTag = allow_all or any(
                            str(tag or "").strip().casefold() in normalized_tag_set
                            for tag in test_tags
                        )
                        if foundTag:
                            test_cases.append(test_case)
                        else:
                            logger.debug("No matching tag found for Test Case: %s", test_case_id)
                            skipped_no_tags.append((test_case_id, test_tags))
                            
        # Print a high-level summary using WARNING level so it's always visible
        summary = [
            "\n" + "="*60,
            " TEST EXECUTION SUMMARY",
            "="*60,
            f" Target Tags:         {normalized_tags}",
            f" Total Tests Found:   {len(test_cases) + len(skipped_no_tags) + len(skipped_no_steps)}",
            f" Tests Selected:      {len(test_cases)}",
            f" Tests Skipped:       {len(skipped_no_tags) + len(skipped_no_steps)}"
        ]
        
        if test_cases:
            summary.append("\n --- SELECTED TESTS (WILL RUN) ---")
            for tc in test_cases:
                summary.append(f"  [RUN] {tc.get('testCaseID')} (Tags: {tc.get('tags', [])})")
                
        if skipped_no_tags or skipped_no_steps:
            summary.append("\n --- SKIPPED TESTS ---")
            for t_id, tags in skipped_no_tags:
                summary.append(f"  [SKIP - No Tag Match] {t_id} (Tags: {tags})")
            for t_id in skipped_no_steps:
                summary.append(f"  [SKIP - No Steps] {t_id}")
                
        summary.append("="*60 + "\n")
        logger.warning("\n".join(summary))
        
        if not test_cases:
            logger.warning(f"[DEBUG LOG] ZERO tests loaded from {folder_path} but the directory exists! Tags matched: {normalized_tags}")
            import sys
            print(f"[ERROR] ZERO tests loaded from {folder_path} but the directory exists! Tags matched: {normalized_tags}", file=sys.stderr)
            
    else:
        logger.warning(f"[DEBUG LOG] Directory does not exist: {folder_path}")
        logger.error("Test Directory %s not found.", folder_path)
        import sys
        print(f"[ERROR] Test Directory does not exist: {folder_path}", file=sys.stderr)
        
    return test_cases

def load_all_test_cases(root_folder_OLD, selected_tags=[], selected_modules=[], selected_env=[]):
    logger.info("~~~ Loading ALL Test Cases From ALL Systems ~~~")
    test_cases = []

    root_folder = os.path.join(workspaceDir, "Tests")
    if not os.path.exists(root_folder):
        logger.error("Root Test Directory %s not found.", root_folder)
        return []

    for sys_folder in os.listdir(root_folder):
        sys_path = os.path.join(root_folder, sys_folder, "MasterRegressionSuite")
        if not os.path.isdir(sys_path):
            continue  # Skip non-system folders

        for dirpath, _, filenames in os.walk(sys_path):
            for file_name in filenames:
                if not file_name.endswith(".json"):
                    continue
                file_path = os.path.join(dirpath, file_name)
                logger.info("-> Reading Test File: %s", file_path)

                try:
                    with open(file_path, "r", encoding="utf-8") as file:
                        test_data = json.load(file)

                    for test_case in test_data.get("testCases", []):
                        test_id = test_case.get("testCaseID", "<No ID>")
                        test_tags = test_case.get("tags", [])
                        test_steps = test_case.get("steps", [])

                        logger.info("  -> Found Test Case: %s | Tags: %s", test_id, test_tags)

                        if not test_steps:
                            logger.error("[ERROR] Test Case '%s' has NO STEPS!", test_id)
                            continue

                        # Tag filtering
                        #if selected_tags:
                        #    if not any(tag in selected_tags for tag in test_tags):
                        #        logger.debug("No matching tag found for Test Case: %s", test_id)
                        #        continue

                        # TODO: Add module/env filtering if needed
                        test_cases.append(test_case)

                except Exception as e:
                    logger.error("[ERROR] Failed to read %s: %s", file_path, e)

    logger.info("Total Loaded Test Cases: %d", len(test_cases))
    return test_cases

def load_api(root_folder):
    logger.info("~~~ Loading ALL API Endpoints ~~~")
    apiEndpoints = []

    if not os.path.exists(root_folder):
        logger.error("Root Directory %s not found.", root_folder)
        return []

        file_path = os.path.join(root_folder, "data/mappings/apiEndpoints.json")
        logger.info("-> Reading API File: %s", file_path)

        try:
            with open(file_path, "r", encoding="utf-8") as file:
                data = json.load(file)

            endpoints = data.get("endpoints", {}) 

            for endpointName, endpointData in endpoints.items():
                apiEndpoints[endpointName] = endpointData
                logger.info(f"Loaded API Endpoint: {endpointName}\nData: {endpointData}")

        except Exception as e:
            logger.error("[ERROR] Failed to read %s: %s", file_path, e)

    logger.info("Total Loaded API Endpoints: %d", len(apiEndpoints))
    return apiEndpoints