
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
from main_section import main_util
import sys
version = os.getenv("version")

def status_table() -> Table:

    deep_status = main_util.DeepSeek_connect_test()
    gpt_status = main_util.GTP_connect_test()
    gemini_status = main_util.Gemini_connect_test()

    online = "[bold green]Online"
    offline = "[bold red]Offline"

    if deep_status:
        deep_show = online
    else:
        deep_show = offline

    if gpt_status:
        gpt_show = online
    else:
        gpt_show = offline

    if gemini_status:
        gemini_show = online
    else:
        gemini_show = offline

    table = Table(title="[bold red]LLM status")

    table.add_column("LLM")
    table.add_column("Status")

    table.add_row("DeepSeek",deep_show)
    table.add_row("GPT",gpt_show)
    table.add_row("Gemini",gemini_show)

    return table


def show():
    console = Console()
    console.print(Panel(f"Version:{version}",title="[bold blue]Settings",highlight=True))
    console.print(status_table())

if __name__ == "__main__":
    show()