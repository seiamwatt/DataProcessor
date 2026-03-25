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
from dotenv import load_dotenv
import os
from analysis_section import report_analysis
import sys
import uuid
from rich.columns import Columns
import boto3
import pytz

from analysisPDF_section import report_analysis_pdfs

console = Console()


# resource path
def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_dotenv(resource_path(".env"))
os.environ["TERM"] = "xterm-256color"


def analysis_section_panel() -> Panel:
    art = """[red]
  ████  ██  ██  ████  ██    ██  ██ ████  █ ████  [blue] ████  █████  ████  ██████ █  ████  ██  ██
[red] ██  ██ ███ ██ ██  ██ ██    ██  ██ ██    █ ██    [blue] ██    ██     ██      ██   █ ██  ██ ███ ██
[red] █████  ██ ███ █████  ██     ████   ███  █  ███  [blue]  ███  ████   ██      ██   █ ██  ██ ██ ███
[red] ██  ██ ██  ██ ██  ██ █████   ██   ████  █ ████  [blue] ████  █████  ████    ██   █  ████  ██  ██
"""
    return Panel(art, highlight=True)


def args_table() -> Table:
    table = Table(title="[blue]Arguments Needed", border_style="bright_cyan")
    table.add_column("[red]Args", no_wrap=True)
    table.add_column("[red]Description", no_wrap=True)
    table.add_column("[red]Required", no_wrap=True)

    table.add_row("input path", "input directory containing PDFs", "True")
    table.add_row("output name", "output CSV filename", "True")
    table.add_row("batch size", "num rows to process per batch", "False")
    table.add_row("start at", "PDF # to start processing from (1-based)", "False")
    return table


def default_value_table() -> Table:
    table = Table(title="[blue]Default Values", border_style="bright_cyan")
    table.add_column("[red]Args", no_wrap=True)
    table.add_column("[red]Default Value", no_wrap=True)

    table.add_row("Start", "Top pdf in directory")
    table.add_row("End", "All pdf in directory")
    table.add_row("batch size", "3")
    table.add_row("start at", "1 (first PDF)")
    return table


def processing_end_panel() -> Panel:
    return Panel("", title="[bold blue] End of processing")


def show():
    status = True
    load_dotenv(resource_path(".env"))
    console.print(analysis_section_panel())

    console.print(Columns([args_table(), default_value_table()]))

    while status:
        input_status = True

        while input_status:
            try:
                input_path = questionary.path("Input directory").ask()
                if input_path is None:
                    return
                # Strip surrounding quotes (e.g. drag-and-drop adds them on macOS)
                input_path = input_path.strip("'\" ")

                # Validate that input_path is actually a directory
                if not os.path.isdir(input_path):
                    console.print("[bold red]Input path is not a valid directory")
                    continue

                output_name = questionary.text(
                    "Output file name (e.g. results.csv)",
                    default="output.csv",
                ).ask()
                if output_name is None:
                    return

                # Ensure the filename ends with .csv
                if not output_name.endswith(".csv"):
                    output_name += ".csv"

                # Place the output CSV inside the input directory
                output_path = os.path.join(input_path, output_name)

                batch_input = questionary.text(
                    "Batch Size", default="3"
                ).ask()
                if batch_input is None:
                    return

                batch_size = int(batch_input)

                # --- Start-at prompt ---
                # Collect PDFs early so we can show the total count
                all_pdf_files = sorted(
                    [f for f in os.listdir(input_path) if f.endswith(".pdf")]
                )
                num_total = len(all_pdf_files)

                if num_total == 0:
                    console.print("[bold red]No PDF files found in the directory")
                    continue

                start_input = questionary.text(
                    f"Start at PDF # (1–{num_total})", default="1"
                ).ask()
                if start_input is None:
                    return

                start_at = int(start_input)
                if start_at < 1 or start_at > num_total:
                    console.print(
                        f"[bold red]Start must be between 1 and {num_total}"
                    )
                    continue

                input_status = False

            except ValueError:
                console.print("[bold red]Batch size and start-at must be numbers")
            except Exception as e:
                console.print(f"[bold red]Invalid input: {e}")

        deep_key = os.getenv("DeepSeek_key")
        if deep_key is None:
            console.print("[bold red]DeepSeek API key invalid")
            return

        gpt_key = os.getenv("GPT_key")
        if gpt_key is None:
            console.print("[bold red]GPT API key invalid ")
            return

        gemini_key = os.getenv("Gemini_key")
        if gemini_key is None:
            console.print("[bold red]Gemini API key invalid")
            return

        # Slice the list from the chosen start position (1-based → 0-based)
        pdf_files = all_pdf_files[start_at - 1 :]
        num_pdfs = len(pdf_files)

        console.print(
            f"[bold green]Found {num_total} PDFs total — "
            f"starting from #{start_at} ({num_pdfs} to process) — "
            f"output will be saved to: {output_path}"
        )

        # If resuming mid-way, append instead of overwriting
        first_write = start_at == 1
        pdf_processed_count = 0

        with Progress() as progress:
            time_start = time.time()
            task1 = progress.add_task("[red]Analysing data", total=num_pdfs)

            for file in pdf_files:
                file_path = os.path.join(input_path, file)
                analysis_result = report_analysis_pdfs.pdf_processing(
                    deep_key, gemini_key, gpt_key, file_path
                )

                # Skip if processing failed or returned empty
                if analysis_result is None or analysis_result.empty:
                    console.print(f"[bold yellow]  Skipping {file} (processing failed)")
                    progress.update(task1, advance=1)
                    continue

                # Write header only on the first file, then append without header
                if first_write:
                    analysis_result.to_csv(output_path, mode="w", index=False)
                    first_write = False
                else:
                    analysis_result.to_csv(
                        output_path, mode="a", header=False, index=False
                    )

                progress.update(task1, advance=1)
                pdf_processed_count += 1
                console.print("[bold red] PDF Processed")
                console.print(f"[bold red]count:{pdf_processed_count}")

            elapsed = time.time() - time_start
            console.print(f"[bold green]Done in {elapsed:.1f}s — saved to {output_path}")

        console.print(processing_end_panel())

        status_update = questionary.confirm("Exit?").ask()
        if status_update:
            status = False


if __name__ == "__main__":
    show()

    