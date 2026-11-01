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
import re
import json
from concurrent.futures import ThreadPoolExecutor

from dataprocessor.adjudicator_section import adjudicator
from dataprocessor.adjudicator_section import prompts

console = Console()
os.environ["TERM"] = "xterm-256color"

from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, ERR_BAR, EMPHASIS, MUTED, OK, WARN, ERR,
    CYAN, GREEN, RED, BLUE, PURPLE, PROMPT_STYLE, chip, section,
)

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()


def adjudicator_section_panel() -> Panel:
    title = Text("ADJUDICATOR SECTION",style= f"bold {ACCENT_BAR}")
    subtitle = Text("Adjudication pipeline",style=MUTED)
    meta = Text(datetime.now().strftime("Session started %Y-%m-%d %H:%M"), style=MUTED)

    body = Group(
        Align.center(title),
        Align.center(subtitle),
        Align.center(meta)
    )

    return Panel(
        Padding(body,(1,4)),
        box = box.SQUARE,
        border_style = ACCENT
    )


def adjudicator_args_table() -> Table:
    table = Table(
        title ="Run parameters",
        title_style = f"bold {ACCENT}",
        title_justify="left",
        box=None,
        header_style=f"bold {ACCENT_BAR}",
        pad_edge=False,
        expand=True
    )

    table.add_column("PARAMETER", no_wrap=True, style=EMPHASIS)
    table.add_column("DESCRIPTION", style=MUTED)
    table.add_column("REQUIRED", justify="center", no_wrap=True)
    table.add_column("DEFAULT", no_wrap=True, style=BLUE)

    required = Text("Yes",style=f"bold {RED}")
    optional = Text("No",style=GREEN)
    table.add_row("Input path", "Path to the analysis CSV (gm_ / gpt_ columns)", required, "\u2014")
    table.add_row("Output path", "Destination CSV file name or folder", required, "\u2014")
    table.add_row("Start row", "First row to process", optional, "0")
    table.add_row("End row", "Row at which processing stops", optional, "End of file")
    table.add_row("URL col name", "Column containing the PDF URL", optional, "url")
    return table


