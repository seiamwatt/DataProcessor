"""Wayback — Internet Archive report collector
Terminal UI (rich + questionary)

Interactive:      python wayback_UI.py
Non-interactive:  python wayback_UI.py --csv orgs.csv --out ./reports --rpm 50
"""

import argparse
import os
import sys
import threading
import time
from datetime import timedelta

import questionary
from questionary import Style, Validator, ValidationError
from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from dataprocessor.config import load_env
from dataprocessor.wayback_section import wayback

console = Console()

# ---------------------------------------------------------------- theme ---

# One shared htop palette for every screen — see dataprocessor/theme.py.
from dataprocessor.theme import (
    ACCENT, ACCENT_DIM, ACCENT_BAR, OK_BAR, PURPLE_BAR, GREEN_BAR, ERR_BAR, WARN_BAR, INK, MUTED, OK, WARN,
    DANGER, CYAN, GREEN, RED, BLUE, PURPLE, chip, section,
)
from dataprocessor.theme import PROMPT_STYLE as Q_STYLE

# The rate the archive is swept at. 50/min is the default everywhere; the
# wizard offers the two directions someone actually wants to move it in.
RPM_CHOICES = {
    "50 — default": 50,
    "30 — gentler, for long overnight runs": 30,
    "20 — minimum footprint": 20,
}

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
        " ██    ██ ██████ ██  ██ █████  ██████ ██████ ██  ██\n"
        " ██ ██ ██ ██  ██ ██████ ██  ██ ██  ██ ██     ████  \n"
        " ██ ██ ██ ██████   ██   █████  ██████ ██     ██ ██ \n"
        "  ██  ██  ██  ██   ██   █████  ██  ██ ██████ ██  ██\n",
        style=f"bold {ACCENT}",
    )
    sub = Text("Internet Archive report collector", style=MUTED, justify="center")
    return Panel(
        Group(Align.center(art), Align.center(sub)),
        box=box.SQUARE,
        border_style=ACCENT_DIM,
        subtitle=f"[{MUTED}]Wayback[/]",
        padding=(0, 2),
    )


def passes_table() -> Table:
    t = Table(box=box.SIMPLE_HEAD, border_style=ACCENT_DIM, header_style=f"bold {PURPLE_BAR}",
              title="What it sweeps (per org)", title_style=f"bold {INK}",
              expand=True)
    t.add_column("Pass", style=f"bold {ACCENT}", no_wrap=True)
    t.add_column("Retrieves", style=INK)
    t.add_row("documents", "Archived PDFs whose URL reads like a report")
    t.add_row("xml", "Word 2003 “Save As XML” reports, minus the sitemaps")
    t.add_row("pages", "Reports published as HTML — the pre-2010 norm")
    return t


def inputs_table() -> Table:
    t = Table(box=box.SIMPLE_HEAD, border_style=ACCENT_DIM, header_style=f"bold {ACCENT_BAR}",
              title="Required inputs", title_style=f"bold {INK}", expand=True)
    t.add_column("Input", style=f"bold {INK}", no_wrap=True)
    t.add_column("Notes", style=MUTED)
    t.add_row("Org CSV", "Columns: name, domain, ein")
    t.add_row("Output folder", "Destination for manifest_wayback.csv")
    t.add_row("Lookback", "How far back to search (default: all of it, 1996-now)")
    t.add_row("Request rate", "Shared budget for web.archive.org (default 50/min)")
    t.add_row("Row range", "Subset of CSV rows to process")
    return t


def intro():
    console.print(banner())
    console.print(
        Panel(
            Columns([passes_table(), inputs_table()], equal=True, expand=True),
            box=box.SQUARE,
            border_style=ACCENT_DIM,
            padding=(1, 2),
        )
    )
    console.print(
        f"[{MUTED}]Every request goes to one host, so the rate below is the "
        f"whole run's budget. The worker count is derived from it — enough "
        f"requests in flight to actually spend the budget, never enough to "
        f"exceed it.[/]\n"
    )


