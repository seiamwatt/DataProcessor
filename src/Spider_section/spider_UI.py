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
from rich.columns import Columns
from dotenv import load_dotenv
import os
import sys
import uuid
import boto3
import pytz
import pandas as pd

from Spider_section import spider

console = Console()


def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_dotenv(resource_path(".env"))


def clean_path(raw: str) -> str:
    """Normalize a path from typing or macOS drag-and-drop.

    Handles surrounding quotes, a trailing space, backslash-escaped spaces,
    and a leading ~.
    """
    if raw is None:
        return ""
    p = raw.strip().strip("'\"").strip()   # outer whitespace, then quotes, then any leftover space
    p = p.replace("\\ ", " ")              # un-escape drag-and-drop spaces
    return os.path.expanduser(p)


def banner_panel() -> Panel:
    art = """[bright_green]
 ██████ █████  ██████ █████  ██████ █████ 
 ███    ██  ██   ██   ██  ██ ██     ██  ██
    ███ █████    ██   ██  ██ ████   █████ 
 ██████ ██     ██████ █████  ██████ ██  ██
"""
    return Panel(art, subtitle="[green]🕷  spider", highlight=True)


def args_table() -> Table:
    table = Table(title="[blue]Arguments Needed", border_style="bright_cyan")
    table.add_column("[red]Args", no_wrap=True)
    table.add_column("[red]Description", no_wrap=True)
    table.add_column("[red]Required", no_wrap=True)

    table.add_row("Org CSV", "list of orgs: name,domain,ein", "True")
    table.add_row("Output folder", "where PDFs + manifest are saved", "True")
    table.add_row("Sources", "990 / wayback / live", "True")
    table.add_row("Years", "lookback window (default 20)", "False")
    table.add_row("Depth", "live-crawl link depth (default 3)", "False")
    table.add_row("Start Row", "start row index", "True")
    table.add_row("End Row", "end row index", "True")
    return table


def sources_index() -> Table:
    table = Table(title="[blue]Sources", border_style="bright_cyan", show_lines=True)
    table.add_column("ID", style="cyan", justify="center")
    table.add_column("Source", style="white")
    table.add_column("What it pulls", style="green")

    sources = [
        ("1", "990",     "IRS Form 990 financial filings (ProPublica, back to 2001)"),
        ("2", "wayback", "Historical report PDFs since removed (Internet Archive)"),
        ("3", "live",    "Current report PDFs (site crawl, BFS)"),
    ]
    for sid, source, desc in sources:
        table.add_row(sid, source, desc)

    return table


def display_tables():
    panel = Panel(
        Columns(
            [sources_index(), args_table()],
            equal=True,
            expand=True,
        ),
        title="[bold bright_cyan]Spider — Nonprofit Report Crawler",
        subtitle="[dim]nonprofit data toolkit",
        border_style="bright_cyan",
        padding=(1, 2),
    )

    console.print(panel)


def show():
    console.print(banner_panel())
    display_tables()

    status = True

    while status:
        console.print(Rule("[bold blue]New Session[/bold blue]"))
        input_valid = False

        while not input_valid:
            # --- org list CSV (name, domain, ein) ---
            raw = questionary.path("Org list CSV (or drag a file in):").ask()
            if raw is None:                       # Ctrl+C / Esc cancels
                return
            org_csv_path = clean_path(raw)

            if not os.path.isfile(org_csv_path):
                console.print(f"[bold red]Not a file: {org_csv_path!r} — try again")
                continue

            try:
                orgs_df = spider.load_csv(org_csv_path)
            except Exception as e:
                console.print(f"[bold red]Couldn't read CSV: {e!r}")
                continue
            console.print(f"[green]Loaded {len(orgs_df)} orgs from {org_csv_path}")

            # --- output folder for PDFs + manifest ---
            out_raw = questionary.path("Output folder for PDFs + manifest:", default="./reports").ask()
            out_dir = clean_path(out_raw) or "./reports"

            # --- which sources to run ---
            sources = questionary.checkbox(
                "Sources to run:",
                choices=[
                    questionary.Choice("990 — IRS Form 990 (ProPublica)", value="990", checked=True),
                    questionary.Choice("wayback — archived report PDFs", value="wayback", checked=True),
                    questionary.Choice("live — crawl current site", value="live", checked=True),
                ],
            ).ask()
            if not sources:
                console.print("[bold red]Pick at least one source, try again")
                continue

            # --- numeric inputs (validated on their own so a bad number
            #     doesn't bounce you all the way back to the path prompt) ---
            try:
                years = int(questionary.text("Years lookback", default="20").ask())
                depth = int(questionary.text("Live-crawl depth", default="3").ask())
                start_row = int(questionary.text("Start Row index", default="0").ask())
                end_row = int(questionary.text("End Row index", default=str(len(orgs_df))).ask())
            except (TypeError, ValueError) as e:
                console.print(f"[bold red]Numbers only for years/depth/rows: {e!r}")
                continue

            input_valid = True

        console.print("[bold cyan]Crawling Data")

        with console.status("[bold cyan]Running spider…", spinner="dots"):
            manifest = spider.populate_data(
                orgs_df=orgs_df,
                out_dir=out_dir,
                sources=sources,
                years=years,
                depth=depth,
                start_row=start_row,
                end_row=end_row,
            )

        if manifest is not None and len(manifest):
            console.print(
                f"[bold green]{len(manifest)} documents collected -> {os.path.join(out_dir, 'manifest.csv')}"
            )
        else:
            console.print("[bold yellow]No documents found")

        console.print("[bold cyan]End of processing")

        run_again = questionary.confirm("Repeat processing?").ask()
        if not run_again:
            status = False


if __name__ == "__main__":
    show()