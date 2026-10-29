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


class RandGenError(Exception):
    """An autoGen rule could not produce a value (unknown rule, bad config, failing function)."""


# Custom RandGen class for dynamically generating random test data using customization rules for automating testing
class RandGen:
    def __init__(self, config_file, cwd):
        self.config_file = config_file
        self.cwd = cwd
        self.namespace = {}
        self.config = {}
        self.rand_gen_rules, self.dynamic_objects, self.custom_functions = {}, {}, {}
        self.selected_address, self.selected_custom = {}, {}
        self.broken = {}  # name -> why it failed to load (dynamic_objects / custom_functions)
        self.error = None  # set when the config file itself can't be loaded; raised on first use
        try:
            with open(config_file, "r", encoding="utf-8-sig") as file:
                self.config = json.load(file)
        except (OSError, ValueError) as e:
            logger.exception(" __init__() - Error loading configuration: config_file: '%s'", config_file)
            self.error = RandGenError(f"Could not load autoGen rules from {config_file}: {e}")
            return
        self.prepare_namespace()
        self.rand_gen_rules = self.config.get("rand_gen_rules", {})
        self.dynamic_objects = self.config.get("dynamic_objects", {})
        self.custom_functions = self.config.get("custom_functions", {})
        self.build_dynamic_objects()
        self.build_custom_functions()
        self.selected_address = self.select_random_address()
        self.selected_custom = self.select_random_custom()


    # Inject cwd into the function namespace
    def prepare_namespace(self):
        self.namespace["cwd"] = self.cwd



    # Pick one address randomly (use for all address fields)
    def select_random_address(self):
        address_list = self.config.get("address_list", [])
        selected_addresses = [addr for addr in address_list if isinstance(addr, dict) and addr.get("use", False)]
        return random.choice(selected_addresses) if selected_addresses else {}



    # Pick one custom randomly (use for all custom fields)
    def select_random_custom(self):
        custom_list = self.config.get("custom_list", [])
        selected_customs = [custom for custom in custom_list if isinstance(custom, dict) and custom.get("use", False)]
        return random.choice(selected_customs) if selected_customs else {}



    # Dynamically create and register objects. One broken entry no longer stops the others from loading.
    def build_dynamic_objects(self):
        for key, code in self.dynamic_objects.items():
            try:
                exec(code, self.namespace)
                self.namespace[key] = self.namespace.get(key)
            except Exception as e:
                self.broken[key] = f"{type(e).__name__}: {e}"
                logger.exception(" build_dynamic_objects() - Error executing dynamic_objects['%s']", key)



    # Compile and register all functions from code into namespace (each entry on its own)
    def build_custom_functions(self):
        for key, code in self.custom_functions.items():
            try:
                exec(code, self.namespace)
            except Exception as e:
                self.broken[key] = f"{type(e).__name__}: {e}"
                logger.exception(" build_custom_functions() - Error executing custom_functions['%s']", key)


    # Split functionName(arg, ..., key=value) into the name, positional and keyword arguments.
    # Arguments must be literals (numbers, strings, lists, ...): nothing in the expression is executed.
    def parse_call(self, expression):
        text = str(expression).strip()
        match = re.match(r"^(\w+)(\(.*\))?$", text, re.S)
        if not match:
            raise RandGenError(f"Invalid autoGen expression '{expression}' (expected name or name(arg, ...))")
        if not match.group(2):
            return match.group(1), [], {}
        try:
            call = ast.parse(text, mode="eval").body
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                raise ValueError("not a simple function call")
            args = [ast.literal_eval(a) for a in call.args]
            kwargs = {k.arg: ast.literal_eval(k.value) for k in call.keywords if k.arg}
        except (ValueError, SyntaxError, TypeError) as e:
            raise RandGenError(f"Invalid arguments in autoGen expression '{expression}': {e}") from e
        return call.func.id, args, kwargs


    # Generate dynamic values ( function call or constant ) based on field mappings in JSON.
    # Raises RandGenError with the reason instead of returning None, so a test can't silently run with no data.
    def generate_field_value(self, field_name):
        if self.error:
            raise self.error
        logger.info(f"field_name: {field_name}")
        rand_gen_rules_name, args, kwargs = self.parse_call(field_name)
        if rand_gen_rules_name not in self.rand_gen_rules:
            raise RandGenError(f"Unknown autoGen rule '{rand_gen_rules_name}' "
                               f"(rules are defined in {self.config_file})")
        field_value = self.rand_gen_rules[rand_gen_rules_name]
        if field_value and isinstance(field_value, str):
            if field_value.startswith("random_address."):  # address fields
                _, key = field_value.split(".", 1)
                return self.selected_address.get(key, f"generate_field_value() - error: {key} not found in address")
            if field_value.startswith("random_custom."):  # custom fields
                _, key = field_value.split(".", 1)
                return self.selected_custom.get(key, f"generate_field_value() - error: {key} not found in custom")
            # Case 1: functionName(...) must resolve to a loaded function
            # Case 2: a bare name is called only if it is a loaded function, otherwise it is a constant
            func_name = field_value.split('(')[0]
            func = self.namespace.get(func_name)
            if '(' in field_value and not callable(func):
                why = self.broken.get(func_name) or "it is not defined in custom_functions"
                raise RandGenError(f"autoGen rule '{rand_gen_rules_name}' calls {func_name}(), "
                                   f"which is not available: {why}")
            if callable(func):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    raise RandGenError(f"autoGen rule '{rand_gen_rules_name}' failed in {func_name}(): "
                                       f"{type(e).__name__}: {e}") from e
        return field_value if field_value else None  # constant as is (falsy constants keep returning None)
