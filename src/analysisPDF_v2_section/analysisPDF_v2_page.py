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
import questionary
from dotenv import load_dotenv,find_dotenv
import os
import pandas as pd
import sys
import time
from rich.progress import Progress
import uuid
from rich.columns import Columns
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
import pytz
import os
import glob

from analysisPDF_v2_section import analysisPDF_v2

console = Console()
os.environ["TERM"] = "xterm-256color"

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_dotenv(resource_path(".env"))


# ── UI Panels ───────────────────────────────────────────────────────────────


def banner_panel() -> Panel:
    art = """[red]
  ████  ██  ██  ████  ██    ██  ██ ████  █ ████  [blue] ████  █████  ████  ██████ █  ████  ██  ██
[red] ██  ██ ███ ██ ██  ██ ██    ██  ██ ██    █ ██    [blue] ██    ██     ██      ██   █ ██  ██ ███ ██
[red] █████  ██ ███ █████  ██     ████   ███  █  ███  [blue]  ███  ████   ██      ██   █ ██  ██ ██ ███
[red] ██  ██ ██  ██ ██  ██ █████   ██   ████  █ ████  [blue] ████  █████  ████    ██   █  ████  ██  ██
"""
    return Panel(art, highlight=True, subtitle="[dim]PDF Annual Report Coding Pipeline[/dim]")


def args_table() -> Table:
    table = Table(title="[blue]Arguments Needed", border_style="bright_cyan")
    table.add_column("[red]Args", no_wrap=True)
    table.add_column("[red]Description", no_wrap=True)
    table.add_column("[red]Required", no_wrap=True)

    table.add_row("PDF directory", "Folder containing PDF files", "True")
    table.add_row("Output CSV", "Output CSV file path", "True")
    table.add_row("Start PDF", "Index of first PDF to process", "False")
    table.add_row("End PDF", "Index of last PDF to process", "False")
    table.add_row("Max pages", "Max pages to extract per PDF", "False")
    return table


def default_value_table() -> Table:
    table = Table(title="[blue]Default Values", border_style="bright_cyan")
    table.add_column("[red]Args", no_wrap=True)
    table.add_column("[red]Default Value", no_wrap=True)

    table.add_row("Start PDF", "0")
    table.add_row("End PDF", "total PDFs found")
    table.add_row("Max pages", "2000")
    return table


def api_status_table(deep_ok: bool, gemini_ok: bool, gpt_ok: bool) -> Table:
    table = Table(title="[blue]API Key Status", border_style="bright_cyan")
    table.add_column("[red]Provider", no_wrap=True)
    table.add_column("[red]Status", no_wrap=True)

    def status(ok):
        return "[bold green]✓ Found[/bold green]" if ok else "[bold red]✗ Missing[/bold red]"

    table.add_row("DeepSeek", status(deep_ok))
    table.add_row("Gemini", status(gemini_ok))
    table.add_row("GPT", status(gpt_ok))
    return table


def processing_end_panel(total_pdfs: int, elapsed: float, output_path: str) -> Panel:
    msg = (
        f"[bold green]Processing complete![/bold green]\n"
        f"[blue]PDFs processed:[/blue] {total_pdfs}\n"
        f"[blue]Time elapsed:[/blue]   {elapsed:.1f}s\n"
        f"[blue]Output saved:[/blue]   {output_path}"
    )
    return Panel(msg, title="[bold blue]Results", border_style="green")


# ── Single PDF processor (with console output) ─────────────────────────────


def process_single_pdf(pdf_path, deep_key, gemini_key, gpt_key, max_pages):
    """Process one PDF through all three LLMs. Returns a result dict."""

    result_row = {"PDF": os.path.basename(pdf_path)}

    # extract text
    console.print("  [dim]Extracting text...[/dim]", end=" ")
    t0 = time.time()
    pdf_txt = analysisPDF_v2.extract_pdf_text(pdf_path, max_pages=max_pages)
    ext_time = time.time() - t0

    if pdf_txt is None:
        console.print(f"[bold red]FAILED[/bold red] ({ext_time:.1f}s)")
        return result_row

    console.print(f"[green]{len(pdf_txt)} chars[/green] ({ext_time:.1f}s)")

    prompt = analysisPDF_v2.create_prompt(
        pdf_filename=os.path.basename(pdf_path),
        pdf_text=pdf_txt,
    )

    # ── LLM calls ───────────────────────────────────────────────────────
    llm_calls = [
        ("DeepSeek", "ds", lambda: analysisPDF_v2.connect_to_DeepSeek(api_key=deep_key, prompt=prompt)),
        ("Gemini", "gm", lambda: analysisPDF_v2.connect_to_Gemini(api_key=gemini_key, prompt=prompt)),
        ("GPT", "gpt", lambda: analysisPDF_v2.connect_to_GPT(api_key=gpt_key, prompt=prompt)),
    ]

    for display_name, prefix, call_fn in llm_calls:
        console.print(f"  [dim]Calling {display_name}...[/dim]", end=" ")
        output = call_fn()

        if output:
            console.print("[bold green]✓[/bold green]")
            for field in analysisPDF_v2.JSON_FIELDS:
                result_row[f"{prefix}_{field}"] = output.get(field, "")
        else:
            console.print("[bold red]✗[/bold red]")
            for field in analysisPDF_v2.JSON_FIELDS:
                result_row[f"{prefix}_{field}"] = "Parsing Error"

    return result_row


