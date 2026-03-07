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

# resource path
def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_dotenv(resource_path(".env"))

import sqlite3

connector = sqlite3.connect("analysis_log.db")
cursor = connector.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS analysis_log (
        ID TEXT,
        START_ROW INTEGER,
        END_ROW INTEGER,
        TIME_ELAPSED REAL
    )
""")
connector.commit()

def analysis_page_panel() -> Panel:
    return Panel("",title="[blue]Analysis Page")

def args_table() -> Table:
    table = Table(title = "[blue]Argruments Needed",border_style="bright_cyan")
    table.add_column("[red]Args",no_wrap=True)
    table.add_column("[red]Description",no_wrap=True)
    table.add_column("[red]Required",no_wrap=True)

    table.add_row("input path","input csv File path","True")
    table.add_row("output path","output csv path","True")
    table.add_row("batch size","num rows to process per batch","False")
    table.add_row("start row","row start number","False")
    table.add_row("end row","row num where processing ends","False")
    table.add_row("pdf url coloumn","col name to extract","False")
    return table


def processing_end_panel() -> Panel:
    return Panel("",title="[bold blue] End of processing")


def show():
   console = Console()
   status = True
   load_dotenv(resource_path(".env"))
   console.print(analysis_page_panel())
   console.print(args_table())

   while status:
    input_path = questionary.path("Input CSV file:").ask()
    input_path = input_path.strip("'\"")

    output_path = questionary.path("Output CSV file:").ask()
    batch_size = questionary.text("Batch Size").ask()
    start_row = questionary.text("Start row").ask()
    end_row = questionary.text("End row").ask()
    col_name = questionary.text("Col name").ask()

    output_path = os.path.join(os.path.dirname(input_path), output_path)

    if start_row is None:
        start_row = 0

    if end_row is None:
        end_row = len(df)

    if batch_size is None:
        batch_size = 5

    if col_name is None:
        col_name = "pdf_url"

    start_row = int(start_row)
    end_row = int(end_row)
    batch_size = int(batch_size)

    df = report_analysis.load_csv(input_path)

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

    df_subset = df.iloc[start_row:end_row]
    total_batches = (len(df_subset) + batch_size - 1) // batch_size

    console.print("[bold red]processing Data")

    with Progress() as progress:
        time_start = time.time()
        task1 = progress.add_task("[red]Analysing data", total=total_batches)

        for i in range(0, len(df_subset), batch_size):
            batch_num = i // batch_size + 1
            console.print(f"[bold blue] Batch num: {batch_num}")
            batch = df_subset.iloc[i:i + batch_size]
            batch_result = report_analysis.batch_processing(batch, col_name, deep_key, gemini_key, gpt_key)
            batch_result.to_csv(output_path, mode='a', header=(i == 0))
            progress.update(task1, advance=1)

            id = str(uuid.uuid4())
            

        progress.stop()
        time_elapsed = time.time() - time_start
        data_to_log = [id, start_row, end_row, time_elapsed]
        cursor.execute("INSERT INTO analysis_log VALUES(?, ?, ?, ?)", data_to_log)
        connector.commit()
        status_update = questionary.confirm("Exit? [Y/N]").ask()

        if status_update:
            status = False

    connector.close()
   
if __name__ == "__main__":
    show()





