def config_panel(cfg: dict, n_orgs: int) -> Panel:
    t = Table(box=None, show_header=False, pad_edge=False)
    t.add_column(style=MUTED, justify="right", no_wrap=True)
    t.add_column(style=INK)

    n_selected = cfg["end_row"] - cfg["start_row"]
    t.add_row("Org CSV", cfg["org_csv_path"])
    t.add_row("Organizations",
              f"{n_selected} of {n_orgs}  (rows {cfg['start_row']}–{cfg['end_row']})")
    t.add_row("Output", os.path.abspath(cfg["out_dir"]))
    t.add_row("Lookback", "1996-now (whole archive)" if not cfg["years"]
                          else f"{cfg['years']} years")

    rate = Text()
    rate.append(f" {cfg['rpm']}/min ", style=f"black on {ACCENT}")
    rate.append(f"  across {cfg['workers']} worker(s)", style=MUTED)
    t.add_row("Request rate", rate)
    t.add_row("Mode", "download files" if not cfg["links_only"] else "record links only")

    return Panel(
        t,
        title=f"[bold {INK}]Run configuration[/]",
        border_style=ACCENT_DIM,
        box=box.SQUARE,
        padding=(1, 2),
    )


# Up to `workers` orgs are open at once, so a high --workers could push the
# panel off screen. Show the lowest-numbered few and count the rest.
MAX_ACTIVE_SHOWN = 6


def _active_rows(active: list[dict]) -> Text:
    """Render the rows in flight, one line each."""
    shown, hidden = active[:MAX_ACTIVE_SHOWN], len(active) - MAX_ACTIVE_SHOWN
    out = Text()
    for i, a in enumerate(shown):
        if i:
            out.append("\n")
        row = "?" if a["row"] is None else str(a["row"])
        out.append(f"row {row}", style=f"bold {ACCENT}")
        out.append(f"  {a['name']}", style=INK)
    if hidden > 0:
        out.append(f"\n+{hidden} more", style=MUTED)
    return out


def _rate_line(limiter) -> Text:
    """Requests used, measured rate, and whether the archive pushed back.

    Worth showing live: this is the one number that decides whether a long run
    finishes or gets the IP blocked, and `in_window` is the real measured rate
    rather than the configured ceiling.
    """
    s = limiter.stats()
    out = Text()
    out.append(f"{s['in_window']}", style=f"bold {ACCENT}")
    out.append(f"/{s['rpm_limit']} per min", style=INK)
    out.append(f"   {s['total']} total", style=MUTED)
    if s["paused_for"] > 0:
        out.append(f"   paused {s['paused_for']:.0f}s", style=f"bold {WARN}")
    elif s["throttled"]:
        out.append(f"   {s['throttled']} throttled", style=WARN)
    return out


def running_panel(cfg: dict, started: float, progress=None, limiter=None) -> Panel:
    n = cfg["end_row"] - cfg["start_row"]
    snap = progress.snapshot() if progress is not None else None
    body = Table(box=None, show_header=False, pad_edge=False)
    body.add_column(style=MUTED, justify="right", no_wrap=True)
    body.add_column(style=INK)

    if snap and snap["active"]:
        body.add_row("Status", _active_rows(snap["active"]))
    else:
        # Before the first org is picked up, and in the gap after the last one
        # finishes while the manifest is written.
        waiting = "Starting up" if not snap or not snap["total"] else "Wrapping up"
        body.add_row("Status", Text(waiting, style=f"bold {ACCENT}"))

    done = f"{snap['done']}/{snap['total']} done  ·  " if snap and snap["total"] else ""
    body.add_row("Organizations",
                 f"{done}{n} in range (rows {cfg['start_row']}–{cfg['end_row']})")
    if limiter is not None:
        body.add_row("Archive rate", _rate_line(limiter))
    body.add_row("Elapsed", fmt_duration(time.monotonic() - started))
    return Panel(body, border_style=ACCENT, box=box.SQUARE,
                 title=f"[{GREEN_BAR}] Wayback running [/]", padding=(1, 2))


