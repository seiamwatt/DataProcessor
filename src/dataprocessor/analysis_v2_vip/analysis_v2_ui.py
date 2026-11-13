import os
import sys
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import questionary
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.padding import Padding
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
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from dataprocessor.config import load_env
from dataprocessor.analysis_v2_vip import analysis_v2 as analysis
from dataprocessor.analysis_v2_section import prompts
from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, ERR_BAR, EMPHASIS, MUTED, OK,
    GREEN, RED, BLUE, PURPLE, PROMPT_STYLE,
)

console = Console()
os.environ["TERM"] = "xterm-256color"
load_env()

# output = url, file, status columns, then analysis columns; match back to the main dataset on url or file
ID_COLUMNS = ["url", "file"]
STATUS_COLUMNS = ["Coding_status", "Review_scope", "Coding_notes"]
MODEL_PREFIXES = {"gemini": "gm", "gpt": "gpt"}
MODEL_NAMES = {"gemini": analysis.GEMINI_MODEL, "gpt": analysis.GPT_MODEL}
MODEL_KEYS = {"gemini": "Gemini_key", "gpt": "GPT_key"}
OUTPUT_COLUMNS = ID_COLUMNS + STATUS_COLUMNS + [
    f"{prefix}_{col}" for prefix in MODEL_PREFIXES.values() for col in prompts.ANALYSIS_COLUMNS
]
DEFAULT_WORKERS = int(os.getenv("VIP_WORKERS", 60))

# folder input gets these columns instead of the csv's
FILE_COL = "file_name"
PATH_COL = "file_path"


def analysis_section_panel() -> Panel:
    body = Group(
        Align.center(Text("ANALYSIS SECTION V2 VIP", style=f"bold {ACCENT_BAR}")),
        Align.center(Text("Direct API analysis pipeline", style=MUTED)),
        Align.center(Text(datetime.now().strftime("Session started %Y-%m-%d %H:%M"), style=MUTED)),
    )
    return Panel(Padding(body, (1, 4)), box=box.SQUARE, border_style=ACCENT)


def args_table() -> Table:
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

    required = Text("Yes", style=f"bold {RED}")
    optional = Text("No", style=GREEN)
    table.add_row("Input path", "CSV file of PDF URLs, or a folder of PDF files", required, "—")
    table.add_row("Output path", "Destination CSV file name or folder", required, "—")
    table.add_row("Start row", "First row / file to process", optional, "0")
    table.add_row("End row", "Row / file at which processing stops", optional, "End of input")
    table.add_row("Column name", "CSV column containing the PDF URL", optional, "url")
    table.add_row("Models", "LLMs to run on each report", optional, "gpt")
    table.add_row("Workers", "Reports processed at once", optional, str(DEFAULT_WORKERS))
    return table


def run_summary_panel(id, start_row, end_row, time_elapsed, output_path, ok_count, total) -> Panel:
    mins, secs = divmod(int(time_elapsed), 60)
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Run ID", Text(id, style=PURPLE))
    table.add_row("Rows processed", Text(f"{start_row:,} – {end_row:,}", style=PURPLE))
    table.add_row("Completed", Text(f"{ok_count:,} / {total:,}", style=PURPLE))
    table.add_row("Elapsed time", Text(f"{mins}m {secs}s", style=BLUE))
    table.add_row("Output file", Text(output_path, style=GREEN))
    return Panel(table, title=Text(" Run complete ", style=f"bold {OK_BAR}"),
                 title_align="left", border_style=OK, box=box.SQUARE)


# dragged paths come in quoted or with escaped spaces
def clean_path(path):
    return path.strip().strip("'\"").replace("\\ ", " ")


def load_pdf_folder(folder):
    names = sorted(
        name for name in os.listdir(folder)
        if name.lower().endswith(".pdf") and not name.startswith(".")
    )
    return pd.DataFrame({
        FILE_COL: names,
        PATH_COL: [os.path.join(folder, name) for name in names],
    })


# document and coding status fields for one report
def status_record(has_text, model_results, models):
    # no text means no model was called, so nothing counts as failed
    failed = [model for model in models if has_text and model_results.get(model) is None]

    if not has_text:
        status = "Pending"
        scope = "No text extracted"
    elif failed:
        status = "Awaiting review"
        scope = "Full extracted PDF text"
    else:
        status = "Completed"
        scope = "Full extracted PDF text"

    notes = f"LLM coders: {', '.join(f'{m} {MODEL_NAMES[m]}' for m in models)}; {datetime.now():%Y-%m-%d}; protocol {prompts.PROTOCOL_VERSION}"
    if failed:
        notes += f"; failed: {', '.join(failed)}"

    return {
        "Coding_status": status,
        "Review_scope": scope,
        "Coding_notes": notes,
    }


# folder input: the pdf's name; csv input: the last part of the url
def file_name(row, col_name, is_folder):
    if is_folder:
        return row[FILE_COL]
    return os.path.basename(str(row[col_name]).split("?")[0].rstrip("/"))


ANALYZE = {"gpt": analysis.gpt_analyze, "gemini": analysis.gemini_analyze}


