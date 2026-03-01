import pyfiglet
from rich.console import Console
import questionary
from alive_progress import alive_bar
import time

console = Console()
print(pyfiglet.figlet_format("My Tool"))

task = questionary.select("What to do?", choices=["Analyze", "Export", "Quit"]).ask()

with alive_bar(50, bar="smooth") as bar:
    for _ in range(50):
        time.sleep(0.03)
        bar()

console.print(f"[bold green]✓ {task} complete![/bold green]")