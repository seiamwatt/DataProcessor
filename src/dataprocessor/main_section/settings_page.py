
import time
import random
import platform
from datetime import datetime, timedelta
from collections import deque
import questionary

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
from dataprocessor.main_section import main_util
import sys
from rich.columns import Columns
# THEME ---------------------------------------------------------------------------
# One shared htop palette for every screen. Edit dataprocessor/theme.py to
# retheme the whole application; nothing here defines its own colors.
from dataprocessor.theme import (
    ACCENT, ACCENT_DIM, ACCENT_BAR, OK_BAR, PURPLE_BAR, GREEN_BAR, ERR_BAR, EMPHASIS, MUTED, OK, ERR,
    CYAN, GREEN, RED, BLUE, PURPLE, chip, section,
)
# ---------------------------------------------------------------------------------
# SETUP ---------------------------------------------------------------------------
version = os.getenv("version")
deep_key = os.getenv("DeepSeek_key")
gemini_key = os.getenv("Gemini_key")
gpt_key = os.getenv("GPT_key")
# ---------------------------------------------------------------------------------

def key_table() -> Table:

    table = Table(title="", box=None, pad_edge=False, expand=True,
                  header_style=f"bold {PURPLE_BAR}")

    table.add_column("LLM", style=EMPHASIS)
    table.add_column("VALUE", style=PURPLE)
    table.add_row("DeepSeek",deep_key)
    table.add_row("GPT",gpt_key)
    table.add_row("Gemini",gemini_key)
    return table

def key_panel() -> Panel:
    return Panel(key_table(), title=chip("Keys", PURPLE_BAR), title_align="left",
                 border_style=ACCENT_DIM, box=box.SQUARE)

def status_panel() -> Panel:
    deep_status = main_util.DeepSeek_connect_test()
    gpt_status = main_util.GTP_connect_test()
    gemini_status = main_util.Gemini_connect_test()

    online = f"[{OK_BAR}] ONLINE [/]"
    offline = f"[{ERR_BAR}] OFFLINE [/]"

    table = Table(box=None, pad_edge=False, expand=True, show_header=True,
                  header_style=f"bold {GREEN_BAR}")
    table.add_column("LLM", ratio=1, style=EMPHASIS)
    table.add_column("STATUS", ratio=1)

    table.add_row("DeepSeek", online if deep_status else offline)
    table.add_row("GPT", online if gpt_status else offline)
    table.add_row("Gemini", online if gemini_status else offline)

    return Panel(table, title=chip("LLM Status", GREEN_BAR), title_align="left",
                 border_style=ACCENT_DIM, box=box.SQUARE)

def filter_overview_panel() -> Panel:
    overview = (
        "1. Load a CSV file containing PDF URLs\n\n"
        "2. For each row, download the PDF and attempt to extract its text\n"
        "   — if the PDF is image-based, [bold]OCR[/bold] is applied first\n\n"
        "3. The extracted text is sent to the [bold]DeepSeek AI API[/bold]\n"
        "   along with the URL to classify whether it's an annual report\n\n"
        "4. Each result is tagged with:\n"
        "   — [bold]is_annual_report[/bold] (true/false)\n"
        "   — [bold]confidence[/bold] level (high/medium/low)\n"
        "   — [bold]reason[/bold] for the classification\n\n"
        "5. Results are saved progressively to an output CSV in batches"
    )
    return Panel(overview, title=chip("Filter Process Overview", PURPLE_BAR), title_align="left",
                 border_style=ACCENT_DIM, box=box.SQUARE)

def analysis_overview_panel() -> Panel:
    overview = (
        "1. Load a CSV file containing PDF URLs\n\n"
        "2. For each row, download the PDF and extract its text\n"
        "   — if the PDF is image-based, [bold]OCR[/bold] is applied first\n\n"
        "3. Each PDF is analyzed in 4 chained steps across [bold]DeepSeek, GPT, and Gemini[/bold]:\n"
        "   — [bold]Y1[/bold] — identify the main issue (shared across all models)\n"
        "   — [bold]Y2[/bold] — rate the organization's position strength (0–100)\n"
        "   — [bold]Y3[/bold] — extract supporting quotes from the document\n"
        "   — [bold]Y4[/bold] — classify the political/cultural orientation (1–5)\n\n"
        "4. Each model produces its own independent result row tagged with its [bold]LLM name[/bold]\n"
        "   and a shared [bold]UUID[/bold] to link the 3 results back to the same source row\n\n"
        "5. Results are saved progressively to an output CSV in batches"
    )
    return Panel(overview, title=chip("Analysis Process Overview", PURPLE_BAR), title_align="left",
                 border_style=ACCENT_DIM, box=box.SQUARE)



def show():
    console = Console()

    # ── Header ──────────────────────────────────────────────────────
    console.print()
    console.print(section("DataProcessor"))
    console.print()

    # ── Main content: overviews left, status right ───────────────────
    overviews = Table.grid(padding=(0, 0))
    overviews.add_column()
    overviews.add_row(filter_overview_panel())
    overviews.add_row(Padding("", (0, 0)))  # small gap
    overviews.add_row(analysis_overview_panel())

    status = Table.grid(padding=(1, 0))
    status.add_column()
    status.add_row(status_panel())
    status.add_row(key_panel())

    console.print(Columns([overviews, status], padding=(0, 2)))

    # ── Footer ───────────────────────────────────────────────────────
    console.print()
    console.print(Rule(f"[{MUTED}]Version {version}[/]", style=MUTED))
    console.print()
if __name__ == "__main__":
    show()