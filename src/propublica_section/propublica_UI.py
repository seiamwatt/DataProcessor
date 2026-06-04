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
import os

import sys
import uuid
from rich.columns import Columns
import boto3
import pytz
import pandas as pd

from propublica_section import propublica_logic

console = Console()


def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_dotenv(resource_path(".env"))


def args_table() -> Table:
    table = Table(title="[blue]Arguments Needed", border_style="bright_cyan")
    table.add_column("[red]Args", no_wrap=True)
    table.add_column("[red]Description", no_wrap=True)
    table.add_column("[red]Required", no_wrap=True)

    table.add_row("Input CSV", "existing CSV to append results", "False")
    table.add_row("NTEE CODE category", "NTEE code 1-10", "True")
    table.add_row("Num pages", "Number of pages to process", "True")
    return table

def propublica_index() -> Table:
    table = Table(title="[blue]Processing Index", border_style="bright_cyan",show_lines=True)
    table.add_column("Category ID", style="cyan", justify="center")
    table.add_column("Category", style="white")
    table.add_column("Letters", style="green", justify="center")

    categories = [
        ("1",  "Arts, Culture & Humanities",    "A"),
        ("2",  "Education",                     "B"),
        ("3",  "Environment & Animals",         "C, D"),
        ("4",  "Health",                        "E, F, G, H"),
        ("5",  "Human Services",                "I, J, K, L, M, N, O, P"),
        ("6",  "International, Foreign Affairs", "Q"),
        ("7",  "Public, Societal Benefit",      "R, S, T, U, V, W"),
        ("8",  "Religion Related",              "X"),
        ("9",  "Mutual/Membership Benefit",     "Y"),
        ("10", "Unknown, Unclassified",         "Z"),
    ]

    for cat_id, category, letters in categories:
        table.add_row(cat_id, category, letters)

    return table

def display_tables():
    panel = Panel(
        Columns(
            [propublica_index(), args_table()],
            equal=True,
            expand=True,
        ),
        title="[bold bright_cyan]ProPublica Nonprofit Explorer",
        subtitle="[dim]nonprofit data toolkit",
        border_style="bright_cyan",
        padding=(1, 2),
    )

    console.print(panel)


def show():

    # console.print(args_table())

    display_tables()


    status = True

    while status:
        console.print(Rule("[bold blue]New Session[/bold blue]"))
        input_valid = False

        while not input_valid:
            try:
                input_csv_path = questionary.path("Input CSV file (or drag a folder path in):").ask()
                input_csv_path = input_csv_path.strip("'\"")

                if os.path.isfile(input_csv_path):
                    df = propublica_logic.load_csv(input_csv_path)
                    console.print(f"[green]Loaded existing CSV: {input_csv_path}")
                elif os.path.isdir(input_csv_path):
                    input_csv_path = os.path.join(input_csv_path, "output.csv")
                    console.print(f"[yellow]Will create new file: {input_csv_path}")
                else:
                    console.print("[bold red]Path is not a valid file or folder, try again")
                    continue
                ntee_code_catagory = questionary.text("NTEE code category 1-10").ask()
                # ntee_code = questionary.text("NTEE code (e.g. A01)").ask()

                num_page = questionary.text("Number of pages to process",default="500").ask()
                num_page = int(num_page)

                start_state_index = questionary.text("Start State Index",default="0").ask()
                start_state_index = int(start_state_index)

                end_state_index = questionary.text("End State Index",default="57").ask()
                end_state_index = int(end_state_index)

                input_valid = True
            except Exception as e:
                console.print("[bold red]Invalid input")

        console.print("[bold cyan]Processing Data")

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
            console.print(f"[bold green]{len(results)} results appended to {input_csv_path}")
        else:
            console.print("[bold yellow]No results found")

        console.print("[bold cyan]End of processing")

        run_again = questionary.confirm("Repeat processing?").ask()
        if not run_again:
            status = False




if __name__ == "__main__":
    show()