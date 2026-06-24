import logging
from datetime import datetime
from colorama import Fore, Back, Style, init

init(autoreset=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="%H:%M:%S",
)


class Logger:
    def __init__(self):
        self.logger = logging.getLogger("CYBORG")

        self.COLORS = {
            "TIMESTAMP": Fore.LIGHTBLACK_EX,
            "BRAND_BRACKET": Fore.WHITE,
            "BRAND_NAME": Fore.GREEN + Style.BRIGHT,
            "SEPARATOR": Fore.BLUE + Style.BRIGHT,
            "INFO": Fore.BLUE + Style.BRIGHT,
            "SUCCESS": Fore.GREEN + Style.BRIGHT,
            "WARNING": Fore.YELLOW + Style.BRIGHT,
            "ERROR": Fore.RED + Style.BRIGHT,
            "DEBUG": Fore.MAGENTA + Style.BRIGHT,
            "MUSIC": Fore.CYAN + Style.BRIGHT,
            "MESSAGE": Fore.WHITE,
            "EXTRA": Fore.LIGHTBLACK_EX,
            "KEY": Fore.CYAN,
            "VALUE": Fore.WHITE + Style.BRIGHT,
            "RESET": Style.RESET_ALL,
        }

        self.ICONS = {
            "INFO": "i",
            "SUCCESS": "+",
            "WARNING": "!",
            "ERROR": "x",
            "DEBUG": "*",
            "MUSIC": "~",
        }

    def _timestamp(self):
        now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        return f"{self.COLORS['TIMESTAMP']}{now}{self.COLORS['RESET']}"

    def _brand(self):
        return (
            f"{self.COLORS['BRAND_BRACKET']}["
            f"{self.COLORS['BRAND_NAME']}CYBORG"
            f"{self.COLORS['BRAND_BRACKET']}]"
            f"{self.COLORS['RESET']}"
        )

    def _format(self, message, level="INFO", **kwargs):
        ts = self._timestamp()
        brand = self._brand()
        color = self.COLORS.get(level.upper(), self.COLORS["MESSAGE"])
        icon = self.ICONS.get(level.upper(), ".")
        icon_part = f"{color}{icon}{self.COLORS['RESET']}"
        msg_part = f"{self.COLORS['MESSAGE']}{message}{self.COLORS['RESET']}"

        extra = ""
        if kwargs:
            items = []
            for k, v in kwargs.items():
                items.append(
                    f"{self.COLORS['KEY']}{k}{self.COLORS['RESET']}="
                    f"{self.COLORS['VALUE']}{v}{self.COLORS['RESET']}"
                )
            extra = f" {self.COLORS['EXTRA']}({', '.join(items)}){self.COLORS['RESET']}"

        return f"{ts} {brand} {icon_part} {msg_part}{extra}"

    def info(self, message, **kwargs):
        self.logger.info(self._format(message, "INFO", **kwargs))

    def success(self, message, **kwargs):
        self.logger.info(self._format(message, "SUCCESS", **kwargs))

    def warning(self, message, **kwargs):
        self.logger.warning(self._format(message, "WARNING", **kwargs))

    def error(self, message, **kwargs):
        self.logger.error(self._format(message, "ERROR", **kwargs))

    def debug(self, message, **kwargs):
        self.logger.debug(self._format(message, "DEBUG", **kwargs))

    def music(self, message, **kwargs):
        self.logger.info(self._format(message, "MUSIC", **kwargs))

    def separator(self):
        line = "-" * 80
        print(f"{self.COLORS['SEPARATOR']}{line}{self.COLORS['RESET']}")


logger = Logger()
