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

import report_filter
console = Console()

def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_dotenv(resource_path(".env"))


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

def show():
    load_dotenv(resource_path(".env"))
    console.print("Filter Data")
    console.print(filter_page_panel())
    console.print(args_table())

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


    df = report_filter.load_csv(input_path)

    api_key = os.getenv("DeepSeek_key")
    if api_key is None:
        console.print("[bold red]No API key")
        return


    df_subset = df.iloc[start_row:end_row]
    all_results = []
    total_batches = (len(df_subset) + batch_size - 1) // batch_size
    console.print("[bold red]processing Data")
    with Progress() as progress:
        task1 = progress.add_task("[red]Filtering Data",total=total_batches)
        for i in range(0,len(df_subset),batch_size):

            batch_num = i // batch_size + 1
            console.print(f"[bold blue] Batch num: {batch_num}")
            batch = df_subset.iloc[i:i+batch_size]
            batch_result = report_filter.batch_processing(df_batch=batch,api_key= api_key,pdf_url_column=col_name,extract_text=True)
            all_results.append(batch_result)

            
            progress.update(task1,advance=1)
     
        progress.stop()
    
    
    if all_results:
        console.print(processing_end_panel())
        final_df = pd.concat(all_results,ignore_index=True)
        final_df.to_csv(output_path)

if __name__ == "__main__":
    show()