def results_panel(manifest, cfg: dict, elapsed: float, limiter=None) -> Panel:
    if manifest is None or len(manifest) == 0:
        body = Group(
            Text("No archived documents were found", style=f"bold {WARN}"),
            Text("Consider widening the year lookback, or enabling all formats.",
                 style=MUTED),
        )
        return Panel(body, border_style=WARN, box=box.SQUARE,
                     title=f"[{WARN_BAR}] Finished — no results [/]", padding=(1, 2))

    lines = Table(box=None, show_header=False, pad_edge=False)
    lines.add_column(style=MUTED, justify="right", no_wrap=True)
    lines.add_column(style=INK)
    lines.add_row("Documents", Text(str(len(manifest)), style=f"bold {ACCENT}"))
    lines.add_row("Elapsed", fmt_duration(elapsed))
    if limiter is not None:
        s = limiter.stats()
        lines.add_row("Archive requests",
                      f"{s['total']} at {s['rpm_limit']}/min"
                      + (f"  ·  {s['throttled']} throttled" if s["throttled"] else ""))
    path = os.path.join(os.path.abspath(cfg["out_dir"]), wayback.MANIFEST_NAME)
    lines.add_row("Manifest", path if os.path.exists(path) else "— nothing collected")

    # Per-format breakdown if the manifest exposes it
    try:
        counts = manifest["format"].value_counts()
        breakdown = Text()
        for i, (fmt, cnt) in enumerate(counts.items()):
            if i:
                breakdown.append("   ")
            breakdown.append(f"{fmt} ", style=f"bold {ACCENT}")
            breakdown.append(str(cnt), style=INK)
        lines.add_row("By format", breakdown)
    except Exception:
        pass

    return Panel(lines, border_style=ACCENT, box=box.SQUARE,
                 title=f"[{GREEN_BAR}] Finished [/]", padding=(1, 2))


# ---------------------------------------------------------------- wizard ---


