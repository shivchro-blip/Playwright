import json
import random
import re
import ast
import logging
import asyncio
import faker

# Set up logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Custom RandGen class for dynamically generating random test data using customization rules for automating testing
class RandGen:
    def __init__(self, config_file, cwd):
        try:
            with open(config_file, "r") as file:
                self.config = json.load(file)

            self.cwd = cwd
            self.namespace = {}
            self.prepare_namespace()
            self.rand_gen_rules = self.config.get("rand_gen_rules", {})
            self.dynamic_objects = self.config.get("dynamic_objects", {})
            self.custom_functions = self.config.get("custom_functions", {})
            self.build_dynamic_objects()
            self.build_custom_functions()

            self.selected_address = self.select_random_address()
            self.selected_custom = self.select_random_custom()
        except Exception as e:
            logger.error(" __init__() - Error loading configuration: config_file: '%s' Error: %s", config_file, str(e))


    # Inject cwd into the function namespace
    def prepare_namespace(self):
        self.namespace["cwd"] = self.cwd



    # Pick one address randomly (use for all address fields)
    def select_random_address(self):
        try:
            address_list = self.config.get("address_list", [])
            selected_addresses = [addr for addr in address_list if addr.get("use", False)]
            if not selected_addresses:
                return {}
            return random.choice(selected_addresses)
        except Exception as e:
            logger.error(" select_random_address() - Error selecting random_address: %s", str(e))



    # Pick one custom randomly (use for all custom fields)
    def select_random_custom(self):
        try:
            custom_list = self.config.get("custom_list", [])
            selected_customs = [custom for custom in custom_list if custom.get("use", False)]
            if not selected_customs:
                return {}
            return random.choice(selected_customs)
        except Exception as e:
            logger.error(" select_random_custom() - Error selecting random_custom: %s", str(e))



    # Dynamically create and register objects
    def build_dynamic_objects(self):
        try:
            for key, code in self.dynamic_objects.items():
                exec(code, self.namespace)
                self.namespace[key] = self.namespace.get(key)
        except Exception as e:
            logger.error(" build_dynamic_objects() - Error executing dynamic_objects: '%s' Error: '%s'", self.dynamic_objects, str(e))



    # Compile and register all functions from code into namespace
    def build_custom_functions(self):
        try:
            for key, code in self.custom_functions.items():
                exec(code, self.namespace)
        except Exception as e:
            logger.error(" build_custom_functions() - Error executing custom_functions: '%s' Error: '%s'", self.custom_functions, str(e))


    # Split functionName(param1, param2) into a functionNmame and argument values
    # returns functionName and a list of arguments
    def parse_call(self, expression):
        try:
            match = re.match(r"^(\w+)(\((.*)\))?$", str(expression))
            logger.info(f"match: {match}")
            if not match:
                logger.error(" parse_call() -Invalid expression format: '%s'", expression)
            name = match.group(1)
            params = match.group(3)
            logger.info(f"name: {name}")
            logger.info(f"params: {params}")
            args = []
            if params:
                args = ast.literal_eval(f"({params},)" if ',' not in params else f"({params})")
            return name, args
        except Exception as e:
            logger.error(" parse_call() - Error executing parse_call - expression: '%s' Error: '%s'", expression, str(e))
            return expression, []


    # Generate dynamic values ( function call or constant ) based on field mappings in JSON
    def generate_field_value(self, field_name):
        try:
            logger.info(f"field_name: {field_name}")
            rand_gen_rules_name, args = self.parse_call(field_name)
            if rand_gen_rules_name not in self.rand_gen_rules:
                logger.warning(" generate_field_value() - Field '%s' not found in JSON config.", field_name)
            field_value = self.rand_gen_rules[rand_gen_rules_name]
            if field_value:
                if isinstance(field_value, str):
                    if field_value.startswith("random_address."):  # address fields
                        _, key = field_value.split(".", 1)
                        return self.selected_address.get(key,f"generate_field_value() - error: {key} not found in address")
                    elif field_value.startswith("random_custom."):  # custom fields
                        _, key = field_value.split(".", 1)
                        return self.selected_custom.get(key,f"generate_field_value() - error: {key} not found in custom")
                    # Case 1: rand_gen_rules maps to function call syntax e.g functioName(val, int)
                    elif isinstance(field_value, str) and '(' in field_value:
                        func_name = field_value.split('(')[0]
                        if func_name in self.namespace and callable(self.namespace[func_name]):
                            return self.namespace[func_name](*args)
                        else:
                             logger.warning(" generate_field_value() - Function '%s' not found in JSON config.", func_name)
                    # Case 2: rand_gen_rules maps to function call syntax e.g functionName
                    elif isinstance(field_value, str) and field_value in self.namespace and callable(self.namespace[field_value]):
                        return self.namespace[field_value](*args)

                return field_value  # return constant as is
        except Exception as e:
            logger.error(" generate_field_value() - Error generating value for field_name: '%s'  Error: %s", field_name, str(e))