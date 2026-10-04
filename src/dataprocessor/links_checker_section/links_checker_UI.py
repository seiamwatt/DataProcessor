import time
import random
import platform
from datetime import datetime, timedelta
from collections import deque
import questionary
import argparse
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich import box
from rich.rule import Rule
from rich.padding import Padding
from dotenv import load_dotenv, find_dotenv
from dataprocessor.config import load_env
import os
import pandas as pd
import sys
import uuid
from rich.columns import Columns
import boto3
from botocore.exceptions import ClientError
import pytz
import threading
from concurrent.futures import ThreadPoolExecutor

from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, ERR_BAR, EMPHASIS, MUTED, OK, WARN, ERR,
    CYAN, GREEN, RED, BLUE, PURPLE, PROMPT_STYLE, chip, section,
)
console = Console()

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()

def links_section_panel() -> Panel:
    title = Text("Link check",style = f"bold {ACCENT_BAR}")
    subtitle = Text("Wayback/Live links checker pipeline", style= MUTED)
    meta = Text(datetime.now().strftime("Session started %Y-%m-%d %H:%M"), style=MUTED)

    body = Group(
        Align.center(title),
        Align.center(subtitle),
        Align.center(meta),
    )

    return Panel(
        Padding(body,(1,4)),
        box = box.SQUARE,
        border_style=ACCENT,
    )

def args_table() -> Table:
    table = Table(
        title = "Run parameters",
        title_style=f"bold {ACCENT}",
        title_justify="left",
        box=None,
        header_style=f"bold {ACCENT_BAR}",
        pad_edge=False,
        expand=True,
    )

    table.add_column("PARAMETER",no_wrap=True,style=EMPHASIS)
    table.add_column("DESCRIPTION",style=MUTED)
    table.add_column("REQUIRED",justify="center",no_wrap=True)
    table.add_column("DEFAULT",no_wrap=True,style=BLUE)

    required = Text("Yes", style=f"bold {RED}")
    optional = Text("No", style=GREEN)

    table.add_row("Input path", "Path to the source CSV file", required, "\u2014")
    table.add_row("Output path", "Destination CSV file name", required, "\u2014")
    table.add_row("Start row", "First row to process", optional, "0")
    table.add_row("End row", "Row at which processing stops", optional, "End of file")
    table.add_row("Column name", "Column containing the PDF URL", optional, "pdf_url")


def run_summary_panel(id, start_row, end_row, time_elapsed, output_path) -> Panel:
    """Final report card for the completed run."""
    mins, secs = divmod(int(time_elapsed), 60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", Text(id, style=PURPLE))
    table.add_row("Rows processed", Text(f"{start_row:,} \u2013 {end_row:,}", style=PURPLE))
    table.add_row("Elapsed time", Text(f"{mins}m {secs}s", style=BLUE))
    table.add_row("Output file", Text(output_path, style=GREEN))
    return Panel(table, title=Text(" Run complete ", style=f"bold {OK_BAR}"),
                 title_align="left", border_style=OK, box=box.SQUARE)

def show():
    status = True
    console.clear()
    console.print(links_section_panel())
    console.print(args_table())
    console.print()

    while(status):
        input_status = True
        console.print(Rule(Text(" Configuration ", style=ACCENT_BAR), style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = input_path.strip("'\"")
                df = pd.read_csv(input_path)
                default_end_row = len(df)
                output_path = questionary.path("Output CSV file:", style=PROMPT_STYLE).ask()
                start_row = questionary.text("Start row", default=str(0), style=PROMPT_STYLE).ask()
                end_row = questionary.text("End row", default=str(default_end_row), style=PROMPT_STYLE).ask()
                col_name = questionary.text("Column name", default="url", style=PROMPT_STYLE).ask()

                output_path = os.path.join(os.path.dirname(input_path), output_path)
                start_row = int(start_row)
                end_row = int(end_row)
                input_status = False
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input.[/] [{MUTED}]Check the file path and ensure numeric fields contain whole numbers, then try again.[/]")
                




        
