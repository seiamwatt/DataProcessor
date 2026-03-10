#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║             🎨 RICH TERMINAL SHOWCASE 🎨                    ║
║         A comprehensive demo of every Rich feature          ║
╚══════════════════════════════════════════════════════════════╝
"""

import time
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.tree import Tree
from rich.columns import Columns
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    TimeRemainingColumn, TimeElapsedColumn, MofNCompleteColumn,
    track
)
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.traceback import install as install_traceback
from rich.live import Live
from rich.align import Align
from rich.rule import Rule
from rich.emoji import Emoji
from rich.highlighter import RegexHighlighter
from rich.theme import Theme
from rich.logging import RichHandler
from rich.measure import Measurement
from rich.padding import Padding
from rich.style import Style
from rich import box
from rich import print as rprint
import logging

console = Console(width=100)

def section_header(title: str, subtitle: str = ""):
    console.print()
    console.print(Rule(f"[bold magenta]✦ {title} ✦[/]", style="bright_magenta"))
    if subtitle:
        console.print(Align.center(f"[dim italic]{subtitle}[/]"))
    console.print()

def pause(seconds=0.5):
    time.sleep(seconds)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. WELCOME BANNER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_banner():
    banner = Text()
    banner.append("╔══════════════════════════════════════════════════════════╗\n", style="bold cyan")
    banner.append("║", style="bold cyan")
    banner.append("       🎨 THE ULTIMATE RICH TERMINAL SHOWCASE 🎨        ", style="bold white on blue")
    banner.append("║\n", style="bold cyan")
    banner.append("║", style="bold cyan")
    banner.append("          Exploring Every Feature of Rich Library         ", style="italic yellow")
    banner.append("║\n", style="bold cyan")
    banner.append("╚══════════════════════════════════════════════════════════╝", style="bold cyan")
    console.print(Align.center(banner))
    console.print()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. TEXT STYLING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_text_styling():
    section_header("TEXT STYLING & MARKUP", "Rich supports inline markup for expressive text")

    console.print("[bold]Bold text[/bold]  │  [italic]Italic text[/italic]  │  [underline]Underlined[/underline]  │  [strikethrough]Strikethrough[/strikethrough]")
    console.print("[bold italic]Bold + Italic[/]  │  [underline bold]Bold + Underline[/]  │  [reverse]Reversed[/]  │  [blink]Blinking[/]")
    console.print()

    # Colors
    colors = ["red", "green", "blue", "yellow", "magenta", "cyan", "white"]
    color_text = Text()
    for c in colors:
        color_text.append(f"  ■ {c.upper()}  ", style=f"bold {c}")
    console.print(Panel(color_text, title="[bold]🎨 Basic Colors[/]", border_style="bright_blue"))

    # Extended colors
    gradient = Text()
    for i in range(0, 256, 4):
        gradient.append("█", style=f"color({i})")
    console.print(Panel(gradient, title="[bold]🌈 256-Color Gradient[/]", border_style="bright_green"))

    # RGB colors
    rgb_text = Text()
    for r in range(0, 255, 5):
        rgb_text.append("█", style=f"rgb({r},{255-r},{128})")
    console.print(Panel(rgb_text, title="[bold]🔴🟢🔵 True RGB Colors[/]", border_style="bright_red"))

    # Background colors
    console.print()
    bg = Text()
    bg.append(" Dark on Light ", style="black on white")
    bg.append("  ")
    bg.append(" Light on Dark ", style="white on black")
    bg.append("  ")
    bg.append(" Fire Theme ", style="bold yellow on red")
    bg.append("  ")
    bg.append(" Ocean Theme ", style="bold white on blue")
    bg.append("  ")
    bg.append(" Matrix Theme ", style="bold green on black")
    console.print(bg)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. TABLES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_tables():
    section_header("TABLES", "Beautiful data presentation with multiple box styles")

    # Feature-rich table
    table = Table(
        title="🚀 [bold]Programming Language Comparison[/bold]",
        caption="[dim]Source: Developer Survey 2025[/dim]",
        box=box.DOUBLE_EDGE,
        show_lines=True,
        title_style="bold bright_white",
        border_style="bright_blue",
        header_style="bold white on dark_blue",
        row_styles=["", "dim"],
        pad_edge=True,
        expand=True,
    )

    table.add_column("Language", style="bold cyan", justify="left", no_wrap=True)
    table.add_column("Type System", style="yellow", justify="center")
    table.add_column("Speed", justify="center")
    table.add_column("Popularity", justify="center")
    table.add_column("Use Case", style="italic green")
    table.add_column("Rating", justify="right")

    data = [
        ("🐍 Python",    "Dynamic",  "🐢 Moderate", "⭐⭐⭐⭐⭐", "AI/ML, Web, Scripts", "[bold green]9.2[/]"),
        ("🦀 Rust",      "Static",   "🚀 Blazing",  "⭐⭐⭐⭐",   "Systems, WebAssembly", "[bold green]9.5[/]"),
        ("☕ Java",      "Static",   "⚡ Fast",      "⭐⭐⭐⭐⭐", "Enterprise, Android", "[bold yellow]8.1[/]"),
        ("🟨 JavaScript","Dynamic",  "⚡ Fast (V8)", "⭐⭐⭐⭐⭐", "Web, Full-Stack", "[bold yellow]8.4[/]"),
        ("🐹 Go",        "Static",   "🚀 Very Fast", "⭐⭐⭐⭐",   "Cloud, Microservices", "[bold green]8.8[/]"),
        ("💎 Ruby",      "Dynamic",  "🐢 Moderate",  "⭐⭐⭐",     "Web (Rails)", "[bold red]7.2[/]"),
        ("⚡ C++",       "Static",   "🚀 Blazing",   "⭐⭐⭐⭐",   "Games, Embedded", "[bold green]8.6[/]"),
    ]
    for row in data:
        table.add_row(*row)

    console.print(table)
    console.print()

    # Table with different box styles
    box_styles = [
        ("SIMPLE", box.SIMPLE),
        ("ROUNDED", box.ROUNDED),
        ("HEAVY", box.HEAVY),
        ("MINIMAL", box.MINIMAL_DOUBLE_HEAD),
    ]

    tables = []
    for name, bx in box_styles:
        t = Table(title=f"[bold]{name}[/]", box=bx, border_style="bright_cyan", width=23)
        t.add_column("A", style="green")
        t.add_column("B", style="yellow")
        t.add_row("Hello", "World")
        t.add_row("Rich", "Tables")
        tables.append(t)

    console.print(Columns(tables, equal=True, expand=True))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. PANELS & LAYOUT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_panels():
    section_header("PANELS & PADDING", "Contained content with borders and titles")

    # Basic panels
    panels = [
        Panel("[bold green]✅ Success![/]\nOperation completed.", title="Success", border_style="green", width=30),
        Panel("[bold yellow]⚠️ Warning![/]\nDisk space low.", title="Warning", border_style="yellow", width=30),
        Panel("[bold red]❌ Error![/]\nConnection failed.", title="Error", border_style="red", width=30),
    ]
    console.print(Columns(panels, equal=True, expand=True))
    console.print()

    # Nested panels
    inner = Panel(
        "[italic]This panel is nested inside another panel![/italic]\n"
        "Rich supports arbitrary nesting of renderables.",
        title="[bold]Inner Panel[/]",
        border_style="bright_cyan",
        padding=(1, 2),
    )
    outer = Panel(
        inner,
        title="[bold magenta]Outer Panel[/]",
        subtitle="[dim]Nested layout example[/]",
        border_style="bright_magenta",
        padding=(1, 2),
    )
    console.print(outer)

    # Padded content
    console.print(Padding("[bold]This text has custom padding (2, 8)[/]", (2, 8), style="on dark_blue"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. TREES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_trees():
    section_header("TREE VIEW", "Hierarchical data visualization")

    tree = Tree("🏢 [bold bright_white]Acme Corporation[/]", guide_style="bright_blue")

    eng = tree.add("🔧 [bold cyan]Engineering[/]")
    frontend = eng.add("🌐 [yellow]Frontend Team[/]")
    frontend.add("👤 [green]Alice[/] - React Lead")
    frontend.add("👤 [green]Bob[/] - UI Designer")
    frontend.add("📁 Components/")

    backend = eng.add("⚙️ [yellow]Backend Team[/]")
    backend.add("👤 [green]Charlie[/] - API Architect")
    backend.add("👤 [green]Diana[/] - Database Expert")
    services = backend.add("📦 [dim]Microservices[/]")
    services.add("🔹 auth-service")
    services.add("🔹 user-service")
    services.add("🔹 payment-service")

    devops = eng.add("🚀 [yellow]DevOps[/]")
    devops.add("👤 [green]Eve[/] - Cloud Engineer")
    devops.add("☁️ AWS Infrastructure")
    devops.add("🐳 Docker & K8s")

    design = tree.add("🎨 [bold magenta]Design[/]")
    design.add("👤 [green]Frank[/] - Creative Director")
    design.add("👤 [green]Grace[/] - UX Researcher")

    hr = tree.add("👥 [bold red]Human Resources[/]")
    hr.add("👤 [green]Heidi[/] - HR Manager")

    console.print(Panel(tree, title="[bold]Organization Chart[/]", border_style="bright_blue"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. SYNTAX HIGHLIGHTING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_syntax():
    section_header("SYNTAX HIGHLIGHTING", "Beautiful code display with line numbers")

    python_code = '''\
from dataclasses import dataclass
from typing import Optional
import asyncio

@dataclass
class User:
    """A user in the system."""
    name: str
    email: str
    age: int
    role: Optional[str] = "member"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    async def send_notification(self, message: str) -> None:
        """Send an async notification to the user."""
        await asyncio.sleep(0.1)  # Simulate network delay
        print(f"📧 Notified {self.name}: {message}")

async def main():
    users = [
        User("Alice", "alice@example.com", 30, "admin"),
        User("Bob", "bob@example.com", 25),
    ]
    tasks = [u.send_notification("Welcome!") for u in users]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
'''

    syntax = Syntax(python_code, "python", theme="monokai", line_numbers=True,
                    word_wrap=True, padding=1)
    console.print(Panel(syntax, title="[bold]🐍 Python Code[/]", border_style="bright_green", expand=True))

    # Also show some JSON
    json_data = '''{
    "name": "Rich Showcase",
    "version": "2.0.0",
    "features": ["tables", "trees", "syntax", "progress"],
    "config": {
        "theme": "monokai",
        "width": 100,
        "emoji": true
    }
}'''
    json_syntax = Syntax(json_data, "json", theme="dracula", line_numbers=True, padding=1)
    console.print(Panel(json_syntax, title="[bold]📋 JSON Data[/]", border_style="bright_yellow"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. MARKDOWN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_markdown():
    section_header("MARKDOWN RENDERING", "Rich renders Markdown beautifully in the terminal")

    md_text = """\
