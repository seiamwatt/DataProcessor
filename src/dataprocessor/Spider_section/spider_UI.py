"""Spider — Nonprofit Report Crawler
Terminal UI (rich + questionary)

Interactive:      python spider_ui.py
Non-interactive:  python spider_ui.py --csv orgs.csv --out ./reports --sources wayback,live
"""

import argparse
import os
import sys
import time
from datetime import timedelta

import questionary
from questionary import Style, Validator, ValidationError
from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from dotenv import load_dotenv
from dataprocessor.config import load_env

from dataprocessor.Spider_section import spider

console = Console()

# ---------------------------------------------------------------- theme ---

ACCENT = "dark_orange"          # spider orange — the one loud color
ACCENT_DIM = "dark_orange3"
INK = "grey85"
MUTED = "grey50"
DANGER = "red3"
WARN = "gold3"

Q_STYLE = Style(
    [
        ("qmark", "fg:#ff8700 bold"),
        ("question", "bold"),
        ("answer", "fg:#ff8700 bold"),
        ("pointer", "fg:#ff8700 bold"),
        ("highlighted", "fg:#ff8700"),
        ("selected", "fg:#ff8700"),
        ("instruction", "fg:#666666"),
    ]
)

SOURCES = {
    "wayback": ("Archived report PDFs", "Internet Archive \u2014 backfills what live misses"),
    "live": ("Current report PDFs", "BFS crawl of the live site (runs first)"),
}

# What every interactive crawl runs. These two are complementary, not
# alternatives: live sweeps the current site and wayback backfills the reports
# it no longer serves.
#
DEFAULT_SOURCES = tuple(SOURCES)

# ------------------------------------------------------------- utilities ---


