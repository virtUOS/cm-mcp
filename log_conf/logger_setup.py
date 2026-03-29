# logger_setup.py
import logging
import sys

def get_logger(name: str = __name__) -> logging.Logger:
    """
    Returns a logger configured with:
    • INFO level (change to DEBUG for more detail)
    • Console output (stderr) with timestamp, level, name, and message
    • Simple one‑line format; extend as needed
    """
    logger = logging.getLogger(name)

    # Configure only once (avoid duplicate handlers on re‑import)
    if not logger.handlers:
        logger.setLevel(logging.INFO)

        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)

        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(formatter)

        logger.addHandler(console_handler)

    return logger