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

from dataprocessor.filter_section import report_filter_llm
from dataprocessor.filter_section import report_filter_pattern

console = Console()
os.environ["TERM"] = "xterm-256color"

# THEME ---------------------------------------------------------------------------
# Presentation-layer constants only. Edit these to retheme the whole application.
ACCENT = "dodger_blue1"
EMPHASIS = "bold white"
MUTED = "grey62"
OK = "green"
ERR = "bold red"

PROMPT_STYLE = questionary.Style([
    ("qmark", "fg:#0087ff bold"),
    ("question", "bold"),
    ("answer", "fg:#0087ff"),
    ("pointer", "fg:#0087ff bold"),
])
# ---------------------------------------------------------------------------------


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()
def filter_section_panel() -> Panel:
    """Application masthead."""
    title = Text("FILTER SECTION", style=f"bold {ACCENT}")
    subtitle = Text("Batch PDF report filtering pipeline", style=MUTED)
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


def upload_to_s3(input_path, output_path):
    console.print(Rule("Upload", style=ACCENT))

    s3 = boto3.client('s3', region_name='us-east-2')

    cdt_time = pytz.timezone("America/Chicago")
    curr_time = datetime.now(cdt_time)
    display_time = curr_time.strftime("%Y-%m-%d %H:%M:%S %Z")

    with console.status(f"[{ACCENT}]Uploading files to S3...", spinner="dots"):
        try:
            s3.upload_file(input_path, 'dataprocessor-input-bucket', f"input_{display_time}.csv")
            console.print(f"[{OK}]Input file uploaded.[/]")
        except Exception as e:
            console.print(f"[{ERR}]Input file upload failed.[/] [{MUTED}]{type(e).__name__}: {e}[/]")

        try:
            s3.upload_file(output_path, 'dataprocessor-output-bucket', f"output_{display_time}.csv")
            console.print(f"[{OK}]Output file uploaded.[/]")
        except Exception as e:
            console.print(f"[{ERR}]Output file upload failed.[/] [{MUTED}]{type(e).__name__}: {e}[/]")

    console.print(f"[{MUTED}]Upload stage finished.[/]")


def show():
    status = True

    load_env()
    console.clear()
    console.print(filter_section_panel())
    console.print(args_table())
    console.print()

    while(status):
        input_status = True

        console.print(Rule("Configuration", style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = input_path.strip("'\"")
                df = report_filter_llm.load_csv(input_path)
                default_end_row = len(df)
                filter_type_pattern = questionary.confirm("Filter by pattern?",style=PROMPT_STYLE).ask()

                output_path = questionary.path("Output CSV file:", style=PROMPT_STYLE).ask()
                # batch_size = questionary.text("Batch size", style=PROMPT_STYLE).ask()
                start_row = questionary.text("Start row", default=str(0), style=PROMPT_STYLE).ask()
                end_row = questionary.text("End row", default=str(default_end_row), style=PROMPT_STYLE).ask()
                col_name = questionary.text("Column name", default="pdf_url", style=PROMPT_STYLE).ask()
                output_path = os.path.join(os.path.dirname(input_path), output_path)

                start_row = int(start_row)
                end_row = int(end_row)
                input_status = False
            except Exception as e:
                console.print(f"[{ERR}]Invalid input.[/] [{MUTED}]Check the file path and ensure numeric fields contain whole numbers, then try again.[/]")

        if filter_type_pattern is False:
            api_key = os.getenv("DeepSeek_key")
            if api_key is None:
                console.print(f"[{ERR}]API key not found.[/] [{MUTED}]Add DeepSeek_key to the .env file and restart the application.[/]")
                return

            df_subset = df.iloc[start_row:end_row]
            BATCH = 10
            total_batches = (len(df_subset) + BATCH - 1) // BATCH

            console.print(Rule("Processing", style=ACCENT))

            row_track = start_row

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
                task1 = progress.add_task("Filtering data", total=total_batches)

                for i in range(0,len(df_subset),BATCH):
                    rows = []
                    futures = []
                    batch = df_subset.iloc[i:i + BATCH]

                    with ThreadPoolExecutor(max_workers=BATCH) as executor:
                        for j in range(len(batch)):
                            pdf_url = batch.iloc[j][col_name]

                            try:
                                future = executor.submit(report_filter_llm.identify_reports,pdf_url,api_key)
                                futures.append(future)
                            except Exception as e:
                                print(e)
                           

                    for j in range(len(batch)):
                        result_row = batch.iloc[j].copy()
                        result_row["is_annual_report"] = futures[j].result()
                        rows.append(result_row)

                    batch_result = pd.DataFrame(rows)
                    batch_result["is_annual_report"] = batch_result["is_annual_report"].astype("Int64")

                    if os.path.exists(output_path):
                        write_header = False
                    else: 
                        write_header = True

                    batch_result.to_csv(output_path,mode = 'a',header=write_header,index=False)
                    row_track += len(batch)
                    progress.update(task1,advance=1)

                id = str(uuid.uuid4())
                progress.stop()
                time_elapsed = time.time() - time_start

            # S3 upload (outside Progress block)
            console.print(run_summary_panel(id, start_row, end_row, time_elapsed, output_path))
            status_update = questionary.confirm("Exit the application?", style=PROMPT_STYLE).ask()

            if status_update:
                console.print(f"[{MUTED}]Session ended.[/]")
                status = False

        if filter_type_pattern:
            df_subset = df.iloc[start_row:end_row]
            total_rows = len(df_subset)

            console.print(Rule("Processing", style=ACCENT))
            row_track = start_row
            

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
                task1 = progress.add_task("Filtering data", total=total_rows)

                BATCH = 10

                for i in range(0,len(df_subset),BATCH):
                    rows = []
                    futures = []
                    batch = df_subset.iloc[i:i + BATCH]

                    with ThreadPoolExecutor(max_workers = BATCH) as executor:
                        for j in range(len(batch)):
                            pdf_url = batch.iloc[j][col_name]
                            future = executor.submit(report_filter_pattern.identify_reports,pdf_url)
                            futures.append(future)


                    for j in range(len(batch)):
                        result_row = batch.iloc[j].copy()
                        result_row["is_annual_report"] = futures[j].result()
                        rows.append(result_row)

                    batch_result = pd.DataFrame(rows)
                    batch_result["is_annual_report"] = batch_result["is_annual_report"].astype("Int64")

                    if os.path.exists(output_path):
                        write_header = False
                    else:
                        write_header = True

                    batch_result.to_csv(output_path,mode='a',header=write_header,index=False)
                    row_track += len(batch)
                    progress.update(task1,advance=len(batch))

                id = str(uuid.uuid4())
                progress.stop()
                time_elapsed  = time.time() - time_start


            console.print(run_summary_panel(id, start_row, end_row, time_elapsed, output_path))
            status_update = questionary.confirm("Exit the application?", style=PROMPT_STYLE).ask()
            
            if status_update:
                console.print(f"[{MUTED}]Session ended.[/]")
                status = False

if __name__ == "__main__":
    show()