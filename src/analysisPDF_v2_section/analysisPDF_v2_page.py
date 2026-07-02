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
import os
import pandas as pd
import sys
import uuid
from rich.columns import Columns
import boto3
from botocore.exceptions import ClientError
import pytz
import glob

from analysisPDF_v2_section import analysisPDF_v2

console = Console()
os.environ["TERM"] = "xterm-256color"

# THEME ---------------------------------------------------------------------------
# Presentation-layer constants only. Edit these to retheme the whole application.
ACCENT = "cyan"
EMPHASIS = "bold white"
MUTED = "grey62"
OK = "green"
ERR = "bold red"

PROMPT_STYLE = questionary.Style([
    ("qmark", "fg:#00d7ff bold"),
    ("question", "bold"),
    ("answer", "fg:#00d7ff"),
    ("pointer", "fg:#00d7ff bold"),
])
# ---------------------------------------------------------------------------------


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_dotenv(resource_path(".env"))


# -- UI Panels ----------------------------------------------------------------


def banner_panel() -> Panel:
    """Application masthead."""
    title = Text("ANALYSIS SECTION \u2014 PDF V2", style=f"bold {ACCENT}")
    subtitle = Text("PDF annual report coding pipeline", style=MUTED)
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

    table.add_row("PDF directory", "Folder containing PDF files (searched recursively)", "Yes", "\u2014")
    table.add_row("Output CSV", "Output CSV file path", "Yes", "\u2014")
    table.add_row("Start PDF", "Index of the first PDF to process", "No", "0")
    table.add_row("End PDF", "Index of the last PDF to process", "No", "Total PDFs found")
    table.add_row("Max pages", "Maximum pages to extract per PDF", "No", "2000")
    return table


def api_status_table(deep_ok: bool, gemini_ok: bool, gpt_ok: bool) -> Table:
    """Environment check: one row per provider."""
    table = Table(
        title="Environment check",
        title_style=f"bold {ACCENT}",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        border_style=MUTED,
        header_style=f"bold {ACCENT}",
        pad_edge=False,
    )
    table.add_column("Provider", no_wrap=True, style=EMPHASIS)
    table.add_column("API key", justify="center", no_wrap=True)

    def status(ok):
        return Text("Configured", style=OK) if ok else Text("Missing", style=ERR)

    table.add_row("DeepSeek", status(deep_ok))
    table.add_row("Gemini", status(gemini_ok))
    table.add_row("GPT", status(gpt_ok))
    return table


