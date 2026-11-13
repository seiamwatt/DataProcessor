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
import threading
import queue


from dataprocessor.analysis_v2_section import analysis_v2 as analysis
from dataprocessor.analysis_v2_section import prompts


console = Console()
os.environ["TERM"] = "xterm-256color"

from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, ERR_BAR, EMPHASIS, MUTED, OK, WARN, ERR,
    CYAN, GREEN, RED, BLUE, PURPLE, PROMPT_STYLE, chip, section,
)
# ---------------------------------------------------------------------------------
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()



def analysis_section_panel() -> Panel:
    title = Text("ANALYSIS SECTION",style= f"bold {ACCENT_BAR}")
    subtitle = Text("Analysis pipeline",style=MUTED)
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

def args_table() -> Table:
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
    table.add_row("Input path", "Path to the source CSV file", required, "\u2014")
    table.add_row("Output path", "Destination CSV file name or folder", required, "\u2014")
    table.add_row("Start row", "First row to process", optional, "0")
    table.add_row("End row", "Row at which processing stops", optional, "End of file")
    table.add_row("Column name", "Column containing the PDF URL", optional, "url")
    return table


def run_summary_panel(id,start_row,end_row,time_elapsed,output_path) -> Panel:
    mins, secs = divmod((int(time_elapsed)),60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", Text(id, style=PURPLE))
    table.add_row("Rows processed", Text(f"{start_row:,} \u2013 {end_row:,}", style=PURPLE))
    table.add_row("Elapsed time", Text(f"{mins}m {secs}s", style=BLUE))
    table.add_row("Output file", Text(output_path, style=GREEN))
    return Panel(table, title=Text(" Run complete ", style=f"bold {OK_BAR}"),
            title_align="left", border_style=OK, box=box.SQUARE)




# output = original input columns untouched, then document/status columns, then analysis columns
STATUS_COLUMNS = ["Document_ID", "Document_type", "Source_URL", "Coding_status", "Review_scope", "Coding_notes"]
# column prefix per model, each model gets its own block of analysis columns
MODEL_PREFIXES = {"gemini": "gm", "gpt": "gpt"}
MODEL_NAMES = {"gemini": analysis.GEMINI_MODEL, "gpt": analysis.GPT_MODEL}
ANALYSIS_OUTPUT_COLUMNS = STATUS_COLUMNS + [
    f"{prefix}_{col}" for prefix in MODEL_PREFIXES.values() for col in prompts.ANALYSIS_COLUMNS
]
RETRIES = 3
RETRY_SECONDS = 10
# batches allowed to run at once; openai caps queued tokens per model, so too many fail with token_limit_exceeded
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT_BATCHES", 4))
# marks the end of the extraction queue
EXTRACTION_DONE = object()


# dragged paths come in quoted or with escaped spaces
def clean_path(path):
    return path.strip().strip("'\"").replace("\\ ", " ")


# network errors (connection reset, timeout) retry a few times
# RuntimeError means the batch job itself failed, so that is not retried
def with_retry(func,*args):
    for attempt in range(RETRIES):
        try:
            return func(*args)
        except RuntimeError:
            raise
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_SECONDS)


# gemini and gpt use batch jobs, submit then poll until results are ready
def gemini_run(reports,api_key):
    if all(report[1] is None for report in reports):
        return [None] * len(reports)

    job_name = with_retry(analysis.gemini_batch_submit,reports,api_key)
    analysis.wait_gemini(job_name,api_key)
    result_rows = with_retry(analysis.gemini_batch,job_name,api_key,len(reports))
    if result_rows is None:
        raise RuntimeError(f"gemini batch {job_name} still running after wait")
    return result_rows


def gpt_run(reports,api_key):
    if all(report[1] is None for report in reports):
        return [None] * len(reports)

    batch_id = with_retry(analysis.gpt_batch_submit,reports,api_key)
    analysis.wait_gpt(batch_id,api_key)
    result_rows = with_retry(analysis.gpt_batch,api_key,batch_id,len(reports))
    if result_rows is None:
        raise RuntimeError(f"gpt batch {batch_id} still running after wait")
    return result_rows

