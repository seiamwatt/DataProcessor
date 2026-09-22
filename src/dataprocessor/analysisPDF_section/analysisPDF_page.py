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
from dotenv import load_dotenv
from dataprocessor.config import load_env
import os
from dataprocessor.analysis_section import report_analysis
import sys
import uuid
from rich.columns import Columns
import boto3
import pytz

from dataprocessor.analysisPDF_section import report_analysis_pdfs

console = Console()

# THEME ---------------------------------------------------------------------------
# One shared htop palette for every screen. Edit dataprocessor/theme.py to
# retheme the whole application; nothing here defines its own colors.
from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, ERR_BAR, EMPHASIS, MUTED, OK, WARN, ERR,
    CYAN, GREEN, RED, BLUE, PURPLE, PROMPT_STYLE, chip, section,
)
# ---------------------------------------------------------------------------------


# resource path
def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_env()
os.environ["TERM"] = "xterm-256color"


def analysis_section_panel() -> Panel:
    """Application masthead."""
    title = Text(" ANALYSIS SECTION \u2014 PDF ", style=f"bold {ACCENT_BAR}")
    subtitle = Text("Directory-based PDF analysis pipeline", style=MUTED)
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

    table.add_row("Input directory", "Directory containing the PDF files", f"[bold {RED}]Yes[/]", "\u2014")
    table.add_row("Output name", "Output CSV file name, saved in the input directory", f"[bold {RED}]Yes[/]", "output.csv")
    table.add_row("Batch size", "Rows processed per batch", f"[{GREEN}]No[/]", "3")
    table.add_row("Start at", "PDF number to start from (1-based); later PDFs append", f"[{GREEN}]No[/]", "1 (first PDF)")
    return table


def run_summary_panel(num_total, start_at, pdf_processed_count, skipped, elapsed, output_path) -> Panel:
    """Final report card for the completed run."""
    mins, secs = divmod(int(elapsed), 60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("PDFs in directory", f"{num_total:,}")
    table.add_row("Started at", Text(f"#{start_at}", style=BLUE))
    table.add_row("Processed", Text(f"{pdf_processed_count:,}", style=PURPLE))
    table.add_row("Skipped", Text(f"{skipped:,}", style=PURPLE))
    table.add_row("Elapsed time", Text(f"{mins}m {secs}s", style=BLUE))
    table.add_row("Output file", Text(output_path, style=GREEN))
    return Panel(table, title=Text(" Run complete ", style=f"bold {OK_BAR}"),
                 title_align="left", border_style=OK, box=box.SQUARE)


def show():
    status = True
    load_env()
    console.clear()
    console.print(analysis_section_panel())

    console.print(args_table())
    console.print()

    while status:
        input_status = True

        console.print(section("Configuration"))

        while input_status:
            try:
                input_path = questionary.path("Input directory", style=PROMPT_STYLE).ask()
                if input_path is None:
                    return
                # Strip surrounding quotes (e.g. drag-and-drop adds them on macOS)
                input_path = input_path.strip("'\" ")

                # Validate that input_path is actually a directory
                if not os.path.isdir(input_path):
                    console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Input path is not a valid directory.[/] [{MUTED}]Provide the path to a folder, not a file.[/]")
                    continue

                output_name = questionary.text(
                    "Output file name (e.g. results.csv)",
                    default="output.csv",
                    style=PROMPT_STYLE,
                ).ask()
                if output_name is None:
                    return

                # Ensure the filename ends with .csv
                if not output_name.endswith(".csv"):
                    output_name += ".csv"

                # Place the output CSV inside the input directory
                output_path = os.path.join(input_path, output_name)

                batch_input = questionary.text(
                    "Batch size", default="3", style=PROMPT_STYLE
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
                    console.print(f"[{ERR_BAR}] ERR [/] [{RED}]No PDF files were found in the directory.[/] [{MUTED}]Check the path and try again.[/]")
                    continue

                console.print(f"[{MUTED}]Found {num_total:,} PDF files in the directory.[/]")

                start_input = questionary.text(
                    f"Start at PDF # (1\u2013{num_total})", default="1", style=PROMPT_STYLE
                ).ask()
                if start_input is None:
                    return

                start_at = int(start_input)
                if start_at < 1 or start_at > num_total:
                    console.print(
                        f"[{ERR}]Start position must be between 1 and {num_total}.[/]"
                    )
                    continue

                input_status = False

            except ValueError:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Batch size and start position must be whole numbers.[/]")
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input.[/] [{MUTED}]{e}[/]")

        deep_key = os.getenv("DeepSeek_key")
        if deep_key is None:
            console.print(f"[{ERR_BAR}] ERR [/] [{RED}]DeepSeek API key not found.[/] [{MUTED}]Add DeepSeek_key to the .env file and restart the application.[/]")
            return

        gpt_key = os.getenv("GPT_key")
        if gpt_key is None:
            console.print(f"[{ERR_BAR}] ERR [/] [{RED}]GPT API key not found.[/] [{MUTED}]Add GPT_key to the .env file and restart the application.[/]")
            return

        gemini_key = os.getenv("Gemini_key")
        if gemini_key is None:
            console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Gemini API key not found.[/] [{MUTED}]Add Gemini_key to the .env file and restart the application.[/]")
            return

        # Slice the list from the chosen start position (1-based -> 0-based)
        pdf_files = all_pdf_files[start_at - 1 :]
        num_pdfs = len(pdf_files)

        console.print(section("Processing"))
        console.print(
            f"[{MUTED}]{num_total:,} PDFs found. Starting from #{start_at} "
            f"({num_pdfs:,} to process). Output: {output_path}[/]"
        )

        # If resuming mid-way, append instead of overwriting
        first_write = start_at == 1
        pdf_processed_count = 0

        with Progress(
            SpinnerColumn(style=ACCENT),
            TextColumn(f"[bold {ACCENT}]{{task.description}}"),
            BarColumn(bar_width=None, style="grey23", complete_style=OK,
                          finished_style=OK, pulse_style=ACCENT),
            MofNCompleteColumn(),
            TextColumn(f"[{MUTED}]files"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            time_start = time.time()
            task1 = progress.add_task("Analysing PDFs", total=num_pdfs)

            for file in pdf_files:
                progress.update(task1, description=f"Processing {file}")
                file_path = os.path.join(input_path, file)
                analysis_result = report_analysis_pdfs.pdf_processing(
                    deep_key, gemini_key, gpt_key, file_path
                )

                # Skip if processing failed or returned empty
                if analysis_result is None or analysis_result.empty:
                    console.print(f"[{WARN}]Skipped {file} \u2014 processing failed or returned no data.[/]")
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
                progress.update(task1, description=f"Processed {file}  |  {pdf_processed_count:,} completed")

            elapsed = time.time() - time_start

        console.print(run_summary_panel(num_total, start_at, pdf_processed_count,
                                        num_pdfs - pdf_processed_count, elapsed, output_path))

        status_update = questionary.confirm("Exit the application?", style=PROMPT_STYLE).ask()
        if status_update:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False


if __name__ == "__main__":
    show()