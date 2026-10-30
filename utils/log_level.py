"""Logger level for the framework modules, from LOG_LEVEL (DEBUG, INFO, WARNING or ERROR); INFO when unset."""
import logging
import os

LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


def from_env(name: str = "LOG_LEVEL", default: int = logging.INFO) -> int:
    return LEVELS.get(os.getenv(name, "").strip().upper(), default)
