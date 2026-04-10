import logging


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger by name."""

    return logging.getLogger(name)
