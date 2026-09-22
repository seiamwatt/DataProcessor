"""Shared presentation theme for every DataProcessor screen.

One htop-style palette, imported by every UI module so the sections read as a
single application. This file is the only place to edit to retheme all of them.

BLUE is the main color: masthead, run-parameter tables, section rules, prompts.
The other bars appear where they mean something, the way htop's meters do:

    BLUE    the screen itself — config, parameters, section rules
    PURPLE  reference material — lookup tables, notes, pricing, overviews
    GREEN   health and completion — status checks, live progress, run complete
    RED     required fields and failures
    YELLOW  finished, but with nothing to show

Values follow the same code: purple for identifiers and counts, blue for
timing and defaults, green for the output a run produced, red for what failed.
"""

import questionary
from rich.rule import Rule
from rich.text import Text

# Base palette ---------------------------------------------------------------
CYAN = "bright_cyan"
GREEN = "green"
RED = "red"
BLUE = "dodger_blue1"
PURPLE = "medium_purple1"
YELLOW = "yellow"
WHITE = "grey85"
GREY = "grey50"

# Reverse-video bars — htop's column header and function-key strip ------------
BLUE_BAR = "black on dodger_blue1"      # main chrome
PURPLE_BAR = "black on medium_purple1"  # reference tables and notes
GREEN_BAR = "black on green"            # status, progress, completion
ERR_BAR = "bold white on red"
WARN_BAR = "black on yellow"

ACCENT_BAR = BLUE_BAR
OK_BAR = GREEN_BAR

# Semantic names used by the section modules ---------------------------------
ACCENT = BLUE                 # labels, rules, spinners
ACCENT_DIM = "dodger_blue3"   # panel borders that should sit back
INK = WHITE                 # body text on panels
EMPHASIS = "bold white"     # values and numbers
MUTED = GREY                # secondary text
OK = GREEN
WARN = YELLOW
ERR = f"bold {RED}"
DANGER = RED

PROMPT_STYLE = questionary.Style([
    ("qmark", "fg:#0087ff bold"),
    ("question", "bold"),
    ("answer", "fg:#ffffff bold"),
    ("pointer", "fg:#0087ff bold"),
    ("highlighted", "fg:#000000 bg:#0087ff"),
    ("selected", "fg:#000000 bg:#0087ff"),
    ("instruction", "fg:#767676"),
])


def chip(label: str, style: str = ACCENT_BAR) -> Text:
    """A reverse-video block — how htop labels a bar."""
    return Text(f" {label} ", style=style)


def section(title: str, style: str = ACCENT_BAR) -> Rule:
    """A section divider carrying a reverse-video label."""
    return Rule(chip(title, style), style=ACCENT)