def resource_path(relative_path: str) -> str:
    """Path for bundled files (dev and PyInstaller)."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def clean_path(raw: str) -> str:
    """Normalize typed or drag-and-dropped paths (quotes, escaped spaces, ~)."""
    if not raw:
        return ""
    p = raw.strip().strip("'\"").strip()
    p = p.replace("\\ ", " ")
    return os.path.expanduser(p)


def fmt_duration(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))


# ------------------------------------------------------------ validators ---


class CsvFileValidator(Validator):
    """Path must exist, be a file, and look like a CSV."""

    def validate(self, document):
        p = clean_path(document.text)
        if not p:
            raise ValidationError(message="Enter a path, or drag a file into the terminal")
        if not os.path.exists(p):
            raise ValidationError(message=f"No such file: {p}")
        if not os.path.isfile(p):
            raise ValidationError(message="The path is a folder. Provide the CSV file itself")
        if not p.lower().endswith((".csv", ".tsv")):
            raise ValidationError(message="Expected a .csv file")


class IntValidator(Validator):
    """Integer within optional bounds."""

    def __init__(self, lo=None, hi=None):
        self.lo, self.hi = lo, hi

    def validate(self, document):
        try:
            v = int(document.text)
        except ValueError:
            raise ValidationError(message="Enter a whole number")
        if self.lo is not None and v < self.lo:
            raise ValidationError(message=f"Must be at least {self.lo}")
        if self.hi is not None and v > self.hi:
            raise ValidationError(message=f"Must not exceed {self.hi}")


# ----------------------------------------------------------------- views ---


def banner() -> Panel:
    art = Text(
        "\n"
        " ██████ █████  ██████ █████  ██████ █████ \n"
        " ███    ██  ██   ██   ██  ██ ██     ██  ██\n"
        "    ███ █████    ██   ██  ██ ████   █████ \n"
        " ██████ ██     ██████ █████  ██████ ██  ██\n",
        style=f"bold {ACCENT}",
    )
    sub = Text("Nonprofit report crawler", style=MUTED, justify="center")
    return Panel(
        Group(Align.center(art), Align.center(sub)),
        box=box.HEAVY,
        border_style=ACCENT_DIM,
        subtitle=f"[{MUTED}]Spider[/]",
        padding=(0, 2),
    )


def sources_table() -> Table:
    t = Table(box=box.SIMPLE_HEAD, border_style=ACCENT_DIM,
              title="Sources (live + wayback run every crawl)",
              title_style=f"bold {INK}", expand=True)
    t.add_column("ID", style=f"bold {ACCENT}", no_wrap=True)
    t.add_column("Retrieves", style=INK)
    t.add_column("Source", style=MUTED)
    for sid, (what, where) in SOURCES.items():
        t.add_row(sid, what, where)
    return t


def inputs_table() -> Table:
    t = Table(box=box.SIMPLE_HEAD, border_style=ACCENT_DIM, title="Required inputs", title_style=f"bold {INK}", expand=True)
    t.add_column("Input", style=f"bold {INK}", no_wrap=True)
    t.add_column("Notes", style=MUTED)
    t.add_row("Org CSV", "Columns: name, domain, ein")
    t.add_row("Output folder", "Destination for PDFs and one manifest per source")
    t.add_row("Years / depth", "Defaults: 20 years, depth 3")
    t.add_row("Row range", "Subset of CSV rows to process")
    return t


def intro():
    console.print(banner())
    console.print(
        Panel(
            Columns([sources_table(), inputs_table()], equal=True, expand=True),
            box=box.ROUNDED,
            border_style=ACCENT_DIM,
            padding=(1, 2),
        )
    )


def config_panel(cfg: dict, n_orgs: int) -> Panel:
    t = Table(box=None, show_header=False, pad_edge=False)
    t.add_column(style=MUTED, justify="right", no_wrap=True)
    t.add_column(style=INK)

    src_badges = Text()
    for i, s in enumerate(cfg["sources"]):
        if i:
            src_badges.append("  ")
        src_badges.append(f" {s} ", style=f"black on {ACCENT}")

    n_selected = cfg["end_row"] - cfg["start_row"]
    t.add_row("Org CSV", cfg["org_csv_path"])
    t.add_row("Organizations", f"{n_selected} of {n_orgs}  (rows {cfg['start_row']}\u2013{cfg['end_row']})")
    t.add_row("Output", os.path.abspath(cfg["out_dir"]))
    t.add_row("Sources", src_badges)
    t.add_row("Lookback", f"{cfg['years']} years")
    t.add_row("Crawl depth", str(cfg["depth"]))

    return Panel(
        t,
        title=f"[bold {INK}]Run configuration[/]",
        border_style=ACCENT_DIM,
        box=box.ROUNDED,
        padding=(1, 2),
    )


def running_panel(cfg: dict, started: float) -> Panel:
    n = cfg["end_row"] - cfg["start_row"]
    body = Table(box=None, show_header=False, pad_edge=False)
    body.add_column(style=MUTED, justify="right", no_wrap=True)
    body.add_column(style=INK)
    body.add_row("Status", Text("Crawling in progress", style=f"bold {ACCENT}"))
    body.add_row("Organizations", f"{n}  (rows {cfg['start_row']}\u2013{cfg['end_row']})")
    body.add_row("Sources", " \u00b7 ".join(cfg["sources"]))
    body.add_row("Elapsed", fmt_duration(time.monotonic() - started))
    return Panel(body, border_style=ACCENT, box=box.ROUNDED, title=f"[bold {ACCENT}]Spider running[/]", padding=(1, 2))


def results_panel(manifest, cfg: dict, elapsed: float) -> Panel:
    if manifest is None or len(manifest) == 0:
        body = Group(
            Text("No documents were found", style=f"bold {WARN}"),
            Text("Consider widening the year lookback or adding sources.", style=MUTED),
        )
        return Panel(body, border_style=WARN, box=box.ROUNDED, title=f"[bold {WARN}]Finished \u2014 no results[/]", padding=(1, 2))

    lines = Table(box=None, show_header=False, pad_edge=False)
    lines.add_column(style=MUTED, justify="right", no_wrap=True)
    lines.add_column(style=INK)
    lines.add_row("Documents", Text(str(len(manifest)), style=f"bold {ACCENT}"))
    lines.add_row("Elapsed", fmt_duration(elapsed))
    for src in cfg["sources"]:
        path = os.path.join(os.path.abspath(cfg["out_dir"]), spider.manifest_name(src))
        lines.add_row(f"Manifest ({src})", path if os.path.exists(path) else "\u2014 nothing collected")

    # Per-source breakdown if the manifest exposes it
    try:
        counts = manifest["source"].value_counts()
        breakdown = Text()
        for i, (src, cnt) in enumerate(counts.items()):
            if i:
                breakdown.append("   ")
            breakdown.append(f"{src} ", style=f"bold {ACCENT}")
            breakdown.append(str(cnt), style=INK)
        lines.add_row("By source", breakdown)
    except Exception:
        pass

    return Panel(lines, border_style=ACCENT, box=box.ROUNDED, title=f"[bold {ACCENT}]Finished[/]", padding=(1, 2))


# ---------------------------------------------------------------- wizard ---


def ask_config() -> dict | None:
    """Interactive wizard. Returns config dict, or None if cancelled."""

    # Org CSV — validated inline, no retry loop needed
    raw = questionary.path(
        "Org list CSV (or drag a file in):",
        validate=CsvFileValidator(),
        style=Q_STYLE,
    ).ask()
    if raw is None:
        return None
    org_csv_path = clean_path(raw)

    try:
        orgs_df = spider.load_csv(org_csv_path)
    except Exception as e:
        console.print(f"[bold {DANGER}]Unable to read the CSV file:[/] {e!r}")
        return None
    if len(orgs_df) == 0:
        console.print(f"[bold {WARN}]The CSV file contains no rows.[/]")
        return None
    console.print(f"  [{MUTED}]Loaded [bold]{len(orgs_df)}[/] organizations.[/]\n")

    # Output folder
    out_raw = questionary.path("Output folder for PDFs and manifest:", default="./reports", style=Q_STYLE).ask()
    if out_raw is None:
        return None
    out_dir = clean_path(out_raw) or "./reports"
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        console.print(f"[bold {DANGER}]{out_dir} exists and is not a folder.[/]")
        return None

    # Numbers — each validated inline, bounds-checked against the CSV
    years = questionary.text("Years of lookback:", default="20", validate=IntValidator(lo=1, hi=100), style=Q_STYLE).ask()
    if years is None:
        return None
    depth = questionary.text("Live-crawl depth:", default="3", validate=IntValidator(lo=0, hi=10), style=Q_STYLE).ask()
    if depth is None:
        return None
    start_row = questionary.text(
        "Start row:", default="0", validate=IntValidator(lo=0, hi=len(orgs_df) - 1), style=Q_STYLE
    ).ask()
    if start_row is None:
        return None
    end_row = questionary.text(
        "End row:",
        default=str(len(orgs_df)),
        validate=IntValidator(lo=int(start_row) + 1, hi=len(orgs_df)),
        style=Q_STYLE,
    ).ask()
    if end_row is None:
        return None

    return {
        "org_csv_path": org_csv_path,
        "orgs_df": orgs_df,
        "out_dir": out_dir,
        "sources": DEFAULT_SOURCES,
        "years": int(years),
        "depth": int(depth),
        "start_row": int(start_row),
        "end_row": int(end_row),
    }


# ------------------------------------------------------------------- run ---


def run_crawl(cfg: dict):
    os.makedirs(cfg["out_dir"], exist_ok=True)
    started = time.monotonic()

    # Live status card with an elapsed clock while the spider works.
    # (populate_data blocks, so we refresh the clock from the render callable.)
    with Live(running_panel(cfg, started), console=console, refresh_per_second=2) as live:
        import threading

        stop = threading.Event()

        def tick():
            while not stop.is_set():
                live.update(running_panel(cfg, started))
                stop.wait(0.5)

        t = threading.Thread(target=tick, daemon=True)
        t.start()
        try:
            manifest = spider.populate_data(
                orgs_df=cfg["orgs_df"],
                out_dir=cfg["out_dir"],
                sources=cfg["sources"],
                years=cfg["years"],
                depth=cfg["depth"],
                start_row=cfg["start_row"],
                end_row=cfg["end_row"],
            )
        finally:
            stop.set()
            t.join(timeout=1)

    elapsed = time.monotonic() - started
    console.print(results_panel(manifest, cfg, elapsed))


# ------------------------------------------------------------------ main ---


def parse_args():
    p = argparse.ArgumentParser(description="Spider — nonprofit report crawler")
    p.add_argument("--csv", help="org list CSV (name,domain,ein)")
    p.add_argument("--out", default="./reports", help="output folder")
    p.add_argument("--sources", help="comma-separated: wayback,live")
    p.add_argument("--years", type=int, default=20)
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--start-row", type=int, default=0)
    p.add_argument("--end-row", type=int, default=None)
    return p.parse_args()


def show():
    load_env()
    args = parse_args()

    # ---- non-interactive mode: all required flags present -----------------
    if args.csv and args.sources:
        csv_path = clean_path(args.csv)
        if not os.path.isfile(csv_path):
            console.print(f"[bold {DANGER}]Not a file:[/] {csv_path}")
            sys.exit(1)
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]
        bad = [s for s in sources if s not in SOURCES]
        if bad:
            console.print(f"[bold {DANGER}]Unknown source(s):[/] {', '.join(bad)} \u2014 valid options: {', '.join(SOURCES)}")
            sys.exit(1)
        orgs_df = spider.load_csv(csv_path)
        cfg = {
            "org_csv_path": csv_path,
            "orgs_df": orgs_df,
            "out_dir": clean_path(args.out) or "./reports",
            "sources": sources,
            "years": args.years,
            "depth": args.depth,
            "start_row": max(0, args.start_row),
            "end_row": min(len(orgs_df), args.end_row) if args.end_row else len(orgs_df),
        }
        console.print(banner())
        console.print(config_panel(cfg, len(orgs_df)))
        run_crawl(cfg)
        return

    # ---- interactive mode --------------------------------------------------
    intro()
    while True:
        console.print(Rule(f"[bold {ACCENT}]New session[/]", style=ACCENT_DIM))

        cfg = ask_config()
        if cfg is None:
            console.print(f"[{MUTED}]Cancelled.[/]")
            break

        # Review before committing
        console.print()
        console.print(config_panel(cfg, len(cfg["orgs_df"])))
        go = questionary.confirm("Start crawling with this configuration?", default=True, style=Q_STYLE).ask()
        if not go:
            retry = questionary.confirm("Re-enter the settings?", default=True, style=Q_STYLE).ask()
            if retry:
                continue
            break

        run_crawl(cfg)

        again = questionary.confirm("Run another session?", default=False, style=Q_STYLE).ask()
        if not again:
            break

    console.print(f"[{MUTED}]Session ended.[/]")


if __name__ == "__main__":
    try:
        show()
    except KeyboardInterrupt:
        console.print(f"\n[{MUTED}]Interrupted. No further data was written.[/]")
        sys.exit(130)