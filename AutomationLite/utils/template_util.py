import re
from utils.rand_gen import RandGen


def substitute_placeholders(config, mapping=None):
    """
    This is for Application Types in portal
    Recursively substitute placeholders in a JSON-like structure.
    Placeholders are denoted by {{key}}. For each key:
      - If mapping is provided and contains the key, its value is used.
      - Otherwise, if RandGen has an attribute with that key, call it to get a value.
      - Otherwise, the placeholder remains unchanged.
    """
    if mapping is None:
        mapping = {}
    if isinstance(config, dict):
        return {k: substitute_placeholders(v, mapping) for k, v in config.items()}
    elif isinstance(config, list):
        return [substitute_placeholders(item, mapping) for item in config]
    elif isinstance(config, str):
        # Find all placeholders in the string.
        placeholders = re.findall(r"{{\s*(\w+)\s*}}", config)
        for key in placeholders:
            if key in mapping:
                replacement = str(mapping[key])
            elif hasattr(RandGen, key):
                # Call the corresponding RandGen method.
                replacement = str(getattr(RandGen, key)())
            else:
                replacement = f"{{{{{key}}}}}"  # leave unchanged if not found.
            config = re.sub(r"{{\s*" + re.escape(key) + r"\s*}}", replacement, config)
        return config
    else:
        return config
