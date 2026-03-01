from rich.console import Console
import questionary

console = Console()

choice = questionary.select(
    "Pick a report:",
    choices=["Sales", "Inventory", "Users"]
).ask()

console.print(f"[bold green]Generating {choice} report...[/bold green]")
