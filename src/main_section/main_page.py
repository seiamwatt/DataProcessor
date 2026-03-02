
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
import os
load_dotenv()
version = os.getenv("version")

console = Console()
version = os.getenv("version")
def welcome_panel():
    return Panel("",title="[red]Data Processor",subtitle=f"[blue]version: {version}",highlight=True)
    
def choice_menu():
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","ALL"]).ask()
    return choice

def LLM_token_table():
    table = Table(title = "LLM tokens rate")
    table.add_column("[blue]LLM",no_wrap=True)
    table.add_column("[blue]Input (per 1M)",no_wrap=True)
    table.add_column("[blue]Output (per 1M)",no_wrap=True)

    table.add_row("DeepSeek","0.028 USD","0.42 USD")
    table.add_row("GPT 4.1 ","3 USD","0.75 USD")
    table.add_row("Gemini","1.25 USD","12 USD")
    return table

def show():
    console.print(welcome_panel())
    console.print(LLM_token_table())
    # console.print(choice_menu())

if __name__ == "__main__":
    show()
