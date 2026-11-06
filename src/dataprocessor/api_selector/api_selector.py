import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import questionary
from dotenv import set_key
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dataprocessor.config import load_env
from dataprocessor.main_section import main_util
from dataprocessor.theme import (
    ACCENT, ACCENT_BAR, ACCENT_DIM, OK_BAR, ERR_BAR, WARN_BAR, EMPHASIS, MUTED,
    BLUE, PURPLE, PROMPT_STYLE, chip, section,
)

console = Console()

# Where saved keys go: the same file load_env() reads first.
ENV_PATH = os.path.join(os.path.expanduser("~"), ".dataprocessor", ".env")

# display name -> (env var, connection test)
PROVIDERS = {
    "DeepSeek": ("DeepSeek_key", main_util.DeepSeek_connect_test),
    "GPT": ("GPT_key", main_util.GTP_connect_test),
    "Gemini": ("Gemini_key", main_util.Gemini_connect_test),
}

EDIT = "Edit a key"
EDIT_ALL = "Edit all keys"
TEST = "Re-test connections"
BACK = "Back"

DOT = "\u2022"


# Helpers ------------------------------------------------------------------------

def mask(key: str | None) -> Text:
    if not key:
        return Text("not set", style=MUTED)
    if len(key) <= 10:
        return Text("•" * len(key), style=PURPLE)
    return Text(f"{key[:4]}{'•' * 6}{key[-4:]}", style=PURPLE)


def current_key(name: str) -> str:
    return os.getenv(PROVIDERS[name][0]) or ""


def test_connections() -> dict[str, bool]:
    """Run all connection tests in parallel behind a spinner."""
    with console.status(f"[{ACCENT}]Testing connections…[/]", spinner="dots"):
        with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
            futures = {name: pool.submit(test) for name, (_, test) in PROVIDERS.items()}
            return {name: f.result() for name, f in futures.items()}


def save_key(name: str, value: str) -> None:
    env_var = PROVIDERS[name][0]
    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    set_key(ENV_PATH, env_var, value)
    os.environ[env_var] = value  # takes effect now, no restart needed


# Panels -------------------------------------------------------------------------

def api_selector_panel() -> Panel:
    body = Group(
        Align.center(Text("API KEYS", style=f"bold {ACCENT_BAR}")),
        Align.center(Text("Manage LLM provider keys", style=MUTED)),
        Align.center(Text(datetime.now().strftime("Session started %Y-%m-%d %H:%M"), style=MUTED)),
    )
    return Panel(Padding(body, (1, 4)), box=box.SQUARE, border_style=ACCENT)


def key_status_panel(status: dict[str, bool]) -> Panel:
    table = Table(box=None, pad_edge=False, expand=True, header_style=f"bold {ACCENT_BAR}")
    table.add_column("PROVIDER", style=EMPHASIS, no_wrap=True)
    table.add_column("KEY", ratio=2)
    table.add_column("ENV VAR", style=BLUE, no_wrap=True)
    table.add_column("STATUS", justify="center", no_wrap=True)

    for name, (env_var, _) in PROVIDERS.items():
        key = current_key(name)
        if not key:
            state = Text(" NOT SET ", style=WARN_BAR)
        elif status.get(name):
            state = Text(" ONLINE ", style=OK_BAR)
        else:
            state = Text(" OFFLINE ", style=ERR_BAR)
        table.add_row(name, mask(key), env_var, state)

    footer = Text(f"Saved to {ENV_PATH}", style=MUTED)
    return Panel(Group(table, Text(), footer), title=chip("Keys", ACCENT_BAR),
                 title_align="left", border_style=ACCENT_DIM, box=box.SQUARE)


def render(status: dict[str, bool]) -> None:
    console.clear()
    console.print(api_selector_panel())
    console.print(key_status_panel(status))
    console.print()


# Prompts ------------------------------------------------------------------------

def prompt_key(name: str) -> str | None:
    """Ask for one key. Blank keeps the current value; returns None if unchanged."""
    has_key = bool(current_key(name))
    hint = "Enter to keep current" if has_key else "Enter to skip"
    value = questionary.password(
        f"{name} API key:",
        instruction=f"({hint})",
        style=PROMPT_STYLE,
    ).ask()
    if value is None:  # Ctrl-C
        return None
    value = value.strip().strip("'\"")
    return value or None


def edit_keys(names: list[str]) -> bool:
    """Prompt for each key in names, save the ones that changed. Returns True if any changed."""
    changed = []
    for name in names:
        value = prompt_key(name)
        if value and value != current_key(name):
            save_key(name, value)
            changed.append(name)

    if changed:
        console.print(f"[{OK_BAR}] SAVED [/] {', '.join(changed)}")
    else:
        console.print(f"[{MUTED}]No changes.[/]")
    return bool(changed)


def choose_provider() -> list[str]:
    choices = [
        questionary.Choice(
            title=f"{name:<10}{'set' if current_key(name) else 'not set'}",
            value=name,
        )
        for name in PROVIDERS
    ]
    choices.append(questionary.Choice(title="Cancel", value=None))
    picked = questionary.select("Which provider?", choices=choices, style=PROMPT_STYLE).ask()
    return [picked] if picked else []


# Entry point --------------------------------------------------------------------

def show():
    load_env()
    status = test_connections()

    while True:
        render(status)
        console.print(section("Actions"))

        # Nudge the user straight to setup if nothing is configured yet.
        missing = [n for n in PROVIDERS if not current_key(n)]
        default = EDIT_ALL if len(missing) == len(PROVIDERS) else EDIT

        action = questionary.select(
            "What would you like to do?",
            choices=[EDIT, EDIT_ALL, TEST, BACK],
            default=default,
            style=PROMPT_STYLE,
        ).ask()

        if action in (None, BACK):
            return
        if action == TEST:
            status = test_connections()
            continue

        names = list(PROVIDERS) if action == EDIT_ALL else choose_provider()
        if names and edit_keys(names):
            status = test_connections()  # show right away whether the new key works


if __name__ == "__main__":
    show()
