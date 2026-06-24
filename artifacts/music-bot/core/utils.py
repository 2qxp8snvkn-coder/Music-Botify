import os
from pystyle import Colors, Colorate
from colorama import Fore, Style, init

init(autoreset=True)

PRIMARY = Colors.red_to_yellow
SECONDARY = Colors.cyan_to_blue


ASCII_ART = r"""
  ______      __                     
 / ____/_  __/ /_  ____  _________ 
/ /   / / / / __ \/ __ \/ ___/ __ \
/ /___/ /_/ / /_/ / /_/ / /  / /_/ /
\____/\__, /_.___/\____/_/   \__, / 
     /____/                 /____/  
"""


def banner():
    os.system("cls" if os.name == "nt" else "clear")
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80

    colored = Colorate.Horizontal(PRIMARY, ASCII_ART.center(width))
    for line in colored.split("\n"):
        print(line.center(width))

    print(Colorate.Horizontal(SECONDARY, "-" * width))
    print(Colorate.Horizontal(PRIMARY, "Music Selfbot by CyborG DevelopmenT".center(width)))
    print(Colorate.Horizontal(SECONDARY, "-" * width + "\n"))


def menu_box(title, items):
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80

    box_width = max(len(title) + 6, max(len(i) for i in items) + 6, 40)
    pad = " " * ((width - box_width) // 2 - 2)

    top = "-" * (box_width - 2)
    print(Colorate.Horizontal(PRIMARY, f"\n{pad}+{top}+"))
    print(Colorate.Horizontal(PRIMARY, f"{pad}|{title.center(box_width - 2)}|"))
    print(Colorate.Horizontal(PRIMARY, f"{pad}+{top}+\n"))

    for item in items:
        print(Colorate.Horizontal(SECONDARY, f"{pad}  {item}"))
    print()


def format_time(ms):
    if not ms or ms <= 0:
        return "00:00"
    seconds = int(ms / 1000)
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def create_progress_bar(current, total, size=30):
    if not total or total <= 0:
        return f"{Fore.LIGHTBLACK_EX}{'-' * size}{Style.RESET_ALL}"
    progress = round((current / total) * size)
    filled = f"{Fore.CYAN}{'=' * progress}{Style.RESET_ALL}"
    empty = f"{Fore.LIGHTBLACK_EX}{'-' * (size - progress)}{Style.RESET_ALL}"
    return f"{filled}{Fore.WHITE}o{Style.RESET_ALL}{empty}"


def truncate_token(token):
    return token[-5:] if len(token) >= 5 else token


def inp(prompt_text):
    return input(Colorate.Horizontal(PRIMARY, f" > {prompt_text}: ")).strip()
