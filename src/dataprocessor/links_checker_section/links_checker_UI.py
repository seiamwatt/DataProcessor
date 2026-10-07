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
from dataprocessor.config import load_env
import os
import pandas as pd
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

from dataprocessor.links_checker_section import links_checker

from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, ERR_BAR, EMPHASIS, MUTED, OK,
    GREEN, RED, BLUE, PURPLE, PROMPT_STYLE,
)
console = Console()

SOURCE_COL = "source"
RESULT_COL = "check_dead"

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()

# dragged paths come in quoted or with escaped spaces
def clean_path(path):
    return path.strip().strip("'\"").replace("\\ ", " ")

def links_section_panel() -> Panel:
    title = Text("Link check",style = f"bold {ACCENT_BAR}")
    subtitle = Text("Wayback/Live links checker pipeline", style= MUTED)
    meta = Text(datetime.now().strftime("Session started %Y-%m-%d %H:%M"), style=MUTED)

    body = Group(
        Align.center(title),
        Align.center(subtitle),
        Align.center(meta),
    )

    return Panel(
        Padding(body,(1,4)),
        box = box.SQUARE,
        border_style=ACCENT,
    )

def args_table() -> Table:
    table = Table(
        title = "Run parameters",
        title_style=f"bold {ACCENT}",
        title_justify="left",
        box=None,
        header_style=f"bold {ACCENT_BAR}",
        pad_edge=False,
        expand=True,
    )

    table.add_column("PARAMETER",no_wrap=True,style=EMPHASIS)
    table.add_column("DESCRIPTION",style=MUTED)
    table.add_column("REQUIRED",justify="center",no_wrap=True)
    table.add_column("DEFAULT",no_wrap=True,style=BLUE)

    required = Text("Yes", style=f"bold {RED}")
    optional = Text("No", style=GREEN)

    table.add_row("Input path", f"Source CSV file, needs a '{SOURCE_COL}' column (wayback/live)", required, "—")
    table.add_row("Output path", "Destination CSV file name or folder", required, "—")
    table.add_row("Start row", "First row to process", optional, "0")
    table.add_row("End row", "Row at which processing stops", optional, "End of file")
    table.add_row("URL col name", "Column containing the link to check", optional, "url")
    table.add_row("Batch size", "Rows checked per batch", optional, "200")

    return table

def run_summary_panel(id, start_row, end_row, dead_count, time_elapsed, output_path) -> Panel:
    """Final report card for the completed run."""
    mins, secs = divmod(int(time_elapsed), 60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", Text(id, style=PURPLE))
    table.add_row("Rows processed", Text(f"{start_row:,} – {end_row:,}", style=PURPLE))
    table.add_row("Dead links", Text(f"{dead_count:,}", style=RED))
    table.add_row("Elapsed time", Text(f"{mins}m {secs}s", style=BLUE))
    table.add_row("Output file", Text(output_path, style=GREEN))
    return Panel(table, title=Text(" Run complete ", style=f"bold {OK_BAR}"),
                 title_align="left", border_style=OK, box=box.SQUARE)

def show():
    status = True
    console.clear()
    console.print(links_section_panel())
    console.print(args_table())
    console.print()

    while(status):
        input_status = True
        console.print(Rule(Text(" Configuration ", style=ACCENT_BAR), style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = clean_path(input_path)
                df = pd.read_csv(input_path)
                default_end_row = len(df)
                output_path = questionary.path("Output CSV file or folder:", style=PROMPT_STYLE).ask()
                start_row = questionary.text("Start row", default=str(0), style=PROMPT_STYLE).ask()
                end_row = questionary.text("End row", default=str(default_end_row), style=PROMPT_STYLE).ask()
                col_name = questionary.text("URL col name", default="url", style=PROMPT_STYLE).ask()
                batch_size = questionary.text("Batch size", default=str(200), style=PROMPT_STYLE).ask()

                output_path = os.path.join(os.path.dirname(input_path), clean_path(output_path))

                # dragged in a folder, name the file after the input
                if os.path.isdir(output_path):
                    input_name = os.path.splitext(os.path.basename(input_path))[0]
                    output_path = os.path.join(output_path, f"{input_name}_links.csv")
                start_row = int(start_row)
                end_row = int(end_row)
                batch_size = int(batch_size)

                missing_cols = [col for col in (col_name, SOURCE_COL) if col not in df.columns]
                if missing_cols:
                    console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Missing column(s): {', '.join(missing_cols)}.[/] [{MUTED}]Check the column names in the CSV, then try again.[/]")
                    continue

                if batch_size < 1:
                    raise ValueError("batch size must be at least 1")

                input_status = False
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input.[/] [{MUTED}]Check the file path and ensure numeric fields contain whole numbers, then try again.[/]")

        df_subset = df.iloc[start_row:end_row]
        BATCH = batch_size

        total_batches = (len(df_subset) + BATCH - 1) // BATCH
        row_track = start_row
        dead_count = 0

        # shared across batches so the wayback budget holds for the whole run
        limiter = links_checker.rate_limiter(window_size=60,max_request=50)

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
            task1 = progress.add_task("Verifying links",total=total_batches)

            for i in range(0,len(df_subset),BATCH):
                rows = []
                futures = []
                batch = df_subset.iloc[i:i + BATCH]

                with ThreadPoolExecutor(max_workers=BATCH) as executor:
                    for j in range(len(batch)):
                        url = batch.iloc[j][col_name]
                        source = batch.iloc[j][SOURCE_COL]

                        if source == "wayback":
                            while not limiter.allow_request():
                                time.sleep(1)

                        futures.append(executor.submit(links_checker.check_dead,url,source,limiter))

                for j in range(len(batch)):
                    result_row = batch.iloc[j].copy()
                    try:
                        result_row[RESULT_COL] = futures[j].result()
                    except Exception as e:
                        progress.console.print(f"[{ERR_BAR}] ERR [/] [{MUTED}]row {row_track + j:,} → {e}[/]")
                        result_row[RESULT_COL] = None
                    rows.append(result_row)

                batch_result = pd.DataFrame(rows)
                batch_dead = int((batch_result[RESULT_COL] == True).sum())
                dead_count += batch_dead

                progress.console.print(f"[{OK_BAR}] OK [/] [{MUTED}]rows {row_track:,} – {row_track + len(batch):,} → {batch_dead}/{len(batch)} dead[/]")
                row_track += len(batch)

                if os.path.exists(output_path):
                    write_header = False
                else:
                    write_header = True

                batch_result.to_csv(output_path, mode='a', header=write_header, index=False)
                progress.update(task1, advance=1)

            id = str(uuid.uuid4())
            progress.stop()
            time_elapsed = time.time() - time_start

        console.print(run_summary_panel(id, start_row, row_track, dead_count, time_elapsed, output_path))

        run_again = questionary.confirm("Repeat processing?", style=PROMPT_STYLE).ask()

        if not run_again:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False