# document and coding status fields for one report
def status_record(source_row,url,report,model_results,j):
    failed = [model for model, result_rows in model_results.items() if result_rows[j] is None]

    if report[1] is None:
        status = "Pending"
        scope = "No text extracted"
    elif failed:
        status = "Awaiting review"
        scope = "Full extracted PDF text"
    else:
        status = "Completed"
        scope = "Full extracted PDF text"

    notes = f"LLM coders: {', '.join(f'{m} {n}' for m, n in MODEL_NAMES.items())}; {datetime.now():%Y-%m-%d}; protocol {prompts.PROTOCOL_VERSION}"
    if failed:
        notes += f"; failed: {', '.join(failed)}"

    return {
        "Document_ID": source_row.get("Document_ID", f"{source_row.get('ein', '')}_{source_row.get('year', '')}"),
        "Document_type": source_row.get("Document_type", "Annual report"),
        "Source_URL": url,
        "Coding_status": status,
        "Review_scope": scope,
        "Coding_notes": notes,
    }


def model_result(future,model,num_reports):
    try:
        return future.result()
    except Exception as e:
        console.print(f"[{ERR_BAR}] ERR [/] [{RED}]{model} failed:[/] [{MUTED}]{e}[/]")
        return [None] * num_reports


# one chunk of rows whose batch jobs are running in the background
class Chunk:
    def __init__(self,batch,reports,row_start,futures):
        self.batch = batch
        self.reports = reports
        self.row_start = row_start
        self.futures = futures   # {model: future}

    def done(self):
        return all(future.done() for future in self.futures.values())


# waits for a chunk's batch jobs, then appends its rows to the csv
def write_chunk(chunk,col_name,output_columns,output_path,progress,task):
    batch = chunk.batch
    model_results = {
        model: model_result(future,model,len(chunk.reports))
        for model, future in chunk.futures.items()
    }

    # one output row per report, models side by side as prefixed columns
    rows = []
    for j in range(len(batch)):
        source_row = batch.iloc[j]

        result_row = source_row.to_dict()
        result_row.update(status_record(source_row,source_row[col_name],chunk.reports[j],model_results,j))

        for model, result_rows in model_results.items():
            prefix = MODEL_PREFIXES[model]
            result = result_rows[j]
            if not isinstance(result, dict):
                result = {}
            for col in prompts.ANALYSIS_COLUMNS:
                result_row[f"{prefix}_{col}"] = result.get(col)

        rows.append(result_row)

    ok_counts = []
    for model, result_rows in model_results.items():
        ok = sum(1 for result in result_rows if result is not None)
        ok_counts.append(f"{model} {ok}/{len(batch)}")
    progress.console.print(f"[{OK_BAR}] OK [/] [{MUTED}]rows {chunk.row_start:,} – {chunk.row_start + len(batch):,} → {', '.join(ok_counts)}[/]")

    batch_result = pd.DataFrame(rows, columns=output_columns)
    write_header = not os.path.exists(output_path)
    batch_result.to_csv(output_path, mode='a', header=write_header, index=False)
    progress.update(task, advance=1)