# one report end to end: extract text, call each model, build the output row
# never raises, so one bad report cannot stop the run
def process_row(source, file_name, is_file, clients, progress):
    try:
        if is_file:
            text = analysis.extract_pdf_text_file(source)
        else:
            text = analysis.extract_pdf_text_url(source)
        prompt = analysis.build_prompt(text)
    except Exception as e:
        progress.console.print(f"[{ERR_BAR}] ERR [/] [{RED}]extract failed:[/] [{MUTED}]{source}: {e}[/]")
        prompt = None

    model_results = {}
    if prompt is not None:
        for model, client in clients.items():
            try:
                model_results[model] = ANALYZE[model](client, prompt)
            except Exception as e:
                progress.console.print(f"[{ERR_BAR}] ERR [/] [{RED}]{model} failed:[/] [{MUTED}]{source}: {e}[/]")
                model_results[model] = None

    result_row = {"url": "" if is_file else source, "file": file_name}
    result_row.update(status_record(prompt is not None, model_results, list(clients)))

    for model, prefix in MODEL_PREFIXES.items():
        result = model_results.get(model)
        if not isinstance(result, dict):
            result = {}
        for col in prompts.ANALYSIS_COLUMNS:
            result_row[f"{prefix}_{col}"] = result.get(col)

    return result_row


def ask_config():
    input_path = clean_path(questionary.path("Input CSV file or PDF folder:", style=PROMPT_STYLE).ask())

    is_folder = os.path.isdir(input_path)
    if is_folder:
        df = load_pdf_folder(input_path)
        if df.empty:
            raise ValueError("no pdf files in folder")
        console.print(f"[{MUTED}]Found {len(df):,} PDF files.[/]")
        col_name = PATH_COL
        base_dir = input_path
        input_name = os.path.basename(os.path.normpath(input_path))
    else:
        df = analysis.load_csv(input_path)
        if df is None:
            raise ValueError("could not read csv")
        col_name = None
        base_dir = os.path.dirname(input_path)
        input_name = os.path.splitext(os.path.basename(input_path))[0]

    output_path = questionary.path("Output CSV file or folder:", default=f"{input_name}_analysis.csv", style=PROMPT_STYLE).ask()
    start_row = int(questionary.text("Start row", default="0", style=PROMPT_STYLE).ask())
    end_row = int(questionary.text("End row", default=str(len(df)), style=PROMPT_STYLE).ask())
    if col_name is None:
        col_name = questionary.text("Column name", default="url", style=PROMPT_STYLE).ask()
        if col_name not in df.columns:
            raise ValueError(f"column {col_name} not in csv")

    models = questionary.checkbox(
        "Models",
        choices=[questionary.Choice("gpt", checked=True), questionary.Choice("gemini", checked=False)],
        style=PROMPT_STYLE,
    ).ask()
    if not models:
        raise ValueError("no model selected")
    workers = int(questionary.text("Workers", default=str(DEFAULT_WORKERS), style=PROMPT_STYLE).ask())
    if workers < 1:
        raise ValueError("workers must be at least 1")

    output_path = os.path.join(base_dir, clean_path(output_path))
    # dragged in a folder, name the file after the input
    if os.path.isdir(output_path):
        output_path = os.path.join(output_path, f"{input_name}_analysis.csv")

    return df, is_folder, output_path, start_row, end_row, col_name, models, workers


def show():
    status = True
    load_env()

    console.clear()
    console.print(analysis_section_panel())
    console.print(args_table())
    console.print()

    while status:
        console.print(Rule(Text(" Configuration ", style=ACCENT_BAR), style=ACCENT))

        while True:
            try:
                df, is_folder, output_path, start_row, end_row, col_name, models, workers = ask_config()
                break
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input ({e}).[/] [{MUTED}]Check the path, column name and numeric fields, then try again.[/]")

        missing = [MODEL_KEYS[m] for m in models if not os.getenv(MODEL_KEYS[m])]
        if missing:
            console.print(f"[{ERR_BAR}] ERR [/] [{RED}]API key not found: {', '.join(missing)}.[/] [{MUTED}]Add it in API Selector or the .env file.[/]")
            continue

        make_client = {"gpt": analysis.gpt_client, "gemini": analysis.gemini_client}
        clients = {m: make_client[m](os.getenv(MODEL_KEYS[m])) for m in models}

        df_subset = df.iloc[start_row:end_row]
        total = len(df_subset)

        console.print(Rule(Text(" Processing ", style=ACCENT_BAR), style=ACCENT))

        with Progress(
            SpinnerColumn(style=ACCENT),
            TextColumn(f"[bold {ACCENT}]{{task.description}}"),
            BarColumn(bar_width=None, style="grey23", complete_style=OK,
                      finished_style=OK, pulse_style=ACCENT),
            MofNCompleteColumn(),
            TextColumn(f"[{MUTED}]reports"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            time_start = time.time()
            task = progress.add_task("Analysing reports", total=total)
            ok_count = 0

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(process_row, row[col_name], file_name(row, col_name, is_folder), is_folder, clients, progress)
                    for _, row in df_subset.iterrows()
                ]

                # write in input order; each row is appended as soon as it and all rows before it are done
                for future in futures:
                    result_row = future.result()
                    if result_row["Coding_status"] == "Completed":
                        ok_count += 1
                    write_header = not os.path.exists(output_path)
                    pd.DataFrame([result_row], columns=OUTPUT_COLUMNS).to_csv(
                        output_path, mode="a", header=write_header, index=False
                    )
                    progress.update(task, advance=1)

            progress.stop()
            time_elapsed = time.time() - time_start

        console.print(run_summary_panel(str(uuid.uuid4()), start_row, end_row, time_elapsed, output_path, ok_count, total))

        if not questionary.confirm("Repeat processing?", style=PROMPT_STYLE).ask():
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False
