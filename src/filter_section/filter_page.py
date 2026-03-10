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

import report_filter
console = Console()
os.environ["TERM"] = "xterm-256color"

def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_dotenv(resource_path(".env"))

# sqllite DB connect
import sqlite3

connector = sqlite3.connect("filter_log.db")
cursor = connector.cursor()


cursor.execute("""
    CREATE TABLE IF NOT EXISTS filter_log (
        ID TEXT,
        START_ROW INTEGER,
        END_ROW INTEGER,
        TIME_ELAPSED REAL
    )
""")
connector.commit()

def filter_page_panel():
    return Panel("",title="[blue]Data Filter")

def args_table():
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

def processing_end_panel():
    return Panel("",title="[bold blue] End of processing")

def default_value_table() -> Table:
    table = Table(title="[blue]Default Values",border_style="bright_cyan")
    table.add_column("[red]Args",no_wrap=True)
    table.add_column("[red]Default Value",no_wrap=True)

    table.add_row("Start row", "0")
    table.add_row("End row", "df len")
    table.add_row("Col name","pdf_url")
    return table

def show():
    status = True


    load_dotenv(resource_path(".env"))
    console.print("Filter Data")
    console.print(filter_page_panel())
    console.print(Columns([args_table(),default_value_table()]))



    # console.print(args_table())
    # console.print(default_value_table())

    while(status):
        
        input_path = questionary.path("Input CSV file:").ask()
        input_path = input_path.strip("'\"")
        df = report_filter.load_csv(input_path)
        default_end_row = len(df)

        output_path = questionary.path("Output CSV file:").ask()
        batch_size = questionary.text("Batch Size").ask()
        start_row = questionary.text("Start row",default=str(0)).ask()
        end_row = questionary.text("End row",default=str(default_end_row)).ask()
        col_name = questionary.text("Col name",default="pdf_url").ask()
        output_path = os.path.join(os.path.dirname(input_path), output_path)

        start_row = int(start_row)
        end_row = int(end_row)
        batch_size = int(batch_size)

        api_key = os.getenv("DeepSeek_key")
        if api_key is None:
            console.print("[bold red]No API key")
            return


        df_subset = df.iloc[start_row:end_row]
        all_results = []
        total_batches = (len(df_subset) + batch_size - 1) // batch_size
        console.print("[bold red]processing Data")

        # -------
        with Progress() as progress:
            time_start = time.time()
            task1 = progress.add_task("[red]Filtering Data", total=total_batches)

            for i in range(0,len(df_subset),batch_size):
                batch_num = i // batch_size + 1
                console.print(f"[bold blue] Batch num: {batch_num}")
                batch = df_subset.iloc[i:i+batch_size]
                batch_result = report_filter.batch_processing(df_batch=batch,api_key= api_key,pdf_url_column=col_name,extract_text=True)
                batch_result.to_csv(output_path, mode='a', header=(i == 0))
                progress.update(task1,advance=1)

            
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