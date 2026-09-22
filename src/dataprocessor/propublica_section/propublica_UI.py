import time
import random
import platform
from datetime import datetime, timedelta
from collections import deque
import questionary
import argparse
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
from rich.console import Group
from dotenv import load_dotenv
from dataprocessor.config import load_env
import os

import sys
import uuid
from rich.columns import Columns
import boto3
import pytz
import pandas as pd

from dataprocessor.propublica_section import propublica_logic

console = Console()

# THEME ---------------------------------------------------------------------------
# One shared htop palette for every screen. Edit dataprocessor/theme.py to
# retheme the whole application; nothing here defines its own colors.
from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, PURPLE_BAR, ERR_BAR, EMPHASIS, MUTED, OK, WARN, ERR,
    CYAN, GREEN, RED, BLUE, PURPLE, PROMPT_STYLE, chip, section,
)
# ---------------------------------------------------------------------------------


def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_env()
def banner_panel() -> Panel:
    """Application masthead."""
    title = Text(" PROPUBLICA NONPROFIT EXPLORER ", style=f"bold {ACCENT_BAR}")
    subtitle = Text("Nonprofit data collection pipeline \u2014 output to local CSV", style=MUTED)
    meta = Text(datetime.now().strftime("Session started %Y-%m-%d %H:%M"), style=MUTED)
    body = Group(
        Align.center(title),
        Align.center(subtitle),
        Align.center(meta),
    )
    return Panel(
        Padding(body, (1, 4)),
        box=box.SQUARE,
        border_style=ACCENT,
    )


def args_table() -> Table:
    """Single reference table: parameter, description, required flag, default."""
    table = Table(
        title="Run parameters",
        title_style=f"bold {ACCENT}",
        title_justify="left",
        box=None,
        header_style=f"bold {ACCENT_BAR}",
        pad_edge=False,
        expand=True,
    )
    table.add_column("PARAMETER", no_wrap=True, style=EMPHASIS)
    table.add_column("DESCRIPTION", style=MUTED)
    table.add_column("REQUIRED", justify="center", no_wrap=True)
    table.add_column("DEFAULT", no_wrap=True, style=BLUE)

    table.add_row("Input CSV", "Existing CSV to append results, or a folder for a new file", f"[bold {RED}]Yes[/]", "\u2014")
    table.add_row("NTEE category", "NTEE code category (1\u201310, see index)", f"[bold {RED}]Yes[/]", "\u2014")
    table.add_row("Number of pages", "Pages to process per request", f"[bold {RED}]Yes[/]", "500")
    table.add_row("Start state index", "First state index to process", f"[{GREEN}]No[/]", "0")
    table.add_row("End state index", "Last state index to process", f"[{GREEN}]No[/]", "57")
    return table


def propublica_index() -> Table:
    """Reference index of NTEE categories."""
    table = Table(
        title="NTEE category index",
        title_style=f"bold {PURPLE}",
        title_justify="left",
        box=None,
        header_style=f"bold {PURPLE_BAR}",
        pad_edge=False,
        expand=True,
    )
    table.add_column("ID", justify="center", no_wrap=True, style=EMPHASIS)
    table.add_column("Category")
    table.add_column("Letters", justify="center", style=MUTED)

    categories = [
        ("1",  "Arts, Culture & Humanities",     "A"),
        ("2",  "Education",                      "B"),
        ("3",  "Environment & Animals",          "C, D"),
        ("4",  "Health",                         "E, F, G, H"),
        ("5",  "Human Services",                 "I, J, K, L, M, N, O, P"),
        ("6",  "International, Foreign Affairs", "Q"),
        ("7",  "Public, Societal Benefit",       "R, S, T, U, V, W"),
        ("8",  "Religion Related",               "X"),
        ("9",  "Mutual/Membership Benefit",      "Y"),
        ("10", "Unknown, Unclassified",          "Z"),
    ]

    for cat_id, category, letters in categories:
        table.add_row(cat_id, category, letters)

    return table


def display_tables():
    console.print(banner_panel())
    console.print(Columns(
        [propublica_index(), args_table()],
        equal=True,
        expand=True,
    ))
    console.print()


def run_summary_panel(rows_appended: int, output_path: str) -> Panel:
    """Final report card for the completed run."""
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Rows appended", f"{rows_appended:,}")
    table.add_row("Output file", Text(output_path, style=GREEN))
    return Panel(table, title=Text(" Run complete ", style=f"bold {OK_BAR}"),
                 title_align="left", border_style=OK, box=box.SQUARE)


def show():

    # console.print(args_table())

    console.clear()
    display_tables()


    status = True

    while status:
        console.print(section("Configuration"))
        input_valid = False

        while not input_valid:
            try:
                input_csv_path = questionary.path("Input CSV file (or drag a folder path in):", style=PROMPT_STYLE).ask()
                input_csv_path = input_csv_path.strip("'\"")

                if os.path.isfile(input_csv_path):
                    df = propublica_logic.load_csv(input_csv_path)
                    console.print(f"[{MUTED}]Loaded existing CSV: {input_csv_path}[/]")
                elif os.path.isdir(input_csv_path):
                    input_csv_path = os.path.join(input_csv_path, "output.csv")
                    console.print(f"[{MUTED}]A new file will be created: {input_csv_path}[/]")
                else:
                    console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Path is not a valid file or folder.[/] [{MUTED}]Provide an existing CSV file or a folder, then try again.[/]")
                    continue
                ntee_code_catagory = questionary.text("NTEE code category (1\u201310)", style=PROMPT_STYLE).ask()
                # ntee_code = questionary.text("NTEE code (e.g. A01)").ask()

                num_page = questionary.text("Number of pages to process", default="500", style=PROMPT_STYLE).ask()
                num_page = int(num_page)

                start_state_index = questionary.text("Start state index", default="0", style=PROMPT_STYLE).ask()
                start_state_index = int(start_state_index)

                end_state_index = questionary.text("End state index", default="57", style=PROMPT_STYLE).ask()
                end_state_index = int(end_state_index)

                input_valid = True
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input.[/] [{MUTED}]Ensure numeric fields contain whole numbers, then try again.[/]")

        console.print(section("Processing"))

        with console.status(f"[{ACCENT}]Collecting data from ProPublica...", spinner="dots"):
            results = propublica_logic.populate_data(
                num_pages=num_page,
                ntee_catagory_id=ntee_code_catagory,
                start_state_index=start_state_index,
                end_state_index=end_state_index
            )

        if results:
            df = pd.DataFrame(results)
            file_exists = os.path.isfile(input_csv_path)
            df.to_csv(input_csv_path, mode="a", header=not file_exists, index=False)
            console.print(run_summary_panel(len(results), input_csv_path))
        else:
            console.print(f"[{WARN}]No results were found for this configuration.[/] [{MUTED}]Nothing was written to the file.[/]")

        run_again = questionary.confirm("Repeat processing?", style=PROMPT_STYLE).ask()
        if not run_again:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False




if __name__ == "__main__":
    show()