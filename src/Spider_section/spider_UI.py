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


from Spider_section import spider

console = Console()
os.environ["TERM"] = "xterm-256color"

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_dotenv(resource_path(".env"))


def banner_panel() -> Panel:

    art = ""
    return Panel(art,highlight=True)

def args_table() -> Table:

    table = Table(title="[blue]Arguments Needed", border_style="bright_cyan")
    table.add_column("[red]Args", no_wrap=True)
    table.add_column("[red]Description", no_wrap=True)
    table.add_column("[red]Required", no_wrap=True)

    return table



def show():
    pass





if __name__ == "__main__":
    show()