def adjudicator_summary_panel(id,start_row,end_row,time_elapsed,output_path) -> Panel:
    mins, secs = divmod((int(time_elapsed)),60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", Text(id, style=PURPLE))
    table.add_row("Rows processed", Text(f"{start_row:,} \u2013 {end_row:,}", style=PURPLE))
    table.add_row("Elapsed time", Text(f"{mins}m {secs}s", style=BLUE))
    table.add_row("Output file", Text(output_path, style=GREEN))
    return Panel(table, title=Text(" Adjudication complete ", style=f"bold {OK_BAR}"),
            title_align="left", border_style=OK, box=box.SQUARE)


ID_COLUMNS = ["org", "ein", "domain", "year"]
# column prefix per model, each model gets its own block of analysis columns
MODEL_PREFIXES = {"deepseek": "ds"}
OUTPUT_COLUMNS = ID_COLUMNS + ["url"] + [
    f"{prefix}_{col}" for prefix in MODEL_PREFIXES.values() for col in prompts.ANALYSIS_COLUMNS
]
RETRIES = 3
RETRY_SECONDS = 10


def clean_path(path):
    return path.strip().strip("'\"").replace("\\ ", " ")

def model_result(future,model,num_reports):
    try:
        return future.result()
    except Exception as e:
        console.print(f"[{ERR_BAR}] ERR [/] [{RED}]{model} failed:[/] [{MUTED}]{e}[/]")
        return [None] * num_reports

def show():
    status = True
    load_env()

    console.clear()
    console.print(adjudicator_section_panel())
    console.print(adjudicator_args_table())
    console.print()

    while(status):
        input_status = True
        console.print(Rule(Text(" Configuration ", style=ACCENT_BAR), style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = clean_path(input_path)
                df = adjudicator.load_csv(input_path)
                default_end_row = len(df)
                output_path = questionary.path("Output CSV file or folder:", style=PROMPT_STYLE).ask()
                start_row = questionary.text("Start row", default=str(0), style=PROMPT_STYLE).ask()
                end_row = questionary.text("End row", default=str(default_end_row), style=PROMPT_STYLE).ask()
                col_name = questionary.text("URL col name", default="url", style=PROMPT_STYLE).ask()
                batch_size = questionary.text("Batch size",default="10",style =PROMPT_STYLE).ask()
                batch_size = int(batch_size)
                output_path = os.path.join(os.path.dirname(input_path), clean_path(output_path))
                if os.path.isdir(output_path):
                    input_name = os.path.splitext(os.path.basename(input_path))[0]
                    output_path = os.path.join(output_path, f"{input_name}_adjudicated.csv")
                start_row = int(start_row)
                end_row = int(end_row)
                input_status = False
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input.[/] [{MUTED}]Check the file path and ensure numeric fields contain whole numbers, then try again.[/]")

        deepseek_api_key = os.getenv("DeepSeek_key")

        if (deepseek_api_key is None):
            console.print(f"[{ERR_BAR}] ERR [/] [{RED}]API key not found.[/] [{MUTED}]Add API keys to the .env file and restart the application.[/]")
            continue

        df_subset = df.iloc[start_row:end_row]
        BATCH = batch_size

        total_batches = (len(df_subset) + BATCH - 1) // BATCH
        row_track = start_row

        console.print(Rule(Text(" Processing ", style=ACCENT_BAR), style=ACCENT))

        with Progress(
            SpinnerColumn(style=ACCENT),
            TextColumn(f"[bold {ACCENT}]{{task.description}}"),
            BarColumn(bar_width=None, style="grey23", complete_style=OK,
                finished_style=OK, pulse_style=ACCENT),
            MofNCompleteColumn(),
            TextColumn(f"[{MUTED}]batches"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            time_start = time.time()
            task1 = progress.add_task("Adjudicating data",total=total_batches)

            for i in range(0,len(df_subset),BATCH):
                rows = []
                batch = df_subset.iloc[i:i + BATCH]
                report_batch = adjudicator.need_adjudicate(batch,col_name)

                with ThreadPoolExecutor(max_workers = 10) as process:
                    deepseek_process = process.submit(adjudicator.deepseek_batch,report_batch,deepseek_api_key)
                    result_deepseek = model_result(deepseek_process,"deepseek",len(report_batch))

                model_results = {
                    "deepseek":result_deepseek
                }

                # every row is written, only rows that needed adjudication get deepseek columns filled
                for j in range(len(batch)):
                    source_row = batch.iloc[j]

                    result_row = {}
                    for col in ID_COLUMNS:
                        result_row[col] = source_row.get(col, "")
                    result_row["url"] = source_row[col_name]

                    for model, result_rows in model_results.items():
                        prefix = MODEL_PREFIXES[model]
                        result = result_rows[j]
                        if not isinstance(result, dict):
                            result = {}
                        for col in prompts.ANALYSIS_COLUMNS:
                            result_row[f"{prefix}_{col}"] = result.get(col)

                    rows.append(result_row)

                flagged = sum(1 for report in report_batch if report[1] is not None)
                ok = sum(1 for result in result_deepseek if result is not None)
                progress.console.print(f"[{OK_BAR}] OK [/] [{MUTED}]rows {row_track:,} – {row_track + len(batch):,} → deepseek {ok}/{flagged} adjudicated[/]")
                row_track += len(batch)

                batch_result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
                
                if os.path.exists(output_path):
                    write_header = False
                else:
                    write_header = True

                batch_result.to_csv(output_path, mode='a', header=write_header, index=False)
                progress.update(task1, advance=1)

            id = str(uuid.uuid4())
            progress.stop()
            time_elapsed = time.time() - time_start

        console.print(adjudicator_summary_panel(id,start_row,end_row,time_elapsed,output_path))
        
        run_again = questionary.confirm("Repeat processing?", style=PROMPT_STYLE).ask()

        if not run_again:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False

        











            


