import logging

from colorama import init, Fore, Style

COLOR_PASS = Fore.GREEN
COLOR_FAIL = Fore.RED
RESET_ALL = Style.RESET_ALL


def setup_logging(log_level: str = "INFO"):
    """
    Sets up the logging configuration for the application.

    :param log_level:
    :return:
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # initialize Colorama for colored output.
    init(autoreset=True)