# ── Main loop ──────────────────────────────────────────────────────────────


def show():
    status = True

    console.print(banner_panel())
    console.print(Columns([args_table(), default_value_table()]))
    console.print()

    # ── Load API keys ───────────────────────────────────────────────────
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
        console.print(f"[bold red]Missing API keys in .env: {', '.join(missing)}[/bold red]")
        return

    # ── Session loop ────────────────────────────────────────────────────
    while status:
        console.print(Rule("[bold blue]New Session[/bold blue]"))
        input_valid = False

        while not input_valid:
            try:
                pdf_dir = questionary.path("PDF directory:").ask()
                if pdf_dir is None:
                    return
                pdf_dir = pdf_dir.strip("'\"")

                if not os.path.isdir(pdf_dir):
                    console.print("[bold red]Directory not found.[/bold red]")
                    continue

                pdf_files = sorted(
                    glob.glob(os.path.join(pdf_dir, "**", "*.pdf"), recursive=True)
                )
                if not pdf_files:
                    console.print("[bold red]No PDFs found in that directory.[/bold red]")
                    continue

                console.print(f"[green]Found {len(pdf_files)} PDF(s)[/green]")

                output_path = questionary.path("Output CSV file:").ask()
                if output_path is None:
                    return
                output_path = output_path.strip("'\"")

                # make output relative to pdf_dir if not absolute
                if not os.path.isabs(output_path):
                    output_path = os.path.join(os.path.dirname(pdf_dir), output_path)

                start_pdf_str = questionary.text(
                    "Start PDF index", default="0"
                ).ask()
                start_pdf = int(start_pdf_str)

                end_pdf_str = questionary.text(
                    "End PDF index", default=str(len(pdf_files))
                ).ask()
                end_pdf = int(end_pdf_str)

                # clamp values
                start_pdf = max(0, min(start_pdf, len(pdf_files)))
                end_pdf = max(start_pdf, min(end_pdf, len(pdf_files)))

                max_pages_str = questionary.text(
                    "Max pages per PDF", default="2000"
                ).ask()
                max_pages = int(max_pages_str)

                input_valid = True

            except KeyboardInterrupt:
                return
            except Exception:
                console.print("[bold red]Invalid input, try again.[/bold red]")

        # ── Processing ──────────────────────────────────────────────────
        pdf_subset = pdf_files[start_pdf:end_pdf]
        total = len(pdf_subset)
        console.print()
        console.print(f"[bold red]Processing PDFs {start_pdf}–{end_pdf - 1} ({total} PDF(s))[/bold red]")
        console.print()

        results = []
        time_start = time.time()

        with Progress() as progress:
            task = progress.add_task("[red]Analysing PDFs", total=total)

            for i, pdf_path in enumerate(pdf_subset, 1):
                global_idx = start_pdf + i - 1
                console.print(
                    f"[bold blue][{global_idx}/{len(pdf_files)}][/bold blue] {os.path.basename(pdf_path)}"
                )

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

                print(f"  [dim]Appended row {global_idx} → {output_path}[/dim]")
                

                progress.update(task, advance=1)

        time_elapsed = time.time() - time_start
        run_id = str(uuid.uuid4())[:8]

        
        console.print(processing_end_panel(total, time_elapsed, output_path))
        console.print(f"[dim]Run ID: {run_id}[/dim]")
        

        # ── Continue or exit ────────────────────────────────────────────
        exit_choice = questionary.confirm("Exit?").ask()
        if exit_choice:
            status = False

    


if __name__ == "__main__":
    show()