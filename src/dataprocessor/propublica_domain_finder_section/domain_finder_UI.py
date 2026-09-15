import time
from datetime import datetime
import questionary
from rich.console import Console, Group
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
import pandas as pd
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

from dataprocessor.propublica_domain_finder_section import domain_finder

console = Console()
os.environ["TERM"] = "xterm-256color"

# THEME ---------------------------------------------------------------------------
# Presentation-layer constants only. Edit these to retheme the whole application.
ACCENT = "dodger_blue1"
EMPHASIS = "bold white"
MUTED = "grey62"
OK = "green"
WARN = "yellow"
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
def domain_finder_panel() -> Panel:
    """Application masthead."""
    title = Text("PROPUBLICA DOMAIN FINDER", style=f"bold {ACCENT}")
    subtitle = Text("Reads the website line off each org's latest Form 990", style=MUTED)
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

    table.add_row("Input path", "Path to the source CSV file", "Yes", "—")
    table.add_row("Output path", "Destination CSV file name", "Yes", "—")
    table.add_row("Column name", "Column containing the EIN", "No", "ein")
    table.add_row("Start row", "First row to process", "No", "0")
    table.add_row("End row", "Row at which processing stops", "No", "End of file")
    table.add_row("Filings per org", "Older filings to try when the newest has no website", "No", "3")
    return table


def notes_table() -> Table:
    """What the finder can and cannot read, so a blank column is not a surprise."""
    table = Table(
        title="Before you run",
        title_style=f"bold {ACCENT}",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        border_style=MUTED,
        header_style=f"bold {ACCENT}",
        pad_edge=False,
    )
    table.add_column("Note", no_wrap=True, style=EMPHASIS)
    table.add_column("Detail")

    table.add_row("Source", "Form 990 line J, read off the IRS copy of the filing")
    table.add_row("990-PF orgs", "Private foundations file a form with no website line, these come back blank")
    table.add_row("Old filings", "Pre 2016 scans are not mirrored by the IRS and are skipped")
    table.add_row("Speed", "Roughly 5s per org, the scans are OCR'd a batch at a time")
    return table


def display_tables():
    console.print(domain_finder_panel())
    console.print(args_table())
    console.print(notes_table())
    console.print()


def run_summary_panel(id, start_row, end_row, found_count, time_elapsed, output_path) -> Panel:
    """Final report card for the completed run."""
    mins, secs = divmod(int(time_elapsed), 60)
    rows_done = end_row - start_row
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", id)
    table.add_row("Rows processed", f"{start_row:,} – {end_row:,}")
    table.add_row("Domains found", f"{found_count:,} of {rows_done:,}")
    table.add_row("Elapsed time", f"{mins}m {secs}s")
    table.add_row("Output file", output_path)
    return Panel(table, title=Text("Run complete", style=f"bold {OK}"),
                 title_align="left", border_style=OK, box=box.ROUNDED)


def show():
    status = True

    load_env()
    console.clear()
    display_tables()

    while(status):
        input_status = True

        console.print(Rule("Configuration", style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = input_path.strip("'\"")
                df = domain_finder.load_csv(input_path)
                default_end_row = len(df)

                output_path = questionary.path("Output CSV file:", style=PROMPT_STYLE).ask()
                col_name = questionary.text("Column name", default="ein", style=PROMPT_STYLE).ask()
                start_row = questionary.text("Start row", default=str(0), style=PROMPT_STYLE).ask()
                end_row = questionary.text("End row", default=str(default_end_row), style=PROMPT_STYLE).ask()
                max_filings = questionary.text("Filings per org", default=str(3), style=PROMPT_STYLE).ask()
                output_path = os.path.join(os.path.dirname(input_path), output_path)

                start_row = int(start_row)
                end_row = int(end_row)
                max_filings = int(max_filings)

                if col_name not in df.columns:
                    console.print(f"[{ERR}]Column '{col_name}' is not in the file.[/] [{MUTED}]Available columns: {', '.join(df.columns)}[/]")
                    continue

                input_status = False
            except Exception as e:
                console.print(f"[{ERR}]Invalid input.[/] [{MUTED}]Check the file path and ensure numeric fields contain whole numbers, then try again.[/]")

        df_subset = df.iloc[start_row:end_row]
        BATCH = 30
        found_count = 0

        console.print(Rule("Processing", style=ACCENT))

        with Progress(
            SpinnerColumn(style=ACCENT),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None, complete_style=ACCENT, finished_style=OK),
            MofNCompleteColumn(),
            TextColumn(f"[{MUTED}]orgs"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            time_start = time.time()
            task1 = progress.add_task("Finding domains", total=len(df_subset))

            for i in range(0, len(df_subset), BATCH):
                rows = []
                futures = []
                batch = df_subset.iloc[i:i + BATCH]

                with ThreadPoolExecutor(max_workers=BATCH) as executor:
                    for j in range(len(batch)):
                        ein = batch.iloc[j][col_name]

                        try:
                            future = executor.submit(domain_finder.find_domain, ein, max_filings)
                            futures.append(future)
                        except Exception as e:
                            print(e)

                for j in range(len(batch)):
                    result_row = batch.iloc[j].copy()
                    domain = futures[j].result()
                    result_row["domain"] = domain
                    rows.append(result_row)

                    if domain:
                        found_count += 1
                        progress.console.print(f"[{OK}]{result_row[col_name]} → {domain}[/]")
                    else:
                        progress.console.print(f"[{MUTED}]{result_row[col_name]} → no domain on file[/]")

                    progress.update(task1, advance=1)

                batch_result = pd.DataFrame(rows)

                if os.path.exists(output_path):
                    write_header = False
                else:
                    write_header = True

                batch_result.to_csv(output_path, mode='a', header=write_header, index=False)

            id = str(uuid.uuid4())
            progress.stop()
            time_elapsed = time.time() - time_start

        console.print(run_summary_panel(id, start_row, end_row, found_count, time_elapsed, output_path))

        run_again = questionary.confirm("Repeat processing?", style=PROMPT_STYLE).ask()

        if not run_again:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False


if __name__ == "__main__":
    show()
