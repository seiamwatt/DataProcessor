from datetime import datetime
import questionary
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich import box
from rich.rule import Rule
from rich.padding import Padding
import os
import re

from dataprocessor.summary_section import summary

from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, OK_BAR, ERR_BAR, EMPHASIS, MUTED, OK,
    GREEN, RED, BLUE, PURPLE, PROMPT_STYLE,
)
console = Console()

# dragged paths come in quoted or with escaped spaces
def clean_path(path):
    path = os.path.expanduser(path.strip().strip("'\""))
    if not os.path.exists(path):
        path = re.sub(r"\\(.)",r"\1",path)
    return path

def error(msg):
    console.print(f"[{ERR_BAR}] ERR [/] [{RED}]{msg}[/]")

def summary_section_panel() -> Panel:
    title = Text("Summary",style = f"bold {ACCENT_BAR}")
    subtitle = Text("Org by year report matrix", style= MUTED)
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

    table.add_row("Input path", "Dated report CSV, needs 'org', 'domain' and 'year' columns", required, "—")
    table.add_row("Output name", "File name, saved next to the input, plus a <name>_stats.csv", optional, "<input>_matrix.csv")

    return table

def run_summary_panel(stats, output_path, rows) -> Panel:
    """Final report card for the completed run."""
    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column(style=ACCENT, no_wrap=True)
    table.add_column(style=EMPHASIS)
    table.add_row("Unique years", Text(f"{stats['unique_years']:,}", style=PURPLE))
    table.add_row("Longest streak", Text(f"{stats['longest_run']:,} consecutive years", style=PURPLE))
    table.add_row("Annual reports", Text(f"{stats['reports']:,} ({stats['duplicates']:,} duplicate org-year rows merged)", style=PURPLE))
    table.add_row("Orgs", Text(f"{stats['orgs']:,}", style=PURPLE))
    table.add_row("No domain", Text(f"{stats['no_domain']:,} orgs", style=MUTED))
    table.add_row("Years", Text(f"{stats['first_year']} – {stats['last_year']} ({stats['year_columns']} columns)", style=BLUE))
    table.add_row("Rows dropped", Text(f"{stats['dropped_undated']:,} undated, {stats['dropped_out_of_range']:,} outside {summary.MIN_YEAR}-{summary.MAX_YEAR}", style=RED))
    table.add_row("Output file", Text(f"{output_path} ({rows:,} rows)", style=GREEN))
    table.add_row("Stats file", Text(summary.stats_path(output_path), style=GREEN))
    return Panel(table, title=Text(" Run complete ", style=f"bold {OK_BAR}"),
                 title_align="left", border_style=OK, box=box.SQUARE)

def show():
    status = True
    console.clear()
    console.print(summary_section_panel())
    console.print(args_table())
    console.print()

    while(status):
        input_status = True
        console.print(Rule(Text(" Configuration ", style=ACCENT_BAR), style=ACCENT))

        while input_status:
            try:
                input_path = questionary.path("Input CSV file:", style=PROMPT_STYLE).ask()
                input_path = clean_path(input_path)
                df = summary.load_csv(input_path)
                if df is None or df.empty:
                    error("Could not read the CSV or it has no rows.")
                    continue

                year_col = summary.find_year_col(df)
                missing_cols = [col for col in summary.REQUIRED_COLS if col not in df.columns]
                if year_col is None:
                    missing_cols.append("year")
                if missing_cols:
                    error(f"Missing column(s): {', '.join(missing_cols)}. Found: {', '.join(map(str, df.columns))}")
                    continue

                input_name = os.path.splitext(os.path.basename(input_path))[0]
                output_name = questionary.text("Output CSV name", default=f"{input_name}_matrix.csv", style=PROMPT_STYLE).ask()

                # only the file name is used, output always goes next to the input
                output_name = os.path.basename(clean_path(output_name))
                if not output_name.lower().endswith(".csv"):
                    output_name += ".csv"
                output_path = os.path.join(os.path.dirname(os.path.abspath(input_path)), output_name)

                if os.path.abspath(output_path) == os.path.abspath(input_path):
                    error("That is the input file. Pick another name.")
                    continue
                if os.path.exists(output_path):
                    overwrite = questionary.confirm(f"{output_name} already exists. Overwrite it?", default=False, style=PROMPT_STYLE).ask()
                    if not overwrite:
                        continue

                input_status = False
            except Exception as e:
                console.print(f"[{ERR_BAR}] ERR [/] [{RED}]Invalid input.[/] [{MUTED}]Check the file path, then try again.[/]")

        console.print(Rule(Text(" Processing ", style=ACCENT_BAR), style=ACCENT))

        try:
            out, stats_df, stats = summary.build_matrix(df, year_col)
            out.to_csv(output_path, index=False)
            stats_df.to_csv(summary.stats_path(output_path), index=False)
            console.print(run_summary_panel(stats, output_path, len(out)))
        except ValueError as e:
            error(str(e))

        run_again = questionary.confirm("Repeat processing?", style=PROMPT_STYLE).ask()

        if not run_again:
            console.print(f"[{MUTED}]Session ended.[/]")
            status = False