def processing_end_panel(total_pdfs: int, elapsed: float, output_path: str, run_id: str) -> Panel:
    """Final report card for the completed run."""
    mins, secs = divmod(int(elapsed), 60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", run_id)
    table.add_row("PDFs processed", f"{total_pdfs:,}")
    table.add_row("Elapsed time", f"{mins}m {secs}s")
    table.add_row("Output file", output_path)
    return Panel(table, title=Text("Run complete", style=f"bold {OK}"),
                 title_align="left", border_style=OK, box=box.ROUNDED)


# -- Single PDF processor (with console output) --------------------------------
def process_single_pdf(pdf_path, deep_key, gemini_key, gpt_key, max_pages):
    """Process one PDF through all three LLMs. Returns a result dict."""

    result_row = {"PDF": os.path.basename(pdf_path)}

    # extract text
    t0 = time.time()
    pdf_txt = analysisPDF_v2.extract_pdf_text(pdf_path, max_pages=max_pages)
    ext_time = time.time() - t0

    if pdf_txt is None:
        console.print(f"    [{ERR}]Text extraction failed[/] [{MUTED}]({ext_time:.1f}s)[/]")
        return result_row

    console.print(f"    [{MUTED}]Text extracted \u2014 {len(pdf_txt):,} characters ({ext_time:.1f}s)[/]")

    prompt = analysisPDF_v2.create_prompt(
        pdf_filename=os.path.basename(pdf_path),
        pdf_text=pdf_txt,
    )

    # -- LLM calls --------------------------------------------------------
    llm_calls = [
        ("DeepSeek", "ds", lambda: analysisPDF_v2.connect_to_DeepSeek(api_key=deep_key, prompt=prompt)),
        ("Gemini", "gm", lambda: analysisPDF_v2.connect_to_Gemini(api_key=gemini_key, prompt=prompt)),
        ("GPT", "gpt", lambda: analysisPDF_v2.connect_to_GPT(api_key=gpt_key, prompt=prompt)),
    ]

    for display_name, prefix, call_fn in llm_calls:
        output = call_fn()

        if output:
            console.print(f"    {display_name}: [{OK}]Succeeded[/]")
            for field in analysisPDF_v2.JSON_FIELDS:
                result_row[f"{prefix}_{field}"] = output.get(field, "")
        else:
            console.print(f"    {display_name}: [{ERR}]Failed \u2014 recorded as parsing error[/]")
            for field in analysisPDF_v2.JSON_FIELDS:
                result_row[f"{prefix}_{field}"] = "Parsing Error"

    return result_row

# -- Main loop ------------------------------------------------------------------


def show():
    status = True

    console.clear()
    console.print(banner_panel())
    console.print(args_table())
    console.print()

    # -- Load API keys ----------------------------------------------------
    deep_key = os.getenv("DeepSeek_key")
    gemini_key = os.getenv("Gemini_key")
    gpt_key = os.getenv("GPT_key")

    console.print(
        api_status_table(
            deep_ok=deep_key is not None,
            gemini_ok=gemini_key is not None,
            gpt_ok=gpt_key is not None,
        )
    )
    console.print()

    missing = []
    if deep_key is None:
        missing.append("DeepSeek_key")
    if gemini_key is None:
        missing.append("Gemini_key")
    if gpt_key is None:
        missing.append("GPT_key")

    if missing:
        console.print(f"[{ERR}]Missing API keys in .env: {', '.join(missing)}.[/] [{MUTED}]Add them and restart the application.[/]")
        return

    # -- Session loop ------------------------------------------------------
    while status:
        console.print(Rule("Configuration", style=ACCENT))
        input_valid = False

        while not input_valid:
            try:
                pdf_dir = questionary.path("PDF directory:", style=PROMPT_STYLE).ask()
                if pdf_dir is None:
                    return
                pdf_dir = pdf_dir.strip("'\"")

                if not os.path.isdir(pdf_dir):
                    console.print(f"[{ERR}]Directory not found.[/] [{MUTED}]Check the path and try again.[/]")
                    continue

                pdf_files = sorted(
                    glob.glob(os.path.join(pdf_dir, "**", "*.pdf"), recursive=True)
                )
                if not pdf_files:
                    console.print(f"[{ERR}]No PDF files were found in that directory.[/]")
                    continue

                console.print(f"[{MUTED}]Found {len(pdf_files):,} PDF file(s).[/]")

                output_path = questionary.path("Output CSV file:", style=PROMPT_STYLE).ask()
                if output_path is None:
                    return
                output_path = output_path.strip("'\"")

                # make output relative to pdf_dir if not absolute
                if not os.path.isabs(output_path):
                    output_path = os.path.join(os.path.dirname(pdf_dir), output_path)

                start_pdf_str = questionary.text(
                    "Start PDF index", default="0", style=PROMPT_STYLE
                ).ask()
                start_pdf = int(start_pdf_str)

                end_pdf_str = questionary.text(
                    "End PDF index", default=str(len(pdf_files)), style=PROMPT_STYLE
                ).ask()
                end_pdf = int(end_pdf_str)

                # clamp values
                start_pdf = max(0, min(start_pdf, len(pdf_files)))
                end_pdf = max(start_pdf, min(end_pdf, len(pdf_files)))

                max_pages_str = questionary.text(
                    "Max pages per PDF", default="2000", style=PROMPT_STYLE
                ).ask()
                max_pages = int(max_pages_str)

                input_valid = True

            except KeyboardInterrupt:
                return
            except Exception:
                console.print(f"[{ERR}]Invalid input.[/] [{MUTED}]Ensure numeric fields contain whole numbers, then try again.[/]")

        # -- Processing --------------------------------------------------
        pdf_subset = pdf_files[start_pdf:end_pdf]
        total = len(pdf_subset)
        console.print(Rule("Processing", style=ACCENT))
        console.print(f"[{MUTED}]Processing PDFs {start_pdf}\u2013{end_pdf - 1} ({total:,} file(s)).[/]")
        results = []
        time_start = time.time()

        with Progress(
            SpinnerColumn(style=ACCENT),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None, complete_style=ACCENT, finished_style=OK),
            MofNCompleteColumn(),
            TextColumn(f"[{MUTED}]files"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Analysing PDFs", total=total)

            for i, pdf_path in enumerate(pdf_subset, 1):
                global_idx = start_pdf + i - 1

                progress.update(task, description=f"File {global_idx} of {len(pdf_files)}")
                console.print(f"[{EMPHASIS}][{global_idx}/{len(pdf_files)}] {os.path.basename(pdf_path)}[/]")

                row = process_single_pdf(
                    pdf_path=pdf_path,
                    deep_key=deep_key,
                    gemini_key=gemini_key,
                    gpt_key=gpt_key,
                    max_pages=max_pages,
                )
                results.append(row)

                # append to CSV (write header only if file doesn't exist yet)
                write_header = not os.path.exists(output_path)
                pd.DataFrame([row]).to_csv(
                    output_path, mode="a", header=write_header, index=False
                )

                console.print(f"    [{MUTED}]Row {global_idx} appended to {output_path}[/]")

                progress.update(task, advance=1)

        time_elapsed = time.time() - time_start
        run_id = str(uuid.uuid4())[:8]


        console.print(processing_end_panel(total, time_elapsed, output_path, run_id))


        # -- Continue or exit ----------------------------------------------
        exit_choice = questionary.confirm("Exit the application?", style=PROMPT_STYLE).ask()
        if exit_choice:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False




if __name__ == "__main__":
    show()