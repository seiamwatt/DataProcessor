
import time
import random
import platform
from datetime import datetime, timedelta
from collections import deque
import questionary

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich import box
from rich.rule import Rule
from rich.padding import Padding
import questionary
from dotenv import load_dotenv
from dataprocessor.config import load_env
import os
from dataprocessor.main_section import main_util
import sys
import importlib.metadata

# THEME ---------------------------------------------------------------------------
# One shared htop palette for every screen. Edit dataprocessor/theme.py to
# retheme the whole application; nothing here defines its own colors.
from dataprocessor.theme import (
    ACCENT, ACCENT_DIM, ACCENT_BAR, OK_BAR, PURPLE_BAR, ERR_BAR, EMPHASIS, MUTED, OK, ERR,
    CYAN, GREEN, RED, BLUE, PURPLE, chip, section,
)
# ---------------------------------------------------------------------------------
# SETUP ---------------------------------------------------------------------------
os.environ["TERM"] = "xterm-256color"
def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()
try:
    version = importlib.metadata.version("dataprocessor")
except importlib.metadata.PackageNotFoundError:
    version = os.getenv("version")

console = Console()
# ---------------------------------------------------------------------------------
# UI ---------------------------------------------------------------------------
def welcome_panel() -> Panel:
    art = f"""[{RED}]
 ████   █████  ██████  █████ 
 ██  ██ ██  ██   ██   ██  ██ 
 ██  ██ █████    ██   █████  
 ████   ██  ██   ██   ██  ██ 
[{BLUE}]
 ████  ████   ████   ████  ████  █████  █████  ████  ████ 
 ██  █ ██  █ ██  ██ ██    ██    ██     ██     ██  ██ ██  █
 ████  ████  ██  ██ ██    ████   ███    ███  ██  ██ ████ 
 ██    ██  █  ████   ████  ████ █████  █████  ████  ██  █
"""
    return Panel(art, subtitle=f"[{MUTED}]version: {version}",
                 border_style=ACCENT_DIM, box=box.SQUARE)
    
def choice_menu():
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","ALL"]).ask()
    return choice

def info_panel() -> Panel:
    return Panel(f"[{EMPHASIS}]Version: {version}[/]", title=chip("Info", PURPLE_BAR),
                 title_align="left", border_style=ACCENT_DIM, box=box.SQUARE)

def LLM_token_table():
    table = Table(title="LLM tokens rate", title_style=f"bold {PURPLE}",
                  title_justify="left", box=None, pad_edge=False, expand=True,
                  header_style=f"bold {PURPLE_BAR}")
    table.add_column("LLM", no_wrap=True, style=EMPHASIS)
    table.add_column("INPUT (PER 1M)", no_wrap=True, style=BLUE)
    table.add_column("OUTPUT (PER 1M)", no_wrap=True, style=PURPLE)

    table.add_row("DeepSeek","0.028 USD","0.42 USD")
    table.add_row("GPT 4.1 ","3 USD","0.75 USD")
    table.add_row("Gemini","1.25 USD","12 USD")
    return table
# ---------------------------------------------------------------------------------

def show():
    console.print(welcome_panel())
    console.print(LLM_token_table())
    

if __name__ == "__main__":
    show()
