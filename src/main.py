from rich.console import Console
from LLM_consoles import report_analysis
from LLM_consoles import report_filter
import time 
import questionary
from UI import main_page
from UI import filter_page
import subprocess
import os

console = Console()
script_dir = os.path.dirname(os.path.abspath(__file__))

def choice_menu():
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","ALL"]).ask()
    return choice


def main():
    main_page.show()
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","Settings"]).ask()


    if choice == "Part 1:Filter data":
        subprocess.Popen([
            "osascript", "-e",
            f'tell app "Terminal" to do script "cd {script_dir} && python3 filter_page.py"'
        ])
    elif choice == "Part 2:LLM analysis":
        subprocess.Popen([
            "osascript", "-e",
            f'tell app "Terminal" to do script "cd {script_dir} && python3 analysis_page.py"'
        ])

    elif choice == "Settings":
        pass
   

if __name__ == "__main__":
    main()