def ask_config() -> dict | None:
    """Interactive wizard. Returns config dict, or None if cancelled."""

    raw = questionary.path(
        "Org list CSV (or drag a file in):",
        validate=CsvFileValidator(),
        style=Q_STYLE,
    ).ask()
    if raw is None:
        return None
    org_csv_path = clean_path(raw)

    try:
        orgs_df = wayback.load_csv(org_csv_path)
    except Exception as e:
        console.print(f"[bold {DANGER}]Unable to read the CSV file:[/] {e!r}")
        return None
    if len(orgs_df) == 0:
        console.print(f"[bold {WARN}]The CSV file contains no rows.[/]")
        return None
    console.print(f"  [{MUTED}]Loaded [bold]{len(orgs_df)}[/] organizations.[/]\n")

    out_raw = questionary.path("Output folder for the manifest:",
                               default="./reports", style=Q_STYLE).ask()
    if out_raw is None:
        return None
    out_dir = clean_path(out_raw) or "./reports"
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        console.print(f"[bold {DANGER}]{out_dir} exists and is not a folder.[/]")
        return None

    years = questionary.text("Years of lookback (0 = whole archive, 1996-now):",
                             default="0",
                             validate=IntValidator(lo=0, hi=100),
                             style=Q_STYLE).ask()
    if years is None:
        return None

    rpm_label = questionary.select(
        "Requests per minute to web.archive.org:",
        choices=list(RPM_CHOICES),
        default=next(iter(RPM_CHOICES)),
        style=Q_STYLE,
    ).ask()
    if rpm_label is None:
        return None

    start_row = questionary.text(
        "Start row:", default="0",
        validate=IntValidator(lo=0, hi=len(orgs_df) - 1), style=Q_STYLE
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

    download = questionary.confirm(
        "Download the files, rather than only recording their URLs?",
        default=False, style=Q_STYLE,
    ).ask()
    if download is None:
        return None

    return {
        "org_csv_path": org_csv_path,
        "orgs_df": orgs_df,
        "out_dir": out_dir,
        "years": int(years),
        "rpm": RPM_CHOICES[rpm_label],
        # Sized from the chosen rate, not picked by hand: too few workers is
        # what leaves a run sitting below its own budget.
        "workers": wayback.WaybackConfig(
            requests_per_minute=RPM_CHOICES[rpm_label]).effective_workers,
        "start_row": int(start_row),
        "end_row": int(end_row),
        "links_only": not download,
    }


# ------------------------------------------------------------------- run ---


def run_sweep(cfg: dict):
    os.makedirs(cfg["out_dir"], exist_ok=True)
    started = time.monotonic()

    # Built here rather than inside populate_data so the status panel can read
    # the live request rate off it while the sweep blocks.
    limiter = wayback.RateLimiter(cfg["rpm"])
    progress = wayback.CrawlProgress()

    with Live(running_panel(cfg, started, progress, limiter), console=console,
              refresh_per_second=2) as live:
        stop = threading.Event()

        def tick():
            while not stop.is_set():
                live.update(running_panel(cfg, started, progress, limiter))
                stop.wait(0.5)

        t = threading.Thread(target=tick, daemon=True)
        t.start()
        try:
            manifest = wayback.populate_data(
                orgs_df=cfg["orgs_df"],
                out_dir=cfg["out_dir"],
                years=cfg["years"],
                start_row=cfg["start_row"],
                end_row=cfg["end_row"],
                links_only=cfg["links_only"],
                requests_per_minute=cfg["rpm"],
                max_workers=cfg["workers"],
                progress=progress,
                limiter=limiter,
            )
        finally:
            stop.set()
            t.join(timeout=1)

    elapsed = time.monotonic() - started
    console.print(results_panel(manifest, cfg, elapsed, limiter))


# ------------------------------------------------------------------ main ---


def parse_args():
    p = argparse.ArgumentParser(description="Wayback — Internet Archive report collector")
    p.add_argument("--csv", help="org list CSV (name,domain,ein)")
    p.add_argument("--out", default="./reports", help="output folder")
    p.add_argument("--years", type=int, default=0,
                   help="0 = whole archive (1996-now)")
    p.add_argument("--rpm", type=int, default=wayback.WaybackConfig.requests_per_minute,
                   help="requests per minute to web.archive.org (whole-run budget)")
    p.add_argument("--workers", type=int, default=0,
                   help="0 sizes the pool from the rate budget")
    p.add_argument("--start-row", type=int, default=0)
    p.add_argument("--end-row", type=int, default=None)
    p.add_argument("--download", action="store_true",
                   help="save the files, not just their URLs")
    return p.parse_args()


def show():
    load_env()
    args = parse_args()

    # ---- non-interactive mode: a CSV is enough -----------------------------
    if args.csv:
        csv_path = clean_path(args.csv)
        if not os.path.isfile(csv_path):
            console.print(f"[bold {DANGER}]Not a file:[/] {csv_path}")
            sys.exit(1)
        orgs_df = wayback.load_csv(csv_path)
        cfg = {
            "org_csv_path": csv_path,
            "orgs_df": orgs_df,
            "out_dir": clean_path(args.out) or "./reports",
            "years": args.years,
            "rpm": max(1, args.rpm),
            "workers": (args.workers if args.workers > 0 else
                        wayback.WaybackConfig(
                            requests_per_minute=max(1, args.rpm)).effective_workers),
            "start_row": max(0, args.start_row),
            "end_row": min(len(orgs_df), args.end_row) if args.end_row else len(orgs_df),
            "links_only": not args.download,
        }
        console.print(banner())
        console.print(config_panel(cfg, len(orgs_df)))
        run_sweep(cfg)
        return

    # ---- interactive mode --------------------------------------------------
    intro()
    while True:
        console.print(section("New session"))

        cfg = ask_config()
        if cfg is None:
            console.print(f"[{MUTED}]Cancelled.[/]")
            break

        # Review before committing
        console.print()
        console.print(config_panel(cfg, len(cfg["orgs_df"])))
        go = questionary.confirm("Start the sweep with this configuration?",
                                 default=True, style=Q_STYLE).ask()
        if not go:
            retry = questionary.confirm("Re-enter the settings?", default=True,
                                        style=Q_STYLE).ask()
            if retry:
                continue
            break

        run_sweep(cfg)

        again = questionary.confirm("Run another session?", default=False,
                                    style=Q_STYLE).ask()
        if not again:
            break

    console.print(f"[{MUTED}]Session ended.[/]")


if __name__ == "__main__":
    try:
        show()
    except KeyboardInterrupt:
        console.print(f"\n[{MUTED}]Interrupted. Partial manifest written.[/]")
        sys.exit(130)