# 📖 Rich Markdown Demo

## Features

Rich can render **bold**, *italic*, `inline code`, and ~~strikethrough~~.

### Code Block
```python
def hello():
    print("Hello from Markdown!")
```

### Lists
- 🎨 Beautiful styling
- 📊 Tables and layouts
- 🌳 Tree views
- 📝 Markdown rendering

### Blockquote
> "The terminal is a canvas, and Rich is the paint."
> — *A Happy Developer*

### Table
| Feature  | Status |
|----------|--------|
| Tables   | ✅     |
| Trees    | ✅     |
| Markdown | ✅     |
"""
    console.print(Panel(Markdown(md_text), title="[bold]Markdown Output[/]",
                        border_style="bright_blue", padding=(1, 2)))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. PROGRESS BARS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_progress():
    section_header("PROGRESS BARS & SPINNERS", "Track tasks with beautiful progress indicators")

    # Simple track
    console.print("[bold]Simple progress with track():[/]")
    for _ in track(range(50), description="[cyan]Processing...[/]", console=console):
        time.sleep(0.02)
    console.print()

    # Multi-task progress
    console.print("[bold]Multi-task progress:[/]")
    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold blue]{task.description}[/]"),
        BarColumn(bar_width=30, complete_style="green", finished_style="bright_green"),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
        expand=True,
    ) as progress:
        task1 = progress.add_task("Downloading files...", total=100)
        task2 = progress.add_task("Compiling assets...", total=80)
        task3 = progress.add_task("Running tests......", total=60)
        task4 = progress.add_task("Deploying app......", total=40)

        while not progress.finished:
            progress.update(task1, advance=1.8)
            progress.update(task2, advance=1.2)
            progress.update(task3, advance=0.9)
            progress.update(task4, advance=0.6)
            time.sleep(0.03)

    console.print("[bold green]✅ All tasks complete![/]")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_logging():
    section_header("RICH LOGGING", "Enhanced log output with Rich handler")

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)],
        force=True,
    )
    log = logging.getLogger("rich_demo")

    log.debug("Initializing [bold]Rich[/bold] showcase application...")
    log.info("Connected to [cyan]database[/cyan] server at localhost:5432")
    log.warning("Memory usage at [yellow]87%[/yellow] — consider scaling up")
    log.error("Failed to load config from [red]/etc/app/config.yml[/red]")
    log.critical("🔥 System overload detected! Immediate action required!")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. TRACEBACKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_tracebacks():
    section_header("RICH TRACEBACKS", "Beautiful, informative error traces")
    install_traceback(console=console, show_locals=True, width=100)

    try:
        def calculate_stats(data):
            total = sum(data)
            average = total / len(data)
            maximum = max(data)
            result = {"total": total, "avg": average, "max": maximum}
            # This will raise an error
            return result["median"]

        numbers = [42, 17, 93, 8, 56]
        calculate_stats(numbers)
    except Exception:
        console.print_exception(show_locals=True, width=100)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 11. JSON RENDERING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_json():
    section_header("JSON PRETTY PRINTING", "Syntax-highlighted JSON with Rich")

    data = {
        "user": {
            "id": 12345,
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "active": True,
            "roles": ["admin", "developer", "analyst"],
            "settings": {
                "theme": "dark",
                "notifications": True,
                "language": "en-US",
                "beta_features": None,
            },
            "scores": [98.5, 87.3, 92.1, 100.0],
        }
    }
    console.print_json(json.dumps(data))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 12. LIVE DISPLAY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_live_display():
    section_header("LIVE DISPLAY", "Real-time updating content")

    import random
    metrics = {"CPU": 45, "Memory": 62, "Disk": 38, "Network": 15}

    def make_dashboard(step):
        table = Table(title=f"[bold]📊 System Monitor[/bold] [dim](tick {step})[/dim]",
                      box=box.ROUNDED, border_style="bright_cyan", expand=True)
        table.add_column("Metric", style="bold white", width=12)
        table.add_column("Value", justify="center", width=10)
        table.add_column("Bar", width=40)
        table.add_column("Status", justify="center", width=10)

        for name, base in metrics.items():
            val = max(5, min(99, base + random.randint(-10, 10)))
            metrics[name] = val
            color = "green" if val < 60 else "yellow" if val < 80 else "red"
            bar_fill = "█" * (val // 3) + "░" * (33 - val // 3)
            status = "✅ OK" if val < 70 else "⚠️ WARN" if val < 85 else "🔥 CRIT"
            table.add_row(
                f"{'💻' if name=='CPU' else '🧠' if name=='Memory' else '💾' if name=='Disk' else '🌐'} {name}",
                f"[bold {color}]{val}%[/]",
                f"[{color}]{bar_fill}[/]",
                status,
            )
        return table

    with Live(make_dashboard(0), console=console, refresh_per_second=4) as live:
        for i in range(1, 25):
            time.sleep(0.15)
            live.update(make_dashboard(i))

    console.print("[bold green]✅ Live monitoring stopped.[/]")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 13. COLUMNS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_columns():
    section_header("COLUMNS LAYOUT", "Responsive multi-column content")

    cards = []
    card_data = [
        ("🐍 Python", "bright_green", "Versatile scripting\nand ML powerhouse"),
        ("🦀 Rust", "bright_red", "Memory-safe systems\nprogramming"),
        ("⚡ TypeScript", "bright_blue", "Type-safe JavaScript\nfor the web"),
        ("🐹 Go", "bright_cyan", "Simple, fast\nconcurrency king"),
        ("☕ Java", "yellow", "Enterprise-grade\nplatform language"),
        ("💜 Kotlin", "magenta", "Modern JVM\nlanguage"),
    ]
    for name, color, desc in card_data:
        cards.append(
            Panel(
                f"[bold]{name}[/]\n\n{desc}",
                border_style=color,
                width=22,
                padding=(1, 2),
            )
        )
    console.print(Columns(cards, equal=True, expand=True))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 14. RULES & SEPARATORS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_rules():
    section_header("RULES & SEPARATORS", "Horizontal rules with different styles")

    console.print(Rule("Default Rule"))
    console.print(Rule("[bold red]Styled Rule[/]", style="red"))
    console.print(Rule("[bold blue]Left Aligned[/]", align="left", style="blue"))
    console.print(Rule("[bold green]Right Aligned[/]", align="right", style="green"))
    console.print(Rule(style="bright_yellow"))
    console.print(Rule("[bold magenta]✨ Fancy Separator ✨[/]", characters="═", style="magenta"))
    console.print(Rule(characters="·", style="dim"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 15. EMOJI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_emoji():
    section_header("EMOJI SUPPORT", "Rich supports emoji shortcodes")

    emojis = [
        (":rocket:", "rocket"), (":fire:", "fire"), (":star:", "star"),
        (":heart:", "heart"), (":thumbs_up:", "thumbs_up"), (":warning:", "warning"),
        (":eyes:", "eyes"), (":bulb:", "bulb"), (":gear:", "gear"),
        (":package:", "package"), (":bug:", "bug"), (":sparkles:", "sparkles"),
    ]
    text = Text()
    for code, name in emojis:
        text.append(f"  {code} = ", style="dim")
        text.append(f"{Emoji(name)}", style="bold")
        text.append(f"  ({name})", style="dim italic")
        text.append("  │", style="dim")
    console.print(Panel(text, title="[bold]Emoji Gallery[/]", border_style="bright_yellow"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 16. CONSOLE STATUS / SPINNER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_status():
    section_header("STATUS SPINNERS", "Indicate ongoing operations with spinners")

    spinners = ["dots", "line", "dots2", "star", "hamburger", "growVertical", "moon", "runner", "bouncingBar"]
    for spinner in spinners:
        with console.status(f"[bold green]Running spinner: [cyan]{spinner}[/cyan]...", spinner=spinner):
            time.sleep(0.8)
        console.print(f"  [dim]✓[/dim] Spinner [cyan]{spinner}[/] done")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 17. INSPECT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_inspect():
    section_header("OBJECT INSPECTION", "Inspect any Python object")

    class Starship:
        """A class representing a spaceship. 🚀"""
        def __init__(self, name: str, crew: int, warp_speed: float):
            self.name = name
            self.crew = crew
            self.warp_speed = warp_speed
            self.shields = True

        def engage(self) -> str:
            """Engage warp drive."""
            return f"{self.name} engaging at warp {self.warp_speed}!"

        def fire_phasers(self, target: str) -> str:
            """Fire phasers at a target."""
            return f"Firing at {target}!"

    ship = Starship("USS Enterprise", 430, 9.9)
    console.print(Panel("[bold]Inspecting a Starship object:[/]", border_style="bright_cyan"))
    from rich import inspect as rich_inspect
    rich_inspect(ship, console=console, methods=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 18. LAYOUT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_layout():
    section_header("LAYOUT SYSTEM", "Split-pane terminal layouts")

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="sidebar", ratio=1),
        Layout(name="main", ratio=3),
    )

    layout["header"].update(
        Panel(Align.center("[bold bright_white]🖥️  DASHBOARD v2.0[/] [dim]— Powered by Rich[/dim]"),
              style="on dark_blue", border_style="bright_blue")
    )

    # Sidebar
    sidebar_tree = Tree("[bold]📁 Navigation[/]")
    sidebar_tree.add("[link]🏠 Home[/]")
    sidebar_tree.add("[link]📊 Analytics[/]")
    sidebar_tree.add("[link]👥 Users[/]")
    sidebar_tree.add("[link]⚙️ Settings[/]")
    layout["sidebar"].update(Panel(sidebar_tree, title="[bold]Menu[/]", border_style="bright_green"))

    # Main
    main_table = Table(box=box.SIMPLE, expand=True)
    main_table.add_column("Endpoint", style="cyan")
    main_table.add_column("Requests", justify="right", style="green")
    main_table.add_column("Latency", justify="right", style="yellow")
    main_table.add_row("/api/users", "12,847", "45ms")
    main_table.add_row("/api/posts", "8,392", "62ms")
    main_table.add_row("/api/auth", "3,201", "28ms")
    layout["main"].update(Panel(main_table, title="[bold]API Metrics[/]", border_style="bright_yellow"))

    layout["footer"].update(
        Panel(Align.center("[dim]Press Ctrl+C to exit │ Last updated: just now │ Status: 🟢 All systems operational[/]"),
              border_style="dim")
    )

    console.print(layout)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    console.clear()
    show_banner()
    pause(0.3)

    demos = [
        ("Text Styling & Colors", show_text_styling),
        ("Tables", show_tables),
        ("Panels & Padding", show_panels),
        ("Tree Views", show_trees),
        ("Syntax Highlighting", show_syntax),
        ("Markdown Rendering", show_markdown),
        ("Progress Bars", show_progress),
        ("Rich Logging", show_logging),
        ("Tracebacks", show_tracebacks),
        ("JSON Pretty Print", show_json),
        ("Live Display", show_live_display),
        ("Columns Layout", show_columns),
        ("Rules & Separators", show_rules),
        ("Emoji Support", show_emoji),
        ("Status Spinners", show_status),
        ("Object Inspection", show_inspect),
        ("Layout System", show_layout),
    ]



    # Finale
    console.print()
    console.print(Rule("[bold bright_magenta]✨ SHOWCASE COMPLETE ✨[/]", style="bright_magenta"))
    console.print()
    console.print(Align.center(Panel(
        "[bold bright_white]Thank you for exploring Rich![/]\n\n"
        "📦 Install: [cyan]pip install rich[/cyan]\n"
        "📖 Docs: [link=https://rich.readthedocs.io]https://rich.readthedocs.io[/link]\n"
        "⭐ GitHub: [link=https://github.com/Textualize/rich]github.com/Textualize/rich[/link]\n\n"
        "[dim italic]\"Make your terminal beautiful.\"[/]",
        title="[bold]🎨 Rich Library[/]",
        border_style="bright_cyan",
        padding=(1, 4),
        width=60,
    )))
    console.print()


if __name__ == "__main__":
    main()

if __name__ ==. "__main__:
    console.print(debfwnfifdnsnqwopvne vjsps ajfejfnjrhnngnvhbf sgn vpsienghvnslgjr ghjs ghfls
                  jfgjdnnsikgwnmgiren sofrogjdpr ;sgjdpeng;jkjfs;gf fdjg;nvg sep=
                  
                  jsj 
                  sdjdw fkgbriffngs
                  fgsjgfjwwkjgs
                  fioensagidfnfdshg spr fiberignidnskjjgnbhslsnvgsjfvs 
                  shfspsa dufsalasfhd siebsjdhfsb)