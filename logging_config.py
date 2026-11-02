import logging
import re

from colorama import init, Fore, Style

COLOR_PASS = Fore.GREEN
COLOR_FAIL = Fore.RED
COLOR_WARN = Fore.YELLOW
RESET_ALL = Style.RESET_ALL


# scheme://user:password@ or scheme://user@ in any text. Greedy up to the last "@" before the path, so an
# unencoded "@" in a password does not leave the rest of the password visible.
_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/\s'\"]*@")


def redact_credentials(text: str) -> str:
    return _USERINFO.sub(r"\1***@", text)


class RedactCredentials(logging.Filter):
    """Hide URL credentials in every record, also those of libraries such as selenium."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_credentials(record.getMessage())
        record.args = None
        # An exception message or a stack can hold the URL as well. The formatter uses exc_text when it is set.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_credentials(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_credentials(record.stack_info)
        return True


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

    for handler in logging.getLogger().handlers:
        if not any(isinstance(item, RedactCredentials) for item in handler.filters):
            handler.addFilter(RedactCredentials())

    # initialize Colorama for colored output.
    init(autoreset=True)
