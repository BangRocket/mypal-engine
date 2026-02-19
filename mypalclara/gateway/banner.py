"""Startup banner for Clara Gateway."""

from __future__ import annotations

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"

# Color palette: green → warm gold (matches Clara's aesthetic)
GRADIENT = [
    "\033[38;5;115m",
    "\033[38;5;150m",
    "\033[38;5;186m",
    "\033[38;5;222m",
    "\033[38;5;216m",
    "\033[38;5;210m",
]

PORTRAIT_COLOR = "\033[38;5;108m"

# Pre-generated braille portrait (Option D: compact 22 cols)
PORTRAIT_LINES = [
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡀⠀⢤⣿⣿⣿⣿⣿⣿",
    "⠀⠀⠀⠀⠀⣠⣶⣿⡁⠀⠀⠀⣹⠇⠀⠀⠙⣿⣿⣿⣿⣿",
    "⠀⠀⠀⠀⣼⣿⣿⣿⠗⠋⠁⠀⠙⠢⡀⠀⠀⠈⢿⣿⣿⣿",
    "⠀⠀⠀⣾⣿⣿⣿⠇⠀⠀⠀⠀⠀⠀⠘⣄⠀⠀⠈⢿⣿⣿",
    "⠀⢀⣾⣿⣿⣿⣯⣄⡀⠀⢀⣠⡄⠀⠀⢸⣧⡀⣄⠈⢿⣿",
    "⢀⣾⣿⣿⣿⣿⣿⣿⣷⠀⠐⢿⣶⠶⠀⠀⣿⣷⡼⣆⢸⣿",
    "⢸⣿⣿⣿⣿⡟⠛⠉⠃⠀⠀⠀⠀⠀⠀⠀⢸⣿⣿⣿⠈⣿",
    "⢸⣿⣿⣿⣿⣷⣀⡰⣶⡤⠄⠀⠀⠀⠀⢀⣼⡿⠿⠃⠀⣿",
    "⢠⣿⣿⣿⣿⣿⣿⣿⣷⡦⠤⠤⠀⠀⠀⣼⣿⡇⢰⡆⠀⠘",
    "⣿⣿⣿⣿⣿⣿⣿⣿⠛⠋⠀⠀⢀⣠⣾⣿⣿⣧⠘⢇⢀⠀",
    "⣿⣿⣿⣿⣿⣿⣿⣿⣷⣶⣶⠖⠉⠚⣿⣿⣿⣿⣧⣸⣿⣿",
    "⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠃⠀⠀⢀⣿⣿⣿⣿⣿⠟⠉⣾",
    "⣾⣿⣿⣿⣿⣿⣿⣿⣿⡟⠀⠀⠀⠀⣻⣿⣯⣿⣡⣿⠉⠚",
]

# Block-letter MYPALCLARA banner
BANNER_TEXT = [
    "  ███╗   ███╗██╗   ██╗██████╗  █████╗ ██╗      ██████╗ ██╗      █████╗  ██████╗  █████╗ ",
    "  ████╗ ████║╚██╗ ██╔╝██╔══██╗██╔══██╗██║     ██╔════╝ ██║     ██╔══██╗ ██╔══██╗██╔══██╗",
    "  ██╔████╔██║ ╚████╔╝ ██████╔╝███████║██║     ██║      ██║     ███████║ ██████╔╝███████║ ",
    "  ██║╚██╔╝██║  ╚██╔╝  ██╔═══╝ ██╔══██║██║     ██║      ██║     ██╔══██║ ██╔══██╗██╔══██║ ",
    "  ██║ ╚═╝ ██║   ██║   ██║     ██║  ██║███████╗╚██████╗ ███████╗██║  ██║ ██║  ██║██║  ██║ ",
    "  ╚═╝     ╚═╝   ╚═╝   ╚═╝     ╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
]


def compose_banner(version: str) -> str:
    """Compose the startup banner with portrait and text side by side."""
    portrait = PORTRAIT_LINES
    banner = BANNER_TEXT
    gap = 3

    portrait_height = len(portrait)
    banner_height = len(banner)
    banner_start = max(0, (portrait_height - banner_height) // 2)

    portrait_width = max(len(line) for line in portrait)
    separator = " " * gap

    lines: list[str] = []

    total_height = max(portrait_height, banner_start + banner_height + 3)
    for i in range(total_height):
        # Portrait part
        if i < len(portrait):
            p_line = portrait[i]
            p_colored = f"{PORTRAIT_COLOR}{p_line}{RESET}"
        else:
            p_line = ""
            p_colored = " " * portrait_width

        padding = " " * (portrait_width - len(p_line))

        # Banner/text part
        banner_idx = i - banner_start
        if 0 <= banner_idx < len(banner):
            color_idx = int(
                banner_idx / max(len(banner) - 1, 1) * (len(GRADIENT) - 1)
            )
            b_colored = f"{GRADIENT[color_idx]}{banner[banner_idx]}{RESET}"
        elif banner_idx == len(banner) + 1:
            b_colored = f"  {DIM}v{version} • Your AI companion{RESET}"
        else:
            b_colored = ""

        if p_colored.strip() or b_colored.strip():
            lines.append(f"{p_colored}{padding}{separator}{b_colored}")

    # Trim trailing empty lines
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append("")

    return "\n".join(lines)


def print_banner(version: str) -> None:
    """Print the startup banner to stdout."""
    print(compose_banner(version))


# Status output colors
class Colors:
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    WHITE = "\033[97m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @staticmethod
    def ok(text: str) -> str:
        return f"{Colors.GREEN}{text}{Colors.RESET}"

    @staticmethod
    def warn(text: str) -> str:
        return f"{Colors.YELLOW}{text}{Colors.RESET}"

    @staticmethod
    def err(text: str) -> str:
        return f"{Colors.RED}{text}{Colors.RESET}"

    @staticmethod
    def info(text: str) -> str:
        return f"{Colors.CYAN}{text}{Colors.RESET}"

    @staticmethod
    def dim(text: str) -> str:
        return f"{Colors.DIM}{text}{Colors.RESET}"

    @staticmethod
    def bold(text: str) -> str:
        return f"{Colors.BOLD}{text}{Colors.RESET}"

    @staticmethod
    def header(text: str) -> str:
        return f"{Colors.BOLD}{Colors.WHITE}{text}{Colors.RESET}"