def show():
    status = True
    load_env()

    console.clear()
    console.print(analysis_section_panel())
    console.print(args_table())
    console.print()

    while(status):
        input_status = True
        console.print(Rule(Text(" Configuration ", style=ACCENT_BAR), style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = clean_path(input_path)
                df = analysis.load_csv(input_path)
                default_end_row = len(df)
                output_path = questionary.path("Output CSV file or folder:", style=PROMPT_STYLE).ask()
                start_row = questionary.text("Start row", default=str(0), style=PROMPT_STYLE).ask()
                end_row = questionary.text("End row", default=str(default_end_row), style=PROMPT_STYLE).ask()
                col_name = questionary.text("Column name", default="url", style=PROMPT_STYLE).ask()
                batch_size = questionary.text("Batch size",default="10",style =PROMPT_STYLE).ask()
                batch_size = int(batch_size)
                output_path = os.path.join(os.path.dirname(input_path), clean_path(output_path))

                # dragged in a folder, name the file after the input
                if os.path.isdir(output_path):
                    input_name = os.path.splitext(os.path.basename(input_path))[0]
                    output_path = os.path.join(output_path, f"{input_name}_analysis.csv")
                start_row = int(start_row)
                end_row = int(end_row)
                input_status = False
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input.[/] [{MUTED}]Check the file path and ensure numeric fields contain whole numbers, then try again.[/]")

        gemini_api_key = os.getenv("Gemini_key")
        gpt_api_key = os.getenv("GPT_key")

        if (gemini_api_key is None) or (gpt_api_key is None):
            console.print(f"[{ERR_BAR}] ERR [/] [{RED}]API key not found.[/] [{MUTED}]Add API keys to the .env file and restart the application.[/]")
            continue

        output_columns = list(df.columns) + [col for col in ANALYSIS_OUTPUT_COLUMNS if col not in df.columns]
        df_subset = df.iloc[start_row:end_row]
        BATCH = batch_size

        total_batches = (len(df_subset) + BATCH - 1) // BATCH

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
            task1 = progress.add_task("Analysing data",total=total_batches)

            # chunks whose batches are running, oldest first so the csv stays in row order
            pending = deque()
            # extracted chunks waiting for a batch slot
            extracted = queue.Queue()

            def write_oldest():
                write_chunk(pending.popleft(),col_name,output_columns,output_path,progress,task1)

            def write_finished():
                while pending and pending[0].done():
                    write_oldest()

            # text extraction runs on its own thread so it keeps going while batches are at the cap
            def extract_all():
                try:
                    for i in range(0,len(df_subset),BATCH):
                        batch = df_subset.iloc[i:i + BATCH]
                        url_links = [batch.iloc[j][col_name] for j in range(len(batch))]
                        # rows with source == "wayback" are rate limited against web.archive.org
                        sources = [batch.iloc[j].get("source") for j in range(len(batch))]
                        reports = analysis.create_reports_batch(url_links,sources)
                        extracted.put((batch,reports,start_row + i))
                except Exception as e:
                    extracted.put(e)
                    return
                extracted.put(EXTRACTION_DONE)

            threading.Thread(target=extract_all,daemon=True).start()

            # each model's batch for a chunk runs in its own thread: submit, wait, collect
            with ThreadPoolExecutor(max_workers = MAX_IN_FLIGHT * len(MODEL_PREFIXES)) as process:
                while True:
                    try:
                        item = extracted.get(timeout=1)
                    except queue.Empty:
                        # nothing extracted yet, write any batches that finished meanwhile
                        write_finished()
                        continue

                    if item is EXTRACTION_DONE:
                        break
                    if isinstance(item, Exception):
                        raise item

                    batch, reports, row_start = item

                    # at the cap, wait for the oldest batch (extraction carries on in its thread)
                    while len(pending) >= MAX_IN_FLIGHT:
                        write_oldest()

                    futures = {
                        # "gemini": process.submit(gemini_run,reports,gemini_api_key),
                        "gpt": process.submit(gpt_run,reports,gpt_api_key),
                    }
                    pending.append(Chunk(batch,reports,row_start,futures))
                    write_finished()

                # extraction done, wait for the remaining batches
                while pending:
                    write_oldest()

            id = str(uuid.uuid4())
            progress.stop()
            time_elapsed = time.time() - time_start

        console.print(run_summary_panel(id,start_row,end_row,time_elapsed,output_path))

        run_again = questionary.confirm("Repeat processing?", style=PROMPT_STYLE).ask()

        if not run_again:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False
