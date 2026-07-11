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

from dataprocessor.analysis_v2_section import analysis_v2

console = Console()
os.environ["TERM"] = "xterm-256color"

# THEME ---------------------------------------------------------------------------
# Presentation-layer constants only. Edit these to retheme the whole application.
ACCENT = "medium_purple1"
EMPHASIS = "bold white"
MUTED = "grey62"
OK = "green"
ERR = "bold red"

PROMPT_STYLE = questionary.Style([
    ("qmark", "fg:#af87ff bold"),
    ("question", "bold"),
    ("answer", "fg:#af87ff"),
    ("pointer", "fg:#af87ff bold"),
])
# ---------------------------------------------------------------------------------


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()
def analysis_section_panel() -> Panel:
    """Application masthead."""
    title = Text("ANALYSIS SECTION V2", style=f"bold {ACCENT}")
    subtitle = Text("Batch PDF report analysis pipeline", style=MUTED)
    meta = Text(datetime.now().strftime("Session started %Y-%m-%d %H:%M"), style=MUTED)
    body = Group(
        Align.center(title),
        Align.center(subtitle),
        Align.center(meta),
    )
    return Panel(
        Padding(body, (1, 4)),
        box=box.HEAVY,
        border_style=ACCENT,
    )


def args_table() -> Table:
    """Single reference table: parameter, description, required flag, default."""
    table = Table(
        title="Run parameters",
        title_style=f"bold {ACCENT}",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        border_style=MUTED,
        header_style=f"bold {ACCENT}",
        pad_edge=False,
    )
    table.add_column("Parameter", no_wrap=True, style=EMPHASIS)
    table.add_column("Description")
    table.add_column("Required", justify="center", no_wrap=True)
    table.add_column("Default", no_wrap=True, style=MUTED)

    table.add_row("Input path", "Path to the source CSV file", "Yes", "\u2014")
    table.add_row("Output path", "Destination CSV file name", "Yes", "\u2014")
    table.add_row("Batch size", "Rows processed per batch", "Yes", "\u2014")
    table.add_row("Start row", "First row to process", "No", "0")
    table.add_row("End row", "Row at which processing stops", "No", "End of file")
    table.add_row("Column name", "Column containing the PDF URL", "No", "pdf_url")
    return table


def run_summary_panel(id, start_row, end_row, time_elapsed, output_path) -> Panel:
    """Final report card for the completed run."""
    mins, secs = divmod(int(time_elapsed), 60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", id)
    table.add_row("Rows processed", f"{start_row:,} \u2013 {end_row:,}")
    table.add_row("Elapsed time", f"{mins}m {secs}s")
    table.add_row("Output file", output_path)
    return Panel(table, title=Text("Run complete", style=f"bold {OK}"),
                 title_align="left", border_style=OK, box=box.ROUNDED)


def show():
    status = True

    load_env()
    console.clear()
    console.print(analysis_section_panel())
    console.print(args_table())
    console.print()

    while status:
        input_status = True

        console.print(Rule("Configuration", style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = input_path.strip("'\"")
                df = analysis_v2.load_csv(input_path)
                default_end_row = len(df)

                output_path = questionary.path("Output CSV file:", style=PROMPT_STYLE).ask()
                batch_size = questionary.text("Batch size", style=PROMPT_STYLE).ask()
                start_row = questionary.text("Start row", default=str(0), style=PROMPT_STYLE).ask()
                end_row = questionary.text("End row", default=str(default_end_row), style=PROMPT_STYLE).ask()
                col_name = questionary.text("Column name", default="pdf_url", style=PROMPT_STYLE).ask()
                output_path = os.path.join(os.path.dirname(input_path), output_path)

                start_row = int(start_row)
                end_row = int(end_row)
                batch_size = int(batch_size)
                input_status = False
            except Exception as e:
                console.print(f"[{ERR}]Invalid input.[/] [{MUTED}]Check the file path and ensure numeric fields contain whole numbers, then try again.[/]")



        deep_key = os.getenv("DeepSeek_key")
        if deep_key is None:
            console.print(f"[{ERR}]DeepSeek API key not found.[/] [{MUTED}]Add DeepSeek_key to the .env file and restart the application.[/]")
            return

        gpt_key = os.getenv("GPT_key")
        if gpt_key is None:
            console.print(f"[{ERR}]GPT API key not found.[/] [{MUTED}]Add GPT_key to the .env file and restart the application.[/]")
            return

        gemini_key = os.getenv("Gemini_key")
        if gemini_key is None:
            console.print(f"[{ERR}]Gemini API key not found.[/] [{MUTED}]Add Gemini_key to the .env file and restart the application.[/]")
            return

        df_subset = df.iloc[start_row:end_row]
        total_batches = (len(df_subset) + batch_size - 1) // batch_size

        console.print(Rule("Processing", style=ACCENT))
        row_to_process = end_row - start_row
        console.print(f"[{MUTED}]Rows to process: {row_to_process:,}[/]")

        curr_row = start_row


        with Progress(
            SpinnerColumn(style=ACCENT),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None, complete_style=ACCENT, finished_style=OK),
            MofNCompleteColumn(),
            TextColumn(f"[{MUTED}]batches"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            time_start = time.time()
            task1 = progress.add_task("Analysing data", total=total_batches)

            for i in range(0, len(df_subset), batch_size):
                batch_num = i // batch_size + 1
                progress.update(task1, description=f"Batch {batch_num} of {total_batches}  |  Row {curr_row}")

                curr_row += 1

                batch = df_subset.iloc[i:i + batch_size]
                batch_result = analysis_v2.batch_processing(batch, col_name, deep_key, gemini_key, gpt_key)

                if os.path.exists(output_path):
                    write_header = False
                else:
                    write_header = True

                batch_result.to_csv(output_path, mode='a', header=write_header)

                progress.update(task1, advance=1)

            id = str(uuid.uuid4())
            progress.stop()
            # upload_to_s3(input_path=input_path,output_path=output_path)
            time_elapsed = time.time() - time_start
            console.print(run_summary_panel(id, start_row, end_row, time_elapsed, output_path))
            status_update = questionary.confirm("Exit the application?", style=PROMPT_STYLE).ask()

            if status_update:
                console.print(f"[{MUTED}]Session ended.[/]")
                status = False



if __name__ == "__main__":
    show()
